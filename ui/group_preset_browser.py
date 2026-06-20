"""
保存グループブラウザ。

左ペインのプロンプトタブ内で、既存の group_presets を独立したタイルグループとして扱う。
DB形式は変えず、第一弾では表示・検索・D&D・基本管理だけを担う。
"""
from __future__ import annotations

import json
from uuid import uuid4

from PySide6.QtCore import Qt, QPoint, QMimeData, QSize, Signal
from PySide6.QtGui import QDrag, QPixmap, QPainter, QColor, QPen, QBrush
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QStyledItemDelegate,
    QStyle,
    QStyleOptionViewItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.i18n import tr
from core.prompt_builder import GroupTile
from db.group_preset_db import unique_group_name
from ui.star_widget import StarWidget
from ui.styles import ACCENT, SURFACE0, SURFACE1, SURFACE2, SUBTEXT, ui_font
from ui.styles import TEXT, YELLOW, themed_button_style
import db.app_db as _app_db
import db.library_db as _library_db


_ROLE_PRESET_ID = Qt.ItemDataRole.UserRole + 1
_ROLE_GROUP_JSON = Qt.ItemDataRole.UserRole + 2
_ROLE_RATING = Qt.ItemDataRole.UserRole + 3
_ROLE_MEMO = Qt.ItemDataRole.UserRole + 4
_ROLE_DISPLAY_LABEL = Qt.ItemDataRole.UserRole + 5
_ROLE_NSFW = Qt.ItemDataRole.UserRole + 6
_ROLE_KIND = Qt.ItemDataRole.UserRole + 7
_ROLE_CATEGORY_KEY = Qt.ItemDataRole.UserRole + 8
_ITEM_H = 64
_CATEGORY_H = 28
_STAR_FILLED = "★"
_STAR_EMPTY = "☆"
_KIND_CATEGORY = "category"
_KIND_ITEM = "item"


def _show_nsfw() -> bool:
    row = _app_db.fetchone("SELECT value FROM app_settings WHERE key='show_nsfw'")
    return bool(int(row["value"])) if row else False


class _GroupPresetDelegate(QStyledItemDelegate):
    """タイルグループをタグ風の囲いで表示するデリゲート。"""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        if index.data(_ROLE_KIND) == _KIND_CATEGORY:
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            rect = option.rect.adjusted(2, 3, -2, -3)
            painter.setPen(QPen(QColor(SURFACE2)))
            painter.setBrush(QBrush(QColor(SURFACE1)))
            painter.drawRoundedRect(rect, 4, 4)
            painter.setPen(QColor(ACCENT))
            label = index.data(Qt.ItemDataRole.DisplayRole) or ""
            painter.drawText(
                rect.adjusted(8, 0, -8, 0),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                option.fontMetrics.elidedText(label, Qt.TextElideMode.ElideRight, rect.width() - 16),
            )
            painter.restore()
            return

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg = QColor(SURFACE1 if option.state & QStyle.StateFlag.State_Selected else SURFACE0)
        rect = option.rect.adjusted(2, 2, -2, -2)
        painter.setPen(QPen(QColor(SURFACE2)))
        painter.setBrush(QBrush(bg))
        painter.drawRoundedRect(rect, 4, 4)

        fm = option.fontMetrics
        x = rect.x() + 8
        y = rect.y() + 6
        w = rect.width() - 16

        label = index.data(Qt.ItemDataRole.DisplayRole) or ""
        if index.data(_ROLE_NSFW):
            label = f"{label}  NSFW"
        painter.setPen(QColor(TEXT))
        painter.drawText(
            x, y, w, fm.height(),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
            fm.elidedText(label, Qt.TextElideMode.ElideRight, w),
        )

        memo = index.data(_ROLE_MEMO) or ""
        painter.setPen(QColor(SUBTEXT))
        painter.drawText(
            x, y + fm.lineSpacing(), w, fm.height(),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
            fm.elidedText(memo, Qt.TextElideMode.ElideRight, w),
        )

        rating = index.data(_ROLE_RATING) or 0
        stars = _STAR_FILLED * rating + _STAR_EMPTY * (5 - rating)
        painter.setPen(QColor(YELLOW))
        painter.drawText(
            x, y + fm.lineSpacing() * 2, w, fm.height(),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
            stars,
        )
        painter.restore()

    def sizeHint(self, option, index) -> QSize:
        if index.data(_ROLE_KIND) == _KIND_CATEGORY:
            return QSize(0, _CATEGORY_H)
        return QSize(0, max(_ITEM_H, option.fontMetrics.lineSpacing() * 3 + 12))


class _GroupPresetTree(QTreeWidget):
    star_click = Signal(object, int)

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
                star_y_top = rect.y() + 6 + fm.lineSpacing() * 2 - 2
                star_y_bottom = star_y_top + fm.height() + 6
                if star_y_top <= event.pos().y() <= star_y_bottom:
                    rel_x = event.pos().x() - (rect.x() + 8)
                    star_w = max(1, fm.horizontalAdvance(_STAR_FILLED))
                    if 0 <= rel_x <= star_w * 5:
                        self.star_click.emit(item, int(min(5, max(1, rel_x // star_w + 1))))
                        return
            self._drag_start_pos = event.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if (
            event.buttons() & Qt.MouseButton.LeftButton
            and self._drag_start_pos is not None
            and (event.pos() - self._drag_start_pos).manhattanLength() >= QApplication.startDragDistance()
        ):
            item = self.itemAt(self._drag_start_pos)
            self._drag_start_pos = None
            if item and item.data(0, _ROLE_KIND) == _KIND_ITEM:
                self.parent()._begin_group_drag_for_item(item, self._drag_start_pos)
            return
        super().mouseMoveEvent(event)


class GroupPresetEditDialog(QDialog):
    """保存済みタイルグループの表示情報編集ダイアログ。"""

    def __init__(self, record: dict, parent=None):
        super().__init__(parent)
        self._record = record
        self.setWindowTitle(tr("group_preset_browser.edit_title"))
        self.setModal(True)
        self.resize(440, 300)
        self.setStyleSheet(f"QDialog {{ background-color: {SURFACE0}; }}")
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        form = QFormLayout()
        form.setSpacing(8)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        lbl_style = f"color: {TEXT};"
        edit_style = (
            f"background: {SURFACE1}; color: {TEXT}; border: 1px solid {SURFACE2}; "
            f"border-radius: 3px; padding: 2px 4px;"
        )

        self._name_edit = QLineEdit(self._record.get("name") or "")
        self._name_edit.setStyleSheet(edit_style)
        name_lbl = QLabel(tr("group_preset_browser.name_label"))
        name_lbl.setStyleSheet(lbl_style)
        form.addRow(name_lbl, self._name_edit)

        self._display_label_edit = QLineEdit(self._record.get("display_label") or "")
        self._display_label_edit.setStyleSheet(edit_style)
        label_lbl = QLabel(tr("group_preset_browser.display_label"))
        label_lbl.setStyleSheet(lbl_style)
        form.addRow(label_lbl, self._display_label_edit)

        self._star_widget = StarWidget(
            rating=self._record.get("rating") or 0,
            editable=True,
            font_size=14,
        )
        rating_lbl = QLabel(tr("prompt_text_edit.rating"))
        rating_lbl.setStyleSheet(lbl_style)
        form.addRow(rating_lbl, self._star_widget)

        self._memo_edit = QPlainTextEdit(self._record.get("memo") or "")
        self._memo_edit.setFixedHeight(90)
        self._memo_edit.setStyleSheet(edit_style)
        memo_lbl = QLabel(tr("prompt_text_edit.memo"))
        memo_lbl.setStyleSheet(lbl_style)
        form.addRow(memo_lbl, self._memo_edit)

        self._nsfw_check = QCheckBox()
        self._nsfw_check.setChecked(bool(self._record.get("is_nsfw")))
        self._nsfw_check.setStyleSheet(f"color: {TEXT};")
        nsfw_lbl = QLabel(tr("prompt_text_edit.nsfw"))
        nsfw_lbl.setStyleSheet(lbl_style)
        form.addRow(nsfw_lbl, self._nsfw_check)

        root.addLayout(form)
        root.addStretch()

        btn_box = QDialogButtonBox()
        save_btn = btn_box.addButton(tr("prompt_text_edit.save"), QDialogButtonBox.ButtonRole.AcceptRole)
        cancel_btn = btn_box.addButton(tr("prompt_text_edit.cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        save_btn.setStyleSheet(themed_button_style("success", bold=True))
        cancel_btn.setStyleSheet(
            f"QPushButton {{ background: {SURFACE1}; color: {SUBTEXT}; "
            f"border: 1px solid {SURFACE2}; border-radius: 4px; padding: 4px 16px; }}"
        )
        btn_box.accepted.connect(self._on_save)
        btn_box.rejected.connect(self.reject)
        root.addWidget(btn_box)

    def _on_save(self) -> None:
        name = self._name_edit.text().strip()
        if not name:
            return
        group_json = self._record.get("group_json") or ""
        if group_json:
            try:
                group_data = json.loads(group_json)
                if isinstance(group_data, dict):
                    group_data["name"] = name
                    group_json = json.dumps(group_data, ensure_ascii=False)
            except Exception:
                pass
        rating = self._star_widget.rating
        _library_db.execute(
            """
            UPDATE group_presets
               SET name=?, group_json=?, display_label=?, memo=?, rating=?, is_nsfw=?
             WHERE id=?
            """,
            (
                name,
                group_json,
                self._display_label_edit.text().strip() or None,
                self._memo_edit.toPlainText().strip() or None,
                rating if rating > 0 else None,
                1 if self._nsfw_check.isChecked() else 0,
                self._record["id"],
            ),
        )
        self.accept()


class GroupPresetBrowser(QWidget):
    """保存グループの一覧・検索・D&D入口。"""

    group_double_clicked = Signal(str, str)

    _instances: list["GroupPresetBrowser"] = []
    _GROUP_BROWSER_MIME = "application/x-invoke-browser-group"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_start: QPoint | None = None
        self._drag_item: QTreeWidgetItem | None = None
        self._visible_count = 0
        GroupPresetBrowser._instances.append(self)
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        title = QLabel(tr("group_preset_browser.title"))
        title.setFont(ui_font(bold=True))
        title.setStyleSheet(f"color: {ACCENT}; padding: 2px 4px;")
        root.addWidget(title)
        self._title_label = title

        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText(tr("group_preset_browser.search_placeholder"))
        self._search_edit.setFixedHeight(24)
        self._search_edit.setFont(ui_font(-1))
        self._search_edit.setStyleSheet(
            f"QLineEdit {{ background: {SURFACE0}; color: {TEXT}; "
            f"border: 1px solid {SURFACE2}; border-radius: 3px; padding: 0 4px; }}"
        )
        self._search_edit.textChanged.connect(self.refresh)
        root.addWidget(self._search_edit)
        self._search_edit.hide()

        self._count_label = QLabel(tr("group_preset_browser.count", count=0))
        self._count_label.setFont(ui_font(-2))
        self._count_label.setStyleSheet(f"color: {SUBTEXT}; padding: 0 2px;")
        root.addWidget(self._count_label)

        self._list = _GroupPresetTree(self)
        self._list.setHeaderHidden(True)
        self._list.setRootIsDecorated(True)
        self._list.setItemDelegate(_GroupPresetDelegate(self._list))
        self._list.setFont(ui_font(-1))
        self._list.setDragEnabled(False)
        self._list.setAcceptDrops(True)
        self._list.viewport().setAcceptDrops(True)
        self._list.viewport().installEventFilter(self)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_context_menu)
        self._list.itemDoubleClicked.connect(self._on_double_clicked)
        self._list.star_click.connect(self._on_star_click)
        self._list.setStyleSheet(
            f"QTreeWidget {{ background: {SURFACE0}; border: 1px solid {SURFACE2}; "
            f"border-radius: 4px; }}"
            f"QTreeWidget::item {{ color: {ACCENT}; padding: 1px 2px; }}"
            f"QTreeWidget::item:selected {{ background: {SURFACE1}; }}"
            f"QTreeWidget::item:hover {{ background: {SURFACE1}; }}"
        )
        root.addWidget(self._list, stretch=1)

    def refresh(self) -> None:
        expanded_keys = self._expanded_category_keys()
        keyword = self._search_edit.text().strip().lower() if hasattr(self, "_search_edit") else ""
        words = [w for w in keyword.split() if w]
        self._list.clear()
        nsfw_clause = "" if _show_nsfw() else "WHERE COALESCE(is_nsfw, 0) = 0"
        rows = _library_db.fetchall(
            f"""SELECT id, name, group_json, display_label, memo,
                       category, COALESCE(rating, 0) AS rating, COALESCE(is_nsfw, 0) AS is_nsfw
                  FROM group_presets
                  {nsfw_clause}
              ORDER BY sort_order, created_at"""
        )
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
            name = row["name"] or ""
            label = row["display_label"] or name
            memo = row["memo"] or ""
            group_json = row["group_json"] or ""
            haystack = f"{name}\n{label}\n{memo}\n{group_json}".lower()
            if words and not any(word in haystack for word in words):
                continue
            item = QTreeWidgetItem([label])
            item.setData(0, _ROLE_KIND, _KIND_ITEM)
            item.setData(0, _ROLE_PRESET_ID, row["id"])
            item.setData(0, _ROLE_GROUP_JSON, group_json)
            item.setData(0, _ROLE_DISPLAY_LABEL, label)
            item.setData(0, _ROLE_MEMO, memo)
            item.setData(0, _ROLE_RATING, row["rating"] or 0)
            item.setData(0, _ROLE_NSFW, row["is_nsfw"] or 0)
            item.setData(0, _ROLE_CATEGORY_KEY, row["category"] or "")
            item.setSizeHint(0, QSize(0, _ITEM_H))
            parent = category_items.get(row["category"] or "")
            if parent is not None:
                parent.addChild(item)
            else:
                self._list.addTopLevelItem(item)
            visible += 1

        self._visible_count = visible
        self._restore_expanded_category_keys(expanded_keys)
        self._count_label.setText(tr("group_preset_browser.count", count=visible))

    def _expanded_category_keys(self) -> set[str]:
        keys: set[str] = set()
        if not hasattr(self, "_list"):
            return keys
        for i in range(self._list.topLevelItemCount()):
            item = self._list.topLevelItem(i)
            if item.data(0, _ROLE_KIND) != _KIND_CATEGORY:
                continue
            key = item.data(0, _ROLE_CATEGORY_KEY)
            if key and item.isExpanded():
                keys.add(str(key))
        return keys

    def _restore_expanded_category_keys(self, keys: set[str]) -> None:
        self._list.collapseAll()
        for i in range(self._list.topLevelItemCount()):
            item = self._list.topLevelItem(i)
            if item.data(0, _ROLE_KIND) != _KIND_CATEGORY:
                continue
            key = item.data(0, _ROLE_CATEGORY_KEY)
            if key and str(key) in keys:
                item.setExpanded(True)

    def retranslate_and_restyle(self) -> None:
        self._title_label.setText(tr("group_preset_browser.title"))
        self._search_edit.setPlaceholderText(tr("group_preset_browser.search_placeholder"))
        self._count_label.setText(tr("group_preset_browser.count", count=self._visible_count))
        self.refresh()

    def set_search_query(self, query: str) -> None:
        self._search_edit.blockSignals(True)
        self._search_edit.setText(query)
        self._search_edit.blockSignals(False)
        self.refresh()

    def visible_count(self) -> int:
        return self._visible_count

    def eventFilter(self, obj, event) -> bool:
        if obj is self._list.viewport():
            if event.type() in (event.Type.DragEnter, event.Type.DragMove):
                if self._can_accept_group_tile_drop(event):
                    event.acceptProposedAction()
                else:
                    event.ignore()
                return True
            elif event.type() == event.Type.Drop:
                if self._handle_group_tile_drop(event):
                    event.acceptProposedAction()
                else:
                    event.ignore()
                return True
            elif event.type() == event.Type.DragLeave:
                self._reset_drag_state()
                return True
        return super().eventFilter(obj, event)

    def _begin_group_drag_for_item(self, item: QTreeWidgetItem, press_pos: QPoint | None = None) -> None:
        if item is None or item.data(0, _ROLE_KIND) != _KIND_ITEM:
            return
        group_json = item.data(0, _ROLE_GROUP_JSON) or ""
        if not group_json:
            return
        preset_name = item.text(0).strip()

        import ui.tag_drag as tag_drag
        tag_drag.set_group_drag(group_json, preset_name)

        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(self._GROUP_BROWSER_MIME, group_json.encode())
        drag.setMimeData(mime)

        pixmap, hotspot = self._drag_pixmap_for_item(item, press_pos)
        if not pixmap.isNull():
            drag.setPixmap(pixmap)
            drag.setHotSpot(hotspot)

        self._reset_drag_state()
        try:
            drag.exec(Qt.DropAction.CopyAction)
        finally:
            self._reset_drag_state()
            tag_drag.clear_group_drag()

    def _begin_group_drag(self) -> None:
        if self._drag_item is not None:
            self._begin_group_drag_for_item(self._drag_item)

    def _drag_pixmap_for_item(self, item: QTreeWidgetItem, press_pos: QPoint | None) -> tuple[QPixmap, QPoint]:
        from ui.drag_pixmap import translucent_drag_pixmap

        rect = self._list.visualItemRect(item).intersected(self._list.viewport().rect())
        if rect.isEmpty():
            return QPixmap(), QPoint(0, 0)
        pixmap = self._list.viewport().grab(rect)
        hotspot = (press_pos or rect.center()) - rect.topLeft()
        hotspot.setX(max(0, min(hotspot.x(), rect.width() - 1)))
        hotspot.setY(max(0, min(hotspot.y(), rect.height() - 1)))
        return translucent_drag_pixmap(pixmap), hotspot

    def _reset_drag_state(self) -> None:
        self._drag_start = None
        self._drag_item = None

    def _can_accept_group_tile_drop(self, event) -> bool:
        from ui.tile_widget import TILE_MIME
        if not event.mimeData().hasFormat(TILE_MIME):
            return False
        import ui.tile_drag as tile_drag
        tw = tile_drag.get_drag()
        return tw is not None and isinstance(tw.tile, GroupTile)

    def _handle_group_tile_drop(self, event) -> bool:
        if not self._can_accept_group_tile_drop(event):
            return False
        import ui.tile_drag as tile_drag
        tw = tile_drag.get_drag()
        if tw is None or not isinstance(tw.tile, GroupTile):
            return False
        row = _library_db.fetchone("SELECT COALESCE(MAX(sort_order), 0) + 10 AS n FROM group_presets")
        sort_order = row["n"] if row else 10
        name = unique_group_name(tw.tile.name)
        group_data = tw.tile.to_dict(include_ui_state=False)
        if isinstance(group_data, dict):
            group_data["name"] = name
        group_json = json.dumps(group_data, ensure_ascii=False)
        _library_db.execute(
            "INSERT INTO group_presets (name, group_json, sort_order) VALUES (?, ?, ?)",
            (name, group_json, sort_order),
        )
        self.notify_presets_changed()
        return True

    def _on_context_menu(self, pos: QPoint) -> None:
        item = self._list.itemAt(pos)
        menu = QMenu(self)
        add_category_action = None
        rename_category_action = delete_category_action = None
        move_actions: dict[object, str | None] = {}
        edit_action = rename_action = delete_action = None

        if item is not None and item.data(0, _ROLE_KIND) == _KIND_CATEGORY:
            add_category_action = menu.addAction(tr("group_preset_browser.menu_add_category"))
            rename_category_action = menu.addAction(tr("group_preset_browser.menu_rename_category"))
            delete_category_action = menu.addAction(
                tr("group_preset_browser.menu_delete_category", label=item.text(0))
            )
        elif item is not None and item.data(0, _ROLE_KIND) == _KIND_ITEM:
            edit_action = menu.addAction(tr("prompt_text_browser.context_edit"))
            rename_action = menu.addAction(tr("tag_browser.menu_rename"))
            move_menu = menu.addMenu(tr("group_preset_browser.menu_move_to_category"))
            uncategorized_action = move_menu.addAction(tr("group_preset_browser.move_to_uncategorized"))
            move_actions[uncategorized_action] = None
            categories = self._fetch_categories()
            if categories:
                move_menu.addSeparator()
                for cat in categories:
                    action = move_menu.addAction(cat["label"])
                    move_actions[action] = cat["key"]
            menu.addSeparator()
            delete_action = menu.addAction(tr("tag_browser.menu_delete"))
        else:
            add_category_action = menu.addAction(tr("group_preset_browser.menu_add_category"))

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
            self._move_preset_to_category(item, move_actions[action])
        elif action is edit_action and item is not None:
            self._open_edit_dialog(item)
        elif action is rename_action and item is not None:
            self._rename_preset(item)
        elif action is delete_action and item is not None:
            self._delete_preset(item)

    def _on_double_clicked(self, item: QTreeWidgetItem) -> None:
        if item.data(0, _ROLE_KIND) != _KIND_ITEM:
            return
        group_json = item.data(0, _ROLE_GROUP_JSON) or ""
        if not group_json:
            return
        self.group_double_clicked.emit(group_json, item.text(0).strip())

    def _open_edit_dialog(self, item: QTreeWidgetItem) -> None:
        preset_id = item.data(0, _ROLE_PRESET_ID)
        row = _library_db.fetchone("SELECT * FROM group_presets WHERE id=?", (preset_id,))
        if row is None:
            return
        dlg = GroupPresetEditDialog(dict(row), parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.notify_presets_changed()

    def _on_star_click(self, item: QTreeWidgetItem, new_rating: int) -> None:
        if item is None or item.data(0, _ROLE_KIND) != _KIND_ITEM:
            return
        preset_id = item.data(0, _ROLE_PRESET_ID)
        current = item.data(0, _ROLE_RATING) or 0
        if new_rating == current:
            new_rating = 0
        _library_db.execute(
            "UPDATE group_presets SET rating=? WHERE id=?",
            (new_rating if new_rating else None, preset_id),
        )
        item.setData(0, _ROLE_RATING, new_rating)
        self._list.update(self._list.indexFromItem(item))

    def _rename_preset(self, item: QTreeWidgetItem) -> None:
        preset_id = item.data(0, _ROLE_PRESET_ID)
        row = _library_db.fetchone("SELECT name, group_json FROM group_presets WHERE id=?", (preset_id,))
        current_name = row["name"] if row else ""
        name, ok = QInputDialog.getText(
            self,
            tr("tag_browser.rename_title"),
            tr("tag_browser.rename_label"),
            text=current_name,
        )
        if not ok or not name.strip():
            return
        new_name = name.strip()
        group_json = row["group_json"] if row else ""
        if group_json:
            try:
                group_data = json.loads(group_json)
                if isinstance(group_data, dict):
                    group_data["name"] = new_name
                    group_json = json.dumps(group_data, ensure_ascii=False)
            except Exception:
                pass
        _library_db.execute(
            "UPDATE group_presets SET name=?, group_json=? WHERE id=?",
            (new_name, group_json, preset_id),
        )
        self.notify_presets_changed()

    def _delete_preset(self, item: QTreeWidgetItem) -> None:
        preset_id = item.data(0, _ROLE_PRESET_ID)
        row = _library_db.fetchone("SELECT name FROM group_presets WHERE id=?", (preset_id,))
        name = row["name"] if row else item.text(0)
        if QMessageBox.question(
            self,
            tr("tag_browser.confirm_delete_title"),
            tr("tag_browser.preset_delete_confirm_msg", name=name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        _library_db.execute("DELETE FROM group_presets WHERE id=?", (preset_id,))
        self.notify_presets_changed()

    def _fetch_categories(self) -> list[dict]:
        rows = _library_db.fetchall(
            "SELECT key, label FROM group_categories ORDER BY sort_order, label"
        )
        return [dict(r) for r in rows]

    def _next_category_sort_order(self) -> int:
        row = _library_db.fetchone("SELECT COALESCE(MAX(sort_order), 0) + 10 AS n FROM group_categories")
        return int(row["n"] if row else 10)

    def _add_category(self) -> None:
        label, ok = QInputDialog.getText(
            self,
            tr("group_preset_browser.add_category_title"),
            tr("group_preset_browser.add_category_prompt"),
        )
        if not ok or not label.strip():
            return
        _library_db.execute(
            "INSERT INTO group_categories (key, label, sort_order) VALUES (?, ?, ?)",
            (f"custom_{uuid4().hex}", label.strip(), self._next_category_sort_order()),
        )
        self.notify_presets_changed()

    def _rename_category(self, item: QTreeWidgetItem) -> None:
        category_key = item.data(0, _ROLE_CATEGORY_KEY)
        if not category_key:
            return
        label, ok = QInputDialog.getText(
            self,
            tr("group_preset_browser.rename_category_title"),
            tr("group_preset_browser.add_category_prompt"),
            text=item.text(0),
        )
        if not ok or not label.strip():
            return
        _library_db.execute("UPDATE group_categories SET label=? WHERE key=?", (label.strip(), category_key))
        self.notify_presets_changed()

    def _delete_category(self, item: QTreeWidgetItem) -> None:
        category_key = item.data(0, _ROLE_CATEGORY_KEY)
        if not category_key:
            return
        row = _library_db.fetchone(
            "SELECT COUNT(*) AS n FROM group_presets WHERE COALESCE(category,'')=?",
            (category_key,),
        )
        count = int(row["n"] if row else 0)
        label = item.text(0)
        if QMessageBox.question(
            self,
            tr("group_preset_browser.delete_category_title"),
            tr("group_preset_browser.delete_category_confirm", label=label, n=count),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        _library_db.execute("UPDATE group_presets SET category=NULL WHERE COALESCE(category,'')=?", (category_key,))
        _library_db.execute("DELETE FROM group_categories WHERE key=?", (category_key,))
        self.notify_presets_changed()

    def _move_preset_to_category(self, item: QTreeWidgetItem, category_key: str | None) -> None:
        preset_id = item.data(0, _ROLE_PRESET_ID)
        if preset_id is None:
            return
        _library_db.execute(
            "UPDATE group_presets SET category=? WHERE id=?",
            (category_key or None, preset_id),
        )
        self.notify_presets_changed()

    @classmethod
    def notify_presets_changed(cls) -> None:
        for instance in list(cls._instances):
            instance.refresh()

    def closeEvent(self, event) -> None:
        if self in GroupPresetBrowser._instances:
            GroupPresetBrowser._instances.remove(self)
        super().closeEvent(event)
