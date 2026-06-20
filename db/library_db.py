"""library_*.db wrapper — operates on the currently active library."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Generator

from db.connections import get_active_library_conn, get_library_conn


def get_connection() -> sqlite3.Connection:
    return get_active_library_conn()


def fetchone(sql: str, params: tuple = ()) -> sqlite3.Row | None:
    return get_active_library_conn().execute(sql, params).fetchone()


def fetchall(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    return get_active_library_conn().execute(sql, params).fetchall()


def execute(sql: str, params: tuple = ()) -> sqlite3.Cursor:
    conn = get_active_library_conn()
    cur = conn.execute(sql, params)
    conn.commit()
    return cur


@contextmanager
def transaction() -> Generator[sqlite3.Connection, None, None]:
    conn = get_active_library_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def for_library(name: str) -> "_LibraryHandle":
    """Return a handle scoped to a specific named library."""
    return _LibraryHandle(name)


class _LibraryHandle:
    def __init__(self, name: str) -> None:
        self._name = name

    def _conn(self) -> sqlite3.Connection:
        return get_library_conn(self._name)

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
