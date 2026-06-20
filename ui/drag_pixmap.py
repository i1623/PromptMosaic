"""Helpers for drag preview pixmaps."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QPixmap


def translucent_drag_pixmap(source: QPixmap, opacity: float = 0.55) -> QPixmap:
    """Return a semi-transparent copy for drag previews."""
    if source.isNull():
        return source
    result = QPixmap(source.size())
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    painter.setOpacity(max(0.0, min(1.0, opacity)))
    painter.drawPixmap(0, 0, source)
    painter.end()
    return result
