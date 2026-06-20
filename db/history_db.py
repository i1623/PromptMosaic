"""history_*.db wrapper — operates on the currently active history."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Generator

from db.connections import get_active_history_conn, get_history_conn


def get_connection() -> sqlite3.Connection:
    return get_active_history_conn()


def fetchone(sql: str, params: tuple = ()) -> sqlite3.Row | None:
    return get_active_history_conn().execute(sql, params).fetchone()


def fetchall(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    return get_active_history_conn().execute(sql, params).fetchall()


def execute(sql: str, params: tuple = ()) -> sqlite3.Cursor:
    conn = get_active_history_conn()
    cur = conn.execute(sql, params)
    conn.commit()
    return cur


@contextmanager
def transaction() -> Generator[sqlite3.Connection, None, None]:
    conn = get_active_history_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def ensure_default_generation_group() -> int:
    """Ensure the active history DB has at least one generation group."""
    from db.init import ensure_default_generation_group as _ensure
    return _ensure()


def for_history(name: str) -> "_HistoryHandle":
    """Return a handle scoped to a specific named history."""
    return _HistoryHandle(name)


class _HistoryHandle:
    def __init__(self, name: str) -> None:
        self._name = name

    def _conn(self) -> sqlite3.Connection:
        return get_history_conn(self._name)

    def fetchone(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        return self._conn().execute(sql, params).fetchone()

    def fetchall(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self._conn().execute(sql, params).fetchall()

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        conn = self._conn()
        cur = conn.execute(sql, params)
        conn.commit()
        return cur

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        conn = self._conn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
