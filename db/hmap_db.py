"""history_map.db wrapper — cross-history relationships and editing lineage."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Generator

from db.connections import get_history_map_conn

DRAFT_DB_PREFIX = "draft:"


def is_draft_db(history_db: str | None) -> bool:
    return str(history_db or "").startswith(DRAFT_DB_PREFIX)


def draft_db_name(owner_history_db: str) -> str:
    return f"{DRAFT_DB_PREFIX}{owner_history_db}"


def owner_history_db_from_draft(history_db: str) -> str:
    return str(history_db)[len(DRAFT_DB_PREFIX):]


def is_draft_key(key: tuple[str, int] | None) -> bool:
    return bool(key and is_draft_db(key[0]))


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


def record_draft_node(
    owner_history_db: str,
    parent_db: str,
    parent_id: int,
    prompt_json: str,
    *,
    memo_text: str = "",
    group_id: int | None = None,
    title: str | None = None,
    draft_id: int | None = None,
) -> tuple[str, int]:
    """Insert or update an image-less draft node and return its UI key."""
    conn = get_history_map_conn()
    if draft_id is None:
        cur = conn.execute(
            """
            INSERT INTO editor_history_draft_nodes
                (owner_history_db, parent_db, parent_id, group_id, prompt_json, memo_text, title,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (owner_history_db, parent_db, parent_id, group_id, prompt_json, memo_text, title),
        )
        conn.commit()
        return (draft_db_name(owner_history_db), int(cur.lastrowid))

    conn.execute(
        """
        UPDATE editor_history_draft_nodes
           SET owner_history_db=?, parent_db=?, parent_id=?, group_id=?,
               prompt_json=?, memo_text=?, title=?, updated_at=CURRENT_TIMESTAMP
         WHERE id=?
        """,
        (owner_history_db, parent_db, parent_id, group_id, prompt_json, memo_text, title, int(draft_id)),
    )
    conn.commit()
    return (draft_db_name(owner_history_db), int(draft_id))


def fetch_draft_node(owner_history_db: str, draft_id: int) -> sqlite3.Row | None:
    return fetchone(
        """
        SELECT id, owner_history_db, parent_db, parent_id, group_id, prompt_json,
               memo_text, title, created_at, updated_at, deleted_at
          FROM editor_history_draft_nodes
         WHERE owner_history_db=? AND id=? AND deleted_at IS NULL
        """,
        (owner_history_db, int(draft_id)),
    )


def fetch_active_draft_rows(owner_history_db: str) -> list[sqlite3.Row]:
    return fetchall(
        """
        SELECT id, owner_history_db, parent_db, parent_id, group_id, prompt_json,
               memo_text, title, created_at, updated_at, deleted_at
          FROM editor_history_draft_nodes
         WHERE owner_history_db=? AND deleted_at IS NULL
         ORDER BY updated_at DESC, id DESC
        """,
        (owner_history_db,),
    )


def _normal_node_row(history_db: str, history_id: int) -> dict | None:
    row = fetchone(
        "SELECT history_db, history_id, parent_db, parent_id, created_at "
        "FROM editor_history_nodes WHERE history_db=? AND history_id=?",
        (history_db, int(history_id)),
    )
    if row is None:
        return None
    return {
        "node_type": "generation",
        "history_db": str(row["history_db"]),
        "history_id": int(row["history_id"]),
        "parent_db": row["parent_db"],
        "parent_id": int(row["parent_id"]) if row["parent_id"] is not None else None,
        "created_at": str(row["created_at"] or ""),
        "updated_at": str(row["created_at"] or ""),
        "deleted_at": None,
        "title": None,
        "group_id": None,
    }


def _draft_node_row(history_db: str, history_id: int) -> dict | None:
    if not is_draft_db(history_db):
        return None
    owner = owner_history_db_from_draft(history_db)
    row = fetch_draft_node(owner, int(history_id))
    if row is None:
        return None
    return {
        "node_type": "draft",
        "history_db": draft_db_name(owner),
        "history_id": int(row["id"]),
        "parent_db": str(row["parent_db"]),
        "parent_id": int(row["parent_id"]),
        "created_at": str(row["created_at"] or ""),
        "updated_at": str(row["updated_at"] or row["created_at"] or ""),
        "deleted_at": row["deleted_at"],
        "title": row["title"],
        "group_id": row["group_id"],
    }


def fetch_node(history_db: str, history_id: int) -> dict | None:
    if is_draft_db(history_db):
        return _draft_node_row(history_db, history_id)
    return _normal_node_row(history_db, history_id)


def find_root(history_db: str, history_id: int) -> tuple[str, int] | None:
    """Walk up parent chain to find the root node. Returns None if node not found."""
    current_db, current_id = history_db, history_id
    seen: set[tuple[str, int]] = set()
    while True:
        key: tuple[str, int] = (current_db, current_id)
        if key in seen:
            return key  # cycle guard
        seen.add(key)
        row = fetch_node(current_db, int(current_id))
        if row is None:
            return None  # node not in DB
        if row["parent_db"] is None:
            return key  # root found
        current_db = row["parent_db"]
        current_id = int(row["parent_id"])


def _child_rows(parent_db: str, parent_id: int) -> list[dict]:
    rows: list[dict] = []
    for row in fetchall(
        "SELECT history_db, history_id, parent_db, parent_id, created_at "
        "FROM editor_history_nodes WHERE parent_db=? AND parent_id=?",
        (parent_db, int(parent_id)),
    ):
        rows.append({
            "node_type": "generation",
            "history_db": str(row["history_db"]),
            "history_id": int(row["history_id"]),
            "parent_db": row["parent_db"],
            "parent_id": int(row["parent_id"]) if row["parent_id"] is not None else None,
            "created_at": str(row["created_at"] or ""),
            "updated_at": str(row["created_at"] or ""),
            "deleted_at": None,
            "title": None,
            "group_id": None,
        })
    for row in fetchall(
        """
        SELECT id, owner_history_db, parent_db, parent_id, group_id, title,
               created_at, updated_at, deleted_at
          FROM editor_history_draft_nodes
         WHERE parent_db=? AND parent_id=? AND deleted_at IS NULL
        """,
        (parent_db, int(parent_id)),
    ):
        rows.append({
            "node_type": "draft",
            "history_db": draft_db_name(str(row["owner_history_db"])),
            "history_id": int(row["id"]),
            "parent_db": str(row["parent_db"]),
            "parent_id": int(row["parent_id"]),
            "created_at": str(row["created_at"] or ""),
            "updated_at": str(row["updated_at"] or row["created_at"] or ""),
            "deleted_at": row["deleted_at"],
            "title": row["title"],
            "group_id": row["group_id"],
        })
    return sorted(rows, key=lambda r: (str(r.get("updated_at") or r.get("created_at") or ""), int(r["history_id"])), reverse=True)


def fetch_tree_nodes(root_db: str, root_id: int) -> list[dict]:
    """Fetch generation and draft nodes in the tree rooted at the given UI key."""
    root = fetch_node(root_db, int(root_id))
    if root is None:
        return []
    result: list[dict] = []
    queue: list[dict] = [root]
    seen: set[tuple[str, int]] = set()
    while queue:
        node = queue.pop(0)
        key = (str(node["history_db"]), int(node["history_id"]))
        if key in seen:
            continue
        seen.add(key)
        result.append(node)
        queue.extend(_child_rows(key[0], key[1]))
    return sorted(
        result,
        key=lambda r: (str(r.get("updated_at") or r.get("created_at") or ""), int(r["history_id"])),
        reverse=True,
    )


def parent_key(history_db: str, history_id: int) -> tuple[str, int] | None:
    row = fetch_node(history_db, int(history_id))
    if row is None or row["parent_db"] is None:
        return None
    return (str(row["parent_db"]), int(row["parent_id"]))


def child_keys(history_db: str, history_id: int) -> list[tuple[str, int]]:
    return [
        (str(row["history_db"]), int(row["history_id"]))
        for row in _child_rows(history_db, int(history_id))
    ]


def sibling_keys(history_db: str, history_id: int) -> list[tuple[str, int]]:
    parent = parent_key(history_db, int(history_id))
    if parent is None:
        return []
    return child_keys(parent[0], parent[1])


def soft_delete_draft(owner_history_db: str, draft_id: int) -> None:
    execute(
        """
        UPDATE editor_history_draft_nodes
           SET deleted_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
         WHERE owner_history_db=? AND id=? AND deleted_at IS NULL
        """,
        (owner_history_db, int(draft_id)),
    )


def detach_subtree(history_db: str, history_id: int) -> None:
    """Make (history_db, history_id) a new root by clearing its parent link."""
    execute(
        "UPDATE editor_history_nodes SET parent_db=NULL, parent_id=NULL "
        "WHERE history_db=? AND history_id=?",
        (history_db, history_id),
    )
