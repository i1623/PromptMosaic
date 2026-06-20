"""
Multi-DB connection manager.

Each DB type has its own file:
  data/app.db                  — app-wide settings
  data/environment.db          — machine/environment settings
  data/notes.db                — user knowledge notes
  data/i2t.db                  — image-to-text analysis history
  data/history_map.db          — cross-history relationships
  data/index.db                — cache: DB catalog (rebuildable)
  data/suggestions.db          — cache: cross-library suggestions (rebuildable)
  data/send_queue.db           — transient: generation send buffer (empty when idle)
  data/libraries/library_*.db  — one per library
  data/histories/history_*.db  — one per history folder
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

_DATA_DIR      = Path(__file__).parent.parent / "data"
_LIBRARIES_DIR = _DATA_DIR / "libraries"
_HISTORIES_DIR = _DATA_DIR / "histories"

# Thread-local dict: str(path) -> Connection
_local = threading.local()

# 全スレッドの接続 dict を追跡するプロセス全体のレジストリ。
# close_all() がワーカースレッドの接続も閉じられるようにするためのもの
# （Windows ではハンドルが残ると DB ファイルの削除/リネームが失敗する）。
_registry_lock = threading.Lock()
_conn_registry: list[tuple[threading.Thread, dict[str, sqlite3.Connection]]] = []

# Active library/history names (process-wide state)
_active_library: str = "default"
_active_history: str = "default"


def _close_conns(conns: dict[str, sqlite3.Connection]) -> None:
    for conn in list(conns.values()):
        try:
            conn.close()
        except Exception:
            pass
    conns.clear()


def _prune_dead_threads_locked() -> None:
    """終了したスレッドの接続を閉じてレジストリから除去する（_registry_lock 保持下で呼ぶ）。"""
    alive: list[tuple[threading.Thread, dict[str, sqlite3.Connection]]] = []
    for thread, conns in _conn_registry:
        if thread.is_alive():
            alive.append((thread, conns))
        else:
            _close_conns(conns)
    _conn_registry[:] = alive


def _get_connections() -> dict[str, sqlite3.Connection]:
    if not hasattr(_local, "connections"):
        _local.connections = {}
        with _registry_lock:
            _prune_dead_threads_locked()
            _conn_registry.append((threading.current_thread(), _local.connections))
    return _local.connections


def _open_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(path),
        detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def _get_conn_for(path: Path) -> sqlite3.Connection:
    key = str(path.resolve())
    conns = _get_connections()
    if key not in conns:
        conns[key] = _open_db(path)
    return conns[key]


# ── Fixed singleton DBs ──────────────────────────────────────────────

def get_app_conn() -> sqlite3.Connection:
    return _get_conn_for(_DATA_DIR / "app.db")


def get_environment_conn() -> sqlite3.Connection:
    return _get_conn_for(_DATA_DIR / "environment.db")


def get_notes_conn() -> sqlite3.Connection:
    return _get_conn_for(_DATA_DIR / "notes.db")


def get_i2t_conn() -> sqlite3.Connection:
    return _get_conn_for(_DATA_DIR / "i2t.db")


def get_history_map_conn() -> sqlite3.Connection:
    return _get_conn_for(_DATA_DIR / "history_map.db")


def get_index_conn() -> sqlite3.Connection:
    return _get_conn_for(_DATA_DIR / "index.db")


def get_suggestions_conn() -> sqlite3.Connection:
    return _get_conn_for(_DATA_DIR / "suggestions.db")


def get_send_queue_conn() -> sqlite3.Connection:
    """送信キュー（一時バッファ）。正常起動・終了時は常に空であるべきDB。"""
    return _get_conn_for(_DATA_DIR / "send_queue.db")


# ── Per-library / per-history connections ───────────────────────────

def get_library_conn(name: str) -> sqlite3.Connection:
    return _get_conn_for(_LIBRARIES_DIR / f"library_{name}.db")


def get_history_conn(name: str) -> sqlite3.Connection:
    path = _HISTORIES_DIR / f"history_{name}.db"
    key = str(path.resolve())
    conns = _get_connections()
    if key not in conns:
        conn = _open_db(path)
        # environment.db を env エイリアスでアタッチ（cross-DB JOIN 用）
        env_path = str((_DATA_DIR / "environment.db").resolve())
        conn.execute("ATTACH DATABASE ? AS env", (env_path,))
        conns[key] = conn
    return conns[key]


# ── Active library/history ──────────────────────────────────────────

def set_active_library(name: str) -> None:
    global _active_library
    _active_library = name


def set_active_history(name: str) -> None:
    global _active_history
    _active_history = name


def get_active_library_name() -> str:
    return _active_library


def get_active_history_name() -> str:
    return _active_history


def get_active_library_conn() -> sqlite3.Connection:
    return get_library_conn(_active_library)


def get_active_history_conn() -> sqlite3.Connection:
    return get_history_conn(_active_history)


# ── Discovery helpers ───────────────────────────────────────────────

def list_library_names() -> list[str]:
    """Return library names derived from data/libraries/library_*.db filenames."""
    if not _LIBRARIES_DIR.exists():
        return []
    names = []
    for path in sorted(_LIBRARIES_DIR.glob("library_*.db")):
        name = path.stem[len("library_"):]
        if name:
            names.append(name)
    return names


def list_history_names() -> list[str]:
    """Return history names derived from data/histories/history_*.db filenames."""
    if not _HISTORIES_DIR.exists():
        return []
    names = []
    for path in sorted(_HISTORIES_DIR.glob("history_*.db")):
        name = path.stem[len("history_"):]
        if name:
            names.append(name)
    return names


def library_db_path(name: str) -> Path:
    return _LIBRARIES_DIR / f"library_{name}.db"


def history_db_path(name: str) -> Path:
    return _HISTORIES_DIR / f"history_{name}.db"


def data_dir() -> Path:
    return _DATA_DIR


def libraries_dir() -> Path:
    return _LIBRARIES_DIR


def histories_dir() -> Path:
    return _HISTORIES_DIR


# ── Teardown ────────────────────────────────────────────────────────

def close_all() -> None:
    """
    Close all connections in ALL threads (not just the calling thread).

    Used before deleting/renaming DB files (Windows requires all handles
    released) and at application exit. Threads still alive simply reopen
    lazily on their next DB access. A worker mid-query when this runs will
    get a sqlite3 error on its current statement — callers should stop
    workers first when practical.
    """
    with _registry_lock:
        for _thread, conns in _conn_registry:
            _close_conns(conns)
        _prune_dead_threads_locked()
