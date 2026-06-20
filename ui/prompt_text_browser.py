"""
文章プロンプトブラウザ — 左ペイン「文章」タブ

・全件表示 / キーワード検索（partial / AND / OR）
・一覧アイテム: ラベルテキスト + メモ + 星評価
・星クリックで評価変更（同じ星をクリックでリセット）
・ダブルクリックで中央ペインへ追加要求シグナル発行
"""
from __future__ import annotations

from uuid import uuid4

from PySide6.QtCore import Qt, QSize, Signal, QPoint, QMimeData, QTimer
from PySide6.QtGui import QPixmap, QPainter, QColor, QPen, QDrag, QDragEnterEvent, QDropEvent, QBrush
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QToolButton, QLabel, QPushButton,
    QTreeWidget, QTreeWidgetItem,
    QStyledItemDelegate, QStyleOptionViewItem, QStyle,
    QApplication, QMessageBox, QInputDialog, QMenu,
)

import db.app_db as _app_db
import db.library_db as _db
from db.prompt_text_db import (
    get_all_prompt_texts, search_prompt_texts, update_prompt_text,
    insert_prompt_text,
)
from core.i18n import tr
from ui.styles import (
    SURFACE0, SURFACE1, SURFACE2, TEXT, SUBTEXT, YELLOW,
    tag_browser_chip_colors, ui_font,
)

# 文章プロンプト D&D の MIME タイプ（block_widget.py と共有）
PROMPT_TEXT_MIME = "application/x-prompt-text-id"


# ── 定数 ─────────────────────────────────────────────────────────────────────

_TEXT_PAD_X = 8
_ITEM_H     = 58
_STAR_FILLED = "★"
_STAR_EMPTY  = "☆"

_ROLE_ID     = Qt.ItemDataRole.UserRole + 1
_ROLE_RATING = Qt.ItemDataRole.UserRole + 2
_ROLE_MEMO   = Qt.ItemDataRole.UserRole + 3
_ROLE_DISPLAY_LABEL = Qt.ItemDataRole.UserRole + 4
_ROLE_KIND = Qt.ItemDataRole.UserRole + 5
_ROLE_CATEGORY_KEY = Qt.ItemDataRole.UserRole + 6
_KIND_CATEGORY = "category"
_KIND_ITEM = "item"
_CATEGORY_H = 28


# ── ヘルパー ──────────────────────────────────────────────────────────────────

def _show_nsfw() -> bool:
    row = _app_db.fetchone("SELECT value FROM app_settings WHERE key='show_nsfw'")
    return bool(int(row["value"])) if row else False


# ── デリゲート ────────────────────────────────────────────────────────────────

class _PromptTextDelegate(QStyledItemDelegate):
    """ラベル・メモ・星評価をカスタム描画するデリゲート。"""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        if index.data(_ROLE_KIND) == _KIND_CATEGORY:
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            r = option.rect.adjusted(2, 3, -2, -3)
            painter.setPen(QPen(QColor(SURFACE2)))
            painter.setBrush(QBrush(QColor(SURFACE1)))
            painter.drawRoundedRect(r, 4, 4)
            painter.setPen(QColor(TEXT))
            label = index.data(Qt.ItemDataRole.DisplayRole) or ""
            painter.drawText(
                r.adjusted(8, 0, -8, 0),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                option.fontMetrics.elidedText(label, Qt.TextElideMode.ElideRight, r.width() - 16),
            )
            painter.restore()
            return

        painter.save()

        # 中央ペインの文章プロンプトタイルと同系色で表示する。
        item_bg, item_fg, item_border = tag_browser_chip_colors("natural_feature")
        if option.state & QStyle.StateFlag.State_Selected:
            bg = QColor(item_bg).lighter(112)
        else:
            bg = QColor(item_bg)

        r = option.rect.adjusted(2, 2, -2, -2)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(item_border)))
        painter.setBrush(QBrush(bg))
        painter.drawRoundedRect(r, 4, 4)
        x, y, h = r.x(), r.y(), r.height()

        # テキスト領域
        text_x = x + _TEXT_PAD_X
        text_w = r.width() - (_TEXT_PAD_X * 2)
        fm = option.fontMetrics

        # 1行目: 一行メモ
        label = index.data(Qt.ItemDataRole.DisplayRole) or ""
        elided = fm.elidedText(label, Qt.TextElideMode.ElideRight, text_w)
        painter.setPen(QColor(item_fg))
        painter.drawText(
            text_x, y + 6, text_w, fm.height(),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
            elided,
        )

        # 2行目: メモを1行だけ表示
        memo = index.data(_ROLE_MEMO) or ""
        memo_elided = fm.elidedText(memo, Qt.TextElideMode.ElideRight, text_w)
        painter.setPen(QColor(SUBTEXT))
        painter.drawText(
            text_x, y + 6 + fm.lineSpacing(), text_w, fm.height(),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
            memo_elided,
        )

        # 3行目: 星評価
        rating = index.data(_ROLE_RATING) or 0
        stars = _STAR_FILLED * rating + _STAR_EMPTY * (5 - rating)
        painter.setPen(QColor(YELLOW))
        star_y = y + 6 + fm.lineSpacing() * 2
        painter.drawText(
            text_x, star_y, text_w, fm.height(),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
            stars,
        )

        painter.restore()

    def sizeHint(self, option, index) -> QSize:
        if index.data(_ROLE_KIND) == _KIND_CATEGORY:
            return QSize(0, _CATEGORY_H)
        return QSize(0, max(_ITEM_H, option.fontMetrics.lineSpacing() * 3 + 12))


# ── 星クリック対応リスト ──────────────────────────────────────────────────────

class _PromptList(QTreeWidget):
    """星評価行へのクリックを検出して star_click シグナルを発行する。"""

    star_click = Signal(object, int)  # (item, new_rating 1-5)
    label_edit_requested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_start_pos: QPoint | None = None

    def mousePressEvent(self, event) -> None:
        self._drag_start_pos = None
        if event.button() == Qt.MouseButton.LeftButton:
            item = self.itemAt(event.pos())
            if item and item.data(0, _ROLE_KIND) == _KIND_ITEM:
                rect = self.visualItemRect(item)
                fm = self.fontMetrics()
                # 星行のY範囲（3行目）
                star_y_top = rect.y() + 6 + fm.lineSpacing() * 2 - 2
                star_y_bottom = star_y_top + fm.height() + 6
                if star_y_top <= event.pos().y() <= star_y_bottom:
                    text_x = rect.x() + _TEXT_PAD_X
                    rel_x = event.pos().x() - text_x
                    star_w = max(1, fm.horizontalAdvance(_STAR_FILLED))
                    # 星5個分の幅の外はクリック無効
                    if rel_x < 0 or rel_x > star_w * 5:
                        return
                    new_rating = min(5, max(1, rel_x // star_w + 1))
                    self.star_click.emit(item, int(new_rating))
                    return
            self._drag_start_pos = event.pos()
        super().mousePressEvent(event)

    def _label_rect(self, item: QTreeWidgetItem):
        rect = self.visualItemRect(item)
        fm = self.fontMetrics()
        from PySide6.QtCore import QRect
        return QRect(
            rect.x() + _TEXT_PAD_X,
            rect.y() + 4,
            max(1, rect.width() - (_TEXT_PAD_X * 2)),
            fm.height() + 4,
        )

    def memo_rect(self, item: QTreeWidgetItem):
        rect = self.visualItemRect(item)
        fm = self.fontMetrics()
        from PySide6.QtCore import QRect
        return QRect(
            rect.x() + _TEXT_PAD_X,
            rect.y() + 4 + fm.lineSpacing(),
            max(1, rect.width() - (_TEXT_PAD_X * 2)),
            fm.height() + 4,
        )

    def mouseMoveEvent(self, event) -> None:
        if (
            event.buttons() & Qt.MouseButton.LeftButton
            and self._drag_start_pos is not None
            and (event.pos() - self._drag_start_pos).manhattanLength()
            >= QApplication.startDragDistance()
        ):
            item = self.itemAt(self._drag_start_pos)
            self._drag_start_pos = None
            if item and item.data(0, _ROLE_KIND) == _KIND_ITEM:
                prompt_text_id = item.data(0, _ROLE_ID)
                if prompt_text_id is not None:
                    drag = QDrag(self)
                    mime = QMimeData()
                    mime.setData(PROMPT_TEXT_MIME, str(prompt_text_id).encode())
                    drag.setMimeData(mime)
                    pixmap, hotspot = self._drag_pixmap_for_item(item)
                    if not pixmap.isNull():
                        drag.setPixmap(pixmap)
                        drag.setHotSpot(hotspot)
                    drag.exec(Qt.DropAction.CopyAction)
            return
        super().mouseMoveEvent(event)

    def _drag_pixmap_for_item(self, item: QTreeWidgetItem) -> tuple[QPixmap, QPoint]:
        from ui.drag_pixmap import translucent_drag_pixmap

        rect = self.visualItemRect(item).intersected(self.viewport().rect())
        if rect.isEmpty():
            return QPixmap(), QPoint(0, 0)
        pixmap = self.viewport().grab(rect)
        hotspot = self._drag_start_pos - rect.topLeft() if self._drag_start_pos is not None else rect.center() - rect.topLeft()
        hotspot.setX(max(0, min(hotspot.x(), rect.width() - 1)))
        hotspot.setY(max(0, min(hotspot.y(), rect.height() - 1)))
        return translucent_drag_pixmap(pixmap), hotspot


# ── メインウィジェット ────────────────────────────────────────────────────────

class PromptTextBrowser(QWidget):
    """
    文章プロンプトブラウザ。

    Signals:
        item_double_clicked(prompt_text_id): ダブルクリックで中央ペインへ追加要求
        rating_changed(prompt_text_id, new_rating): 星クリックで評価変更
    """

    item_double_clicked = Signal(int)   # prompt_text_id
    rating_changed      = Signal(int, int)  # prompt_text_id, new_rating

    def __init__(self, parent=None):
        super().__init__(parent)
        self._search_mode = "partial"
        self._edit_item: QTreeWidgetItem | None = None
        self._visible_count = 0
        self._build_ui()
        self.setAcceptDrops(True)
        self.refresh()

    # ── UI構築 ────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        self._title_label = QLabel(tr("prompt_text_browser.title"))
        self._title_label.setFont(ui_font(bold=True))
        self._title_label.setStyleSheet(f"color: {TEXT}; padding: 2px 4px;")
        root.addWidget(self._title_label)

        # 検索バー
        bar = QHBoxLayout()
        bar.setSpacing(4)

        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText(tr("prompt_text_browser.search_placeholder"))
        self._search_edit.setFixedHeight(24)
        self._search_edit.setFont(ui_font(-1))
        self._search_edit.setStyleSheet(
            f"QLineEdit {{ background: {SURFACE0}; color: {TEXT}; "
            f"border: 1px solid {SURFACE2}; border-radius: 3px; padding: 0 4px; }}"
        )
        self._search_edit.returnPressed.connect(self._on_search_or)
        self._search_edit.textChanged.connect(
            lambda t: self._clear_btn.setVisible(bool(t))
        )
        self._search_edit.textChanged.connect(lambda _t: self._on_search_or())
        bar.addWidget(self._search_edit, stretch=1)

        self._and_btn = QPushButton(tr("prompt_text_browser.and_btn"))
        self._and_btn.setToolTip(tr("prompt_text_browser.and_tooltip"))
        self._and_btn.setFixedWidth(42)
        self._and_btn.setStyleSheet(
            f"QPushButton {{ background: {SURFACE1}; color: {SUBTEXT}; "
            f"border: 1px solid {SURFACE2}; border-radius: 3px; padding: 1px 4px; }}"
            f"QPushButton:hover {{ color: {YELLOW}; border-color: {YELLOW}; }}"
        )
        self._and_btn.clicked.connect(self._on_search_and)
        bar.addWidget(self._and_btn)
        self._and_btn.hide()

        self._or_btn = QPushButton(tr("prompt_text_browser.or_btn"))
        self._or_btn.setToolTip(tr("prompt_text_browser.or_tooltip"))
        self._or_btn.setFixedWidth(36)
        self._or_btn.setStyleSheet(self._and_btn.styleSheet())
        self._or_btn.clicked.connect(self._on_search_or)
        bar.addWidget(self._or_btn)
        self._or_btn.hide()

        self._clear_btn = QToolButton()
        self._clear_btn.setText("×")
        self._clear_btn.setToolTip(tr("prompt_text_browser.search_clear_tooltip"))
        self._clear_btn.setFixedSize(24, 24)
        self._clear_btn.hide()
        self._clear_btn.clicked.connect(self._on_clear)
        bar.addWidget(self._clear_btn)

        root.addLayout(bar)
        self._search_edit.hide()
        self._clear_btn.hide()

        # 件数ラベル
        self._count_label = QLabel()
        self._count_label.setFont(ui_font(-2))
        self._count_label.setStyleSheet(f"color: {SUBTEXT};")
        root.addWidget(self._count_label)

        # 一覧
        self._list = _PromptList(self)
        self._list.setHeaderHidden(True)
        self._list.setRootIsDecorated(True)
        self._list.setItemDelegate(_PromptTextDelegate())
        self._list.setStyleSheet(
            f"QTreeWidget {{ background: {SURFACE0}; border: 1px solid {SURFACE2}; }}"
            f"QTreeWidget::item:selected {{ background: {SURFACE1}; }}"
            f"QTreeWidget::item:hover {{ background: {SURFACE1}; }}"
        )
        self._list.setAcceptDrops(True)
        self._list.viewport().setAcceptDrops(True)
        self._list.itemDoubleClicked.connect(self._on_double_clicked)
        self._list.star_click.connect(self._on_star_click)
        self._list.label_edit_requested.connect(self._start_label_edit)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_context_menu)
        root.addWidget(self._list, stretch=1)

        self._inline_edit = QLineEdit(self._list.viewport())
        self._inline_edit.hide()
        self._inline_edit.editingFinished.connect(self._finish_label_edit)
        self._list.setMouseTracking(True)
        self._list.viewport().setMouseTracking(True)
        self._list.viewport().installEventFilter(self)
        self._memo_timer = QTimer(self)
        self._memo_timer.setSingleShot(True)
        self._memo_timer.setInterval(500)
        self._memo_timer.timeout.connect(self._show_memo_tip)
        self._memo_item: QTreeWidgetItem | None = None
        self._memo_pos = QPoint()
        self._memo_tip = QLabel(None, Qt.WindowType.ToolTip)
        self._memo_tip.setWordWrap(True)
        self._memo_tip.setMaximumWidth(420)
        self._memo_tip.setStyleSheet(
            f"QLabel {{ background: {SURFACE1}; color: {TEXT}; "
            f"border: 1px solid {SURFACE2}; border-radius: 4px; padding: 6px 8px; }}"
        )

    def eventFilter(self, obj, event) -> bool:
        if obj is self._list.viewport():
            if event.type() == event.Type.MouseMove:
                self._schedule_memo_tip(event.pos())
            elif event.type() == event.Type.Leave:
                self._hide_memo_tip()
            elif event.type() in (event.Type.DragEnter, event.Type.DragMove):
                if self._can_accept_prompt_tile_drop(event):
                    event.acceptProposedAction()
                else:
                    event.ignore()
                return True
            elif event.type() == event.Type.Drop:
                if self._handle_prompt_tile_drop(event):
                    event.acceptProposedAction()
                else:
                    event.ignore()
                return True
        return super().eventFilter(obj, event)

    def _schedule_memo_tip(self, pos: QPoint) -> None:
        item = self._list.itemAt(pos)
        if not item or item.data(0, _ROLE_KIND) != _KIND_ITEM:
            self._hide_memo_tip()
            return
        memo = str(item.data(0, _ROLE_MEMO) or "").strip()
        if not memo or not self._list.memo_rect(item).contains(pos):
            self._hide_memo_tip()
            return
        if self._memo_item is item and self._memo_tip.isVisible():
            self._move_memo_tip(pos)
            return
        self._memo_item = item
        self._memo_pos = pos
        self._memo_timer.start()

    def _show_memo_tip(self) -> None:
        if not self._memo_item:
            return
        memo = str(self._memo_item.data(0, _ROLE_MEMO) or "").strip()
        if not memo:
            return
        self._memo_tip.setText(memo)
        self._memo_tip.adjustSize()
        self._move_memo_tip(self._memo_pos)
        self._memo_tip.show()

    def _move_memo_tip(self, pos: QPoint) -> None:
        self._memo_pos = pos
        self._memo_tip.move(self._list.viewport().mapToGlobal(pos + QPoint(14, 14)))

    def _hide_memo_tip(self) -> None:
        self._memo_timer.stop()
        self._memo_item = None
        self._memo_tip.hide()

    # ── データロード ──────────────────────────────────────────────────────────

    def refresh(self) -> None:
        """現在の検索条件で DB から再ロードして一覧を更新する。"""
        query = self._search_edit.text()
        show_nsfw = _show_nsfw()
        if query.strip():
            rows = search_prompt_texts(query, self._search_mode, show_nsfw)
        else:
            rows = get_all_prompt_texts(show_nsfw)
        self._populate(rows)

    def retranslate_and_restyle(self) -> None:
        self._title_label.setText(tr("prompt_text_browser.title"))
        self._search_edit.setPlaceholderText(tr("prompt_text_browser.search_placeholder"))
        self._and_btn.setText(tr("prompt_text_browser.and_btn"))
        self._and_btn.setToolTip(tr("prompt_text_browser.and_tooltip"))
        self._or_btn.setText(tr("prompt_text_browser.or_btn"))
        self._or_btn.setToolTip(tr("prompt_text_browser.or_tooltip"))
        self._clear_btn.setToolTip(tr("prompt_text_browser.search_clear_tooltip"))
        self.refresh()

    def _populate(self, rows: list[dict]) -> None:
        self._list.clear()
        categories = self._fetch_categories()
        category_items: dict[str, QTreeWidgetItem] = {}
        for cat in categories:
            cat_item = QTreeWidgetItem([cat["label"] or ""])
            cat_item.setData(0, _ROLE_KIND, _KIND_CATEGORY)
            cat_item.setData(0, _ROLE_CATEGORY_KEY, cat["key"])
            cat_item.setSizeHint(0, QSize(0, _CATEGORY_H))
            self._list.addTopLevelItem(cat_item)
            category_items[cat["key"]] = cat_item

        visible = 0
        for row in rows:
            label = (row.get("display_label") or "").strip()
            if not label:
                label = (row.get("source_text") or "")[:50]

            item = QTreeWidgetItem([label])
            item.setData(0, _ROLE_KIND, _KIND_ITEM)
            item.setData(0, _ROLE_ID,     row["id"])
            item.setData(0, _ROLE_RATING, row.get("rating") or 0)
            item.setData(0, _ROLE_MEMO,   row.get("memo") or "")
            item.setData(0, _ROLE_DISPLAY_LABEL, row.get("display_label") or "")
            item.setData(0, _ROLE_CATEGORY_KEY, row.get("category") or "")
            item.setSizeHint(0, QSize(0, max(_ITEM_H, self._list.fontMetrics().lineSpacing() * 3 + 12)))
            parent = category_items.get(row.get("category") or "")
            if parent is not None:
                parent.addChild(item)
            else:
                self._list.addTopLevelItem(item)
            visible += 1

        self._visible_count = visible
        self._list.collapseAll()
        self._count_label.setText(
            tr("prompt_text_browser.count", count=visible)
        )

    # ── 検索スロット ──────────────────────────────────────────────────────────

    def _on_search_enter(self) -> None:
        self._search_mode = "partial"
        self.refresh()

    def _on_search_and(self) -> None:
        self._search_mode = "and"
        self.refresh()

    def _on_search_or(self) -> None:
        self._search_mode = "or"
        self.refresh()

    def _on_clear(self) -> None:
        self._search_edit.clear()
        self._search_mode = "or"
        self.refresh()

    def set_search_query(self, query: str) -> None:
        self._search_mode = "or"
        self._search_edit.blockSignals(True)
        self._search_edit.setText(query)
        self._clear_btn.setVisible(bool(query))
        self._search_edit.blockSignals(False)
        self.refresh()

    def visible_count(self) -> int:
        return self._visible_count

    # ── アイテム操作スロット ──────────────────────────────────────────────────

    def _on_double_clicked(self, item: QTreeWidgetItem) -> None:
        if item.data(0, _ROLE_KIND) != _KIND_ITEM:
            return
        prompt_text_id = item.data(0, _ROLE_ID)
        if prompt_text_id is not None:
            self.item_double_clicked.emit(prompt_text_id)

    def _on_star_click(self, item: QTreeWidgetItem, new_rating: int) -> None:
        if item is None or item.data(0, _ROLE_KIND) != _KIND_ITEM:
            return
        prompt_text_id = item.data(0, _ROLE_ID)
        current = item.data(0, _ROLE_RATING) or 0
        # 同じ星をクリックしたらリセット（未評価）
        if new_rating == current:
            new_rating = 0
        db_rating = new_rating if new_rating else None
        update_prompt_text(prompt_text_id, rating=db_rating)
        item.setData(0, _ROLE_RATING, new_rating)
        self._list.update(self._list.indexFromItem(item))
        self.rating_changed.emit(prompt_text_id, new_rating)

    def _start_label_edit(self, item: QTreeWidgetItem) -> None:
        prompt_text_id = item.data(0, _ROLE_ID)
        if prompt_text_id is None:
            return
        self._edit_item = item
        rect = self._list._label_rect(item)
        self._inline_edit.setGeometry(rect)
        self._inline_edit.setText(item.data(0, _ROLE_DISPLAY_LABEL) or item.text(0))
        self._inline_edit.show()
        self._inline_edit.setFocus()
        self._inline_edit.selectAll()

    def _finish_label_edit(self) -> None:
        if not self._edit_item:
            return
        item = self._edit_item
        self._edit_item = None
        self._inline_edit.hide()
        prompt_text_id = item.data(0, _ROLE_ID)
        if prompt_text_id is None:
            return
        text = self._inline_edit.text().strip()
        update_prompt_text(prompt_text_id, display_label=text)
        item.setData(0, _ROLE_DISPLAY_LABEL, text)
        if text:
            item.setText(0, text)
        self._list.viewport().update()

    def _on_context_menu(self, pos) -> None:
        item = self._list.itemAt(pos)
        menu = QMenu(self)
        add_category_action = None
        rename_category_action = delete_category_action = None
        move_actions: dict[object, str | None] = {}
        edit_action = add_action = delete_action = None
        prompt_text_id = None

        if item is not None and item.data(0, _ROLE_KIND) == _KIND_CATEGORY:
            add_category_action = menu.addAction(tr("prompt_text_browser.menu_add_category"))
            rename_category_action = menu.addAction(tr("prompt_text_browser.menu_rename_category"))
            delete_category_action = menu.addAction(
                tr("prompt_text_browser.menu_delete_category", label=item.text(0))
            )
        elif item is not None and item.data(0, _ROLE_KIND) == _KIND_ITEM:
            prompt_text_id = item.data(0, _ROLE_ID)
            label = item.text(0) or tr("main.tab_prompt_texts")
            edit_action = menu.addAction(tr("prompt_text_browser.context_edit"))
            add_action = menu.addAction(tr("prompt_text_browser.context_add_to_center", label=label))
            move_menu = menu.addMenu(tr("prompt_text_browser.menu_move_to_category"))
            uncategorized_action = move_menu.addAction(tr("prompt_text_browser.move_to_uncategorized"))
            move_actions[uncategorized_action] = None
            categories = self._fetch_categories()
            if categories:
                move_menu.addSeparator()
                for cat in categories:
                    action = move_menu.addAction(cat["label"])
                    move_actions[action] = cat["key"]
            menu.addSeparator()
            delete_action = menu.addAction(tr("prompt_text_browser.context_delete"))
        else:
            add_category_action = menu.addAction(tr("prompt_text_browser.menu_add_category"))

        action = menu.exec(self._list.viewport().mapToGlobal(pos))
        if action is None:
            return
        if add_category_action is not None and action is add_category_action:
            self._add_category()
        elif rename_category_action is not None and action is rename_category_action and item is not None:
            self._rename_category(item)
        elif delete_category_action is not None and action is delete_category_action and item is not None:
            self._delete_category(item)
        elif action in move_actions and item is not None:
            self._move_prompt_to_category(item, move_actions[action])
        elif action is add_action and prompt_text_id is not None:
            self.item_double_clicked.emit(prompt_text_id)
        elif action is edit_action and prompt_text_id is not None:
            self._open_edit_dialog(prompt_text_id)
        elif action is delete_action and prompt_text_id is not None:
            self._delete_item(prompt_text_id)

    def _delete_item(self, prompt_text_id: int) -> None:
        result = QMessageBox.question(
            self,
            tr("prompt_text_browser.delete_title"),
            tr("prompt_text_browser.delete_body"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if result == QMessageBox.StandardButton.Yes:
            from db.prompt_text_db import delete_prompt_text
            delete_prompt_text(prompt_text_id)
            self.refresh()

    def _open_edit_dialog(self, prompt_text_id: int) -> None:
        record = _db.fetchone(
            "SELECT * FROM prompt_texts WHERE id = ?", (prompt_text_id,)
        )
        if record is None:
            return
        from ui.prompt_text_edit_dialog import PromptTextEditDialog
        dlg = PromptTextEditDialog(dict(record), parent=self)
        dlg.saved.connect(lambda _id: self.refresh())
        dlg.exec()

    # ── タイル D&D 受け取り（中央ペイン → 一覧登録） ────────────────────────

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self._can_accept_prompt_tile_drop(event):
            event.acceptProposedAction()
            return
        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        if self._handle_prompt_tile_drop(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def _can_accept_prompt_tile_drop(self, event) -> bool:
        from ui.tile_widget import TILE_MIME
        if event.mimeData().hasFormat(TILE_MIME):
            import ui.tile_drag as tile_drag
            tw = tile_drag.get_drag()
            if tw is not None:
                from core.prompt_builder import NaturalTextTile
                if isinstance(tw.tile, NaturalTextTile):
                    return True
        return False

    def _handle_prompt_tile_drop(self, event) -> bool:
        from ui.tile_widget import TILE_MIME
        from core.prompt_builder import NaturalTextTile
        if not event.mimeData().hasFormat(TILE_MIME):
            return False
        import ui.tile_drag as tile_drag
        tw = tile_drag.get_drag()
        if tw is None or not isinstance(tw.tile, NaturalTextTile):
            return False

        tile = tw.tile
        db_source_text     = tile.source_text or tile.text
        db_translated_text = tile.translated_text or tile.text

        if not db_source_text.strip():
            return False

        insert_prompt_text(
            source_text=db_source_text,
            translated_text=db_translated_text,
            display_label=tile.display_label,
        )
        self.refresh()
        return True

    def _fetch_categories(self) -> list[dict]:
        rows = _db.fetchall(
            "SELECT key, label FROM prompt_text_categories ORDER BY sort_order, label"
        )
        return [dict(r) for r in rows]

    def _next_category_sort_order(self) -> int:
        row = _db.fetchone("SELECT COALESCE(MAX(sort_order), 0) + 10 AS n FROM prompt_text_categories")
        return int(row["n"] if row else 10)

    def _add_category(self) -> None:
        label, ok = QInputDialog.getText(
            self,
            tr("prompt_text_browser.add_category_title"),
            tr("prompt_text_browser.add_category_prompt"),
        )
        if not ok or not label.strip():
            return
        _db.execute(
            "INSERT INTO prompt_text_categories (key, label, sort_order) VALUES (?, ?, ?)",
            (f"custom_{uuid4().hex}", label.strip(), self._next_category_sort_order()),
        )
        self.refresh()

    def _rename_category(self, item: QTreeWidgetItem) -> None:
        category_key = item.data(0, _ROLE_CATEGORY_KEY)
        if not category_key:
            return
        label, ok = QInputDialog.getText(
            self,
            tr("prompt_text_browser.rename_category_title"),
            tr("prompt_text_browser.add_category_prompt"),
            text=item.text(0),
        )
        if not ok or not label.strip():
            return
        _db.execute("UPDATE prompt_text_categories SET label=? WHERE key=?", (label.strip(), category_key))
        self.refresh()

    def _delete_category(self, item: QTreeWidgetItem) -> None:
        category_key = item.data(0, _ROLE_CATEGORY_KEY)
        if not category_key:
            return
        row = _db.fetchone(
            "SELECT COUNT(*) AS n FROM prompt_texts WHERE COALESCE(category,'')=?",
            (category_key,),
        )
        count = int(row["n"] if row else 0)
        label = item.text(0)
        if QMessageBox.question(
            self,
            tr("prompt_text_browser.delete_category_title"),
            tr("prompt_text_browser.delete_category_confirm", label=label, n=count),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        _db.execute("UPDATE prompt_texts SET category=NULL WHERE COALESCE(category,'')=?", (category_key,))
        _db.execute("DELETE FROM prompt_text_categories WHERE key=?", (category_key,))
        self.refresh()

    def _move_prompt_to_category(self, item: QTreeWidgetItem, category_key: str | None) -> None:
        prompt_text_id = item.data(0, _ROLE_ID)
        if prompt_text_id is None:
            return
        _db.execute(
            "UPDATE prompt_texts SET category=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (category_key or None, prompt_text_id),
        )
        self.refresh()
