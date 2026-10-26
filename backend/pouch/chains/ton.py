"""TON через TonAPI: баланс, жетоны и стейкинг в номинатор-пулах.

Ликвидный стейкинг (tsTON, stTON и т. п.) — это жетоны. Пулы ликвидного стейкинга
TonAPI тоже показывает среди позиций, поэтому их пропускаем, чтобы не считать дважды.
"""

from decimal import Decimal

from .base import Balance, Context, RateLimiter, native, token

BASE = "https://tonapi.io/v2"
LIQUID_POOLS = {"liquidTF"}


class TonApi:
    network = "ton"

    def __init__(self, base_url: str = BASE):
        self.base_url = base_url
        # 1 RPS и без ключа, и на бесплатном ключе.
        self.limiter = RateLimiter(1.1)

    async def fetch(self, address: str, ctx: Context) -> Balance:
        key = ctx.keys.get("tonapi")
        headers = {"Authorization": f"Bearer {key}"} if key else None
        account = await ctx.http.get(f"{self.base_url}/accounts/{address}", limiter=self.limiter, headers=headers)
        jettons = await ctx.http.get(
            f"{self.base_url}/accounts/{address}/jettons", limiter=self.limiter, headers=headers
        )

        holdings = [native("ton", Decimal(account.get("balance", 0)).scaleb(-9))]
        hidden = 0
        for item in jettons.get("balances", []):
            jetton = item["jetton"]
            amount = Decimal(item["balance"]).scaleb(-int(jetton.get("decimals", 9)))
            if amount == 0:
                continue
            contract = jetton["address"].lower()
            verification = jetton.get("verification")
            known = ctx.tokens.lookup("ton", contract) is not None
            # Спам-фильтр: только жетоны, известные CoinGecko или проверенные TonAPI.
            if verification == "blacklist" or not (known or verification == "whitelist"):
                hidden += 1
                continue
            holdings.append(token(ctx, "ton", contract, amount, jetton.get("symbol", ""), jetton.get("name", "")))
        staked = await self._staked(address, ctx, headers)
        if staked:
            holdings.append(native("ton", Decimal(staked).scaleb(-9), kind="staked"))
        return Balance(holdings, hidden)

    async def _staked(self, address: str, ctx: Context, headers: dict | None) -> int:
        data = await ctx.http.get(
            f"{self.base_url}/staking/nominator/{address}/pools", limiter=self.limiter, headers=headers
        )
        total = 0
        for position in data.get("pools", []):
            if await self._implementation(position["pool"], ctx, headers) in LIQUID_POOLS:
                continue
            # pending_withdraw — часть amount, которую попросили вывести; ready_withdraw уже освобождён.
            total += position["amount"] + position["pending_deposit"] + position["ready_withdraw"]
        return total

    async def _implementation(self, pool: str, ctx: Context, headers: dict | None) -> str:
        key = f"ton.pool:{pool}"
        if key not in ctx.meta:
            info = await ctx.http.get(f"{self.base_url}/staking/pool/{pool}", limiter=self.limiter, headers=headers)
            ctx.meta[key] = {"implementation": info.get("pool", {}).get("implementation", "")}
        return ctx.meta[key]["implementation"]
