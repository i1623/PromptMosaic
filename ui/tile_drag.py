"""
タイルドラッグ状態レジストリ

QDrag は同一プロセス内で実行されるため、
シリアライズせずに Python オブジェクト参照をそのまま渡す。

Usage:
    # ドラッグ開始時 (TileWidget)
    tile_drag.set_drag(tile_widget)

    # ドロップ先 (BlockWidget)
    tw = tile_drag.get_drag()

    # ドラッグ終了後 (TileWidget._begin_drag の後処理)
    tile_drag.clear_drag()
"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ui.tile_widget import TileWidget

_dragged: "TileWidget | None" = None


def set_drag(tw: "TileWidget") -> None:
    global _dragged
    _dragged = tw


def get_drag() -> "TileWidget | None":
    return _dragged


def clear_drag() -> None:
    global _dragged
    _dragged = None
