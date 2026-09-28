"""Environment-configured admin login and revocable opaque sessions."""

import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from fastapi import HTTPException

try:
    from argon2 import PasswordHasher
    from argon2.exceptions import InvalidHashError, VerifyMismatchError
except ImportError:  # local upgrade compatibility; production dependency is mandatory
    PasswordHasher = None
    InvalidHashError = VerifyMismatchError = ValueError

DB = Path(
    os.environ.get(
        "MEDFLOW_DB", str(Path(__file__).resolve().parents[4] / "data/processed/auth.sqlite3")
    )
)


@contextmanager
def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute(
        "CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, user_id INTEGER, email TEXT NOT NULL, demo INTEGER NOT NULL, expires_at INTEGER NOT NULL, role TEXT NOT NULL DEFAULT 'viewer')"
    )
    columns = {row[1] for row in connection.execute("PRAGMA table_info(sessions)")}
    if "role" not in columns:
        connection.execute("ALTER TABLE sessions ADD COLUMN role TEXT NOT NULL DEFAULT 'viewer'")
    connection.execute(
        "CREATE TABLE IF NOT EXISTS login_attempts (client_key TEXT PRIMARY KEY, failures INTEGER NOT NULL, window_started INTEGER NOT NULL, blocked_until INTEGER NOT NULL)"
    )
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def password_hash(password: str, salt: str | None = None) -> str:
    """Create Argon2id hashes; retain deterministic scrypt only for legacy tests/migration."""
    if salt is None and PasswordHasher is not None:
        return PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2).hash(password)
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return f"scrypt${salt}${digest}"


def issue(email, user_id=None, demo=False, role="viewer"):
    if role not in {"viewer", "analyst", "admin"}:
        raise ValueError("Invalid role")
    token = secrets.token_urlsafe(32)
    with connect() as db:
        db.execute("DELETE FROM sessions WHERE expires_at < ?", (int(time.time()),))
        db.execute(
            "INSERT INTO sessions(token_hash,user_id,email,demo,expires_at,role) VALUES (?, ?, ?, ?, ?, ?)",
            (
                hashlib.sha256(token.encode()).hexdigest(),
                user_id,
                email,
                int(demo),
                int(time.time()) + 28800,
                role,
            ),
        )
    return {
        "access_token": token,
        "email": email,
        "role": role,
        "demo": demo,
    }


def _credentials(payload: dict) -> tuple[str, str]:
    email, password = payload.get("email", ""), payload.get("password", "")
    if not isinstance(email, str) or not isinstance(password, str):
        raise HTTPException(401, "Неверный email или пароль")
    email = email.strip().lower()
    if not email or len(email) > 254 or not 8 <= len(password) <= 128:
        raise HTTPException(401, "Неверный email или пароль")
    return email, password


def _admin_credentials() -> tuple[str, str]:
    email = os.environ.get("ADMIN_EMAIL", "").strip().lower()
    stored_hash = os.environ.get("ADMIN_PASSWORD_HASH", "")
    if not email or not stored_hash.startswith(("$argon2id$", "scrypt$")):
        raise HTTPException(503, "Администратор не настроен")
    return email, stored_hash


def _rate_limit(client_key: str, success: bool | None = None) -> None:
    now = int(time.time())
    with connect() as db:
        row = db.execute("SELECT * FROM login_attempts WHERE client_key=?", (client_key,)).fetchone()
        if row and row["blocked_until"] > now:
            raise HTTPException(429, "Слишком много попыток. Повторите позже")
        if success is True:
            db.execute("DELETE FROM login_attempts WHERE client_key=?", (client_key,))
        elif success is False:
            failures = int(row["failures"]) + 1 if row and now - int(row["window_started"]) < 900 else 1
            started = int(row["window_started"]) if row and now - int(row["window_started"]) < 900 else now
            blocked = now + 900 if failures >= 5 else 0
            db.execute(
                "INSERT INTO login_attempts VALUES(?,?,?,?) ON CONFLICT(client_key) DO UPDATE SET failures=excluded.failures,window_started=excluded.window_started,blocked_until=excluded.blocked_until",
                (client_key, failures, started, blocked),
            )


def login_user(payload, client_key: str = "local"):
    _rate_limit(client_key)
    email, password = _credentials(payload)
    admin_email, stored_hash = _admin_credentials()
    try:
        if stored_hash.startswith("$argon2id$"):
            if PasswordHasher is None:
                raise HTTPException(503, "Администратор не настроен")
            password_matches = PasswordHasher().verify(stored_hash, password)
        else:
            _, salt, _ = stored_hash.split("$", 2)
            password_matches = hmac.compare_digest(password_hash(password, salt), stored_hash)
    except (TypeError, ValueError, InvalidHashError, VerifyMismatchError):
        password_matches = False
    except HTTPException:
        raise
    if not stored_hash:
        raise HTTPException(503, "Администратор не настроен") from None
    if not hmac.compare_digest(email, admin_email) or not password_matches:
        _rate_limit(client_key, success=False)
        raise HTTPException(401, "Неверный email или пароль")
    _rate_limit(client_key, success=True)
    return issue(admin_email, role="admin")


def session(authorization):
    token = (authorization or "").removeprefix("Bearer ")
    with connect() as db:
        row = db.execute(
            "SELECT * FROM sessions WHERE token_hash=? AND expires_at>?",
            (hashlib.sha256(token.encode()).hexdigest(), int(time.time())),
        ).fetchone()
    if not row:
        raise HTTPException(401, "Войдите в систему")
    return {"email": row["email"], "role": row["role"], "demo": bool(row["demo"]), "expires_at": row["expires_at"]}


def require_role(authorization: str | None, allowed: set[str]) -> dict:
    user = session(authorization)
    if user["role"] not in allowed:
        raise HTTPException(403, "Недостаточно прав")
    return user


def revoke(authorization):
    session(authorization)
    with connect() as db:
        db.execute(
            "DELETE FROM sessions WHERE token_hash=?",
            (hashlib.sha256(authorization.removeprefix("Bearer ").encode()).hexdigest(),),
        )
