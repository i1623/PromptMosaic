"""
ブラウザタグのドラッグ状態レジストリ

TagBrowser から BlockWidget へのドラッグ時に使用する。
tile_drag.py と対称的な構造。

Usage:
    # ドラッグ開始時 (TagBrowser)
    tag_drag.set_drag(name_en="1girl", name_local="女の子", category="object", tag_id=42)

    # ドロップ先 (BlockWidget)
    info = tag_drag.get_drag()  # {"name_en": ..., "name_local": ..., "category": ..., "id": ...} | None

    # ドラッグ終了後 (TagBrowser._begin_browser_drag の後処理)
    tag_drag.clear_drag()
"""
from __future__ import annotations

# ブラウザタグドラッグの MIME タイプ（tag_browser と block_widget の両方が参照）
BROWSER_MIME = "application/x-invoke-browser-tag"

_dragged: dict | None = None


def set_drag(
    name_en: str,
    name_local: str,
    category: str,
    tag_id: int | None = None,
    *,
    dictionary_key: str = "",
    is_nav: bool = False,
    child_count: int = 0,
) -> None:
    global _dragged
    _dragged = {
        "name_en":    name_en,
        "name_local": name_local,
        "category":   category,
        "dictionary_key": dictionary_key,
        "id":         tag_id,
        "is_nav":     is_nav,
        "child_count": child_count,
    }


def get_drag() -> dict | None:
    return _dragged


def clear_drag() -> None:
    global _dragged
    _dragged = None


# ── 保存グループドラッグ（TagBrowser → BlockWidget） ─────────────────────────

GROUP_BROWSER_MIME = "application/x-invoke-browser-group"

_dragged_group: dict | None = None  # {"group_json": str, "preset_name": str}


def set_group_drag(group_json: str, preset_name: str = "") -> None:
    global _dragged_group
    _dragged_group = {
        "group_json": group_json,
        "preset_name": preset_name,
    }


def get_group_drag() -> dict | None:
    return _dragged_group


def clear_group_drag() -> None:
    global _dragged_group
    _dragged_group = None
