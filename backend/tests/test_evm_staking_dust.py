"""EVM через Zerion, стейкинг TON и Solana, займы, спам и порог пыли."""

import asyncio
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import EVM, PRICES, SOL, TON, fixture
from pouch.chains import default_adapters
from pouch.chains.base import AdapterError, Context, Http, new_client
from pouch.chains.evm import NO_KEY
from pouch.main import create_app
from pouch.portfolio import Portfolio
from pouch.prices import build_directory
from pouch.vault import Vault


def fetch(fake_api, network, address, keys=None, meta=None):
    async def go():
        async with new_client(httpx.MockTransport(fake_api)) as client:
            ctx = Context(Http(client), keys or {}, build_directory(fixture("coingecko_coins_list.json")), {} if meta is None else meta)
            return await default_adapters()[network].fetch(address, ctx), ctx

    return asyncio.run(go())


def kinds(balance):
    return {(h.symbol, h.kind, h.chain): h for h in balance.holdings}


# --- EVM ---


def test_evm_requires_key(fake_api):
    with pytest.raises(AdapterError, match="ключ Zerion"):
        fetch(fake_api, "evm", EVM)
    assert not any("zerion" in c for c in fake_api.calls)


def test_evm_tokens_positions_and_spam(fake_api):
    balance, _ = fetch(fake_api, "evm", EVM, keys={"zerion": "k"})
    h = kinds(balance)
    assert h[("ETH", "free", "ethereum")].amount == Decimal("1.5")
    assert h[("ETH", "free", "base")].amount == Decimal("0.25")
    assert h[("USDC", "loan", "ethereum")].protocol == "Aave V3"
    assert h[("stETH", "staked", "ethereum")].price_usd == Decimal("3990.0")
    # Два спам-токена скрыты, служебный aToken и нулевой DAI просто пропущены.
    assert balance.hidden_tokens == 2
    symbols = {x.symbol for x in balance.holdings}
    assert "aEthWETH" not in symbols and "DAI" not in symbols and "CLAIM-REWARD.COM" not in symbols


def test_evm_sends_basic_auth(fake_api):
    seen = []

    def handler(request):
        seen.append(request.headers["authorization"])
        return fake_api(request)

    fetch(handler, "evm", EVM, keys={"zerion": "zk_test"})
    assert seen and all(a == "Basic emtfdGVzdDo=" for a in seen)  # base64("zk_test:")


def test_evm_defi_requested_at_most_every_30_minutes(fake_api):
    meta = {}
    fetch(fake_api, "evm", EVM, keys={"zerion": "k"}, meta=meta)
    fetch(fake_api, "evm", EVM, keys={"zerion": "k"}, meta=meta)
    assert len(fake_api.calls) == 3  # simple, complex, simple


def test_evm_defi_quota_exhausted_uses_cache(fake_api):
    meta = {}
    fetch(fake_api, "evm", EVM, keys={"zerion": "k"}, meta=meta)
    cache = meta[f"zerion.complex:{EVM.lower()}"]
    cache["at"] = "2000-01-01T00:00:00+00:00"  # кэш просрочен
    fake_api.failing.add("zerion-complex")
    balance, _ = fetch(fake_api, "evm", EVM, keys={"zerion": "k"}, meta=meta)
    assert balance.warning and "429" in balance.warning
    assert ("USDC", "loan", "ethereum") in kinds(balance)


def test_evm_defi_failure_without_cache_is_error(fake_api):
    fake_api.failing.add("zerion-complex")
    with pytest.raises(AdapterError):
        fetch(fake_api, "evm", EVM, keys={"zerion": "k"})


# --- стейкинг ---


def test_ton_staking_skips_liquid_pools(fake_api):
    balance, ctx = fetch(fake_api, "ton", TON)
    staked = kinds(balance)[("TON", "staked", None)]
    # whales: 10 050 + 500 ожидает депозита + 200 готово к выводу; ликвидный пул — это жетон
    assert staked.amount == Decimal("10750")
    assert ctx.meta["ton.pool:0:a44757069a7b04e393782b4a2d3e5e449f19d16a4986a9e25436e6b97e45a16a"] == {"implementation": "whales"}


def test_solana_native_stake(fake_api):
    balance, _ = fetch(fake_api, "sol", SOL)
    assert kinds(balance)[("SOL", "staked", None)].amount == Decimal("1.085854859")


# --- итог ---


@pytest.fixture
def client(tmp_path, fake_api):
    vault = Vault(tmp_path, 900)
    portfolio = Portfolio(vault, transport=httpx.MockTransport(fake_api))
    app = create_app(vault, allowed_hosts={"testserver"}, static_dir=tmp_path / "none", portfolio_service=portfolio)
    with TestClient(app) as c:
        c.post("/api/setup", json={"password": "correct horse battery"})
        c.put("/api/settings/keys/zerion", json={"value": "k"})
        yield c


def evm_wallet(client):
    client.post("/api/wallets", json={"name": "EVM", "network": "evm", "address": EVM})
    view = client.post("/api/refresh").json()
    return view, view["wallets"][0]


def test_loan_counts_as_net_value(client):
    view, w = evm_wallet(client)
    # Свободные: ETH 1.75 × 4000 + USDC 1200.5; позиции: депозит ETH 2 × 4000 − долг 3000 USDC
    # + stETH 3990 + награда AAVE 30. ARB на $0.20 — пыль, XYZ — без цены.
    expected = 1.75 * 4000 + 1200.5 + 2 * 4000 - 3000 + 3990 + 30
    assert w["value_usd"] == pytest.approx(expected)
    assert view["total_usd"] == pytest.approx(expected)
    loan = next(h for h in w["holdings"] if h["kind"] == "loan")
    assert loan["value_usd"] == pytest.approx(-3000) and loan["protocol"] == "Aave V3"
    assert w["holdings"][0]["kind"] == "free"  # свободные балансы идут первыми


def test_dust_threshold(client):
    _, w = evm_wallet(client)
    assert w["dust_count"] == 1 and w["dust_usd"] == pytest.approx(0.2)
    assert not any(h["symbol"] == "ARB" for h in w["holdings"])
    unpriced = next(h for h in w["holdings"] if h["symbol"] == "XYZ")
    assert unpriced["value_usd"] is None  # без цены — не пыль, а «нет цены»

    assert client.put("/api/settings/general", json={"dust_threshold_usd": 0}).json()["dust_threshold_usd"] == 0
    w = client.get("/api/portfolio").json()["wallets"][0]
    assert w["dust_count"] == 0 and any(h["symbol"] == "ARB" for h in w["holdings"])

    client.put("/api/settings/general", json={"dust_threshold_usd": 5000})
    w = client.get("/api/portfolio").json()["wallets"][0]
    # Под порог попадает и долг $3000: пыль сравнивается по модулю.
    assert all(abs(h["value_usd"]) >= 5000 for h in w["holdings"] if h["value_usd"] is not None)
    assert client.put("/api/settings/general", json={"dust_threshold_usd": -1}).status_code == 422


def test_assets_merge_chains_and_net_loans(client):
    view, _ = evm_wallet(client)
    assets = {a["symbol"]: a for a in view["assets"]}
    assert assets["ETH"]["amount"] == "3.75"  # 1.5 + 0.25 + депозит 2
    assert assets["USDC"]["amount"] == "-1799.5"  # 1200.5 − 3000 долга


def test_partial_defi_flags_total(client, fake_api):
    evm_wallet(client)
    portfolio = client.app.state.portfolio
    meta, _ = portfolio._cache_get("token.meta")
    meta[f"zerion.complex:{EVM.lower()}"]["at"] = "2000-01-01T00:00:00+00:00"
    portfolio._cache_put("token.meta", meta)
    fake_api.failing.add("zerion-complex")
    view = client.post("/api/refresh").json()
    w = view["wallets"][0]
    assert w["status"] == "fresh" and "DeFi не обновился" in w["warning"]
    assert view["incomplete"] is True
    assert any(h["kind"] == "loan" for h in w["holdings"])


def test_missing_key_is_error_status(tmp_path, fake_api):
    vault = Vault(tmp_path, 900)
    app = create_app(vault, allowed_hosts={"testserver"}, static_dir=tmp_path / "none",
                     portfolio_service=Portfolio(vault, transport=httpx.MockTransport(fake_api)))
    with TestClient(app) as c:
        c.post("/api/setup", json={"password": "correct horse battery"})
        c.post("/api/wallets", json={"name": "EVM", "network": "evm", "address": EVM})
        view = c.post("/api/refresh").json()
        w = view["wallets"][0]
        assert w["status"] == "error" and w["error"] == NO_KEY and view["incomplete"] is True


def test_ton_total_includes_staking(tmp_path, fake_api):
    vault = Vault(tmp_path, 900)
    app = create_app(vault, allowed_hosts={"testserver"}, static_dir=tmp_path / "none",
                     portfolio_service=Portfolio(vault, transport=httpx.MockTransport(fake_api)))
    with TestClient(app) as c:
        c.post("/api/setup", json={"password": "correct horse battery"})
        c.post("/api/wallets", json={"name": "TON", "network": "ton", "address": TON})
        w = c.post("/api/refresh").json()["wallets"][0]
        staked = next(h for h in w["holdings"] if h["kind"] == "staked")
        assert staked["value_usd"] == pytest.approx(10750 * PRICES["the-open-network"])
