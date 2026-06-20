"""notes.db wrapper — user knowledge notes (source-of-truth)."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Generator

from db.connections import get_notes_conn


def get_connection() -> sqlite3.Connection:
    return get_notes_conn()


def fetchone(sql: str, params: tuple = ()) -> sqlite3.Row | None:
    return get_notes_conn().execute(sql, params).fetchone()


def fetchall(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    return get_notes_conn().execute(sql, params).fetchall()


def execute(sql: str, params: tuple = ()) -> sqlite3.Cursor:
    conn = get_notes_conn()
    cur = conn.execute(sql, params)
    conn.commit()
    return cur


@contextmanager
def transaction() -> Generator[sqlite3.Connection, None, None]:
    conn = get_notes_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
