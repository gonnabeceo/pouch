import pytest
from fastapi.testclient import TestClient

from pouch.main import create_app
from pouch.vault import Vault

PASSWORD = "correct horse battery"
BTC = "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"
EVM = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"


@pytest.fixture
def client(tmp_path):
    app = create_app(Vault(tmp_path, 900), allowed_hosts={"testserver"}, static_dir=tmp_path / "none")
    with TestClient(app) as c:
        yield c


@pytest.fixture
def authed(client):
    assert client.post("/api/setup", json={"password": PASSWORD}).status_code == 204
    return client


def test_locked_by_default(client):
    assert client.get("/api/status").json() == {
        "initialized": False, "unlocked": False, "autolock_seconds": 900, "min_password_length": 8,
    }
    assert client.get("/api/wallets").status_code == 401


def test_session_cookie_required(authed):
    assert authed.get("/api/wallets").status_code == 200
    authed.cookies.clear()
    assert authed.get("/api/wallets").status_code == 401


def test_lock_and_unlock(authed):
    assert authed.post("/api/lock").status_code == 204
    assert authed.get("/api/wallets").status_code == 401
    assert authed.post("/api/unlock", json={"password": "wrong-password"}).status_code == 401
    assert authed.post("/api/unlock", json={"password": PASSWORD}).status_code == 204
    assert authed.get("/api/status").json()["unlocked"] is True


def test_wallet_crud(authed):
    r = authed.post("/api/wallets", json={"name": " Сбережения ", "network": "btc", "address": BTC})
    assert r.status_code == 201
    w = r.json()
    assert w["name"] == "Сбережения" and w["hidden"] is False and w["note"] == ""

    r = authed.patch(f"/api/wallets/{w['id']}", json={"note": "ledger в сейфе", "name": "Холодный"})
    assert r.json()["note"] == "ledger в сейфе" and r.json()["name"] == "Холодный"

    authed.patch(f"/api/wallets/{w['id']}", json={"hidden": True})
    assert authed.get("/api/wallets").json() == []
    hidden = authed.get("/api/wallets", params={"include_hidden": True}).json()
    assert [x["hidden"] for x in hidden] == [True]

    authed.patch(f"/api/wallets/{w['id']}", json={"hidden": False})
    assert len(authed.get("/api/wallets").json()) == 1

    assert authed.delete(f"/api/wallets/{w['id']}").status_code == 204
    assert authed.get("/api/wallets", params={"include_hidden": True}).json() == []
    assert authed.delete(f"/api/wallets/{w['id']}").status_code == 404


def test_wallet_validation(authed):
    r = authed.post("/api/wallets", json={"name": "x", "network": "btc", "address": EVM})
    assert r.status_code == 422 and "Bitcoin" in r.json()["detail"]
    r = authed.post("/api/wallets", json={"name": "x", "network": "doge", "address": BTC})
    assert r.status_code == 422


def test_duplicate_address(authed):
    authed.post("/api/wallets", json={"name": "a", "network": "evm", "address": EVM})
    r = authed.post("/api/wallets", json={"name": "b", "network": "evm", "address": EVM.lower()})
    assert r.status_code == 409
    # Тот же адрес в другой сети — отдельная запись.
    r = authed.post("/api/wallets", json={"name": "c", "network": "hyperliquid", "address": EVM})
    assert r.status_code == 201


def test_patch_revalidates_address(authed):
    w = authed.post("/api/wallets", json={"name": "a", "network": "evm", "address": EVM}).json()
    r = authed.patch(f"/api/wallets/{w['id']}", json={"network": "btc"})
    assert r.status_code == 422


def test_foreign_host_and_origin_rejected(authed):
    assert authed.get("/api/status", headers={"host": "evil.example"}).status_code == 403
    r = authed.post("/api/lock", headers={"origin": "http://evil.example"})
    assert r.status_code == 403
    assert authed.get("/api/status").json()["unlocked"] is True


def test_csp_header(client):
    assert "default-src 'self'" in client.get("/api/status").headers["content-security-policy"]


def test_change_password_api(authed):
    r = authed.post("/api/password", json={"old": "wrong-password", "new": "another long one"})
    assert r.status_code == 403
    assert authed.post("/api/password", json={"old": PASSWORD, "new": "short"}).status_code == 422
    assert authed.post("/api/password", json={"old": PASSWORD, "new": "another long one"}).status_code == 204
    # Сессия остаётся, вход — уже новым паролем.
    assert authed.get("/api/wallets").status_code == 200
    authed.post("/api/lock")
    assert authed.post("/api/unlock", json={"password": PASSWORD}).status_code == 401
    assert authed.post("/api/unlock", json={"password": "another long one"}).status_code == 204
