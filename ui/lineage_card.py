"""
系譜ナビゲーター親カード＋継承権者カード

中央ペインの「Positive Prompt」見出し行に常駐する。

LineageParentCard — 現在の編集系譜ノードの「親」を表示する。
    クリックで親の設定をロード（シードは除く）できる。表示はサムネ＋#番号のみ。

    状態:
        none    — 系譜なし（現在ノード未設定。次の生成は新しい開祖になる）
        root    — 現在ノードが開祖（親なし）
        parent  — 親あり（サムネ＋#番号、クリック可）
        missing — 親ノードの記録が見つからない（クリック不可）

LineageHeirCard — 継承権者（現在ノード＝次の生成の親になるノード）を表示する。
    ◀▶ で同じ親を持つ兄弟の間で継承権者を切り替えられる。
    ◀▶ は常に場所を確保し（無効時はディム表示）、サムネの位置は固定。
"""
from __future__ import annotations

from PySide6.QtWidgets import QApplication, QWidget, QHBoxLayout, QLabel, QFrame, QToolButton, QVBoxLayout, QScrollArea, QSizePolicy, QGridLayout
from PySide6.QtCore import Signal, Qt, QElapsedTimer, QEvent, QPoint, QTimer, QPropertyAnimation
from PySide6.QtGui import QBrush, QColor, QConicalGradient, QPainter, QPen, QPixmap

from core.i18n import tr
from ui.styles import (
    SURFACE0, SURFACE1, SURFACE2, TEXT, SUBTEXT, ACCENT, GREEN, EMOJI_ICON_SS, ui_font,
)

_THUMB = 44  # サムネ一辺(px)。履歴サムネ(64〜)の縮小表示
_STRIP_THUMB = 128
_HEIR_PREVIEW_MS = 2200


def _set_thumb(label: QLabel, thumb_bytes: bytes | None, fallback: str = "🖼", size: int = _THUMB) -> None:
    """サムネ QLabel に画像をセットする（なければ fallback 文字）。"""
    pix = QPixmap()
    if thumb_bytes and pix.loadFromData(bytes(thumb_bytes)) and not pix.isNull():
        label.setText("")
        label.setPixmap(pix.scaled(
            size, size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))
    else:
        label.setPixmap(QPixmap())
        label.setText(fallback)


class _LineageNodeThumb(QFrame):
    double_clicked = Signal(str, int)

    def __init__(self, history_db: str, gen_id: int, thumb_bytes: bytes | None, preview: QPixmap | None, current: bool, parent=None):
        super().__init__(parent)
        self._history_db = history_db
        self._gen_id = int(gen_id)
        self._preview_pix = QPixmap(preview) if preview and not preview.isNull() else QPixmap()
        self._popup: QFrame | None = None
        self.setObjectName("currentLineageThumb" if current else "lineageThumb")
        self.setFixedSize(_STRIP_THUMB + 4, _STRIP_THUMB + 4)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        self._thumb = QLabel()
        self._thumb.setFixedSize(_STRIP_THUMB, _STRIP_THUMB)
        self._thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._thumb)
        _set_thumb(self._thumb, thumb_bytes, size=_STRIP_THUMB)
        if self._preview_pix.isNull() and thumb_bytes:
            self._preview_pix.loadFromData(bytes(thumb_bytes))
        self.setToolTip(tr("editor.lineage_strip_node_tooltip"))
        self._restyle(current)

    def key(self) -> tuple[str, int]:
        return self._history_db, self._gen_id

    def _restyle(self, current: bool) -> None:
        border = GREEN if current else SURFACE2
        self.setStyleSheet(
            f"QFrame#lineageThumb, QFrame#currentLineageThumb {{ background: {SURFACE1}; "
            f"border: {'2' if current else '1'}px solid {border}; border-radius: 4px; }}"
            f"QFrame#lineageThumb:hover {{ border: 1px solid {ACCENT}; }}"
            f"QLabel {{ background: {SURFACE0}; color: {SUBTEXT}; border: none; }}"
        )

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit(self._history_db, self._gen_id)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._show_preview()
        super().mousePressEvent(event)

    def _show_preview(self) -> None:
        if self._preview_pix.isNull():
            return
        if self._popup is not None:
            self._popup.close()
        pix = self._preview_pix
        screen = QApplication.screenAt(self.mapToGlobal(self.rect().center()))
        avail = screen.availableGeometry() if screen is not None else QApplication.primaryScreen().availableGeometry()
        scaled = pix.scaled(
            max(_STRIP_THUMB * 2, pix.width() // 2),
            max(_STRIP_THUMB * 2, pix.height() // 2),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        if scaled.width() > avail.width() * 0.72 or scaled.height() > avail.height() * 0.72:
            scaled = pix.scaled(
                int(avail.width() * 0.72),
                int(avail.height() * 0.72),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        popup = QFrame(None, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        popup.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        popup.setStyleSheet(f"QFrame {{ background: {SURFACE0}; border: 1px solid {ACCENT}; border-radius: 4px; }}")
        lay = QVBoxLayout(popup)
        lay.setContentsMargins(6, 6, 6, 6)
        label = QLabel()
        label.setPixmap(scaled)
        label.setFixedSize(scaled.size())
        lay.addWidget(label)
        popup.adjustSize()

        above = self.mapToGlobal(QPoint(0, -popup.height() - 8))
        below = self.mapToGlobal(QPoint(0, self.height() + 8))
        pos = below if below.y() + popup.height() <= avail.bottom() else above
        if pos.x() + popup.width() > avail.right():
            pos.setX(max(avail.left(), avail.right() - popup.width()))
        if pos.y() < avail.top():
            pos.setY(avail.top())
        popup.move(pos)
        popup.show()
        self._popup = popup
        popup.destroyed.connect(lambda *_: setattr(self, "_popup", None))


class LineageTwoRowView(QFrame):
    node_activated = Signal(str, int)
    goto_current_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._parent_widgets: dict[tuple[str, int], _LineageNodeThumb] = {}
        self._child_widgets: dict[tuple[str, int], _LineageNodeThumb] = {}
        self._anims: list[QPropertyAnimation] = []
        self.setObjectName("lineageTwoRowView")
        row_h = _STRIP_THUMB + 8
        self._compact_height = (row_h * 2) + 10
        self.setFixedHeight(self._compact_height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        root = QGridLayout(self)
        root.setContentsMargins(4, 3, 4, 3)
        root.setHorizontalSpacing(4)
        root.setVerticalSpacing(2)
        self._left_btn = self._make_scroll_button("◀")
        self._right_btn = self._make_scroll_button("▶")
        self._up_btn = self._make_scroll_button("▲")
        self._down_btn = self._make_scroll_button("▼")
        self._left_btn.clicked.connect(lambda: self._step_scroll_both(-260))
        self._right_btn.clicked.connect(lambda: self._step_scroll_both(260))
        self._up_btn.clicked.connect(lambda: self._step_row_to_current(self._parent_row, self._parent_widgets))
        self._down_btn.clicked.connect(lambda: self._step_row_to_current(self._child_row, self._child_widgets))

        self._goto_btn = QToolButton()
        self._goto_btn.setText("◎")
        self._goto_btn.setFixedSize(24, 22)
        self._goto_btn.setToolTip(tr("editor.lineage_strip_goto_tooltip"))
        self._goto_btn.clicked.connect(self.goto_current_requested.emit)

        self._parent_row, self._parent_lay = self._make_row(row_h)
        self._child_row, self._child_lay = self._make_row(row_h)
        rows = QWidget()
        rows_lay = QVBoxLayout(rows)
        rows_lay.setContentsMargins(0, 0, 0, 0)
        rows_lay.setSpacing(2)
        rows_lay.addWidget(self._parent_row)
        rows_lay.addWidget(self._child_row)
        root.addWidget(self._up_btn, 0, 1, Qt.AlignmentFlag.AlignHCenter)
        root.addWidget(self._goto_btn, 0, 2, Qt.AlignmentFlag.AlignRight)
        root.addWidget(self._left_btn, 1, 0)
        root.addWidget(rows, 1, 1)
        root.addWidget(self._right_btn, 1, 2)
        root.addWidget(self._down_btn, 2, 1, Qt.AlignmentFlag.AlignHCenter)
        root.setColumnStretch(1, 1)
        root.setRowStretch(1, 1)
        self._restyle()
        self.hide()

    def _make_scroll_button(self, text: str) -> QToolButton:
        btn = QToolButton()
        btn.setText(text)
        btn.setFixedSize(24, 22)
        return btn

    def _make_row(self, row_h: int) -> tuple[QScrollArea, QHBoxLayout]:
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFixedHeight(row_h)
        area.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.viewport().installEventFilter(self)
        holder = QWidget()
        holder.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        lay = QHBoxLayout(holder)
        lay.setContentsMargins(28, 1, 28, 1)
        lay.setSpacing(6)
        lay.addStretch(1)
        lay.addStretch(1)
        area.setWidget(holder)
        return area, lay

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.Wheel:
            area = None
            if obj is self._parent_row.viewport():
                area = self._parent_row
            elif obj is self._child_row.viewport():
                area = self._child_row
            if area is not None:
                delta = event.angleDelta().x() or event.angleDelta().y()
                self._smooth_scroll(area, -delta)
                event.accept()
                return True
        return super().eventFilter(obj, event)

    def _restyle(self) -> None:
        self.setStyleSheet(
            f"QFrame#lineageTwoRowView {{ background: {SURFACE0}; border-top: 1px solid {SURFACE2}; }}"
            f"QScrollArea {{ background: transparent; border: none; }}"
            f"QToolButton {{ background: {SURFACE1}; color: {ACCENT}; border: 1px solid {ACCENT}; border-radius: 3px; }}"
            f"QToolButton:hover {{ background: {SURFACE2}; }}"
        )

    def set_rows(self, parents: list[dict], children: list[dict], current: tuple[str, int] | None) -> None:
        self._current = current
        self._clear_layout(self._parent_lay)
        self._clear_layout(self._child_lay)
        self._parent_widgets = self._fill_row(self._parent_lay, parents, current)
        self._child_widgets = self._fill_row(self._child_lay, children, current)
        has_any = bool(parents or children)
        self.setVisible(has_any)
        if has_any:
            QTimer.singleShot(0, self.scroll_to_current)
        self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        current = getattr(self, "_current", None)
        if current is None or current not in self._parent_widgets or not self._child_widgets:
            return
        parent_widget = self._parent_widgets[current]
        start = parent_widget.mapTo(self, parent_widget.rect().center())
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor(GREEN), 2))
        start_y = parent_widget.mapTo(self, QPoint(parent_widget.width() // 2, parent_widget.height())).y()
        split_y = self._child_row.mapTo(self, QPoint(0, 0)).y() - 2
        painter.drawLine(start.x(), start_y, start.x(), split_y)
        for child_widget in self._child_widgets.values():
            end = child_widget.mapTo(self, QPoint(child_widget.width() // 2, 0))
            painter.drawLine(start.x(), split_y, end.x(), split_y)
            painter.drawLine(end.x(), split_y, end.x(), end.y())
        painter.end()

    def _fill_row(self, lay: QHBoxLayout, rows: list[dict], current: tuple[str, int] | None) -> dict[tuple[str, int], _LineageNodeThumb]:
        widgets: dict[tuple[str, int], _LineageNodeThumb] = {}
        for row in rows:
            key = (str(row["history_db"]), int(row["history_id"]))
            w = _LineageNodeThumb(
                key[0],
                key[1],
                row.get("thumbnail_data"),
                row.get("preview_pixmap"),
                key == current,
            )
            w.double_clicked.connect(self.node_activated.emit)
            lay.insertWidget(max(0, lay.count() - 1), w)
            widgets[key] = w
        return widgets

    @staticmethod
    def _clear_layout(lay: QHBoxLayout) -> None:
        while lay.count() > 2:
            item = lay.takeAt(1)
            if item and item.widget():
                item.widget().deleteLater()

    def scroll_to_current(self) -> None:
        for area, widgets in ((self._parent_row, self._parent_widgets), (self._child_row, self._child_widgets)):
            for widget in widgets.values():
                if widget.objectName() == "currentLineageThumb":
                    self._smooth_center(area, widget)
                    return

    def _scroll_row_to_current(self, area: QScrollArea, widgets: dict[tuple[str, int], _LineageNodeThumb]) -> None:
        for widget in widgets.values():
            if widget.objectName() == "currentLineageThumb":
                self._smooth_center(area, widget)
                return
        first = next(iter(widgets.values()), None)
        if first is not None:
            self._smooth_center(area, first)

    def _step_row_to_current(self, area: QScrollArea, widgets: dict[tuple[str, int], _LineageNodeThumb]) -> None:
        for widget in widgets.values():
            if widget.objectName() == "currentLineageThumb":
                self._step_center(area, widget)
                return
        first = next(iter(widgets.values()), None)
        if first is not None:
            self._step_center(area, first)

    def _scroll_both(self, delta: int) -> None:
        self._smooth_scroll(self._parent_row, delta)
        self._smooth_scroll(self._child_row, delta)

    def _step_scroll_both(self, delta: int) -> None:
        self._step_scroll(self._parent_row, delta)
        self._step_scroll(self._child_row, delta)

    def _step_scroll(self, area: QScrollArea, delta: int) -> None:
        bar = area.horizontalScrollBar()
        target = max(bar.minimum(), min(bar.maximum(), bar.value() + delta))
        bar.setValue(target)

    def _step_center(self, area: QScrollArea, widget: QWidget) -> None:
        bar = area.horizontalScrollBar()
        target = widget.x() + widget.width() // 2 - area.viewport().width() // 2
        bar.setValue(max(bar.minimum(), min(bar.maximum(), target)))

    def _smooth_center(self, area: QScrollArea, widget: QWidget) -> None:
        bar = area.horizontalScrollBar()
        target = widget.x() + widget.width() // 2 - area.viewport().width() // 2
        target = max(bar.minimum(), min(bar.maximum(), target))
        self._animate_bar(bar, target)

    def _smooth_scroll(self, area: QScrollArea, delta: int) -> None:
        bar = area.horizontalScrollBar()
        target = max(bar.minimum(), min(bar.maximum(), bar.value() + int(delta * 0.9)))
        self._animate_bar(bar, target)

    def _animate_bar(self, bar, target: int) -> None:
        anim = QPropertyAnimation(bar, b"value", self)
        anim.setDuration(180)
        anim.setStartValue(bar.value())
        anim.setEndValue(target)
        anim.finished.connect(lambda: self._anims.remove(anim) if anim in self._anims else None)
        self._anims.append(anim)
        anim.start()


class LineageParentCard(QFrame):
    """親ノード表示カード（サムネ＋#番号）。クリックで clicked を emit する。"""

    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = "none"
        self._clickable = False

        self.setFixedHeight(_THUMB + 8)
        self.setFrameShape(QFrame.Shape.NoFrame)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 2, 8, 2)
        lay.setSpacing(6)

        self._thumb = QLabel()
        self._thumb.setFixedSize(_THUMB, _THUMB)
        self._thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._thumb)

        self._line1 = QLabel()  # #番号
        self._line1.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self._line1.installEventFilter(self)
        lay.addWidget(self._line1)

        self.set_none()

    # ── 状態セッター ─────────────────────────────────────

    def set_none(self) -> None:
        """系譜なし（現在ノード未設定）。"""
        self._state = "none"
        self._clickable = False
        self._thumb.setPixmap(QPixmap())
        self._thumb.setText("—")
        self._line1.setText(tr("editor.lineage_none"))
        self.setToolTip(tr("editor.lineage_none_tooltip"))
        self._restyle()

    def set_root(self) -> None:
        """現在ノードが開祖（親なし）。"""
        self._state = "root"
        self._clickable = False
        self._thumb.setPixmap(QPixmap())
        self._thumb.setText("◆")
        self._line1.setText(tr("editor.lineage_root"))
        self.setToolTip(tr("editor.lineage_root_tooltip"))
        self._restyle()

    def set_missing(self, gen_no: int) -> None:
        """親の記録が見つからない。"""
        self._state = "missing"
        self._clickable = False
        self._thumb.setPixmap(QPixmap())
        self._thumb.setText("?")
        self._line1.setText(f"#{gen_no}")
        self.setToolTip(tr("editor.lineage_parent_missing"))
        self._restyle()

    def set_parent(self, gen_no: int, thumb_bytes: bytes | None) -> None:
        """親情報を表示する（クリック可）。"""
        self._state = "parent"
        self._clickable = True
        _set_thumb(self._thumb, thumb_bytes)
        self._line1.setText(f"#{gen_no}")
        self.setToolTip(tr("editor.lineage_parent_tooltip"))
        self._restyle()

    # ── 見た目 ───────────────────────────────────────────

    def _restyle(self) -> None:
        active = self._state == "parent"
        self._line1.setFont(ui_font(-1, bold=True))
        self._line1.setStyleSheet(f"color: {ACCENT if active else SUBTEXT}; background: transparent;")
        self._thumb.setStyleSheet(
            f"background: {SURFACE0}; color: {SUBTEXT}; "
            f"border: 1px solid {SURFACE2}; border-radius: 3px;"
        )
        hover = f"QFrame:hover {{ border-color: {ACCENT}; background: {SURFACE2}; }}" if active else ""
        self.setStyleSheet(
            f"LineageParentCard {{ background: {SURFACE1}; "
            f"border: 1px solid {SURFACE2}; border-radius: 4px; }} {hover}"
        )
        self.setCursor(
            Qt.CursorShape.PointingHandCursor if active else Qt.CursorShape.ArrowCursor
        )

    def retranslate(self) -> None:
        """言語切替時に表示文字列を更新する（データ系状態は呼び出し側が再セット）。"""
        if self._state == "none":
            self.set_none()
        elif self._state == "root":
            self.set_root()
        elif self._state == "parent":
            self.setToolTip(tr("editor.lineage_parent_tooltip"))
            self._restyle()

    # ── イベント ─────────────────────────────────────────

    def mouseReleaseEvent(self, event) -> None:
        if self._clickable and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class LineageHeirCard(QFrame):
    """
    継承権者（現在ノード）表示カード。

    レイアウト: [◀][サムネ][#番号][▶]
    ◀▶ は常に同じ幅を占有し、無効時はディム表示になるだけなので
    サムネ・番号の位置は切替候補の有無で左右に動かない。
    """

    prev_requested = Signal()
    next_requested = Signal()

    # 移動アニメーション: 1秒で一回転を3秒間（目立たせる）
    _GLOW_DURATION_MS = 3000
    _GLOW_ROTATION_MS = 1000

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rank = 0
        self._total = 1
        self._preview_pix = QPixmap()
        self._preview_popup: QFrame | None = None
        # 履歴マップからの「現在位置に設定」を知らせる回転発光
        self._glow_clock = QElapsedTimer()
        self._glow_timer = QTimer(self)
        self._glow_timer.setInterval(33)
        self._glow_timer.timeout.connect(self._on_glow_tick)

        self.setFixedHeight(_THUMB + 8)
        self.setFrameShape(QFrame.Shape.NoFrame)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(4)

        self._prev_btn = QToolButton()
        self._prev_btn.setText("◀")
        self._prev_btn.clicked.connect(self.prev_requested.emit)
        lay.addWidget(self._prev_btn)

        self._thumb = QLabel()
        self._thumb.setFixedSize(_THUMB, _THUMB)
        self._thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb.installEventFilter(self)
        lay.addWidget(self._thumb)

        self._line1 = QLabel()  # #番号
        self._line1.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        lay.addWidget(self._line1)

        self._next_btn = QToolButton()
        self._next_btn.setText("▶")
        self._next_btn.clicked.connect(self.next_requested.emit)
        lay.addWidget(self._next_btn)

        for btn in (self._prev_btn, self._next_btn):
            btn.setFixedSize(18, _THUMB)
            btn.setToolTip(tr("editor.lineage_heir_switch_tooltip"))
            btn.setStyleSheet(
                f"QToolButton {{ background: transparent; color: {ACCENT}; "
                f"border: none; {EMOJI_ICON_SS} }}"
                f"QToolButton:hover {{ background: {SURFACE2}; }}"
                f"QToolButton:disabled {{ color: {SURFACE2}; }}"
            )

        self._restyle()
        self.set_none()

    # ── 移動アニメーション（履歴マップからの現在位置設定の合図）──────

    def play_move_glow(self) -> None:
        """カード枠に回転発光を3秒間表示する（1秒で一回転）。"""
        self._glow_clock.start()
        self._glow_timer.start()
        self.update()

    def _on_glow_tick(self) -> None:
        if self._glow_clock.elapsed() >= self._GLOW_DURATION_MS:
            self._glow_timer.stop()
        self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if not self._glow_timer.isActive():
            return
        elapsed = self._glow_clock.elapsed()
        angle = (elapsed % self._GLOW_ROTATION_MS) / self._GLOW_ROTATION_MS * 360.0
        rect = self.rect().adjusted(2, 2, -2, -2)
        base = QColor(ACCENT)
        bright = QColor(ACCENT).lighter(170)
        grad = QConicalGradient(rect.center(), -angle)
        grad.setColorAt(0.00, base)
        grad.setColorAt(0.25, bright)
        grad.setColorAt(0.50, base)
        grad.setColorAt(0.75, bright)
        grad.setColorAt(1.00, base)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QBrush(grad), 3))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, 6, 6)
        painter.end()

    # ── 状態セッター ─────────────────────────────────────

    def set_none(self) -> None:
        """継承権者なし（現在ノード未設定）。カードごと隠す。"""
        self.hide()

    def set_heir(
        self,
        gen_no: int,
        thumb_bytes: bytes | None,
        rank: int,
        total: int,
        preview_pixmap: QPixmap | None = None,
    ) -> None:
        """
        継承権者を表示する。

        rank:  兄弟内の順位（0始まり。0 = 継承権第一位 = 最小の#番号）
        total: 兄弟の総数
        """
        self._rank = rank
        self._total = total
        _set_thumb(self._thumb, thumb_bytes)
        self._preview_pix = QPixmap(preview_pixmap) if preview_pixmap and not preview_pixmap.isNull() else QPixmap()
        if self._preview_pix.isNull() and thumb_bytes:
            self._preview_pix.loadFromData(bytes(thumb_bytes))
        self._line1.setText(f"#{gen_no}")
        self._line1.setFont(ui_font(-1, bold=True))
        self._prev_btn.setEnabled(rank > 0)
        self._next_btn.setEnabled(rank < total - 1)
        self.setToolTip(tr("editor.lineage_heir_tooltip", rank=rank + 1, total=total))
        self.show()

    # ── 見た目 ───────────────────────────────────────────

    def _restyle(self) -> None:
        self._line1.setStyleSheet(f"color: {TEXT}; background: transparent;")
        self._thumb.setStyleSheet(
            f"background: {SURFACE0}; color: {SUBTEXT}; "
            f"border: 1px solid {ACCENT}; border-radius: 3px;"
        )
        self.setStyleSheet(
            f"LineageHeirCard {{ background: {SURFACE1}; "
            f"border: 1px solid {SURFACE2}; border-radius: 4px; }}"
        )

    def retranslate(self) -> None:
        """言語切替時にツールチップを更新する。"""
        for btn in (self._prev_btn, self._next_btn):
            btn.setToolTip(tr("editor.lineage_heir_switch_tooltip"))
        if self.isVisible():
            self.setToolTip(
                tr("editor.lineage_heir_tooltip", rank=self._rank + 1, total=self._total)
            )

    # ── サムネクリック一時プレビュー ───────────────────────

    def eventFilter(self, obj, event) -> bool:
        if obj in (getattr(self, "_thumb", None), getattr(self, "_line1", None)) and event.type() == QEvent.Type.MouseButtonRelease:
            if event.button() == Qt.MouseButton.LeftButton:
                self._show_temporary_preview()
                event.accept()
                return True
        return super().eventFilter(obj, event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._show_temporary_preview()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _show_temporary_preview(self) -> None:
        if self._preview_pix.isNull():
            return
        if self._preview_popup is not None:
            self._preview_popup.close()
            self._preview_popup = None

        pix = self._preview_pix
        target_w = max(_THUMB * 3, pix.width() // 2)
        target_h = max(_THUMB * 3, pix.height() // 2)

        screen = QApplication.screenAt(self._thumb.mapToGlobal(self._thumb.rect().center()))
        avail = screen.availableGeometry() if screen is not None else QApplication.primaryScreen().availableGeometry()
        max_w = max(220, int(avail.width() * 0.72))
        max_h = max(220, int(avail.height() * 0.72))
        scaled = pix.scaled(
            min(target_w, max_w),
            min(target_h, max_h),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

        popup = QFrame(None, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        popup.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        popup.setStyleSheet(
            f"QFrame {{ background: {SURFACE0}; border: 1px solid {ACCENT}; border-radius: 4px; }}"
        )
        lay = QVBoxLayout(popup)
        lay.setContentsMargins(6, 6, 6, 6)
        label = QLabel()
        label.setPixmap(scaled)
        label.setFixedSize(scaled.size())
        lay.addWidget(label)
        popup.adjustSize()

        pos = self._thumb.mapToGlobal(QPoint(0, self._thumb.height() + 6))
        if pos.x() + popup.width() > avail.right():
            pos.setX(max(avail.left(), avail.right() - popup.width()))
        if pos.y() + popup.height() > avail.bottom():
            pos.setY(max(avail.top(), self._thumb.mapToGlobal(QPoint(0, 0)).y() - popup.height() - 6))
        popup.move(pos)
        popup.show()
        self._preview_popup = popup
        popup.destroyed.connect(lambda *_: setattr(self, "_preview_popup", None))
        QTimer.singleShot(_HEIR_PREVIEW_MS, popup.close)
