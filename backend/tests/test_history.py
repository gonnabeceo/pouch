"""История для графиков: реальная по снапшотам и моделирование по историческим ценам."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient

import conftest
from conftest import BTC, EVM, LTC
from pouch import history, snapshots
from pouch.main import create_app
from pouch.portfolio import Portfolio
from pouch.vault import Vault

BTC_AMOUNT = 41.71435165
LTC_AMOUNT = (49530687893780 - 47442489351900) / 1e8


@pytest.fixture
def c(tmp_path, fake_api, monkeypatch):
    monkeypatch.setitem(conftest.PRICES, "bitcoin", 80000)
    # Снапшоты в этих тестах ставим сами, с нужным временем.
    monkeypatch.setattr(snapshots, "take_if_due", lambda *a, **k: None)
    monkeypatch.setitem(history.PACE_SECONDS, "keyless", 0)
    vault = Vault(tmp_path / "data", 900)
    portfolio = Portfolio(vault, transport=httpx.MockTransport(fake_api))
    app = create_app(vault, allowed_hosts={"testserver"}, static_dir=tmp_path / "none", portfolio_service=portfolio)
    with TestClient(app) as client:
        client.post("/api/setup", json={"password": "correct horse battery"})
        client.btc = client.post("/api/wallets", json={"name": "BTC", "network": "btc", "address": BTC}).json()["id"]
        client.ltc = client.post("/api/wallets", json={"name": "LTC", "network": "ltc", "address": LTC}).json()["id"]
        client.portfolio, client.fake_api = portfolio, fake_api
        client.post("/api/refresh")
        yield client


def snapshot_at(c, btc_price: float, ago: timedelta):
    conftest.PRICES["bitcoin"] = btc_price
    c.post("/api/refresh")
    snapshots.take(c.portfolio, datetime.now(UTC) - ago)


def test_snapshot_history_totals_and_change(c):
    snapshot_at(c, 40000, timedelta(days=3))
    snapshot_at(c, 60000, timedelta(days=1))
    conftest.PRICES["bitcoin"] = 80000
    c.post("/api/refresh")
    h = c.get("/api/history", params={"days": 7}).json()
    totals = [p["total_usd"] for p in h["points"]]
    ltc = LTC_AMOUNT * conftest.PRICES["litecoin"]
    assert totals[0] == pytest.approx(BTC_AMOUNT * 40000 + ltc)
    assert totals[-1] == pytest.approx(BTC_AMOUNT * 80000 + ltc)
    assert h["points"][-1].get("live") is True
    assert h["assets"]["top"] == ["BTC", "LTC"]
    first_row = h["assets"]["rows"][0]["values"]
    assert first_row["BTC"] == pytest.approx(BTC_AMOUNT * 40000) and first_row["Прочее"] == 0
    btc_series = next(w for w in h["wallets"] if w["id"] == c.btc)["series"]
    assert btc_series[0]["value_usd"] == pytest.approx(BTC_AMOUNT * 40000)


def test_range_filter(c):
    snapshot_at(c, 40000, timedelta(days=3))
    snapshot_at(c, 60000, timedelta(hours=2))
    day = c.get("/api/history", params={"days": 1}).json()
    assert day["points"][0]["total_usd"] > BTC_AMOUNT * 50000  # трёхдневного снапшота нет
    everything = c.get("/api/history", params={"days": 0}).json()
    assert everything["first_snapshot_at"] and len(everything["points"]) >= 3


def test_history_follows_current_membership(c):
    snapshot_at(c, 40000, timedelta(days=2))
    c.patch(f"/api/wallets/{c.ltc}", json={"hidden": True})
    h = c.get("/api/history", params={"days": 7}).json()
    # Скрытый кошелёк выпадает из всей истории, а не только из сегодняшней точки.
    assert h["points"][0]["total_usd"] == pytest.approx(BTC_AMOUNT * 40000)
    assert [w["id"] for w in h["wallets"]] == [c.btc]

    pid = c.post("/api/portfolios", json={"name": "Только BTC", "wallet_ids": [c.btc]}).json()["id"]
    c.patch(f"/api/wallets/{c.ltc}", json={"hidden": False})
    h = c.get("/api/history", params={"days": 7, "portfolio_id": pid}).json()
    assert h["portfolio"]["name"] == "Только BTC" and h["assets"]["top"] == ["BTC"]


def test_empty_history(c):
    h = c.get("/api/history", params={"days": 7}).json()
    assert h["snapshot_count"] == 0 and len(h["points"]) == 1 and h["points"][0]["live"]


def simulate(c, days=30):
    """Первый запрос ставит загрузку цен в фон; дожидаемся её и берём готовый ответ."""
    first = c.get("/api/history/simulated", params={"days": days}).json()
    c.portal.call(history.wait_jobs, c.portfolio)
    return first, c.get("/api/history/simulated", params={"days": days}).json()


def test_simulated_history(c):
    first, h = simulate(c)
    # Сначала истории цен нет — ответ приходит сразу, загрузка идёт в фоне.
    assert set(first["pending"]) == {"BTC", "LTC"} and first["assets_with_history"] == []
    assert h["pending"] == [] and set(h["assets_with_history"]) == {"BTC", "LTC"}
    assert h["coverage_pct"] == pytest.approx(100)
    ltc = LTC_AMOUNT * conftest.PRICES["litecoin"]
    # Первая точка — текущие количества по ценам начала периода (половина текущих).
    assert h["points"][0]["total_usd"] == pytest.approx((BTC_AMOUNT * 80000 + ltc) / 2)
    assert h["points"][-1]["live"] is True
    calls = len(c.fake_api.calls)
    c.get("/api/history/simulated", params={"days": 30})
    assert not any("market_chart" in x for x in c.fake_api.calls[calls:])  # история цен из кэша


def test_simulated_stablecoins_and_symbol_lookup(c):
    c.put("/api/settings/keys/zerion", json={"value": "k"})
    c.post("/api/wallets", json={"name": "EVM", "network": "evm", "address": EVM})
    c.post("/api/refresh")
    _, h = simulate(c, days=7)
    assert "ETH" in h["assets_with_history"]  # у позиций Zerion нет id CoinGecko — нашли по тикеру
    assert "USDC" in h["assets_stable"] and "USDC" not in h["assets_with_history"]
    assert not any("usd-coin/market_chart" in x for x in c.fake_api.calls)  # на стейблкоины запросов нет
    assert 0 < h["coverage_pct"] <= 100


def test_simulated_waits_out_rate_limit(c, monkeypatch):
    monkeypatch.setattr(history, "RATE_LIMIT_PAUSE", 0)
    real = c.fake_api.__class__.__call__
    hits = {"n": 0}

    def flaky(self, request):
        if "market_chart" in request.url.path and hits["n"] < 2:
            hits["n"] += 1
            return httpx.Response(429)
        return real(self, request)

    monkeypatch.setattr(c.fake_api.__class__, "__call__", flaky)
    _, h = simulate(c)
    assert set(h["assets_with_history"]) == {"BTC", "LTC"} and hits["n"] == 2


def test_snapshot_history_last_hour(c):
    snapshot_at(c, 40000, timedelta(hours=3))
    snapshot_at(c, 50000, timedelta(minutes=40))
    snapshot_at(c, 60000, timedelta(minutes=10))
    h = c.get("/api/history", params={"hours": 1}).json()
    real = [p for p in h["points"] if not p.get("live")]
    assert len(real) == 2
    assert real[0]["total_usd"] == pytest.approx(BTC_AMOUNT * 50000 + LTC_AMOUNT * conftest.PRICES["litecoin"])
    assert c.get("/api/history", params={"hours": 25}).status_code == 422


def test_simulated_last_hour_from_daily_series(c, monkeypatch):
    now_ms = datetime.now(UTC).timestamp() * 1000
    asked = []

    async def recent(http, key, coin_id, days):
        asked.append(days)
        price = conftest.PRICES[coin_id]
        # Как у CoinGecko за сутки: точки каждые 5 минут; берём три — до часа и внутри него.
        return [(int(now_ms - m * 60_000), Decimal(str(price * f))) for m, f in ((90, 0.5), (50, 0.8), (20, 0.9))]

    monkeypatch.setattr(c.portfolio.coingecko, "price_history", recent)
    c.get("/api/history/simulated", params={"hours": 1})
    c.portal.call(history.wait_jobs, c.portfolio)
    h = c.get("/api/history/simulated", params={"hours": 1}).json()
    assert set(asked) == {1}  # час вырезается из дневного ряда
    ltc = LTC_AMOUNT * conftest.PRICES["litecoin"]
    real = [p["total_usd"] for p in h["points"] if not p.get("live")]
    assert real == [pytest.approx((BTC_AMOUNT * 80000 + ltc) * f) for f in (0.8, 0.9)]
    assert h["hours"] == 1
