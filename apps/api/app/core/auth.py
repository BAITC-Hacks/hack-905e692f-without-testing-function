"""SQLite users and revocable opaque sessions; passwords use salted scrypt."""

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import HTTPException

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
        "CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, name TEXT NOT NULL, created_at TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, user_id INTEGER, email TEXT NOT NULL, demo INTEGER NOT NULL, expires_at INTEGER NOT NULL)"
    )
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return f"scrypt${salt}${digest}"


def issue(email, user_id=None, demo=False):
    token = secrets.token_urlsafe(32)
    with connect() as db:
        db.execute("DELETE FROM sessions WHERE expires_at < ?", (int(time.time()),))
        db.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?, ?)",
            (
                hashlib.sha256(token.encode()).hexdigest(),
                user_id,
                email,
                int(demo),
                int(time.time()) + 28800,
            ),
        )
    return {"access_token": token, "email": email, "role": "analyst", "demo": demo}


def credentials(payload):
    email, password = payload.get("email", ""), payload.get("password", "")
    if not isinstance(email, str) or not isinstance(password, str):
        raise HTTPException(400, "Некорректные данные")
    email = email.strip().lower()
    if (
        not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email)
        or len(email) > 254
        or not 8 <= len(password) <= 128
    ):
        raise HTTPException(400, "Укажите email и пароль длиной 8–128 символов")
    return email, password


def register_user(payload):
    email, password = credentials(payload)
    name = payload.get("name", "")
    if not isinstance(name, str) or not name.strip() or len(name) > 100:
        raise HTTPException(400, "Укажите имя (до 100 символов)")
    hashed = password_hash(password)
    try:
        with connect() as db:
            cursor = db.execute(
                "INSERT INTO users (email,password_hash,name,created_at) VALUES (?,?,?,?)",
                (email, hashed, name.strip(), datetime.now(UTC).isoformat()),
            )
            user_id = cursor.lastrowid
    except sqlite3.IntegrityError as error:
        raise HTTPException(409, "Email уже зарегистрирован") from error
    return issue(email, user_id)


def login_user(payload):
    email, password = credentials(payload)
    with connect() as db:
        user = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    stored = user["password_hash"] if user else password_hash("dummy-password")
    if not hmac.compare_digest(password_hash(password, stored.split("$")[1]), stored) or not user:
        raise HTTPException(401, "Неверный email или пароль")
    return issue(email, user["id"])


def session(authorization):
    token = (authorization or "").removeprefix("Bearer ")
    with connect() as db:
        row = db.execute(
            "SELECT * FROM sessions WHERE token_hash=? AND expires_at>?",
            (hashlib.sha256(token.encode()).hexdigest(), int(time.time())),
        ).fetchone()
    if not row:
        raise HTTPException(401, "Войдите в систему")
    return {"email": row["email"], "demo": bool(row["demo"]), "expires_at": row["expires_at"]}


def revoke(authorization):
    session(authorization)
    with connect() as db:
        db.execute(
            "DELETE FROM sessions WHERE token_hash=?",
            (hashlib.sha256(authorization.removeprefix("Bearer ").encode()).hexdigest(),),
        )
