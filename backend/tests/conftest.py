"""Подмена внешних API ответами из tests/fixtures.

Записаны с живых API в октябре 2026: mempool, litecoinspace, dogechain.com, BlockCypher, TonAPI
(аккаунт, жетоны), TronGrid, Solana RPC (токены, стейк-аккаунты), CoinGecko, Hyperliquid (три
аккаунта: мастер с суб-аккаунтами в стандартном режиме, unified со стейкингом и vault, unified
с позициями). Крупные ответы урезаны до нужных полей.

Все записи обезличены: адреса людей заменены сгенерированными (с верными контрольными суммами),
балансы, объёмы и счётчики масштабированы одним коэффициентом, имена суб-аккаунтов заменены.
Публичные контракты токенов, пулы и vault HLP оставлены как есть.

Собраны по схемам из документации, потому что живой ответ записать нельзя: Zerion (нужен ключ)
и пулы TonAPI. В фикстуру токенов Solana добавлен синтетический спам-токен.
"""

import json
from pathlib import Path

import httpx
import pytest

from pouch.chains import base

FIXTURES = Path(__file__).parent / "fixtures"

BTC = "1JdXKhsZcys9oxWwUmoF995jHFzmJ2EK2D"
LTC = "LSnGScCYW5JXRGgPvJZEWuX2rwrY5fL3QA"
TON = "EQCWm0pmzUMIbkwXk5IuJXar-cCkd8rOLc-VPnQPNywuYyTF"
TRON = "TTkcRXCrRtx7BcBxLLQ7kAF7sBq7cSy1mP"
DOGE = "DSVK9AMZLC1cWv2qp8wjtYNdn3pRdR167q"
SOL = "qGM4kye8hT3T2123CaQKUEgJWBQEjor4P8wxYu7xqs2"
EVM = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"
HL = {
    "standard_subs": "0xe76298ce0cd73b2d9c2b5c834161988cb97bdd7e",
    "unified_staking": "0x4b976d37394066d8cc509f15c09e69c21b8abbc4",
    "unified_positions": "0x6e5c2fc87b6bf4e8cf996ad2ec141cc653cf7ef8",
}
LIQUID_POOL = "0:cf4bd44ac9e1fcf4b3b3f8e2aeb2b3d1bbfdc1e0c0a0f2b1d8a5a2c1e3f4a5b6"

PRICES = {"ethereum": 4000, "dogecoin": 0.2, "bitcoin": 80000, "litecoin": 90, "the-open-network": 3, "tron": 0.3, "solana": 180, "tether": 1, "usd-coin": 1}
RUB = 80.13
MARKET_IDS = {"eth": "ethereum", "steth": "staked-ether"}


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text())


class FakeApi:
    """Отвечает как настоящие провайдеры. failing — хосты, которые отдают 503."""

    def __init__(self):
        self.failing: set[str] = set()
        self.calls: list[str] = []
        self.hl_scenario = "standard_subs"
        self.hl_overrides: dict[str, object] = {}  # тип запроса → подменённый ответ

    def __call__(self, request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        self.calls.append(f"{request.method} {host}{path}")
        if host in self.failing:
            return httpx.Response(503)
        if host in ("mempool.space", "litecoinspace.org") and path.startswith("/api/address/"):
            return httpx.Response(200, json=fixture("btc_address.json" if host == "mempool.space" else "ltc_address.json"))
        if host == "dogechain.com":
            return httpx.Response(200, json=fixture("doge_dogechain.json"))
        if host == "api.blockcypher.com":
            return httpx.Response(200, json=fixture("doge_blockcypher.json"))
        if host == "tonapi.io":
            if path.startswith("/v2/staking/nominator/"):
                return httpx.Response(200, json=fixture("ton_staking_pools.json"))
            if path.startswith("/v2/staking/pool/"):
                implementation = "liquidTF" if path.endswith(LIQUID_POOL) else "whales"
                return httpx.Response(200, json={"implementation": {}, "pool": {"implementation": implementation}})
            return httpx.Response(200, json=fixture("ton_jettons.json" if path.endswith("/jettons") else "ton_account.json"))
        if host == "api.zerion.io":
            if request.headers.get("authorization") is None:
                return httpx.Response(402)
            kind = request.url.params["filter[positions]"]
            if kind == "only_complex" and "zerion-complex" in self.failing:
                return httpx.Response(429)
            return httpx.Response(200, json=fixture("zerion_simple.json" if kind == "only_simple" else "zerion_complex.json"))
        if host == "api.hyperliquid.xyz":
            body = json.loads(request.content)
            self.calls[-1] += f" {body['type']}"
            # content, а не json=: настоящий API отвечает и буквальным null (нет суб-аккаунтов).
            if body["type"] in self.hl_overrides:
                return httpx.Response(200, content=json.dumps(self.hl_overrides[body["type"]]))
            for entry in fixture(f"hl_{self.hl_scenario}.json"):
                if entry["request"] == body:
                    return httpx.Response(200, content=json.dumps(entry["response"]))
            return httpx.Response(422, json={"error": "нет записанного ответа"})
        if host == "api.trongrid.io":
            if path == "/wallet/triggerconstantcontract":
                return httpx.Response(200, json={"constant_result": [f"{6:064x}"], "result": {"result": True}})
            return httpx.Response(200, json=fixture("tron_account.json"))
        if host == "api.mainnet-beta.solana.com":
            body = json.loads(request.content)
            if body["method"] == "getBalance":
                return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"value": 1242584379}})
            if body["method"] == "getProgramAccounts":
                return httpx.Response(200, json=fixture("sol_stake_accounts.json"))
            program = body["params"][1]["programId"]
            name = "sol_tokens_spl.json" if program.startswith("Tokenkeg") else "sol_tokens_2022.json"
            return httpx.Response(200, json=fixture(name))
        if host == "api.coingecko.com":
            if path == "/api/v3/coins/list":
                return httpx.Response(200, json=fixture("coingecko_coins_list.json"))
            if path == "/api/v3/coins/markets":
                symbols = request.url.params["symbols"].split(",")
                return httpx.Response(200, json=[
                    {"id": MARKET_IDS[sym], "symbol": sym} for sym in symbols if sym in MARKET_IDS
                ])
            if path.startswith("/api/v3/coins/") and path.endswith("/market_chart"):
                coin = path.split("/")[4]
                if coin not in PRICES:
                    return httpx.Response(404)
                # Синтетика: три точки за период, цена росла от половины до текущей.
                price = PRICES[coin]
                return httpx.Response(200, json={"prices": [
                    [1_700_000_000_000, price / 2], [1_700_003_600_000, price * 0.75], [1_700_007_200_000, price],
                ]})
            if path == "/api/v3/simple/price":
                ids = request.url.params["ids"].split(",")
                out = {i: {"usd": PRICES[i]} for i in ids if i in PRICES}
                out["tether"]["rub"] = RUB
                return httpx.Response(200, json=out)
        return httpx.Response(404)


@pytest.fixture
def fake_api(monkeypatch):
    monkeypatch.setattr(base, "RETRY_BACKOFF", 0)

    async def no_wait(self):
        return None

    monkeypatch.setattr(base.RateLimiter, "wait", no_wait)
    return FakeApi()
