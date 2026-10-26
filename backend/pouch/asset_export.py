"""Экспорт статистики по активам — только агрегаты по монетам, без кошельков.

В файл не попадают ни адреса, ни названия кошельков, ни сети, ни заметки: только актив,
количество, цена, стоимость и доля. Два вида:

- current — текущий состав выбранного портфолио;
- history — по каждому снапшоту: сколько какого актива и на какую сумму.
"""

import csv
import io
import json
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal

from .chains.base import format_amount

CURRENT_COLUMNS = ["asset", "name", "amount", "price_usd", "value_usd", "value_rub", "share_pct"]
HISTORY_COLUMNS = ["taken_at", "asset", "amount", "value_usd", "value_rub", "usd_rub"]
# Подписи в файлах экспорта на языке интерфейса.
TOTAL = {"ru": "ИТОГО", "en": "TOTAL"}
ALL_WALLETS = {"ru": "Все кошельки", "en": "All wallets"}


def _money(value: Decimal | None) -> str | None:
    return None if value is None else str(value.quantize(Decimal("0.01")))


def _price(value: Decimal | None) -> str | None:
    return None if value is None else format_amount(Decimal(f"{value:.8g}"))


def _portfolio_name(view: dict, lang: str) -> str:
    return ALL_WALLETS[lang] if view["portfolio"]["id"] is None else view["portfolio"]["name"]


def current(portfolio, portfolio_id: int | None, lang: str = "ru") -> dict:
    view = portfolio.view(portfolio_id)
    total = Decimal(str(view["total_usd"]))
    rub = Decimal(str(view["rub_rate"])) if view["rub_rate"] else None
    assets = []
    for a in view["assets"]:
        amount, value = Decimal(a["amount"]), Decimal(str(a["value_usd"]))
        assets.append({
            "asset": a["symbol"],
            "name": a["name"],
            "amount": format_amount(amount),
            "price_usd": _price(value / amount) if amount else None,
            "value_usd": _money(value),
            "value_rub": _money(value * rub) if rub else None,
            "share_pct": str((value / total * 100).quantize(Decimal("0.01"))) if total else None,
        })
    return {
        "portfolio": _portfolio_name(view, lang),
        "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "prices_at": view["prices_at"],
        "usd_rub": str(rub) if rub else None,
        "total_usd": _money(total),
        "total_rub": _money(total * rub) if rub else None,
        "incomplete": view["incomplete"],
        "assets": assets,
    }


def history(portfolio, portfolio_id: int | None, lang: str = "ru") -> dict:
    """По снапшотам. Состав портфолио берётся текущий: какие кошельки в него входят сейчас."""
    name = _portfolio_name(portfolio.view(portfolio_id), lang)
    with portfolio.vault.db() as conn:
        members = None
        if portfolio_id is not None:
            members = {r["wallet_id"] for r in conn.execute(
                "SELECT wallet_id FROM portfolio_wallets WHERE portfolio_id = ?", (portfolio_id,))}
        snapshots = conn.execute("SELECT id, taken_at, usd_rub FROM snapshots ORDER BY taken_at").fetchall()
        items = conn.execute(
            "SELECT snapshot_id, wallet_id, symbol, kind, amount, value_usd FROM snapshot_items"
        ).fetchall()

    by_snapshot: dict[int, dict[str, list[Decimal]]] = defaultdict(lambda: defaultdict(lambda: [Decimal(0), Decimal(0)]))
    for i in items:
        if i["value_usd"] is None or (members is not None and i["wallet_id"] not in members):
            continue
        sign = -1 if i["kind"] == "loan" else 1  # долг уменьшает позицию по активу
        bucket = by_snapshot[i["snapshot_id"]][i["symbol"]]
        bucket[0] += sign * Decimal(i["amount"])
        bucket[1] += Decimal(i["value_usd"])

    result = []
    for s in snapshots:
        rub = Decimal(s["usd_rub"]) if s["usd_rub"] else None
        assets = sorted(by_snapshot.get(s["id"], {}).items(), key=lambda kv: kv[1][1], reverse=True)
        total = sum((v for _, (_, v) in assets), Decimal(0))
        result.append({
            "taken_at": s["taken_at"],
            "usd_rub": str(rub) if rub else None,
            "total_usd": _money(total),
            "assets": [
                {"asset": symbol, "amount": format_amount(amount), "value_usd": _money(value),
                 "value_rub": _money(value * rub) if rub else None}
                for symbol, (amount, value) in assets
            ],
        })
    return {"portfolio": name, "exported_at": datetime.now(UTC).isoformat(timespec="seconds"), "snapshots": result}


def to_json(data: dict) -> bytes:
    return json.dumps(data, ensure_ascii=False, indent=2).encode()


def current_csv(data: dict, lang: str = "ru") -> bytes:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=CURRENT_COLUMNS)
    writer.writeheader()
    writer.writerows(data["assets"])
    writer.writerow({"asset": TOTAL[lang], "value_usd": data["total_usd"], "value_rub": data["total_rub"],
                     "share_pct": "100.00" if data["assets"] else None})
    return out.getvalue().encode("utf-8-sig")  # BOM, чтобы Excel и Numbers поняли кириллицу


def history_csv(data: dict) -> bytes:
    """Длинный формат: строка на актив в снапшоте — удобно для сводных таблиц."""
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=HISTORY_COLUMNS)
    writer.writeheader()
    for s in data["snapshots"]:
        for a in s["assets"]:
            writer.writerow({"taken_at": s["taken_at"], "usd_rub": s["usd_rub"], **a})
    return out.getvalue().encode("utf-8-sig")
