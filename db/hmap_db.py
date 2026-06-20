"""history_map.db wrapper — cross-history relationships and editing lineage."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Generator

from db.connections import get_history_map_conn


def get_connection() -> sqlite3.Connection:
    return get_history_map_conn()


def fetchone(sql: str, params: tuple = ()) -> sqlite3.Row | None:
    return get_history_map_conn().execute(sql, params).fetchone()


def fetchall(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    return get_history_map_conn().execute(sql, params).fetchall()


def execute(sql: str, params: tuple = ()) -> sqlite3.Cursor:
    conn = get_history_map_conn()
    cur = conn.execute(sql, params)
    conn.commit()
    return cur


@contextmanager
def transaction() -> Generator[sqlite3.Connection, None, None]:
    conn = get_history_map_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# ── Editor history lineage helpers ──────────────────────────────────

def record_node(
    history_db: str,
    history_id: int,
    parent_db: str | None,
    parent_id: int | None,
) -> None:
    """Insert or replace an editor history node in history_map.db."""
    execute(
        """
        INSERT OR REPLACE INTO editor_history_nodes
            (history_db, history_id, parent_db, parent_id, created_at)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (history_db, history_id, parent_db, parent_id),
    )


def find_root(history_db: str, history_id: int) -> tuple[str, int] | None:
    """Walk up parent chain to find the root node. Returns None if node not found."""
    current_db, current_id = history_db, history_id
    seen: set[tuple[str, int]] = set()
    while True:
        key: tuple[str, int] = (current_db, current_id)
        if key in seen:
            return key  # cycle guard
        seen.add(key)
        row = fetchone(
            "SELECT parent_db, parent_id FROM editor_history_nodes "
            "WHERE history_db=? AND history_id=?",
            (current_db, current_id),
        )
        if row is None:
            return None  # node not in DB
        if row["parent_db"] is None:
            return key  # root found
        current_db = row["parent_db"]
        current_id = int(row["parent_id"])


def fetch_tree_nodes(root_db: str, root_id: int) -> list[sqlite3.Row]:
    """Fetch all nodes in the tree rooted at (root_db, root_id) via recursive CTE."""
    return fetchall(
        """
        WITH RECURSIVE tree(history_db, history_id, parent_db, parent_id, created_at) AS (
            SELECT history_db, history_id, parent_db, parent_id, created_at
            FROM editor_history_nodes
            WHERE history_db = ? AND history_id = ?
            UNION ALL
            SELECT n.history_db, n.history_id, n.parent_db, n.parent_id, n.created_at
            FROM editor_history_nodes n
            JOIN tree ON n.parent_db = tree.history_db AND n.parent_id = tree.history_id
        )
        SELECT * FROM tree
        ORDER BY created_at DESC, history_id DESC
        """,
        (root_db, root_id),
    )


def detach_subtree(history_db: str, history_id: int) -> None:
    """Make (history_db, history_id) a new root by clearing its parent link."""
    execute(
        "UPDATE editor_history_nodes SET parent_db=NULL, parent_id=NULL "
        "WHERE history_db=? AND history_id=?",
        (history_db, history_id),
    )
