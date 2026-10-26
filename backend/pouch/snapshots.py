"""Снапшоты состояния и экспорт.

После каждого полного успешного обновления, но не чаще раза в час, сохраняется снапшот:
по каждому кошельку и токену — количество, цена и стоимость, account value Hyperliquid,
курс USD/RUB и итоги по каждому портфолио. Графики появятся позже, данные копятся сейчас.
"""

import csv
import io
import json
import zipfile
from datetime import UTC, datetime, timedelta

SNAPSHOT_EVERY = timedelta(hours=1)
CSV_COLUMNS = [
    "taken_at", "wallet_id", "wallet_name", "network", "symbol", "kind", "protocol", "chain",
    "amount", "price_usd", "value_usd", "usd_rub",
]


def _num(value) -> str | None:
    return None if value is None else repr(value) if isinstance(value, float) else str(value)


def take_if_due(portfolio, now: datetime | None = None) -> int | None:
    now = now or datetime.now(UTC)
    with portfolio.vault.db() as conn:
        last = conn.execute("SELECT taken_at FROM snapshots ORDER BY taken_at DESC LIMIT 1").fetchone()
    if last and datetime.fromisoformat(last["taken_at"]) > now - SNAPSHOT_EVERY:
        return None
    return take(portfolio, now)


def take(portfolio, now: datetime) -> int:
    everything = portfolio.view(None)
    totals = [(None, everything["portfolio"]["name"], everything["total_usd"])]
    for p in portfolio.portfolios():
        totals.append((p["id"], p["name"], portfolio.view(p["id"])["total_usd"]))

    with portfolio.vault.db() as conn:
        snapshot_id = conn.execute(
            "INSERT INTO snapshots (taken_at, total_usd, usd_rub) VALUES (?, ?, ?)",
            (now.isoformat(timespec="seconds"), _num(everything["total_usd"]), _num(everything["rub_rate"])),
        ).lastrowid
        for w in everything["wallets"]:
            conn.executemany(
                "INSERT INTO snapshot_items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (snapshot_id, w["id"], w["network"], h["symbol"], h["kind"], h["protocol"], h["chain"],
                     h["amount"], _num(h["price_usd"]), _num(h["value_usd"]))
                    for h in w["holdings"]
                ],
            )
            for account in (w["details"] or {}).get("accounts", []):
                account_total = sum(h["value_usd"] or 0 for h in w["holdings"] if h["protocol"] == account["label"])
                conn.execute(
                    "INSERT INTO snapshot_hl_accounts VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (snapshot_id, w["id"], account["address"], account["label"], account["mode"],
                     account["perp_value"], _num(account_total)),
                )
        conn.executemany(
            "INSERT INTO snapshot_portfolios VALUES (?, ?, ?, ?)",
            [(snapshot_id, pid, name, _num(total)) for pid, name, total in totals],
        )
    return snapshot_id


def summary(vault) -> dict:
    with vault.db() as conn:
        row = conn.execute("SELECT count(*) AS n, min(taken_at) AS first, max(taken_at) AS last FROM snapshots").fetchone()
    return {"count": row["n"], "first": row["first"], "last": row["last"]}


def export_json(vault) -> bytes:
    with vault.db() as conn:
        names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM wallets")}
        snapshots = [dict(r) for r in conn.execute("SELECT * FROM snapshots ORDER BY taken_at")]
        for s in snapshots:
            sid = s.pop("id")
            s["portfolios"] = [
                {k: r[k] for k in ("portfolio_id", "name", "total_usd")}
                for r in conn.execute("SELECT * FROM snapshot_portfolios WHERE snapshot_id = ?", (sid,))
            ]
            s["items"] = [
                {**{k: r[k] for k in r.keys() if k != "snapshot_id"}, "wallet_name": names.get(r["wallet_id"])}
                for r in conn.execute("SELECT * FROM snapshot_items WHERE snapshot_id = ?", (sid,))
            ]
            # Адреса суб-аккаунтов в экспорт не идут: достаточно метки и ID кошелька.
            s["hyperliquid"] = [
                {k: r[k] for k in r.keys() if k not in ("snapshot_id", "address")}
                for r in conn.execute("SELECT * FROM snapshot_hl_accounts WHERE snapshot_id = ?", (sid,))
            ]
    body = {"exported_at": datetime.now(UTC).isoformat(timespec="seconds"), "snapshots": snapshots}
    return json.dumps(body, ensure_ascii=False, indent=2).encode()


def export_csv(vault) -> bytes:
    """Одна строка на токен в снапшоте; итоги по портфолио — в JSON-экспорте."""
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    with vault.db() as conn:
        rows = conn.execute(
            "SELECT s.taken_at, s.usd_rub, i.*, w.name AS wallet_name FROM snapshot_items i"
            " JOIN snapshots s ON s.id = i.snapshot_id LEFT JOIN wallets w ON w.id = i.wallet_id"
            " ORDER BY s.taken_at, i.wallet_id"
        ).fetchall()
    for r in rows:
        writer.writerow({k: r[k] for k in CSV_COLUMNS})
    return out.getvalue().encode("utf-8-sig")  # BOM, чтобы Excel и Numbers поняли кириллицу


BACKUP_README = """Зашифрованный бэкап Pouch ({date}).

pouch.db — база, зашифрованная SQLCipher; pouch.meta.json — соль и параметры Argon2id.
Без мастер-пароля, действовавшего на момент бэкапа, база не читается.

Восстановление: остановите Pouch, положите оба файла в папку data/ (или в POUCH_DATA_DIR)
вместо текущих и запустите Pouch. Войдите паролем, действовавшим на момент бэкапа.
"""


def backup_zip(vault) -> bytes:
    files = vault.backup_files()
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
        z.writestr("README.txt", BACKUP_README.format(date=datetime.now(UTC).date().isoformat()))
    return out.getvalue()
