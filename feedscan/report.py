"""Reports: ``feeds.json`` and ``feeds.md`` (MYSP-2 §5.2–5.3).

The report is a pure function of the run results plus a timestamp, so two runs
over the same feeds differ only in ``generated_at``, ``checked_at`` and
``duration_ms`` (AC-12).
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .config import RunConfig
from .models import DomainResult, Format, Status, format_datetime

STATUS_ORDER = (Status.OK, Status.NOT_FOUND, Status.HTML, Status.BLOCKED, Status.ERROR)
FORMAT_ORDER = (
    Format.OWN_SCHEMA,
    Format.BESTCHANGE_COMPATIBLE,
    Format.CSV,
    Format.JSON,
    Format.UNKNOWN,
)


def build_report(results, config: RunConfig, generated_at: datetime) -> dict:
    ordered = sorted(results, key=lambda item: item.domain)
    domains = [_domain_dict(item) for item in ordered]

    status_counts = {status.value: 0 for status in STATUS_ORDER}
    format_counts = {fmt.value: 0 for fmt in FORMAT_ORDER}
    dropped: dict[str, int] = {}
    unmapped: dict[str, int] = {}

    for item in ordered:
        status_counts[item.status.value] = status_counts.get(item.status.value, 0) + 1
        format_counts[item.format.value] = format_counts.get(item.format.value, 0) + 1
        for reason, count in item.dropped_by_reason.items():
            if reason.startswith("unmapped_currency:"):
                key = reason.split(":", 1)[1]
                unmapped[key] = unmapped.get(key, 0) + count
            else:
                dropped[reason] = dropped.get(reason, 0) + count

    top_unmapped = [
        {"id": key, "count": count}
        for key, count in sorted(unmapped.items(), key=lambda pair: (-pair[1], pair[0]))[:5]
    ]

    return {
        "generated_at": format_datetime(generated_at),
        "config": config.to_report_dict(),
        "summary": {
            "domains_total": len(ordered),
            "status_counts": status_counts,
            "format_counts": format_counts,
            "feeds_ok": status_counts[Status.OK.value],
            "feeds_with_mvp_directions": sum(
                1 for item in ordered if item.mvp_directions_found
            ),
            "input_errors": 0,
            "dropped_rate_rows": dict(sorted(dropped.items())),
            "top_unmapped_currencies": top_unmapped,
        },
        "domains": domains,
    }


def _domain_dict(item: DomainResult) -> dict:
    return {
        "domain": item.domain,
        "name": item.name,
        "source": item.source,
        "status": item.status.value,
        "reason_code": item.reason_code,
        "reason_detail": item.reason_detail,
        "format": item.format.value,
        "format_evidence": item.format_evidence,
        "final_url": item.final_url,
        "http_status": item.http_status,
        "content_type": item.content_type,
        "encoding": item.encoding,
        "gzip": item.gzip,
        "size_bytes": item.size_bytes,
        "updated_at": format_datetime(item.updated_at),
        "rate_count": item.rate_count,
        "mvp_directions_found": list(item.mvp_directions_found),
        "examples": item.examples(2),
        "warnings": list(item.warnings),
        "attempts": [attempt.to_dict() for attempt in item.attempts],
        "checked_at": format_datetime(item.checked_at),
        "duration_ms": item.duration_ms,
        "dropped_rate_rows": dict(sorted(item.dropped_by_reason.items())),
        "notes": item.notes,
    }


def render_markdown(report: dict) -> str:
    summary = report["summary"]
    config = report["config"]
    domains = report["domains"]
    lines: list[str] = []

    lines.append("# Инвентаризация фидов обменников")
    lines.append("")
    lines.append(f"Сформировано: {report['generated_at']}")
    lines.append("")
    lines.append("## Параметры прогона")
    lines.append("")
    lines.append("| Параметр | Значение |")
    lines.append("| --- | --- |")
    lines.append(f"| вход | `{config['input']}` |")
    lines.append(f"| контакт в User-Agent | `{config['ua_contact'] or 'не задан'}` |")
    lines.append(f"| таймаут запроса | {config['timeout_s']} с |")
    lines.append(f"| ретраи | {config['retries']} |")
    lines.append(f"| пауза между запросами к домену | {config['per_domain_interval_s']} с |")
    lines.append(f"| лимит ответа | {config['max_bytes'] // (1024 * 1024)} MiB |")
    lines.append(f"| направления MVP | {', '.join(config['mvp_directions'])} |")
    lines.append("")

    lines.append("## Сводка")
    lines.append("")
    lines.append(f"Доменов проверено: **{summary['domains_total']}**.")
    lines.append("")
    lines.append("| Статус | Доменов |")
    lines.append("| --- | --- |")
    for status in STATUS_ORDER:
        lines.append(f"| `{status.value}` | {summary['status_counts'].get(status.value, 0)} |")
    lines.append("")
    lines.append("| Формат | Доменов |")
    lines.append("| --- | --- |")
    for fmt in FORMAT_ORDER:
        lines.append(f"| `{fmt.value}` | {summary['format_counts'].get(fmt.value, 0)} |")
    lines.append("")
    lines.append(
        f"Пригодны для MVP (`ok`): **{summary['feeds_ok']}**; "
        f"из них с направлениями MVP: **{summary['feeds_with_mvp_directions']}**."
    )
    lines.append("")

    lines.append("## Домены")
    lines.append("")
    lines.append("| Домен | Статус | Формат | Курсов | MVP-направления | Причина / детали | URL фида |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for item in domains:
        directions = ", ".join(item["mvp_directions_found"]) or "—"
        reason = item["reason_code"] or "—"
        detail = _md_escape(item["reason_detail"] or "")
        if item["status"] == Status.OK.value:
            reason = f"`{reason}`"
        lines.append(
            f"| `{item['domain']}` | `{item['status']}` | `{item['format']}` | "
            f"{item['rate_count']} | {directions} | {reason}<br>{detail} | "
            f"{_link(item['final_url'], 'фид' if item['status'] == Status.OK.value else 'последний URL')} |"
        )
    lines.append("")

    mvp_domains = [item for item in domains if item["status"] == Status.OK.value]
    lines.append("## Пригодные для MVP")
    lines.append("")
    if mvp_domains:
        for item in mvp_domains:
            lines.append(f"### {item['name']} (`{item['domain']}`)")
            lines.append("")
            lines.append(f"Формат: `{item['format']}`; курсов: {item['rate_count']}; "
                         f"обновление фида: {item['updated_at'] or 'не указано'}; "
                         f"размер: {item['size_bytes']} байт.")
            if item["mvp_directions_found"]:
                lines.append(f"Направления MVP: {', '.join(item['mvp_directions_found'])}.")
            if item["examples"]:
                lines.append("")
                lines.append("| Направление | Курс | Резерв | Сторона |")
                lines.append("| --- | --- | --- | --- |")
                for example in item["examples"]:
                    lines.append(
                        f"| {example['base']}/{example['quote']} | {example['rate']} | "
                        f"{example['reserve'] or '—'} | {example['side']} |"
                    )
            lines.append("")
    else:
        lines.append("Пригодных фидов не найдено.")
        lines.append("")

    refusals = [item for item in domains if item["status"] != Status.OK.value]
    lines.append("## Отказы")
    lines.append("")
    if refusals:
        lines.append("| Домен | Статус | Причина | Детали |")
        lines.append("| --- | --- | --- | --- |")
        for item in refusals:
            lines.append(
                f"| `{item['domain']}` | `{item['status']}` | `{item['reason_code']}` | "
                f"{_md_escape(item['reason_detail'] or '')} |"
            )
        blocked = [item for item in refusals if item["status"] == Status.BLOCKED.value]
        if blocked:
            lines.append("")
            lines.append(
                "Домены со статусом `blocked` не обходились: не подменялся User-Agent, "
                "не решались капчи, не использовались прокси и авторизация."
            )
    else:
        lines.append("Отказов нет.")
    lines.append("")

    lines.append("## Выводы и следующий шаг")
    lines.append("")
    lines.extend(_conclusions(report))
    lines.append("")
    return "\n".join(lines)


def _conclusions(report: dict) -> list[str]:
    summary = report["summary"]
    total = summary["domains_total"] or 1
    ok = summary["feeds_ok"]
    formats = {
        key: value
        for key, value in summary["format_counts"].items()
        if key != "unknown" and value > 0
    }
    lines: list[str] = []

    lines.append(
        f"- Пригодных для машинного чтения фидов: **{ok} из {summary['domains_total']}** "
        f"({ok * 100 // total}%)."
    )
    if summary["feeds_with_mvp_directions"]:
        lines.append(
            f"- Направления MVP (`{', '.join(report['config']['mvp_directions'])}`) найдены "
            f"у **{summary['feeds_with_mvp_directions']}** доменов."
        )
    else:
        lines.append("- Ни один фид не отдал направления MVP в чистом виде.")

    if formats:
        dominant = max(formats.items(), key=lambda pair: pair[1])
        lines.append(
            f"- Преобладающий формат: `{dominant[0]}` ({dominant[1]} дом.). "
            f"Собственная схема требует отдельного парсера на источник, "
            f"BestChange-совместимый фид — одного парсера на все."
        )
    else:
        lines.append(
            "- Ни один формат не определён: распознанных фидов нет, "
            "поэтому сравнивать распространённость форматов пока не на чем."
        )
    blocked = summary["status_counts"].get(Status.BLOCKED.value, 0)
    if blocked:
        lines.append(
            f"- `blocked`: {blocked} дом. — доступ закрыт (403/429/челлендж/логин), "
            f"обход не выполнялся."
        )
    errors = summary["status_counts"].get(Status.ERROR.value, 0)
    if errors:
        lines.append(
            f"- `error`: {errors} дом. — технические сбои и невалидные фиды; "
            f"часть из них стоит перепроверить вручную."
        )

    if ok == 0:
        lines.append(
            "- Следующий шаг: искать обменники с публичной документацией фида "
            "или договариваться о доступе. Без пригодных фидов хранилище и алерты "
            "строить не на чем."
        )
    elif summary["feeds_with_mvp_directions"] == 0:
        lines.append(
            "- Следующий шаг: расширить список обменников — пригодные фиды есть, "
            "но нужных направлений в них нет."
        )
    else:
        lines.append(
            "- Следующий шаг: взять домены с направлениями MVP в прототип опроса "
            "и хранения курсов; остальные фиды оставить как расширение покрытия."
        )
    return lines


def _link(url, label: str = "фид") -> str:
    return f"[{label}]({url})" if url else "—"


def _md_escape(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def write_atomic(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(path.parent), delete=False, suffix=".tmp"
    )
    try:
        with handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, path)
    except BaseException:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise


def write_reports(report: dict, out_dir: Path) -> tuple[Path, Path]:
    json_path = out_dir / "feeds.json"
    md_path = out_dir / "feeds.md"
    write_atomic(json_path, json.dumps(report, ensure_ascii=False, indent=2, sort_keys=False) + "\n")
    write_atomic(md_path, render_markdown(report))
    return json_path, md_path


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
