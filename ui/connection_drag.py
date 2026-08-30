"""Connection-handle drag UI shared by word and group widgets."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from ui.styles import ACCENT, RED, SURFACE0, TEXT, ui_font


class ConnectionHandle(QPushButton):
    """A compact handle: click for actions, drag to create/reorder a connection."""

    def __init__(
        self,
        owner: QWidget,
        *,
        readonly: bool = False,
        can_start: bool = True,
        parent=None,
    ):
        super().__init__("🔗", parent or owner)
        self.owner = owner
        self.can_start = bool(can_start)
        self._press_pos: QPoint | None = None
        self._dragging = False
        self.setFixedSize(26, 18)
        self.setFont(ui_font(-3))
        self.setToolTip("ドラッグして接続／クリックして接続操作")
        self.setStyleSheet(self._style("normal"))
        self.setEnabled(not readonly)

    @staticmethod
    def _style(state: str) -> str:
        border = ACCENT
        bg = SURFACE0
        color = TEXT
        if state == "valid":
            bg = "#24452f"
            border = "#a6e3a1"
        elif state == "invalid":
            bg = "#4a252c"
            border = RED
        return (
            f"QPushButton {{ background: {bg}; color: {color}; border: 1px solid {border}; "
            "border-radius: 3px; padding: 0; }"
            f"QPushButton:hover {{ border-color: #cdd6f4; }}"
        )

    def set_drop_state(self, state: str, *, after: bool = False) -> None:
        self.setText("◀◆" if state == "valid" and not after else "◆▶" if state == "valid" else "🔗")
        self.setStyleSheet(self._style(state))

    def _block_widget(self):
        widget = self.owner
        while widget is not None:
            if hasattr(widget, "_start_connection_drag") and hasattr(widget, "block"):
                return widget
            widget = widget.parentWidget()
        return None

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            if not self.can_start:
                event.accept()
                return
            self._press_pos = event.position().toPoint()
            self._dragging = False
            self.grabMouse()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._press_pos is None:
            super().mouseMoveEvent(event)
            return
        if not self._dragging:
            distance = (event.position().toPoint() - self._press_pos).manhattanLength()
            if distance >= QApplication.startDragDistance():
                block = self._block_widget()
                if block is None:
                    return
                self._dragging = True
                block._start_connection_drag(self.owner, self)
        if self._dragging:
            block = self._block_widget()
            if block is not None:
                block._update_connection_drag(self.owner, self, event.globalPosition().toPoint())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._press_pos is None:
            super().mouseReleaseEvent(event)
            return
        try:
            self.releaseMouse()
        except Exception:
            pass
        block = self._block_widget()
        global_pos = event.globalPosition().toPoint()
        if self._dragging:
            if block is not None:
                block._finish_connection_drag(self.owner, self, global_pos)
        elif block is not None:
            block._show_connection_menu(self.owner, global_pos)
        self._press_pos = None
        self._dragging = False
        event.accept()


class ConnectionCurveOverlay(QWidget):
    """Temporary ComfyUI-style Bezier cable shown only while dragging."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self._start = QPointF()
        self._end = QPointF()
        self._valid = True
        self._label = ""
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.hide()

    def show_curve(self, start: QPoint, end: QPoint, *, valid: bool, label: str = "") -> None:
        self._start = QPointF(start)
        self._end = QPointF(end)
        self._valid = valid
        self._label = label
        self.setGeometry(self.parentWidget().rect())
        self.raise_()
        self.show()
        self.update()

    def clear(self) -> None:
        self.hide()
        self._label = ""

    def paintEvent(self, event) -> None:
        if not self.isVisible():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(ACCENT if self._valid else RED)
        painter.setPen(QPen(color, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        span = max(56.0, abs(self._end.x() - self._start.x()) * 0.48)
        path = QPainterPath(self._start)
        path.cubicTo(
            QPointF(self._start.x() + span, self._start.y()),
            QPointF(self._end.x() - span, self._end.y()),
            self._end,
        )
        painter.drawPath(path)
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(self._start, 4, 4)
        painter.drawEllipse(self._end, 4, 4)
        if self._label:
            mid = path.pointAtPercent(0.5)
            metrics = painter.fontMetrics()
            rect = metrics.boundingRect(self._label).adjusted(-6, -3, 6, 3)
            rect.moveCenter(mid.toPoint())
            painter.setBrush(QColor(SURFACE0))
            painter.setPen(QPen(color, 1))
            painter.drawRoundedRect(rect, 4, 4)
            painter.setPen(QColor(TEXT))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._label)
