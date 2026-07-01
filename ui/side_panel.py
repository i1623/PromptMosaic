"""
サイドパネル（右ペイン）

タブ構成:
  ・履歴タブ  — 階層グループ＋生成履歴（QTreeWidget）
               右クリックメニュー + D&D でグループ操作
               パンくずで現在の「生成先グループ」を表示
  ・ノートタブ — 今日の日記ノート入力

サムネイル:
  generations.thumbnail_data BLOB を読み書きする。
  未格納の場合は Invoke API から取得し、圧縮して DB に保存する。
"""
from __future__ import annotations

import datetime
import hashlib
import io
import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QTabWidget, QTextEdit, QPushButton,
    QTreeWidget, QTreeWidgetItem,
    QMenu, QInputDialog, QMessageBox, QFileDialog,
    QDialog, QDialogButtonBox, QAbstractItemView,
    QLineEdit, QComboBox, QApplication, QCheckBox,
    QStyledItemDelegate, QStyle, QListWidget, QListWidgetItem, QStackedWidget,
)
from PySide6.QtCore import Qt, QThread, QSize, Signal, QPoint, QTimer, QRect, QMimeData, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QPixmap, QIcon, QColor, QDrag, QBrush

import db.app_db as _app_db
import db.history_db as _history_db
import db.notes_db as _notes_db
from core.image_resolver import resolve_generation_image_path
import core.local_storage as local_storage
from core.prompt_builder import GroupTile, PromptDocument
from core.i18n import tr
from ui.prompt_editor import PromptEditor
from ui.drag_pixmap import translucent_drag_pixmap
import ui.styles as styles
from ui.styles import (
    SURFACE0, SURFACE1, SURFACE2, TEXT, SUBTEXT, ACCENT, GREEN, RED,
    emoji_icon_font, ui_font, themed_button_style,
)

if TYPE_CHECKING:
    from api.invoke_client import InvokeClient

# デフォルト値
_DEFAULT_ICON_SIZE = 128
HISTORY_GENERATION_MIME = "application/x-promptmosaic-generation-id"

# QTreeWidgetItem に格納するデータロール
_ROLE_TYPE = Qt.ItemDataRole.UserRole        # str: "group" | "generation"
_ROLE_ID   = Qt.ItemDataRole.UserRole + 1   # int: id
_ROLE_LOADED = Qt.ItemDataRole.UserRole + 2 # bool: group children loaded
_ROLE_IMAGE_NAME = Qt.ItemDataRole.UserRole + 3
_ROLE_THUMB_LOADED = Qt.ItemDataRole.UserRole + 4
_ROLE_RATING = Qt.ItemDataRole.UserRole + 5
_ROLE_TITLE = Qt.ItemDataRole.UserRole + 6
_ROLE_DETAIL = Qt.ItemDataRole.UserRole + 7
_ROLE_FAVORITE = Qt.ItemDataRole.UserRole + 8
_ROLE_GEN_NO = Qt.ItemDataRole.UserRole + 9
_ROLE_CREATED = Qt.ItemDataRole.UserRole + 10
_ROLE_MODEL = Qt.ItemDataRole.UserRole + 11
_ROLE_PARAMS = Qt.ItemDataRole.UserRole + 12
_ROLE_REVIEW_TEXT = Qt.ItemDataRole.UserRole + 13
_ROLE_GROUP_NAME = Qt.ItemDataRole.UserRole + 14
_ROLE_GROUP_NSFW = Qt.ItemDataRole.UserRole + 15
_ROLE_IS_LINEAGE_ROOT = Qt.ItemDataRole.UserRole + 16  # bool: 系譜の開祖（親なしノード）
_ROLE_HISTORY_BG = Qt.ItemDataRole.UserRole + 17
_ROLE_IS_LINEAGE_SINGLE = Qt.ItemDataRole.UserRole + 18
_ROLE_HISTORY_FG = Qt.ItemDataRole.UserRole + 19  # 解決済みの履歴文字色（ツリー上書き>設定既定）
_ROLE_FOCUS_FLASH = Qt.ItemDataRole.UserRole + 20
_ROLE_DRAFT_HISTORY_DB = Qt.ItemDataRole.UserRole + 21


class _StarFilter(QWidget):
    """Five-star filter: 0 means no rating filter."""

    changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._value = 0
        self._buttons: list[QPushButton] = []
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        for idx in range(1, 6):
            btn = QPushButton("☆")
            btn.setFixedSize(18, 22)
            btn.setFont(ui_font(-1, bold=True))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(
                f"QPushButton {{ background: transparent; color: {SUBTEXT}; "
                f"border: none; padding: 0; }}"
                f"QPushButton:hover {{ color: {ACCENT}; }}"
            )
            btn.clicked.connect(lambda _checked=False, n=idx: self._on_star_clicked(n))
            self._buttons.append(btn)
            lay.addWidget(btn)
        self._refresh()

    def value(self) -> int:
        return self._value

    def _on_star_clicked(self, value: int) -> None:
        self._value = 0 if self._value == value else value
        self._refresh()
        self.changed.emit(self._value)

    def _refresh(self) -> None:
        for idx, btn in enumerate(self._buttons, start=1):
            active = idx <= self._value
            btn.setText("★" if active else "☆")
            btn.setStyleSheet(
                f"QPushButton {{ background: transparent; "
                f"color: {ACCENT if active else SUBTEXT}; border: none; padding: 0; }}"
                f"QPushButton:hover {{ color: {ACCENT}; }}"
            )


class _GenerationItemDelegate(QStyledItemDelegate):
    """Paint generation rows without creating QWidget children for every history item."""

    title_committed = Signal(int, str)
    _LINE_COUNT = 6

    @staticmethod
    def row_layout(rect: QRect, icon_size: int, line_h: int) -> dict[str, object]:
        text_h = line_h * _GenerationItemDelegate._LINE_COUNT
        top = rect.top() + max(4, (rect.height() - max(icon_size, text_h)) // 2)
        tile_cell_w = 28
        tile_btn_size = 24
        btn_stack_h = tile_btn_size * 2 + 6
        btn_top = rect.top() + max(2, (rect.height() - btn_stack_h) // 2)
        tile_btn = QRect(
            rect.left() + max(0, (tile_cell_w - tile_btn_size) // 2),
            btn_top,
            tile_btn_size,
            tile_btn_size,
        )
        map_btn = QRect(tile_btn.left(), tile_btn.bottom() + 6, tile_btn_size, tile_btn_size)
        thumb = QRect(rect.left() + tile_cell_w + 2, top, icon_size, icon_size)
        text_left = thumb.right() + 10
        text_width = max(20, rect.right() - text_left - 4)
        text_top = rect.top() + max(2, (rect.height() - text_h) // 2)
        lines = [
            QRect(text_left, text_top + line_h * idx, text_width, line_h)
            for idx in range(_GenerationItemDelegate._LINE_COUNT)
        ]
        return {
            "tile_btn": tile_btn,
            "map_btn": map_btn,
            "thumb": thumb,
            "lines": lines,
            "title": lines[3],
            "stars": lines[2],
        }

    def createEditor(self, parent, option, index):
        if index.data(_ROLE_TYPE) != "generation":
            return None
        editor = QLineEdit(parent)
        editor.setFont(ui_font(-1))
        editor.setPlaceholderText(tr("review.comment_placeholder"))
        editor.setStyleSheet(
            f"QLineEdit {{ background: {SURFACE0}; color: {TEXT}; "
            f"border: 1px solid {ACCENT}; border-radius: 3px; padding: 0 4px; }}"
        )
        return editor

    def setEditorData(self, editor, index) -> None:
        if isinstance(editor, QLineEdit):
            editor.setText(str(index.data(_ROLE_TITLE) or ""))

    def setModelData(self, editor, model, index) -> None:
        if not isinstance(editor, QLineEdit):
            return
        title = editor.text().strip()
        model.setData(index, title, _ROLE_TITLE)
        gen_id = index.data(_ROLE_ID)
        if gen_id is not None:
            self.title_committed.emit(int(gen_id), title)

    def updateEditorGeometry(self, editor, option, index) -> None:
        icon_size = max(32, option.decorationSize.width() or _DEFAULT_ICON_SIZE)
        line_h = option.fontMetrics.lineSpacing()
        layout = self.row_layout(option.rect, icon_size, line_h)
        title_rect: QRect = layout["title"]  # type: ignore[assignment]
        editor.setGeometry(title_rect.adjusted(0, 1, 0, -1))

    def paint(self, painter, option, index) -> None:
        item_type = index.data(_ROLE_TYPE)
        if item_type not in ("generation", "draft"):
            super().paint(painter, option, index)
            return
        is_draft = item_type == "draft"

        painter.save()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        bg = index.data(_ROLE_HISTORY_BG) or SURFACE0
        painter.fillRect(option.rect, QColor(ACCENT if selected else bg))

        icon_size = max(32, option.decorationSize.width() or _DEFAULT_ICON_SIZE)
        line_h = option.fontMetrics.lineSpacing()
        layout = self.row_layout(option.rect, icon_size, line_h)
        thumb_rect: QRect = layout["thumb"]  # type: ignore[assignment]
        line_rects: list[QRect] = layout["lines"]  # type: ignore[assignment]

        icon = index.data(Qt.ItemDataRole.DecorationRole)
        if is_draft:
            painter.fillRect(thumb_rect, QColor(SURFACE1))
            painter.setPen(QColor(ACCENT if selected else SUBTEXT))
            painter.setFont(ui_font(3, bold=True))
            painter.drawText(thumb_rect, Qt.AlignmentFlag.AlignCenter, tr("history_map.draft_icon"))
        elif isinstance(icon, QIcon) and not icon.isNull():
            pix = icon.pixmap(icon_size, icon_size)
            x = thumb_rect.left() + max(0, (thumb_rect.width() - pix.width()) // 2)
            y = thumb_rect.top() + max(0, (thumb_rect.height() - pix.height()) // 2)
            painter.drawPixmap(x, y, pix)
        else:
            painter.fillRect(thumb_rect, QColor(SURFACE1))

        tile_btn_rect: QRect = layout["tile_btn"]  # type: ignore[assignment]
        painter.setPen(QColor(ACCENT if selected else SUBTEXT))
        painter.setBrush(QColor(SURFACE0 if selected else SURFACE1))
        painter.drawRoundedRect(tile_btn_rect, 4, 4)
        painter.setFont(emoji_icon_font())  # 絵文字は12pt固定（フォント設定非追従）
        painter.drawText(
            tile_btn_rect,
            Qt.AlignmentFlag.AlignCenter,
            "🧱",
        )

        map_btn_rect: QRect = layout["map_btn"]  # type: ignore[assignment]
        painter.setPen(QColor(ACCENT if selected else SUBTEXT))
        painter.setBrush(QColor(SURFACE0 if selected else SURFACE1))
        painter.drawRoundedRect(map_btn_rect, 4, 4)
        painter.drawText(
            map_btn_rect,
            Qt.AlignmentFlag.AlignCenter,
            (
                "🗺️" if is_draft
                else "🚩" if index.data(_ROLE_IS_LINEAGE_SINGLE)
                else "👑" if index.data(_ROLE_IS_LINEAGE_ROOT)
                else "🗺️"
            ),
        )

        gen_no = str(index.data(_ROLE_GEN_NO) or "")
        created = str(index.data(_ROLE_CREATED) or "")
        title = str(index.data(_ROLE_TITLE) or "")
        if index.data(_ROLE_FAVORITE):
            title += " ♥"
        model = str(index.data(_ROLE_MODEL) or "")
        params = str(index.data(_ROLE_PARAMS) or "")
        rating = int(index.data(_ROLE_RATING) or 0)
        stars = tr("history_map.draft_badge") if is_draft else "★" * rating + "☆" * (5 - rating)
        lines = [gen_no, created, stars, title, model, params]

        # 解決済みの履歴文字色（ツリー上書き > 設定既定）。主要行はこの色、
        # 副次行(作成日時/モデル/パラメータ)は半透明で弱めて階層を出す。
        fg = index.data(_ROLE_HISTORY_FG) or TEXT
        fg_muted = QColor(fg)
        fg_muted.setAlpha(180)
        for idx, text in enumerate(lines):
            bold = idx in (0, 2)
            painter.setFont(ui_font(-1 if idx in (0, 2, 3) else -2, bold=bold))
            if selected:
                pen_color = QColor(SURFACE0)
            elif idx == 2:
                pen_color = QColor(ACCENT if rating else fg_muted)
            elif idx in (0, 3):
                pen_color = QColor(fg)
            else:
                pen_color = QColor(fg_muted)
            painter.setPen(pen_color)
            painter.drawText(
                line_rects[idx],
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                text,
            )
        flash_phase = int(index.data(_ROLE_FOCUS_FLASH) or 0)
        if flash_phase:
            pulse = 1.0 - abs(4 - flash_phase) / 4.0
            border = QColor("#ffe86a")
            border.setAlpha(130 + int(100 * pulse))
            pen = painter.pen()
            pen.setColor(border)
            pen.setWidth(2 + (1 if pulse > 0.55 else 0))
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(option.rect.adjusted(2, 2, -3, -3), 5, 5)
        painter.restore()


def _read_setting(key: str, default: str) -> str:
    """app_settings から値を読む（なければ default を返す）"""
    row = _app_db.fetchone(
        "SELECT value FROM app_settings WHERE key = ?", (key,)
    )
    return row["value"] if row else default


# ── バックグラウンド サムネイルローダー ──────────────────────────────────

class _ThumbWorker(QThread):
    """
    バックグラウンドで複数サムネイルを順番に取得するスレッド。

    DB の thumbnail_data BLOB を優先チェックし、未格納なら API から取得して
    圧縮・保存する。

    Signals:
        thumbnail_ready(gen_id: int, data: bytes):
            1件取得完了のたびに emit する（メインスレッドで QPixmap に変換）
    """

    thumbnail_ready = Signal(int, bytes)

    def __init__(
        self,
        tasks: list[tuple[int, str]],          # [(gen_id, image_name), ...]
        client: "InvokeClient | None",
        history_name: str,
        parent=None,
    ):
        super().__init__(parent)
        self._tasks        = tasks
        self._client       = client
        self._history_name = history_name
        self._abort        = False

    def stop(self) -> None:
        self._abort = True

    def run(self) -> None:
        from db import connections
        conn = connections.get_history_conn(self._history_name)

        for gen_id, image_name in self._tasks:
            if self._abort:
                break

            # 1. DB BLOB を確認
            row = conn.execute(
                "SELECT thumbnail_data FROM generations WHERE id=?", (gen_id,)
            ).fetchone()
            if row and row[0]:
                self.thumbnail_ready.emit(gen_id, bytes(row[0]))
                continue

            # 2. API から取得（不可ならローカル保存画像にフォールバック）して圧縮・保存
            raw: bytes | None = None
            if self._client is not None and image_name:
                try:
                    raw = self._client.image_thumbnail(image_name)
                except Exception:
                    raw = None
            if raw is None:
                lp_row = conn.execute(
                    "SELECT local_path, invoke_image_name FROM generations WHERE id=?", (gen_id,)
                ).fetchone()
                local_path = str(lp_row[0] or "") if lp_row else ""
                image_name_for_resolve = str(lp_row[1] or "") if lp_row else image_name
                image_path = resolve_generation_image_path(local_path, image_name_for_resolve)
                if image_path:
                    try:
                        raw = image_path.read_bytes()
                    except Exception:
                        raw = None
            if raw is None:
                continue

            try:
                from PIL import Image
                img = Image.open(io.BytesIO(raw))
                img.thumbnail((256, 256))
                buf = io.BytesIO()
                img.save(buf, format="WEBP", quality=80)
                thumb_bytes = buf.getvalue()
            except Exception:
                thumb_bytes = raw

            conn.execute(
                "UPDATE generations SET thumbnail_data=? WHERE id=?",
                (sqlite3.Binary(thumb_bytes), gen_id),
            )
            conn.commit()
            self.thumbnail_ready.emit(gen_id, thumb_bytes)


# ── 生成ツリーウィジェット ───────────────────────────────────────────────

class GenerationTreeWidget(QTreeWidget):
    """
    グループ（フォルダ）と生成（ファイル）を階層表示する QTreeWidget。

    Signals:
        gen_double_clicked(int):      生成IDをダブルクリック
        gen_clicked(int):             生成IDをシングルクリック（プロンプトロード用）
        gen_full_load_requested(int): 右クリック「全てロード」
        gen_thumb_refresh_requested(int): 右クリック「サムネ更新」
        group_focused(object):        フォーカスされたグループID（int | None）
        tree_changed():               構造変更（リフレッシュ要求）
    """

    gen_double_clicked    = Signal(int)
    gen_clicked           = Signal(int)
    draft_clicked         = Signal(str, int)
    draft_delete_requested = Signal(str, int)
    gen_full_load_requested = Signal(int)
    gen_thumb_refresh_requested = Signal(int)
    group_focused         = Signal(object)
    tree_changed          = Signal()
    rating_changed        = Signal(int, int)
    title_changed         = Signal(int, str)
    tile_mode_requested   = Signal(int)
    draft_tile_mode_requested = Signal(str, int)
    history_map_requested = Signal(int)
    draft_history_map_requested = Signal(str, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        # mousePressEvent で独自処理して super をスキップした押下では、Qt 内部の
        # 押下状態が未確定のまま残り、続く mouseMoveEvent でラバーバンド選択
        # （矩形ドラッグ選択）が暴発して「ドラッグ方向が全選択」になる不具合がある。
        # それを防ぐため、独自処理した押下では _suppress_drag を立て、解除されるまで
        # mouseMoveEvent を super に渡さない（= ドラッグ選択を開始させない）。
        self._suppress_drag = False
        self.setHeaderHidden(True)
        self.setIconSize(QSize(_DEFAULT_ICON_SIZE, _DEFAULT_ICON_SIZE))
        self.setIndentation(4)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        delegate = _GenerationItemDelegate(self)
        delegate.title_committed.connect(self.title_changed)
        self.setItemDelegate(delegate)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)
        self.itemDoubleClicked.connect(self._on_dbl_click)
        self.itemClicked.connect(self._on_click)
        self.setStyleSheet(
            f"QTreeWidget {{ background: {SURFACE0}; border: 1px solid {SURFACE2}; }}"
            f"QTreeWidget::item {{ padding: 2px 2px; }}"
            f"QTreeWidget::item:selected {{ background: {ACCENT}; color: {SURFACE0}; }}"
            f"QTreeWidget::branch {{ width: 0px; image: none; border-image: none; }}"
            f"QTreeWidget::branch:has-children {{ image: none; border-image: none; }}"
            f"QTreeWidget::branch:open:has-children {{ image: none; border-image: none; }}"
            f"QTreeWidget::branch:closed:has-children {{ image: none; border-image: none; }}"
        )

    def startDrag(self, supported_actions) -> None:  # type: ignore[override]
        indexes = self.selectedIndexes()
        if not indexes:
            return
        mime = self.model().mimeData(indexes)
        if mime is None:
            return
        item = self.currentItem()
        if item is not None and item.data(0, _ROLE_TYPE) == "generation":
            gen_id = item.data(0, _ROLE_ID)
            custom_mime = QMimeData()
            for fmt in mime.formats():
                custom_mime.setData(fmt, mime.data(fmt))
            custom_mime.setData(HISTORY_GENERATION_MIME, str(gen_id).encode("ascii"))
            mime = custom_mime
        drag = QDrag(self)
        drag.setMimeData(mime)

        rect = self.visualItemRect(item) if item is not None else self.viewport().rect()
        if not rect.isNull():
            pixmap = self.viewport().grab(rect)
            drag.setPixmap(translucent_drag_pixmap(pixmap))
            drag.setHotSpot(self.viewport().mapFromGlobal(self.cursor().pos()) - rect.topLeft())

        drag.exec(supported_actions, self.defaultDropAction())

    def mousePressEvent(self, event) -> None:
        # 独自処理して return する押下では super を呼ばないため、Qt 内部の押下状態が
        # 未確定のまま残る。直後の mouseMoveEvent でラバーバンド選択が暴発するのを
        # 防ぐため、これらの経路では _suppress_drag を立てる（解除は mouseReleaseEvent）。
        # 通常経路（super に委譲）では必ず False に戻す。
        if event.button() == Qt.MouseButton.LeftButton:
            item = self.itemAt(event.pos())
            if item and item.data(0, _ROLE_TYPE) == "group" and self._folder_hit_at_pos(item, event.pos()):
                self._suppress_drag = True
                self.setCurrentItem(item)
                self.group_focused.emit(item.data(0, _ROLE_ID))
                item.setExpanded(not item.isExpanded())
                event.accept()
                return
            if item and item.data(0, _ROLE_TYPE) in ("generation", "draft"):
                if item.data(0, _ROLE_TYPE) == "draft":
                    owner = item.data(0, _ROLE_DRAFT_HISTORY_DB) or ""
                    draft_id = item.data(0, _ROLE_ID)
                    if self._tile_mode_at_pos(item, event.pos()):
                        self._suppress_drag = True
                        if owner and draft_id is not None:
                            self.draft_tile_mode_requested.emit(str(owner), int(draft_id))
                        event.accept()
                        return
                    if self._history_map_at_pos(item, event.pos()):
                        self._suppress_drag = True
                        if owner and draft_id is not None:
                            self.draft_history_map_requested.emit(str(owner), int(draft_id))
                        event.accept()
                        return
                    self._suppress_drag = False
                    return super().mousePressEvent(event)
                if self._tile_mode_at_pos(item, event.pos()):
                    self._suppress_drag = True
                    gen_id = item.data(0, _ROLE_ID)
                    if gen_id is not None:
                        self.tile_mode_requested.emit(int(gen_id))
                    event.accept()
                    return
                if self._history_map_at_pos(item, event.pos()):
                    self._suppress_drag = True
                    gen_id = item.data(0, _ROLE_ID)
                    if gen_id is not None:
                        self.history_map_requested.emit(int(gen_id))
                    event.accept()
                    return
                if self._title_at_pos(item, event.pos()):
                    self._suppress_drag = True
                    self.setCurrentItem(item)
                    self.editItem(item, 0)
                    return
                rating = self._rating_at_pos(item, event.pos())
                if rating is not None:
                    self._suppress_drag = True
                    gen_id = item.data(0, _ROLE_ID)
                    current = int(item.data(0, _ROLE_RATING) or 0)
                    new_rating = 0 if int(rating) == current else int(rating)
                    if gen_id is not None:
                        self.rating_changed.emit(int(gen_id), int(new_rating))
                    return
        self._suppress_drag = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        # 独自処理した押下（_suppress_drag=True）の最中はドラッグ選択を開始させない。
        # super を呼ばないことで QAbstractItemView のラバーバンド選択ロジックを抑止する。
        if self._suppress_drag:
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._suppress_drag:
            # 独自処理した押下に対応する離上。抑止フラグを解除し、素の選択処理は走らせない。
            self._suppress_drag = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Delete:
            gen_items = [
                item for item in self.selectedItems()
                if item.data(0, _ROLE_TYPE) == "generation"
            ]
            if gen_items:
                self._trash_selected(gen_items)
                event.accept()
                return
        super().keyPressEvent(event)

    def _generation_item_layout(self, item: QTreeWidgetItem) -> dict[str, object] | None:
        rect = self.visualItemRect(item)
        if rect.isNull():
            return None
        return _GenerationItemDelegate.row_layout(
            rect, self.iconSize().width(), self.fontMetrics().lineSpacing()
        )

    def _title_at_pos(self, item: QTreeWidgetItem, pos: QPoint) -> bool:
        layout = self._generation_item_layout(item)
        if layout is None:
            return False
        title_rect: QRect = layout["title"]  # type: ignore[assignment]
        return title_rect.adjusted(-2, -2, 2, 2).contains(pos)

    def _rating_at_pos(self, item: QTreeWidgetItem, pos: QPoint) -> int | None:
        layout = self._generation_item_layout(item)
        if layout is None:
            return None
        fm = self.fontMetrics()
        stars_rect: QRect = layout["stars"]  # type: ignore[assignment]
        star_w = max(16, fm.horizontalAdvance("★"))
        hit_rect = QRect(
            stars_rect.left() - 4,
            stars_rect.top() - 2,
            star_w * 5 + 8,
            stars_rect.height() + 4,
        )
        if not hit_rect.contains(pos):
            return None
        return min(5, max(1, (pos.x() - stars_rect.left()) // star_w + 1))

    def _tile_mode_at_pos(self, item: QTreeWidgetItem, pos: QPoint) -> bool:
        layout = self._generation_item_layout(item)
        if layout is None:
            return False
        tile_btn: QRect = layout["tile_btn"]  # type: ignore[assignment]
        return tile_btn.adjusted(-3, -3, 3, 3).contains(pos)

    def _history_map_at_pos(self, item: QTreeWidgetItem, pos: QPoint) -> bool:
        layout = self._generation_item_layout(item)
        if layout is None:
            return False
        map_btn: QRect = layout["map_btn"]  # type: ignore[assignment]
        return map_btn.adjusted(-3, -3, 3, 3).contains(pos)

    def _folder_hit_at_pos(self, item: QTreeWidgetItem, pos: QPoint) -> bool:
        rect = self.visualItemRect(item)
        if rect.isNull():
            return False
        text = item.text(0)
        text_w = self.fontMetrics().horizontalAdvance(text)
        hit = QRect(rect.left(), rect.top(), min(rect.width(), text_w + 12), rect.height())
        return hit.contains(pos)

    # ── クリック / ダブルクリック ────────────────────────

    def _on_click(self, item: QTreeWidgetItem, col: int) -> None:
        item_type = item.data(0, _ROLE_TYPE)
        if item_type == "group":
            self.group_focused.emit(item.data(0, _ROLE_ID))
        elif item_type == "generation":
            parent = item.parent()
            if parent and parent.data(0, _ROLE_TYPE) == "group":
                self.group_focused.emit(parent.data(0, _ROLE_ID))
            else:
                self.group_focused.emit(None)

    def _on_dbl_click(self, item: QTreeWidgetItem, col: int) -> None:
        if item.data(0, _ROLE_TYPE) == "generation":
            self.gen_clicked.emit(item.data(0, _ROLE_ID))
        elif item.data(0, _ROLE_TYPE) == "draft":
            owner = item.data(0, _ROLE_DRAFT_HISTORY_DB) or ""
            draft_id = item.data(0, _ROLE_ID)
            if owner and draft_id is not None:
                self.draft_clicked.emit(str(owner), int(draft_id))

    # ── コンテキストメニュー ─────────────────────────────

    def _on_context_menu(self, pos: QPoint) -> None:
        item      = self.itemAt(pos)
        selected  = self.selectedItems()
        menu      = QMenu(self)

        # ── 複数選択時: 生成アイテムへの一括操作 ──────────────
        gen_sel = [it for it in selected if it.data(0, _ROLE_TYPE) == "generation"]
        if len(gen_sel) > 1:
            n = len(gen_sel)
            act_trash = menu.addAction(tr("side_panel.ctx_trash_n", n=n))
            act_trash.triggered.connect(lambda: self._trash_selected(gen_sel))

            act_move = menu.addAction(tr("side_panel.ctx_move_n", n=n))
            act_move.triggered.connect(lambda: self._move_selected_dialog(gen_sel))
            menu.exec(self.viewport().mapToGlobal(pos))
            return

        # ── 単一 / 空白クリック ───────────────────────────────
        if item is None:
            act = menu.addAction(tr("side_panel.ctx_new_root_group"))
            act.triggered.connect(lambda: self._create_group(parent_id=None))
        else:
            item_type = item.data(0, _ROLE_TYPE)

            if item_type == "group":
                gid  = item.data(0, _ROLE_ID)

                act_sub = menu.addAction(tr("side_panel.ctx_new_subgroup"))
                act_sub.triggered.connect(lambda: self._create_group(parent_id=gid))

                act_edit = menu.addAction(tr("side_panel.ctx_edit_group"))
                act_edit.triggered.connect(lambda: self._edit_group(gid))

                menu.addSeparator()

                act_del_keep = menu.addAction(tr("side_panel.ctx_del_group_keep"))
                act_del_keep.triggered.connect(lambda: self._delete_group(gid, keep_contents=True))

                act_del_all = menu.addAction(tr("side_panel.ctx_del_group_all"))
                act_del_all.triggered.connect(lambda: self._delete_group(gid, keep_contents=False))

            elif item_type == "generation":
                gen_id = item.data(0, _ROLE_ID)

                act_review = menu.addAction(tr("side_panel.ctx_review"))
                act_review.triggered.connect(lambda: self.gen_double_clicked.emit(gen_id))

                act_prompt = menu.addAction(tr("side_panel.ctx_prompt_load"))
                act_prompt.triggered.connect(lambda: self.gen_clicked.emit(gen_id))

                act_full = menu.addAction(tr("side_panel.ctx_full_load"))
                act_full.triggered.connect(lambda: self.gen_full_load_requested.emit(gen_id))

                menu.addSeparator()

                # ローカル画像操作（local_path が設定されている時のみ有効）
                _lp_row = _history_db.fetchone(
                    "SELECT local_path, invoke_image_name FROM generations WHERE id=?",
                    (gen_id,),
                )
                _local_path = _lp_row["local_path"] if _lp_row else None
                _image_name = _lp_row["invoke_image_name"] if _lp_row else None
                _resolved_image = resolve_generation_image_path(_local_path, _image_name)

                act_open_img = menu.addAction(tr("side_panel.ctx_open_image"))
                act_open_img.setEnabled(_resolved_image is not None or bool(_image_name))
                act_open_img.triggered.connect(lambda: self._open_gen_image(gen_id))

                act_open_dir = menu.addAction(tr("side_panel.ctx_open_folder"))
                act_open_dir.setEnabled(bool(_resolved_image and _resolved_image.parent.exists()) or bool(_image_name))
                act_open_dir.triggered.connect(lambda: self._open_gen_folder(gen_id))

                # サムネ更新（取得元: API → ローカル保存画像。どちらも無ければ無効）
                act_thumb = menu.addAction(tr("side_panel.ctx_refresh_thumb"))
                act_thumb.setEnabled(
                    bool(_image_name) or _resolved_image is not None
                )
                act_thumb.triggered.connect(
                    lambda: self.gen_thumb_refresh_requested.emit(gen_id)
                )

                menu.addSeparator()

                act_new_grp = menu.addAction(tr("side_panel.ctx_new_group_here"))
                act_new_grp.triggered.connect(lambda: self._create_group_at_gen(gen_id))

                menu.addSeparator()

                act_move = menu.addAction(tr("side_panel.ctx_move_to_group"))
                act_move.triggered.connect(lambda: self._move_gen_dialog(gen_id))

                menu.addSeparator()

                act_trash = menu.addAction(tr("side_panel.ctx_trash_one"))
                act_trash.triggered.connect(lambda: self._trash_generation(gen_id))

            elif item_type == "draft":
                owner = item.data(0, _ROLE_DRAFT_HISTORY_DB) or ""
                draft_id = item.data(0, _ROLE_ID)
                act_prompt = menu.addAction(tr("side_panel.ctx_prompt_load"))
                act_prompt.triggered.connect(
                    lambda: self.draft_clicked.emit(str(owner), int(draft_id))
                )
                act_map = menu.addAction(tr("editor.history_map_tooltip"))
                act_map.triggered.connect(
                    lambda: self.draft_history_map_requested.emit(str(owner), int(draft_id))
                )
                menu.addSeparator()
                has_children = True
                try:
                    import db.hmap_db as _hmap_db
                    has_children = bool(_hmap_db.child_keys(f"draft:{owner}", int(draft_id)))
                except Exception:
                    has_children = True
                act_delete = menu.addAction(
                    tr("history_map.menu_delete_draft_blocked")
                    if has_children else tr("history_map.menu_delete")
                )
                act_delete.setEnabled(not has_children)
                act_delete.triggered.connect(
                    lambda: self.draft_delete_requested.emit(str(owner), int(draft_id))
                )

        menu.exec(self.viewport().mapToGlobal(pos))

    # ── グループ操作 ─────────────────────────────────────

    @staticmethod
    def _group_name(gid: int) -> str:
        row = _history_db.fetchone("SELECT name FROM generation_groups WHERE id = ?", (gid,))
        return row["name"] if row else f"ID={gid}"

    def _create_group(self, parent_id: int | None) -> None:
        name, ok = QInputDialog.getText(self, tr("side_panel.new_group_title"), tr("side_panel.new_group_label"))
        if ok and name.strip():
            _history_db.execute(
                "INSERT INTO generation_groups (name, parent_id, folder_path) VALUES (?, ?, ?)",
                (name.strip(), parent_id, None),
            )
            self.tree_changed.emit()

    def _rename_group(self, gid: int, current_name: str) -> None:
        name, ok = QInputDialog.getText(
            self, tr("side_panel.rename_group_title"), tr("side_panel.rename_group_label"), text=current_name
        )
        if ok and name.strip():
            _history_db.execute(
                "UPDATE generation_groups SET name = ? WHERE id = ?",
                (name.strip(), gid),
            )
            self.tree_changed.emit()

    def _edit_group(self, gid: int) -> None:
        row = _history_db.fetchone(
            "SELECT name, folder_path, COALESCE(is_nsfw,0) AS is_nsfw "
            "FROM generation_groups WHERE id=?",
            (gid,),
        )
        if not row:
            return
        resolved_path = str(local_storage.resolve_folder_path(gid))
        dlg = _GroupEditDialog(
            self,
            name=row["name"] or "",
            is_nsfw=bool(row["is_nsfw"]),
            folder_path=row["folder_path"] or "",
            resolved_path=resolved_path,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        values = dlg.values
        if not values["name"]:
            QMessageBox.warning(
                self,
                tr("side_panel.group_edit_empty_title"),
                tr("side_panel.group_edit_empty_msg"),
            )
            return
        _history_db.execute(
            "UPDATE generation_groups SET name=?, folder_path=?, is_nsfw=? WHERE id=?",
            (
                values["name"],
                values["folder_path"] or None,
                1 if values["is_nsfw"] else 0,
                gid,
            ),
        )
        self.tree_changed.emit()

    def _delete_group(self, gid: int, keep_contents: bool) -> None:
        name = self._group_name(gid)
        if keep_contents:
            msg = tr("side_panel.del_group_keep_msg", name=name)
        else:
            msg = tr("side_panel.del_group_all_msg", name=name)
        reply = QMessageBox.question(
            self, tr("side_panel.del_group_confirm_title"), msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        if keep_contents:
            parent_row = _history_db.fetchone(
                "SELECT parent_id FROM generation_groups WHERE id = ?", (gid,)
            )
            parent_id = parent_row["parent_id"] if parent_row else None
            if parent_id is None:
                fallback_id = _history_db.ensure_default_generation_group()
                parent_id = fallback_id if fallback_id != gid else None
            if parent_id is None:
                QMessageBox.warning(
                    self,
                    tr("side_panel.del_group_confirm_title"),
                    tr("side_panel.del_group_keep_root_blocked", name=name),
                )
                return
            gen_rows = _history_db.fetchall(
                "SELECT id FROM generations WHERE group_id = ?",
                (gid,),
            )
            _history_db.execute(
                "UPDATE generation_groups SET parent_id = ? WHERE parent_id = ?",
                (parent_id, gid),
            )
            for row in gen_rows:
                self._move_generation_to_group(int(row["id"]), parent_id)
            _history_db.execute("DELETE FROM generation_groups WHERE id = ?", (gid,))
        else:
            # 生成はゴミ箱へ（ソフトデリート）、グループは物理削除
            import datetime as _dt
            now = _dt.datetime.now()
            for gen_id in self._collect_gen_ids(gid):
                _history_db.execute(
                    "UPDATE generations SET deleted_at = ? WHERE id = ?",
                    (now, gen_id),
                )
            _history_db.execute("DELETE FROM generation_groups WHERE id = ?", (gid,))
            _history_db.ensure_default_generation_group()

        self.tree_changed.emit()

    def _collect_gen_ids(self, gid: int) -> list[int]:
        """グループとその全サブグループに属する生成 ID を再帰収集"""
        ids = [r["id"] for r in _history_db.fetchall(
            "SELECT id FROM generations WHERE group_id = ?", (gid,)
        )]
        for sg in _history_db.fetchall(
            "SELECT id FROM generation_groups WHERE parent_id = ?", (gid,)
        ):
            ids.extend(self._collect_gen_ids(sg["id"]))
        return ids

    def _create_group_at_gen(self, gen_id: int) -> None:
        """生成と同じ階層（同じ親グループ）に新規グループを作成する"""
        row = _history_db.fetchone(
            "SELECT group_id FROM generations WHERE id = ?", (gen_id,)
        )
        parent_id = row["group_id"] if row else None
        self._create_group(parent_id=parent_id)

    def _set_folder_path(self, gid: int) -> None:
        """グループの保存先フォルダをOSダイアログで設定する"""
        row = _history_db.fetchone(
            "SELECT folder_path FROM generation_groups WHERE id=?", (gid,)
        )
        current = (row["folder_path"] or "") if row else ""
        chosen = QFileDialog.getExistingDirectory(
            self, tr("side_panel.folder_dialog_title"), current or str(Path.home())
        )
        if chosen:
            _history_db.execute(
                "UPDATE generation_groups SET folder_path=? WHERE id=?",
                (chosen, gid),
            )
        elif row and row["folder_path"]:
            # 空文字を選択した場合は設定をクリア
            reply = QMessageBox.question(
                self, tr("side_panel.folder_clear_title"),
                tr("side_panel.folder_clear_msg"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                _history_db.execute(
                    "UPDATE generation_groups SET folder_path=NULL WHERE id=?", (gid,)
                )

    def _open_gen_image(self, gen_id: int) -> None:
        """Resolved image fileをOSの関連アプリで開く。"""
        row = _history_db.fetchone(
            "SELECT local_path, invoke_image_name FROM generations WHERE id=?", (gen_id,)
        )
        if not row:
            return
        path = resolve_generation_image_path(row["local_path"], row["invoke_image_name"])
        if path:
            os.startfile(str(path))
            return
        image_name = str(row["invoke_image_name"] or "")
        temp_path = self._materialize_invoke_image(image_name)
        if temp_path is not None:
            os.startfile(str(temp_path))
            return
        missing = row["local_path"] or row["invoke_image_name"] or ""
        QMessageBox.warning(self, tr("side_panel.no_image_title"), tr("side_panel.no_image_msg", path=missing))

    def _open_gen_folder(self, gen_id: int) -> None:
        """Resolved image fileの親フォルダをエクスプローラーで開く。"""
        row = _history_db.fetchone(
            "SELECT local_path, invoke_image_name FROM generations WHERE id=?", (gen_id,)
        )
        if not row:
            return
        path = resolve_generation_image_path(row["local_path"], row["invoke_image_name"])
        if path is None:
            temp_path = self._materialize_invoke_image(str(row["invoke_image_name"] or ""))
            if temp_path is not None and temp_path.parent.exists():
                os.startfile(str(temp_path.parent))
                return
            missing = row["local_path"] or row["invoke_image_name"] or ""
            QMessageBox.warning(self, tr("side_panel.no_folder_title"), tr("side_panel.no_folder_msg", path=missing))
            return
        folder = path.parent
        if folder.exists():
            os.startfile(str(folder))
        else:
            QMessageBox.warning(self, tr("side_panel.no_folder_title"), tr("side_panel.no_folder_msg", path=folder))

    def _materialize_invoke_image(self, image_name: str) -> Path | None:
        image_name = str(image_name or "").strip()
        if not image_name or self._client is None:
            return None
        try:
            data = self._client.image_full(image_name)
            temp_dir = Path(tempfile.gettempdir()) / "PromptMosaic" / "invoke_images"
            temp_dir.mkdir(parents=True, exist_ok=True)
            temp_path = temp_dir / image_name
            temp_path.write_bytes(data)
            return temp_path
        except Exception:
            return None

    # ── 生成操作 ─────────────────────────────────────────

    def _trash_generation(self, gen_id: int) -> None:
        """生成をゴミ箱へ移動（ソフトデリート）"""
        import datetime as _dt
        _history_db.execute(
            "UPDATE generations SET deleted_at = ? WHERE id = ?",
            (_dt.datetime.now(), gen_id),
        )
        self.tree_changed.emit()

    def _trash_selected(self, gen_items: list[QTreeWidgetItem]) -> None:
        """選択した複数生成をまとめてゴミ箱へ"""
        import datetime as _dt
        now = _dt.datetime.now()
        n = len(gen_items)
        reply = QMessageBox.question(
            self, tr("side_panel.trash_n_title"),
            tr("side_panel.trash_n_msg", n=n),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        for it in gen_items:
            gid = it.data(0, _ROLE_ID)
            _history_db.execute(
                "UPDATE generations SET deleted_at = ? WHERE id = ?",
                (now, gid),
            )
        self.tree_changed.emit()

    def _move_selected_dialog(self, gen_items: list[QTreeWidgetItem]) -> None:
        """複数選択した生成を一括でグループへ移動するダイアログ"""
        _history_db.ensure_default_generation_group()
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("side_panel.move_n_title"))
        dlg.resize(280, 380)
        lay = QVBoxLayout(dlg)
        n = len(gen_items)
        lay.addWidget(QLabel(tr("side_panel.move_n_label", n=n)))

        picker = QTreeWidget()
        picker.setHeaderHidden(True)
        self._fill_group_picker(picker, None, None)
        picker.expandAll()
        if picker.topLevelItemCount() > 0:
            picker.setCurrentItem(picker.topLevelItem(0))
        lay.addWidget(picker)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        lay.addWidget(btns)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            sel = picker.currentItem()
            if sel:
                target_gid = sel.data(0, Qt.ItemDataRole.UserRole)
                if target_gid is None:
                    return
                for it in gen_items:
                    self._move_generation_to_group(it.data(0, _ROLE_ID), target_gid)
                self.tree_changed.emit()

    def _delete_generation(self, gen_id: int) -> None:
        """後方互換のため残す（ゴミ箱へ移動）"""
        self._trash_generation(gen_id)

    def _move_gen_dialog(self, gen_id: int) -> None:
        """グループ選択ダイアログで生成を移動する"""
        _history_db.ensure_default_generation_group()
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("side_panel.move_gen_title"))
        dlg.resize(280, 380)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel(tr("side_panel.move_gen_label")))

        picker = QTreeWidget()
        picker.setHeaderHidden(True)
        self._fill_group_picker(picker, None, None)
        picker.expandAll()
        if picker.topLevelItemCount() > 0:
            picker.setCurrentItem(picker.topLevelItem(0))
        lay.addWidget(picker)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        lay.addWidget(btns)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            sel = picker.currentItem()
            if sel:
                target_gid = sel.data(0, Qt.ItemDataRole.UserRole)
                if target_gid is not None:
                    self._set_gen_group(gen_id, target_gid)

    def _fill_group_picker(
        self, tree: QTreeWidget, parent_item: QTreeWidgetItem | None, parent_id: int | None
    ) -> None:
        rows = _history_db.fetchall(
            "SELECT id, name FROM generation_groups WHERE parent_id IS ? ORDER BY sort_order, name",
            (parent_id,),
        )
        for row in rows:
            it = QTreeWidgetItem([f"📁  {row['name']}"])
            it.setData(0, Qt.ItemDataRole.UserRole, row["id"])
            if parent_item is None:
                tree.addTopLevelItem(it)
            else:
                parent_item.addChild(it)
            self._fill_group_picker(tree, it, row["id"])

    def _set_gen_group(self, gen_id: int, group_id: int | None) -> None:
        self._move_generation_to_group(gen_id, group_id)
        self.tree_changed.emit()

    def _move_generation_to_group(self, gen_id: int, group_id: int | None) -> None:
        _history_db.execute(
            "UPDATE generations SET group_id = ? WHERE id = ?", (group_id, gen_id)
        )

    # ── ドラッグ & ドロップ ──────────────────────────────

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event) -> None:  # type: ignore[override]
        # 外部ファイルドロップ（OS エクスプローラーからのD&D）
        if event.mimeData().hasUrls() and event.source() is not self:
            target = self.itemAt(event.position().toPoint())
            if target and target.data(0, _ROLE_TYPE) == "generation":
                self._handle_external_file_drop(event, target.data(0, _ROLE_ID))
            else:
                event.ignore()
            return

        target = self.itemAt(event.position().toPoint())

        # ドロップ先グループを決定
        if target is None:
            tgt_group_id = None
        elif target.data(0, _ROLE_TYPE) == "group":
            tgt_group_id = target.data(0, _ROLE_ID)
        elif target.data(0, _ROLE_TYPE) == "generation":
            p = target.parent()
            tgt_group_id = p.data(0, _ROLE_ID) if p and p.data(0, _ROLE_TYPE) == "group" else None
        else:
            event.ignore()
            return

        # 選択アイテム全体をドロップ対象とする（複数選択 D&D 対応）
        sources = self.selectedItems()
        if not sources:
            event.ignore()
            return

        changed = False
        for source in sources:
            src_type = source.data(0, _ROLE_TYPE)
            src_id   = source.data(0, _ROLE_ID)

            if src_type == "generation":
                if tgt_group_id is None:
                    continue
                self._move_generation_to_group(src_id, tgt_group_id)
                changed = True

            elif src_type == "group":
                # ドロップ先が自分自身・循環するケースはスキップ
                if tgt_group_id == src_id or self._would_create_cycle(src_id, tgt_group_id):
                    continue
                _history_db.execute(
                    "UPDATE generation_groups SET parent_id = ? WHERE id = ?",
                    (tgt_group_id, src_id),
                )
                changed = True

        if changed:
            event.accept()
            self.tree_changed.emit()
        else:
            event.ignore()

    def _handle_external_file_drop(self, event, gen_id: int) -> None:
        """OS からドロップされたファイルを外部参照として local_path に記録する。"""
        gen_row = _history_db.fetchone(
            "SELECT group_id FROM generations WHERE id=?", (gen_id,)
        )
        if not gen_row:
            event.ignore()
            return

        for url in event.mimeData().urls():
            if not url.isLocalFile():
                continue
            src_path = Path(url.toLocalFile())
            try:
                _history_db.execute(
                    "UPDATE generations SET local_path=? WHERE id=?",
                    (str(src_path), gen_id),
                )
                event.accept()
                self.tree_changed.emit()
                return
            except OSError as e:
                QMessageBox.warning(
                    self, tr("side_panel.copy_fail_title"), tr("side_panel.copy_fail_msg", error=e)
                )
                event.ignore()
                return

        event.ignore()

    def _would_create_cycle(self, src_id: int, tgt_id: int | None) -> bool:
        """src_id を tgt_id の子にしたとき循環が生じるかを確認"""
        if tgt_id is None:
            return False
        if tgt_id == src_id:
            return True
        row = _history_db.fetchone(
            "SELECT parent_id FROM generation_groups WHERE id = ?", (tgt_id,)
        )
        if not row or row["parent_id"] is None:
            return False
        return self._would_create_cycle(src_id, row["parent_id"])


class _GroupEditDialog(QDialog):
    """履歴フォルダの名前・NSFW・保存先をまとめて編集するダイアログ。"""

    def __init__(self, parent, *, name: str, is_nsfw: bool, folder_path: str, resolved_path: str):
        super().__init__(parent)
        self.setWindowTitle(tr("side_panel.group_edit_title"))
        self.resize(520, 180)

        lay = QVBoxLayout(self)
        lay.setSpacing(8)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel(tr("side_panel.group_edit_name_label")))
        self._name_edit = QLineEdit(name)
        name_row.addWidget(self._name_edit, stretch=1)
        lay.addLayout(name_row)

        self._nsfw_cb = QCheckBox(tr("side_panel.group_edit_nsfw"))
        self._nsfw_cb.setChecked(is_nsfw)
        lay.addWidget(self._nsfw_cb)

        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel(tr("side_panel.group_edit_folder_label")))
        self._folder_edit = QLineEdit(folder_path)
        self._folder_edit.setPlaceholderText(tr("side_panel.group_edit_folder_inherit"))
        folder_row.addWidget(self._folder_edit, stretch=1)
        browse_btn = QPushButton(tr("side_panel.group_edit_browse"))
        browse_btn.clicked.connect(self._browse_folder)
        folder_row.addWidget(browse_btn)
        clear_btn = QPushButton(tr("side_panel.group_edit_clear_folder"))
        clear_btn.clicked.connect(self._folder_edit.clear)
        folder_row.addWidget(clear_btn)
        lay.addLayout(folder_row)

        resolved = QLabel(tr("side_panel.group_edit_resolved_folder", path=resolved_path or "-"))
        resolved.setWordWrap(True)
        resolved.setStyleSheet(f"color: {SUBTEXT};")
        lay.addWidget(resolved)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _browse_folder(self) -> None:
        current = self._folder_edit.text().strip() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, tr("side_panel.folder_dialog_title"), current)
        if chosen:
            self._folder_edit.setText(chosen)

    @property
    def values(self) -> dict:
        return {
            "name": self._name_edit.text().strip(),
            "is_nsfw": self._nsfw_cb.isChecked(),
            "folder_path": self._folder_edit.text().strip(),
        }


# ── 履歴タブ ────────────────────────────────────────────────────────────

class HistoryTab(QWidget):
    """グループ＋生成履歴をツリーで表示するタブ"""

    load_requested      = Signal(int)    # generation_id（プロンプトのみ）
    draft_load_requested = Signal(str, int)
    draft_delete_requested = Signal(str, int)
    full_load_requested = Signal(int)    # generation_id（全パラメータ）
    sync_requested      = Signal()
    group_focus_changed = Signal(object) # group_id (int | None)
    tile_mode_requested = Signal(int)
    draft_tile_mode_requested = Signal(str, int)
    history_map_requested = Signal(int)
    draft_history_map_requested = Signal(str, int)

    def __init__(self, client: "InvokeClient | None" = None, parent=None):
        super().__init__(parent)
        self._client = client
        self._id_to_item: dict[int, QTreeWidgetItem] = {}
        self._draft_key_to_item: dict[tuple[str, int], QTreeWidgetItem] = {}
        self._thumb_worker: _ThumbWorker | None = None
        self._icon_size = _DEFAULT_ICON_SIZE
        self._last_gen_count: int = 0
        self._destination_group_id: int | None = None
        self._rebuilding_tree = False
        # フィルター状態
        self._filter_text: str = ""
        self._filter_min_stars: int = 0
        self._filter_favorites: bool = False
        self._build_ui()

        # 外部プロセス（i2t ツール等）が DB に書き込んだ新規エントリを自動検出
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(3000)  # 3秒ごと
        self._poll_timer.timeout.connect(self._poll_new_generations)
        self._poll_timer.start()

    def _build_ui(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        # ── 検索・フィルターバー ─────────────────────────────────
        search_row = QHBoxLayout()
        search_row.setSpacing(4)

        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText(tr("side_panel.search_placeholder"))
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.setFixedHeight(24)
        self._search_edit.setFont(ui_font(-1))
        self._search_edit.setStyleSheet(
            f"QLineEdit {{ background: {SURFACE0}; color: {TEXT}; "
            f"border: 1px solid {SURFACE2}; border-radius: 3px; padding: 0 4px; }}"
        )
        self._search_edit.textChanged.connect(self._on_filter_changed)
        search_row.addWidget(self._search_edit, stretch=1)
        lay.addLayout(search_row)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(4)

        # 星フィルタ（☆☆☆☆☆ は全表示）
        self._star_lbl = QLabel(tr("side_panel.rating_filter_label"))
        self._star_lbl.setFont(ui_font(-2))
        self._star_lbl.setStyleSheet(f"color: {SUBTEXT};")
        filter_row.addWidget(self._star_lbl)
        self._star_filter = _StarFilter()
        self._star_filter.changed.connect(self._on_filter_changed)
        filter_row.addWidget(self._star_filter)

        # お気に入りトグル
        self._fav_btn = QPushButton("♡")
        self._fav_btn.setCheckable(True)
        self._fav_btn.setFixedSize(34, 22)
        self._fav_btn.setFont(ui_font(-1, bold=True))
        self._fav_btn.setToolTip(tr("side_panel.fav_tooltip"))
        self._fav_btn.setStyleSheet(
            f"QPushButton {{ background: {SURFACE1}; color: {SUBTEXT}; "
            f"border: 1px solid {SURFACE2}; border-radius: 11px; }}"
            f"QPushButton:checked {{ background: {SURFACE2}; color: {RED}; "
            f"border-color: {RED}; }}"
        )
        self._fav_btn.toggled.connect(self._on_filter_changed)
        self._fav_btn.toggled.connect(lambda checked: self._fav_btn.setText("♥" if checked else "♡"))
        filter_row.addWidget(self._fav_btn)
        filter_row.addStretch(1)

        lay.addLayout(filter_row)

        # ── ツリーウィジェット ────────────────────────────────────
        self._tree = GenerationTreeWidget()
        self._tree.gen_double_clicked.connect(self._on_gen_dbl_click)
        self._tree.gen_clicked.connect(self.load_requested.emit)  # double-click → load
        self._tree.draft_clicked.connect(self.draft_load_requested.emit)
        self._tree.draft_delete_requested.connect(self.draft_delete_requested.emit)
        self._tree.gen_full_load_requested.connect(self.full_load_requested.emit)
        self._tree.gen_thumb_refresh_requested.connect(self._refresh_gen_thumbnail)
        self._tree.tile_mode_requested.connect(self.tile_mode_requested.emit)
        self._tree.draft_tile_mode_requested.connect(self.draft_tile_mode_requested.emit)
        self._tree.history_map_requested.connect(self.history_map_requested.emit)
        self._tree.draft_history_map_requested.connect(self.draft_history_map_requested.emit)
        self._tree.group_focused.connect(self._on_group_focused)
        self._tree.tree_changed.connect(self.refresh)
        self._tree.rating_changed.connect(self._on_generation_rating_changed)
        self._tree.title_changed.connect(self._on_generation_title_changed)
        self._tree.itemExpanded.connect(self._on_tree_item_expanded)
        self._tree.itemCollapsed.connect(self._on_tree_item_collapsed)
        self._tree.verticalScrollBar().valueChanged.connect(
            lambda *_: QTimer.singleShot(0, self._load_visible_thumbnails)
        )
        lay.addWidget(self._tree, stretch=1)

        self._refresh_btn = QPushButton(tr("side_panel.refresh_btn"))
        self._refresh_btn.clicked.connect(self.sync_requested.emit)
        self._refresh_btn.clicked.connect(self.refresh)
        lay.addWidget(self._refresh_btn)

        self.refresh()

    # ── グループフォーカス ──────────────────────────────

    def _on_group_focused(self, group_id: int | None) -> None:
        self._destination_group_id = int(group_id) if group_id is not None else None
        self._apply_destination_highlight()
        self.group_focus_changed.emit(group_id)

    def restore_group_id(self, group_id: int) -> None:
        """起動時に保存されていたグループIDを復元する。"""
        self._destination_group_id = int(group_id)
        if not self._select_destination_group(group_id):
            self.refresh()
            self._select_destination_group(group_id)
        QTimer.singleShot(0, lambda gid=group_id: self._select_destination_group(gid))
        QTimer.singleShot(0, self._apply_destination_highlight)
        self.group_focus_changed.emit(group_id)

    def _select_destination_group(self, group_id: int | None) -> bool:
        if group_id is None:
            return False

        def walk(item: QTreeWidgetItem) -> QTreeWidgetItem | None:
            if item.data(0, _ROLE_TYPE) == "group" and item.data(0, _ROLE_ID) == group_id:
                return item
            for idx in range(item.childCount()):
                found = walk(item.child(idx))
                if found is not None:
                    return found
            return None

        for idx in range(self._tree.topLevelItemCount()):
            item = walk(self._tree.topLevelItem(idx))
            if item is not None:
                self._tree.blockSignals(True)
                self._tree.clearSelection()
                self._tree.setCurrentItem(item)
                item.setSelected(True)
                self._tree.blockSignals(False)
                self._apply_destination_highlight()
                return True
        return False

    def _iter_group_items(self):
        def walk(item: QTreeWidgetItem):
            if item.data(0, _ROLE_TYPE) == "group":
                yield item
            for idx in range(item.childCount()):
                yield from walk(item.child(idx))

        for idx in range(self._tree.topLevelItemCount()):
            yield from walk(self._tree.topLevelItem(idx))

    def _find_group_item(self, group_id: int | None) -> QTreeWidgetItem | None:
        if group_id is None:
            return None
        for item in self._iter_group_items():
            if item.data(0, _ROLE_ID) == group_id:
                return item
        return None

    def _clear_destination_highlight(self) -> None:
        for item in self._iter_group_items():
            item.setBackground(0, QBrush())
            item.setForeground(0, QBrush())

    def _apply_destination_highlight(self) -> None:
        self._clear_destination_highlight()
        item = self._find_group_item(self._destination_group_id)
        if item is None:
            return
        bg = QBrush(QColor("#3a3216"))
        fg = QBrush(QColor("#ffe6a3"))
        cur: QTreeWidgetItem | None = item
        while cur is not None:
            if cur.data(0, _ROLE_TYPE) == "group":
                cur.setBackground(0, bg)
                cur.setForeground(0, fg)
            cur = cur.parent()

    # ── ツリー構築 ──────────────────────────────────────

    # ── フィルター ──────────────────────────────────────

    def _on_filter_changed(self, *_args) -> None:
        """検索・フィルター条件が変わったら状態を更新して再描画する。"""
        self._filter_text     = self._search_edit.text()
        self._filter_min_stars = self._star_filter.value()
        self._filter_favorites = self._fav_btn.isChecked()
        self._star_filter.repaint()
        self._fav_btn.repaint()
        QApplication.processEvents()
        self.refresh()

    def retranslate_and_restyle(self) -> None:
        self._search_edit.setPlaceholderText(tr("side_panel.search_placeholder"))
        self._star_lbl.setText(tr("side_panel.rating_filter_label"))
        self._fav_btn.setToolTip(tr("side_panel.fav_tooltip"))
        self._refresh_btn.setText(tr("side_panel.refresh_btn"))
        self.refresh()

    def _current_group_id(self) -> int | None:
        item = self._tree.currentItem()
        if item is None:
            return None
        if item.data(0, _ROLE_TYPE) == "group":
            gid = item.data(0, _ROLE_ID)
            return int(gid) if gid is not None else None
        parent = item.parent()
        if parent and parent.data(0, _ROLE_TYPE) == "group":
            gid = parent.data(0, _ROLE_ID)
            return int(gid) if gid is not None else None
        return None

    @staticmethod
    def _tree_item_contains(parent: QTreeWidgetItem, child: QTreeWidgetItem | None) -> bool:
        if child is None:
            return False
        cur = child
        while cur is not None:
            if cur is parent:
                return True
            cur = cur.parent()
        return False

    def _on_tree_item_collapsed(self, item: QTreeWidgetItem) -> None:
        if item.data(0, _ROLE_TYPE) == "group":
            self._sync_group_folder_icon(item)
            if not self._rebuilding_tree:
                QTimer.singleShot(0, self._persist_expanded_group_ids)
        if self._tree_item_contains(item, self._tree.currentItem()):
            self._tree.clearSelection()
            self._tree.setCurrentItem(item)
            item.setSelected(True)
            if item.data(0, _ROLE_TYPE) == "group":
                self._on_group_focused(item.data(0, _ROLE_ID))
        self._tree.setFocus()

    def _has_filter(self) -> bool:
        """何らかのフィルターが有効かどうかを返す。"""
        return bool(
            self._filter_text.strip()
            or self._filter_min_stars > 0
            or self._filter_favorites
        )

    def _build_filter_sql(self) -> tuple[str, list]:
        """現在のフィルター設定から WHERE 句と params を組み立てる。"""
        where_parts = ["g.deleted_at IS NULL"]
        params: list = []

        if not self._show_nsfw_history():
            visible_group_ids = self._visible_group_ids()
            if visible_group_ids:
                placeholders = ",".join("?" for _ in visible_group_ids)
                where_parts.append(f"g.group_id IN ({placeholders})")
                params.extend(visible_group_ids)
            else:
                where_parts.append("1=0")

        q = self._filter_text.strip().lower()
        if q:
            like = f"%{q}%"
            where_parts.append(
                "(LOWER(COALESCE(r.title,'')) LIKE ?"
                " OR LOWER(COALESCE(r.review_text,'')) LIKE ?"
                " OR LOWER(COALESCE(g.model_name,'')) LIKE ?"
                " OR LOWER(COALESCE(g.scheduler,'')) LIKE ?)"
            )
            params.extend([like, like, like, like])

        if self._filter_min_stars > 0:
            where_parts.append("COALESCE(r.rating, 0) = ?")
            params.append(self._filter_min_stars)

        if self._filter_favorites:
            where_parts.append("COALESCE(r.is_favorite, 0) = 1")

        return " AND ".join(where_parts), params

    @staticmethod
    def _show_nsfw_history() -> bool:
        return _read_setting("show_nsfw", "0") == "1"

    def _visible_group_ids(self) -> list[int]:
        rows = _history_db.fetchall(
            "SELECT id, parent_id, COALESCE(is_nsfw,0) AS is_nsfw "
            "FROM generation_groups ORDER BY sort_order, name"
        )
        by_parent: dict[int | None, list] = {}
        for row in rows:
            by_parent.setdefault(row["parent_id"], []).append(row)

        visible: list[int] = []

        def walk(parent_id: int | None, hidden_parent: bool) -> None:
            for row in by_parent.get(parent_id, []):
                hidden = hidden_parent or bool(row["is_nsfw"])
                if not hidden:
                    visible.append(int(row["id"]))
                walk(int(row["id"]), hidden)

        walk(None, False)
        return visible

    def _poll_new_generations(self) -> None:
        """外部プロセスによる新規追加を検出して自動リフレッシュする。"""
        row = _history_db.fetchone(
            "SELECT COUNT(*) AS cnt FROM generations WHERE deleted_at IS NULL"
        )
        count = row["cnt"] if row else 0
        if count != self._last_gen_count:
            self._last_gen_count = count
            self.refresh()

    def refresh(self) -> None:
        """DB から全グループ・全生成を読み込みツリーを再構築する"""
        saved_expanded_ids = self._saved_expanded_group_ids()
        expanded_ids = saved_expanded_ids if saved_expanded_ids is not None else self._expanded_group_ids()
        selected_keys = self._selected_item_keys()
        current_key = self._current_item_key()
        fallback_group_id = self._current_group_id()
        scroll_value = self._tree.verticalScrollBar().value()

        # 前のローダーを止める
        if self._thumb_worker and self._thumb_worker.isRunning():
            self._thumb_worker.stop()
            self._thumb_worker.wait(500)

        self._icon_size = _DEFAULT_ICON_SIZE
        self._tree.setIconSize(QSize(self._icon_size, self._icon_size))

        self._rebuilding_tree = True
        try:
            self._tree.clear()
            self._id_to_item.clear()
            self._draft_key_to_item.clear()

            # ポーリング用カウントを同期（refresh 後は差分なしと見なす）
            cnt_row = _history_db.fetchone(
                "SELECT COUNT(*) AS cnt FROM generations WHERE deleted_at IS NULL"
            )
            self._last_gen_count = cnt_row["cnt"] if cnt_row else 0

            if self._has_filter():
                # ── フィルター検索モード: 条件に合う履歴をフラット表示 ─────
                where_clause, params = self._build_filter_sql()
                all_gens = _history_db.fetchall(self._generation_select_sql(where_clause), tuple(params))

                # ── フィルター検索モード: フラットリストで表示 ────────
                hdr = QTreeWidgetItem()
                hdr.setText(0, tr("side_panel.search_results_header", n=len(all_gens)))
                hdr.setFlags(Qt.ItemFlag.ItemIsEnabled)
                hdr.setFont(0, ui_font(-1, bold=True))
                hdr.setForeground(0, __import__("PySide6.QtGui", fromlist=["QColor"]).QColor(ACCENT))
                self._tree.addTopLevelItem(hdr)

                for row in all_gens:
                    it = self._make_gen_item(row)
                    hdr.addChild(it)
                    self._id_to_item[row["id"]] = it

                hdr.setExpanded(True)
                QTimer.singleShot(0, self._load_visible_thumbnails)

            else:
                # ── 通常モード: グループだけ先に作り、履歴は展開時に読む ──

                all_groups_raw = _history_db.fetchall(
                    "SELECT id, name, parent_id, COALESCE(is_nsfw,0) AS is_nsfw "
                    "FROM generation_groups ORDER BY sort_order, name"
                )
                if self._show_nsfw_history():
                    all_groups = all_groups_raw
                else:
                    visible_ids = set(self._visible_group_ids())
                    all_groups = [g for g in all_groups_raw if g["id"] in visible_ids]
                gen_counts = {
                    row["group_id"]: row["cnt"]
                    for row in _history_db.fetchall(
                        "SELECT group_id, COUNT(*) AS cnt FROM generations "
                        "WHERE deleted_at IS NULL GROUP BY group_id"
                    )
                    if row["group_id"] in {g["id"] for g in all_groups}
                }
                try:
                    import db.hmap_db as _hmap_db
                    from db.connections import get_active_history_name
                    draft_counts = {
                        row["group_id"]: row["cnt"]
                        for row in _hmap_db.fetchall(
                            "SELECT group_id, COUNT(*) AS cnt FROM editor_history_draft_nodes "
                            "WHERE owner_history_db=? AND deleted_at IS NULL GROUP BY group_id",
                            (get_active_history_name(),),
                        )
                        if row["group_id"] in {g["id"] for g in all_groups}
                    }
                except Exception:
                    draft_counts = {}
                group_map   = {g["id"]: g for g in all_groups}
                sorted_grps = self._topo_sort(list(all_groups), group_map)
                group_items: dict[int, QTreeWidgetItem] = {}

                for g in sorted_grps:
                    it = QTreeWidgetItem()
                    it.setText(0, self._group_item_text(str(g["name"]), bool(g["is_nsfw"]), expanded=False))
                    it.setData(0, _ROLE_TYPE, "group")
                    it.setData(0, _ROLE_ID, g["id"])
                    it.setData(0, _ROLE_GROUP_NAME, g["name"])
                    it.setData(0, _ROLE_GROUP_NSFW, bool(g["is_nsfw"]))
                    it.setData(0, _ROLE_LOADED, False)
                    it.setFont(0, ui_font(bold=True))
                    it.setFlags(it.flags() | Qt.ItemFlag.ItemIsDropEnabled)
                    group_items[g["id"]] = it

                    if g["parent_id"] is None:
                        self._tree.addTopLevelItem(it)
                    else:
                        parent_it = group_items.get(g["parent_id"])
                        if parent_it:
                            parent_it.addChild(it)
                        else:
                            self._tree.addTopLevelItem(it)

                for gid, item in group_items.items():
                    if (gen_counts.get(gid, 0) + draft_counts.get(gid, 0)) > 0 and item.childCount() == 0:
                        item.addChild(self._make_placeholder_item())

                for gid in expanded_ids:
                    item = group_items.get(gid)
                    if item is None:
                        continue
                    item.setExpanded(True)
                    self._sync_group_folder_icon(item)
                    if not item.data(0, _ROLE_LOADED):
                        self._load_group_generations(item)
                if expanded_ids:
                    QTimer.singleShot(0, self._load_visible_thumbnails)
        finally:
            self._rebuilding_tree = False
        self._restore_tree_state(selected_keys, current_key, scroll_value, fallback_group_id)
        self._apply_destination_highlight()

    def _expanded_group_ids(self) -> set[int]:
        ids: set[int] = set()

        def collect(item: QTreeWidgetItem) -> None:
            if item.data(0, _ROLE_TYPE) == "group" and item.isExpanded():
                gid = item.data(0, _ROLE_ID)
                if gid is not None:
                    ids.add(int(gid))
            for i in range(item.childCount()):
                collect(item.child(i))

        for i in range(self._tree.topLevelItemCount()):
            collect(self._tree.topLevelItem(i))
        return ids

    @staticmethod
    def _saved_expanded_group_ids() -> set[int] | None:
        row = _app_db.fetchone(
            "SELECT value FROM app_settings WHERE key=?",
            ("history_tree_expanded_group_ids",),
        )
        if row is None:
            return None
        try:
            raw = json.loads(row["value"] or "[]")
        except Exception:
            return set()
        ids: set[int] = set()
        for value in raw:
            try:
                ids.add(int(value))
            except Exception:
                pass
        return ids

    def _persist_expanded_group_ids(self) -> None:
        if self._rebuilding_tree:
            return
        ids = sorted(self._expanded_group_ids())
        _app_db.execute(
            "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
            ("history_tree_expanded_group_ids", json.dumps(ids)),
        )

    def save_tree_state(self) -> None:
        self._persist_expanded_group_ids()

    def _selected_item_keys(self) -> set[tuple[str, int]]:
        keys: set[tuple[str, int]] = set()
        for item in self._tree.selectedItems():
            key = self._item_key(item)
            if key is not None:
                keys.add(key)
        return keys

    def _current_item_key(self) -> tuple[str, int] | None:
        return self._item_key(self._tree.currentItem())

    @staticmethod
    def _item_key(item: QTreeWidgetItem | None) -> tuple[str, int] | None:
        if item is None:
            return None
        typ = item.data(0, _ROLE_TYPE)
        ident = item.data(0, _ROLE_ID)
        if typ in ("group", "generation") and ident is not None:
            return str(typ), int(ident)
        if typ == "draft" and ident is not None:
            owner = item.data(0, _ROLE_DRAFT_HISTORY_DB) or ""
            return f"draft:{owner}", int(ident)
        return None

    def _restore_tree_state(
        self,
        selected_keys: set[tuple[str, int]],
        current_key: tuple[str, int] | None,
        scroll_value: int,
        fallback_group_id: int | None = None,
    ) -> None:
        if not selected_keys and current_key is None and fallback_group_id is None and scroll_value <= 0:
            self._tree.setFocus()
            return
        self._tree.clearSelection()

        current_item: QTreeWidgetItem | None = None
        fallback_item: QTreeWidgetItem | None = None

        def walk(item: QTreeWidgetItem) -> None:
            nonlocal current_item, fallback_item
            key = self._item_key(item)
            if key in selected_keys:
                item.setSelected(True)
            if key == current_key:
                current_item = item
            if key == ("group", fallback_group_id):
                fallback_item = item
            for idx in range(item.childCount()):
                walk(item.child(idx))

        for idx in range(self._tree.topLevelItemCount()):
            walk(self._tree.topLevelItem(idx))

        if current_item is not None:
            self._tree.setCurrentItem(current_item)
        elif fallback_item is not None:
            self._tree.setCurrentItem(fallback_item)
            fallback_item.setSelected(True)
        self._tree.setFocus()
        QTimer.singleShot(0, lambda v=scroll_value: self._tree.verticalScrollBar().setValue(v))

    @staticmethod
    def _generation_select_sql(where_clause: str) -> str:
        return f"""
            SELECT g.id, g.invoke_image_name, g.model_name, g.created_at, g.group_id,
                   g.scheduler, g.width, g.height, g.steps, g.cfg_scale,
                   COALESCE(g.image_count, 1) AS image_count,
                   r.rating, r.title, r.is_favorite, r.review_text,
                   t.name AS template_name
            FROM generations g
            LEFT JOIN image_reviews r ON r.generation_id = g.id
            LEFT JOIN env.templates t ON t.id = g.template_id
            WHERE {where_clause}
            ORDER BY g.created_at DESC, g.id DESC
        """

    @staticmethod
    def _make_placeholder_item() -> QTreeWidgetItem:
        item = QTreeWidgetItem()
        item.setData(0, _ROLE_TYPE, "placeholder")
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        return item

    @staticmethod
    def _group_item_text(name: str, is_nsfw: bool, *, expanded: bool) -> str:
        icon = "📂" if expanded else "📁"
        nsfw_mark = "🔞" if is_nsfw else ""
        return f"{icon}  {nsfw_mark}{name}"

    def _sync_group_folder_icon(self, item: QTreeWidgetItem) -> None:
        if item.data(0, _ROLE_TYPE) != "group":
            return
        name = str(item.data(0, _ROLE_GROUP_NAME) or "")
        is_nsfw = bool(item.data(0, _ROLE_GROUP_NSFW))
        if not name:
            text = item.text(0).strip()
            name = text[2:].strip() if text.startswith(("📁", "📂")) else text
        item.setText(0, self._group_item_text(name, is_nsfw, expanded=item.isExpanded()))

    def _make_gen_item(
        self, row
    ) -> "QTreeWidgetItem":
        """生成行 DB row から QTreeWidgetItem を作成する。サムネイルは表示時に遅延読み込み。"""
        it = QTreeWidgetItem()
        self._apply_gen_item_data(it, row)
        return it

    def _make_draft_item(self, row) -> "QTreeWidgetItem":
        it = QTreeWidgetItem()
        owner = str(row["owner_history_db"])
        draft_id = int(row["id"])
        created = str(row["updated_at"] or row["created_at"] or "")[:16]
        title = str(row["title"] or tr("history_map.draft_badge"))
        parent_label = ""
        parent_db = str(row["parent_db"] or "")
        parent_id = row["parent_id"]
        if parent_db and parent_id is not None:
            parent_label = tr("side_panel.draft_from", id=int(parent_id))

        it.setText(0, "")
        it.setData(0, _ROLE_TYPE, "draft")
        it.setData(0, _ROLE_ID, draft_id)
        it.setData(0, _ROLE_DRAFT_HISTORY_DB, owner)
        it.setData(0, _ROLE_IMAGE_NAME, "")
        it.setData(0, _ROLE_THUMB_LOADED, True)
        it.setData(0, _ROLE_RATING, 0)
        it.setData(0, _ROLE_TITLE, title)
        it.setData(0, _ROLE_DETAIL, "")
        it.setData(0, _ROLE_FAVORITE, False)
        it.setData(0, _ROLE_GEN_NO, tr("history_map.draft_node_label", n=draft_id))
        it.setData(0, _ROLE_CREATED, created)
        it.setData(0, _ROLE_MODEL, parent_label)
        it.setData(0, _ROLE_PARAMS, tr("side_panel.draft_no_image"))
        it.setData(0, _ROLE_REVIEW_TEXT, str(row["memo_text"] or ""))
        it.setData(0, _ROLE_IS_LINEAGE_ROOT, False)
        it.setData(0, _ROLE_IS_LINEAGE_SINGLE, False)
        it.setData(0, _ROLE_HISTORY_BG, self._history_background_color_for_key(f"draft:{owner}", draft_id))
        it.setData(0, _ROLE_HISTORY_FG, self._history_text_color_for_key(f"draft:{owner}", draft_id))
        row_height = max(self._icon_size + 8, ui_font(-1).pointSize() * 9 + 30, 112)
        it.setSizeHint(0, QSize(self._icon_size + 220, row_height))
        it.setFont(0, ui_font(-1))
        it.setFlags((it.flags() & ~Qt.ItemFlag.ItemIsEditable) & ~Qt.ItemFlag.ItemIsDropEnabled)
        return it

    @staticmethod
    def _is_lineage_root(gen_id: int) -> bool:
        """この生成がアクティブ履歴の系譜開祖（親なしノード）かどうか。"""
        try:
            import db.hmap_db as _hmap_db
            from db.connections import get_active_history_name
            node = _hmap_db.fetchone(
                "SELECT parent_db FROM editor_history_nodes "
                "WHERE history_db=? AND history_id=?",
                (get_active_history_name(), gen_id),
            )
        except Exception:
            return False
        return node is not None and node["parent_db"] is None

    @staticmethod
    def _is_lineage_single(gen_id: int) -> bool:
        """右ペイン表示用: この履歴行が単独（旗🚩表示）かどうか。

        単独＝系譜マップに属していない。具体的には
          ・系譜マップ(editor_history_nodes)に登録が無い（＝まだ枝分かれしていない）
          ・もしくは親も子も無い開祖
        のいずれか。親か子があれば系譜ツリーの一部なのでマップ🗺️表示。

        以前は登録が無い(node is None)場合に False（=マップ）を返していたため、
        本来は単独=旗のはずの履歴が多数マップ表示になり、マップアイコンを
        クリックして種を蒔いて初めて旗に変わる、という食い違いが起きていた。
        """
        try:
            import db.hmap_db as _hmap_db
            from db.connections import get_active_history_name
            history_db = get_active_history_name()
            node = _hmap_db.fetchone(
                "SELECT parent_db FROM editor_history_nodes "
                "WHERE history_db=? AND history_id=?",
                (history_db, gen_id),
            )
            if node is None:
                return True   # 系譜マップに未登録＝単独（旗）
            if node["parent_db"] is not None:
                return False  # 親がいる＝ツリーの一部（マップ）
            return not _hmap_db.child_keys(history_db, gen_id)
        except Exception:
            return False

    @staticmethod
    def _history_background_color(gen_id: int) -> str:
        try:
            import db.app_db as _app_db
            import db.hmap_db as _hmap_db
            from db.connections import get_active_history_name
            history_db = get_active_history_name()
            root = _hmap_db.find_root(history_db, int(gen_id)) or (history_db, int(gen_id))
            safe_db = "".join(ch if ch.isalnum() else "_" for ch in str(root[0]))
            key = f"history_bg_color_{safe_db}_{int(root[1])}"
            row = _app_db.fetchone("SELECT value FROM app_settings WHERE key=?", (key,))
            if row and row["value"]:
                return str(row["value"])
            digest = hashlib.sha1(f"{root[0]}:{int(root[1])}".encode("utf-8")).hexdigest()
            color = QColor()
            color.setHsv(int(digest[:8], 16) % 360, 95, 78)
            value = color.name()
            _app_db.execute(
                "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
                (key, value),
            )
            return value
        except Exception:
            return SURFACE0

    @staticmethod
    def _history_background_color_for_key(history_db: str, history_id: int) -> str:
        try:
            import db.app_db as _app_db
            import db.hmap_db as _hmap_db
            root = _hmap_db.find_root(history_db, int(history_id)) or (history_db, int(history_id))
            safe_db = "".join(ch if ch.isalnum() else "_" for ch in str(root[0]))
            key = f"history_bg_color_{safe_db}_{int(root[1])}"
            row = _app_db.fetchone("SELECT value FROM app_settings WHERE key=?", (key,))
            if row and row["value"]:
                return str(row["value"])
            digest = hashlib.sha1(f"{root[0]}:{int(root[1])}".encode("utf-8")).hexdigest()
            color = QColor()
            color.setHsv(int(digest[:8], 16) % 360, 95, 78)
            value = color.name()
            _app_db.execute(
                "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
                (key, value),
            )
            return value
        except Exception:
            return SURFACE0

    @staticmethod
    def _history_text_color(gen_id: int) -> str:
        """ツリー単位の文字色上書き > 設定のテーマ別既定(styles.HISTORY_TEXT)。"""
        try:
            import db.app_db as _app_db
            import db.hmap_db as _hmap_db
            from db.connections import get_active_history_name
            history_db = get_active_history_name()
            root = _hmap_db.find_root(history_db, int(gen_id)) or (history_db, int(gen_id))
            safe_db = "".join(ch if ch.isalnum() else "_" for ch in str(root[0]))
            key = f"history_text_color_{safe_db}_{int(root[1])}"
            row = _app_db.fetchone("SELECT value FROM app_settings WHERE key=?", (key,))
            if row and row["value"]:
                return str(row["value"])
        except Exception:
            pass
        return styles.HISTORY_TEXT

    @staticmethod
    def _history_text_color_for_key(history_db: str, history_id: int) -> str:
        try:
            import db.app_db as _app_db
            import db.hmap_db as _hmap_db
            root = _hmap_db.find_root(history_db, int(history_id)) or (history_db, int(history_id))
            safe_db = "".join(ch if ch.isalnum() else "_" for ch in str(root[0]))
            key = f"history_text_color_{safe_db}_{int(root[1])}"
            row = _app_db.fetchone("SELECT value FROM app_settings WHERE key=?", (key,))
            if row and row["value"]:
                return str(row["value"])
        except Exception:
            pass
        return styles.HISTORY_TEXT

    def _apply_gen_item_data(self, it: QTreeWidgetItem, row) -> None:
        """既存アイテムにも使えるよう、生成行の表示データだけを適用する。"""
        gen_id      = row["id"]
        model       = row["model_name"] or "—"
        tmpl_name   = row["template_name"] if "template_name" in row.keys() else None
        if tmpl_name:
            model = f"{model} / {tmpl_name}"
        created     = str(row["created_at"] or "")[:16]
        title       = row["title"] or ""
        scheduler   = row["scheduler"] or ""
        steps       = row["steps"] or 0
        cfg         = row["cfg_scale"]
        image_count = row["image_count"] or 1

        param_parts = []
        if steps:
            param_parts.append(f"Steps {steps}")
        if cfg is not None:
            param_parts.append(f"CFG {cfg:g}")
        param_parts.append(f"Count {image_count}")
        if scheduler:
            param_parts.append(scheduler)

        it.setText(0, "")
        it.setData(0, _ROLE_TYPE, "generation")
        it.setData(0, _ROLE_ID, gen_id)
        it.setData(0, _ROLE_IMAGE_NAME, row["invoke_image_name"] or "")
        it.setData(0, _ROLE_THUMB_LOADED, False)
        it.setData(0, _ROLE_RATING, int(row["rating"] or 0))
        it.setData(0, _ROLE_TITLE, title)
        it.setData(0, _ROLE_DETAIL, "")
        it.setData(0, _ROLE_FAVORITE, bool(row["is_favorite"]))
        it.setData(0, _ROLE_GEN_NO, f"[#{gen_id}]")
        it.setData(0, _ROLE_CREATED, created)
        it.setData(0, _ROLE_MODEL, model)
        it.setData(0, _ROLE_PARAMS, "  ".join(param_parts))
        it.setData(0, _ROLE_REVIEW_TEXT, row["review_text"] or "")
        is_single = self._is_lineage_single(int(gen_id))
        it.setData(0, _ROLE_IS_LINEAGE_ROOT, self._is_lineage_root(int(gen_id)))
        it.setData(0, _ROLE_IS_LINEAGE_SINGLE, is_single)
        it.setData(0, _ROLE_HISTORY_BG, SURFACE0 if is_single else self._history_background_color(int(gen_id)))
        it.setData(0, _ROLE_HISTORY_FG, self._history_text_color(int(gen_id)))
        row_height = max(self._icon_size + 8, ui_font(-1).pointSize() * 9 + 30, 112)
        it.setSizeHint(0, QSize(self._icon_size + 220, row_height))
        it.setFont(0, ui_font(-1))
        it.setFlags((it.flags() | Qt.ItemFlag.ItemIsEditable) & ~Qt.ItemFlag.ItemIsDropEnabled)

    def refresh_generation_items(self, gen_ids: list[int]) -> None:
        """表示中の履歴アイテムだけを更新する。見えていない分は次回展開時にDBから読む。"""
        if not gen_ids:
            return
        updated = False
        for gen_id in gen_ids:
            item = self._id_to_item.get(int(gen_id))
            if item is None:
                continue
            row = _history_db.fetchone(
                self._generation_select_sql("g.deleted_at IS NULL AND g.id = ?"),
                (int(gen_id),),
            )
            if not row:
                continue
            self._apply_gen_item_data(item, row)
            item.setIcon(0, QIcon())
            self._tree.viewport().update(self._tree.visualItemRect(item))
            updated = True
        if updated:
            QTimer.singleShot(0, self._load_visible_thumbnails)

    def focus_generation(self, gen_id: int, *, animate: bool = True, flash: bool = True) -> bool:
        """履歴マップから指定された履歴行を右ペイン内で見つけて主張表示する。"""
        gen_id = int(gen_id)
        item = self._id_to_item.get(gen_id)
        if item is None and not self._has_filter():
            row = _history_db.fetchone(
                "SELECT group_id FROM generations WHERE id=? AND deleted_at IS NULL",
                (gen_id,),
            )
            if row is not None:
                group_id = row["group_id"]
                group_item = self._find_group_item(int(group_id)) if group_id is not None else None
                if group_item is None:
                    self.refresh()
                    group_item = self._find_group_item(int(group_id)) if group_id is not None else None
                if group_item is not None:
                    self._expand_group_path(group_item)
                    if not group_item.data(0, _ROLE_LOADED):
                        self._load_group_generations(group_item)
                    group_item.setExpanded(True)
                    self._sync_group_folder_icon(group_item)
                    self._persist_expanded_group_ids()
                    item = self._id_to_item.get(gen_id)
        if item is None:
            return False
        self._select_generation_item(item)
        if animate:
            QTimer.singleShot(0, lambda it=item: self._scroll_to_item_animated(it))
        else:
            self._tree.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)
        if flash:
            QTimer.singleShot(120, lambda it=item: self._flash_generation_item(it))
        QTimer.singleShot(160, self._load_visible_thumbnails)
        return True

    def focus_draft(self, owner_history_db: str, draft_id: int, *, animate: bool = True, flash: bool = True) -> bool:
        owner_history_db = str(owner_history_db)
        draft_id = int(draft_id)
        item = self._draft_key_to_item.get((owner_history_db, draft_id))
        if item is None and not self._has_filter():
            try:
                import db.hmap_db as _hmap_db
                row = _hmap_db.fetch_draft_node(owner_history_db, draft_id)
            except Exception:
                row = None
            if row is not None and row["group_id"] is not None:
                group_item = self._find_group_item(int(row["group_id"]))
                if group_item is None:
                    self.refresh()
                    group_item = self._find_group_item(int(row["group_id"]))
                if group_item is not None:
                    self._expand_group_path(group_item)
                    if not group_item.data(0, _ROLE_LOADED):
                        self._load_group_generations(group_item)
                    group_item.setExpanded(True)
                    self._sync_group_folder_icon(group_item)
                    self._persist_expanded_group_ids()
                    item = self._draft_key_to_item.get((owner_history_db, draft_id))
        if item is None:
            return False
        self._select_generation_item(item)
        if animate:
            QTimer.singleShot(0, lambda it=item: self._scroll_to_item_animated(it))
        else:
            self._tree.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)
        if flash:
            QTimer.singleShot(120, lambda it=item: self._flash_generation_item(it))
        return True

    def _expand_group_path(self, item: QTreeWidgetItem) -> None:
        chain: list[QTreeWidgetItem] = []
        cur: QTreeWidgetItem | None = item
        while cur is not None:
            if cur.data(0, _ROLE_TYPE) == "group":
                chain.append(cur)
            cur = cur.parent()
        for group_item in reversed(chain):
            group_item.setExpanded(True)
            self._sync_group_folder_icon(group_item)

    def _scroll_to_item_animated(self, item: QTreeWidgetItem) -> None:
        if item.data(0, _ROLE_TYPE) not in ("generation", "draft"):
            return
        bar = self._tree.verticalScrollBar()
        start = bar.value()
        self._tree.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)
        end = bar.value()
        if start == end:
            self._tree.viewport().update(self._tree.visualItemRect(item))
            return
        bar.setValue(start)
        anim = QPropertyAnimation(bar, b"value", self)
        anim.setDuration(360)
        anim.setStartValue(start)
        anim.setEndValue(end)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._focus_scroll_anim = anim
        anim.finished.connect(lambda: setattr(self, "_focus_scroll_anim", None))
        anim.start()

    def _flash_generation_item(self, item: QTreeWidgetItem) -> None:
        if item.data(0, _ROLE_TYPE) not in ("generation", "draft"):
            return
        phases = [1, 2, 3, 4, 5, 6, 7, 8, 0]

        def step(idx: int = 0) -> None:
            try:
                item.setData(0, _ROLE_FOCUS_FLASH, phases[idx])
                self._tree.viewport().update(self._tree.visualItemRect(item))
            except RuntimeError:
                return
            if idx + 1 < len(phases):
                QTimer.singleShot(90, lambda: step(idx + 1))

        step()

    def _select_generation_item(self, item: QTreeWidgetItem) -> None:
        self._tree.setCurrentItem(item)
        self._tree.clearSelection()
        item.setSelected(True)
        parent = item.parent()
        if parent and parent.data(0, _ROLE_TYPE) == "group":
            self._on_group_focused(parent.data(0, _ROLE_ID))
        else:
            self._on_group_focused(None)

    def _on_generation_rating_changed(self, gen_id: int, rating: int) -> None:
        _history_db.execute(
            """
            INSERT INTO image_reviews (generation_id, rating, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(generation_id) DO UPDATE SET
                rating = excluded.rating,
                updated_at = CURRENT_TIMESTAMP
            """,
            (gen_id, rating if rating else None),
        )
        item = self._id_to_item.get(gen_id)
        if item is not None:
            item.setData(0, _ROLE_RATING, int(rating or 0))
            self._tree.viewport().update(self._tree.visualItemRect(item))
        if self._has_filter():
            self.refresh()

    def _on_generation_title_changed(self, gen_id: int, title: str) -> None:
        _history_db.execute(
            """
            INSERT INTO image_reviews (generation_id, title, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(generation_id) DO UPDATE SET
                title = excluded.title,
                updated_at = CURRENT_TIMESTAMP
            """,
            (gen_id, title or None),
        )
        item = self._id_to_item.get(gen_id)
        if item is not None:
            item.setData(0, _ROLE_TITLE, title)
            self._tree.viewport().update(self._tree.visualItemRect(item))

    def _on_tree_item_expanded(self, item: QTreeWidgetItem) -> None:
        if item.data(0, _ROLE_TYPE) != "group":
            return
        self._sync_group_folder_icon(item)
        if not self._rebuilding_tree:
            QTimer.singleShot(0, self._persist_expanded_group_ids)
        if item.data(0, _ROLE_LOADED):
            QTimer.singleShot(0, self._load_visible_thumbnails)
            return
        self._load_group_generations(item)
        QTimer.singleShot(0, self._load_visible_thumbnails)

    def _load_group_generations(self, item: QTreeWidgetItem) -> None:
        gid = item.data(0, _ROLE_ID)
        if gid is None:
            return

        # プレースホルダーだけ除去し、サブグループは維持する。
        for i in reversed(range(item.childCount())):
            child = item.child(i)
            if child.data(0, _ROLE_TYPE) == "placeholder":
                item.removeChild(child)

        rows = _history_db.fetchall(
            self._generation_select_sql("g.deleted_at IS NULL AND g.group_id = ?"),
            (gid,),
        )
        draft_rows = []
        active_history = ""
        try:
            import db.hmap_db as _hmap_db
            from db.connections import get_active_history_name
            active_history = get_active_history_name()
            draft_rows = _hmap_db.fetchall(
                """
                SELECT id, owner_history_db, parent_db, parent_id, group_id, prompt_json,
                       memo_text, title, created_at, updated_at, deleted_at
                  FROM editor_history_draft_nodes
                 WHERE owner_history_db=? AND group_id=? AND deleted_at IS NULL
                """,
                (active_history, gid),
            )
        except Exception:
            draft_rows = []

        drafts_by_parent: dict[tuple[str, int], list] = {}
        for row in draft_rows:
            if row["parent_db"] is None or row["parent_id"] is None:
                continue
            drafts_by_parent.setdefault(
                (str(row["parent_db"]), int(row["parent_id"])),
                [],
            ).append(row)
        for bucket in drafts_by_parent.values():
            bucket.sort(key=lambda r: (str(r["updated_at"] or r["created_at"] or ""), int(r["id"])), reverse=True)

        shown_drafts: set[tuple[str, int]] = set()
        for row in rows:
            for draft in drafts_by_parent.get((active_history, int(row["id"])), []):
                draft_item = self._make_draft_item(draft)
                item.addChild(draft_item)
                draft_key = (str(draft["owner_history_db"]), int(draft["id"]))
                self._draft_key_to_item[draft_key] = draft_item
                shown_drafts.add(draft_key)
            gen_item = self._make_gen_item(row)
            item.addChild(gen_item)
            self._id_to_item[row["id"]] = gen_item

        # 親画像が非表示・削除済みの場合だけ、ドラフトを最後に退避表示する。
        remaining = [
            row for row in draft_rows
            if (str(row["owner_history_db"]), int(row["id"])) not in shown_drafts
        ]
        remaining.sort(key=lambda r: (str(r["updated_at"] or r["created_at"] or ""), int(r["id"])), reverse=True)
        for row in remaining:
                draft_item = self._make_draft_item(row)
                item.addChild(draft_item)
                self._draft_key_to_item[(str(row["owner_history_db"]), int(row["id"]))] = draft_item
        item.setData(0, _ROLE_LOADED, True)

    def _load_visible_thumbnails(self) -> None:
        if not self.isVisible() or not self._tree.isVisible():
            return
        viewport_rect = self._tree.viewport().rect()
        tasks: list[tuple[int, str]] = []
        for gen_id, item in list(self._id_to_item.items()):
            if item.data(0, _ROLE_THUMB_LOADED):
                continue
            item_rect = self._tree.visualItemRect(item)
            if item_rect.isNull() or not item_rect.intersects(viewport_rect):
                continue
            image_name = item.data(0, _ROLE_IMAGE_NAME) or ""
            if not image_name:
                item.setData(0, _ROLE_THUMB_LOADED, True)
                continue
            pix = self._load_pixmap_for_gen(gen_id)
            if pix:
                item.setIcon(0, QIcon(pix))
                item.setData(0, _ROLE_THUMB_LOADED, True)
            else:
                item.setData(0, _ROLE_THUMB_LOADED, True)
                tasks.append((gen_id, image_name))
        if tasks:
            self._start_thumb_worker(tasks)

    def _start_thumb_worker(self, tasks: list[tuple[int, str]]) -> None:
        from db import connections
        if self._thumb_worker and self._thumb_worker.isRunning():
            self._thumb_worker.stop()
            self._thumb_worker.wait(200)
        self._thumb_worker = _ThumbWorker(
            tasks, self._client,
            connections.get_active_history_name(),
            parent=self,
        )
        self._thumb_worker.thumbnail_ready.connect(self._on_thumbnail_ready)
        self._thumb_worker.start()

    @staticmethod
    def _topo_sort(groups: list, group_map: dict) -> list:
        """親グループが子より先に来るようにトポロジカルソート"""
        result: list = []
        visited: set = set()

        def visit(g):
            if g["id"] in visited:
                return
            visited.add(g["id"])
            if g["parent_id"] is not None and g["parent_id"] in group_map:
                visit(group_map[g["parent_id"]])
            result.append(g)

        for g in groups:
            visit(g)
        return result

    # ── サムネイル ──────────────────────────────────────

    def _load_pixmap_for_gen(self, gen_id: int) -> QPixmap | None:
        row = _history_db.fetchone(
            "SELECT thumbnail_data FROM generations WHERE id=?", (gen_id,)
        )
        if row and row["thumbnail_data"]:
            pix = QPixmap()
            if pix.loadFromData(bytes(row["thumbnail_data"])) and not pix.isNull():
                return pix.scaled(
                    self._icon_size, self._icon_size,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
        return None

    def _refresh_gen_thumbnail(self, gen_id: int) -> None:
        """コンテキストメニュー「サムネ更新」: BLOB を破棄して取得し直す。
        取得元は API（invoke_image_name）→ ローカル保存画像の順（_ThumbWorker）。"""
        row = _history_db.fetchone(
            "SELECT invoke_image_name FROM generations WHERE id=?", (gen_id,)
        )
        if not row:
            return
        _history_db.execute(
            "UPDATE generations SET thumbnail_data=NULL WHERE id=?", (gen_id,)
        )
        item = self._id_to_item.get(gen_id)
        if item is not None:
            item.setData(0, _ROLE_THUMB_LOADED, False)
        self._start_thumb_worker([(gen_id, row["invoke_image_name"] or "")])

    def _on_thumbnail_ready(self, gen_id: int, data: bytes) -> None:
        it = self._id_to_item.get(gen_id)
        if it is None:
            return
        it.setData(0, _ROLE_THUMB_LOADED, True)
        pix = QPixmap()
        if pix.loadFromData(data) and not pix.isNull():
            scaled = pix.scaled(
                self._icon_size, self._icon_size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            it.setIcon(0, QIcon(scaled))
            self._tree.viewport().update(self._tree.visualItemRect(it))

    # ── ダブルクリック ──────────────────────────────────

    def _on_gen_dbl_click(self, gen_id: int) -> None:
        from ui.review_dialog import ReviewDialog
        dlg = ReviewDialog(gen_id, client=self._client, parent=self)
        dlg.load_requested.connect(self.load_requested.emit)
        dlg.review_saved.connect(lambda _: self.refresh())
        dlg.exec()


# ── ノートタブ ───────────────────────────────────────────────────────────

class NotesTab(QWidget):
    """日記ノートを日付ごとに編集するタブ"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._today = datetime.date.today().isoformat()
        self._current_date = self._today
        self._note_id: int | None = None
        self._dirty = False
        self._loading = False
        self._build_ui()
        self._refresh_date_combo(self._today)
        self._load_date(self._today)

    def _build_ui(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        hdr = QHBoxLayout()
        self._date_combo = QComboBox()
        self._date_combo.setFont(ui_font(-1))
        self._date_combo.setToolTip(tr("side_panel.notes_date_tooltip"))
        self._date_combo.currentTextChanged.connect(self._on_date_changed)
        hdr.addWidget(self._date_combo, stretch=1)
        self._today_btn = QPushButton(tr("side_panel.notes_today_btn"))
        self._today_btn.setFixedHeight(24)
        self._today_btn.clicked.connect(lambda: self._select_date(self._today))
        hdr.addWidget(self._today_btn)
        self._delete_btn = QPushButton(tr("side_panel.notes_delete_btn"))
        self._delete_btn.setFixedHeight(24)
        self._delete_btn.setStyleSheet(themed_button_style("danger"))
        self._delete_btn.clicked.connect(self._delete_current)
        hdr.addWidget(self._delete_btn)
        hdr.addStretch()
        lay.addLayout(hdr)

        self._title = QLabel(tr("side_panel.notes_title", date=self._today))
        self._title.setFont(ui_font(bold=True))
        self._title.setStyleSheet(f"color: {SUBTEXT};")
        lay.addWidget(self._title)

        self._editor = QTextEdit()
        self._editor.setPlaceholderText(tr("side_panel.notes_placeholder"))
        self._editor.setFont(ui_font())
        self._editor.textChanged.connect(self._on_text_changed)
        lay.addWidget(self._editor, stretch=1)

        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(1500)
        self._autosave_timer.timeout.connect(self.save_if_dirty)

        self._save_btn = QPushButton(tr("side_panel.save_btn"))
        self._save_btn.setStyleSheet(themed_button_style("success"))
        self._save_btn.clicked.connect(lambda: self._save(force=True))
        lay.addWidget(self._save_btn)

    def retranslate_and_restyle(self) -> None:
        self._date_combo.setToolTip(tr("side_panel.notes_date_tooltip"))
        self._today_btn.setText(tr("side_panel.notes_today_btn"))
        self._delete_btn.setText(tr("side_panel.notes_delete_btn"))
        self._title.setText(tr("side_panel.notes_title", date=self._current_date))
        self._editor.setPlaceholderText(tr("side_panel.notes_placeholder"))
        self._save_btn.setText(tr("side_panel.save_btn"))
        self._date_combo.blockSignals(True)
        for i in range(self._date_combo.count()):
            date = str(self._date_combo.itemData(i) or "")
            if date:
                label = tr("side_panel.notes_today_label", date=date) if date == self._today else date
                self._date_combo.setItemText(i, label)
        self._date_combo.blockSignals(False)

    def _refresh_date_combo(
        self,
        preferred_date: str | None = None,
        *,
        reload_selected: bool = True,
    ) -> None:
        preferred = preferred_date or self._current_date
        rows = _notes_db.fetchall(
            "SELECT date FROM daily_notes ORDER BY date DESC"
        )
        dates = [str(r["date"]) for r in rows]
        if self._today not in dates:
            dates.insert(0, self._today)
        elif dates.index(self._today) != 0:
            dates.remove(self._today)
            dates.insert(0, self._today)

        self._date_combo.blockSignals(True)
        self._date_combo.clear()
        for date in dates:
            label = tr("side_panel.notes_today_label", date=date) if date == self._today else date
            self._date_combo.addItem(str(label), date)
        self._date_combo.blockSignals(False)
        if reload_selected:
            self._select_date(preferred, save_current=False, force_load=True)
        else:
            idx = self._date_combo.findData(preferred)
            if idx >= 0:
                self._date_combo.blockSignals(True)
                self._date_combo.setCurrentIndex(idx)
                self._date_combo.blockSignals(False)

    def _select_date(
        self,
        date: str,
        *,
        save_current: bool = True,
        force_load: bool = False,
    ) -> None:
        idx = self._date_combo.findData(date)
        if idx < 0:
            return
        if save_current:
            self.save_if_dirty()
        self._date_combo.blockSignals(True)
        self._date_combo.setCurrentIndex(idx)
        self._date_combo.blockSignals(False)
        if not force_load and date == self._current_date:
            return
        self._load_date(date)

    def _on_date_changed(self, _text: str = "") -> None:
        date = self._date_combo.currentData()
        if date:
            self.save_if_dirty()
            self._load_date(str(date))

    def _on_text_changed(self) -> None:
        if self._loading:
            return
        self._dirty = True
        self._autosave_timer.start()

    def _load_date(self, date: str) -> None:
        self._autosave_timer.stop()
        self._loading = True
        self._current_date = date
        self._title.setText(tr("side_panel.notes_title", date=date))
        row = _notes_db.fetchone(
            "SELECT id, content FROM daily_notes WHERE date = ?",
            (date,),
        )
        if row:
            self._note_id = row["id"]
            self._editor.setPlainText(row["content"] or "")
        else:
            self._note_id = None
            self._editor.clear()
        self._dirty = False
        self._loading = False

    def _save(self, *, force: bool = False) -> None:
        content = self._editor.toPlainText()
        if not force and self._note_id is None and not content.strip():
            self._dirty = False
            return
        if self._note_id is None:
            result = _notes_db.execute(
                "INSERT INTO daily_notes (date, content) VALUES (?, ?)",
                (self._current_date, content),
            )
            self._note_id = result.lastrowid
            self._refresh_date_combo(self._current_date, reload_selected=False)
        else:
            _notes_db.execute(
                "UPDATE daily_notes SET content = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (content, self._note_id),
            )
        self._dirty = False

    def save_if_dirty(self) -> None:
        if self._dirty:
            self._save(force=False)

    def _delete_current(self) -> None:
        if self._note_id is None:
            self._editor.clear()
            self._dirty = False
            return
        if QMessageBox.question(
            self,
            tr("side_panel.notes_delete_title"),
            tr("side_panel.notes_delete_msg", date=self._current_date),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        _notes_db.execute("DELETE FROM daily_notes WHERE id = ?", (self._note_id,))
        self._note_id = None
        self._editor.clear()
        self._dirty = False
        self._refresh_date_combo(self._today)


# ── ゴミ箱タブ ──────────────────────────────────────────────────────────

class TrashTab(QWidget):
    """ゴミ箱（deleted_at IS NOT NULL の生成一覧）"""

    def __init__(self, client: "InvokeClient | None" = None, parent=None):
        super().__init__(parent)
        self._client = client
        self._thumb_worker: _ThumbWorker | None = None
        self._id_to_item: dict[int, QListWidgetItem] = {}
        self._icon_size = _DEFAULT_ICON_SIZE
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        self._hdr_lbl = QLabel(tr("side_panel.trash_header"))
        self._hdr_lbl.setFont(ui_font(bold=True))
        self._hdr_lbl.setStyleSheet(f"color: {SUBTEXT};")
        lay.addWidget(self._hdr_lbl)

        self._list = QListWidget()
        self._list.setIconSize(QSize(self._icon_size, self._icon_size))
        self._list.setSpacing(2)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._list.setStyleSheet(
            f"QListWidget {{ background: {SURFACE0}; border: 1px solid {SURFACE2}; }}"
            f"QListWidget::item {{ padding: 4px 6px; }}"
            f"QListWidget::item:selected {{ background: {ACCENT}; color: #1e1e2e; }}"
        )
        lay.addWidget(self._list, stretch=1)

        # ボタン行
        btn_row = QHBoxLayout()

        self._restore_btn = QPushButton(tr("side_panel.trash_restore_btn"))
        self._restore_btn.clicked.connect(self._restore_selected)
        btn_row.addWidget(self._restore_btn)

        self._perm_del_btn = QPushButton(tr("side_panel.trash_perm_del_btn"))
        self._perm_del_btn.setStyleSheet(
            "QPushButton { color: #f38ba8; border: 1px solid #f38ba8; "
            "border-radius: 3px; padding: 3px 6px; }"
            "QPushButton:hover { background: #3b1a1a; }"
        )
        self._perm_del_btn.clicked.connect(self._perm_delete_selected)
        btn_row.addWidget(self._perm_del_btn)

        lay.addLayout(btn_row)

        self._empty_btn = QPushButton(tr("side_panel.trash_empty_btn"))
        self._empty_btn.setStyleSheet(
            "QPushButton { color: #f38ba8; border: 1px solid #f38ba8; "
            "border-radius: 3px; padding: 3px 6px; }"
            "QPushButton:hover { background: #3b1a1a; }"
        )
        self._empty_btn.clicked.connect(self._empty_trash)
        lay.addWidget(self._empty_btn)

    def retranslate_and_restyle(self) -> None:
        self._hdr_lbl.setText(tr("side_panel.trash_header"))
        self._restore_btn.setText(tr("side_panel.trash_restore_btn"))
        self._perm_del_btn.setText(tr("side_panel.trash_perm_del_btn"))
        self._empty_btn.setText(tr("side_panel.trash_empty_btn"))
        self.refresh()

    def refresh(self) -> None:
        selected_ids = set(self._selected_ids())
        current_id = None
        current_item = self._list.currentItem()
        if current_item is not None:
            current_id = current_item.data(Qt.ItemDataRole.UserRole)
        had_focus = self._list.hasFocus()

        params: list = []
        nsfw_clause = ""
        if _read_setting("show_nsfw", "0") != "1":
            visible_group_ids = self._visible_group_ids()
            if visible_group_ids:
                placeholders = ",".join("?" for _ in visible_group_ids)
                nsfw_clause = f"AND g.group_id IN ({placeholders})"
                params.extend(visible_group_ids)
            else:
                nsfw_clause = "AND 1=0"
        rows = _history_db.fetchall(
            f"""
            SELECT g.id, g.invoke_image_name, g.model_name, g.deleted_at,
                   r.title
            FROM generations g
            LEFT JOIN image_reviews r ON r.generation_id = g.id
            WHERE g.deleted_at IS NOT NULL
              {nsfw_clause}
            ORDER BY g.deleted_at DESC
            """,
            tuple(params),
        )
        if self._thumb_worker and self._thumb_worker.isRunning():
            self._thumb_worker.stop()
            self._thumb_worker.wait(200)
        self._id_to_item.clear()
        self._list.clear()
        tasks: list[tuple[int, str]] = []
        for row in rows:
            gen_id    = row["id"]
            image_name = row["invoke_image_name"] or ""
            title     = row["title"] or tr("side_panel.gen_default_title", id=gen_id)
            model     = row["model_name"] or "—"
            deleted   = str(row["deleted_at"] or "")[:16]
            item = QListWidgetItem(f"{title}\n{model}  {tr('side_panel.trash_deleted_label')} {deleted}")
            item.setData(Qt.ItemDataRole.UserRole, gen_id)
            item.setFont(ui_font(-1))
            item.setSizeHint(QSize(0, self._icon_size + 14))
            pix = self._load_pixmap_for_gen(int(gen_id))
            if pix:
                item.setIcon(QIcon(pix))
            elif image_name:
                tasks.append((int(gen_id), str(image_name)))
            self._list.addItem(item)
            self._id_to_item[int(gen_id)] = item
            if int(gen_id) in selected_ids:
                item.setSelected(True)
            if current_id is not None and int(gen_id) == int(current_id):
                self._list.setCurrentItem(item)
        if tasks:
            self._start_thumb_worker(tasks)
        if had_focus:
            self._list.setFocus()

    def _load_pixmap_for_gen(self, gen_id: int) -> QPixmap | None:
        row = _history_db.fetchone(
            "SELECT thumbnail_data FROM generations WHERE id=?", (gen_id,)
        )
        if row and row["thumbnail_data"]:
            pix = QPixmap()
            if pix.loadFromData(bytes(row["thumbnail_data"])) and not pix.isNull():
                return pix.scaled(
                    self._icon_size, self._icon_size,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
        return None

    def _start_thumb_worker(self, tasks: list[tuple[int, str]]) -> None:
        if self._client is None:
            return
        from db import connections
        if self._thumb_worker and self._thumb_worker.isRunning():
            self._thumb_worker.stop()
            self._thumb_worker.wait(200)
        self._thumb_worker = _ThumbWorker(
            tasks, self._client,
            connections.get_active_history_name(),
            parent=self,
        )
        self._thumb_worker.thumbnail_ready.connect(self._on_thumbnail_ready)
        self._thumb_worker.start()

    def _on_thumbnail_ready(self, gen_id: int, data: bytes) -> None:
        item = self._id_to_item.get(gen_id)
        if item is None:
            return
        pix = QPixmap()
        if pix.loadFromData(data) and not pix.isNull():
            item.setIcon(QIcon(pix.scaled(
                self._icon_size,
                self._icon_size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )))

    def _visible_group_ids(self) -> list[int]:
        rows = _history_db.fetchall(
            "SELECT id, parent_id, COALESCE(is_nsfw,0) AS is_nsfw "
            "FROM generation_groups ORDER BY sort_order, name"
        )
        by_parent: dict[int | None, list] = {}
        for row in rows:
            by_parent.setdefault(row["parent_id"], []).append(row)
        visible: list[int] = []

        def walk(parent_id: int | None, hidden_parent: bool) -> None:
            for row in by_parent.get(parent_id, []):
                hidden = hidden_parent or bool(row["is_nsfw"])
                if not hidden:
                    visible.append(int(row["id"]))
                walk(int(row["id"]), hidden)

        walk(None, False)
        return visible

    def _selected_ids(self) -> list[int]:
        return [
            it.data(Qt.ItemDataRole.UserRole)
            for it in self._list.selectedItems()
        ]

    def _restore_selected(self) -> None:
        ids = self._selected_ids()
        if not ids:
            QMessageBox.information(self, tr("side_panel.trash_restore_title"), tr("side_panel.trash_restore_no_sel"))
            return
        for gen_id in ids:
            _history_db.execute(
                "UPDATE generations SET deleted_at = NULL WHERE id = ?",
                (gen_id,),
            )
        self.refresh()

    def _perm_delete_selected(self) -> None:
        ids = self._selected_ids()
        if not ids:
            QMessageBox.information(self, tr("side_panel.trash_perm_del_title"), tr("side_panel.trash_perm_del_no_sel"))
            return
        reply = QMessageBox.question(
            self, tr("side_panel.trash_perm_del_confirm_title"),
            tr("side_panel.trash_perm_del_confirm_msg", n=len(ids)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        # 履歴から完全削除しても、元画像ファイルは絶対に削除しない。
        # ここで消すのは PromptMosaic のDB行だけ。
        for gen_id in ids:
            _history_db.execute("DELETE FROM generations WHERE id = ?", (gen_id,))
        self.refresh()

    def _empty_trash(self) -> None:
        row = _history_db.fetchone(
            "SELECT COUNT(*) AS cnt FROM generations WHERE deleted_at IS NOT NULL"
        )
        cnt = row["cnt"] if row else 0
        if cnt == 0:
            QMessageBox.information(self, tr("side_panel.trash_empty_title"), tr("side_panel.trash_empty_info"))
            return
        reply = QMessageBox.question(
            self, tr("side_panel.trash_empty_confirm_title"),
            tr("side_panel.trash_empty_confirm_msg", n=cnt),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        # ゴミ箱を空にしても、元画像ファイルは絶対に削除しない。
        # ここで消すのは PromptMosaic のDB行だけ。
        _history_db.execute(
            "DELETE FROM generations WHERE deleted_at IS NOT NULL"
        )
        self.refresh()


class _HistoryTileClone(QWidget):
    exit_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._generation_id: int | None = None
        self._draft_key: tuple[str, int] | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        header = QWidget()
        header_lay = QHBoxLayout(header)
        header_lay.setContentsMargins(6, 4, 6, 4)
        header_lay.setSpacing(6)

        self._title_lbl = QLabel("")
        self._title_lbl.setFont(ui_font(-1, bold=True))
        self._title_lbl.setStyleSheet(f"color: {TEXT};")
        header_lay.addWidget(self._title_lbl, stretch=1)

        self._new_badge = QLabel("")
        self._new_badge.setFont(ui_font(-1, bold=True))
        self._new_badge.setStyleSheet(f"color: {ACCENT};")
        self._new_badge.hide()
        header_lay.addWidget(self._new_badge)

        self._thumb_lbl = QLabel()
        self._thumb_lbl.setFixedSize(32, 32)
        self._thumb_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb_lbl.setStyleSheet(
            f"background: {SURFACE1}; border: 1px solid {SURFACE2}; border-radius: 3px;"
        )
        header_lay.addWidget(self._thumb_lbl)

        self._exit_btn = QPushButton(tr("side_panel.history_tile_exit"))
        self._exit_btn.setFixedHeight(24)
        self._exit_btn.setStyleSheet(themed_button_style("neutral"))
        self._exit_btn.clicked.connect(self.exit_requested.emit)
        header_lay.addWidget(self._exit_btn)
        root.addWidget(header)

        self._editor = PromptEditor(readonly=True)
        self._editor.setMinimumWidth(240)
        root.addWidget(self._editor, stretch=1)

    def generation_id(self) -> int | None:
        return self._generation_id

    def draft_key(self) -> tuple[str, int] | None:
        return self._draft_key

    def set_new_count(self, count: int) -> None:
        if count > 0:
            self._new_badge.setText(tr("side_panel.history_tile_new_badge", n=count))
            self._new_badge.show()
        else:
            self._new_badge.hide()

    def load_generation(self, generation_id: int) -> bool:
        doc = PromptDocument.load_from_db(generation_id)
        if doc is None:
            return False
        self._expand_open_group_descendants(doc)
        self._generation_id = int(generation_id)
        self._draft_key = None
        self._editor.set_document(doc)
        memo_row = _history_db.fetchone(
            "SELECT review_text FROM image_reviews WHERE generation_id=?",
            (generation_id,),
        )
        self._editor.set_memo((memo_row["review_text"] or "") if memo_row else "")
        self._load_thumbnail(generation_id)
        self._title_lbl.setText(tr("side_panel.history_tile_title", id=generation_id))
        return True

    def load_draft(self, owner_history_db: str, draft_id: int) -> bool:
        import db.hmap_db as _hmap_db
        row = _hmap_db.fetch_draft_node(str(owner_history_db), int(draft_id))
        if row is None:
            return False
        try:
            doc = PromptDocument.from_json(str(row["prompt_json"] or ""))
        except Exception:
            return False
        self._expand_open_group_descendants(doc)
        self._generation_id = None
        self._draft_key = (str(owner_history_db), int(draft_id))
        self._editor.set_document(doc)
        self._editor.set_memo(str(row["memo_text"] or ""))
        self._load_draft_thumbnail()
        self._title_lbl.setText(tr("side_panel.history_tile_draft_title", id=draft_id))
        return True

    @staticmethod
    def _expand_open_group_descendants(doc: PromptDocument) -> None:
        def expand_children(tile: GroupTile) -> None:
            for child in tile.tiles:
                if isinstance(child, GroupTile):
                    child.ui_expanded = True
                    expand_children(child)

        for side in (doc.positive, doc.negative):
            for block in (side.top, side.middle, side.bottom):
                for tile in block.tiles:
                    if isinstance(tile, GroupTile) and tile.ui_expanded:
                        expand_children(tile)

    def _load_thumbnail(self, generation_id: int) -> None:
        self._thumb_lbl.clear()
        self._thumb_lbl.setFont(ui_font())
        self._thumb_lbl.setStyleSheet(
            f"background: {SURFACE1}; border: 1px solid {SURFACE2}; border-radius: 3px;"
        )
        row = _history_db.fetchone(
            "SELECT invoke_image_name, local_path FROM generations WHERE id=?",
            (generation_id,),
        )
        image_name = str(row["invoke_image_name"] or "") if row else ""
        local_path = str(row["local_path"] or "") if row else ""
        pix = self._load_cached_thumbnail(generation_id, image_name)
        if pix is None:
            image_path = resolve_generation_image_path(local_path, image_name)
            if image_path:
                pix = QPixmap(str(image_path))
        if pix is not None and not pix.isNull():
            self._thumb_lbl.setPixmap(
                pix.scaled(
                    32,
                    32,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )

    def _load_draft_thumbnail(self) -> None:
        self._thumb_lbl.clear()
        self._thumb_lbl.setText(tr("history_map.draft_icon"))
        self._thumb_lbl.setFont(ui_font(3, bold=True))
        self._thumb_lbl.setStyleSheet(
            f"background: {SURFACE1}; color: {ACCENT}; "
            f"border: 1px solid {SURFACE2}; border-radius: 3px;"
        )

    @staticmethod
    def _load_cached_thumbnail(generation_id: int, image_name: str) -> QPixmap | None:
        row = _history_db.fetchone(
            "SELECT thumbnail_data FROM generations WHERE id=?", (generation_id,)
        )
        if row and row["thumbnail_data"]:
            pix = QPixmap()
            if pix.loadFromData(bytes(row["thumbnail_data"])) and not pix.isNull():
                return pix
        return None

    def retranslate_and_restyle(self) -> None:
        self._exit_btn.setText(tr("side_panel.history_tile_exit"))
        if self._generation_id is not None:
            self._title_lbl.setText(tr("side_panel.history_tile_title", id=self._generation_id))
        elif self._draft_key is not None:
            self._title_lbl.setText(tr("side_panel.history_tile_draft_title", id=self._draft_key[1]))



# ── SidePanel（コンテナ） ────────────────────────────────────────────────

class SidePanel(QWidget):
    """右ペイン: 履歴ツリー + ノートのタブコンテナ"""

    load_generation_requested      = Signal(int)
    load_draft_requested           = Signal(str, int)
    delete_draft_requested         = Signal(str, int)
    full_load_generation_requested = Signal(int)
    sync_history_requested         = Signal()
    group_focus_changed            = Signal(object)  # int | None
    history_tile_mode_changed      = Signal(bool)
    history_tile_generation_changed = Signal(object)  # int | ("draft", owner, id) | None
    history_map_requested          = Signal(int)
    draft_history_map_requested    = Signal(str, int)
    history_changed                = Signal()  # 履歴行の増減/ゴミ箱出入り（マップ等の追従用）

    def __init__(self, client: "InvokeClient | None" = None, parent=None):
        super().__init__(parent)
        self._client = client
        self._history_tile_mode = False
        self._history_tile_new_count = 0
        self._history_tile_exit_refresh_pending = False
        self._build_ui()
        QTimer.singleShot(650, self._restore_history_tile_mode)

    def _build_ui(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        self._stack = QStackedWidget()
        self._tabs = QTabWidget()
        self._history_tab = HistoryTab(client=self._client)
        self._notes_tab   = NotesTab()
        self._trash_tab   = TrashTab(client=self._client)
        self._history_tile_clone = _HistoryTileClone()

        self._history_tab.load_requested.connect(self.load_generation_requested.emit)
        self._history_tab.draft_load_requested.connect(self.load_draft_requested.emit)
        self._history_tab.draft_delete_requested.connect(self.delete_draft_requested.emit)
        self._history_tab.full_load_requested.connect(self.full_load_generation_requested.emit)
        self._history_tab.sync_requested.connect(self.sync_history_requested.emit)
        self._history_tab.group_focus_changed.connect(self.group_focus_changed.emit)
        self._history_tab.tile_mode_requested.connect(self.enter_history_tile_mode)
        self._history_tab.draft_tile_mode_requested.connect(self.enter_draft_history_tile_mode)
        self._history_tab.history_map_requested.connect(self.history_map_requested.emit)
        self._history_tab.draft_history_map_requested.connect(self.draft_history_map_requested.emit)
        self._history_tile_clone.exit_requested.connect(self.exit_history_tile_mode)
        # ゴミ箱タブと履歴タブを連動させる
        self._history_tab._tree.tree_changed.connect(self._trash_tab.refresh)
        self._trash_tab._restore_btn.clicked.connect(self._history_tab.refresh)
        # 履歴行の変化（ゴミ箱出入り等）を外部へ通知（開いている履歴マップの追従用）
        self._history_tab._tree.tree_changed.connect(self.history_changed.emit)
        self._trash_tab._restore_btn.clicked.connect(self.history_changed.emit)

        self._tabs.tabBar().setStyleSheet(
            "QTabBar::tab { min-width: 44px; max-width: 44px; padding: 4px 0; font-size: 14pt; }"
        )
        self._tabs.addTab(self._history_tab, "🕘")
        self._tabs.setTabToolTip(0, tr("side_panel.tab_history"))
        self._tabs.addTab(self._notes_tab, "📓")
        self._tabs.setTabToolTip(1, tr("side_panel.tab_notes"))
        self._tabs.addTab(self._trash_tab, "🗑️")
        self._tabs.setTabToolTip(2, tr("side_panel.tab_trash"))
        self._tabs.currentChanged.connect(self._on_tab_changed)
        self._stack.addWidget(self._tabs)
        self._stack.addWidget(self._history_tile_clone)
        lay.addWidget(self._stack)

    def retranslate_and_restyle(self) -> None:
        self._tabs.setTabToolTip(0, tr("side_panel.tab_history"))
        self._tabs.setTabToolTip(1, tr("side_panel.tab_notes"))
        self._tabs.setTabToolTip(2, tr("side_panel.tab_trash"))
        self._history_tab.retranslate_and_restyle()
        self._notes_tab.retranslate_and_restyle()
        self._trash_tab.retranslate_and_restyle()
        self._history_tile_clone.retranslate_and_restyle()

    def _on_tab_changed(self, _index: int) -> None:
        self._notes_tab.save_if_dirty()
        if self._tabs.currentWidget() is self._history_tab:
            QTimer.singleShot(0, self._history_tab._load_visible_thumbnails)
        elif self._tabs.currentWidget() is self._trash_tab:
            self._trash_tab.refresh()

    def save_notes_if_dirty(self) -> None:
        self._notes_tab.save_if_dirty()

    def save_history_tree_state(self) -> None:
        self._history_tab.save_tree_state()

    def restore_group_id(self, group_id: int) -> None:
        """起動時に保存されていたグループIDを復元する。"""
        self._history_tab.restore_group_id(group_id)

    def refresh_history(self) -> None:
        if self._history_tile_mode:
            self._history_tile_new_count += 1
            self._history_tile_clone.set_new_count(self._history_tile_new_count)
            return
        self._history_tab.refresh()

    def refresh_history_items(self, gen_ids: list[int]) -> None:
        self._history_tab.refresh_generation_items(gen_ids)

    def focus_history_generation(self, gen_id: int, *, animate: bool = True, flash: bool = True) -> bool:
        if self._history_tile_mode:
            self.exit_history_tile_mode()
            self._refresh_history_after_tile_exit()
        self._stack.setCurrentWidget(self._tabs)
        self._tabs.setCurrentWidget(self._history_tab)
        return self._history_tab.focus_generation(gen_id, animate=animate, flash=flash)

    def focus_history_draft(self, owner_history_db: str, draft_id: int, *, animate: bool = True, flash: bool = True) -> bool:
        if self._history_tile_mode:
            self.exit_history_tile_mode()
            self._refresh_history_after_tile_exit()
        self._stack.setCurrentWidget(self._tabs)
        self._tabs.setCurrentWidget(self._history_tab)
        return self._history_tab.focus_draft(owner_history_db, draft_id, animate=animate, flash=flash)

    def refresh_for_nsfw_setting(self) -> None:
        self._history_tab.refresh()
        self._trash_tab.refresh()

    def enter_history_tile_mode(self, generation_id: int) -> None:
        if not self._history_tile_clone.load_generation(int(generation_id)):
            QMessageBox.warning(
                self,
                tr("side_panel.history_tile_target_removed_title"),
                tr("side_panel.history_tile_target_removed"),
            )
            return
        self._history_tile_mode = True
        self._history_tile_new_count = 0
        self._history_tile_clone.set_new_count(0)
        self._stack.setCurrentWidget(self._history_tile_clone)
        _app_db.execute(
            "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
            ("history_tile_mode_on", "1"),
        )
        _app_db.execute(
            "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
            ("history_tile_kind", "generation"),
        )
        _app_db.execute(
            "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
            ("history_tile_gen_id", str(int(generation_id))),
        )
        self.history_tile_mode_changed.emit(True)
        self.history_tile_generation_changed.emit(int(generation_id))

    def enter_draft_history_tile_mode(self, owner_history_db: str, draft_id: int) -> None:
        if not self._history_tile_clone.load_draft(str(owner_history_db), int(draft_id)):
            QMessageBox.warning(
                self,
                tr("side_panel.history_tile_target_removed_title"),
                tr("side_panel.history_tile_target_removed"),
            )
            return
        self._history_tile_mode = True
        self._history_tile_new_count = 0
        self._history_tile_clone.set_new_count(0)
        self._stack.setCurrentWidget(self._history_tile_clone)
        for key, value in (
            ("history_tile_mode_on", "1"),
            ("history_tile_kind", "draft"),
            ("history_tile_draft_owner", str(owner_history_db)),
            ("history_tile_draft_id", str(int(draft_id))),
        ):
            _app_db.execute(
                "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
                (key, value),
            )
        self.history_tile_mode_changed.emit(True)
        self.history_tile_generation_changed.emit(("draft", str(owner_history_db), int(draft_id)))

    def exit_history_tile_mode(self) -> None:
        self._history_tile_mode = False
        self._history_tile_new_count = 0
        self._history_tile_clone.set_new_count(0)
        self._stack.setCurrentWidget(self._tabs)
        _app_db.execute(
            "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
            ("history_tile_mode_on", "0"),
        )
        _app_db.execute(
            "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
            ("history_tile_kind", ""),
        )
        self.history_tile_mode_changed.emit(False)
        self.history_tile_generation_changed.emit(None)
        self._schedule_history_refresh_after_tile_exit()

    def _schedule_history_refresh_after_tile_exit(self) -> None:
        if self._history_tile_exit_refresh_pending:
            return
        self._history_tile_exit_refresh_pending = True
        QTimer.singleShot(50, self._refresh_history_after_tile_exit)

    def _refresh_history_after_tile_exit(self) -> None:
        if not self._history_tile_exit_refresh_pending:
            return
        self._history_tile_exit_refresh_pending = False
        self._history_tab.refresh()

    def _restore_history_tile_mode(self) -> None:
        if _read_setting("history_tile_mode_on", "0") != "1":
            return
        kind = _read_setting("history_tile_kind", "generation")
        if kind == "draft":
            owner = _read_setting("history_tile_draft_owner", "")
            raw_draft = _read_setting("history_tile_draft_id", "0")
            if owner and raw_draft.isdigit() and int(raw_draft) > 0:
                self.enter_draft_history_tile_mode(owner, int(raw_draft))
            return
        raw = _read_setting("history_tile_gen_id", "0")
        if not raw.isdigit() or int(raw) <= 0:
            return
        self.enter_history_tile_mode(int(raw))

