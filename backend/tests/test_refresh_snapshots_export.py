"""Автообновление, статусы, снапшоты, экспорт и зашифрованный бэкап."""

import csv
import io
import json
import zipfile
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import BTC, HL, LTC, PRICES
from pouch import snapshots
from pouch.main import create_app
from pouch.portfolio import Portfolio
from pouch.vault import Vault

PASSWORD = "correct horse battery"


@pytest.fixture
def setup(tmp_path, fake_api):
    vault = Vault(tmp_path / "data", 900)
    portfolio = Portfolio(vault, transport=httpx.MockTransport(fake_api))
    app = create_app(vault, allowed_hosts={"testserver"}, static_dir=tmp_path / "none", portfolio_service=portfolio)
    with TestClient(app) as c:
        c.post("/api/setup", json={"password": PASSWORD})
        c.btc = c.post("/api/wallets", json={"name": "BTC", "network": "btc", "address": BTC}).json()["id"]
        c.ltc = c.post("/api/wallets", json={"name": "LTC", "network": "ltc", "address": LTC}).json()["id"]
        yield c, vault, portfolio, fake_api


def count(vault):
    return snapshots.summary(vault)["count"]


# --- автообновление и статусы ---


def test_refresh_interval_setting(setup):
    c, *_ = setup
    assert c.get("/api/settings/general").json() == {"dust_threshold_usd": 1.0, "refresh_interval_minutes": 5}
    assert c.put("/api/settings/general", json={"refresh_interval_minutes": 15}).json()["refresh_interval_minutes"] == 15
    assert c.get("/api/settings/general").json()["dust_threshold_usd"] == 1.0  # частичное обновление
    assert c.put("/api/settings/general", json={"refresh_interval_minutes": 0}).status_code == 422
    assert c.put("/api/settings/general", json={"refresh_interval_minutes": 61}).status_code == 422


def test_background_refresh_does_not_extend_session(setup):
    c, vault, *_ = setup
    vault._last_activity = 0.0
    c.post("/api/refresh", headers={"X-Pouch-Background": "1"})
    assert vault._last_activity == 0.0
    c.post("/api/refresh")
    assert vault._last_activity > 0


def test_old_data_marked_stale(setup):
    c, vault, *_ = setup
    c.post("/api/refresh")
    assert {w["status"] for w in c.get("/api/portfolio").json()["wallets"]} == {"fresh"}
    old = (datetime.now(UTC) - timedelta(minutes=16)).isoformat(timespec="seconds")
    with vault.db() as conn:
        conn.execute("UPDATE wallet_state SET fetched_at = ? WHERE wallet_id = ?", (old, c.btc))
    statuses = {w["id"]: w["status"] for w in c.get("/api/portfolio").json()["wallets"]}
    assert statuses == {c.btc: "stale", c.ltc: "fresh"}  # 16 минут > 3 × 5


# --- снапшоты ---


def test_snapshot_after_full_refresh_once_per_hour(setup):
    c, vault, *_ = setup
    c.post("/api/refresh")
    c.post("/api/refresh")
    assert count(vault) == 1


def test_no_snapshot_when_source_failed(setup):
    c, vault, _, fake_api = setup
    fake_api.failing.add("mempool.space")
    c.post("/api/refresh")
    assert count(vault) == 0
    fake_api.failing = {"api.coingecko.com"}
    c.post("/api/refresh")
    assert count(vault) == 0


def test_snapshot_contents(setup):
    c, vault, portfolio, _ = setup
    pid = c.post("/api/portfolios", json={"name": "Только BTC", "wallet_ids": [c.btc]}).json()["id"]
    c.post("/api/refresh")
    data = json.loads(snapshots.export_json(vault))["snapshots"][0]
    btc_value = 41.71435165 * PRICES["bitcoin"]
    assert {i["wallet_id"] for i in data["items"]} == {c.btc, c.ltc}
    btc = next(i for i in data["items"] if i["wallet_id"] == c.btc)
    assert btc["symbol"] == "BTC" and btc["amount"] == "41.71435165" and float(btc["value_usd"]) == pytest.approx(btc_value)
    assert float(data["usd_rub"]) == pytest.approx(80.13)
    totals = {p["name"]: float(p["total_usd"]) for p in data["portfolios"]}
    assert totals["Только BTC"] == pytest.approx(btc_value)
    assert totals["Все кошельки"] == pytest.approx(float(data["total_usd"]))
    assert {p["portfolio_id"] for p in data["portfolios"]} == {None, pid}


def test_snapshot_survives_rename_and_delete(setup):
    c, vault, *_ = setup
    c.post("/api/refresh")
    c.patch(f"/api/wallets/{c.btc}", json={"name": "Холодный"})
    rows = list(csv.DictReader(io.StringIO(snapshots.export_csv(vault).decode("utf-8-sig"))))
    assert {r["wallet_name"] for r in rows if r["wallet_id"] == str(c.btc)} == {"Холодный"}
    c.delete(f"/api/wallets/{c.btc}")
    rows = list(csv.DictReader(io.StringIO(snapshots.export_csv(vault).decode("utf-8-sig"))))
    assert any(r["wallet_id"] == str(c.btc) for r in rows)  # история не потерялась


def test_snapshot_hyperliquid_accounts(setup):
    c, vault, _, fake_api = setup
    fake_api.hl_scenario = "standard_subs"
    c.post("/api/wallets", json={"name": "HL", "network": "hyperliquid", "address": HL["standard_subs"]})
    c.post("/api/refresh")
    hl = json.loads(snapshots.export_json(vault))["snapshots"][0]["hyperliquid"]
    assert len(hl) == 5 and hl[0]["label"] == "Мастер" and hl[0]["mode"] == "disabled"
    assert all(float(a["total_usd"]) >= 0 for a in hl)


def test_take_if_due_respects_hour(setup):
    c, vault, portfolio, _ = setup
    c.post("/api/refresh")
    now = datetime.now(UTC)
    assert snapshots.take_if_due(portfolio, now + timedelta(minutes=59)) is None
    assert snapshots.take_if_due(portfolio, now + timedelta(minutes=61)) is not None
    assert count(vault) == 2


# --- экспорт ---


def test_export_requires_password(setup):
    c, *_ = setup
    c.post("/api/refresh")
    assert c.post("/api/export/snapshots", json={"password": "wrong one", "format": "csv"}).status_code == 403
    assert c.post("/api/export/backup", json={"password": "wrong one"}).status_code == 403
    c.cookies.clear()
    assert c.post("/api/export/snapshots", json={"password": PASSWORD}).status_code == 401


def test_export_files(setup):
    c, *_ = setup
    c.post("/api/refresh")
    r = c.post("/api/export/snapshots", json={"password": PASSWORD, "format": "csv"})
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert {row["symbol"] for row in rows} == {"BTC", "LTC"}
    r = c.post("/api/export/snapshots", json={"password": PASSWORD, "format": "json"})
    assert len(r.json()["snapshots"]) == 1


def test_backup_is_encrypted_and_restorable(setup, tmp_path):
    c, *_ = setup
    c.post("/api/refresh")
    r = c.post("/api/export/backup", json={"password": PASSWORD})
    archive = zipfile.ZipFile(io.BytesIO(r.content))
    assert set(archive.namelist()) == {"pouch.db", "pouch.meta.json", "README.txt"}
    db = archive.read("pouch.db")
    assert not db.startswith(b"SQLite format 3") and BTC.encode() not in db

    restored = tmp_path / "restored"
    restored.mkdir()
    archive.extract("pouch.db", restored)
    archive.extract("pouch.meta.json", restored)
    vault = Vault(restored, 900)
    vault.unlock(PASSWORD)
    with vault.db() as conn:
        assert {r["address"] for r in conn.execute("SELECT address FROM wallets")} == {BTC, LTC}
