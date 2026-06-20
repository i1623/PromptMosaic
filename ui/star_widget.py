"""
5段階スター評価ウィジェット

・クリックで評価を変更（同じ星を再クリックで 0 にリセット）
・ホバーでプレビュー表示
・editable=False のとき表示専用
"""
from __future__ import annotations

from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel
from PySide6.QtCore import Signal, Qt, QEvent
from PySide6.QtGui import QFont


class StarWidget(QWidget):
    """
    クリック可能な 5段階スター評価ウィジェット。

    Signals:
        rating_changed(int): 評価が変わったとき (0〜5)
    """

    rating_changed = Signal(int)

    def __init__(
        self,
        rating: int = 0,
        editable: bool = True,
        font_size: int = 11,
        parent=None,
    ):
        super().__init__(parent)
        self._rating   = max(0, min(5, rating))
        self._editable = editable
        self._fs       = font_size
        self._labels: list[QLabel] = []
        self._build_ui()

    # ── UI 構築 ─────────────────────────────────────────

    def _build_ui(self) -> None:
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(1)

        for i in range(1, 6):
            lbl = QLabel()
            lbl.setFont(QFont("Segoe UI", self._fs))
            lbl.setStyleSheet("color: #f9e2af; background: transparent;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            if self._editable:
                lbl.setCursor(Qt.CursorShape.PointingHandCursor)
                lbl.installEventFilter(self)
                lbl.setProperty("star_idx", i)
            lay.addWidget(lbl)
            self._labels.append(lbl)

        self._redraw()

    def _redraw(self, hover: int = 0) -> None:
        active = hover if hover > 0 else self._rating
        for i, lbl in enumerate(self._labels, 1):
            lbl.setText("★" if i <= active else "☆")

    # ── イベントフィルタ（ホバー / クリック） ────────────

    def eventFilter(self, obj, event) -> bool:
        if not self._editable:
            return False
        idx = obj.property("star_idx")
        if idx is None:
            return False
        t = event.type()
        if t == QEvent.Type.Enter:
            self._redraw(hover=idx)
        elif t == QEvent.Type.Leave:
            self._redraw()
        elif t == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton:
                # 同じ星を再クリック → 0 にリセット
                new = 0 if idx == self._rating else idx
                self._rating = new
                self._redraw()
                self.rating_changed.emit(self._rating)
        return False

    # ── プロパティ ───────────────────────────────────────

    @property
    def rating(self) -> int:
        return self._rating

    @rating.setter
    def rating(self, value: int) -> None:
        self._rating = max(0, min(5, value))
        self._redraw()
