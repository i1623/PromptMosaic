"""
タグブラウザ（左ペイン）— Phase 5: ジャンル分類対応

【表示モード】
  ブラウズモード: QTreeWidget（ジャンル別階層）
  検索モード   : QListWidget（フラット・チップ表示）

【ジャンル分類】
  LoRAブラウザと同じ15ジャンル + mixed_unsorted(99)
  Level 0: ジャンルヘッダー（着色・展開/折りたたみ）
  Level 1: 中分類ノード（is_nav_only=1）
  Level 2+: タグ

【操作】
  ダブルクリック（葉ノード）      → タグを1件追加
  ダブルクリック（グループノード） → 直接の子タグを全件追加
  右クリック                       → コンテキストメニュー

【D&D】
  タグ → ジャンルヘッダー : ジャンル変更・ルートへ移動
  タグ → 中分類ノード    : ジャンル変更・親変更
  タグ → ブロック        : タグ追加（既存の仕組み）
"""
from __future__ import annotations

import csv
import io
import json
import os
import uuid
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QTreeWidget, QTreeWidgetItem, QListWidget, QListWidgetItem,
    QToolButton, QStackedWidget, QSplitter,
    QFileDialog, QMessageBox, QMenu, QDialog, QDialogButtonBox, QRadioButton,
    QColorDialog, QPushButton, QFormLayout, QComboBox, QInputDialog,
    QStyledItemDelegate, QStyleOptionViewItem, QStyle,
    QSizePolicy, QAbstractItemView, QApplication, QTextEdit,
)
from PySide6.QtCore import Signal, Qt, QSize, QRectF, QPoint, QMimeData, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QBrush, QDrag, QPixmap, QFont, QFontMetrics, QTextCursor

from core.i18n import tr
from core.import_security import (
    MAX_IMPORT_RECORDS, MEMO_MAX_CHARS, ONE_LINE_MEMO_MAX_CHARS,
    sanitize_tag_name, sanitize_text, sanitize_text_json,
    load_json_import_file, read_text_import_file,
)
from core.text_sanitize import single_line_text
import ui.styles as _styles
from ui.styles import SURFACE0, SURFACE1, SURFACE2, TEXT, SUBTEXT, ACCENT, ui_font
from ui.styles import RED, EMOJI_ICON_SS
import db.app_db as _app_db
import db.library_db as _library_db
from db.connections import list_library_names, set_active_library, get_active_library_name


def _update_suggestions_for_library(lib_name: str) -> None:
    try:
        from db.discovery import update_library_suggestions
        update_library_suggestions(lib_name)
    except Exception:
        pass


def _rebuild_all_suggestions() -> None:
    try:
        from db.discovery import rebuild_suggestions
        rebuild_suggestions()
    except Exception:
        pass


# ── ジャンル定義（LoRAブラウザと同じ15ジャンル） ─────────────────────────────

_TAG_GENRES: list[tuple[str, str]] = [
    ("character_identity",           tr("lora_genre.character_identity")),
    ("human_expression",             tr("lora_genre.human_expression")),
    ("pose_action_interaction",      tr("lora_genre.pose_action_interaction")),
    ("clothing_accessory",           tr("lora_genre.clothing_accessory")),
    ("living_creature",              tr("lora_genre.living_creature")),
    ("object_artifact",              tr("lora_genre.object_artifact")),
    ("architecture_structure",       tr("lora_genre.architecture_structure")),
    ("location_background",          tr("lora_genre.location_background")),
    ("natural_feature",              tr("lora_genre.natural_feature")),
    ("phenomenon_event",             tr("lora_genre.phenomenon_event")),
    ("era_culture_worldview",        tr("lora_genre.era_culture_worldview")),
    ("art_style_medium",             tr("lora_genre.art_style_medium")),
    ("lighting_color_screen_effect", tr("lora_genre.lighting_color_screen_effect")),
    ("quality_correction",           tr("lora_genre.quality_correction")),
    ("mixed_unsorted",               tr("lora_genre.mixed_unsorted")),
]

_GENRE_LABEL: dict[str, str] = {k: v for k, v in _TAG_GENRES}

# 既定（ビルトイン）ジャンルのキー集合。i18n フォールバックや import 時の判定に使う。
_DEFAULT_GENRE_KEYS: set[str] = {k for k, _ in _TAG_GENRES}


def _fetch_tag_genres() -> list[str]:
    """
    タグブラウザのルートカテゴリ（ジャンル）キーを sort_order 順で返す。
    出所は**アクティブライブラリ**の tag_categories(is_tag_genre=1)。ユーザーが増減できる。
    DB が未初期化／空の場合はハードコードの既定15ジャンルにフォールバックする。
    """
    try:
        rows = _library_db.fetchall(
            "SELECT key FROM tag_categories WHERE COALESCE(is_tag_genre, 0) = 1 "
            "ORDER BY sort_order, key",
        )
        keys = [r["key"] for r in rows if r["key"]]
    except Exception:
        keys = []
    if not keys:
        keys = [k for k, _ in _TAG_GENRES]
    return keys


def _genre_label(genre_key: str) -> str:
    """ジャンルの表示名を返す（アクティブライブラリの tag_categories.label → i18n の順）。"""
    try:
        row = _library_db.fetchone(
            "SELECT label FROM tag_categories WHERE key=?",
            (genre_key,),
        )
        if row and row["label"]:
            return row["label"]
    except Exception:
        pass
    return _styles.CATEGORY_LABELS.get(genre_key) or _GENRE_LABEL.get(genre_key, genre_key)

# ジャンルカラー（ダークモード: Catppuccin Mocha ベース）
_GENRE_COLORS_DARK: dict[str, tuple[str, str]] = {
    "character_identity":           ("#2d1f4e", "#cba6f7"),
    "human_expression":             ("#3d1b2e", "#f38ba8"),
    "pose_action_interaction":      ("#1b2d4e", "#89b4fa"),
    "clothing_accessory":           ("#3d2a1b", "#fab387"),
    "living_creature":              ("#1b3d1b", "#a6e3a1"),
    "object_artifact":              ("#2a2a3a", "#a6adc8"),
    "architecture_structure":       ("#1b3333", "#89dceb"),
    "location_background":          ("#383818", "#f9e2af"),
    "natural_feature":              ("#1b3822", "#b6f27c"),
    "phenomenon_event":             ("#3d2810", "#fab387"),
    "era_culture_worldview":        ("#3d1b3d", "#f5c2e7"),
    "art_style_medium":             ("#1e1e3d", "#b4befe"),
    "lighting_color_screen_effect": ("#3d1010", "#f38ba8"),
    "quality_correction":           ("#0f3a1a", "#a6e3a1"),
    "mixed_unsorted":               ("#252535", "#6c7086"),
}

# ジャンルカラー（ライトモード: Catppuccin Latte ベース）
_GENRE_COLORS_LIGHT: dict[str, tuple[str, str]] = {
    "character_identity":           ("#e5d8f5", "#8839ef"),
    "human_expression":             ("#f5d8e0", "#d20f39"),
    "pose_action_interaction":      ("#d8e4f5", "#1e66f5"),
    "clothing_accessory":           ("#f5e8d8", "#fe640b"),
    "living_creature":              ("#d8f5d8", "#40a02b"),
    "object_artifact":              ("#e8e8f0", "#6c6f85"),
    "architecture_structure":       ("#d8f5f5", "#04a5e5"),
    "location_background":          ("#f5f0d8", "#df8e1d"),
    "natural_feature":              ("#d8f5e0", "#40a02b"),
    "phenomenon_event":             ("#f5ead8", "#fe640b"),
    "era_culture_worldview":        ("#f5d8f0", "#ea76cb"),
    "art_style_medium":             ("#e8d8f5", "#7287fd"),
    "lighting_color_screen_effect": ("#f5d8d8", "#d20f39"),
    "quality_correction":           ("#d8f5d8", "#40a02b"),
    "mixed_unsorted":               ("#e8e8e8", "#9ca0b0"),
}


def _genre_color(genre_key: str) -> tuple[str, str]:
    """現在のテーマのジャンルカラー (bg, fg) を返す"""
    return _styles.tag_browser_base_colors(genre_key)


# ジャンル → タグタイル色キーのマッピング
_GENRE_TO_CATEGORY: dict[str, str] = {
    "character_identity":           "character_identity",
    "human_expression":             "human_expression",
    "pose_action_interaction":      "pose_action_interaction",
    "clothing_accessory":           "clothing_accessory",
    "living_creature":              "living_creature",
    "object_artifact":              "object_artifact",
    "architecture_structure":       "architecture_structure",
    "location_background":          "location_background",
    "natural_feature":              "natural_feature",
    "phenomenon_event":             "phenomenon_event",
    "era_culture_worldview":        "era_culture_worldview",
    "art_style_medium":             "art_style_medium",
    "lighting_color_screen_effect": "lighting_color_screen_effect",
    "quality_correction":           "quality_correction",
    "mixed_unsorted":               "mixed_unsorted",
}


# ── カスタムロール ─────────────────────────────────────────────────────────────
_ROLE_NAME_EN   = Qt.ItemDataRole.UserRole
_ROLE_NAME_JA   = Qt.ItemDataRole.UserRole + 1
_ROLE_CATEGORY  = Qt.ItemDataRole.UserRole + 2
_ROLE_IS_NAV    = Qt.ItemDataRole.UserRole + 3
_ROLE_TAG_ID    = Qt.ItemDataRole.UserRole + 4
_ROLE_GENRE     = Qt.ItemDataRole.UserRole + 5
_ROLE_GENRE_HDR = Qt.ItemDataRole.UserRole + 6  # True = ジャンルヘッダー行
_ROLE_DICTIONARY_KEY = Qt.ItemDataRole.UserRole + 7
_ROLE_GENRE_KEY = Qt.ItemDataRole.UserRole + 8  # bool混入を避けるためのジャンルキー専用ロール

_ROLE_PRESET_ID = Qt.ItemDataRole.UserRole  # group_presets.id

_DEFAULT_DICTIONARY_KEY = "default"
_DICTIONARY_EXPORT_FORMAT = "promptmosaic_tag_dictionary"
_DICTIONARY_EXPORT_VERSION = 1
_DICTIONARY_MAX_BYTES = 8 * 1024 * 1024
_DICTIONARY_MAX_ITEMS = 50_000
_DICTIONARY_MAX_DEPTH = 24


def _normalize_genre_key(value) -> str:
    if value is None or isinstance(value, bool):
        return ""
    return str(value).strip()


def _tree_item_genre(item: QTreeWidgetItem | None) -> str:
    if item is None:
        return "mixed_unsorted"
    genre = _normalize_genre_key(item.data(0, _ROLE_GENRE_KEY))
    if not genre:
        genre = _normalize_genre_key(item.data(0, _ROLE_GENRE))
    return genre or "mixed_unsorted"


def _list_item_genre(item) -> str:
    if item is None:
        return "mixed_unsorted"
    genre = _normalize_genre_key(item.data(_ROLE_GENRE_KEY))
    if not genre:
        genre = _normalize_genre_key(item.data(_ROLE_GENRE))
    return genre or "mixed_unsorted"


def _current_dictionary_key() -> str:
    row = _app_db.fetchone("SELECT value FROM app_settings WHERE key='current_library_db'")
    return (row["value"] if row else "") or _DEFAULT_DICTIONARY_KEY


# ── チップスタイルデリゲート ────────────────────────────────────────────────────

class _ChipDelegate(QStyledItemDelegate):
    """
    QTreeWidget / QListWidget のアイテムをジャンル色チップとして描画する。

    is_genre_hdr=True → ジャンルヘッダー（太字・ジャンル背景）
    is_nav=True        → 中分類見出し（太字・ジャンル前景色）
    is_nav=False       → ジャンル色の丸角チップ（タグタイル）
    """

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        is_genre_hdr = bool(index.data(_ROLE_GENRE_HDR))
        is_nav       = bool(index.data(_ROLE_IS_NAV))
        genre        = _normalize_genre_key(index.data(_ROLE_GENRE_KEY)) or _normalize_genre_key(index.data(_ROLE_GENRE)) or "mixed_unsorted"
        bg_hex, fg_hex = _genre_color(genre)

        if is_genre_hdr:
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            hdr_rect = QRectF(option.rect.adjusted(2, 2, -4, -2))
            border_color = QColor(fg_hex).lighter(115)
            if QColor(_styles.BASE).lightness() > 128:
                border_color = QColor(fg_hex).darker(115)
            if option.state & QStyle.StateFlag.State_Selected:
                border_color = QColor("#ffffff")
            painter.setBrush(QBrush(QColor(bg_hex)))
            painter.setPen(QPen(border_color, 2))
            painter.drawRoundedRect(hdr_rect, 4, 4)
            f = option.font
            f.setBold(True)
            painter.setFont(f)
            painter.setPen(QColor(fg_hex))
            painter.drawText(
                option.rect.adjusted(10, 0, -8, 0),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                index.data(Qt.ItemDataRole.DisplayRole) or "",
            )
            painter.restore()
            return

        if is_nav:
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            nav_rect = QRectF(option.rect.adjusted(2, 2, -4, -2))
            nav_bg = QColor(bg_hex).lighter(112)
            if option.state & QStyle.StateFlag.State_Selected:
                nav_bg = nav_bg.lighter(120)
            painter.setBrush(QBrush(nav_bg))
            painter.setPen(QPen(QColor(fg_hex).darker(130), 1))
            painter.drawRoundedRect(nav_rect, 4, 4)
            f = option.font
            f.setBold(True)
            painter.setFont(f)
            # ライトモードでは少し暗く、ダークモードでは前景色のまま
            nav_color = QColor(fg_hex).darker(130) if QColor(_styles.BASE).lightness() > 128 else QColor(fg_hex)
            painter.setPen(nav_color)
            painter.drawText(
                option.rect.adjusted(8, 0, -8, 0),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                index.data(Qt.ItemDataRole.DisplayRole) or "",
            )
            painter.restore()
            return

        # タグチップ
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        chip_rect = QRectF(option.rect.adjusted(2, 3, -4, -3))
        bg_draw = QColor(bg_hex).lighter(130)
        border = QPen(QColor("#ffffff"), 2) if selected else QPen(QColor(fg_hex).darker(120), 1)
        painter.setBrush(QBrush(bg_draw))
        painter.setPen(border)
        painter.drawRoundedRect(chip_rect, 5, 5)

        main_text  = index.data(Qt.ItemDataRole.DisplayRole) or ""
        name_local = index.data(_ROLE_NAME_JA) or ""
        name_en    = index.data(_ROLE_NAME_EN) or ""
        show_sub   = bool(name_local and name_en and name_local != name_en)

        painter.setPen(QColor(fg_hex))
        r = option.rect
        if show_sub:
            # option.fontMetrics はリストの基底フォント由来でずれることがあるため
            # 実際に使うフォントから QFontMetrics を作り直す
            fm       = QFontMetrics(option.font)
            sub_font = QFont(option.font)
            sub_font.setPointSize(max(6, option.font.pointSize() - 1))
            sub_fm   = QFontMetrics(sub_font)
            top_h    = fm.ascent() + fm.descent()
            sub_h    = sub_fm.ascent() + sub_fm.descent()
            block_y  = r.y() + max(2, (r.height() - top_h - sub_h) // 2)
            painter.setFont(option.font)
            painter.drawText(
                r.x() + 8, block_y, r.width() - 16, top_h,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                main_text,
            )
            painter.setFont(sub_font)
            sub_fg = QColor(fg_hex)
            sub_fg.setAlphaF(0.60)
            painter.setPen(sub_fg)
            elided = sub_fm.elidedText(name_en, Qt.TextElideMode.ElideRight, r.width() - 16)
            painter.drawText(
                r.x() + 8, block_y + top_h, r.width() - 16, sub_h,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                elided,
            )
        else:
            painter.setFont(option.font)
            painter.drawText(
                r.adjusted(8, 0, -8, 0),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                main_text,
            )
        painter.restore()

    def sizeHint(self, option, index) -> QSize:
        sh = super().sizeHint(option, index)
        fm = QFontMetrics(option.font)
        line_h = fm.ascent() + fm.descent()
        if bool(index.data(_ROLE_GENRE_HDR)):
            return QSize(sh.width(), line_h + 10)
        if bool(index.data(_ROLE_IS_NAV)):
            return QSize(sh.width(), line_h + 8)
        name_local = index.data(_ROLE_NAME_JA) or ""
        name_en    = index.data(_ROLE_NAME_EN) or ""
        if name_local and name_en and name_local != name_en:
            sub_font = QFont(option.font)
            sub_font.setPointSize(max(6, option.font.pointSize() - 1))
            sub_fm   = QFontMetrics(sub_font)
            sub_h    = sub_fm.ascent() + sub_fm.descent()
            return QSize(sh.width(), line_h + sub_h + 10)
        return QSize(sh.width(), line_h + 10)


# ── _TagNodeDialog ────────────────────────────────────────────────────────────

class _TagNodeDialog(QDialog):
    """タグ/中分類ノードの追加・編集ダイアログ"""

    def __init__(self, parent_widget=None, *,
                 name_en: str = "", name_local: str = "",
                 genre: str = "mixed_unsorted", is_nav: bool = False,
                 parent_id: int | None = None,
                 name_en_readonly: bool = False,
                 is_nsfw: bool = False,
                 title: str | None = None):
        super().__init__(parent_widget)
        self.setWindowTitle(title or tr("tag_browser.node_edit_generic_title"))
        self.setMinimumWidth(420)
        self._translate_worker = None
        self._build(name_en, name_local, genre, is_nav, parent_id, name_en_readonly, is_nsfw)

    def _build(self, name_en, name_local, genre, is_nav, parent_id, name_en_readonly, is_nsfw):
        from PySide6.QtWidgets import (
            QVBoxLayout, QLabel, QLineEdit, QComboBox, QCheckBox, QDialogButtonBox
        )
        lay = QVBoxLayout(self)
        lay.setSpacing(6)
        self._is_nav = bool(is_nav)

        self._name_en = QLineEdit(name_en)
        self._name_en.setReadOnly(name_en_readonly)
        if not self._is_nav:
            lay.addWidget(QLabel(tr("tag_browser.tag_name_en_label")))
            lay.addWidget(self._name_en)

        lay.addWidget(QLabel(tr("tag_browser.tag_name_ja_label")))
        self._name_local = QLineEdit((name_local or name_en) if self._is_nav else (name_local or ""))
        if not self._is_nav:
            local_row = QHBoxLayout()
            local_row.setSpacing(4)
            local_row.addWidget(self._name_local, 1)
            self._translate_tag_btn = QPushButton(tr("tag_browser.tag_translate_btn"))
            self._translate_tag_btn.setToolTip(tr("tag_browser.tag_translate_tooltip"))
            self._translate_tag_btn.setEnabled(not name_en_readonly)
            self._translate_tag_btn.clicked.connect(self._start_tag_translate)
            local_row.addWidget(self._translate_tag_btn)
            lay.addLayout(local_row)
        else:
            lay.addWidget(self._name_local)

        if not self._is_nav:
            self._translate_cancel_btn = QPushButton(tr("translate_panel.cancel_btn"))
            self._translate_cancel_btn.setEnabled(False)
            self._translate_cancel_btn.clicked.connect(self._cancel_tag_translate)
            self._translate_status_lbl = QLabel("")
            self._translate_status_lbl.setStyleSheet(f"color: {SUBTEXT};")
            status_row = QHBoxLayout()
            status_row.setSpacing(4)
            status_row.addWidget(self._translate_status_lbl, 1)
            status_row.addWidget(self._translate_cancel_btn)

            self._translate_thinking_edit = QTextEdit()
            self._translate_thinking_edit.setReadOnly(True)
            self._translate_thinking_edit.setFixedHeight(72)
            self._translate_thinking_edit.setPlaceholderText(tr("translate_panel.thinking_label"))
            self._translate_thinking_edit.setStyleSheet(
                f"QTextEdit {{ background: {SURFACE0}; color: {SUBTEXT}; "
                f"border: 1px solid {SURFACE2}; border-radius: 4px; padding: 4px; }}"
            )
            self._translate_thinking_edit.hide()
            lay.addLayout(status_row)
            lay.addWidget(self._translate_thinking_edit)

        lay.addWidget(QLabel(tr("tag_browser.tag_genre_label")))
        self._genre_combo = QComboBox()
        for gkey in _fetch_tag_genres():
            self._genre_combo.addItem(_genre_label(gkey), gkey)
        idx = self._genre_combo.findData(genre)
        if idx >= 0:
            self._genre_combo.setCurrentIndex(idx)
        lay.addWidget(self._genre_combo)

        self._nsfw_cb = QCheckBox(tr("tag_browser.tag_nsfw_cb"))
        self._nsfw_cb.setChecked(is_nsfw)
        lay.addWidget(self._nsfw_cb)

        lay.addWidget(QLabel(tr("tag_browser.tag_parent_label")))
        self._parent_combo = QComboBox()
        lay.addWidget(self._parent_combo)

        self._initial_parent_id = parent_id
        self._refresh_parent_combo()
        self._genre_combo.currentIndexChanged.connect(self._refresh_parent_combo)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)
        self._dialog_buttons = btns
        self.finished.connect(self._cancel_tag_translate)

    def _set_tag_translating(self, translating: bool) -> None:
        if not hasattr(self, "_translate_tag_btn"):
            return
        self._translate_tag_btn.setEnabled(not translating and not self._name_en.isReadOnly())
        self._translate_cancel_btn.setEnabled(translating)
        self._dialog_buttons.setEnabled(not translating)

    def _append_translate_thinking(self, text: str) -> None:
        if not hasattr(self, "_translate_thinking_edit"):
            return
        if not self._translate_thinking_edit.isVisible():
            self._translate_thinking_edit.show()
        cursor = self._translate_thinking_edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        self._translate_thinking_edit.setTextCursor(cursor)

    def _start_tag_translate(self) -> None:
        src = single_line_text(self._name_local.text()) or single_line_text(self._name_en.text())
        if not src:
            self._translate_status_lbl.setStyleSheet(f"color: {RED};")
            self._translate_status_lbl.setText(tr("tile.translate_empty_source"))
            return
        from ui.tile_widget import _TileTranslateWorker

        self._translate_status_lbl.setText(tr("translate_panel.status_translating"))
        self._translate_status_lbl.setStyleSheet(f"color: {SUBTEXT};")
        self._translate_thinking_edit.clear()
        self._translate_thinking_edit.hide()
        self._set_tag_translating(True)
        self._translate_worker = _TileTranslateWorker(src, "danboard", False, self)
        self._translate_worker.status_update.connect(self._translate_status_lbl.setText)
        self._translate_worker.thinking_chunk.connect(self._append_translate_thinking)
        self._translate_worker.translation_done.connect(self._finish_tag_translate)
        self._translate_worker.failed.connect(self._fail_tag_translate)
        self._translate_worker.start()

    def _finish_tag_translate(self, text: str) -> None:
        self._translate_worker = None
        text = single_line_text(text)
        if text:
            self._name_en.setText(text)
            self._translate_status_lbl.setText(tr("tile.translate_done"))
            self._translate_status_lbl.setStyleSheet(f"color: {SUBTEXT};")
        else:
            self._translate_status_lbl.setStyleSheet(f"color: {RED};")
            self._translate_status_lbl.setText(tr("main.translate_failed", error=tr("main.translate_empty_result")))
        self._translate_thinking_edit.hide()
        self._set_tag_translating(False)

    def _fail_tag_translate(self, msg: str) -> None:
        self._translate_worker = None
        self._translate_status_lbl.setStyleSheet(f"color: {RED};")
        self._translate_status_lbl.setText(tr("main.translate_failed", error=msg))
        self._translate_thinking_edit.hide()
        self._set_tag_translating(False)

    def _cancel_tag_translate(self, *_args) -> None:
        worker = getattr(self, "_translate_worker", None)
        if worker is not None and worker.isRunning():
            if hasattr(worker, "cancel_and_wait"):
                worker.cancel_and_wait()
            else:
                worker.cancel()
                worker.wait(2000)
        self._translate_worker = None
        if hasattr(self, "_translate_cancel_btn"):
            self._translate_thinking_edit.hide()
            self._set_tag_translating(False)

    def _refresh_parent_combo(self) -> None:
        current_genre = self._genre_combo.currentData() or "mixed_unsorted"
        self._parent_combo.clear()
        self._parent_combo.addItem(tr("tag_browser.tag_parent_default"), None)
        rows = _library_db.fetchall(
            "SELECT id, name_en, name_local FROM tags "
            "WHERE COALESCE(genre,'mixed_unsorted')=? AND COALESCE(is_nav_only,0)=1 "
            "ORDER BY name_en",
            (current_genre,),
        )
        for r in rows:
            display = r["name_local"] or r["name_en"] or ""
            self._parent_combo.addItem(f"📁 {display}", r["id"])

        if self._initial_parent_id is not None:
            idx = self._parent_combo.findData(self._initial_parent_id)
            if idx >= 0:
                self._parent_combo.setCurrentIndex(idx)
            self._initial_parent_id = None

    @property
    def result_values(self) -> dict:
        genre = self._genre_combo.currentData() or "mixed_unsorted"
        name_en = single_line_text(self._name_en.text())
        name_local = single_line_text(self._name_local.text())
        if self._is_nav and not name_en:
            name_en = f"nav_{genre}_{uuid.uuid4().hex[:12]}"
        return {
            "name_en":    name_en,
            "name_local": name_local or None,
            "genre":     genre,
            # 既定ジャンルは従来の色キーへ、ユーザー追加ジャンルは genre キー自身を
            # 色カテゴリとして使う（tag_categories に色行があるのでタイルも正しく着色される）。
            "category":  _GENRE_TO_CATEGORY.get(genre, genre),
            "is_nav":    int(self._is_nav),
            "is_nsfw":   int(self._nsfw_cb.isChecked()),
            "parent_id": self._parent_combo.currentData(),
        }


class _GenreSettingsDialog(QDialog):
    """トップカテゴリーの表示名・色をまとめて編集するダイアログ。"""

    def __init__(self, genre: str, parent=None):
        super().__init__(parent)
        self._genre = genre
        self._reset_requested = False
        self._light_bg, self._light_fg, self._dark_bg, self._dark_fg = self._load_theme_colors(genre)
        self.setWindowTitle(tr("tag_browser.genre_settings_title", label=_genre_label(genre)))
        self.setModal(True)
        self.resize(460, 260)
        self.setStyleSheet(f"QDialog {{ background: {SURFACE0}; color: {TEXT}; }}")

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._label_edit = QLineEdit(_genre_label(genre))
        self._label_edit.setStyleSheet(f"color: {TEXT}; background: {SURFACE1}; border: 1px solid {SURFACE2};")
        form.addRow(tr("tag_browser.genre_settings_label"), self._label_edit)

        self._light_bg_btn = QPushButton()
        self._light_bg_btn.clicked.connect(lambda: self._choose_color("light_bg"))
        form.addRow(tr("tag_browser.genre_settings_light_bg"), self._light_bg_btn)

        self._light_fg_btn = QPushButton()
        self._light_fg_btn.clicked.connect(lambda: self._choose_color("light_fg"))
        form.addRow(tr("tag_browser.genre_settings_light_fg"), self._light_fg_btn)

        self._dark_bg_btn = QPushButton()
        self._dark_bg_btn.clicked.connect(lambda: self._choose_color("dark_bg"))
        form.addRow(tr("tag_browser.genre_settings_dark_bg"), self._dark_bg_btn)

        self._dark_fg_btn = QPushButton()
        self._dark_fg_btn.clicked.connect(lambda: self._choose_color("dark_fg"))
        form.addRow(tr("tag_browser.genre_settings_dark_fg"), self._dark_fg_btn)
        root.addLayout(form)

        row = QHBoxLayout()
        reset_btn = QPushButton(tr("tag_browser.genre_settings_reset"))
        reset_btn.clicked.connect(self._reset_and_accept)
        row.addWidget(reset_btn)
        row.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        row.addWidget(buttons)
        root.addLayout(row)

        self._refresh_color_buttons()

    @property
    def reset_requested(self) -> bool:
        return self._reset_requested

    def values(self) -> tuple[str, str, str, str, str]:
        label = single_line_text(self._label_edit.text()) or _GENRE_LABEL.get(self._genre, self._genre)
        return label, self._light_bg, self._light_fg, self._dark_bg, self._dark_fg

    @staticmethod
    def _load_theme_colors(genre: str) -> tuple[str, str, str, str]:
        default_light = _styles.tag_browser_default_base_colors_for_theme(genre, "light")
        default_dark = _styles.tag_browser_default_base_colors_for_theme(genre, "dark")
        row = _library_db.fetchone(
            "SELECT bg_color, fg_color, bg_color_light, fg_color_light, bg_color_dark, fg_color_dark "
            "FROM tag_categories WHERE key=?",
            (genre,),
        )
        if not row:
            return default_light[0], default_light[1], default_dark[0], default_dark[1]
        legacy = (row["bg_color"], row["fg_color"])
        light_fallback = default_light if legacy == default_dark else legacy
        light_bg = row["bg_color_light"] or light_fallback[0]
        light_fg = row["fg_color_light"] or light_fallback[1]
        dark_bg = row["bg_color_dark"] or legacy[0] or default_dark[0]
        dark_fg = row["fg_color_dark"] or legacy[1] or default_dark[1]
        return light_bg, light_fg, dark_bg, dark_fg

    def _choose_color(self, target: str) -> None:
        current_hex = {
            "light_bg": self._light_bg,
            "light_fg": self._light_fg,
            "dark_bg": self._dark_bg,
            "dark_fg": self._dark_fg,
        }[target]
        current = QColor(current_hex)
        chosen = QColorDialog.getColor(current, self, tr(
            "tag_browser.color_bg_title" if target.endswith("_bg") else "tag_browser.color_fg_title",
            label=single_line_text(self._label_edit.text()) or _genre_label(self._genre),
        ))
        if not chosen.isValid():
            return
        if target == "light_bg":
            self._light_bg = chosen.name()
        elif target == "light_fg":
            self._light_fg = chosen.name()
        elif target == "dark_bg":
            self._dark_bg = chosen.name()
        else:
            self._dark_fg = chosen.name()
        self._refresh_color_buttons()

    def _refresh_color_buttons(self) -> None:
        self._light_bg_btn.setText(self._light_bg)
        self._light_fg_btn.setText(self._light_fg)
        self._dark_bg_btn.setText(self._dark_bg)
        self._dark_fg_btn.setText(self._dark_fg)
        self._light_bg_btn.setStyleSheet(f"background: {self._light_bg}; color: {self._light_fg}; border: 1px solid {SURFACE2};")
        self._light_fg_btn.setStyleSheet(f"background: {self._light_bg}; color: {self._light_fg}; border: 1px solid {SURFACE2};")
        self._dark_bg_btn.setStyleSheet(f"background: {self._dark_bg}; color: {self._dark_fg}; border: 1px solid {SURFACE2};")
        self._dark_fg_btn.setStyleSheet(f"background: {self._dark_bg}; color: {self._dark_fg}; border: 1px solid {SURFACE2};")

    def _reset_and_accept(self) -> None:
        self._reset_requested = True
        self.accept()


# ── TagBrowser ────────────────────────────────────────────────────────────────

class TagBrowser(QWidget):
    """
    タグブラウザ（左ペイン）。

    Signals:
        tag_selected(tag_name, tag_local, category, dictionary_key):
            タグが選択（ダブルクリック or グループ展開時の子タグ1件ずつ）。
        tag_updated(old_name_en, new_name_en, new_name_ja, new_category):
            タグが編集された（ブロック内タイルの更新用）。
        tag_categories_changed():
            D&D 等でタグのカテゴリ色が変更された。
    """

    tag_selected = Signal(str, str, str, str)   # (tag_name, tag_local, category, dictionary_key)
    tag_updated  = Signal(str, str, str, str)   # (old_en, new_en, new_ja, new_cat)
    tag_categories_changed = Signal()

    _BROWSER_MIME       = "application/x-invoke-browser-tag"
    _GROUP_BROWSER_MIME = "application/x-invoke-browser-group"

    _instance: "TagBrowser | None" = None

    def __init__(self, parent=None, *, show_presets: bool = True, show_header_icon: bool = True):
        super().__init__(parent)
        TagBrowser._instance = self
        self._show_presets_panel = show_presets
        self._show_header_icon = show_header_icon

        # 展開状態の保持（ジャンルヘッダー: genre_key → bool、中分類ノード: tag_id → bool）
        self._genre_expanded: dict[str, bool] = {"mixed_unsorted": False}
        self._node_expanded:  dict[int, bool]  = {}

        # D&D 状態
        self._drag_start:       QPoint | None = None
        self._drag_item_en:     str = ""
        self._drag_item_ja:     str = ""
        self._drag_item_cat:    str = ""
        self._drag_item_id:     int | None = None
        self._drag_item_genre:  str = "mixed_unsorted"
        self._drag_item_is_nav: bool = False  # True = 中分類ノードのドラッグ
        self._drag_item_child_count: int = 0
        self._drag_source_view = None
        self._drag_source_pos: QPoint | None = None
        self._skip_next_click_toggle: bool = False
        self._drop_hint_text: str = ""
        self._drop_hint_widget: QLabel | None = None

        # 保存グループ D&D 状態
        self._preset_drag_start: QPoint | None = None
        self._preset_drag_item  = None
        self._presets_expanded  = True
        self._saved_presets_height = 0

        # NSFW 設定を DB から読む
        _nsfw_row = _app_db.fetchone("SELECT value FROM app_settings WHERE key='show_nsfw'")
        self._show_nsfw: bool = bool(int(_nsfw_row["value"])) if _nsfw_row else False
        self._dictionary_key: str = _current_dictionary_key()

        self._build_ui()
        self._load_dictionaries()
        self._load_tags()
        if self._show_presets_panel:
            self._load_presets()

    # ── UI 構築 ───────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self.setMinimumWidth(160)
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        # ヘッダー
        self._hdr_label = QLabel(self._tag_header_text())
        self._hdr_label.setFont(ui_font(bold=True))
        self._hdr_label.setStyleSheet(f"color: {ACCENT}; padding: 2px 4px;")
        root.addWidget(self._hdr_label)

        # ライブラリ切替
        dict_row = QHBoxLayout()
        dict_row.setSpacing(3)
        self._dict_combo = QComboBox()
        self._dict_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._dict_combo.currentIndexChanged.connect(self._on_dictionary_combo_changed)
        self._dict_combo.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._dict_combo.customContextMenuRequested.connect(self._on_dictionary_context_menu)
        dict_row.addWidget(self._dict_combo, 1)

        self._dict_prev_btn = QToolButton()
        self._dict_prev_btn.setText("◀")
        self._dict_prev_btn.setStyleSheet("QToolButton { " + EMOJI_ICON_SS + " }")
        self._dict_prev_btn.setToolTip(tr("tag_browser.dictionary_prev_tooltip"))
        self._dict_prev_btn.clicked.connect(lambda: self._step_dictionary(-1))
        dict_row.addWidget(self._dict_prev_btn)

        self._dict_next_btn = QToolButton()
        self._dict_next_btn.setText("▶")
        self._dict_next_btn.setStyleSheet("QToolButton { " + EMOJI_ICON_SS + " }")
        self._dict_next_btn.setToolTip(tr("tag_browser.dictionary_next_tooltip"))
        self._dict_next_btn.clicked.connect(lambda: self._step_dictionary(1))
        dict_row.addWidget(self._dict_next_btn)

        root.addLayout(dict_row)

        # 検索バー
        self._search = QLineEdit()
        self._search.setPlaceholderText(tr("tag_browser.search_placeholder"))
        self._search.setClearButtonEnabled(True)
        self._search.setFixedHeight(24)
        self._search.setFont(ui_font(-1))
        self._search.setStyleSheet(
            f"QLineEdit {{ background: {SURFACE0}; color: {TEXT}; "
            f"border: 1px solid {SURFACE2}; border-radius: 3px; padding: 0 4px; }}"
        )
        self._search.textChanged.connect(self._apply_filter)
        root.addWidget(self._search)
        self._search.hide()

        # 件数ラベル
        self._count_label = QLabel(tr("tag_browser.count", count=0))
        self._count_label.setFont(ui_font(-2))
        self._count_label.setStyleSheet(f"color: {SUBTEXT}; padding: 0 2px;")
        root.addWidget(self._count_label)

        # ツリー / リスト（QStackedWidget で切替）
        self._stack = QStackedWidget()

        # ブラウズモード: QTreeWidget
        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setColumnCount(1)
        self._tree.setItemDelegate(_ChipDelegate())
        self._tree.setExpandsOnDoubleClick(False)
        self._tree.setRootIsDecorated(True)
        self._tree.setIndentation(16)
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._tree.setStyleSheet(
            f"QTreeWidget {{ background: {SURFACE0}; border: 1px solid {SURFACE2}; "
            f"border-radius: 4px; }}"
            f"QTreeWidget::item {{ padding: 2px 2px; }}"
            f"QTreeWidget::item:selected {{ background: transparent; }}"
            f"QTreeWidget::branch {{ background: {SURFACE0}; }}"
        )
        self._tree.itemClicked.connect(self._on_tree_single_clicked)
        self._tree.itemDoubleClicked.connect(self._on_tree_double_clicked)
        self._tree.itemExpanded.connect(lambda item: self._remember_tree_item_state(item, True))
        self._tree.itemCollapsed.connect(self._on_tree_item_collapsed)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        # Qt 標準のアイテムドラッグを使うと、微小な自己ドロップ後に
        # カスタム MIME ではない「行そのもの」のドラッグが混ざることがある。
        # タグブラウザは eventFilter で専用 QDrag を作るため標準ドラッグは止める。
        self._tree.setDragEnabled(False)
        self._tree.viewport().installEventFilter(self)
        self._tree.viewport().setAcceptDrops(True)
        self._stack.addWidget(self._tree)   # index 0

        # 検索モード: QListWidget
        self._list = QListWidget()
        self._list.setItemDelegate(_ChipDelegate())
        self._list.setStyleSheet(
            f"QListWidget {{ background: {SURFACE0}; border: 1px solid {SURFACE2}; "
            f"border-radius: 4px; }}"
            f"QListWidget::item {{ padding: 1px 2px; }}"
        )
        self._list.itemDoubleClicked.connect(self._on_list_double_clicked)
        self._list.setDragEnabled(False)
        self._list.viewport().installEventFilter(self)
        self._list.viewport().setAcceptDrops(True)
        self._stack.addWidget(self._list)   # index 1

        # ── タグツリー + 保存グループ を QSplitter で上下分割 ──
        self._v_splitter = QSplitter(Qt.Orientation.Vertical)
        self._v_splitter.addWidget(self._stack)

        if self._show_presets_panel:
            # 保存グループセクション
            self._presets_panel = QWidget()
            self._presets_panel.setStyleSheet("QWidget { background: transparent; }")
            presets_vlay = QVBoxLayout(self._presets_panel)
            presets_vlay.setContentsMargins(0, 2, 0, 0)
            presets_vlay.setSpacing(0)

            self._presets_header = QWidget()
            self._presets_header.setStyleSheet(
                f"QWidget {{ background: {SURFACE1}; border-radius: 3px 3px 0 0; }}"
            )
            phdr_lay = QHBoxLayout(self._presets_header)
            phdr_lay.setContentsMargins(4, 2, 4, 2)
            phdr_lay.setSpacing(4)

            self._presets_toggle_btn = QToolButton()
            self._presets_toggle_btn.setText("▼")
            self._presets_toggle_btn.setFixedSize(16, 16)
            self._presets_toggle_btn.setStyleSheet(
                f"QToolButton {{ background: transparent; color: {ACCENT}; border: none; }}"
                f"QToolButton:hover {{ color: #cdd6f4; }}"
            )
            self._presets_toggle_btn.clicked.connect(self._toggle_presets)
            phdr_lay.addWidget(self._presets_toggle_btn)

            self._presets_title_lbl = QLabel(tr("tag_browser.saved_groups_title"))
            self._presets_title_lbl.setFont(ui_font(-1, bold=True))
            self._presets_title_lbl.setStyleSheet(f"color: {ACCENT}; background: transparent;")
            phdr_lay.addWidget(self._presets_title_lbl, stretch=1)

            self._drop_hint_lbl = QLabel(tr("tag_browser.saved_groups_drop_hint"))
            self._drop_hint_lbl.setFont(ui_font(-2))
            self._drop_hint_lbl.setStyleSheet(f"color: {SUBTEXT}; background: transparent;")
            phdr_lay.addWidget(self._drop_hint_lbl)

            presets_vlay.addWidget(self._presets_header)

            self._presets_list = QListWidget()
            self._presets_list.setMinimumHeight(60)
            self._presets_list.setDragEnabled(False)
            self._presets_list.setFont(ui_font(-1))
            self._presets_list.setStyleSheet(
                f"QListWidget {{ background: {SURFACE0}; border: 1px solid {SURFACE2}; "
                f"border-top: none; border-radius: 0 0 3px 3px; }}"
                f"QListWidget::item {{ color: {ACCENT}; padding: 2px 4px; }}"
                f"QListWidget::item:selected {{ background: {SURFACE1}; }}"
                f"QListWidget::item:hover {{ background: {SURFACE1}; }}"
            )
            self._presets_list.viewport().installEventFilter(self)
            self._presets_list.viewport().setAcceptDrops(True)
            self._presets_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            self._presets_list.customContextMenuRequested.connect(self._on_presets_context_menu)
            presets_vlay.addWidget(self._presets_list)
            self._v_splitter.addWidget(self._presets_panel)
            self._v_splitter.setHandleWidth(4)
        root.addWidget(self._v_splitter, stretch=1)
        if self._show_presets_panel:
            self._v_splitter.splitterMoved.connect(self._on_splitter_moved)
            self._load_presets_panel_state()

        # フッター（アイコンボタン）
        footer = QHBoxLayout()
        footer.setSpacing(4)

        self._nsfw_btn = QToolButton()
        self._nsfw_btn.setText("🔞")
        self._nsfw_btn.setCheckable(True)
        self._nsfw_btn.setChecked(self._show_nsfw)
        self._nsfw_btn.setToolTip(tr("tag_browser.nsfw_tooltip", state=tr(
            "tag_browser.nsfw_showing" if self._show_nsfw else "tag_browser.nsfw_hidden"
        )))
        self._nsfw_btn.clicked.connect(self._toggle_nsfw)
        self._nsfw_btn.setVisible(False)

        footer.addStretch()
        root.addLayout(footer)

    # ── データロード ──────────────────────────────────────────────────────────

    def _load_dictionaries(self, select_key: str | None = None) -> None:
        lib_names = list_library_names()
        if not lib_names:
            lib_names = [_DEFAULT_DICTIONARY_KEY]

        wanted = select_key or self._dictionary_key or _current_dictionary_key()
        self._dict_combo.blockSignals(True)
        self._dict_combo.clear()
        for name in lib_names:
            self._dict_combo.addItem(name, name)
        idx = self._dict_combo.findData(wanted)
        if idx < 0:
            idx = 0
        self._dict_combo.setCurrentIndex(idx)
        self._dict_combo.blockSignals(False)
        self._dictionary_key = self._dict_combo.currentData() or _DEFAULT_DICTIONARY_KEY
        self._save_current_dictionary_key()

    def _save_current_dictionary_key(self) -> None:
        _app_db.execute(
            "INSERT INTO app_settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            ("current_library_db", self._dictionary_key),
        )
        set_active_library(self._dictionary_key)

    def _on_dictionary_combo_changed(self, index: int) -> None:
        key = self._dict_combo.itemData(index)
        if not key or key == self._dictionary_key:
            return
        self._dictionary_key = key
        self._save_current_dictionary_key()
        self._node_expanded.clear()
        _styles.load_categories_from_db()
        self._load_tags()
        self.tag_categories_changed.emit()

    def _step_dictionary(self, delta: int) -> None:
        count = self._dict_combo.count()
        if count <= 1:
            return
        self._dict_combo.setCurrentIndex((self._dict_combo.currentIndex() + delta) % count)

    def _on_dictionary_context_menu(self, pos: QPoint) -> None:
        menu = QMenu(self)
        menu.addAction(tr("tag_browser.dictionary_rename_action")).triggered.connect(
            self._rename_current_dictionary
        )
        menu.addAction(tr("tag_browser.dictionary_copy_action")).triggered.connect(
            self._copy_current_dictionary
        )
        menu.addAction(tr("tag_browser.dictionary_delete_action")).triggered.connect(
            self._delete_current_dictionary
        )
        menu.exec(self._dict_combo.mapToGlobal(pos))

    def _rename_current_dictionary(self) -> None:
        QMessageBox.information(
            self,
            tr("tag_browser.dictionary_rename_title"),
            tr("tag_browser.dictionary_rename_not_supported"),
        )

    def _delete_current_dictionary(self) -> None:
        if self._dict_combo.count() <= 1:
            QMessageBox.information(
                self,
                tr("tag_browser.dictionary_delete_blocked_title"),
                tr("tag_browser.dictionary_delete_blocked_msg"),
            )
            return
        current_key = self._dictionary_key
        current_name = self._dict_combo.currentText() or tr("tag_browser.dictionary_default_name")
        reply = QMessageBox.warning(
            self,
            tr("tag_browser.dictionary_delete_title"),
            tr("tag_browser.dictionary_delete_msg", name=current_name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        next_index = self._dict_combo.currentIndex()
        if next_index >= self._dict_combo.count() - 1:
            next_index = max(0, next_index - 1)
        next_key = self._dict_combo.itemData(next_index)
        if next_key == current_key:
            next_key = None
            for i in range(self._dict_combo.count()):
                key = self._dict_combo.itemData(i)
                if key != current_key:
                    next_key = key
                    break
        if not next_key:
            return
        try:
            from db.connections import library_db_path, close_all
            db_path = library_db_path(current_key)
            close_all()
            if db_path.exists():
                db_path.unlink()
            wal = db_path.with_suffix(".db-wal")
            shm = db_path.with_suffix(".db-shm")
            for f in (wal, shm):
                if f.exists():
                    try:
                        f.unlink()
                    except Exception:
                        pass
        except Exception as exc:
            QMessageBox.critical(
                self,
                tr("tag_browser.dictionary_delete_error_title"),
                tr("tag_browser.dictionary_delete_error_msg", error=exc),
            )
            return
        self._load_dictionaries(next_key)
        self._load_tags()
        self.tag_categories_changed.emit()
        _rebuild_all_suggestions()

    def _copy_current_dictionary(self) -> None:
        current_name = self._dict_combo.currentText() or tr("tag_browser.dictionary_default_name")
        name, ok = QInputDialog.getText(
            self,
            tr("tag_browser.dictionary_copy_title"),
            tr("tag_browser.dictionary_copy_label"),
            text=tr("tag_browser.dictionary_copy_default", name=current_name),
        )
        name = single_line_text(name).strip()
        if not ok or not name:
            return
        import shutil
        from db.connections import library_db_path, close_all
        src_path = library_db_path(self._dictionary_key)
        dst_path = library_db_path(name)
        if dst_path.exists():
            QMessageBox.warning(self, tr("tag_browser.error_title"),
                                tr("tag_browser.dictionary_copy_exists", name=name))
            return
        try:
            close_all()
            shutil.copy2(src_path, dst_path)
        except Exception as e:
            QMessageBox.critical(self, tr("tag_browser.error_title"), str(e))
            return
        self._load_dictionaries(name)
        self._load_tags()
        _rebuild_all_suggestions()

    def _load_tags(self) -> None:
        self._apply_filter()

    def set_search_query(self, query: str) -> None:
        self._search.blockSignals(True)
        self._search.setText(query)
        self._search.blockSignals(False)
        self._apply_filter()

    def visible_count(self) -> int:
        if self._stack.currentWidget() is self._list:
            return self._list.count()
        return getattr(self, "_last_visible_count", 0)

    def _apply_filter(self) -> None:
        keyword = self._search.text().strip().lower()
        if keyword:
            self._stack.setCurrentIndex(1)
            self._populate_search_list(keyword)
        else:
            self._stack.setCurrentIndex(0)
            self._build_tree()

    # ── ツリー構築 ────────────────────────────────────────────────────────────

    def _save_tree_state(self) -> None:
        """全ノードの展開状態をジャンルヘッダーと中分類ノードに分けて保存する。"""
        def _walk(item: QTreeWidgetItem) -> None:
            self._remember_tree_item_state(item, item.isExpanded())
            for i in range(item.childCount()):
                _walk(item.child(i))

        for i in range(self._tree.topLevelItemCount()):
            _walk(self._tree.topLevelItem(i))

    def _remember_tree_item_state(self, item: QTreeWidgetItem, expanded: bool) -> None:
        if bool(item.data(0, _ROLE_GENRE_HDR)):
            gkey = _tree_item_genre(item)
            if gkey:
                self._genre_expanded[gkey] = expanded
        else:
            tag_id = item.data(0, _ROLE_TAG_ID)
            if tag_id is not None and (bool(item.data(0, _ROLE_IS_NAV)) or item.childCount() > 0):
                self._node_expanded[int(tag_id)] = expanded

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
        self._remember_tree_item_state(item, False)
        if self._tree_item_contains(item, self._tree.currentItem()):
            self._tree.clearSelection()
            self._tree.setCurrentItem(item)
            if item.flags() & Qt.ItemFlag.ItemIsSelectable:
                item.setSelected(True)
        self._tree.setFocus()

    def _remember_open_destination(self, genre: str, parent_id: int | None) -> None:
        if genre:
            self._genre_expanded[genre] = True
        if parent_id is not None:
            for node_id in self._ancestor_node_ids(int(parent_id)):
                self._node_expanded[node_id] = True

    @staticmethod
    def _ancestor_node_ids(node_id: int) -> list[int]:
        ids: list[int] = []
        seen: set[int] = set()
        current: int | None = node_id
        while current is not None and current not in seen:
            seen.add(current)
            ids.append(current)
            row = _library_db.fetchone(
                "SELECT parent_id FROM tags WHERE id=?",
                (current,),
            )
            current = row["parent_id"] if row else None
        return ids

    def _restore_tree_state(self) -> None:
        """保存した展開状態をツリーに適用する。"""
        def _walk(item: QTreeWidgetItem) -> None:
            if bool(item.data(0, _ROLE_GENRE_HDR)):
                gkey = _tree_item_genre(item)
                if gkey:
                    item.setExpanded(self._genre_expanded.get(gkey, False))
            else:
                tag_id = item.data(0, _ROLE_TAG_ID)
                if tag_id is not None and tag_id in self._node_expanded:
                    item.setExpanded(self._node_expanded[tag_id])
            for i in range(item.childCount()):
                _walk(item.child(i))

        for i in range(self._tree.topLevelItemCount()):
            _walk(self._tree.topLevelItem(i))

    def _restore_tree_scroll(self, value: int) -> None:
        bar = self._tree.verticalScrollBar()
        bar.setValue(max(bar.minimum(), min(value, bar.maximum())))

    def _build_tree(self) -> None:
        """ジャンル別階層ツリーを構築する。"""
        scroll_value = self._tree.verticalScrollBar().value()
        self._save_tree_state()
        self._tree.clear()

        nsfw_clause = "" if self._show_nsfw else "AND COALESCE(is_nsfw, 0) = 0"
        rows = _library_db.fetchall(
            "SELECT id, name_en, name_local, "
            "COALESCE(genre, 'mixed_unsorted') AS genre, "
            "COALESCE(category, 'object') AS category, "
            "parent_id, COALESCE(is_nav_only, 0) AS is_nav "
            f"FROM tags WHERE 1=1 {nsfw_clause} "
            "ORDER BY COALESCE(is_nav_only,0) DESC, popularity DESC, name_en",
        )

        # parent_id → [子行]
        children_of: dict[int | None, list] = {}
        for r in rows:
            children_of.setdefault(r["parent_id"], []).append(r)

        # ジャンル別ルートノード
        genre_roots: dict[str, list] = {}
        for r in children_of.get(None, []):
            gkey = r["genre"]
            genre_roots.setdefault(gkey, []).append(r)

        def _make_item(r, inherited_genre: str | None = None) -> QTreeWidgetItem:
            is_nav      = bool(r["is_nav"])
            name_en     = r["name_en"] or ""
            name_local  = r["name_local"] or ""
            display     = name_local or name_en
            genre       = inherited_genre or r["genre"]

            item = QTreeWidgetItem()
            item.setText(0, display)
            item.setData(0, _ROLE_NAME_EN,   name_en)
            item.setData(0, _ROLE_NAME_JA,   name_local)
            item.setData(0, _ROLE_GENRE,     genre)
            item.setData(0, _ROLE_GENRE_KEY, genre)
            item.setData(0, _ROLE_CATEGORY,  _GENRE_TO_CATEGORY.get(genre, genre))
            item.setData(0, _ROLE_IS_NAV,    is_nav)
            item.setData(0, _ROLE_TAG_ID,    r["id"])
            item.setData(0, _ROLE_GENRE_HDR, False)
            item.setData(0, _ROLE_DICTIONARY_KEY, self._dictionary_key)

            if is_nav:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)

            for child in children_of.get(r["id"], []):
                item.addChild(_make_item(child, genre))

            n = item.childCount()
            if n > 0 and not is_nav:
                item.setText(0, f"{display}  ＋{n}")

            return item

        total_tags = sum(1 for r in rows if not r["is_nav"])

        for gkey in _fetch_tag_genres():
            glabel = _genre_label(gkey)
            gchildren = genre_roots.get(gkey, [])

            # ジャンルヘッダーは常に表示（D&Dターゲットとして機能するため）
            hdr = QTreeWidgetItem()
            hdr.setText(0, f"  {glabel}")
            hdr.setData(0, _ROLE_GENRE,     gkey)
            hdr.setData(0, _ROLE_GENRE_KEY, gkey)
            hdr.setData(0, _ROLE_IS_NAV,    True)
            hdr.setData(0, _ROLE_GENRE_HDR, True)
            hdr.setData(0, _ROLE_TAG_ID,    None)
            hdr.setData(0, _ROLE_DICTIONARY_KEY, self._dictionary_key)
            hdr.setFlags(
                hdr.flags()
                & ~Qt.ItemFlag.ItemIsSelectable
                & ~Qt.ItemFlag.ItemIsDragEnabled
            )

            for r in gchildren:
                hdr.addChild(_make_item(r, gkey))

            self._tree.addTopLevelItem(hdr)

        self._restore_tree_state()
        QTimer.singleShot(0, lambda v=scroll_value: self._restore_tree_scroll(v))
        self._last_visible_count = total_tags
        self._count_label.setText(tr("tag_browser.count", count=total_tags))

    # ── 検索リスト構築 ────────────────────────────────────────────────────────

    def _populate_search_list(self, keyword: str) -> None:
        self._list.clear()
        nsfw_clause = "" if self._show_nsfw else "AND COALESCE(is_nsfw, 0) = 0"
        words = [w for w in keyword.split() if w]
        if not words:
            words = [keyword]
        word_clause = " OR ".join(
            ["LOWER(name_en) LIKE ? OR LOWER(COALESCE(name_local,'')) LIKE ?"] * len(words)
        )
        params: list[str] = []
        for word in words:
            like = f"%{word}%"
            params.extend([like, like])
        rows = _library_db.fetchall(
            "SELECT id, name_en, name_local, COALESCE(category,'object') AS category, "
            "COALESCE(genre,'mixed_unsorted') AS genre "
            "FROM tags "
            "WHERE COALESCE(is_nav_only,0) = 0 "
            f"  AND ({word_clause}) "
            f"{nsfw_clause} "
            "ORDER BY popularity DESC, name_en LIMIT 300",
            tuple(params),
        )
        for r in rows:
            name_en    = r["name_en"] or ""
            name_local = r["name_local"] or ""
            display    = name_local or name_en

            item = QListWidgetItem(display)
            item.setData(_ROLE_NAME_EN,   name_en)
            item.setData(_ROLE_NAME_JA,   name_local)
            item.setData(_ROLE_TAG_ID,    r["id"])
            item.setData(_ROLE_GENRE,     r["genre"])
            item.setData(_ROLE_GENRE_KEY, r["genre"])
            item.setData(_ROLE_CATEGORY,  _GENRE_TO_CATEGORY.get(r["genre"], r["genre"]))
            item.setData(_ROLE_IS_NAV,    False)
            item.setData(_ROLE_GENRE_HDR, False)
            item.setData(_ROLE_DICTIONARY_KEY, self._dictionary_key)
            self._list.addItem(item)

        self._last_visible_count = self._list.count()
        self._count_label.setText(tr("tag_browser.search_count", count=self._list.count()))

    # ── スロット ──────────────────────────────────────────────────────────────

    def _on_splitter_moved(self, pos: int, index: int) -> None:
        if not self._presets_expanded:
            self._apply_presets_collapse_state()
            return
        sizes = self._v_splitter.sizes()
        presets_h = sizes[1] if len(sizes) > 1 else 0
        if presets_h < self._presets_min_height():
            return
        self._saved_presets_height = presets_h
        try:
            _app_db.execute(
                "INSERT INTO app_settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                ("tag_browser_presets_height", str(presets_h)),
            )
        except Exception:
            pass

    def _toggle_nsfw(self) -> None:
        self._show_nsfw = self._nsfw_btn.isChecked()
        _app_db.execute(
            "UPDATE app_settings SET value=? WHERE key='show_nsfw'",
            ("1" if self._show_nsfw else "0",),
        )
        self._nsfw_btn.setToolTip(tr("tag_browser.nsfw_tooltip", state=tr(
            "tag_browser.nsfw_showing" if self._show_nsfw else "tag_browser.nsfw_hidden"
        )))
        self._load_tags()

    def set_show_nsfw(self, show: bool) -> None:
        """設定ダイアログ側の NSFW 表示設定をタグブラウザへ反映する。"""
        if self._show_nsfw == show:
            return
        self._show_nsfw = show
        self._nsfw_btn.blockSignals(True)
        self._nsfw_btn.setChecked(show)
        self._nsfw_btn.blockSignals(False)
        self._nsfw_btn.setToolTip(tr("tag_browser.nsfw_tooltip", state=tr(
            "tag_browser.nsfw_showing" if self._show_nsfw else "tag_browser.nsfw_hidden"
        )))
        self._load_tags()

    def _on_tree_single_clicked(self, item: QTreeWidgetItem, _col: int) -> None:
        if self._skip_next_click_toggle:
            self._skip_next_click_toggle = False
            return
        if (
            item.childCount() > 0
            and bool(item.data(0, _ROLE_IS_NAV))
            and not bool(item.data(0, _ROLE_GENRE_HDR))
        ):
            item.setExpanded(not item.isExpanded())

    def _on_tree_double_clicked(self, item: QTreeWidgetItem, _col: int) -> None:
        is_nav = bool(item.data(0, _ROLE_IS_NAV))
        if is_nav:
            return

        cat = item.data(0, _ROLE_CATEGORY) or ""
        n   = item.childCount()
        if n > 0:
            for i in range(n):
                child = item.child(i)
                if not bool(child.data(0, _ROLE_IS_NAV)):
                    en    = child.data(0, _ROLE_NAME_EN) or ""
                    local = child.data(0, _ROLE_NAME_JA) or ""
                    ccat  = child.data(0, _ROLE_CATEGORY) or cat
                    self.tag_selected.emit(en, local, ccat, self._dictionary_key)
        else:
            en    = item.data(0, _ROLE_NAME_EN) or ""
            local = item.data(0, _ROLE_NAME_JA) or ""
            self.tag_selected.emit(en, local, cat, self._dictionary_key)

    def _on_list_double_clicked(self, item: QListWidgetItem) -> None:
        en    = item.data(_ROLE_NAME_EN) or ""
        local = item.data(_ROLE_NAME_JA) or ""
        cat   = item.data(_ROLE_CATEGORY) or ""
        self.tag_selected.emit(en, local, cat, self._dictionary_key)

    def _on_tree_context_menu(self, pos: QPoint) -> None:
        item = self._tree.itemAt(pos)
        menu = QMenu(self)

        if item is None:
            # 空白右クリック: カテゴリ（ルート分類）を追加できる
            menu.addAction(tr("tag_browser.menu_add_category")).triggered.connect(
                self._add_category
            )
        else:
            is_genre_hdr = bool(item.data(0, _ROLE_GENRE_HDR))
            is_nav  = bool(item.data(0, _ROLE_IS_NAV))
            tag_id  = item.data(0, _ROLE_TAG_ID)
            genre      = _tree_item_genre(item)
            name_en    = item.data(0, _ROLE_NAME_EN) or ""
            name_local = item.data(0, _ROLE_NAME_JA) or ""
            cat        = item.data(0, _ROLE_CATEGORY) or "object"
            label      = name_local or name_en
            n       = item.childCount()

            if is_genre_hdr:
                # ジャンルヘッダー右クリック
                menu.addAction(tr("tag_browser.menu_add_child_tag")).triggered.connect(
                    lambda: self._show_node_dialog(
                        genre=genre, is_nav=False, parent_id=None,
                        title=tr("tag_browser.menu_add_child_tag_title", label=_genre_label(genre))
                    )
                )
                menu.addAction(tr("tag_browser.menu_add_subcat")).triggered.connect(
                    lambda: self._show_node_dialog(
                        genre=genre, is_nav=True, parent_id=None,
                        title=tr("tag_browser.menu_add_subcat_title", label=_genre_label(genre))
                    )
                )
                menu.addSeparator()
                menu.addAction(tr("tag_browser.menu_genre_settings")).triggered.connect(
                    lambda: self._show_genre_settings(genre)
                )
                menu.addSeparator()
                menu.addAction(tr("tag_browser.menu_add_category")).triggered.connect(
                    self._add_category
                )
                # mixed_unsorted(99) は受け皿なので削除不可
                if genre not in ("mixed_unsorted", "all", ""):
                    menu.addAction(
                        tr("tag_browser.menu_delete_category", label=_genre_label(genre))
                    ).triggered.connect(lambda _checked=False, g=genre: self._delete_category(g))
            elif is_nav:
                # 中分類ノード右クリック
                menu.addAction(tr("tag_browser.menu_edit_node")).triggered.connect(
                    lambda: self._edit_node(item)
                )
                menu.addSeparator()
                menu.addAction(tr("tag_browser.menu_add_child_tag")).triggered.connect(
                    lambda: self._show_node_dialog(
                        genre=genre, is_nav=False, parent_id=tag_id,
                        title=tr("tag_browser.menu_add_child_tag_title", label=label)
                    )
                )
                menu.addAction(tr("tag_browser.menu_add_subcat")).triggered.connect(
                    lambda: self._show_node_dialog(
                        genre=genre, is_nav=True, parent_id=tag_id,
                        title=tr("tag_browser.menu_add_subcat_title", label=label)
                    )
                )
                menu.addSeparator()
                del_text = tr("tag_browser.menu_delete_with_promote", label=label) if n > 0 else tr("tag_browser.menu_delete_node", label=label)
                menu.addAction(del_text).triggered.connect(lambda: self._delete_node(item))
            else:
                # タグノード右クリック
                menu.addAction(tr("tag_browser.menu_edit_node")).triggered.connect(
                    lambda: self._edit_node(item)
                )
                menu.addSeparator()
                if n > 0:
                    menu.addAction(tr("tag_browser.menu_add_only", label=label)).triggered.connect(
                        lambda: self.tag_selected.emit(name_en, name_local, cat, self._dictionary_key)
                    )
                    menu.addAction(tr("tag_browser.menu_add_children", n=n)).triggered.connect(
                        lambda: self._on_tree_double_clicked(item, 0)
                    )
                else:
                    menu.addAction(tr("tag_browser.menu_add_tag", label=label)).triggered.connect(
                        lambda: self.tag_selected.emit(name_en, name_local, cat, self._dictionary_key)
                    )
                menu.addSeparator()
                menu.addAction(tr("tag_browser.menu_add_child_tag")).triggered.connect(
                    lambda: self._show_node_dialog(
                        genre=genre, is_nav=False, parent_id=tag_id,
                        title=tr("tag_browser.menu_add_child_tag_title", label=label)
                    )
                )
                menu.addAction(tr("tag_browser.menu_add_subcat")).triggered.connect(
                    lambda: self._show_node_dialog(
                        genre=genre, is_nav=True, parent_id=tag_id,
                        title=tr("tag_browser.menu_add_subcat_title", label=label)
                    )
                )
                menu.addSeparator()
                del_text = tr("tag_browser.menu_delete_with_promote", label=label) if n > 0 else tr("tag_browser.menu_delete_node", label=label)
                menu.addAction(del_text).triggered.connect(lambda: self._delete_node(item))

        if not menu.isEmpty():
            menu.exec(self._tree.viewport().mapToGlobal(pos))

    def _show_genre_settings(self, genre: str) -> None:
        dlg = _GenreSettingsDialog(genre, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        if dlg.reset_requested:
            self._reset_genre_colors(genre)
            return
        label, light_bg, light_fg, dark_bg, dark_fg = dlg.values()
        row = _library_db.fetchone("SELECT sort_order FROM tag_categories WHERE key=?", (genre,))
        sort_order = row["sort_order"] if row else self._genre_sort_order(genre)
        _library_db.execute(
            "INSERT OR REPLACE INTO tag_categories "
            "(key, label, bg_color, fg_color, bg_color_light, fg_color_light, bg_color_dark, fg_color_dark, sort_order, is_tag_genre) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
            (genre, label, dark_bg, dark_fg, light_bg, light_fg, dark_bg, dark_fg, sort_order),
        )
        self._reload_color_state()

    def _set_genre_color(self, genre: str, target: str) -> None:
        light_bg, light_fg, dark_bg, dark_fg = _GenreSettingsDialog._load_theme_colors(genre)
        is_light = _styles.is_light_theme()
        bg, fg = (light_bg, light_fg) if is_light else (dark_bg, dark_fg)
        current = QColor(bg if target == "bg" else fg)
        title = tr(
            "tag_browser.color_bg_title" if target == "bg" else "tag_browser.color_fg_title",
            label=_genre_label(genre),
        )
        chosen = QColorDialog.getColor(current, self, title)
        if not chosen.isValid():
            return
        if target == "bg":
            bg = chosen.name()
        else:
            fg = chosen.name()
        label = _genre_label(genre)
        row = _library_db.fetchone("SELECT sort_order FROM tag_categories WHERE key=?", (genre,))
        sort_order = row["sort_order"] if row else self._genre_sort_order(genre)
        if is_light:
            light_bg, light_fg = bg, fg
        else:
            dark_bg, dark_fg = bg, fg
        _library_db.execute(
            "INSERT OR REPLACE INTO tag_categories "
            "(key, label, bg_color, fg_color, bg_color_light, fg_color_light, bg_color_dark, fg_color_dark, sort_order, is_tag_genre) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
            (genre, label, dark_bg, dark_fg, light_bg, light_fg, dark_bg, dark_fg, sort_order),
        )
        self._reload_color_state()

    def _reset_genre_colors(self, genre: str) -> None:
        light_bg, light_fg = _styles.tag_browser_default_base_colors_for_theme(genre, "light")
        dark_bg, dark_fg = _styles.tag_browser_default_base_colors_for_theme(genre, "dark")
        label = "" if genre in _DEFAULT_GENRE_KEYS else _genre_label(genre)
        row = _library_db.fetchone(
            "SELECT sort_order FROM tag_categories WHERE key=?",
            (genre,),
        )
        sort_order = row["sort_order"] if row else self._genre_sort_order(genre)
        _library_db.execute(
            """INSERT OR REPLACE INTO tag_categories
               (key, label, bg_color, fg_color, bg_color_light, fg_color_light,
                bg_color_dark, fg_color_dark, sort_order, is_tag_genre)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
            (genre, label, dark_bg, dark_fg, light_bg, light_fg, dark_bg, dark_fg, sort_order),
        )
        self._reload_color_state()

    def _reload_color_state(self) -> None:
        _styles.load_categories_from_db()
        self._tree.viewport().update()
        self._list.viewport().update()
        self._load_tags()
        self.tag_categories_changed.emit()

    @staticmethod
    def _genre_sort_order(genre: str) -> int:
        for idx, (gkey, _label) in enumerate(_TAG_GENRES, start=1):
            if gkey == genre:
                return idx * 10
        return 999

    def _next_genre_sort_order(self) -> int:
        """新規カテゴリ用の sort_order。mixed_unsorted(99) より前に来るよう採番する。"""
        row = _library_db.fetchone(
            "SELECT MAX(sort_order) AS m FROM tag_categories "
            "WHERE COALESCE(is_tag_genre,0)=1 AND key != 'mixed_unsorted'",
        )
        base = int(row["m"]) if row and row["m"] is not None else 0
        return base + 10

    def _add_category(self) -> None:
        """ルートカテゴリ（ジャンル）を新規追加する。"""
        label, ok = QInputDialog.getText(
            self,
            tr("tag_browser.add_category_title"),
            tr("tag_browser.add_category_prompt"),
        )
        if not ok:
            return
        label = single_line_text(label).strip()
        if not label:
            return
        key = f"custom_{uuid.uuid4().hex[:12]}"
        sort_order = self._next_genre_sort_order()
        # 既定色は未分類と同じグレー系。あとから「カテゴリ設定」で変更できる。
        light_bg, light_fg = "#e8e8f0", "#6c6f85"
        dark_bg, dark_fg = "#2a2a3a", "#a6adc8"
        _library_db.execute(
            "INSERT OR REPLACE INTO tag_categories "
            "(key, label, bg_color, fg_color, bg_color_light, fg_color_light, "
            " bg_color_dark, fg_color_dark, sort_order, is_tag_genre) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
            (key, label, dark_bg, dark_fg, light_bg, light_fg, dark_bg, dark_fg, sort_order),
        )
        self._reload_color_state()

    def _delete_category(self, genre: str) -> None:
        """ルートカテゴリを削除する。所属タグは現在ライブラリ内だけ mixed_unsorted(99) へ移す。"""
        genre = _normalize_genre_key(genre)
        if genre in ("mixed_unsorted", "all", ""):
            QMessageBox.information(
                self,
                tr("tag_browser.delete_category_title"),
                tr("tag_browser.category_protected"),
            )
            return
        row = _library_db.fetchone(
            "SELECT label FROM tag_categories WHERE key=? AND COALESCE(is_tag_genre,0)=1",
            (genre,),
        )
        if row is None:
            QMessageBox.warning(
                self,
                tr("tag_browser.delete_category_title"),
                tr("tag_browser.category_delete_missing", label=str(genre)),
            )
            self._genre_expanded.pop(genre, None)
            self._reload_color_state()
            return
        label = _genre_label(genre)
        cnt_row = _library_db.fetchone(
            "SELECT COUNT(*) AS c FROM tags "
            "WHERE (COALESCE(genre,'mixed_unsorted')=? OR COALESCE(category,'')=?)",
            (genre, genre),
        )
        cnt = int(cnt_row["c"]) if cnt_row else 0
        reply = QMessageBox.question(
            self,
            tr("tag_browser.delete_category_title"),
            tr("tag_browser.delete_category_confirm", label=label, n=cnt),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        # 分類(genre)を99へ移し、タイル色キー(category)も合わせて99に揃える
        # （削除したカテゴリの色行が消え、タイル色が宙に浮くのを防ぐ）。
        _library_db.execute(
            "UPDATE tags SET genre='mixed_unsorted', category='mixed_unsorted' "
            "WHERE (COALESCE(genre,'mixed_unsorted')=? OR COALESCE(category,'')=?)",
            (genre, genre),
        )
        _library_db.execute(
            "DELETE FROM tag_categories WHERE key=?",
            (genre,),
        )
        self._genre_expanded.pop(genre, None)
        self._reload_color_state()

    # ── D&D: ブラウザ → プロンプト ───────────────────────────────────────────

    def _drop_destination_at(self, pos: QPoint, *, tile_drop: bool) -> tuple[str, int | None, str]:
        item = self._tree.itemAt(pos)
        if item is None:
            return "mixed_unsorted", None, _genre_label("mixed_unsorted")

        is_genre_hdr = bool(item.data(0, _ROLE_GENRE_HDR))
        is_nav = bool(item.data(0, _ROLE_IS_NAV))
        tag_id = item.data(0, _ROLE_TAG_ID)
        genre = _tree_item_genre(item)

        if is_genre_hdr:
            return genre, None, _genre_label(genre)
        if is_nav and tag_id:
            return genre, int(tag_id), self._item_label(item)

        parent = item.parent()
        if parent and not bool(parent.data(0, _ROLE_GENRE_HDR)):
            parent_id = parent.data(0, _ROLE_TAG_ID)
            if parent_id:
                return genre, int(parent_id), self._item_label(parent)
        return genre, None, _genre_label(genre)

    @staticmethod
    def _item_label(item: QTreeWidgetItem) -> str:
        return item.data(0, _ROLE_NAME_JA) or item.data(0, _ROLE_NAME_EN) or item.text(0).strip()

    def _item_path_label(self, item: QTreeWidgetItem) -> str:
        parts: list[str] = []
        cur: QTreeWidgetItem | None = item
        while cur is not None:
            if bool(cur.data(0, _ROLE_GENRE_HDR)):
                genre = _tree_item_genre(cur)
                parts.append(_genre_label(genre))
                break
            label = cur.data(0, _ROLE_NAME_JA) or cur.data(0, _ROLE_NAME_EN) or cur.text(0).strip()
            if label:
                parts.append(label)
            cur = cur.parent()
        return " / ".join(reversed(parts))

    def _show_drop_hint(self, event, *, tile_drop: bool) -> None:
        genre, parent_id, label = self._drop_destination_at(event.position().toPoint(), tile_drop=tile_drop)
        bg_hex, fg_hex = _genre_color(genre)
        text = label
        if self._drop_hint_widget is None:
            self._drop_hint_widget = QLabel(None, Qt.WindowType.ToolTip)
            self._drop_hint_widget.setFont(ui_font(-1, bold=True))
            self._drop_hint_widget.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._drop_hint_text = label
        self._drop_hint_widget.setText(text)
        self._drop_hint_widget.setStyleSheet(
            f"QLabel {{ background: {bg_hex}; color: {fg_hex}; "
            f"border: 2px solid {fg_hex}; border-radius: 4px; padding: 5px 8px; }}"
        )
        self._drop_hint_widget.adjustSize()
        hint_size = self._drop_hint_widget.sizeHint()
        cursor_pos = event.position().toPoint()
        viewport_rect = self._tree.viewport().rect()
        margin = 8

        if cursor_pos.y() < viewport_rect.height() // 2:
            hint_y = max(margin, viewport_rect.height() - hint_size.height() - margin)
        else:
            hint_y = margin

        if cursor_pos.x() < viewport_rect.width() // 2:
            hint_x = max(margin, viewport_rect.width() - hint_size.width() - margin)
        else:
            hint_x = margin

        hint_pos = QPoint(hint_x, hint_y)
        global_pos = self._tree.viewport().mapToGlobal(hint_pos)
        self._drop_hint_widget.move(global_pos)
        self._drop_hint_widget.show()

    def _hide_drop_hint(self) -> None:
        self._drop_hint_text = ""
        if self._drop_hint_widget is not None:
            self._drop_hint_widget.hide()

    def _reset_tag_drag_state(self) -> None:
        self._drag_start      = None
        self._drag_item_en    = ""
        self._drag_item_ja    = ""
        self._drag_item_cat   = ""
        self._drag_item_id    = None
        self._drag_item_genre = "mixed_unsorted"
        self._drag_item_is_nav = False
        self._drag_item_child_count = 0
        self._drag_source_view = None
        self._drag_source_pos = None

    def _reset_preset_drag_state(self) -> None:
        self._preset_drag_start = None
        self._preset_drag_item  = None

    def eventFilter(self, obj, event) -> bool:
        from PySide6.QtCore import QEvent
        presets_viewport = self._presets_list.viewport() if hasattr(self, "_presets_list") else None
        if event.type() == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton:
                self._drag_start = event.pos()
                if obj is self._tree.viewport():
                    item = self._tree.itemAt(event.pos())
                    self._skip_next_click_toggle = False
                    if item:
                        self._skip_next_click_toggle = event.pos().x() < self._tree.visualItemRect(item).left()
                    if item and bool(item.data(0, _ROLE_GENRE_HDR)):
                        # ジャンルヘッダー: ~ItemIsSelectableのためitemClickedが発火しないので直接開閉
                        item.setExpanded(not item.isExpanded())
                        self._drag_start = None
                        return True
                    elif item and not bool(item.data(0, _ROLE_GENRE_HDR)):
                        # タグ・中分類: ドラッグ開始準備
                        self._drag_item_en     = item.data(0, _ROLE_NAME_EN) or ""
                        self._drag_item_ja     = item.data(0, _ROLE_NAME_JA) or ""  # name_local
                        self._drag_item_id     = item.data(0, _ROLE_TAG_ID)
                        self._drag_item_genre  = _tree_item_genre(item)
                        self._drag_item_cat    = _GENRE_TO_CATEGORY.get(self._drag_item_genre, self._drag_item_genre)
                        self._drag_item_is_nav = bool(item.data(0, _ROLE_IS_NAV))
                        self._drag_item_child_count = item.childCount()
                        self._drag_source_view = self._tree
                        self._drag_source_pos = event.pos()
                    else:
                        self._drag_start = None
                elif obj is self._list.viewport():
                    item = self._list.itemAt(event.pos())
                    if item:
                        self._drag_item_en    = item.data(_ROLE_NAME_EN) or ""
                        self._drag_item_ja    = item.data(_ROLE_NAME_JA) or ""
                        self._drag_item_id    = item.data(_ROLE_TAG_ID)
                        self._drag_item_genre = _list_item_genre(item)
                        self._drag_item_cat   = _GENRE_TO_CATEGORY.get(self._drag_item_genre, self._drag_item_genre)
                        self._drag_item_is_nav = False
                        self._drag_item_child_count = 0
                        self._drag_source_view = self._list
                        self._drag_source_pos = event.pos()
                    else:
                        self._drag_start = None
                elif presets_viewport is not None and obj is presets_viewport:
                    self._drag_start   = None
                    self._drag_item_en = ""
                    pitem = self._presets_list.itemAt(event.pos())
                    if pitem:
                        self._preset_drag_start = event.pos()
                        self._preset_drag_item  = pitem
                    else:
                        self._preset_drag_start = None
                        self._preset_drag_item  = None

        elif event.type() == QEvent.Type.MouseMove:
            if (self._drag_start is not None
                    and (event.buttons() & Qt.MouseButton.LeftButton)
                    and self._drag_item_en):
                if (event.pos() - self._drag_start).manhattanLength() >= QApplication.startDragDistance():
                    self._begin_browser_drag()
                    return True
            if (self._preset_drag_start is not None
                    and (event.buttons() & Qt.MouseButton.LeftButton)
                    and self._preset_drag_item is not None):
                if (event.pos() - self._preset_drag_start).manhattanLength() >= QApplication.startDragDistance():
                    self._begin_group_drag()
                    return True

        elif event.type() == QEvent.Type.MouseButtonRelease:
            self._reset_tag_drag_state()
            self._reset_preset_drag_state()

        elif event.type() == QEvent.Type.DragEnter:
            if obj in (self._tree.viewport(), self._list.viewport()):
                from ui.tile_widget import TILE_MIME
                if event.mimeData().hasFormat(self._BROWSER_MIME) or event.mimeData().hasFormat(TILE_MIME):
                    if obj is self._tree.viewport():
                        self._show_drop_hint(event, tile_drop=event.mimeData().hasFormat(TILE_MIME))
                    event.acceptProposedAction()
                else:
                    self._hide_drop_hint()
                    event.ignore()
                return True
            elif presets_viewport is not None and obj is presets_viewport:
                from ui.tile_widget import TILE_MIME
                if event.mimeData().hasFormat(TILE_MIME):
                    import ui.tile_drag as tile_drag
                    tw = tile_drag.get_drag()
                    if tw is not None:
                        from core.prompt_builder import GroupTile
                        if isinstance(tw.tile, GroupTile):
                            event.acceptProposedAction()
                            return True
                event.ignore()
                return True

        elif event.type() == QEvent.Type.DragMove:
            if obj in (self._tree.viewport(), self._list.viewport()):
                from ui.tile_widget import TILE_MIME
                if event.mimeData().hasFormat(self._BROWSER_MIME) or event.mimeData().hasFormat(TILE_MIME):
                    if obj is self._tree.viewport():
                        self._show_drop_hint(event, tile_drop=event.mimeData().hasFormat(TILE_MIME))
                    else:
                        self._hide_drop_hint()
                    event.acceptProposedAction()
                else:
                    self._hide_drop_hint()
                    event.ignore()
                return True
            elif presets_viewport is not None and obj is presets_viewport:
                from ui.tile_widget import TILE_MIME
                if event.mimeData().hasFormat(TILE_MIME):
                    import ui.tile_drag as tile_drag
                    tw = tile_drag.get_drag()
                    if tw is not None:
                        from core.prompt_builder import GroupTile
                        if isinstance(tw.tile, GroupTile):
                            event.acceptProposedAction()
                            return True
                event.ignore()
                return True

        elif event.type() == QEvent.Type.DragLeave:
            self._hide_drop_hint()
            self._reset_tag_drag_state()
            self._reset_preset_drag_state()
            return True

        elif event.type() == QEvent.Type.Drop:
            self._hide_drop_hint()
            if obj in (self._tree.viewport(), self._list.viewport()):
                self._handle_viewport_drop(event)
                self._reset_tag_drag_state()
                return True
            elif presets_viewport is not None and obj is presets_viewport:
                self._handle_preset_drop(event)
                self._reset_preset_drag_state()
                return True

        return super().eventFilter(obj, event)

    def _begin_browser_drag(self) -> None:
        import ui.tag_drag as tag_drag
        tag_drag.set_drag(
            name_en    = self._drag_item_en,
            name_local = self._drag_item_ja,
            category   = self._drag_item_cat,
            tag_id   = self._drag_item_id,
            dictionary_key = self._dictionary_key,
            is_nav   = self._drag_item_is_nav,
            child_count = self._drag_item_child_count,
        )

        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(self._BROWSER_MIME, self._drag_item_en.encode())
        drag.setMimeData(mime)

        pixmap, hotspot = self._browser_drag_pixmap()
        if not pixmap.isNull():
            drag.setPixmap(pixmap)
            drag.setHotSpot(hotspot)

        self._drag_start = None
        try:
            drag.exec(Qt.DropAction.CopyAction)
        finally:
            self._reset_tag_drag_state()
            tag_drag.clear_drag()

    def _browser_drag_pixmap(self) -> tuple[QPixmap, QPoint]:
        """表示中のタグ行そのものを、他のD&Dと同じ半透明プレビューにする。"""
        from ui.drag_pixmap import translucent_drag_pixmap

        view = self._drag_source_view
        pos = self._drag_source_pos
        if view is self._tree and pos is not None:
            item = self._tree.itemAt(pos)
            if item is not None:
                rect = self._tree.visualItemRect(item)
                return self._grab_view_row_pixmap(self._tree, rect, pos)

        if view is self._list and pos is not None:
            item = self._list.itemAt(pos)
            if item is not None:
                rect = self._list.visualItemRect(item)
                return self._grab_view_row_pixmap(self._list, rect, pos)

        label_text = ("📁 " if self._drag_item_is_nav else "") + (self._drag_item_ja or self._drag_item_en)
        fallback = QPixmap(max(60, len(label_text) * 8 + 16), 26)
        bg_hex, fg_hex = _genre_color(self._drag_item_genre)
        fallback.fill(QColor(bg_hex).lighter(130))
        painter = QPainter(fallback)
        painter.setPen(QColor(fg_hex))
        painter.drawText(
            fallback.rect().adjusted(6, 0, -6, 0),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            label_text,
        )
        painter.end()
        return translucent_drag_pixmap(fallback), QPoint(8, 13)

    @staticmethod
    def _grab_view_row_pixmap(view, rect, press_pos: QPoint) -> tuple[QPixmap, QPoint]:
        from ui.drag_pixmap import translucent_drag_pixmap

        viewport = view.viewport()
        rect = rect.adjusted(0, 0, 0, 0).intersected(viewport.rect())
        if rect.isEmpty():
            return QPixmap(), QPoint(0, 0)

        pixmap = viewport.grab(rect)
        hotspot = press_pos - rect.topLeft()
        hotspot.setX(max(0, min(hotspot.x(), rect.width() - 1)))
        hotspot.setY(max(0, min(hotspot.y(), rect.height() - 1)))
        return translucent_drag_pixmap(pixmap), hotspot

    def _begin_group_drag(self) -> None:
        item = self._preset_drag_item
        if item is None:
            return
        preset_id = item.data(_ROLE_PRESET_ID)
        row = _library_db.fetchone("SELECT group_json FROM group_presets WHERE id=?", (preset_id,))
        if not row or not row["group_json"]:
            return
        group_json = row["group_json"]
        preset_name = item.text()

        import ui.tag_drag as tag_drag
        tag_drag.set_group_drag(group_json, preset_name)

        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(self._GROUP_BROWSER_MIME, group_json.encode())
        drag.setMimeData(mime)

        label_text = item.text()
        pixmap = QPixmap(max(80, len(label_text) * 8 + 16), 26)
        pixmap.fill(QColor(SURFACE1).lighter(120))
        p = QPainter(pixmap)
        p.setPen(QColor(ACCENT))
        p.drawText(
            pixmap.rect().adjusted(6, 0, -6, 0),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            label_text,
        )
        p.end()
        drag.setPixmap(pixmap)
        drag.setHotSpot(QPoint(8, 13))

        self._reset_preset_drag_state()
        try:
            drag.exec(Qt.DropAction.CopyAction)
        finally:
            self._reset_preset_drag_state()
            tag_drag.clear_group_drag()

    def _move_subtree_genre(self, node_id: int, genre: str, parent_id: int | None) -> None:
        """ノードとその全子孫の genre・category・parent_id を再帰的に更新する。"""
        cat = _GENRE_TO_CATEGORY.get(genre, "object")
        _library_db.execute(
            "UPDATE tags SET genre=?, category=?, parent_id=? WHERE id=?",
            (genre, cat, parent_id, node_id),
        )
        children = _library_db.fetchall(
            "SELECT id FROM tags WHERE parent_id=?",
            (node_id,),
        )
        for child in children:
            # 子ノードは parent_id はそのままで genre のみ変更
            _library_db.execute(
                "UPDATE tags SET genre=?, category=? WHERE id=?",
                (genre, cat, child["id"]),
            )
            grandchildren = _library_db.fetchall(
                "SELECT id FROM tags WHERE parent_id=?",
                (child["id"],),
            )
            for gc in grandchildren:
                self._move_subtree_genre_inner(gc["id"], genre, cat)

    def _move_subtree_genre_inner(self, node_id: int, genre: str, cat: str) -> None:
        """_move_subtree_genre の再帰補助（parent_id は変更しない）。"""
        _library_db.execute(
            "UPDATE tags SET genre=?, category=? WHERE id=?",
            (genre, cat, node_id),
        )
        children = _library_db.fetchall(
            "SELECT id FROM tags WHERE parent_id=?",
            (node_id,),
        )
        for child in children:
            self._move_subtree_genre_inner(child["id"], genre, cat)

    def _handle_preset_drop(self, event) -> None:
        from ui.tile_widget import TILE_MIME
        import ui.tile_drag as tile_drag

        if not event.mimeData().hasFormat(TILE_MIME):
            event.ignore()
            return

        tw = tile_drag.get_drag()
        if tw is None:
            event.ignore()
            return

        from core.prompt_builder import GroupTile
        if not isinstance(tw.tile, GroupTile):
            event.ignore()
            return

        from PySide6.QtWidgets import QInputDialog
        import json
        from db.group_preset_db import unique_group_name

        name, ok = QInputDialog.getText(
            self, tr("group.save_dialog_title"), tr("group.save_dialog_label"),
            text=tw.tile.name,
        )
        if ok and name.strip():
            name = unique_group_name(name)
            group_data = tw.tile.to_dict(include_ui_state=False)
            if isinstance(group_data, dict):
                group_data["name"] = name
            group_json = json.dumps(group_data, ensure_ascii=False)
            row = _library_db.fetchone(
                "SELECT COALESCE(MAX(sort_order), 0) + 10 AS n FROM group_presets"
            )
            sort_order = row["n"] if row else 10
            _library_db.execute(
                "INSERT INTO group_presets (name, group_json, sort_order) VALUES (?, ?, ?)",
                (name.strip(), group_json, sort_order),
            )
            self._load_presets()

        event.acceptProposedAction()

    def _handle_viewport_drop(self, event) -> None:
        from ui.tile_widget import TILE_MIME
        import ui.tag_drag as tag_drag

        if event.mimeData().hasFormat(self._BROWSER_MIME):
            info = tag_drag.get_drag()
            src_id = info.get("id") if info else None
            if src_id:
                drop_pos    = event.position().toPoint()
                target_item = self._tree.itemAt(drop_pos)
                if target_item and target_item.data(0, _ROLE_TAG_ID) == src_id:
                    event.acceptProposedAction()
                    return
                tgt_genre, tgt_parent_id, _label = self._drop_destination_at(drop_pos, tile_drop=True)
                if tgt_parent_id == src_id:
                    event.acceptProposedAction()
                    return

                if bool(info.get("is_nav")):
                    # ── 中分類ノードのドロップ: サブツリーごとジャンル変更 ──
                    self._remember_open_destination(tgt_genre, tgt_parent_id)
                    self._move_subtree_genre(src_id, tgt_genre, parent_id=tgt_parent_id)
                else:
                    # ── タグノードのドロップ ──
                    self._remember_open_destination(tgt_genre, tgt_parent_id)
                    self._move_subtree_genre(src_id, tgt_genre, parent_id=tgt_parent_id)

                self._load_tags()
                self.tag_categories_changed.emit()
            event.acceptProposedAction()

        elif event.mimeData().hasFormat(TILE_MIME):
            import ui.tile_drag as tile_drag
            tw = tile_drag.get_drag()
            if tw is not None:
                from core.prompt_builder import TagTile
                if isinstance(tw.tile, TagTile):
                    drop_pos    = event.position().toPoint()
                    drop_genre, drop_parent_id, _label = self._drop_destination_at(drop_pos, tile_drop=True)
                    self._remember_open_destination(drop_genre, drop_parent_id)
                    self._show_node_dialog(
                        name_en=tw.tile.tag_name,
                        name_local=tw.tile.tag_local or "",
                        genre=drop_genre,
                        parent_id=drop_parent_id,
                        title=tr("tag_browser.menu_add_root_tag_title"),
                    )
                    row = _library_db.fetchone(
                        "SELECT name_en, name_local, COALESCE(category,'object') AS category "
                        "FROM tags WHERE name_en=?",
                        (tw.tile.tag_name,),
                    )
                    if row:
                        if row["name_en"]:
                            tw.tile.tag_name = row["name_en"]
                        if row["name_local"]:
                            tw.tile.tag_local = row["name_local"]
                        if row["category"]:
                            tw.tile.category = row["category"]
                    tw.refresh()
            event.acceptProposedAction()
        else:
            event.ignore()

    # ── 保存グループ管理 ──────────────────────────────────────────────────────

    def _toggle_presets(self) -> None:
        if self._presets_expanded:
            sizes = self._v_splitter.sizes()
            if len(sizes) > 1 and sizes[1] >= self._presets_min_height():
                self._saved_presets_height = sizes[1]
        self._presets_expanded = not self._presets_expanded
        self._apply_presets_collapse_state()
        try:
            _app_db.execute(
                "INSERT INTO app_settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                ("tag_browser_presets_collapsed", "0" if self._presets_expanded else "1"),
            )
        except Exception:
            pass

    def _presets_min_height(self) -> int:
        return max(24, self._presets_header.sizeHint().height() + 2)

    def _presets_fallback_height(self) -> int:
        row_h = self._presets_list.sizeHintForRow(0)
        if row_h <= 0:
            row_h = self._presets_list.fontMetrics().lineSpacing() + 8
        return self._presets_min_height() + row_h * 3 + 6

    def _apply_presets_collapse_state(self) -> None:
        collapsed_h = self._presets_min_height()
        self._presets_list.setVisible(self._presets_expanded)
        self._presets_toggle_btn.setText("▼" if self._presets_expanded else "▶")

        if not self._presets_expanded:
            self._presets_list.clearSelection()
            self._presets_list.setCurrentItem(None)
            self._presets_toggle_btn.setFocus()
            self._presets_panel.setMinimumHeight(collapsed_h)
            self._presets_panel.setMaximumHeight(collapsed_h)
            def _collapse_now() -> None:
                total = sum(self._v_splitter.sizes())
                if total > collapsed_h:
                    self._v_splitter.setSizes([total - collapsed_h, collapsed_h])
            QTimer.singleShot(0, _collapse_now)
            return

        self._presets_panel.setMinimumHeight(collapsed_h)
        self._presets_panel.setMaximumHeight(16777215)
        target_h = self._saved_presets_height if self._saved_presets_height >= collapsed_h else self._presets_fallback_height()
        def _restore_now() -> None:
            total = sum(self._v_splitter.sizes())
            if total > target_h:
                self._v_splitter.setSizes([total - target_h, target_h])
        QTimer.singleShot(0, _restore_now)

    def _load_presets_panel_state(self) -> None:
        row = _app_db.fetchone("SELECT value FROM app_settings WHERE key='tag_browser_presets_height'")
        if row:
            try:
                v = int(row["value"])
                if v >= self._presets_min_height():
                    self._saved_presets_height = v
            except (TypeError, ValueError):
                pass
        else:
            # 旧設定から移行できる場合は、上ペイン位置ではなく下ペイン初期値として安全な3行分を使う。
            self._saved_presets_height = self._presets_fallback_height()

        row = _app_db.fetchone("SELECT value FROM app_settings WHERE key='tag_browser_presets_collapsed'")
        self._presets_expanded = not (row and row["value"] == "1")
        self._apply_presets_collapse_state()

    def _load_presets(self) -> None:
        if not hasattr(self, "_presets_list"):
            return
        self._presets_list.clear()
        rows = _library_db.fetchall(
            "SELECT id, name FROM group_presets ORDER BY sort_order, created_at"
        )
        for r in rows:
            item = QListWidgetItem(f"📦  {r['name']}")
            item.setData(_ROLE_PRESET_ID, r["id"])
            self._presets_list.addItem(item)

    def _on_presets_context_menu(self, pos: QPoint) -> None:
        item = self._presets_list.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        menu.addAction(tr("tag_browser.menu_rename")).triggered.connect(
            lambda: self._rename_preset(item)
        )
        menu.addSeparator()
        menu.addAction(tr("tag_browser.menu_delete")).triggered.connect(
            lambda: self._delete_preset(item)
        )
        menu.exec(self._presets_list.viewport().mapToGlobal(pos))

    def _rename_preset(self, item: QListWidgetItem) -> None:
        preset_id = item.data(_ROLE_PRESET_ID)
        row = _library_db.fetchone("SELECT name, group_json FROM group_presets WHERE id=?", (preset_id,))
        current_name = row["name"] if row else ""
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(
            self, tr("tag_browser.rename_title"), tr("tag_browser.rename_label"),
            text=current_name,
        )
        if ok and name.strip():
            new_name = name.strip()
            group_json = row["group_json"] if row else ""
            if group_json:
                try:
                    import json as _json
                    group_data = _json.loads(group_json)
                    if isinstance(group_data, dict):
                        group_data["name"] = new_name
                        group_json = _json.dumps(group_data, ensure_ascii=False)
                except Exception:
                    pass
            _library_db.execute(
                "UPDATE group_presets SET name=?, group_json=? WHERE id=?",
                (new_name, group_json, preset_id),
            )
            self._load_presets()

    def _delete_preset(self, item: QListWidgetItem) -> None:
        preset_id = item.data(_ROLE_PRESET_ID)
        row = _library_db.fetchone("SELECT name FROM group_presets WHERE id=?", (preset_id,))
        name = row["name"] if row else item.text()
        if QMessageBox.question(
            self, tr("tag_browser.confirm_delete_title"),
            tr("tag_browser.preset_delete_confirm_msg", name=name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        _library_db.execute("DELETE FROM group_presets WHERE id=?", (preset_id,))
        self._load_presets()

    @classmethod
    def notify_presets_changed(cls) -> None:
        if cls._instance is not None:
            cls._instance._load_presets()

    # ── ノード管理 ────────────────────────────────────────────────────────────

    def _show_node_dialog(self, *, name_en="", name_local="",
                          genre="mixed_unsorted", is_nav=False,
                          parent_id=None, title=None,
                          name_en_readonly=False) -> None:
        dlg = _TagNodeDialog(
            self, name_en=name_en, name_local=name_local, genre=genre,
            is_nav=is_nav, parent_id=parent_id, title=title or tr("tag_browser.node_add_title"),
            name_en_readonly=name_en_readonly,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        v = dlg.result_values
        if not v["name_en"]:
            return
        try:
            self._remember_open_destination(v["genre"], v["parent_id"])
            _library_db.execute(
                """INSERT OR REPLACE INTO tags
                   (name_en, name_local, genre, category, parent_id,
                    is_nav_only, is_nsfw, popularity, emphasis_recommended)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 0, 1.0)""",
                (v["name_en"], v["name_local"], v["genre"], v["category"],
                 v["parent_id"], v["is_nav"], v.get("is_nsfw", 0)),
            )
            self._load_tags()
            _update_suggestions_for_library(get_active_library_name())
        except Exception as e:
            QMessageBox.critical(self, tr("tag_browser.error_title"), str(e))

    def _edit_node(self, item: QTreeWidgetItem) -> None:
        tag_id     = item.data(0, _ROLE_TAG_ID)
        name_en    = item.data(0, _ROLE_NAME_EN) or ""
        name_local = item.data(0, _ROLE_NAME_JA) or ""
        genre      = _tree_item_genre(item)
        is_nav     = bool(item.data(0, _ROLE_IS_NAV))

        row = _library_db.fetchone(
            "SELECT parent_id, COALESCE(is_nsfw, 0) AS is_nsfw FROM tags WHERE id=?",
            (tag_id,),
        )
        parent_id = row["parent_id"] if row else None
        is_nsfw   = bool(row["is_nsfw"]) if row else False

        dlg = _TagNodeDialog(
            self, name_en=name_en, name_local=name_local, genre=genre,
            is_nav=is_nav, parent_id=parent_id, is_nsfw=is_nsfw,
            title=tr("tag_browser.node_edit_title", label=name_local or name_en),
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        v = dlg.result_values
        if not v["name_en"]:
            return
        try:
            self._remember_open_destination(v["genre"], v["parent_id"])
            _library_db.execute(
                """UPDATE tags SET name_en=?, name_local=?, genre=?, category=?,
                   parent_id=?, is_nav_only=?, is_nsfw=?, updated_at=CURRENT_TIMESTAMP
                   WHERE id=?""",
                (v["name_en"], v["name_local"], v["genre"], v["category"],
                 v["parent_id"], v["is_nav"], v.get("is_nsfw", 0), tag_id),
            )
            self._load_tags()
            _update_suggestions_for_library(get_active_library_name())
            self.tag_updated.emit(
                name_en,
                v["name_en"],
                v["name_local"] or "",
                v["category"] or "",
            )
        except Exception as e:
            QMessageBox.critical(self, tr("tag_browser.error_title"), str(e))

    def _delete_node(self, item: QTreeWidgetItem) -> None:
        tag_id     = item.data(0, _ROLE_TAG_ID)
        name_local = item.data(0, _ROLE_NAME_JA) or item.data(0, _ROLE_NAME_EN) or ""
        is_nav     = bool(item.data(0, _ROLE_IS_NAV))
        n       = item.childCount()

        msg = tr("tag_browser.node_delete_confirm_msg", name=name_local)
        if n > 0:
            msg += tr("tag_browser.node_delete_promote_note", n=n)

        if QMessageBox.question(
            self, tr("tag_browser.node_delete_confirm_title"), msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return

        row = _library_db.fetchone(
            "SELECT parent_id FROM tags WHERE id=?",
            (tag_id,),
        )
        parent_id = row["parent_id"] if row else None

        _library_db.execute(
            "UPDATE tags SET parent_id=? WHERE parent_id=?",
            (parent_id, tag_id),
        )
        _library_db.execute("DELETE FROM tags WHERE id=?", (tag_id,))
        self._load_tags()
        self.tag_categories_changed.emit()
        _update_suggestions_for_library(get_active_library_name())

    # ── インポート / エクスポート（設定ダイアログから呼ばれるstaticメソッド） ──

    @staticmethod
    def _do_import(path: str, mode: str = "upsert") -> dict:
        """
        JSON または CSV からタグ・保存グループをインポートする。

        mode:
          "merge"  — 新規のみ追加（既存スキップ）
          "upsert" — 追加＋既存を上書き更新

        Returns dict:
          tags_inserted, tags_updated, tags_skipped,
          groups_inserted, groups_updated, groups_skipped
        """
        import json as _json

        result = dict(
            tags_inserted=0, tags_updated=0, tags_skipped=0,
            groups_inserted=0, groups_updated=0, groups_skipped=0,
        )
        ext = os.path.splitext(path)[1].lower()

        # ── JSON 形式（タグ + グループ） ──────────────────────────────────
        if ext == ".json":
            data = load_json_import_file(path)

            tag_rows   = data.get("tags",   [])
            group_rows = data.get("groups", [])
            if len(tag_rows) + len(group_rows) > MAX_IMPORT_RECORDS:
                raise ValueError(tr("import_security.too_many_records", count=MAX_IMPORT_RECORDS))

            # タグインポート
            rows_to_resolve: list[tuple[str, str]] = []
            rows_no_parent:  list[str]             = []

            for item in tag_rows:
                if not isinstance(item, dict):
                    result["tags_skipped"] += 1
                    continue
                name_en = sanitize_tag_name(item.get("name_en"), field_name="name_en")
                if not name_en:
                    result["tags_skipped"] += 1
                    continue

                # name_local / name_ja 両方を受け付ける（後方互換）
                name_local = sanitize_text(
                    item.get("name_local") or item.get("name_ja"),
                    max_len=200,
                ) or None
                genre      = sanitize_tag_name(item.get("genre") or "mixed_unsorted", field_name="genre")
                category   = (
                    sanitize_tag_name(item.get("category"), field_name="category")
                    or _GENRE_TO_CATEGORY.get(genre, "object")
                )
                subcat     = sanitize_text(item.get("subcategory"), max_len=200) or None
                parent_en  = sanitize_tag_name(item.get("parent_en"), field_name="parent_en")
                try:
                    is_nav = int(item.get("is_nav_only") or 0)
                except (ValueError, TypeError):
                    is_nav = 0
                try:
                    pop = int(item.get("popularity") or 0)
                except (ValueError, TypeError):
                    pop = 0
                try:
                    emph = float(item.get("emphasis_recommended") or 1.0)
                except (ValueError, TypeError):
                    emph = 1.0

                existing = _library_db.fetchone(
                    "SELECT id FROM tags WHERE name_en=?",
                    (name_en,),
                )
                if existing:
                    if mode == "merge":
                        result["tags_skipped"] += 1
                    else:
                        _library_db.execute(
                            """UPDATE tags SET
                                   name_local=?, genre=?, category=?, subcategory=?,
                                   is_nav_only=?, popularity=?, emphasis_recommended=?,
                                   updated_at=CURRENT_TIMESTAMP
                               WHERE name_en=?""",
                            (name_local, genre, category, subcat, is_nav, pop, emph, name_en),
                        )
                        result["tags_updated"] += 1
                else:
                    _library_db.execute(
                        """INSERT INTO tags
                               (name_en, name_local, genre, category, subcategory,
                                is_nav_only, popularity, emphasis_recommended)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (name_en, name_local, genre, category, subcat, is_nav, pop, emph),
                    )
                    result["tags_inserted"] += 1

                if parent_en:
                    rows_to_resolve.append((name_en, parent_en))
                else:
                    rows_no_parent.append(name_en)

            TagBrowser._resolve_parents(rows_to_resolve, rows_no_parent, mode)

            # グループインポート
            for gitem in group_rows:
                if not isinstance(gitem, dict):
                    result["groups_skipped"] += 1
                    continue
                name = sanitize_text(gitem.get("name"), max_len=200)
                if not name:
                    result["groups_skipped"] += 1
                    continue
                display_label = sanitize_text(
                    gitem.get("display_label"),
                    max_len=ONE_LINE_MEMO_MAX_CHARS,
                ) or None
                memo = sanitize_text(
                    gitem.get("memo"),
                    max_len=MEMO_MAX_CHARS,
                    allow_newline=True,
                ) or None
                try:
                    rating = int(gitem.get("rating") or 0)
                except (ValueError, TypeError):
                    rating = 0
                rating = rating if 1 <= rating <= 5 else None
                try:
                    is_nsfw = int(gitem.get("is_nsfw") or 0)
                except (ValueError, TypeError):
                    is_nsfw = 0
                is_nsfw = 1 if is_nsfw else 0

                # group_data (dict) または group_json (str) を受け付ける
                group_data = gitem.get("group_data")
                if group_data is not None:
                    try:
                        group_data = sanitize_text_json(group_data)
                        group_json = _json.dumps(group_data, ensure_ascii=False)
                    except Exception:
                        result["groups_skipped"] += 1
                        continue
                else:
                    group_json = (gitem.get("group_json") or "").strip()
                    if not group_json:
                        result["groups_skipped"] += 1
                        continue
                    try:
                        group_data = sanitize_text_json(_json.loads(group_json))
                        group_json = _json.dumps(group_data, ensure_ascii=False)
                    except Exception:
                        result["groups_skipped"] += 1
                        continue

                try:
                    sort_order = int(gitem.get("sort_order") or 0)
                except (ValueError, TypeError):
                    sort_order = 0
                from db.group_preset_db import unique_group_name
                name = unique_group_name(name)
                try:
                    group_data = _json.loads(group_json)
                    if isinstance(group_data, dict):
                        group_data["name"] = name
                        group_json = _json.dumps(group_data, ensure_ascii=False)
                except Exception:
                    pass
                _library_db.execute(
                    """INSERT INTO group_presets
                       (name, group_json, sort_order, display_label, memo, rating, is_nsfw)
                       VALUES (?,?,?,?,?,?,?)""",
                    (name, group_json, sort_order, display_label, memo, rating, is_nsfw),
                )
                result["groups_inserted"] += 1

        # ── CSV 形式（タグのみ、後方互換） ────────────────────────────────
        else:
            rows_to_resolve: list[tuple[str, str]] = []
            rows_no_parent:  list[str]             = []

            csv_text = read_text_import_file(path, allowed_suffixes=(".csv",))
            reader = csv.DictReader(io.StringIO(csv_text))
            for row_index, row in enumerate(reader, start=1):
                if row_index > MAX_IMPORT_RECORDS:
                    raise ValueError(tr("import_security.too_many_records", count=MAX_IMPORT_RECORDS))
                name_en = sanitize_tag_name(row.get("name_en"), field_name="name_en")
                if not name_en:
                    result["tags_skipped"] += 1
                    continue

                # name_local / name_ja 両方を受け付ける
                name_local = sanitize_text(
                    row.get("name_local") or row.get("name_ja"),
                    max_len=200,
                ) or None
                genre      = sanitize_tag_name(row.get("genre") or "mixed_unsorted", field_name="genre")
                category   = (
                    sanitize_tag_name(row.get("category"), field_name="category")
                    or _GENRE_TO_CATEGORY.get(genre, "object")
                )
                subcat     = sanitize_text(row.get("subcategory"), max_len=200) or None
                parent_en  = sanitize_tag_name(row.get("parent_en"), field_name="parent_en")
                try:
                    is_nav = int(row.get("is_nav_only") or 0)
                except ValueError:
                    is_nav = 0
                try:
                    pop = int(row.get("popularity") or 0)
                except ValueError:
                    pop = 0
                try:
                    emph = float(row.get("emphasis_recommended") or 1.0)
                except ValueError:
                    emph = 1.0

                existing = _library_db.fetchone(
                    "SELECT id FROM tags WHERE name_en=?",
                    (name_en,),
                )
                if existing:
                    if mode == "merge":
                        result["tags_skipped"] += 1
                    else:
                        _library_db.execute(
                            """UPDATE tags SET
                                   name_local=?, genre=?, category=?, subcategory=?,
                                   is_nav_only=?, popularity=?, emphasis_recommended=?,
                                   updated_at=CURRENT_TIMESTAMP
                               WHERE name_en=?""",
                            (name_local, genre, category, subcat, is_nav, pop, emph, name_en),
                        )
                        result["tags_updated"] += 1
                else:
                    _library_db.execute(
                        """INSERT INTO tags
                               (name_en, name_local, genre, category, subcategory,
                                is_nav_only, popularity, emphasis_recommended)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (name_en, name_local, genre, category, subcat, is_nav, pop, emph),
                    )
                    result["tags_inserted"] += 1

                if parent_en:
                    rows_to_resolve.append((name_en, parent_en))
                else:
                    rows_no_parent.append(name_en)

            TagBrowser._resolve_parents(rows_to_resolve, rows_no_parent, mode)

        return result

    @staticmethod
    def _resolve_parents(
        rows_to_resolve: list[tuple[str, str]],
        rows_no_parent:  list[str],
        mode: str,
    ) -> None:
        """parent_en → parent_id の解決。upsert モードでは NULL クリアも行う。"""
        for name_en, parent_en in rows_to_resolve:
            parent_row = _library_db.fetchone(
                "SELECT id FROM tags WHERE name_en=?",
                (parent_en,),
            )
            if parent_row:
                _library_db.execute(
                    "UPDATE tags SET parent_id=? WHERE name_en=?",
                    (parent_row["id"], name_en),
                )

        if mode == "upsert":
            for name_en in rows_no_parent:
                _library_db.execute(
                    "UPDATE tags SET parent_id=NULL WHERE name_en=?",
                    (name_en,),
                )

    # ── エクスポート ──────────────────────────────────────────────────────────

    @staticmethod
    def _do_export(path: str) -> tuple[int, int]:
        """タグと保存グループを JSON ファイルに書き出す。(tags件数, groups件数) を返す。"""
        import json as _json
        dictionary_key = _current_dictionary_key()

        tag_rows = _library_db.fetchall(
            """SELECT t.name_en, t.name_local,
                      COALESCE(t.genre,'mixed_unsorted') AS genre,
                      t.category, t.subcategory,
                      p.name_en AS parent_en,
                      COALESCE(t.is_nav_only,0) AS is_nav_only,
                      COALESCE(t.popularity,0) AS popularity,
                      COALESCE(t.emphasis_recommended,1.0) AS emphasis_recommended
               FROM tags t
               LEFT JOIN tags p ON t.parent_id = p.id
               ORDER BY COALESCE(t.is_nav_only,0) DESC,
                        COALESCE(t.parent_id, t.id), t.popularity DESC, t.name_en"""
        )

        group_rows = _library_db.fetchall(
            """SELECT name, sort_order, group_json, display_label, memo,
                      COALESCE(rating, 0) AS rating, COALESCE(is_nsfw, 0) AS is_nsfw
               FROM group_presets ORDER BY sort_order, created_at"""
        )

        tags_out = []
        for r in tag_rows:
            tags_out.append({
                "name_en":              r["name_en"] or "",
                "name_local":           r["name_local"] or "",
                "genre":                r["genre"] or "mixed_unsorted",
                "category":             r["category"] or "",
                "subcategory":          r["subcategory"] or "",
                "parent_en":            r["parent_en"] or "",
                "is_nav_only":          int(r["is_nav_only"]),
                "popularity":           int(r["popularity"]),
                "emphasis_recommended": float(r["emphasis_recommended"]),
            })

        groups_out = []
        for g in group_rows:
            try:
                group_data = _json.loads(g["group_json"] or "{}")
            except Exception:
                group_data = {}
            groups_out.append({
                "name":          g["name"],
                "sort_order":    int(g["sort_order"] or 0),
                "display_label": g["display_label"] or "",
                "memo":          g["memo"] or "",
                "rating":        int(g["rating"] or 0),
                "is_nsfw":       int(g["is_nsfw"] or 0),
                "group_data":    group_data,
            })

        payload = {
            "version": 1,
            "dictionary_key": dictionary_key,
            "tags":    tags_out,
            "groups":  groups_out,
        }

        with open(path, "w", encoding="utf-8") as f:
            _json.dump(payload, f, ensure_ascii=False, indent=2)

        return len(tags_out), len(groups_out)

    # ── ユーティリティ ────────────────────────────────────────────────────────

    @staticmethod
    def _ui_language() -> str:
        row = _app_db.fetchone("SELECT value FROM app_settings WHERE key = 'language'")
        return row["value"] if row else "ja"

    def _tag_header_text(self) -> str:
        prefix = "🏷  " if self._show_header_icon else ""
        return prefix + tr("tag_browser.title")

    def reload(self) -> None:
        """外部からの再ロード要求（設定変更後など）"""
        self.retranslate_and_restyle()
        self._load_tags()
        self._load_presets()

    def retranslate_and_restyle(self) -> None:
        self._hdr_label.setText(self._tag_header_text())
        self._dict_prev_btn.setToolTip(tr("tag_browser.dictionary_prev_tooltip"))
        self._dict_next_btn.setToolTip(tr("tag_browser.dictionary_next_tooltip"))
        self._search.setPlaceholderText(tr("tag_browser.search_placeholder"))
        if hasattr(self, "_presets_title_lbl"):
            self._presets_title_lbl.setText(tr("tag_browser.saved_groups_title"))
        if hasattr(self, "_drop_hint_lbl"):
            self._drop_hint_lbl.setText(tr("tag_browser.saved_groups_drop_hint"))
        self._nsfw_btn.setToolTip(tr("tag_browser.nsfw_tooltip", state=tr(
            "tag_browser.nsfw_showing" if self._show_nsfw else "tag_browser.nsfw_hidden"
        )))
