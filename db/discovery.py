"""
DB file discovery and cache rebuild.

Scans data/libraries/ and data/histories/ for valid DB files,
updates index.db, and (re)builds suggestions.db from library data.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from db import connections
from db.connections import (
    get_index_conn,
    get_suggestions_conn,
    list_library_names,
    list_history_names,
    library_db_path,
    history_db_path,
)

_SCHEMA_DIR = Path(__file__).parent


def _apply_schema(conn: sqlite3.Connection, schema_path: Path) -> None:
    conn.executescript(schema_path.read_text(encoding="utf-8"))
    conn.commit()


# ── Index rebuild ────────────────────────────────────────────────────

def rebuild_index() -> None:
    """
    Scan data/libraries/ and data/histories/, record status in index.db.
    Safe to call at any time; index.db is cache-only.
    """
    conn = get_index_conn()
    _apply_schema(conn, _SCHEMA_DIR / "schema_index.sql")
    conn.execute("DELETE FROM db_catalog")

    _catalog_fixed_dbs(conn)
    _catalog_library_dbs(conn)
    _catalog_history_dbs(conn)
    conn.commit()


def _catalog_fixed_dbs(conn: sqlite3.Connection) -> None:
    data_dir = connections.data_dir()
    fixed = [
        ("app",         "app",         data_dir / "app.db"),
        ("environment", "environment", data_dir / "environment.db"),
        ("notes",       "notes",       data_dir / "notes.db"),
        ("i2t",         "i2t",         data_dir / "i2t.db"),
        ("history_map", "history_map", data_dir / "history_map.db"),
        ("suggestions", "suggestions", data_dir / "suggestions.db"),
    ]
    for db_type, db_name, path in fixed:
        status = "ok" if path.exists() else "missing"
        conn.execute(
            "INSERT OR REPLACE INTO db_catalog (db_type, db_name, file_path, status) VALUES (?,?,?,?)",
            (db_type, db_name, str(path), status),
        )


def _catalog_library_dbs(conn: sqlite3.Connection) -> None:
    libs_dir = connections.libraries_dir()
    if not libs_dir.exists():
        return
    for path in sorted(libs_dir.glob("library_*.db")):
        name = path.stem[len("library_"):]
        if not name:
            continue
        status, err = _probe_db(path)
        conn.execute(
            "INSERT OR REPLACE INTO db_catalog (db_type, db_name, file_path, status, error_msg) VALUES (?,?,?,?,?)",
            ("library", name, str(path), status, err),
        )


def _catalog_history_dbs(conn: sqlite3.Connection) -> None:
    hists_dir = connections.histories_dir()
    if not hists_dir.exists():
        return
    for path in sorted(hists_dir.glob("history_*.db")):
        name = path.stem[len("history_"):]
        if not name:
            continue
        status, err = _probe_db(path)
        conn.execute(
            "INSERT OR REPLACE INTO db_catalog (db_type, db_name, file_path, status, error_msg) VALUES (?,?,?,?,?)",
            ("history", name, str(path), status, err),
        )


def _probe_db(path: Path) -> tuple[str, str | None]:
    """Try to open a DB and run a basic check. Returns (status, error_msg)."""
    try:
        conn = sqlite3.connect(str(path))
        conn.execute("PRAGMA integrity_check").fetchone()
        conn.close()
        return "ok", None
    except Exception as exc:
        return "error", str(exc)


# ── Suggestions rebuild ──────────────────────────────────────────────

def rebuild_suggestions() -> None:
    """
    Rebuild suggestions.db from all readable library_*.db files.
    Safe to call at any time; suggestions.db is cache-only.
    """
    conn = get_suggestions_conn()
    _apply_schema(conn, _SCHEMA_DIR / "schema_suggestions.sql")
    conn.execute("DELETE FROM suggestions")

    for lib_name in list_library_names():
        path = library_db_path(lib_name)
        if not path.exists():
            continue
        try:
            _ingest_library(conn, lib_name, path)
        except Exception:
            pass  # broken library DB — skip silently

    conn.commit()


def _ingest_library(
    sugg_conn: sqlite3.Connection,
    lib_name: str,
    lib_path: Path,
) -> None:
    lib_conn = sqlite3.connect(str(lib_path))
    lib_conn.row_factory = sqlite3.Row

    try:
        # Tags
        rows = lib_conn.execute(
            "SELECT id, name_en, name_local, category FROM tags"
        ).fetchall()
        for row in rows:
            text = row["name_en"] or ""
            if not text:
                continue
            sugg_conn.execute(
                """
                INSERT OR REPLACE INTO suggestions
                    (source_library_db, source_table, source_id, kind,
                     text, translated_text, display_label, normalized_text, sort_key)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    lib_name, "tags", row["id"], "tag",
                    text,
                    row["name_local"] or "",
                    row["name_local"] or text,
                    text.lower(),
                    text.lower(),
                ),
            )

        # prompt_texts
        rows = lib_conn.execute(
            "SELECT id, source_text, translated_text, display_label FROM prompt_texts"
        ).fetchall()
        for row in rows:
            text = row["source_text"] or ""
            if not text:
                continue
            sugg_conn.execute(
                """
                INSERT OR REPLACE INTO suggestions
                    (source_library_db, source_table, source_id, kind,
                     text, translated_text, display_label, normalized_text, sort_key)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    lib_name, "prompt_texts", row["id"], "prompt_text",
                    text,
                    row["translated_text"] or "",
                    row["display_label"] or text[:40],
                    text.lower(),
                    text.lower(),
                ),
            )
    finally:
        lib_conn.close()


def update_library_suggestions(lib_name: str) -> None:
    """
    Incremental update: remove all suggestions for lib_name,
    then re-ingest from that library DB.
    Called after tile/tag/group changes in the active library.
    """
    conn = get_suggestions_conn()
    try:
        conn.execute("DELETE FROM suggestions WHERE source_library_db = ?", (lib_name,))
        path = library_db_path(lib_name)
        if path.exists():
            _ingest_library(conn, lib_name, path)
        conn.commit()
    except Exception:
        pass
