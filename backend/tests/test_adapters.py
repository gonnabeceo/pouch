import asyncio
import json
from decimal import Decimal

import httpx
import pytest

from conftest import BTC, LTC, SOL, TON, TRON, fixture
from pouch.chains import default_adapters
from pouch.chains.base import AdapterError, Context, Http, new_client
from pouch.prices import CoinGecko, build_directory


def run(fake_api, network: str, address: str):
    async def go():
        async with new_client(httpx.MockTransport(fake_api)) as client:
            ctx = Context(Http(client), {}, build_directory(fixture("coingecko_coins_list.json")), {})
            return await default_adapters()[network].fetch(address, ctx), ctx

    return asyncio.run(go())


def by_symbol(balance):
    return {(h.symbol, h.kind): h for h in balance.holdings}


def test_btc_includes_mempool(fake_api):
    balance, _ = run(fake_api, "btc", BTC)
    # подтверждённые плюс неподтверждённые из мемпула
    assert balance.holdings[0].amount == Decimal("41.71435165")
    assert balance.holdings[0].coingecko_id == "bitcoin"


def test_ltc(fake_api):
    balance, _ = run(fake_api, "ltc", LTC)
    assert balance.holdings[0].amount == Decimal(49530687893780 - 47442489351900).scaleb(-8)
    assert balance.holdings[0].symbol == "LTC"


def test_ton_filters_unknown_and_blacklisted(fake_api):
    balance, _ = run(fake_api, "ton", TON)
    holdings = by_symbol(balance)
    assert holdings[("TON", "free")].amount == Decimal("1151567.280062626")
    jettons = fixture("ton_jettons.json")["balances"]
    shown = {h.contract for h in balance.holdings if h.contract}
    for item in jettons:
        j = item["jetton"]
        if j["verification"] == "blacklist":
            assert j["address"] not in shown
    assert holdings[("MGRMGA", "free")].coingecko_id == "make-gram-great-again"
    nonzero = sum(1 for item in jettons if int(item["balance"]) > 0)
    assert len(shown) + balance.hidden_tokens == nonzero


def test_tron_staked_and_trc20(fake_api):
    balance, ctx = run(fake_api, "tron", TRON)
    holdings = by_symbol(balance)
    assert holdings[("TRX", "free")].amount == Decimal("15061.432850")
    # frozenV2 плюс делегированное другим адресам
    assert holdings[("TRX", "staked")].amount == Decimal("6313349.945")
    assert holdings[("USDT", "free")].amount == Decimal("59193.555188")
    assert balance.hidden_tokens == 9
    assert ctx.meta == {"tron:TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t": {"decimals": 6}}


def test_tron_decimals_cached(fake_api):
    async def go():
        async with new_client(httpx.MockTransport(fake_api)) as client:
            ctx = Context(Http(client), {}, build_directory(fixture("coingecko_coins_list.json")), {})
            adapter = default_adapters()["tron"]
            await adapter.fetch(TRON, ctx)
            await adapter.fetch(TRON, ctx)

    asyncio.run(go())
    assert sum("triggerconstantcontract" in c for c in fake_api.calls) == 1


def test_solana_aggregates_known_tokens(fake_api):
    balance, _ = run(fake_api, "sol", SOL)
    holdings = by_symbol(balance)
    assert holdings[("SOL", "free")].amount == Decimal("1.242584379")
    usdc = holdings[("USDC", "free")]
    assert usdc.coingecko_id == "usd-coin" and usdc.amount > 0
    # В фикстуру добавлен синтетический спам-токен SyntheticSpamMint…: он скрыт.
    assert balance.hidden_tokens == 1
    assert all(h.coingecko_id for h in balance.holdings)


def test_helius_key_switches_endpoint(fake_api):
    seen = []

    def handler(request):
        seen.append(request.url)
        method = json.loads(request.content)["method"]
        result = {"getBalance": {"value": 0}, "getProgramAccounts": []}.get(method, {"value": []})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})

    async def go():
        async with new_client(httpx.MockTransport(handler)) as client:
            ctx = Context(Http(client), {"helius": "k-123"}, build_directory([]), {})
            await default_adapters()["sol"].fetch(SOL, ctx)

    asyncio.run(go())
    assert all(u.host == "mainnet.helius-rpc.com" and u.params["api-key"] == "k-123" for u in seen)


def test_retries_then_error_without_address(fake_api):
    fake_api.failing.add("mempool.space")
    with pytest.raises(AdapterError) as e:
        run(fake_api, "btc", BTC)
    assert "503" in str(e.value) and BTC not in str(e.value)
    assert sum("mempool.space" in c for c in fake_api.calls) == 3


def test_retry_recovers_after_429():
    responses = iter([httpx.Response(429, headers={"retry-after": "0"}), httpx.Response(200, json={"ok": 1})])

    async def go():
        async with new_client(httpx.MockTransport(lambda r: next(responses))) as client:
            return await Http(client, backoff=0).get("https://example.org/x")

    assert asyncio.run(go()) == {"ok": 1}


def test_prices_and_rub(fake_api):
    async def go():
        async with new_client(httpx.MockTransport(fake_api)) as client:
            return await CoinGecko().prices(Http(client), None, {"bitcoin", "solana"})

    usd, rub = asyncio.run(go())
    assert usd["bitcoin"] == Decimal("80000") and usd["tether"] == Decimal("1")
    assert rub == Decimal("80.13")


def test_directory_normalizes_ton_contracts():
    directory = build_directory(
        [{"id": "tether", "symbol": "usdt", "name": "Tether",
          "platforms": {"the-open-network": "EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs"}}]
    )
    raw = next(iter(directory.contracts["ton"]))
    assert raw.startswith("0:")
    # TonAPI отдаёт raw, пользователь может видеть friendly — обе формы находят одну монету.
    assert directory.lookup("ton", raw) == "tether"
    assert directory.lookup("ton", "EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs") == "tether"
    assert directory.describe("tether") == ("USDT", "Tether")
