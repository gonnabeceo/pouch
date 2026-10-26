"""Портфолио — именованные наборы кошельков; скрытие не теряет состав."""

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import BTC, LTC, PRICES, TRON
from pouch.main import create_app
from pouch.portfolio import Portfolio
from pouch.vault import Vault

BTC_USD = 41.71435165 * PRICES["bitcoin"]
LTC_USD = (49530687893780 - 47442489351900) / 1e8 * PRICES["litecoin"]


@pytest.fixture
def client(tmp_path, fake_api):
    vault = Vault(tmp_path, 900)
    app = create_app(vault, allowed_hosts={"testserver"}, static_dir=tmp_path / "none",
                     portfolio_service=Portfolio(vault, transport=httpx.MockTransport(fake_api)))
    with TestClient(app) as c:
        c.post("/api/setup", json={"password": "correct horse battery"})
        ids = {}
        for name, network, address in (("btc", "btc", BTC), ("ltc", "ltc", LTC), ("tron", "tron", TRON)):
            ids[name] = c.post("/api/wallets", json={"name": name, "network": network, "address": address}).json()["id"]
        c.post("/api/refresh")
        c.ids = ids
        yield c


def test_portfolio_total_is_subset(client):
    p = client.post("/api/portfolios", json={"name": "Сбережения", "wallet_ids": [client.ids["btc"], client.ids["ltc"]]})
    assert p.status_code == 201
    view = client.get("/api/portfolio", params={"portfolio_id": p.json()["id"]}).json()
    assert view["portfolio"]["name"] == "Сбережения"
    assert [w["name"] for w in view["wallets"]] == ["btc", "ltc"]
    assert view["total_usd"] == pytest.approx(BTC_USD + LTC_USD)
    assert {a["symbol"] for a in view["assets"]} == {"BTC", "LTC"}

    everything = client.get("/api/portfolio").json()
    assert everything["portfolio"] == {"id": None, "name": "Все кошельки"}
    assert len(everything["wallets"]) == 3 and everything["total_usd"] > view["total_usd"]


def test_wallet_in_several_portfolios(client):
    a = client.post("/api/portfolios", json={"name": "A", "wallet_ids": [client.ids["btc"]]}).json()
    b = client.post("/api/portfolios", json={"name": "B", "wallet_ids": [client.ids["btc"], client.ids["tron"]]}).json()
    listed = {p["name"]: p["wallet_ids"] for p in client.get("/api/portfolios").json()}
    assert client.ids["btc"] in listed["A"] and client.ids["btc"] in listed["B"]
    assert a["id"] != b["id"]


def test_hiding_keeps_membership(client):
    p = client.post("/api/portfolios", json={"name": "S", "wallet_ids": [client.ids["btc"], client.ids["ltc"]]}).json()
    client.patch(f"/api/wallets/{client.ids['btc']}", json={"hidden": True})
    view = client.get("/api/portfolio", params={"portfolio_id": p["id"]}).json()
    assert [w["name"] for w in view["wallets"]] == ["ltc"]
    assert view["total_usd"] == pytest.approx(LTC_USD)
    assert client.ids["btc"] in client.get("/api/portfolios").json()[0]["wallet_ids"]

    client.patch(f"/api/wallets/{client.ids['btc']}", json={"hidden": False})
    view = client.get("/api/portfolio", params={"portfolio_id": p["id"]}).json()
    assert view["total_usd"] == pytest.approx(BTC_USD + LTC_USD)


def test_rename_change_and_delete(client):
    p = client.post("/api/portfolios", json={"name": "Old", "wallet_ids": [client.ids["btc"]]}).json()
    r = client.patch(f"/api/portfolios/{p['id']}", json={"name": "New"})
    assert r.json() == {"id": p["id"], "name": "New", "wallet_ids": [client.ids["btc"]]}
    r = client.patch(f"/api/portfolios/{p['id']}", json={"wallet_ids": [client.ids["tron"]]})
    assert r.json()["wallet_ids"] == [client.ids["tron"]] and r.json()["name"] == "New"
    assert client.delete(f"/api/portfolios/{p['id']}").status_code == 204
    assert client.get("/api/portfolios").json() == []
    assert client.get("/api/portfolio", params={"portfolio_id": p["id"]}).status_code == 404
    # Кошельки при удалении портфолио не трогаются.
    assert len(client.get("/api/wallets").json()) == 3


def test_deleted_wallet_leaves_portfolio(client):
    p = client.post("/api/portfolios", json={"name": "S", "wallet_ids": [client.ids["btc"], client.ids["ltc"]]}).json()
    client.delete(f"/api/wallets/{client.ids['btc']}")
    assert client.get("/api/portfolios").json()[0]["wallet_ids"] == [client.ids["ltc"]]
    assert client.get("/api/portfolio", params={"portfolio_id": p["id"]}).status_code == 200


def test_validation(client):
    assert client.post("/api/portfolios", json={"name": "  ", "wallet_ids": []}).status_code == 422
    assert client.post("/api/portfolios", json={"name": "все кошельки"}).status_code == 409
    client.post("/api/portfolios", json={"name": "Dup"})
    assert client.post("/api/portfolios", json={"name": "Dup"}).status_code == 409
    assert client.post("/api/portfolios", json={"name": "X", "wallet_ids": [999]}).status_code == 409
    assert client.patch("/api/portfolios/999", json={"name": "Y"}).status_code == 404
    assert client.delete("/api/portfolios/999").status_code == 404


def test_empty_portfolio(client):
    p = client.post("/api/portfolios", json={"name": "Пусто"}).json()
    view = client.post("/api/refresh", params={"portfolio_id": p["id"]}).json()
    assert view["wallets"] == [] and view["total_usd"] == 0 and view["incomplete"] is False
