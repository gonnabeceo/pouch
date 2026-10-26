import sqlite3

import pytest

from pouch import vault as v

PASSWORD = "correct horse battery"


@pytest.fixture
def vault(tmp_path):
    return v.Vault(tmp_path, autolock_seconds=900)


def test_setup_and_reopen(vault):
    vault.setup(PASSWORD)
    with vault.db() as conn:
        conn.execute("INSERT INTO settings VALUES ('k', 'v')")
    vault.lock()
    assert not vault.unlocked
    with pytest.raises(v.Locked):
        with vault.db():
            pass
    vault.unlock(PASSWORD)
    with vault.db() as conn:
        assert conn.execute("SELECT value FROM settings").fetchone()[0] == "v"


def test_file_is_encrypted(vault):
    vault.setup(PASSWORD)
    with vault.db() as conn:
        conn.execute("INSERT INTO settings VALUES ('secret', 'bc1q-marker')")
    vault.lock()
    data = vault.db_path.read_bytes()
    assert not data.startswith(b"SQLite format 3")
    assert b"bc1q-marker" not in data
    with pytest.raises(sqlite3.DatabaseError):
        sqlite3.connect(vault.db_path).execute("SELECT * FROM sqlite_master").fetchall()


def test_password_not_stored(vault):
    vault.setup(PASSWORD)
    vault.lock()
    for path in vault.data_dir.iterdir():
        assert PASSWORD.encode() not in path.read_bytes()


def test_wrong_password(vault):
    vault.setup(PASSWORD)
    vault.lock()
    with pytest.raises(v.WrongPassword):
        vault.unlock("not the password")
    assert not vault.unlocked


def test_backoff_after_failures(vault):
    vault.setup(PASSWORD)
    vault.lock()
    for _ in range(v.FREE_ATTEMPTS):
        with pytest.raises(v.WrongPassword):
            vault.unlock("nope-nope")
    with pytest.raises(v.TooManyAttempts):
        vault.unlock(PASSWORD)


def test_setup_rules(vault):
    with pytest.raises(v.WeakPassword):
        vault.setup("short")
    with pytest.raises(v.NotInitialized):
        vault.unlock(PASSWORD)
    vault.setup(PASSWORD)
    with pytest.raises(v.AlreadyInitialized):
        vault.setup(PASSWORD)


def test_missing_db_is_not_recreated(vault):
    vault.setup(PASSWORD)
    vault.lock()
    vault.db_path.unlink()
    with pytest.raises(v.VaultError):
        vault.unlock("any password at all")
    assert not vault.db_path.exists()


def test_autolock(vault, monkeypatch):
    token = vault.setup(PASSWORD)
    assert vault.check_session(token)
    assert not vault.check_autolock()
    now = v.time.monotonic()
    monkeypatch.setattr(v.time, "monotonic", lambda: now + 901)
    assert vault.check_autolock()
    assert not vault.unlocked
    assert not vault.check_session(token)


NEW = "a whole new passphrase"


def test_change_password(vault):
    vault.setup(PASSWORD)
    with vault.db() as conn:
        conn.execute("INSERT INTO settings VALUES ('k', 'v')")
    with pytest.raises(v.WrongPassword):
        vault.change_password("not it at all", NEW)
    with pytest.raises(v.WeakPassword):
        vault.change_password(PASSWORD, "short")
    vault.change_password(PASSWORD, NEW)
    assert not vault.pending_meta_path.exists()
    vault.lock()
    with pytest.raises(v.WrongPassword):
        vault.unlock(PASSWORD)
    vault.unlock(NEW)
    with vault.db() as conn:
        assert conn.execute("SELECT value FROM settings").fetchone()[0] == "v"


def test_change_interrupted_after_rekey(vault, monkeypatch):
    vault.setup(PASSWORD)
    real_replace = v.os.replace

    def crash_on_final_swap(src, dst):
        if str(src).endswith("pending.json"):
            raise RuntimeError("сбой питания")
        return real_replace(src, dst)

    monkeypatch.setattr(v.os, "replace", crash_on_final_swap)
    with pytest.raises(RuntimeError):
        vault.change_password(PASSWORD, NEW)
    monkeypatch.setattr(v.os, "replace", real_replace)
    vault.lock()
    assert vault.pending_meta_path.exists()
    # База уже на новом ключе: открывается новым паролем, параметры подхватываются из pending.
    vault.unlock(NEW)
    assert not vault.pending_meta_path.exists()
    vault.lock()
    vault.unlock(NEW)


def test_change_interrupted_before_rekey(vault):
    vault.setup(PASSWORD)
    vault._write_meta(vault._new_meta(), vault.pending_meta_path)  # записали pending и упали до rekey
    vault.lock()
    vault.unlock(PASSWORD)
    assert not vault.pending_meta_path.exists()
