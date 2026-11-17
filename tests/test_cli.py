"""CLI contract: exit codes and end-to-end run without real feeds (AC-02, AC-32)."""

from __future__ import annotations

import json

import pytest

from feedscan.cli import EXIT_INPUT, main


def write_input(tmp_path, rows: str, *, header="name,domain,url,source,notes"):
    path = tmp_path / "exchangers.csv"
    path.write_text(header + "\n" + rows, encoding="utf-8")
    return path


def write_currencies(tmp_path):
    path = tmp_path / "currencies.csv"
    path.write_text(
        "code,name,type,aliases\nUSDT,Tether,crypto,USDTTRC\nRUB,Rouble,fiat,\n",
        encoding="utf-8",
    )
    return path


def test_missing_input_returns_exit_2(tmp_path, capsys):
    code = main(["--input", str(tmp_path / "absent.csv"), "--out-dir", str(tmp_path / "out")])
    assert code == EXIT_INPUT
    assert "not found" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_missing_column_returns_exit_2_and_writes_nothing(tmp_path, capsys):
    path = write_input(tmp_path, "A,a.test,https://a.test/,manual,", header="name,domain,url,notes")
    code = main(
        [
            "--input",
            str(path),
            "--currencies",
            str(write_currencies(tmp_path)),
            "--out-dir",
            str(tmp_path / "out"),
        ]
    )
    assert code == EXIT_INPUT
    assert "source" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_broken_registry_returns_exit_2(tmp_path, capsys):
    path = write_input(tmp_path, "A,a.test,https://a.test/,manual,")
    currencies = tmp_path / "currencies.csv"
    currencies.write_text("code,name,type,aliases\nusdt,Tether,crypto,\n", encoding="utf-8")
    code = main(
        [
            "--input",
            str(path),
            "--currencies",
            str(currencies),
            "--out-dir",
            str(tmp_path / "out"),
        ]
    )
    assert code == EXIT_INPUT
    assert "currencies.csv" in capsys.readouterr().err


def test_run_on_unresolvable_domains_writes_reports_and_exits_zero(tmp_path, capsys):
    path = write_input(
        tmp_path,
        "Nowhere,nowhere.invalid,https://nowhere.invalid/,manual,reserved TLD\n"
        "Nowhere Two,nowhere-two.invalid,https://nowhere-two.invalid/,bestchange,\n",
    )
    out_dir = tmp_path / "out"
    code = main(
        [
            "--input",
            str(path),
            "--currencies",
            str(write_currencies(tmp_path)),
            "--out-dir",
            str(out_dir),
            "--per-domain-interval",
            "0",
            "--retries",
            "0",
            "--timeout",
            "2",
            "--connect-timeout",
            "2",
        ]
    )
    stderr = capsys.readouterr().err
    assert code == 0, stderr
    assert (out_dir / "feeds.json").exists()
    assert (out_dir / "feeds.md").exists()

    report = json.loads((out_dir / "feeds.json").read_text(encoding="utf-8"))
    assert report["summary"]["domains_total"] == 2
    assert report["summary"]["status_counts"]["ok"] == 0
    assert sum(report["summary"]["status_counts"].values()) == 2
    assert "FEEDSCAN_CONTACT" in stderr


def test_missing_contact_produces_warning_but_no_browser_ua(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("FEEDSCAN_CONTACT", raising=False)
    path = write_input(tmp_path, "Nowhere,nowhere.invalid,https://nowhere.invalid/,manual,")
    code = main(
        [
            "--input",
            str(path),
            "--currencies",
            str(write_currencies(tmp_path)),
            "--out-dir",
            str(tmp_path / "out"),
            "--per-domain-interval",
            "0",
            "--retries",
            "0",
            "--timeout",
            "2",
            "--connect-timeout",
            "2",
        ]
    )
    assert code == 0
    stderr = capsys.readouterr().err
    assert "FEEDSCAN_CONTACT" in stderr
    report = json.loads((tmp_path / "out" / "feeds.json").read_text(encoding="utf-8"))
    assert report["config"]["ua_contact"] is None
    assert "Mozilla" not in json.dumps(report)


def test_invalid_flag_values_return_exit_2(tmp_path):
    path = write_input(tmp_path, "A,a.test,https://a.test/,manual,")
    code = main(
        [
            "--input",
            str(path),
            "--currencies",
            str(write_currencies(tmp_path)),
            "--out-dir",
            str(tmp_path / "out"),
            "--retries",
            "-1",
        ]
    )
    assert code == EXIT_INPUT
