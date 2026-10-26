"""Экспорт статистики по активам: агрегаты без адресов и названий кошельков."""

import csv
import io
from decimal import Decimal
from urllib.parse import unquote

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import BTC, EVM, HL, LTC, PRICES, TON
from pouch.main import create_app
from pouch.portfolio import Portfolio
from pouch.vault import Vault

PASSWORD = "correct horse battery"
BTC_USD = 41.71435165 * PRICES["bitcoin"]


@pytest.fixture
def c(tmp_path, fake_api):
    vault = Vault(tmp_path / "data", 900)
    app = create_app(vault, allowed_hosts={"testserver"}, static_dir=tmp_path / "none",
                     portfolio_service=Portfolio(vault, transport=httpx.MockTransport(fake_api)))
    fake_api.hl_scenario = "unified_staking"
    with TestClient(app) as client:
        client.post("/api/setup", json={"password": PASSWORD})
        client.put("/api/settings/keys/zerion", json={"value": "k"})
        ids = {}
        for name, network, address in (("Холодный BTC", "btc", BTC), ("LTC", "ltc", LTC), ("EVM", "evm", EVM),
                                       ("TON", "ton", TON), ("HL", "hyperliquid", HL["unified_staking"])):
            ids[name] = client.post("/api/wallets", json={"name": name, "network": network, "address": address}).json()["id"]
        client.post("/api/refresh")
        client.ids = ids
        yield client


def export(c, **body):
    return c.post("/api/export/assets", json={"password": PASSWORD, **body})


def assert_no_wallet_info(text: str):
    for secret in (BTC, LTC, EVM, TON, HL["unified_staking"], "Холодный", "0x", "EQ"):
        assert secret.lower() not in text.lower(), secret


def test_current_csv_aggregates_without_wallets(c):
    r = export(c, format="csv", scope="current")
    assert r.status_code == 200
    text = r.content.decode("utf-8-sig")
    assert_no_wallet_info(text)
    rows = list(csv.DictReader(io.StringIO(text)))
    assert list(rows[0]) == ["asset", "name", "amount", "price_usd", "value_usd", "value_rub", "share_pct"]
    btc = next(r for r in rows if r["asset"] == "BTC")
    assert float(btc["value_usd"]) == pytest.approx(BTC_USD, abs=0.01)
    assert float(btc["price_usd"]) == pytest.approx(PRICES["bitcoin"])
    total = rows[-1]
    assert total["asset"] == "ИТОГО" and total["share_pct"] == "100.00"
    assert float(total["value_usd"]) == pytest.approx(sum(float(r["value_usd"]) for r in rows[:-1]), abs=0.05)
    assert sum(float(r["share_pct"]) for r in rows[:-1]) == pytest.approx(100, abs=0.1)
    # USDC по всем кошелькам: спот Hyperliquid + 1200.5 на EVM − 3000 долга в Aave.
    hl_usdc = Decimal("4688103.4294539903")
    assert Decimal(next(r for r in rows if r["asset"] == "USDC")["amount"]) == hl_usdc + Decimal("1200.5") - 3000


def test_current_json_for_portfolio(c):
    pid = c.post("/api/portfolios", json={"name": "Сбережения", "wallet_ids": [c.ids["Холодный BTC"], c.ids["LTC"]]}).json()["id"]
    r = export(c, format="json", scope="current", portfolio_id=pid)
    assert_no_wallet_info(r.text)
    data = r.json()
    assert data["portfolio"] == "Сбережения"
    assert {a["asset"] for a in data["assets"]} == {"BTC", "LTC"}
    assert float(data["total_rub"]) == pytest.approx(float(data["total_usd"]) * 80.13, rel=1e-4)
    name = unquote(r.headers["content-disposition"].split("filename*=UTF-8''")[1])
    assert name.startswith("Pouch — Сбережения — активы")


def test_history(c):
    r = export(c, format="csv", scope="history")
    text = r.content.decode("utf-8-sig")
    assert_no_wallet_info(text)
    rows = list(csv.DictReader(io.StringIO(text)))
    assert {r["taken_at"] for r in rows} and {"BTC", "LTC", "HYPE"} <= {r["asset"] for r in rows}
    data = export(c, format="json", scope="history").json()
    assert len(data["snapshots"]) == 1
    assert float(data["snapshots"][0]["total_usd"]) > 0


def test_history_respects_portfolio(c):
    pid = c.post("/api/portfolios", json={"name": "BTC", "wallet_ids": [c.ids["Холодный BTC"]]}).json()["id"]
    data = export(c, format="json", scope="history", portfolio_id=pid).json()
    assets = data["snapshots"][0]["assets"]
    assert [a["asset"] for a in assets] == ["BTC"]
    assert float(assets[0]["value_usd"]) == pytest.approx(BTC_USD, abs=0.01)


def test_requires_password_and_portfolio(c):
    assert c.post("/api/export/assets", json={"password": "wrong pass"}).status_code == 403
    assert export(c, portfolio_id=999).status_code == 404


def test_snapshot_json_has_no_hyperliquid_addresses(c):
    r = c.post("/api/export/snapshots", json={"password": PASSWORD, "format": "json"})
    hl = r.json()["snapshots"][0]["hyperliquid"]
    assert hl and all("address" not in a for a in hl)
    assert HL["unified_staking"] not in r.text


def test_english_labels(c):
    r = export(c, format="csv", scope="current", lang="en")
    rows = list(csv.DictReader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert rows[-1]["asset"] == "TOTAL"
    data = export(c, format="json", scope="current", lang="en").json()
    assert data["portfolio"] == "All wallets"
    assert "All wallets" in unquote(r.headers["content-disposition"])
