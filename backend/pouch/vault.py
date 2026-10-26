"""Зашифрованное хранилище: SQLCipher, ключ выводится из мастер-пароля через Argon2id.

Пароль нигде не сохраняется. На диске лежат только зашифрованная база и
метаданные KDF (соль и параметры), по которым ключ нельзя восстановить без пароля.
Ключ живёт в открытом соединении SQLCipher и стирается при блокировке.
"""

import base64
import json
import logging
import os
import secrets
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import sqlcipher3
from argon2.low_level import Type, hash_secret_raw

from . import schema

log = logging.getLogger("pouch")

MIN_PASSWORD_LENGTH = 8
KDF_DEFAULTS = {"t": 3, "m": 64 * 1024, "p": 4}  # 3 прохода, 64 МиБ, 4 потока
FREE_ATTEMPTS = 5
MAX_BACKOFF_SECONDS = 15 * 60


class VaultError(Exception):
    pass


class WrongPassword(VaultError):
    pass


class WeakPassword(VaultError):
    pass


class AlreadyInitialized(VaultError):
    pass


class NotInitialized(VaultError):
    pass


class Locked(VaultError):
    pass


class TooManyAttempts(VaultError):
    def __init__(self, retry_after: int):
        super().__init__(retry_after)
        self.retry_after = retry_after


def derive_key(password: str, salt: bytes, t: int, m: int, p: int) -> bytearray:
    return bytearray(hash_secret_raw(password.encode(), salt, t, m, p, 32, Type.ID))


def _wipe(buf: bytearray) -> None:
    for i in range(len(buf)):
        buf[i] = 0


class Vault:
    def __init__(self, data_dir: Path, autolock_seconds: int):
        self.data_dir = Path(data_dir)
        self.db_path = self.data_dir / "pouch.db"
        self.meta_path = self.data_dir / "pouch.meta.json"
        self.pending_meta_path = self.data_dir / "pouch.meta.pending.json"
        self.autolock_seconds = autolock_seconds
        self._conn = None
        self._lock = threading.RLock()
        self._session: str | None = None
        self._last_activity = 0.0
        self._failures = 0
        self._blocked_until = 0.0

    @property
    def initialized(self) -> bool:
        return self.meta_path.exists()

    @property
    def unlocked(self) -> bool:
        return self._conn is not None

    # --- жизненный цикл ---

    def setup(self, password: str) -> str:
        with self._lock:
            if self.initialized:
                raise AlreadyInitialized
            if self.db_path.exists():
                # База без метаданных KDF не открывается; не перезаписываем её молча.
                raise VaultError(f"Найден {self.db_path.name} без {self.meta_path.name}")
            if len(password) < MIN_PASSWORD_LENGTH:
                raise WeakPassword
            self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            meta = self._new_meta()
            key = self._derive(password, meta)
            try:
                conn = self._open(key)
            finally:
                _wipe(key)
            os.chmod(self.db_path, 0o600)
            self._write_meta(meta)
            self._conn = conn
            return self._start_session()

    def unlock(self, password: str) -> str:
        with self._lock:
            if not self.initialized:
                raise NotInitialized
            if not self.db_path.exists():
                # Иначе SQLCipher создал бы пустую базу с любым паролем.
                raise VaultError(f"Файл {self.db_path.name} не найден")
            now = time.monotonic()
            if now < self._blocked_until:
                raise TooManyAttempts(int(self._blocked_until - now) + 1)
            if self._conn is not None:
                # Уже открыто другой вкладкой: пароль всё равно проверяем заново.
                self.lock("повторный вход")
            try:
                conn = self._open_with_password(password)
            except WrongPassword:
                self._failures += 1
                if self._failures >= FREE_ATTEMPTS:
                    backoff = min(30 * 2 ** (self._failures - FREE_ATTEMPTS), MAX_BACKOFF_SECONDS)
                    self._blocked_until = now + backoff
                raise
            self._failures = 0
            self._conn = conn
            return self._start_session()

    def verify_password(self, password: str) -> None:
        """Повторная проверка пароля для экспорта: с той же паузой после неудачных попыток."""
        with self._lock:
            if self._conn is None:
                raise Locked
            now = time.monotonic()
            if now < self._blocked_until:
                raise TooManyAttempts(int(self._blocked_until - now) + 1)
            try:
                self._open_with_password(password).close()
            except WrongPassword:
                self._failures += 1
                if self._failures >= FREE_ATTEMPTS:
                    backoff = min(30 * 2 ** (self._failures - FREE_ATTEMPTS), MAX_BACKOFF_SECONDS)
                    self._blocked_until = now + backoff
                raise
            self._failures = 0

    def backup_files(self) -> dict[str, bytes]:
        """Зашифрованная база и параметры KDF как есть. Под блокировкой записи нет — копия согласована."""
        with self._lock:
            if self._conn is None:
                raise Locked
            return {self.db_path.name: self.db_path.read_bytes(), self.meta_path.name: self.meta_path.read_bytes()}

    def change_password(self, old: str, new: str) -> None:
        with self._lock:
            if self._conn is None:
                raise Locked
            if len(new) < MIN_PASSWORD_LENGTH:
                raise WeakPassword
            self._open_with_password(old).close()  # проверка старого пароля отдельным соединением
            meta = self._new_meta()
            key = self._derive(new, meta)
            try:
                # Сначала новые параметры KDF рядом, потом rekey, потом подмена. Если процесс
                # упадёт посередине, _open_with_password найдёт тот вариант, что подходит к базе.
                self._write_meta(meta, self.pending_meta_path)
                self._conn.execute(f"PRAGMA rekey = \"x'{key.hex()}'\"")
            finally:
                _wipe(key)
            os.replace(self.pending_meta_path, self.meta_path)
            log.info("Мастер-пароль изменён")

    def lock(self, reason: str = "вручную") -> None:
        with self._lock:
            if self._conn is not None:
                log.info("База заблокирована: %s", reason)
                self._conn.close()  # SQLCipher обнуляет ключ в памяти при закрытии
                self._conn = None
            self._session = None

    # --- сессия и бездействие ---

    def check_session(self, token: str | None) -> bool:
        session = self._session
        return bool(token and session and self.unlocked and secrets.compare_digest(token, session))

    def touch(self) -> None:
        self._last_activity = time.monotonic()

    def check_autolock(self) -> bool:
        with self._lock:
            if self.unlocked and time.monotonic() - self._last_activity > self.autolock_seconds:
                self.lock("бездействие")
                return True
            return False

    @contextmanager
    def db(self):
        with self._lock:
            if self._conn is None:
                raise Locked
            try:
                yield self._conn
                self._conn.commit()
            except BaseException:
                self._conn.rollback()
                raise

    # --- внутреннее ---

    def _start_session(self) -> str:
        self._session = secrets.token_urlsafe(32)
        log.info("Открыта новая сессия")
        self.touch()
        return self._session

    @staticmethod
    def _derive(password: str, meta: dict) -> bytearray:
        if meta.get("kdf") != "argon2id":
            raise VaultError(f"Неизвестный KDF: {meta.get('kdf')}")
        return derive_key(password, base64.b64decode(meta["salt"]), meta["t"], meta["m"], meta["p"])

    def _open(self, key: bytearray):
        conn = sqlcipher3.connect(str(self.db_path), check_same_thread=False)
        try:
            # Сырой 256-битный ключ: SQLCipher не применяет свой PBKDF2 поверх Argon2id.
            conn.execute(f"PRAGMA key = \"x'{key.hex()}'\"")
            conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        except sqlcipher3.DatabaseError:
            conn.close()
            raise WrongPassword from None
        conn.row_factory = sqlcipher3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        schema.migrate(conn)
        return conn

    @staticmethod
    def _new_meta() -> dict:
        return {"version": 1, "kdf": "argon2id", "salt": base64.b64encode(secrets.token_bytes(16)).decode(), **KDF_DEFAULTS}

    def _open_with_password(self, password: str):
        candidates = [self.meta_path]
        if self.pending_meta_path.exists():
            candidates.append(self.pending_meta_path)
        for path in candidates:
            key = self._derive(password, json.loads(path.read_text()))
            try:
                conn = self._open(key)
            except WrongPassword:
                continue
            finally:
                _wipe(key)
            if path == self.pending_meta_path:
                os.replace(self.pending_meta_path, self.meta_path)  # смена пароля прервалась после rekey
            elif self.pending_meta_path.exists():
                self.pending_meta_path.unlink()  # прервалась до rekey
            return conn
        raise WrongPassword

    def _write_meta(self, meta: dict, path: Path | None = None) -> None:
        path = path or self.meta_path
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(meta, indent=2))
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
