"""Обновление балансов и цен, подсчёт итога.

Каждый кошелёк опрашивается отдельно и параллельно: ошибка одного источника не трогает
остальные. Последний успешный результат хранится в базе, и при сбое показывается он
с пометкой «устарело» — тихо занижать сумму нельзя.
"""

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import sqlcipher3

from .chains import default_adapters
from .chains.base import Adapter, AdapterError, Balance, Context, Http, format_amount, new_client
from . import snapshots
from .prices import CoinGecko, Directory
from .vault import Vault

log = logging.getLogger("pouch")

API_KEYS = ("zerion", "coingecko", "helius", "tonapi", "trongrid")
DEFAULT_DUST_USD = 1.0
DEFAULT_REFRESH_MINUTES = 5
SETTINGS = {"dust_threshold_usd": DEFAULT_DUST_USD, "refresh_interval_minutes": DEFAULT_REFRESH_MINUTES}
STALE_AFTER_INTERVALS = 3  # данные старше трёх интервалов обновления считаются устаревшими
# Автообновление опрашивает сервер чаще интервала: кошелёк становится «пора обновить» чуть раньше срока,
# чтобы опрос раз в минуту не откладывал обновление на лишнюю минуту.
DUE_SLACK = timedelta(seconds=30)
ALL_WALLETS = "Все кошельки"


class PortfolioNotFound(LookupError):
    pass


class PortfolioConflict(ValueError):
    pass
WALLET_TIMEOUT_SECONDS = 90
DIRECTORY_MAX_AGE = timedelta(days=1)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _last_attempt(wallet) -> datetime | None:
    times = [t for t in (wallet["fetched_at"], wallet["error_at"]) if t]
    return datetime.fromisoformat(max(times)) if times else None


class Portfolio:
    def __init__(
        self,
        vault: Vault,
        adapters: dict[str, Adapter] | None = None,
        prices: CoinGecko | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.vault = vault
        self.adapters = adapters if adapters is not None else default_adapters()
        self.coingecko = prices or CoinGecko()
        self.transport = transport
        self._refresh_lock = asyncio.Lock()

    # --- ключи провайдеров ---

    def keys(self) -> dict[str, str]:
        with self.vault.db() as conn:
            rows = conn.execute("SELECT key, value FROM settings WHERE key LIKE 'api_key.%'").fetchall()
        return {r["key"].removeprefix("api_key."): r["value"] for r in rows}

    def set_key(self, name: str, value: str | None) -> None:
        with self.vault.db() as conn:
            if value:
                conn.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (f"api_key.{name}", value))
            else:
                conn.execute("DELETE FROM settings WHERE key = ?", (f"api_key.{name}",))

    # --- кэш ---

    def _cache_get(self, key: str) -> tuple[dict | None, str | None]:
        with self.vault.db() as conn:
            row = conn.execute("SELECT value, updated_at FROM cache WHERE key = ?", (key,)).fetchone()
        return (json.loads(row["value"]), row["updated_at"]) if row else (None, None)

    def _cache_put(self, key: str, value: dict) -> None:
        with self.vault.db() as conn:
            conn.execute("INSERT OR REPLACE INTO cache VALUES (?, ?, ?)", (key, json.dumps(value), _now()))

    async def _directory(self, http: Http, key: str | None) -> Directory:
        cached, updated_at = self._cache_get("coingecko.directory")
        fresh = updated_at and datetime.fromisoformat(updated_at) > datetime.now(UTC) - DIRECTORY_MAX_AGE
        if cached and fresh:
            return Directory.from_json(cached)
        try:
            directory = await self.coingecko.directory(http, key)
        except AdapterError as e:
            log.warning("Справочник токенов не обновлён: %s", e)
            return Directory.from_json(cached) if cached else Directory.empty()
        self._cache_put("coingecko.directory", directory.to_json())
        return directory

    # --- обновление ---

    async def refresh(self, portfolio_id: int | None = None, only_due: bool = False) -> dict:
        """Обновляет все нескрытые кошельки: выбранное портфолио влияет только на представление.

        only_due — открытие страницы и автообновление: запрашиваются только кошельки, чьи данные
        старше интервала из настроек. Сколько бы вкладок ни было открыто и как часто бы ни заходили
        на главную, к провайдерам уходит не больше одного обновления за интервал — это бережёт
        квоты вроде Zerion. Кнопка «Обновить» обновляет всё. Параллельные запросы ждут идущее обновление.
        """
        async with self._refresh_lock:
            await self._refresh(only_due)
        return self.view(portfolio_id)

    # --- портфолио ---

    def portfolios(self) -> list[dict]:
        with self.vault.db() as conn:
            rows = conn.execute("SELECT id, name FROM portfolios ORDER BY created_at, id").fetchall()
            members = conn.execute("SELECT portfolio_id, wallet_id FROM portfolio_wallets").fetchall()
        wallet_ids: dict[int, list[int]] = {r["id"]: [] for r in rows}
        for m in members:
            wallet_ids[m["portfolio_id"]].append(m["wallet_id"])
        return [{"id": r["id"], "name": r["name"], "wallet_ids": sorted(wallet_ids[r["id"]])} for r in rows]

    def save_portfolio(self, portfolio_id: int | None, name: str | None, wallet_ids: list[int] | None) -> dict:
        with self.vault.db() as conn:
            if name is not None and name.casefold() == ALL_WALLETS.casefold():
                raise PortfolioConflict(f"Имя «{ALL_WALLETS}» занято встроенным портфолио")
            if portfolio_id is None:
                try:
                    portfolio_id = conn.execute(
                        "INSERT INTO portfolios (name, created_at) VALUES (?, ?)", (name, _now())
                    ).lastrowid
                except sqlcipher3.IntegrityError:
                    raise PortfolioConflict("Портфолио с таким названием уже есть") from None
            else:
                if conn.execute("SELECT 1 FROM portfolios WHERE id = ?", (portfolio_id,)).fetchone() is None:
                    raise PortfolioNotFound
                if name is not None:
                    try:
                        conn.execute("UPDATE portfolios SET name = ? WHERE id = ?", (name, portfolio_id))
                    except sqlcipher3.IntegrityError:
                        raise PortfolioConflict("Портфолио с таким названием уже есть") from None
            if wallet_ids is not None:
                known = {r["id"] for r in conn.execute("SELECT id FROM wallets").fetchall()}
                unknown = set(wallet_ids) - known
                if unknown:
                    raise PortfolioConflict("Некоторых кошельков уже нет — обновите страницу")
                conn.execute("DELETE FROM portfolio_wallets WHERE portfolio_id = ?", (portfolio_id,))
                conn.executemany(
                    "INSERT INTO portfolio_wallets VALUES (?, ?)", [(portfolio_id, w) for w in set(wallet_ids)]
                )
        return next(p for p in self.portfolios() if p["id"] == portfolio_id)

    def delete_portfolio(self, portfolio_id: int) -> None:
        with self.vault.db() as conn:
            if conn.execute("DELETE FROM portfolios WHERE id = ?", (portfolio_id,)).rowcount == 0:
                raise PortfolioNotFound

    async def _refresh(self, only_due: bool) -> None:
        keys = self.keys()
        with self.vault.db() as conn:
            wallets = conn.execute(
                "SELECT w.id, w.network, w.address, s.data, s.fetched_at, s.error, s.error_at FROM wallets w"
                " LEFT JOIN wallet_state s ON s.wallet_id = w.id WHERE w.hidden = 0"
            ).fetchall()
        targets = [w for w in wallets if w["network"] in self.adapters]
        skipped = []
        if only_due:
            due_before = datetime.now(UTC) - timedelta(minutes=self.settings()["refresh_interval_minutes"]) + DUE_SLACK
            # Последняя попытка — успешная или нет: упавший источник тоже не дёргаем чаще интервала.
            due = [w for w in targets if _last_attempt(w) is None or _last_attempt(w) < due_before]
            due_ids = {w["id"] for w in due}
            skipped = [w for w in targets if w["id"] not in due_ids]
            targets = due
            prices, _ = self._cache_get("prices")
            if not targets and prices and datetime.fromisoformat(prices["at"]) >= due_before:
                return
        async with new_client(self.transport) as client:
            http = Http(client)
            directory = await self._directory(http, keys.get("coingecko"))
            meta, _ = self._cache_get("token.meta")
            ctx = Context(http, keys, directory, meta or {})

            results = await asyncio.gather(*(self._fetch_wallet(w, ctx) for w in targets))
            self._store_results(targets, results)
            self._cache_put("token.meta", ctx.meta)

            ids = {h["coingecko_id"] for state in self._states().values() for h in state["holdings"] if h["coingecko_id"]}
            prices_ok = False
            try:
                usd, rub = await self.coingecko.prices(http, keys.get("coingecko"), ids)
            except AdapterError as e:
                log.warning("Цены не обновлены: %s", e)
                self._cache_put("prices.error", {"error": str(e), "at": _now()})
            else:
                self._cache_put(
                    "prices",
                    {"usd": {k: str(v) for k, v in usd.items()}, "rub": str(rub) if rub else None, "at": _now()},
                )
                self._cache_put("prices.error", {})
                prices_ok = True
        self._cache_put("refresh", {"at": _now()})

        # Снапшот — только после полного успешного обновления: все источники и цены ответили.
        # Пропущенные кошельки недавно опрашивались, но их последний результат мог быть ошибкой
        # или прийти с предупреждением.
        complete = (
            prices_ok
            and all(isinstance(r, Balance) and not r.warning for r in results)
            and all(w["error"] is None and w["data"] and not json.loads(w["data"]).get("warning") for w in skipped)
        )
        if complete:
            snapshots.take_if_due(self)

    async def _fetch_wallet(self, wallet, ctx: Context) -> Balance | str:
        adapter = self.adapters[wallet["network"]]
        try:
            return await asyncio.wait_for(adapter.fetch(wallet["address"], ctx), WALLET_TIMEOUT_SECONDS)
        except AdapterError as e:
            return str(e)
        except TimeoutError:
            return "источник не ответил вовремя"
        except Exception as e:  # неожиданный формат ответа и т. п. — не роняем остальные кошельки
            log.exception("Адаптер %s упал", wallet["network"])
            return f"ошибка обработки ответа ({type(e).__name__})"

    def _store_results(self, wallets, results) -> None:
        now = _now()
        with self.vault.db() as conn:
            for wallet, result in zip(wallets, results):
                if isinstance(result, Balance):
                    conn.execute(
                        "INSERT INTO wallet_state (wallet_id, data, fetched_at) VALUES (?, ?, ?)"
                        " ON CONFLICT (wallet_id) DO UPDATE SET data = excluded.data,"
                        " fetched_at = excluded.fetched_at, error = NULL, error_at = NULL",
                        (wallet["id"], json.dumps(result.to_json()), now),
                    )
                else:
                    conn.execute(
                        "INSERT INTO wallet_state (wallet_id, error, error_at) VALUES (?, ?, ?)"
                        " ON CONFLICT (wallet_id) DO UPDATE SET error = excluded.error, error_at = excluded.error_at",
                        (wallet["id"], result, now),
                    )

    def _states(self) -> dict[int, dict]:
        with self.vault.db() as conn:
            rows = conn.execute("SELECT wallet_id, data FROM wallet_state WHERE data IS NOT NULL").fetchall()
        return {r["wallet_id"]: json.loads(r["data"]) for r in rows}

    # --- общие настройки ---

    def settings(self) -> dict:
        with self.vault.db() as conn:
            rows = conn.execute("SELECT key, value FROM settings WHERE key IN (?, ?)", tuple(SETTINGS)).fetchall()
        stored = {r["key"]: float(r["value"]) for r in rows}
        return {key: type(default)(stored.get(key, default)) for key, default in SETTINGS.items()}

    def update_settings(self, **values: float | None) -> dict:
        with self.vault.db() as conn:
            for key, value in values.items():
                if key in SETTINGS and value is not None:
                    conn.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, str(value)))
        return self.settings()

    # --- представление ---

    def view(self, portfolio_id: int | None = None) -> dict:
        """Итог по портфолио; None — встроенное «Все кошельки» (все нескрытые)."""
        portfolio_name = ALL_WALLETS
        if portfolio_id is not None:
            with self.vault.db() as conn:
                row = conn.execute("SELECT name FROM portfolios WHERE id = ?", (portfolio_id,)).fetchone()
            if row is None:
                raise PortfolioNotFound
            portfolio_name = row["name"]
        prices, _ = self._cache_get("prices")
        prices_error, _ = self._cache_get("prices.error")
        usd = {k: Decimal(v) for k, v in (prices or {}).get("usd", {}).items()}
        rub_rate = Decimal(prices["rub"]) if prices and prices.get("rub") else None
        settings = self.settings()
        dust = Decimal(str(settings["dust_threshold_usd"]))
        stale_before = datetime.now(UTC) - timedelta(
            minutes=settings["refresh_interval_minutes"] * STALE_AFTER_INTERVALS
        )

        with self.vault.db() as conn:
            rows = conn.execute(
                "SELECT w.id, w.name, w.network, w.address, s.data, s.fetched_at, s.error, s.error_at"
                " FROM wallets w LEFT JOIN wallet_state s ON s.wallet_id = w.id"
                " WHERE w.hidden = 0"
                " AND (? IS NULL OR w.id IN (SELECT wallet_id FROM portfolio_wallets WHERE portfolio_id = ?))"
                " ORDER BY w.created_at, w.id",
                (portfolio_id, portfolio_id),
            ).fetchall()

        total = Decimal(0)
        assets: dict[str, dict] = {}
        wallets = []
        for r in rows:
            wallet = {
                "id": r["id"],
                "name": r["name"],
                "network": r["network"],
                "address": r["address"],
                "fetched_at": r["fetched_at"],
                "error": r["error"],
                "warning": None,
                "details": None,
                "holdings": [],
                "hidden_tokens": 0,
                "dust_count": 0,
                "dust_usd": 0.0,
                "value_usd": None,
            }
            if r["data"] is None:
                wallet["status"] = "error" if r["error"] else "pending"
            elif r["error"] or datetime.fromisoformat(r["fetched_at"]) < stale_before:
                # Устарело: источник не ответил или данные давно не обновлялись.
                wallet["status"] = "stale"
            else:
                wallet["status"] = "fresh"
            if r["data"] is not None:
                balance = Balance.from_json(json.loads(r["data"]))
                wallet["hidden_tokens"] = balance.hidden_tokens
                wallet["warning"] = balance.warning
                wallet["details"] = balance.details
                value = Decimal(0)
                dust_value = Decimal(0)
                for h in balance.holdings:
                    price = usd.get(h.coingecko_id) if h.coingecko_id else None
                    if price is None:
                        price = h.price_usd
                    # Долг уменьшает стоимость: позиция с займом считается по чистой стоимости.
                    sign = -1 if h.kind == "loan" else 1
                    h_value = sign * h.amount * price if price is not None else None
                    if h_value is not None and abs(h_value) < dust:
                        wallet["dust_count"] += 1
                        dust_value += h_value
                        continue
                    if h_value is not None:
                        value += h_value
                    wallet["holdings"].append(
                        {
                            "symbol": h.symbol,
                            "name": h.name,
                            "coingecko_id": h.coingecko_id,
                            "amount": format_amount(h.amount),
                            "kind": h.kind,
                            "protocol": h.protocol,
                            "chain": h.chain,
                            "price_usd": float(price) if price is not None else None,
                            "value_usd": float(h_value) if h_value is not None else None,
                        }
                    )
                    if h_value is None:
                        continue
                    asset = assets.setdefault(
                        h.symbol, {"symbol": h.symbol, "name": h.name, "amount": Decimal(0), "value_usd": Decimal(0)}
                    )
                    asset["amount"] += sign * h.amount
                    asset["value_usd"] += h_value
                wallet["holdings"].sort(key=lambda h: (h["kind"] != "free", -(h["value_usd"] or 0)))
                wallet["dust_usd"] = float(dust_value)
                wallet["value_usd"] = float(value)
                total += value
            wallets.append(wallet)

        problems = [w for w in wallets if w["status"] in ("stale", "error") or w["warning"]]
        last_refresh, _ = self._cache_get("refresh")
        return {
            "portfolio": {"id": portfolio_id, "name": portfolio_name},
            "total_usd": float(total),
            "total_rub": float(total * rub_rate) if rub_rate else None,
            "rub_rate": float(rub_rate) if rub_rate else None,
            "updated_at": (last_refresh or {}).get("at"),
            "prices_at": prices.get("at") if prices else None,
            "prices_error": (prices_error or {}).get("error"),
            "incomplete": bool(problems) or bool((prices_error or {}).get("error")),
            "dust_threshold_usd": float(dust),
            "wallets": wallets,
            "assets": [
                {**a, "amount": format_amount(a["amount"]), "value_usd": float(a["value_usd"])}
                for a in sorted(assets.values(), key=lambda a: a["value_usd"], reverse=True)
            ],
        }
