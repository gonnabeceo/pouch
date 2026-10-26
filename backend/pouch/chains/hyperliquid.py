"""Hyperliquid через публичный info API (без ключа): спот, перпы, vaults, стейкинг HYPE, суб-аккаунты.

Как считается итог — проверено 26.10.2026 сверкой с запросом `portfolio`, то есть с суммой
в интерфейсе Hyperliquid (docs/hyperliquid.md):

- unified account и portfolio margin: все балансы и нереализованный PnL живут в спот-состоянии,
  account value перпов — лишь выделенная маржа. Считаем спот, перпы не прибавляем.
- стандартный режим: перпы и спот раздельны, у каждой перп-биржи (основной и HIP-3) свой
  баланс. Считаем account value по всем биржам (PnL уже внутри) плюс спот.
- vaults и стейкинг HYPE (делегировано, не делегировано, в очереди на вывод) отдельно от
  спота в обоих режимах. Номинал позиций не прибавляется никогда.

Открытые позиции читаются со всех перп-бирж в любом режиме — для отдельного блока.
"""

from decimal import Decimal

from .base import Balance, Context, Holding, RateLimiter, format_amount

INFO_URL = "https://api.hyperliquid.xyz/info"
UNIFIED_MODES = {"unifiedAccount", "portfolioMargin"}
# Расхождение с интерфейсом Hyperliquid, после которого показываем предупреждение.
MISMATCH_SHARE = Decimal("0.01")
MISMATCH_MIN_USD = Decimal(1)


class Hyperliquid:
    network = "hyperliquid"

    def __init__(self, url: str = INFO_URL):
        self.url = url
        # 1200 единиц веса в минуту на IP; запросы весят 2–20.
        self.limiter = RateLimiter(0.3)

    async def _info(self, ctx: Context, body: dict):
        return await ctx.http.post(self.url, limiter=self.limiter, json=body)

    async def fetch(self, address: str, ctx: Context) -> Balance:
        master = address.lower()
        dexes = [d["name"] if d else "" for d in await self._info(ctx, {"type": "perpDexs"})]
        prices = await self._spot_prices(ctx)
        subs = await self._info(ctx, {"type": "subAccounts", "user": master}) or []

        holdings: list[Holding] = []
        accounts: list[dict] = []
        warnings: list[str] = []
        for label, user in [("Мастер", master)] + [(f"Суб-аккаунт {s['name']}", s["subAccountUser"].lower()) for s in subs]:
            account_holdings, details, warning = await self._account(ctx, label, user, dexes, prices)
            holdings += account_holdings
            accounts.append(details)
            if warning:
                warnings.append(warning)
        return Balance(holdings, warning="; ".join(warnings) or None, details={"accounts": accounts})

    async def _spot_prices(self, ctx: Context) -> dict[str, Decimal]:
        meta = await self._info(ctx, {"type": "spotMeta"})
        mids = await self._info(ctx, {"type": "allMids"})
        names = {t["index"]: t["name"] for t in meta["tokens"]}
        pairs = [(names[p["tokens"][0]], names[p["tokens"][1]], mids.get(p["name"])) for p in meta["universe"]]
        usd = {"USDC": Decimal(1)}
        # Сначала пары к USDC, затем к другим котируемым токенам с уже известной ценой (USDT0 и т. п.).
        for base, quote, mid in pairs:
            if quote == "USDC" and mid:
                usd.setdefault(base, Decimal(mid))
        for base, quote, mid in pairs:
            if mid and base not in usd and quote in usd:
                usd[base] = Decimal(mid) * usd[quote]
        return usd

    async def _account(self, ctx: Context, label: str, user: str, dexes: list[str], prices: dict[str, Decimal]):
        mode = await self._info(ctx, {"type": "userAbstraction", "user": user})
        unified = mode in UNIFIED_MODES
        spot = await self._info(ctx, {"type": "spotClearinghouseState", "user": user})

        perp_value = Decimal(0)
        positions = []
        for dex in dexes:
            state = await self._info(ctx, {"type": "clearinghouseState", "user": user, "dex": dex})
            perp_value += Decimal(state["marginSummary"]["accountValue"])
            positions += [_position(dex, item["position"]) for item in state.get("assetPositions", [])]

        vaults = await self._info(ctx, {"type": "userVaultEquities", "user": user}) or []
        staking = await self._info(ctx, {"type": "delegatorSummary", "user": user})
        portfolio = dict(await self._info(ctx, {"type": "portfolio", "user": user}))

        holdings = []
        for b in spot.get("balances", []):
            amount = Decimal(b["total"])
            if amount:
                holdings.append(Holding(b["coin"], amount, price_usd=prices.get(b["coin"]), protocol=label))
        if not unified and perp_value:
            holdings.append(
                Holding("Перпы", perp_value, name="Account value", price_usd=Decimal(1), kind="deposit", protocol=label)
            )
        vault_details = []
        for v in vaults:
            equity = Decimal(v["equity"])
            name = await self._vault_name(ctx, v["vaultAddress"])
            vault_details.append(
                {"name": name, "address": v["vaultAddress"], "equity": format_amount(equity),
                 "locked_until": v.get("lockedUntilTimestamp")}
            )
            if equity:
                holdings.append(Holding("Vault", equity, name=name, price_usd=Decimal(1), kind="deposit", protocol=label))
        staked = sum(Decimal(staking[k]) for k in ("delegated", "undelegated", "totalPendingWithdrawal"))
        if staked:
            holdings.append(Holding("HYPE", staked, price_usd=prices.get("HYPE"), kind="staked", protocol=label))

        computed = sum((h.amount * h.price_usd for h in holdings if h.price_usd is not None), Decimal(0))
        history = portfolio.get("day", {}).get("accountValueHistory") or []
        ui_total = Decimal(history[-1][1]) if history else None
        warning = None
        if ui_total is not None:
            diff = abs(computed - ui_total)
            if diff > MISMATCH_MIN_USD and diff > MISMATCH_SHARE * abs(ui_total):
                share = diff / abs(ui_total) * 100 if ui_total else Decimal(100)
                warning = f"{label}: сумма расходится с интерфейсом Hyperliquid на {share:.1f}%"

        details = {
            "label": label,
            "address": user,
            "mode": mode,
            "perp_value": None if unified else format_amount(perp_value),
            "ui_total": format_amount(ui_total) if ui_total is not None else None,
            "positions": positions,
            "vaults": vault_details,
        }
        return holdings, details, warning

    async def _vault_name(self, ctx: Context, vault: str) -> str:
        key = f"hl.vault:{vault.lower()}"
        if key not in ctx.meta:
            info = await self._info(ctx, {"type": "vaultDetails", "vaultAddress": vault})
            ctx.meta[key] = {"name": info.get("name") or vault}
        return ctx.meta[key]["name"]


def _position(dex: str, p: dict) -> dict:
    size = Decimal(p["szi"])
    leverage = p.get("leverage") or {}
    return {
        "dex": dex or None,
        "coin": p["coin"],
        "side": "long" if size > 0 else "short",
        "size": format_amount(abs(size)),
        "entry_px": p.get("entryPx"),
        "position_value": p.get("positionValue"),
        "unrealized_pnl": p.get("unrealizedPnl"),
        "leverage": leverage.get("value"),
        "leverage_type": leverage.get("type"),
        "liquidation_px": p.get("liquidationPx"),
        "margin_used": p.get("marginUsed"),
    }
