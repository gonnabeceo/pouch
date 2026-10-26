"""История для графиков.

Два источника, и интерфейс их не смешивает:

- snapshots — что было на самом деле: итог, активы и кошельки по сохранённым снапшотам.
  Считается по текущему составу портфолио (нескрытые кошельки, которые в нём сейчас),
  чтобы изменение отражало деньги, а не то, что кошелёк добавили или скрыли.
- simulated — моделирование «если бы весь период держали нынешний состав»: текущие
  количества × исторические цены CoinGecko. Доступно сразу, но это не реальная история.
"""

import asyncio
import logging
from bisect import bisect_right
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from .chains.base import AdapterError, Http, new_client

log = logging.getLogger("pouch")

TOP_ASSETS = 5
OTHER = "Прочее"
SIMULATED_MAX_ASSETS = 10  # больше — дольше первая загрузка при лимитах CoinGecko
MAX_POINTS = 400


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _thin(points: list, limit: int = MAX_POINTS) -> list:
    """Равномерно прореживает ряд, сохраняя последнюю точку."""
    if len(points) <= limit:
        return points
    step = len(points) / (limit - 1)
    return [points[int(i * step)] for i in range(limit - 1)] + [points[-1]]


# --- реальная история по снапшотам ---


def snapshot_history(portfolio, portfolio_id: int | None, days: int) -> dict:
    view = portfolio.view(portfolio_id)
    current = {w["id"]: w for w in view["wallets"]}
    since = _iso(datetime.now(UTC) - timedelta(days=days)) if days else ""

    with portfolio.vault.db() as conn:
        snaps = conn.execute(
            "SELECT id, taken_at, usd_rub FROM snapshots WHERE taken_at >= ? ORDER BY taken_at", (since,)
        ).fetchall()
        items = conn.execute(
            "SELECT i.snapshot_id, i.wallet_id, i.symbol, i.value_usd FROM snapshot_items i"
            " JOIN snapshots s ON s.id = i.snapshot_id WHERE s.taken_at >= ?", (since,)
        ).fetchall()
        first = conn.execute("SELECT min(taken_at) FROM snapshots").fetchone()[0]

    by_asset: dict[int, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    by_wallet: dict[int, dict[int, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for i in items:
        if i["value_usd"] is None or i["wallet_id"] not in current:
            continue
        value = Decimal(i["value_usd"])
        by_asset[i["snapshot_id"]][i["symbol"]] += value
        by_wallet[i["snapshot_id"]][i["wallet_id"]] += value

    # Длинные периоды — одна точка в день (последний снапшот дня), короткие — все.
    if days == 0 or days > 7:
        last_of_day = {}
        for s in snaps:
            last_of_day[s["taken_at"][:10]] = s
        snaps = list(last_of_day.values())

    points = [
        {"t": s["taken_at"], "total_usd": float(sum(by_wallet[s["id"]].values(), Decimal(0))),
         "usd_rub": float(s["usd_rub"]) if s["usd_rub"] else None}
        for s in snaps
    ]
    # Текущий итог — последняя точка, чтобы график доходил до «сейчас».
    if view["updated_at"] and (not points or view["updated_at"] > points[-1]["t"]):
        points.append({"t": view["updated_at"], "total_usd": view["total_usd"], "usd_rub": view["rub_rate"], "live": True})

    # Цвет закреплён за активом, а не за местом: топ берём по текущей стоимости.
    top = [a["symbol"] for a in view["assets"] if a["value_usd"] > 0][:TOP_ASSETS]
    asset_rows = []
    for s in snaps:
        values = by_asset[s["id"]]
        row = {sym: float(values.get(sym, 0)) for sym in top}
        rest = sum((v for sym, v in values.items() if sym not in top), Decimal(0))
        row[OTHER] = float(max(rest, Decimal(0)))
        asset_rows.append({"t": s["taken_at"], "values": row})
    if points and points[-1].get("live"):
        row = {sym: next(a["value_usd"] for a in view["assets"] if a["symbol"] == sym) for sym in top}
        row[OTHER] = max(view["total_usd"] - sum(row.values()), 0.0)
        asset_rows.append({"t": points[-1]["t"], "values": row})

    wallets = []
    for wid, w in current.items():
        series = [{"t": s["taken_at"], "value_usd": float(by_wallet[s["id"]].get(wid, 0))} for s in snaps]
        if w["value_usd"] is not None and points and points[-1].get("live"):
            series.append({"t": points[-1]["t"], "value_usd": w["value_usd"]})
        wallets.append({"id": wid, "name": w["name"], "network": w["network"], "series": _thin(series, 60)})

    return {
        "portfolio": view["portfolio"],
        "first_snapshot_at": first,
        "snapshot_count": len(snaps),
        "points": _thin(points),
        "assets": {"top": top, "rows": _thin(asset_rows, 120)},
        "wallets": wallets,
        "allocation": [{"symbol": a["symbol"], "value_usd": a["value_usd"]} for a in view["assets"] if a["value_usd"] > 0],
        "total_usd": view["total_usd"],
        "rub_rate": view["rub_rate"],
    }


# --- моделирование: текущий состав по историческим ценам ---

# Стейблкоинам история цен не нужна: считаем их по текущей стоимости и запросы не тратим.
STABLECOINS = {"USDC", "USDT", "USDT0", "DAI", "USDE", "USDS", "USD1", "FDUSD", "PYUSD", "TUSD", "USDH", "RLUSD", "USDTB"}
# Пауза между запросами истории цен: без ключа CoinGecko быстро отвечает 429.
PACE_SECONDS = {"keyless": 6.0, "key": 1.5}
RATE_LIMIT_PAUSE = 65.0
CHART_ATTEMPTS = 3


def _chart(portfolio, coin_id: str, days: int) -> tuple[list[tuple[int, Decimal]] | None, bool]:
    cached, updated_at = portfolio._cache_get(f"cg.chart:{coin_id}:{days}")
    if not cached:
        return None, False
    ttl = timedelta(hours=1) if days <= 7 else timedelta(hours=6)
    fresh = bool(updated_at) and datetime.fromisoformat(updated_at) > datetime.now(UTC) - ttl
    return [(t, Decimal(p)) for t, p in cached["prices"]], fresh


async def _price_job(portfolio) -> None:
    """Фоновая догрузка истории цен: страница получает ответ сразу и дорисовывает график по мере загрузки."""
    queue: set = portfolio.history_queue
    key = portfolio.keys().get("coingecko")
    pace = PACE_SECONDS["key" if key else "keyless"]
    async with new_client(portfolio.transport) as client:
        http = Http(client, retries=0)
        while queue:
            symbol, coin_id, days = queue.pop()
            if coin_id is None:
                symbols, _ = portfolio._cache_get("cg.symbols")
                symbols = symbols or {}
                if symbol not in symbols:
                    try:
                        symbols.update(await portfolio.coingecko.ids_by_symbol(http, key, [symbol]))
                    except AdapterError as e:
                        if "429" in str(e):
                            queue.add((symbol, None, days))
                            await asyncio.sleep(RATE_LIMIT_PAUSE)
                            continue
                        log.warning("История цен: тикер не найден (%s)", e)
                    symbols.setdefault(symbol, None)
                    portfolio._cache_put("cg.symbols", symbols)
                if symbols.get(symbol):
                    queue.add((symbol, symbols[symbol], days))
                continue
            _, fresh = _chart(portfolio, coin_id, days)
            if fresh:
                continue
            for _attempt in range(CHART_ATTEMPTS):
                try:
                    prices = await portfolio.coingecko.price_history(http, key, coin_id, days)
                except AdapterError as e:
                    if "429" in str(e):
                        await asyncio.sleep(RATE_LIMIT_PAUSE)
                        continue
                    log.warning("История цен не загружена: %s", e)
                    break
                portfolio._cache_put(f"cg.chart:{coin_id}:{days}", {"prices": [[t, str(p)] for t, p in prices]})
                break
            await asyncio.sleep(pace)


def _ensure_job(portfolio) -> None:
    job = getattr(portfolio, "history_job", None)
    if job is None or job.done():
        async def run():
            try:
                await _price_job(portfolio)
            except Exception:  # база заблокировалась или сеть упала — следующий запрос страницы запустит снова
                log.exception("Фоновая загрузка истории цен прервалась")

        portfolio.history_job = asyncio.create_task(run())


async def wait_jobs(portfolio) -> None:
    """Для тестов: дождаться фоновой загрузки."""
    job = getattr(portfolio, "history_job", None)
    if job:
        await job


async def simulated_history(portfolio, portfolio_id: int | None, days: int) -> dict:
    view = portfolio.view(portfolio_id)
    days = min(max(days or 365, 1), 365)
    if not hasattr(portfolio, "history_queue"):
        portfolio.history_queue = set()

    ids: dict[str, str] = {}
    for w in view["wallets"]:
        for h in w["holdings"]:
            if h["coingecko_id"] and h["symbol"] not in ids:
                ids[h["symbol"]] = h["coingecko_id"]
    symbols, _ = portfolio._cache_get("cg.symbols")
    for sym, coin_id in (symbols or {}).items():
        if coin_id:
            ids.setdefault(sym, coin_id)

    assets = sorted(view["assets"], key=lambda a: abs(a["value_usd"]), reverse=True)
    stables = [a for a in assets if a["symbol"].upper() in STABLECOINS]
    # Самые крупные нестабильные позиции получают историю цен; остальное — по текущей стоимости.
    chosen = [a for a in assets if a["value_usd"] and a not in stables][:SIMULATED_MAX_ASSETS]

    histories: dict[str, list[tuple[int, Decimal]]] = {}
    pending = []
    for a in chosen:
        sym = a["symbol"]
        coin_id = ids.get(sym)
        if coin_id is None:
            if (symbols or {}).get(sym, "?") is None:
                continue  # CoinGecko такой тикер не знает
            pending.append(sym)
            portfolio.history_queue.add((sym, None, days))
            continue
        prices, fresh = _chart(portfolio, coin_id, days)
        if prices:
            histories[sym] = prices  # даже устаревшая история лучше пустого графика
        if not fresh:
            pending.append(sym)
            portfolio.history_queue.add((sym, coin_id, days))
    if portfolio.history_queue:
        _ensure_job(portfolio)

    with_history = [a for a in chosen if histories.get(a["symbol"])]
    constant = sum((Decimal(str(a["value_usd"])) for a in assets if a not in with_history), Decimal(0))
    total_abs = sum((abs(Decimal(str(a["value_usd"]))) for a in assets), Decimal(0))
    covered = sum((abs(Decimal(str(a["value_usd"]))) for a in with_history + stables), Decimal(0))

    points = []
    if with_history:
        # Сетка времени — по ряду самой крупной позиции; для остальных берём последнюю известную цену.
        grid = [t for t, _ in histories[with_history[0]["symbol"]]]
        series = {a["symbol"]: ([t for t, _ in histories[a["symbol"]]], [p for _, p in histories[a["symbol"]]])
                  for a in with_history}
        amounts = {a["symbol"]: Decimal(a["amount"]) for a in with_history}
        for t in grid:
            total = constant
            for sym, (times, prices) in series.items():
                idx = max(bisect_right(times, t) - 1, 0)
                total += amounts[sym] * prices[idx]
            points.append({"t": _iso(datetime.fromtimestamp(t / 1000, UTC)), "total_usd": float(total)})
    points.append({"t": _iso(datetime.now(UTC)), "total_usd": view["total_usd"], "live": True})

    return {
        "portfolio": view["portfolio"],
        "days": days,
        "points": _thin(points),
        "coverage_pct": float(covered / total_abs * 100) if total_abs else 0.0,
        "assets_with_history": [a["symbol"] for a in with_history],
        "assets_stable": [a["symbol"] for a in stables],
        "assets_constant": [a["symbol"] for a in assets if a not in with_history and a not in stables],
        "pending": pending,
        "rub_rate": view["rub_rate"],
    }
