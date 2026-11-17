# Инвентаризация фидов обменников

Сформировано: 2026-11-17T10:13:39Z

## Параметры прогона

| Параметр | Значение |
| --- | --- |
| вход | `data/exchangers.csv` |
| контакт в User-Agent | `https://github.com/DenisDolgov/crypto-monitor` |
| таймаут запроса | 15.0 с |
| ретраи | 2 |
| пауза между запросами к домену | 1.0 с |
| лимит ответа | 5 MiB |
| направления MVP | USDT/RUB, BTC/RUB |

## Сводка

Доменов проверено: **25**.

| Статус | Доменов |
| --- | --- |
| `ok` | 1 |
| `not_found` | 14 |
| `html` | 5 |
| `blocked` | 2 |
| `error` | 3 |

| Формат | Доменов |
| --- | --- |
| `own_schema` | 0 |
| `bestchange_compatible` | 1 |
| `csv` | 0 |
| `json` | 0 |
| `unknown` | 24 |

Пригодны для MVP (`ok`): **1**; из них с направлениями MVP: **0**.

## Домены

| Домен | Статус | Формат | Курсов | MVP-направления | Причина / детали | URL фида |
| --- | --- | --- | --- | --- | --- | --- |
| `60cek.org` | `blocked` | `unknown` | 0 | — | challenge_cloudflare<br>HTTP 403, marker="challenge-platform", title="403 Forbidden", final_url=https://60sek.net/rates.xml?legacy_source=8h2eszd | — |
| `alfacash.com` | `blocked` | `unknown` | 0 | — | challenge_cloudflare<br>HTTP 403, marker="just a moment", title="Just a moment...", final_url=https://www.hugedomains.com/domain_profile.cfm?d=alfacash.com | — |
| `baksman.org` | `not_found` | `unknown` | 0 | — | robots_disallowed<br>главная запрещена robots.txt | — |
| `bitpapa.com` | `not_found` | `unknown` | 0 | — | http_404<br>HTTP 404 | — |
| `btc-alpha.com` | `html` | `unknown` | 0 | — | html_page<br>HTTP 200, title="ALP.COM: Bitcoin Exchange \| Cryptocurrency Exchange", final_url=https://www.alp.com/rates.xml | — |
| `changelly.com` | `not_found` | `unknown` | 0 | — | http_404<br>HTTP 404 | — |
| `changenow.io` | `not_found` | `unknown` | 0 | — | http_404<br>HTTP 404 | — |
| `coinpayments.net` | `not_found` | `unknown` | 0 | — | http_404<br>HTTP 404 | — |
| `emcd.io` | `not_found` | `unknown` | 0 | — | http_404<br>HTTP 404 | — |
| `exhub.io` | `html` | `unknown` | 0 | — | html_page<br>HTTP 200, title="exhub.io" | — |
| `exolix.com` | `not_found` | `unknown` | 0 | — | http_404<br>HTTP 404 | — |
| `fixedfloat.com` | `html` | `unknown` | 0 | — | html_page<br>HTTP 200, final_url=https://ff.io/rates.xml | — |
| `garantex.io` | `error` | `unknown` | 0 | — | robots_unavailable<br>robots.txt недоступен (dns_error); проверка домена не выполнена | — |
| `guardarian.com` | `html` | `unknown` | 0 | — | html_page<br>HTTP 200, title="Buy, Sell, and Swap crypto on Guardarian \| Fiat On/Off Ramp &amp; Best rates" | — |
| `kassa.cc` | `not_found` | `unknown` | 0 | — | http_404<br>HTTP 404 | — |
| `letsexchange.io` | `not_found` | `unknown` | 0 | — | http_404<br>HTTP 404 | — |
| `moonpay.com` | `html` | `unknown` | 0 | — | html_page<br>HTTP 200, title="MoonPay: Buy and sell Bitcoin, Ethereum, and other cryptos", final_url=https://www.moonpay.com/rates.xml | — |
| `netex24.net` | `error` | `unknown` | 0 | — | robots_unavailable<br>robots.txt недоступен (tls_error); проверка домена не выполнена | — |
| `pm2btc.com` | `error` | `unknown` | 0 | — | robots_unavailable<br>robots.txt недоступен (timeout); проверка домена не выполнена | — |
| `simpleswap.io` | `not_found` | `unknown` | 0 | — | http_404<br>HTTP 404 | — |
| `simplex.com` | `not_found` | `unknown` | 0 | — | http_404<br>HTTP 404 | — |
| `swapter.io` | `not_found` | `unknown` | 0 | — | http_404<br>HTTP 404 | — |
| `transcoin.me` | `ok` | `bestchange_compatible` | 14 | — | `found`<br>фид найден, курсов: 14 | [фид](https://transcoin.me/bestchange.xml) |
| `utorg.pro` | `not_found` | `unknown` | 0 | — | http_404<br>HTTP 404 | — |
| `xcoex.com` | `not_found` | `unknown` | 0 | — | http_404<br>HTTP 404 | — |

## Пригодные для MVP

### TransCoin (`transcoin.me`)

Формат: `bestchange_compatible`; курсов: 14; обновление фида: не указано; размер: 37345 байт.

| Направление | Курс | Резерв | Сторона |
| --- | --- | --- | --- |
| BTC/EUR | 36320.17766325 | 100000 | unknown |
| BTC/USD | 41253.07553704 | 100000 | unknown |

## Отказы

| Домен | Статус | Причина | Детали |
| --- | --- | --- | --- |
| `60cek.org` | `blocked` | `challenge_cloudflare` | HTTP 403, marker="challenge-platform", title="403 Forbidden", final_url=https://60sek.net/rates.xml?legacy_source=8h2eszd |
| `alfacash.com` | `blocked` | `challenge_cloudflare` | HTTP 403, marker="just a moment", title="Just a moment...", final_url=https://www.hugedomains.com/domain_profile.cfm?d=alfacash.com |
| `baksman.org` | `not_found` | `robots_disallowed` | главная запрещена robots.txt |
| `bitpapa.com` | `not_found` | `http_404` | HTTP 404 |
| `btc-alpha.com` | `html` | `html_page` | HTTP 200, title="ALP.COM: Bitcoin Exchange \| Cryptocurrency Exchange", final_url=https://www.alp.com/rates.xml |
| `changelly.com` | `not_found` | `http_404` | HTTP 404 |
| `changenow.io` | `not_found` | `http_404` | HTTP 404 |
| `coinpayments.net` | `not_found` | `http_404` | HTTP 404 |
| `emcd.io` | `not_found` | `http_404` | HTTP 404 |
| `exhub.io` | `html` | `html_page` | HTTP 200, title="exhub.io" |
| `exolix.com` | `not_found` | `http_404` | HTTP 404 |
| `fixedfloat.com` | `html` | `html_page` | HTTP 200, final_url=https://ff.io/rates.xml |
| `garantex.io` | `error` | `robots_unavailable` | robots.txt недоступен (dns_error); проверка домена не выполнена |
| `guardarian.com` | `html` | `html_page` | HTTP 200, title="Buy, Sell, and Swap crypto on Guardarian \| Fiat On/Off Ramp &amp; Best rates" |
| `kassa.cc` | `not_found` | `http_404` | HTTP 404 |
| `letsexchange.io` | `not_found` | `http_404` | HTTP 404 |
| `moonpay.com` | `html` | `html_page` | HTTP 200, title="MoonPay: Buy and sell Bitcoin, Ethereum, and other cryptos", final_url=https://www.moonpay.com/rates.xml |
| `netex24.net` | `error` | `robots_unavailable` | robots.txt недоступен (tls_error); проверка домена не выполнена |
| `pm2btc.com` | `error` | `robots_unavailable` | robots.txt недоступен (timeout); проверка домена не выполнена |
| `simpleswap.io` | `not_found` | `http_404` | HTTP 404 |
| `simplex.com` | `not_found` | `http_404` | HTTP 404 |
| `swapter.io` | `not_found` | `http_404` | HTTP 404 |
| `utorg.pro` | `not_found` | `http_404` | HTTP 404 |
| `xcoex.com` | `not_found` | `http_404` | HTTP 404 |

Домены со статусом `blocked` не обходились: не подменялся User-Agent, не решались капчи, не использовались прокси и авторизация.

## Выводы и следующий шаг

- Пригодных для машинного чтения фидов: **1 из 25** (4%).
- Ни один фид не отдал направления MVP в чистом виде.
- Преобладающий формат: `bestchange_compatible` (1 дом.). Собственная схема требует отдельного парсера на источник, BestChange-совместимый фид — одного парсера на все.
- `blocked`: 2 дом. — доступ закрыт (403/429/челлендж/логин), обход не выполнялся.
- `error`: 3 дом. — технические сбои и невалидные фиды; часть из них стоит перепроверить вручную.
- Следующий шаг: расширить список обменников — пригодные фиды есть, но нужных направлений в них нет.
