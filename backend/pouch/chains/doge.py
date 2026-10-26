"""Dogecoin: dogechain.com, при сбое — BlockCypher.

BlockCypher без ключа ограничен 100 запросами в час на IP, поэтому он запасной.
Оба источника проверены 26.10.2026 на одном адресе и дали одинаковый баланс.
"""

from decimal import Decimal

from .base import AdapterError, Balance, Context, RateLimiter, native

DOGECHAIN = "https://dogechain.com/api/v1"
BLOCKCYPHER = "https://api.blockcypher.com/v1/doge/main"


class Dogecoin:
    network = "doge"

    def __init__(self, dogechain_url: str = DOGECHAIN, blockcypher_url: str = BLOCKCYPHER):
        self.dogechain_url = dogechain_url
        self.blockcypher_url = blockcypher_url
        self.dogechain_limiter = RateLimiter(0.5)
        self.blockcypher_limiter = RateLimiter(0.4)  # 3 RPS

    async def fetch(self, address: str, ctx: Context) -> Balance:
        errors = []
        for source in (self._dogechain, self._blockcypher):
            try:
                return Balance([native("doge", await source(address, ctx))])
            except (AdapterError, KeyError, ValueError) as e:
                errors.append(str(e) if isinstance(e, AdapterError) else f"неожиданный ответ ({type(e).__name__})")
        raise AdapterError("; ".join(errors))

    async def _dogechain(self, address: str, ctx: Context) -> Decimal:
        data = await ctx.http.get(f"{self.dogechain_url}/address/balance/{address}", limiter=self.dogechain_limiter)
        if data.get("success") != 1:
            raise AdapterError("dogechain.com: запрос не выполнен")
        return Decimal(data["balance"])

    async def _blockcypher(self, address: str, ctx: Context) -> Decimal:
        data = await ctx.http.get(f"{self.blockcypher_url}/addrs/{address}/balance", limiter=self.blockcypher_limiter)
        # final_balance — с неподтверждёнными, как и у BTC/LTC.
        return Decimal(data["final_balance"]).scaleb(-8)
