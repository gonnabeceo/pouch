"""Цены и справочник токенов CoinGecko.

Справочник «контракт → id монеты» скачивается раз в сутки одним запросом,
цены всех активов — одним запросом /simple/price на обновление.
"""

from decimal import Decimal

from .addresses import InvalidAddress, validate
from .chains.base import Http, RateLimiter

BASE = "https://api.coingecko.com/api/v3"
RUB_SOURCE = "tether"  # курс RUB — рыночный USDT/RUB
IDS_PER_REQUEST = 200

# Наша сеть → платформа в CoinGecko.
PLATFORMS = {"tron": "tron", "sol": "solana", "ton": "the-open-network"}

def _headers(key: str | None) -> dict | None:
    return {"x-cg-demo-api-key": key} if key else None


def _contract_key(network: str, contract: str) -> str | None:
    if network == "ton":
        # CoinGecko хранит friendly-форму (EQ…), TonAPI отдаёт raw (0:hex): сводим к raw.
        try:
            return validate("ton", contract)[1]
        except InvalidAddress:
            return None
    return contract


class Directory:
    def __init__(self, contracts: dict[str, dict[str, str]], coins: dict[str, list[str]]):
        self.contracts = contracts  # сеть → ключ контракта → id
        self.coins = coins  # id → [символ, название]

    def lookup(self, network: str, contract: str) -> str | None:
        key = _contract_key(network, contract)
        return self.contracts.get(network, {}).get(key) if key else None

    def describe(self, coingecko_id: str) -> tuple[str, str] | None:
        coin = self.coins.get(coingecko_id)
        return (coin[0].upper(), coin[1]) if coin else None

    def to_json(self) -> dict:
        return {"contracts": self.contracts, "coins": self.coins}

    @classmethod
    def from_json(cls, data: dict) -> "Directory":
        return cls(data["contracts"], data["coins"])

    @classmethod
    def empty(cls) -> "Directory":
        return cls({}, {})


class CoinGecko:
    def __init__(self, base_url: str = BASE):
        self.base_url = base_url
        self.limiter = RateLimiter(2.0)

    async def directory(self, http: Http, key: str | None) -> Directory:
        coins_list = await http.get(
            f"{self.base_url}/coins/list",
            params={"include_platform": "true"},
            headers=_headers(key),
            limiter=self.limiter,
        )
        return build_directory(coins_list)

    async def prices(self, http: Http, key: str | None, ids: set[str]) -> tuple[dict[str, Decimal], Decimal | None]:
        """Цены в USD по id и курс USDT/RUB."""
        wanted = sorted(ids | {RUB_SOURCE})
        usd: dict[str, Decimal] = {}
        rub_rate = None
        for i in range(0, len(wanted), IDS_PER_REQUEST):
            chunk = wanted[i : i + IDS_PER_REQUEST]
            currencies = "usd,rub" if RUB_SOURCE in chunk else "usd"
            data = await http.get(
                f"{self.base_url}/simple/price",
                params={"ids": ",".join(chunk), "vs_currencies": currencies, "precision": "full"},
                headers=_headers(key),
                limiter=self.limiter,
            )
            for coin_id, quote in data.items():
                if quote.get("usd") is not None:
                    usd[coin_id] = Decimal(str(quote["usd"]))
                if coin_id == RUB_SOURCE and quote.get("rub") is not None:
                    rub_rate = Decimal(str(quote["rub"]))
        return usd, rub_rate


    async def ids_by_symbol(self, http: Http, key: str | None, symbols: list[str]) -> dict[str, str]:
        """Тикер → id самой крупной по капитализации монеты с этим тикером."""
        if not symbols:
            return {}
        data = await http.get(
            f"{self.base_url}/coins/markets",
            params={"vs_currency": "usd", "symbols": ",".join(s.lower() for s in symbols), "include_tokens": "top"},
            headers=_headers(key),
            limiter=self.limiter,
        )
        found: dict[str, str] = {}
        for coin in data:
            found.setdefault(coin["symbol"].upper(), coin["id"])
        return found

    async def price_history(self, http: Http, key: str | None, coin_id: str, days: int) -> list[tuple[int, Decimal]]:
        """[(мс, цена USD)]: до 1 дня — каждые 5 минут, до 90 — почасово, дальше — по дням."""
        data = await http.get(
            f"{self.base_url}/coins/{coin_id}/market_chart",
            params={"vs_currency": "usd", "days": str(days)},
            headers=_headers(key),
            limiter=self.limiter,
        )
        return [(int(t), Decimal(str(p))) for t, p in data.get("prices", []) if p is not None]


def build_directory(coins_list: list[dict]) -> Directory:
    contracts: dict[str, dict[str, str]] = {network: {} for network in PLATFORMS}
    coins: dict[str, list[str]] = {}
    for coin in coins_list:
        platforms = coin.get("platforms") or {}
        relevant = False
        for network, platform in PLATFORMS.items():
            address = platforms.get(platform)
            contract = _contract_key(network, address) if address else None
            # Один контракт иногда указан у нескольких монет — оставляем первую.
            if contract and contract not in contracts[network]:
                contracts[network][contract] = coin["id"]
                relevant = True
        if relevant or not platforms:
            coins[coin["id"]] = [coin["symbol"], coin["name"]]
    return Directory(contracts, coins)

