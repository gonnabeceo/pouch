"""EVM через Zerion: токены во всех EVM-сетях, стейкинг и DeFi-позиции одним источником.

Токены (only_simple) запрашиваются при каждом обновлении. DeFi (only_complex) идёт из
отдельной, меньшей квоты, поэтому запрашивается не чаще раза в COMPLEX_TTL и между
обновлениями берётся из кэша. Подробности и лимиты — docs/evm-aggregators.md.
"""

import base64
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from .base import AdapterError, Balance, Context, Holding, RateLimiter

BASE = "https://api.zerion.io/v1"
COMPLEX_TTL = timedelta(minutes=30)
NO_KEY = "нужен ключ Zerion — задайте его в настройках"


def parse_positions(data: dict) -> tuple[list[Holding], int]:
    holdings: list[Holding] = []
    spam = 0
    for item in data.get("data", []):
        attrs = item.get("attributes") or {}
        flags = attrs.get("flags") or {}
        if flags.get("is_trash"):
            spam += 1
            continue
        if flags.get("displayable") is False:
            # Служебные токены протоколов (доли в пулах и т. п.): их стоимость уже в позиции.
            continue
        amount = Decimal(str((attrs.get("quantity") or {}).get("numeric") or "0"))
        if amount == 0:
            continue
        info = attrs.get("fungible_info") or {}
        relationships = item.get("relationships") or {}
        position_type = attrs.get("position_type") or "wallet"
        price = attrs.get("price")
        app = attrs.get("application_metadata") or {}
        holdings.append(
            Holding(
                symbol=info.get("symbol") or "?",
                name=info.get("name") or "",
                amount=amount,
                contract=((relationships.get("fungible") or {}).get("data") or {}).get("id"),
                kind="free" if position_type == "wallet" else position_type,
                price_usd=Decimal(str(price)) if price is not None else None,
                protocol=app.get("name") or attrs.get("protocol"),
                chain=((relationships.get("chain") or {}).get("data") or {}).get("id"),
            )
        )
    return holdings, spam


class Zerion:
    network = "evm"

    def __init__(self, base_url: str = BASE):
        self.base_url = base_url
        self.limiter = RateLimiter(0.4)  # 3 RPS на бесплатном тарифе

    async def _positions(self, address: str, ctx: Context, kind: str) -> dict:
        key = ctx.keys.get("zerion")
        if not key:
            raise AdapterError(NO_KEY)
        token = base64.b64encode(f"{key}:".encode()).decode()
        return await ctx.http.get(
            f"{self.base_url}/wallets/{address}/positions/",
            limiter=self.limiter,
            headers={"Authorization": f"Basic {token}"},
            params={"filter[positions]": kind, "filter[trash]": "no_filter", "currency": "usd", "sort": "value"},
        )

    async def fetch(self, address: str, ctx: Context) -> Balance:
        holdings, spam = parse_positions(await self._positions(address, ctx, "only_simple"))
        complex_holdings, warning = await self._complex(address, ctx)
        return Balance(holdings + complex_holdings, spam, warning)

    async def _complex(self, address: str, ctx: Context) -> tuple[list[Holding], str | None]:
        cache_key = f"zerion.complex:{address.lower()}"
        cached = ctx.meta.get(cache_key)
        now = datetime.now(UTC)
        if cached and datetime.fromisoformat(cached["at"]) > now - COMPLEX_TTL:
            return [Holding.from_json(h) for h in cached["holdings"]], None
        try:
            holdings, _ = parse_positions(await self._positions(address, ctx, "only_complex"))
        except AdapterError as e:
            if not cached:
                raise
            # Квота DeFi-запросов кончилась или сбой: токены свежие, DeFi — последний известный.
            return [Holding.from_json(h) for h in cached["holdings"]], f"DeFi не обновился ({e}), показан кэш"
        ctx.meta[cache_key] = {"at": now.isoformat(timespec="seconds"), "holdings": [h.to_json() for h in holdings]}
        return holdings, None
