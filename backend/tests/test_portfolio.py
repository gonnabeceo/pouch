import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import BTC, PRICES, RUB, SOL, TON, TRON
from pouch.main import create_app
from pouch.portfolio import Portfolio
from pouch.vault import Vault

PASSWORD = "correct horse battery"
EVM = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"


@pytest.fixture
def client(tmp_path, fake_api):
    vault = Vault(tmp_path, 900)
    portfolio = Portfolio(vault, transport=httpx.MockTransport(fake_api))
    app = create_app(vault, allowed_hosts={"testserver"}, static_dir=tmp_path / "none", portfolio_service=portfolio)
    with TestClient(app) as c:
        assert c.post("/api/setup", json={"password": PASSWORD}).status_code == 204
        c.fake_api = fake_api
        yield c


def add(client, name, network, address):
    r = client.post("/api/wallets", json={"name": name, "network": network, "address": address})
    assert r.status_code == 201
    return r.json()["id"]


def wallet(view, wallet_id):
    return next(w for w in view["wallets"] if w["id"] == wallet_id)


def test_refresh_totals(client):
    btc = add(client, "BTC", "btc", BTC)
    sol = add(client, "SOL", "sol", SOL)
    view = client.post("/api/refresh").json()

    btc_value = 41.71435165 * PRICES["bitcoin"]
    assert wallet(view, btc)["status"] == "fresh"
    assert wallet(view, btc)["value_usd"] == pytest.approx(btc_value)
    sol_wallet = wallet(view, sol)
    assert sol_wallet["hidden_tokens"] > 0
    assert view["total_usd"] == pytest.approx(btc_value + sol_wallet["value_usd"])
    assert view["total_rub"] == pytest.approx(view["total_usd"] * RUB)
    assert view["incomplete"] is False and view["updated_at"]


def test_token_without_price_not_in_total(client):
    ton = add(client, "TON", "ton", TON)
    view = client.post("/api/refresh").json()
    w = wallet(view, ton)
    no_price = [h for h in w["holdings"] if h["price_usd"] is None]
    assert no_price, "у жетонов из фикстуры нет цены в FakeApi"
    assert all(h["value_usd"] is None for h in no_price)
    # Свободный TON плюс 10 750 TON в номинатор-пуле.
    assert w["value_usd"] == pytest.approx((1151567.280062626 + 10750) * PRICES["the-open-network"])


def test_failed_source_keeps_last_value_and_warns(client):
    btc = add(client, "BTC", "btc", BTC)
    tron = add(client, "TRON", "tron", TRON)
    first = client.post("/api/refresh").json()

    client.fake_api.failing.add("mempool.space")
    second = client.post("/api/refresh").json()
    w = wallet(second, btc)
    assert w["status"] == "stale" and "503" in w["error"]
    assert w["value_usd"] == wallet(first, btc)["value_usd"]
    assert wallet(second, tron)["status"] == "fresh"
    assert second["total_usd"] == pytest.approx(first["total_usd"])
    assert second["incomplete"] is True

    client.fake_api.failing.clear()
    assert client.post("/api/refresh").json()["incomplete"] is False


def test_never_fetched_source_is_error(client):
    client.fake_api.failing.add("tonapi.io")
    ton = add(client, "TON", "ton", TON)
    view = client.post("/api/refresh").json()
    assert wallet(view, ton)["status"] == "error"
    assert view["incomplete"] is True and view["total_usd"] == 0


def test_price_failure_flags_total(client):
    add(client, "BTC", "btc", BTC)
    client.post("/api/refresh")
    client.fake_api.failing.add("api.coingecko.com")
    view = client.post("/api/refresh").json()
    # Последние известные цены остаются, но итог помечен.
    assert view["total_usd"] > 0 and view["prices_error"] and view["incomplete"] is True


def test_hidden_wallet_not_fetched(client):
    ltc = add(client, "LTC", "ltc", "LSnGScCYW5JXRGgPvJZEWuX2rwrY5fL3QA")
    btc = add(client, "BTC", "btc", BTC)
    client.patch(f"/api/wallets/{btc}", json={"hidden": True})
    view = client.post("/api/refresh").json()
    assert [w["id"] for w in view["wallets"]] == [ltc]
    assert not any("mempool.space" in c for c in client.fake_api.calls)


def test_address_change_drops_old_balance(client):
    btc = add(client, "BTC", "btc", BTC)
    client.post("/api/refresh")
    client.patch(f"/api/wallets/{btc}", json={"address": "3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy"})
    w = wallet(client.get("/api/portfolio").json(), btc)
    assert w["status"] == "pending" and w["holdings"] == []


def test_api_keys_never_returned(client):
    assert client.get("/api/settings/keys").json() == {
        "zerion": False, "coingecko": False, "helius": False, "tonapi": False, "trongrid": False,
    }
    assert client.put("/api/settings/keys/helius", json={"value": " secret-key "}).status_code == 204
    r = client.get("/api/settings/keys")
    assert r.json()["helius"] is True and "secret-key" not in r.text
    client.put("/api/settings/keys/helius", json={"value": ""})
    assert client.get("/api/settings/keys").json()["helius"] is False
    assert client.put("/api/settings/keys/other", json={"value": "x"}).status_code == 404


def test_directory_cached_between_refreshes(client):
    add(client, "BTC", "btc", BTC)
    client.post("/api/refresh")
    client.post("/api/refresh")
    assert sum("coins/list" in c for c in client.fake_api.calls) == 1


def test_portfolio_requires_session(client):
    client.cookies.clear()
    assert client.post("/api/refresh").status_code == 401
    assert client.get("/api/settings/keys").status_code == 401
