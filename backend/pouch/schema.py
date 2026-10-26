"""Миграции схемы. Версия хранится в PRAGMA user_version, миграции только добавляются."""

MIGRATIONS = [
    """
    CREATE TABLE wallets (
        id          INTEGER PRIMARY KEY,
        name        TEXT NOT NULL,
        network     TEXT NOT NULL,
        address     TEXT NOT NULL,
        address_key TEXT NOT NULL,
        note        TEXT NOT NULL DEFAULT '',
        hidden      INTEGER NOT NULL DEFAULT 0,
        created_at  TEXT NOT NULL,
        UNIQUE (network, address_key)
    );
    CREATE TABLE settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """,
    """
    -- Последний известный результат по кошельку: при ошибке источника показываем его как «устарело».
    CREATE TABLE wallet_state (
        wallet_id  INTEGER PRIMARY KEY REFERENCES wallets(id) ON DELETE CASCADE,
        data       TEXT,
        fetched_at TEXT,
        error      TEXT,
        error_at   TEXT
    );
    -- Кэш внешних данных: справочник токенов CoinGecko, метаданные токенов, последние цены.
    CREATE TABLE cache (
        key        TEXT PRIMARY KEY,
        value      TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
    """
    -- Портфолио — именованный набор кошельков. Встроенное «Все кошельки» не хранится.
    -- Состав не зависит от скрытия: скрытый кошелёк остаётся в наборе и вернётся в итоги.
    CREATE TABLE portfolios (
        id         INTEGER PRIMARY KEY,
        name       TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL
    );
    CREATE TABLE portfolio_wallets (
        portfolio_id INTEGER NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
        wallet_id    INTEGER NOT NULL REFERENCES wallets(id) ON DELETE CASCADE,
        PRIMARY KEY (portfolio_id, wallet_id)
    );
    """,
    """
    -- Снапшоты для будущих графиков. Привязаны к ID кошелька, а не к названию: переименование
    -- не ломает историю, а удаление кошелька не стирает прошлые итоги (поэтому без внешнего ключа).
    CREATE TABLE snapshots (
        id        INTEGER PRIMARY KEY,
        taken_at  TEXT NOT NULL,
        total_usd TEXT NOT NULL,
        usd_rub   TEXT
    );
    CREATE TABLE snapshot_items (
        snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
        wallet_id   INTEGER NOT NULL,
        network     TEXT NOT NULL,
        symbol      TEXT NOT NULL,
        kind        TEXT NOT NULL,
        protocol    TEXT,
        chain       TEXT,
        amount      TEXT NOT NULL,
        price_usd   TEXT,
        value_usd   TEXT
    );
    CREATE INDEX snapshot_items_by_snapshot ON snapshot_items(snapshot_id);
    CREATE TABLE snapshot_portfolios (
        snapshot_id  INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
        portfolio_id INTEGER,
        name         TEXT NOT NULL,
        total_usd    TEXT NOT NULL
    );
    CREATE TABLE snapshot_hl_accounts (
        snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
        wallet_id   INTEGER NOT NULL,
        address     TEXT NOT NULL,
        label       TEXT NOT NULL,
        mode        TEXT,
        perp_value  TEXT,
        total_usd   TEXT
    );
    """,
]


def migrate(conn) -> None:
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for version, sql in enumerate(MIGRATIONS[current:], start=current + 1):
        conn.executescript(f"BEGIN; {sql}; PRAGMA user_version = {version}; COMMIT;")
