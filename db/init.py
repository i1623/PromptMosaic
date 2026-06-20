"""
Startup initialization for the multi-DB layout.

Replaces the old db/database.py initialize() function.
No migration from prompt_mosaic.db — clean new schema only.
"""
from __future__ import annotations

from pathlib import Path

from db import connections


def _apply_schema(conn, schema_path: Path) -> None:
    conn.executescript(schema_path.read_text(encoding="utf-8"))
    conn.commit()


_SCHEMA_DIR = Path(__file__).parent


def initialize_all() -> None:
    """
    Create the minimum source-of-truth DB set on first run,
    then rebuild the rebuildable cache DBs.

    Call once at application startup before any DB access.
    """
    _init_app_db()
    _init_environment_db()
    _init_history_map_db()
    _init_notes_db()
    _init_i2t_db()
    _init_send_queue_db()
    _ensure_default_library()
    _ensure_default_history()
    _ensure_all_schemas()
    _load_active_selections()
    _ensure_blob_columns()
    _rebuild_caches()


def _init_app_db() -> None:
    conn = connections.get_app_conn()
    _apply_schema(conn, _SCHEMA_DIR / "schema_app.sql")


def _init_environment_db() -> None:
    conn = connections.get_environment_conn()
    _apply_schema(conn, _SCHEMA_DIR / "schema_environment.sql")
    _seed_i2t_prompt_templates(conn)
    _seed_lora_genres(conn)


def _init_history_map_db() -> None:
    conn = connections.get_history_map_conn()
    _apply_schema(conn, _SCHEMA_DIR / "schema_history_map.sql")


def _init_notes_db() -> None:
    conn = connections.get_notes_conn()
    _apply_schema(conn, _SCHEMA_DIR / "schema_notes.sql")


def _init_i2t_db() -> None:
    conn = connections.get_i2t_conn()
    _apply_schema(conn, _SCHEMA_DIR / "schema_i2t.sql")


def _init_send_queue_db() -> None:
    conn = connections.get_send_queue_conn()
    _apply_schema(conn, _SCHEMA_DIR / "schema_send_queue.sql")


def _ensure_default_library() -> None:
    """Create library_default.db if no valid library DB exists."""
    names = connections.list_library_names()
    if not names:
        _create_library("default")


def _ensure_default_history() -> None:
    """Create history_default.db if no valid history DB exists."""
    names = connections.list_history_names()
    if not names:
        _create_history("default")


def _create_library(name: str) -> None:
    conn = connections.get_library_conn(name)
    _apply_schema(conn, _SCHEMA_DIR / "schema_library.sql")
    _seed_library_categories(conn)


def _create_history(name: str) -> None:
    conn = connections.get_history_conn(name)
    _apply_schema(conn, _SCHEMA_DIR / "schema_history.sql")
    ensure_default_generation_group(name)


def _load_active_selections() -> None:
    """Read current_library_db / current_history_db from app.db."""
    from db import app_db
    lib = app_db.get_setting("current_library_db", "default")
    hist = app_db.get_setting("current_history_db", "default")

    # Validate: make sure the selected DBs actually exist
    lib_names = connections.list_library_names()
    hist_names = connections.list_history_names()

    if lib not in lib_names:
        lib = lib_names[0] if lib_names else "default"
        app_db.set_setting("current_library_db", lib)

    if hist not in hist_names:
        hist = hist_names[0] if hist_names else "default"
        app_db.set_setting("current_history_db", hist)

    connections.set_active_library(lib)
    connections.set_active_history(hist)


def _ensure_all_schemas() -> None:
    """
    Apply the current schema to EVERY library/history DB, not just the active one.

    Schemas are CREATE TABLE IF NOT EXISTS only, so this is cheap and idempotent.
    Without this, a non-active DB created under an older schema would miss tables
    added later, and cross-DB access via for_library()/for_history() would hit
    "no such table" until the user happened to activate it.
    """
    for name in connections.list_library_names():
        _apply_schema(connections.get_library_conn(name), _SCHEMA_DIR / "schema_library.sql")
    for name in connections.list_history_names():
        _apply_schema(connections.get_history_conn(name), _SCHEMA_DIR / "schema_history.sql")


def _ensure_blob_columns() -> None:
    """Add thumbnail_data BLOB columns to existing DBs if they predate this change."""
    def _add(conn, table: str, col: str) -> None:
        existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if col not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} BLOB")
            conn.commit()

    _add(connections.get_environment_conn(), "models", "thumbnail_data")

    for name in connections.list_history_names():
        _add(connections.get_history_conn(name), "generations", "thumbnail_data")

    for name in connections.list_library_names():
        conn = connections.get_library_conn(name)
        _add(conn, "tag_thumbnails", "image_data")
        _add(conn, "prompt_texts", "thumbnail_data")
        _add(conn, "concepts", "thumbnail_data")



def _rebuild_caches() -> None:
    """Rebuild index.db and optionally suggestions.db."""
    from db import discovery
    discovery.rebuild_index()

    from db import app_db
    if app_db.get_setting("suggestions_rebuild_on_startup", "0") == "1":
        discovery.rebuild_suggestions()
    else:
        # Initialize suggestions cache schema only (do not wipe existing data)
        conn = connections.get_suggestions_conn()
        _apply_schema(conn, _SCHEMA_DIR / "schema_suggestions.sql")


def ensure_default_generation_group(history_name: str | None = None) -> int:
    """
    Ensure the active (or named) history DB has at least one generation group.
    Returns the group id.
    """
    if history_name is not None:
        conn = connections.get_history_conn(history_name)
    else:
        conn = connections.get_active_history_conn()

    row = conn.execute(
        "SELECT id FROM generation_groups ORDER BY created_at, id LIMIT 1"
    ).fetchone()
    if row:
        return int(row["id"])

    images_dir = Path(__file__).parent.parent / "images" / "Default"
    cur = conn.execute(
        "INSERT INTO generation_groups (name, parent_id, sort_order, folder_path) "
        "VALUES (?, NULL, 0, ?)",
        ("Default", str(images_dir)),
    )
    conn.commit()
    return int(cur.lastrowid)


def create_library(name: str) -> None:
    """Create a new library_*.db file and apply schema + seed data."""
    _create_library(name)


def create_history(name: str) -> None:
    """Create a new history_*.db file and apply schema + default group."""
    _create_history(name)


# ── Seed helpers ────────────────────────────────────────────────────

def _seed_library_categories(conn) -> None:
    """Seed default tag categories and genres into a newly created library DB."""
    # Legacy 8 base categories (color fallback only)
    base_cats = [
        ("object",      "物体",     "#1a3a5c", "#89b4fa",  10),
        ("state",       "状態",     "#3a3010", "#f9e2af",  20),
        ("quality",     "品質",     "#0f3a1a", "#a6e3a1",  30),
        ("style",       "スタイル", "#2e1a3a", "#cba6f7",  40),
        ("composition", "構図",     "#3a2000", "#fab387",  50),
        ("lighting",    "照明",     "#3a1010", "#f38ba8",  60),
        ("action",      "動作",     "#0a2a2e", "#89dceb",  70),
        ("scene",       "情景",     "#3a1025", "#f5c2e7",  80),
    ]
    for key, label, bg, fg, order in base_cats:
        conn.execute(
            "INSERT OR IGNORE INTO tag_categories "
            "(key, label, bg_color, fg_color, sort_order, is_tag_genre) VALUES (?,?,?,?,?,0)",
            (key, label, bg, fg, order),
        )

    # Browsable genre categories (is_tag_genre=1)
    genres = [
        ("character_identity",           "#2d1f4e", "#cba6f7", "#e5d8f5", "#8839ef",  10),
        ("human_expression",             "#3d1b2e", "#f38ba8", "#f5d8e0", "#d20f39",  20),
        ("pose_action_interaction",      "#1b2d4e", "#89b4fa", "#d8e4f5", "#1e66f5",  30),
        ("clothing_accessory",           "#3d2a1b", "#fab387", "#f5e8d8", "#fe640b",  40),
        ("living_creature",              "#1b3d1b", "#a6e3a1", "#d8f5d8", "#40a02b",  50),
        ("object_artifact",              "#2a2a3a", "#a6adc8", "#e8e8f0", "#6c6f85",  60),
        ("architecture_structure",       "#1b3333", "#89dceb", "#d8f5f5", "#04a5e5",  70),
        ("location_background",          "#383818", "#f9e2af", "#f5f0d8", "#df8e1d",  80),
        ("natural_feature",              "#1b3822", "#b6f27c", "#d8f5e0", "#40a02b",  90),
        ("phenomenon_event",             "#3d2810", "#fab387", "#f5ead8", "#fe640b", 100),
        ("era_culture_worldview",        "#3d1b3d", "#f5c2e7", "#f5d8f0", "#ea76cb", 110),
        ("art_style_medium",             "#1e1e3d", "#b4befe", "#e8d8f5", "#7287fd", 120),
        ("lighting_color_screen_effect", "#3d1010", "#f38ba8", "#f5d8d8", "#d20f39", 130),
        ("quality_correction",           "#0f3a1a", "#a6e3a1", "#d8f5d8", "#40a02b", 140),
        ("mixed_unsorted",               "#252535", "#6c7086", "#e8e8e8", "#9ca0b0", 9990),
    ]
    for key, dbg, dfg, lbg, lfg, order in genres:
        conn.execute(
            "INSERT OR IGNORE INTO tag_categories "
            "(key, label, bg_color, fg_color, bg_color_dark, fg_color_dark, "
            " bg_color_light, fg_color_light, sort_order, is_tag_genre) "
            "VALUES (?,''  ,?,?,?,?,?,?,?,1)",
            (key, dbg, dfg, dbg, dfg, lbg, lfg, order),
        )
    conn.commit()


def _seed_i2t_prompt_templates(conn) -> None:
    defaults = [
        (
            "全体描写（汎用）",
            "You are a Stable Diffusion prompt expert. Analyze the image and output a comma-separated list of English tags that describe the image. Focus on: character appearance, clothing, pose, expression, art style, lighting, background. Output ONLY the comma-separated tags, no explanations.",
            "Describe this image as Stable Diffusion prompt tags.",
            10,
        ),
        (
            "キャラクター・服装",
            "You are a Stable Diffusion prompt expert. Analyze the character in the image. Output a comma-separated list of English tags focusing on: gender, hair color/style/length, eye color, facial features, clothing details, accessories, body type. Output ONLY the comma-separated tags.",
            "Describe the character's appearance and clothing in this image.",
            20,
        ),
        (
            "ポーズ・構図",
            "You are a Stable Diffusion prompt expert. Analyze the pose and composition in the image. Output a comma-separated list of English tags focusing on: body pose, hand position, camera angle, framing, perspective, background elements. Output ONLY the comma-separated tags.",
            "Describe the pose and composition of this image.",
            30,
        ),
        (
            "スタイル・雰囲気",
            "You are a Stable Diffusion prompt expert. Analyze the art style and atmosphere of the image. Output a comma-separated list of English tags focusing on: art style, rendering technique, color palette, lighting mood, atmosphere, quality tags. Output ONLY the comma-separated tags.",
            "Describe the art style and atmosphere of this image.",
            40,
        ),
    ]
    for name, sys_p, usr_p, order in defaults:
        conn.execute(
            "INSERT OR IGNORE INTO i2t_prompt_templates "
            "(name, system_prompt, user_prompt, sort_order) VALUES (?,?,?,?)",
            (name, sys_p, usr_p, order),
        )
    conn.commit()


def _seed_lora_genres(conn) -> None:
    """lora_genres are seeded by the schema SQL INSERT OR IGNORE; this is a no-op."""
    pass
