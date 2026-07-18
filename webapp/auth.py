"""Autenticazione a sessione + gestione account (spec UI Pilze).

Login obbligatorio (le coordinate dei posti sono dati sensibili, §6.1). Account creati
solo da un admin. Password hashate (pbkdf2), sessioni con token in cookie. SQLite.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "pilze_users.db"


def _connect(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY, pw_hash TEXT NOT NULL, salt TEXT NOT NULL,
            is_admin INTEGER NOT NULL DEFAULT 0, created TEXT);
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY, username TEXT NOT NULL, created TEXT);
    """)
    return conn


def _hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000).hex()


def create_user(username: str, password: str, is_admin: bool = False,
                db_path: Path | str = DB_PATH) -> bool:
    conn = _connect(db_path)
    if conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
        conn.close(); return False
    salt = secrets.token_hex(16)
    conn.execute("INSERT INTO users VALUES (?,?,?,?,?)",
                 (username, _hash(password, salt), salt, int(is_admin),
                  datetime.now(timezone.utc).isoformat(timespec="seconds")))
    conn.commit(); conn.close(); return True


def revoke_user(username: str, db_path: Path | str = DB_PATH) -> None:
    conn = _connect(db_path)
    conn.execute("DELETE FROM users WHERE username=?", (username,))
    conn.execute("DELETE FROM sessions WHERE username=?", (username,))
    conn.commit(); conn.close()


def list_users(db_path: Path | str = DB_PATH) -> list[dict]:
    conn = _connect(db_path)
    rows = conn.execute("SELECT username, is_admin, created FROM users ORDER BY username").fetchall()
    conn.close(); return [dict(r) for r in rows]


def verify(username: str, password: str, db_path: Path | str = DB_PATH) -> bool:
    conn = _connect(db_path)
    row = conn.execute("SELECT pw_hash, salt FROM users WHERE username=?", (username,)).fetchone()
    conn.close()
    return bool(row) and secrets.compare_digest(row["pw_hash"], _hash(password, row["salt"]))


def open_session(username: str, db_path: Path | str = DB_PATH) -> str:
    token = secrets.token_urlsafe(32)
    conn = _connect(db_path)
    conn.execute("INSERT INTO sessions VALUES (?,?,?)",
                 (token, username, datetime.now(timezone.utc).isoformat(timespec="seconds")))
    conn.commit(); conn.close(); return token


def session_user(token: str | None, db_path: Path | str = DB_PATH) -> dict | None:
    if not token:
        return None
    conn = _connect(db_path)
    row = conn.execute(
        "SELECT u.username, u.is_admin FROM sessions s JOIN users u ON u.username=s.username "
        "WHERE s.token=?", (token,)).fetchone()
    conn.close(); return dict(row) if row else None


def close_session(token: str | None, db_path: Path | str = DB_PATH) -> None:
    if token:
        conn = _connect(db_path)
        conn.execute("DELETE FROM sessions WHERE token=?", (token,))
        conn.commit(); conn.close()


def ensure_bootstrap_admin(db_path: Path | str = DB_PATH) -> None:
    """Crea l'admin iniziale da PILZE_ADMIN_USER / PILZE_ADMIN_PASS se non esiste alcun utente."""
    conn = _connect(db_path)
    has = conn.execute("SELECT 1 FROM users LIMIT 1").fetchone()
    conn.close()
    if not has:
        u = os.environ.get("PILZE_ADMIN_USER", "admin")
        p = os.environ.get("PILZE_ADMIN_PASS")
        if p:
            create_user(u, p, is_admin=True, db_path=db_path)
