"""environment.db wrapper — machine/environment-specific settings."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Generator

from db.connections import get_environment_conn


def get_connection() -> sqlite3.Connection:
    return get_environment_conn()


def fetchone(sql: str, params: tuple = ()) -> sqlite3.Row | None:
    return get_environment_conn().execute(sql, params).fetchone()


def fetchall(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    return get_environment_conn().execute(sql, params).fetchall()


def execute(sql: str, params: tuple = ()) -> sqlite3.Cursor:
    conn = get_environment_conn()
    cur = conn.execute(sql, params)
    conn.commit()
    return cur


@contextmanager
def transaction() -> Generator[sqlite3.Connection, None, None]:
    conn = get_environment_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def get_setting(key: str, default: str = "") -> str:
    row = fetchone("SELECT value FROM env_settings WHERE key = ?", (key,))
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    execute(
        "INSERT OR REPLACE INTO env_settings (key, value) VALUES (?, ?)",
        (key, value),
    )
