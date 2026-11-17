# 0003. Контракты модулей: HTTP-клиент, обнаружение фида, парсер, валидатор

- **Статус:** Proposed
- **Дата:** 2026-11-17
- **Связанные issue:** MYSP-2 (§1, §2, §6, AC-13…AC-28), MYSP-4, MYSP-3
- **Заменяет / заменено:** —

## Контекст

MYSP-4 реализует CLI по этим контрактам, MYSP-5 проверяет поведение по AC, MYSP-6 ревьюит соответствие. Чтобы проверки были возможны, границы модулей и сигнатуры фиксируются до кода: где проходит сеть, где заканчивается разбор и начинается нормализация, кто отвечает за ретраи и лимит частоты.

Здесь описаны интерфейсы и их поведение, а не реализация. Псевдокод ниже — нормативный: имена методов и полей можно расширять, но нельзя переименовывать или менять смысл без нового ADR.

Терминология MYSP-2 маппится так, чтобы не искать несуществующие файлы:

| Слово из MYSP-2/родительского issue | Модуль |
| --- | --- |
| HTTP-клиент | `feedscan/http_client.py`, `feedscan/decode.py` |
| обнаружение фида | `feedscan/discovery.py`, `feedscan/robots.py` |
| парсер | `feedscan/formats.py`, `feedscan/parsers/*` |
| валидатор | `feedscan/classify.py` + `feedscan/normalize.py` + `feedscan/currencies.py` |

Отдельного `validator.py` нет специально: «валиден ли фид» — это композиция «что за ответ» (`classify`), «что за формат» (`formats`), «есть ли курсы» (`normalize`). Один класс-валидатор с флагом «а теперь проверь ещё вот это» быстро стал бы свалкой.

## Общие правила контрактов

1. Модули `classify`, `formats`, `parsers`, `normalize`, `discovery`, `currencies`, `report` — чистые: не делают сетевых запросов, не читают файлы (кроме `currencies.load` и `report.write_*`), не знают текущего времени. Время и сеть приходят снаружи.
2. Все данные между модулями — frozen dataclasses из `models.py`; словари допустимы только на границе JSON и внутри парсеров.
3. Любая ошибка одного домена локализуется в его `DomainResult`; исключение наружу выпускает только `cli` (фатальные ошибки окружения/записи — exit 1, вход — exit 2).
4. Парсеры не решают, валидна ли валюта, и не считают `Decimal`: они отдают сырые строки. Всё, что связано с числами и реестром, — в `normalize`, в одном месте.
5. `format_evidence` и `reason_detail` — человекочитаемые и короткие (≤ 200 символов), без тел ответов и без персональных данных.

## Контракт HTTP-клиента

```python
class Purpose(str, Enum):
    ROBOTS = "robots"
    HOMEPAGE = "homepage"
    CANDIDATE = "candidate"
    SITEMAP = "sitemap"

@dataclass(frozen=True)
class FetchResult:
    requested_url: str
    final_url: str            # после редиректов
    status: int | None        # None, если ответа не было
    headers: Mapping[str, str]
    body: bytes | None        # распакованное тело, None при ошибке/превышении лимита
    text: str | None          # декодированный текст, None если декодировать не удалось
    encoding: str | None      # фактическая кодировка: utf-8 | windows-1251 | ...
    gzip: bool
    size_bytes: int           # распакованный размер, 0 если тело не получено
    compressed_bytes: int
    redirect_count: int
    warnings: tuple[str, ...]
    error: str | None         # None | timeout | dns_error | tls_error | connect_error
                              # | read_error | too_large | too_many_redirects | redirect_loop
                              # | bad_redirect | unsupported_encoding | invalid_gzip
    attempts: int             # сколько HTTP-запросов сделано по этому кандидату
    elapsed_ms: int

class HttpClient:
    def get(self, url: str, *, purpose: Purpose) -> FetchResult: ...
```

Обязанности клиента (и только они): вежливость, ретраи, редиректы, распаковка, декодирование, лимиты размера, User-Agent. Классификация «что это за ответ» — не его дело.

### Частота запросов

- Ключ лимита — домен из CSV, если хост запроса равен ему или является его поддоменом (`www.example.com` → ключ `example.com`); иначе хост URL. Так «1 запрос/сек на домен» не превращается в 2 запроса/сек, когда главная редиректит на `www`. Каждый HTTP-запрос, включая `robots.txt`, `sitemap.xml`, редиректы и повторы, проходит через один и тот же `_wait_for_domain`.
- Между **началами** запросов к одному ключу лимита — не менее `per_domain_interval` (дефолт 1.0 с), по монотонным часам. Ожидание — `time.sleep` остатка, без busy-loop.
- Прогон последовательный (один домен за раз, кандидаты по порядку). Параллельности в MVP нет — значит, гонок за лимит тоже нет, а тест с fake transport проверяет интервалы по записанным меткам.
- Если `robots.txt` объявляет `Crawl-delay` больше нашего интервала — применяется большее значение. Уменьшать интервал из-за `Crawl-delay` нельзя.

### Таймауты и повторы

| Что | Значение |
| --- | --- |
| connect timeout | 5 с |
| бюджет одной попытки (wall clock) | 15 с |
| повторов на кандидата | 2 → итого не более 3 запросов |
| паузы перед повтором | 1 с, затем 2 с, плюс jitter 0…0.5 с |
| повторяем | `timeout`, `connect_error`, `tls_error`, `read_error`, HTTP 500/502/504 и 503 без признаков челленджа |
| не повторяем | 401, 403, 429, 451, любой 4xx, челлендж/captcha/логин, `too_large`, `invalid_gzip`, `unsupported_encoding`, `bad_redirect`, `too_many_redirects`, `redirect_loop`, 5xx с признаками челленджа |

Бюджет повторов — на кандидата, а не на хоп редиректа: редирект съедает попытку из того же лимита, иначе цепочка из трёх переходов и трёх таймаутов дала бы девять запросов.

> **Расхождение с AC-26.** В MYSP-2 §6 «ретраи: максимум 2», а AC-26 говорит «не более 2 попыток». Это разные вещи: 2 повтора = 3 запроса. ADR фиксирует 3 запроса и требует читать AC-26 как «не более 2 повторов»; Тимуру нужно проверить именно это. Если человек предпочтёт жёстче (2 запроса), меняется одно число в конфиге и формулировка AC.

### Редиректы

- `follow_redirects=False`; клиент разворачивает цепочку сам, до 3 переходов.
- Каждый хоп — отдельный запрос через `_wait_for_domain` и расходует бюджет попыток.
- Разрешены только `http`/`https`. `Location` резолвится через `urljoin` относительно текущего URL. Не-http(s) схема → `bad_redirect`.
- Больше 3 переходов → `too_many_redirects`; повтор URL в цепочке (цикл) → `redirect_loop`.
- Смена хоста допустима, но добавляет warning `cross_domain_redirect` и `final_url` отличается от запрошенного; лимит частоты при этом считается по новому ключу.
- Заголовки авторизации не переносятся (их и нет), cookies не сохраняются между запросами.

### Размер и распаковка

- `Accept-Encoding: gzip` — единственное поддерживаемое сжатие. Ответ с `Content-Encoding: br|deflate|zstd` → `unsupported_encoding` (мы его не просили).
- Чтение потоковое, чанками 64 KiB, с двумя счётчиками: сжатые байты и распакованные. Превышение любого лимита (5 MiB) → `too_large`, чтение обрывается, тело не сохраняется и не попадает в отчёт.
- gzip определяется по `Content-Encoding` или по магическим байтам `1f 8b`. Распаковка инкрементальная (`zlib.decompressobj(16 + zlib.MAX_WBITS)`), чтобы лимит распакованного размера работал до аллокации всего тела.
- Битый gzip → `invalid_gzip` (на уровне домена это `error` с кодом `invalid_gzip`, а не общий `undecodable`).
- `size_bytes` — распакованный размер, `compressed_bytes` — сколько пришло по сети (AC-22).

### Декодирование (контракт `decode.py`)

```python
def decode_body(raw: bytes, declared: str | None) -> Decoded  # text|None, encoding|None, warnings, error
```

1. Снять UTF-8 BOM, если он есть (`utf-8-sig`), и записать `encoding = "utf-8"`.
2. UTF-16/UTF-32 BOM → `unsupported_encoding` (вне MVP, лучше честная ошибка, чем мусор в отчёте).
3. Кандидаты в порядке: заявленная кодировка → `utf-8` → `windows-1251`, без дублей, регистронезависимо, синонимы `cp1251`/`win-1251` → `windows-1251`, `utf8` → `utf-8`.
4. Особый случай MYSP-2 §2.2: заявлена `windows-1251`, но тело строго декодируется как UTF-8 и содержит не-ASCII байты → выбираем `utf-8`, warning `encoding_mismatch`.
5. Первое успешное строгое декодирование побеждает. Ни одно не сработало → `undecodable`.
6. `windows-1251` декодирует почти любые байты, поэтому она последняя: иначе UTF-8-фид молча превратился бы в кракозябры.

### User-Agent и запреты

- UA: `feedscan/<version> (+<contact>)`, где `<contact>` — значение `FEEDSCAN_CONTACT` (e-mail или URL). Без переменной — `feedscan/<version>`, предупреждение в stderr, `config.ua_contact = null` в отчёте.
- Никаких браузерных UA, `Referer`, `Accept-Language`, `sec-ch-ua`, прогрева сессии «как в браузере». Клиент не умеет и не должен уметь притворяться браузером (см. ADR-0004).
- `trust_env=False` — переменные `HTTP_PROXY`/`HTTPS_PROXY` из окружения не применяются: прогон не должен зависеть от чужой сетевой настройки, а «тихий» прокси — это обход, которого мы не планировали.

## Контракт обнаружения фида

```python
class CandidateOrigin(str, Enum):
    HOMEPAGE_LINK = "homepage_link"
    WELL_KNOWN = "well_known"
    SITEMAP = "sitemap"

@dataclass(frozen=True)
class Candidate:
    url: str
    origin: CandidateOrigin
    order: int            # позиция в общем списке, для детерминизма

def homepage_candidates(html: str, base_url: str, domain: str) -> list[Candidate]: ...
def well_known_candidates(domain: str) -> list[Candidate]: ...
def sitemap_candidates(xml_text: str, base_url: str, domain: str) -> list[Candidate]: ...
```

Порядок кандидатов фиксирован (MYSP-2 §1):

1. **Ссылки с главной.** `<link>` с `rel`, содержащим `alternate`, и `type` из {`application/xml`, `text/xml`, `application/rss+xml`}; плюс `<a href>`, где href в нижнем регистре содержит `xml`, `rates`, `feed` или `export`. Относительные URL резолвятся от `final_url` главной. Учитываются только ссылки на тот же хост или поддомен домена из CSV (`host == domain` или `host.endswith("." + domain)`); остальные отбрасываются с note `offsite_link_ignored`. Максимум 10, порядок — порядок в документе.
2. **Типовые пути** — ровно четыре, в этом порядке: `/rates.xml`, `/export/rates.xml`, `/xml/rates.xml`, `/bestchange.xml`, от `https://<domain>/`.
3. **Sitemap** — `https://<domain>/sitemap.xml` как источник ссылок: `<loc>`, оканчивающиеся на `.xml`, тот же хост/поддомен, без самого `sitemap*.xml`, дедуп с сохранением порядка, максимум 10.

Правила:

- Сначала берутся кандидаты с главной. Главная запрашивается один раз; если она недоступна или это не HTML, кандидатов из неё нет, но проверка продолжается по типовым путям.
- Типовые пути проверяются после ссылок с главной и независимо от того, нашлась ли ссылка: фид может быть доступен по стандартному адресу, даже если ссылки на него нет.
- `sitemap.xml` запрашивается **лениво** — только если после главной и типовых путей валидного фида нет. Это экономит запросы к чужим серверам.
- Общий лимит кандидатов на домен — 25. При превышении список обрезается по порядку, добавляется warning `candidate_cap_reached`.
- URL нормализуется: нижний регистр scheme/host, удаление порта по умолчанию, удаление фрагмента; query сохраняется. Дубли удаляются по нормализованному URL.
- `discovery` не делает запросов и не знает про `blocked`; он только строит список по уже полученным документам.

## Контракт `robots.txt` (`robots.py`)

```python
class RobotsGate:
    def check(self, url: str) -> RobotsDecision   # ALLOWED | DISALLOWED | UNAVAILABLE | BLOCKED
```

- Загружается один раз на origin (scheme + host + port) за прогон, результат кэшируется в памяти. `robots.txt` отдаёт 200 → парсим группы `User-agent`, применяем группу с наиболее точным совпадением (`feedscan`), иначе `*`; при равенстве длины пути `Allow` побеждает `Disallow` (RFC 9309).
- 404/410 и прочие 4xx (кроме 401/403/429) → `ALLOWED` для всех путей.
- 401/403/429/челлендж в `robots.txt` → `BLOCKED`: сервер отказал уже на входе, дальше не ходим, домен — `blocked`.
- 5xx/таймаут/DNS → `UNAVAILABLE`: домен получает `error`/`robots_unavailable`; это техническая неопределённость, а не «фида нет».
- `Crawl-delay` учитывается как минимум частоты (см. выше).
- Лимит размера `robots.txt` — 512 KiB, дальше обрезаем и работаем по распознанному префиксу с warning.

## Контракт парсера

```python
@dataclass(frozen=True)
class RawRate:
    source_url: str
    base_id: str | None        # идентификатор из фида, как опубликован
    quote_id: str | None
    rate_raw: str | None
    in_raw: str | None         # сколько клиент отдаёт (единицы base) — если фид даёт объёмы
    out_raw: str | None        # сколько клиент получает (единицы quote)
    orientation: Orientation   # quote_per_base | base_per_quote
    side_raw: str | None       # buy | sell | bid | ask | ... со стороны обменника
    reserve_raw: str | None
    reserve_currency: ReserveCurrency  # BASE | QUOTE | UNKNOWN
    min_raw: str | None
    max_raw: str | None
    fee_raw: str | None
    updated_raw: str | None
    path: str                  # путь до узла, для format_evidence

class Parser(Protocol):
    kind: Format
    def parse(self, text: str) -> Iterator[RawRate]: ...
```

- Парсер отдаёт строки по одной, включая заведомо неполные: отбраковка — дело `normalize`. Исключение — структурно непригодный документ (корень не тот): `FeedParseError(reason_detail)`, который `inventory` превращает в `error`/`invalid_feed_structure`.
- Парсер не читает реестр, не считает `Decimal`, не знает про сети и лимиты.
- `orientation` — ответственность парсера: он единственный знает семантику своего формата. Если фид публикует «сколько base за 1 quote», ставится `base_per_quote`, инверсию делает `normalize`.
- `side_raw` переводится в `Rate.side` в `normalize` по явной таблице: `buy`/`bid` (обменник покупает base) → `client_sells_base`; `sell`/`ask` (обменник продаёт base) → `client_buys_base`; неизвестное → `unknown`. Это место, где легко перепутать сторону, поэтому таблица живёт в одном модуле и покрыта тестами.
- `<rates><item>` без namespace и с `from/to/in/out` — признаки `bestchange_compatible`. Если в документе namespace есть, это `own_schema`: подгонять разбор под «почти BestChange» нельзя, иначе сломается на первом же отклонении.
- `own_schema` ищет поля по словарю синонимов из MYSP-2 §3 (`from|base|currency_from|give|in_currency|source` и т.д.); синонимы — константа в `parsers/xml_own.py`, а не разбросанные по коду проверки.

## Контракт нормализации

```python
class DropReason(str, Enum): ...   # см. ADR-0001

@dataclass(frozen=True)
class Drop:
    reason: str
    detail: str
    raw_currency_id: str | None

@dataclass(frozen=True)
class NormalizedDocument:
    rates: tuple[Rate, ...]
    drops: tuple[Drop, ...]
    dropped_by_reason: Mapping[str, int]
    warnings: tuple[str, ...]

def normalize_document(raws: Iterable[RawRate], registry: CurrencyRegistry) -> NormalizedDocument: ...
```

- Все правила чисел, диапазонов, отсутствующих значений, инверсии, резерва и дедупликации — ADR-0001. Здесь фиксируется только API: пакетная функция на документ, потому что дедупликация по `(base, quote, side)` требует видеть весь документ.
- Порядок `rates` — документный; отчёт сортирует их сам (см. ниже).
- `normalize` не знает, `ok` домен или нет: решение принимает `inventory` по факту `len(rates) > 0`.
- Неизвестная валюта не превращается в `null`: строка отбрасывается, `raw_currency_id` уходит в сводку.

## Контракт классификации

```python
class Status(str, Enum):
    OK = "ok"
    NOT_FOUND = "not_found"
    HTML = "html"
    BLOCKED = "blocked"
    ERROR = "error"

class ResponseClass(str, Enum):
    PAYLOAD = "payload"       # можно пробовать парсить
    HTML = "html"             # обычная страница
    BLOCKED = "blocked"       # отказ/челлендж
    TRANSPORT_ERROR = "transport_error"
    EMPTY = "empty"

@dataclass(frozen=True)
class CandidateAttempt:
    url: str
    origin: CandidateOrigin
    http_status: int | None
    response_class: ResponseClass | None
    reason_code: str | None
    size_bytes: int
    duration_ms: int

def classify_fetch(result: FetchResult) -> tuple[ResponseClass, str]:  # класс + reason_code
    ...

def domain_status(attempts: Sequence[CandidateAttempt]) -> tuple[Status, str, str]:  # status, reason_code, reason_detail
    ...
```

- `classify_fetch` — чистая функция от `FetchResult`: транспортная ошибка → `TRANSPORT_ERROR` с её кодом; пустое/пробельное тело при 2xx → `EMPTY` (`empty_response`); признаки `blocked` (ADR-0004) важнее HTML; иначе по `Content-Type` и началу тела HTML или payload.
- `domain_status` реализует приоритет `blocked > html > error > not_found` из MYSP-2 §1.1 и никогда не «улучшает» домен до `ok`: `ok` ставит `inventory` только по валидному фиду.
- Все reason-коды берутся из закрытого перечня. `blocked`-коды — ADR-0004; остальные:

| Статус | `reason_code` |
| --- | --- |
| `ok` | `found` |
| `not_found` | `http_404`, `http_410`, `no_candidates`, `robots_disallowed` |
| `html` | `html_page` |
| `error` | `timeout`, `dns_error`, `tls_error`, `connect_error`, `read_error`, `http_5xx`, `too_large`, `too_many_redirects`, `redirect_loop`, `bad_redirect`, `unsupported_encoding`, `invalid_gzip`, `undecodable`, `empty_response`, `invalid_feed_structure`, `invalid_xml`, `invalid_json`, `invalid_csv`, `no_rates_in_feed`, `robots_unavailable` |

- Новый код добавляется только вместе с правкой ADR и тестом: коды сравниваются между прогонами и попадают в отчёт, поэтому «локальная импровизация» в классификаторе запрещена.

## Контракт оркестрации

```python
def check_domain(row: ExchangerRow, deps: Deps) -> DomainResult: ...
def run(rows: Sequence[ExchangerRow], config: RunConfig, deps: Deps) -> Report: ...
```

Порядок для домена:

1. `robots.txt` → `BLOCKED`/`UNAVAILABLE` завершают домен сразу (`blocked` / `error`).
2. Главная (если разрешена robots) → кандидаты из ссылок.
3. Кандидаты по порядку: robots-проверка → запрос → `classify_fetch` → при `PAYLOAD` определение формата → парсинг → нормализация. Первый валидный фид завершает домен со статусом `ok`.
4. Если валидного нет и кандидаты кончились — ленивый `sitemap.xml`, затем его кандидаты (те же шаги).
5. Итог: `domain_status` по всем наблюдениям.

Правила остановки и изоляции:

- `blocked` на конкретном URL запрещает повторные запросы **к этому URL**, но не к другим кандидатам: фид может лежать на открытом пути, пока главная закрыта челленджем. Статус `blocked` побеждает только когда валидного фида так и не нашлось.
- `blocked` на `robots.txt` завершает домен целиком: если нам отказали в правилах, дальше не идём.
- `ok` — терминальное состояние домена; оставшиеся кандидаты не проверяются.
- Один упавший домен не прерывает прогон (MYSP-2 §6). Исключения фатальны только для записи отчётов и чтения входа.
- `--limit N` применяется после сортировки доменов по алфавиту: прогон «первых N» воспроизводим и не зависит от порядка строк в CSV.
- Логи — stdlib `logging` в stderr, человекочитаемо, без тел ответов; `--verbose` включает отладочные строки по кандидатам. Лог-файлов нет: единственный артефакт прогона — два отчёта.

## Контракт отчёта

```python
def build_report(results: Sequence[DomainResult], config: RunConfig, generated_at: datetime) -> dict: ...
def render_markdown(report: Mapping) -> str: ...
def write_atomic(path: Path, data: str | bytes) -> None: ...
```

- Состав `feeds.json` и `feeds.md` — MYSP-2 §5.2–5.3, поля не сокращаются. `summary.status_counts` содержит все пять статусов, включая нули.
- Домены сортируются по `domain`; `attempts[]` — в порядке проверки.
- `examples` — первые два различных `(base, quote)` из `rates`, отсортированных по `(base, quote, side, rate)`, где `rate` сравнивается как `Decimal`, а не как строка: выбор не зависит от порядка строк в документе, поэтому AC-12 выполним.
- `report` не знает про сеть и парсеры; на вход только результаты.
- Запись атомарная: временный файл в том же каталоге (`NamedTemporaryFile(delete=False, dir=...)`) → `flush` + `os.replace`. Каталог создаётся перед записью; при ошибке записи — exit 1, оба файла не остаются в полузаписанном состоянии.
- В отчётах нет абсолютных путей, e-mail вне `config.ua_contact`, заголовков с токенами, тел ответов (AC-11, AC-19).

## Последствия

- Сетевой слой можно тестировать без сети и без monkeypatch: `MockTransport` + запись запросов даёт AC-25/26/15 напрямую.
- Парсеры становятся тонкими и почти не меняются при росте реестра валют; вся чувствительная логика чисел — в одном модуле с тестами.
- Ручные редиректы и ручная распаковка — больше кода, чем в «обычном» клиенте; взамен лимиты и частота проверяемы, а не «предполагаются».
- Приоритет статусов и reason-коды живут в `classify` и в перечнях ADR-0003/ADR-0004; расхождение ловится тестом на полный перечень кодов.
- Отчёт жёстко привязан к порядку сортировки; изменение порядка — изменение AC-12 и должно быть осознанным.

## Что осталось открытым

- Формулировка AC-26 про «2 попытки» против 2 повторов — нужно подтверждение (см. врезку выше).
- `Crawl-delay` из `robots.txt`: honor как минимум частоты — решение ADR; если человек захочет строго 1 rps даже при `Crawl-delay: 5`, это делается флагом, но тогда падает вежливость.
- Нужен ли `--limit` как единственный «отладочный» флаг — по опыту MYSP-4 будет видно.
