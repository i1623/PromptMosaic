"""DB helpers for saved tile groups."""

from __future__ import annotations

import db.library_db as database
from core.text_sanitize import single_line_text


def unique_group_name(base_name: str) -> str:
    """Return a saved-group name that does not exactly collide with existing names."""
    base = single_line_text(base_name).strip() or "Group"
    row = database.fetchone("SELECT 1 FROM group_presets WHERE name=? LIMIT 1", (base,))
    if row is None:
        return base

    n = 1
    while True:
        candidate = f"{base} {n}"
        row = database.fetchone(
            "SELECT 1 FROM group_presets WHERE name=? LIMIT 1",
            (candidate,),
        )
        if row is None:
            return candidate
        n += 1
