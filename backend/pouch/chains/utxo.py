"""Bitcoin и Litecoin через Esplora-совместимый API (mempool.space и его форк litecoinspace.org)."""

from decimal import Decimal

from .base import Balance, Context, RateLimiter, native


class Esplora:
    def __init__(self, network: str, base_url: str, min_interval: float = 0.25):
        self.network = network
        self.base_url = base_url
        self.limiter = RateLimiter(min_interval)

    async def fetch(self, address: str, ctx: Context) -> Balance:
        data = await ctx.http.get(f"{self.base_url}/address/{address}", limiter=self.limiter)
        # Подтверждённые плюс неподтверждённые из мемпула — так баланс показывает и сам эксплорер.
        units = sum(
            data[part]["funded_txo_sum"] - data[part]["spent_txo_sum"]
            for part in ("chain_stats", "mempool_stats")
        )
        return Balance([native(self.network, Decimal(units).scaleb(-8))])
