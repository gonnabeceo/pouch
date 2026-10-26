"""Общее для адаптеров сетей: формат результата, ошибки и HTTP с таймаутами, повторами и лимитами.

Адаптер получает адрес и возвращает список токенов с количеством. Ошибки наружу —
только AdapterError с текстом без адресов и URL: он попадает в интерфейс.
"""

import asyncio
import random
import time
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

TIMEOUT_SECONDS = 15
RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_RETRY_DELAY = 10.0
RETRY_BACKOFF = 1.0


def format_amount(amount: Decimal) -> str:
    """Без экспоненты и хвостовых нулей: 1.3E-8 → 0.000000013, 10000.000 → 10000."""
    text = format(amount.normalize(), "f")
    return text if text != "-0" else "0"


@dataclass
class Holding:
    symbol: str
    amount: Decimal
    name: str = ""
    coingecko_id: str | None = None
    contract: str | None = None  # None — нативная монета сети
    # free — свободный баланс; остальное — позиции: staked, deposit, loan, reward, locked, investment
    kind: str = "free"
    price_usd: Decimal | None = None  # цена от источника, если монеты нет в CoinGecko
    protocol: str | None = None
    chain: str | None = None  # сеть внутри EVM: ethereum, base, …

    def to_json(self) -> dict:
        data = {**asdict(self), "amount": format_amount(self.amount)}
        data["price_usd"] = str(self.price_usd) if self.price_usd is not None else None
        return data

    @classmethod
    def from_json(cls, data: dict) -> "Holding":
        price = data.get("price_usd")
        return cls(**{**data, "amount": Decimal(data["amount"]), "price_usd": Decimal(price) if price else None})


@dataclass
class Balance:
    holdings: list[Holding]
    hidden_tokens: int = 0  # спам и неопознанные токены; в итог не входят
    warning: str | None = None  # часть данных не обновилась (например, DeFi из кэша)
    details: dict | None = None  # данные сети для отдельного блока (позиции Hyperliquid)

    def to_json(self) -> dict:
        return {
            "holdings": [h.to_json() for h in self.holdings],
            "hidden_tokens": self.hidden_tokens,
            "warning": self.warning,
            "details": self.details,
        }

    @classmethod
    def from_json(cls, data: dict) -> "Balance":
        return cls(
            [Holding.from_json(h) for h in data["holdings"]],
            data.get("hidden_tokens", 0),
            data.get("warning"),
            data.get("details"),
        )


class AdapterError(Exception):
    pass


class RateLimiter:
    """Минимальная пауза между запросами к одному провайдеру."""

    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._lock = asyncio.Lock()
        self._next = 0.0

    async def wait(self) -> None:
        async with self._lock:
            delay = self._next - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self._next = time.monotonic() + self.min_interval


class Http:
    def __init__(self, client: httpx.AsyncClient, retries: int = 2, backoff: float | None = None):
        self.client = client
        self.retries = retries
        self.backoff = RETRY_BACKOFF if backoff is None else backoff

    async def get(self, url: str, **kwargs) -> Any:
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs) -> Any:
        return await self.request("POST", url, **kwargs)

    async def request(
        self,
        method: str,
        url: str,
        *,
        limiter: RateLimiter | None = None,
        headers: dict | None = None,
        params: dict | None = None,
        json: Any = None,
    ) -> Any:
        host = urlsplit(url).hostname
        for attempt in range(self.retries + 1):
            if limiter:
                await limiter.wait()
            retry_after = None
            try:
                r = await self.client.request(method, url, headers=headers, params=params, json=json)
            except httpx.TransportError as e:
                # Текст исключения httpx содержит URL с адресом, поэтому только тип.
                error = f"{host}: нет ответа ({type(e).__name__})"
            else:
                if r.status_code < 400:
                    try:
                        return r.json()
                    except ValueError:
                        raise AdapterError(f"{host}: ответ не JSON") from None
                error = f"{host}: HTTP {r.status_code}"
                if r.status_code not in RETRY_STATUSES:
                    raise AdapterError(error)
                retry_after = r.headers.get("retry-after")
            if attempt < self.retries:
                delay = float(retry_after) if retry_after and retry_after.isdigit() else self.backoff * 2**attempt
                await asyncio.sleep(min(delay, MAX_RETRY_DELAY) + random.uniform(0, 0.3) * self.backoff)
        raise AdapterError(error)


def new_client(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=TIMEOUT_SECONDS,
        transport=transport,
        headers={"User-Agent": "Pouch/0.1", "Accept": "application/json"},
        follow_redirects=False,
    )


class TokenDirectory(Protocol):
    def lookup(self, network: str, contract: str) -> str | None: ...

    def describe(self, coingecko_id: str) -> tuple[str, str] | None: ...


@dataclass
class Context:
    http: Http
    keys: dict[str, str]
    tokens: TokenDirectory
    # Постоянный кэш адаптеров: метаданные токенов, ответы редких запросов (DeFi раз в 30 минут).
    meta: dict[str, dict] = field(default_factory=dict)


class Adapter(Protocol):
    network: str

    async def fetch(self, address: str, ctx: Context) -> Balance: ...


# Нативные монеты: символ, название, id в CoinGecko.
NATIVE = {
    "btc": ("BTC", "Bitcoin", "bitcoin"),
    "ltc": ("LTC", "Litecoin", "litecoin"),
    "doge": ("DOGE", "Dogecoin", "dogecoin"),
    "ton": ("TON", "Toncoin", "the-open-network"),
    "tron": ("TRX", "TRON", "tron"),
    "sol": ("SOL", "Solana", "solana"),
}


def native(network: str, amount: Decimal, kind: str = "free") -> Holding:
    symbol, name, cg = NATIVE[network]
    return Holding(symbol=symbol, name=name, amount=amount, coingecko_id=cg, kind=kind)


def token(ctx: Context, network: str, contract: str, amount: Decimal, symbol: str = "", name: str = "") -> Holding:
    cg = ctx.tokens.lookup(network, contract)
    described = ctx.tokens.describe(cg) if cg else None
    if described:
        symbol, name = described
    return Holding(symbol=symbol or "?", name=name, amount=amount, coingecko_id=cg, contract=contract)
