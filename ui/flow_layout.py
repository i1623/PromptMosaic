"""
タイルを折り返し表示するFlowLayout。
PySide6標準にはないため自前実装。
"""
from PySide6.QtWidgets import QLayout, QWidget, QWidgetItem
from PySide6.QtCore import Qt, QRect, QSize, QPoint


class FlowLayout(QLayout):
    def __init__(self, parent=None, h_spacing: int = 4, v_spacing: int = 4):
        super().__init__(parent)
        self._h_spacing = h_spacing
        self._v_spacing = v_spacing
        self._items: list[QWidgetItem] = []

    # ── QLayout 必須メソッド ──────────────────────────────

    def addItem(self, item):
        self._items.append(item)

    def insertWidget(self, index: int, widget: QWidget) -> None:
        index = max(0, min(index, len(self._items)))
        self.addChildWidget(widget)
        self._items.insert(index, QWidgetItem(widget))
        self.invalidate()

    def insertItem(self, index: int, item: QWidgetItem) -> None:
        index = max(0, min(index, len(self._items)))
        self._items.insert(index, item)
        self.invalidate()

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), dry_run=True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect, dry_run=False)

    def sizeHint(self) -> QSize:
        # 現在の幅が分かっている場合は正確な折り返し高さを返す
        w = self.geometry().width()
        if w > 0:
            return QSize(w, self.heightForWidth(w))
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    # ── 内部実装 ─────────────────────────────────────────

    def _do_layout(self, rect: QRect, dry_run: bool) -> int:
        m = self.contentsMargins()
        r = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y = r.x(), r.y()
        line_height = 0

        for item in self._items:
            w = item.sizeHint().width()
            h = item.sizeHint().height()
            next_x = x + w + self._h_spacing

            if next_x - self._h_spacing > r.right() and line_height > 0:
                x = r.x()
                y += line_height + self._v_spacing
                next_x = x + w + self._h_spacing
                line_height = 0

            if not dry_run:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))

            x = next_x
            line_height = max(line_height, h)

        return y + line_height - rect.y() + m.bottom()
