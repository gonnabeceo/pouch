"""Tron через TronGrid: TRX, застейканный TRX (Stake 2.0) и TRC-20.

TronGrid отдаёт TRC-20 как пары «контракт → сырое количество» без decimals и символа.
Decimals читаем у контракта один раз и кэшируем, символ и название берём из CoinGecko.
"""

from decimal import Decimal

from .base import AdapterError, Balance, Context, RateLimiter, native, token

BASE = "https://api.trongrid.io"


class TronGrid:
    network = "tron"

    def __init__(self, base_url: str = BASE):
        self.base_url = base_url
        self.limiter = RateLimiter(0.35)

    def _headers(self, ctx: Context) -> dict | None:
        key = ctx.keys.get("trongrid")
        return {"TRON-PRO-API-KEY": key} if key else None

    async def fetch(self, address: str, ctx: Context) -> Balance:
        data = await ctx.http.get(
            f"{self.base_url}/v1/accounts/{address}", limiter=self.limiter, headers=self._headers(ctx)
        )
        accounts = data.get("data") or []
        if not accounts:
            # Адрес ещё не активирован в сети: баланса нет.
            return Balance([native("tron", Decimal(0))])
        account = accounts[0]

        holdings = [native("tron", Decimal(account.get("balance", 0)).scaleb(-6))]
        staked = (
            sum(f.get("amount", 0) for f in account.get("frozenV2", []))
            + account.get("delegated_frozenV2_balance_for_bandwidth", 0)
            + account.get("account_resource", {}).get("delegated_frozenV2_balance_for_energy", 0)
            + sum(u.get("unfreeze_amount", 0) for u in account.get("unfrozenV2", []))
        )
        if staked:
            holdings.append(native("tron", Decimal(staked).scaleb(-6), kind="staked"))

        hidden = 0
        for entry in account.get("trc20", []):
            for contract, raw in entry.items():
                if int(raw) == 0:
                    continue
                if ctx.tokens.lookup("tron", contract) is None:
                    hidden += 1
                    continue
                decimals = await self._decimals(contract, address, ctx)
                holdings.append(token(ctx, "tron", contract, Decimal(int(raw)).scaleb(-decimals)))
        return Balance(holdings, hidden)

    async def _decimals(self, contract: str, owner: str, ctx: Context) -> int:
        key = f"tron:{contract}"
        if key not in ctx.meta:
            result = await ctx.http.post(
                f"{self.base_url}/wallet/triggerconstantcontract",
                limiter=self.limiter,
                headers=self._headers(ctx),
                json={
                    "owner_address": owner,
                    "contract_address": contract,
                    "function_selector": "decimals()",
                    "visible": True,
                },
            )
            values = result.get("constant_result") or []
            if not values or not values[0]:
                raise AdapterError("TronGrid: не удалось прочитать decimals токена")
            ctx.meta[key] = {"decimals": int(values[0], 16)}
        return ctx.meta[key]["decimals"]
