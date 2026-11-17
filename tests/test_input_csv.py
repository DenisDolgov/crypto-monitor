"""Input CSV reading (AC-01 … AC-05)."""

from __future__ import annotations

import pytest

from feedscan.input_csv import InputError, read_exchangers


def test_reads_all_rows_and_columns(fixtures_dir):
    data = read_exchangers(fixtures_dir / "exchangers_ok.csv")
    assert len(data.rows) == 2
    assert data.input_errors == 0
    first = data.rows[0]
    assert first.name == "Exchanger A"
    assert first.domain == "exchanger-a.test"
    assert first.url == "https://exchanger-a.test/"
    assert first.source == "manual"


def test_missing_column_is_fatal(fixtures_dir):
    with pytest.raises(InputError, match="source"):
        read_exchangers(fixtures_dir / "bad_header.csv")


def test_missing_file_is_fatal(tmp_path):
    with pytest.raises(InputError, match="not found"):
        read_exchangers(tmp_path / "absent.csv")


def test_bad_rows_are_skipped_and_counted(fixtures_dir):
    data = read_exchangers(fixtures_dir / "mixed.csv")
    assert [row.domain for row in data.rows] == ["exchanger-a.test", "exchanger-b.test"]
    assert data.input_errors == 3
    assert any("invalid domain" in error for error in data.errors)
    assert any("invalid url" in error for error in data.errors)
    assert any("unknown source" in error for error in data.errors)


def test_duplicate_domain_keeps_first_row_and_warns(fixtures_dir):
    data = read_exchangers(fixtures_dir / "dupes.csv")
    assert [row.domain for row in data.rows] == ["exchanger-a.test", "exchanger-b.test"]
    assert data.rows[0].name == "Exchanger A"
    assert any("duplicate_domain:exchanger-a.test" in warning for warning in data.warnings)


def test_utf8_bom_does_not_leak_into_first_column(fixtures_dir):
    data = read_exchangers(fixtures_dir / "bom.csv")
    assert len(data.rows) == 1
    assert data.rows[0].name == "Обменник А"
    assert data.rows[0].domain == "exchanger-a.test"


def test_extra_column_is_ignored_with_warning(tmp_path):
    path = tmp_path / "extra.csv"
    path.write_text(
        "name,domain,url,source,notes,owner\n"
        "Exchanger A,exchanger-a.test,https://exchanger-a.test/,manual,,tbd\n",
        encoding="utf-8",
    )
    data = read_exchangers(path)
    assert len(data.rows) == 1
    assert "extra_column:owner" in data.warnings


def test_rows_are_not_required_to_be_twenty(tmp_path):
    path = tmp_path / "one.csv"
    path.write_text(
        "name,domain,url,source,notes\n"
        "Only,only.test,https://only.test/,bestchange,\n",
        encoding="utf-8",
    )
    data = read_exchangers(path)
    assert len(data.rows) == 1
