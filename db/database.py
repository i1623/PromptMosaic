"""
TOMBSTONE — db/database.py

This module has been replaced by the multi-DB architecture.
All callers should import from the appropriate new module:

  db/app_db.py         — app-wide settings (app_settings)
  db/env_db.py         — environment/machine settings (env_settings, models, templates, ...)
  db/library_db.py     — active library (tags, tag_categories, group_presets, prompt_texts, ...)
  db/history_db.py     — active history (generations, generation_groups, tiles, ...)
  db/hmap_db.py        — history_map.db (editor_history_nodes, ...)
  db/notes_db.py       — notes.db (daily_notes, discovery_notes, protocols, ...)
  db/i2t_db.py         — i2t.db (i2t_history)
  db/index_db.py       — index.db (db_catalog, cache only)
  db/suggestions_db.py — suggestions.db (suggestions, cache only)

If you see this error at runtime, a caller still imports db.database.
"""


def _dead(*args, **kwargs):
    raise RuntimeError(
        "db.database is removed. Import from db.app_db, db.env_db, "
        "db.library_db, db.history_db, or another new module."
    )


initialize = _dead
get_connection = _dead
get_db_path = _dead
get_current_schema_version = _dead
ensure_default_generation_group = _dead
fetchone = _dead
fetchall = _dead
execute = _dead
transaction = _dead
close = _dead
