"""Currency registry: aliases, ambiguity, validation (ADR-0001)."""

from __future__ import annotations

import pytest

from feedscan.currencies import CurrencyRegistry, RegistryError


def write_registry(tmp_path, body: str):
    path = tmp_path / "currencies.csv"
    path.write_text("code,name,type,aliases\n" + body, encoding="utf-8")
    return path


def test_resolves_codes_aliases_and_case(tmp_path):
    path = write_registry(
        tmp_path,
        "USDT,Tether,crypto,USDTTRC|USDT-TRC20\n"
        "RUB,Russian rouble,fiat,SberbankRUB|TinkoffRUB\n"
        "XAU,Gold,metal,\n",
    )
    registry = CurrencyRegistry.load(path)
    assert registry.resolve("USDT") == "USDT"
    assert registry.resolve("usdttrc") == "USDT"
    assert registry.resolve(" USDT-TRC20 ") == "USDT"
    assert registry.resolve("SberbankRUB") == "RUB"
    assert registry.resolve("xau") == "XAU"
    assert registry.get("XAU").type == "metal"


def test_unknown_identifier_is_not_guessed(tmp_path):
    path = write_registry(tmp_path, "USDT,Tether,crypto,USDTTRC\n")
    registry = CurrencyRegistry.load(path)
    assert registry.resolve("USDTTRC20") is None
    assert registry.resolve("") is None
    assert registry.resolve(None) is None


def test_duplicate_code_is_a_configuration_error(tmp_path):
    path = write_registry(
        tmp_path,
        "USDT,Tether,crypto,\nUSDT,Tether duplicate,crypto,\n",
    )
    with pytest.raises(RegistryError, match="duplicate currency code"):
        CurrencyRegistry.load(path)


def test_ambiguous_alias_is_a_configuration_error(tmp_path):
    path = write_registry(
        tmp_path,
        "USDT,Tether,crypto,USDTERC\nUSDC,USD Coin,crypto,USDTERC\n",
    )
    with pytest.raises(RegistryError, match="ambiguous alias"):
        CurrencyRegistry.load(path)


def test_invalid_type_and_code_are_configuration_errors(tmp_path):
    with pytest.raises(RegistryError, match="invalid type"):
        CurrencyRegistry.load(write_registry(tmp_path, "USDT,Tether,token,\n"))
    with pytest.raises(RegistryError, match="invalid currency code"):
        CurrencyRegistry.load(write_registry(tmp_path, "usdt,Tether,crypto,\n"))


def test_missing_registry_file_is_a_configuration_error(tmp_path):
    with pytest.raises(RegistryError, match="not found"):
        CurrencyRegistry.load(tmp_path / "absent.csv")


def test_missing_required_column_is_a_configuration_error(tmp_path):
    path = tmp_path / "currencies.csv"
    path.write_text("code,name\nUSDT,Tether\n", encoding="utf-8")
    with pytest.raises(RegistryError, match="missing required column"):
        CurrencyRegistry.load(path)
