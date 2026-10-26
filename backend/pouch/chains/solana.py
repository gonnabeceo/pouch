"""Solana через JSON-RPC: публичный RPC или Helius, если задан ключ.

Нативный стейкинг — стейк-аккаунты, где кошелёк указан как withdrawer (он владеет средствами).
Ликвидный стейкинг (jitoSOL, mSOL и т. п.) приходит обычными SPL-токенами. DeFi на Solana
бесплатные источники не отдают.
"""

from collections import defaultdict
from decimal import Decimal

from .base import AdapterError, Balance, Context, RateLimiter, native, token

PUBLIC_RPC = "https://api.mainnet-beta.solana.com"
HELIUS_RPC = "https://mainnet.helius-rpc.com/"
STAKE_PROGRAM = "Stake11111111111111111111111111111111111111"
WITHDRAWER_OFFSET = 44  # 4 байта типа + 8 rent_exempt_reserve + 32 staker
TOKEN_PROGRAMS = (
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",  # SPL Token
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",  # Token-2022
)


class SolanaRpc:
    network = "sol"

    def __init__(self, public_url: str = PUBLIC_RPC, helius_url: str = HELIUS_RPC):
        self.public_url = public_url
        self.helius_url = helius_url
        self.public_limiter = RateLimiter(0.3)
        self.helius_limiter = RateLimiter(0.12)  # 10 RPS на бесплатном Helius

    async def _rpc(self, ctx: Context, method: str, params: list):
        key = ctx.keys.get("helius")
        url, limiter, query = (
            (self.helius_url, self.helius_limiter, {"api-key": key}) if key else (self.public_url, self.public_limiter, None)
        )
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        data = await ctx.http.post(url, limiter=limiter, params=query, json=body)
        if "error" in data:
            raise AdapterError(f"Solana RPC: {method} — ошибка {data['error'].get('code')}")
        return data["result"]

    async def fetch(self, address: str, ctx: Context) -> Balance:
        lamports = (await self._rpc(ctx, "getBalance", [address]))["value"]
        holdings = [native("sol", Decimal(lamports).scaleb(-9))]

        # Один mint может лежать в нескольких токен-аккаунтах — суммируем.
        raw_by_mint: dict[str, int] = defaultdict(int)
        decimals: dict[str, int] = {}
        for program in TOKEN_PROGRAMS:
            result = await self._rpc(
                ctx, "getTokenAccountsByOwner", [address, {"programId": program}, {"encoding": "jsonParsed"}]
            )
            for account in result["value"]:
                info = account["account"]["data"]["parsed"]["info"]
                raw_by_mint[info["mint"]] += int(info["tokenAmount"]["amount"])
                decimals[info["mint"]] = info["tokenAmount"]["decimals"]

        stake_accounts = await self._rpc(
            ctx,
            "getProgramAccounts",
            [
                STAKE_PROGRAM,
                {"encoding": "base64", "dataSlice": {"offset": 0, "length": 0},
                 "filters": [{"memcmp": {"offset": WITHDRAWER_OFFSET, "bytes": address}}]},
            ],
        )
        # Баланс стейк-аккаунта целиком: делегированное, награды и резерв на ренту.
        staked = sum(a["account"]["lamports"] for a in stake_accounts)
        if staked:
            holdings.append(native("sol", Decimal(staked).scaleb(-9), kind="staked"))

        hidden = 0
        for mint, raw in raw_by_mint.items():
            if raw == 0:
                continue
            if ctx.tokens.lookup("sol", mint) is None:
                hidden += 1
                continue
            holdings.append(token(ctx, "sol", mint, Decimal(raw).scaleb(-decimals[mint])))
        return Balance(holdings, hidden)
