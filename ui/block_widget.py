"""
ブロックウィジェット（先頭 / 中間 / 末尾の1ブロック分）

・タイルをFlowLayoutで表示
・テキスト入力からTagTile / NaturalTextTileを追加
・シャッフルON/OFFトグル
・タイルのドラッグ & ドロップ（ブロック内並べ替え / ブロック間移動）
"""
from __future__ import annotations

import random

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QCheckBox, QPushButton, QLineEdit, QToolButton,
    QFrame, QSizePolicy, QApplication, QTextEdit,
    QPlainTextEdit, QMessageBox, QToolTip,
    QMenu, QWidgetAction, QDialog, QDialogButtonBox,
    QCompleter,
)
from PySide6.QtCore import Signal, Qt, QPoint, QEvent, QObject, QTimer, QStringListModel
from PySide6.QtGui import QFont, QDragEnterEvent, QDragMoveEvent, QDragLeaveEvent, QDropEvent, QTextCursor, QAction, QCursor, QTextOption

from core.prompt_builder import Block, TagTile, NaturalTextTile, GroupTile
from core.i18n import tr
from core.text_sanitize import single_line_text
import db.app_db as _app_db
import db.library_db as _library_db
from ui.flow_layout import FlowLayout
from ui.tile_widget import TileWidget, TILE_MIME
from ui.styles import (
    BLOCK_HEADER_COLORS,
    SURFACE0, SURFACE1, SURFACE2, TEXT, SUBTEXT, ACCENT, GREEN, RED, ui_font, themed_button_style,
    is_light_theme, EMOJI_ICON_SS,
)

_PROMPT_TEXT_MIME = "application/x-prompt-text-id"

_MAX_GROUP_NAME = 24  # グループ名に使う最大文字数


def _tile_short_name(tile) -> str:
    """タイルの短い表示名を返す（D&D グループ自動命名用）。"""
    if isinstance(tile, TagTile):
        name = tile.tag_local or tile.tag_name
    elif isinstance(tile, NaturalTextTile):
        # 1. 現地語（原文）, 2. Invoke送信値（訳文）, 3. text
        name = (tile.source_text or tile.text or tile.translated_text or "").strip()
    elif isinstance(tile, GroupTile):
        return tile.name
    else:
        name = str(getattr(tile, "tag_name", "") or getattr(tile, "text", ""))
    name = name.strip()
    if len(name) > _MAX_GROUP_NAME:
        name = name[:_MAX_GROUP_NAME].rstrip() + "…"
    return name or ""


class _AutoExpandTextEdit(QPlainTextEdit):
    """
    入力内容に応じて縦方向に自動拡張する QPlainTextEdit。

    空欄/1行時は1行高に固定し、入力が折り返した時だけ伸ばす。
    """

    returnPressed = Signal()

    _ONE_LINE_HEIGHT = 28
    _MAX_HEIGHT = 120

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tag_mode = True
        self._suggest_model = QStringListModel(self)
        self._suggest_completer = QCompleter(self._suggest_model, self)
        self._suggest_completer.setWidget(self)
        self._suggest_completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self._suggest_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._suggest_completer.activated.connect(self._insert_completion)
        self._suppress_next_completion = False
        self.setAcceptDrops(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(self._ONE_LINE_HEIGHT)
        self.setContentsMargins(0, 0, 0, 0)
        self.document().setDocumentMargin(1)
        self.setViewportMargins(0, 0, 0, 0)
        self.setStyleSheet(
            f"QPlainTextEdit {{ background: {SURFACE0}; color: {TEXT}; "
            f"border: 2px solid {ACCENT}; border-radius: 4px; "
            f"padding: 0px 6px; selection-background-color: {ACCENT}; }}"
            f"QPlainTextEdit:focus {{ background: {SURFACE0}; border-color: {GREEN}; }}"
        )
        self.document().contentsChanged.connect(self._adjust_height)
        self.textChanged.connect(lambda: QTimer.singleShot(0, self._refresh_completion))
        QTimer.singleShot(0, self._adjust_height)

    def set_tag_mode(self, tag_mode: bool) -> None:
        self._tag_mode = tag_mode
        if not self._tag_mode:
            self._suggest_completer.popup().hide()
        QTimer.singleShot(0, self._adjust_height)

    @staticmethod
    def _suggestions_enabled() -> bool:
        row = _app_db.fetchone("SELECT value FROM app_settings WHERE key='tag_input_suggestions_enabled'")
        return row is None or row["value"] != "0"

    @staticmethod
    def _tag_suggestions(prefix: str) -> list[str]:
        prefix = single_line_text(prefix).strip()
        if not prefix:
            return []
        like = f"{prefix}%"
        nsfw_row = _app_db.fetchone("SELECT value FROM app_settings WHERE key='show_nsfw'")
        show_nsfw = nsfw_row is not None and nsfw_row["value"] == "1"
        rows = _library_db.fetchall(
            """
            SELECT name_en, COALESCE(name_local, '') AS name_local
              FROM tags
             WHERE COALESCE(is_nav_only, 0) = 0
               AND (? OR COALESCE(is_nsfw, 0) = 0)
               AND (name_en LIKE ? OR COALESCE(name_local, '') LIKE ?)
             ORDER BY
               CASE
                 WHEN COALESCE(name_local, '') LIKE ? THEN 0
                 WHEN name_en LIKE ? THEN 1
                 ELSE 2
               END,
               COALESCE(NULLIF(name_local, ''), name_en) COLLATE NOCASE
             LIMIT 80
            """,
            (show_nsfw, like, like, like, like),
        )
        seen: set[str] = set()
        suggestions: list[str] = []
        low_prefix = prefix.lower()
        for row in rows:
            local = single_line_text(row["name_local"])
            english = single_line_text(row["name_en"])
            for candidate in (local, english):
                if not candidate or candidate in seen:
                    continue
                if not candidate.lower().startswith(low_prefix):
                    continue
                seen.add(candidate)
                suggestions.append(candidate)
        return suggestions

    def _current_completion_prefix(self) -> tuple[str, int]:
        cursor = self.textCursor()
        text = self.toPlainText()
        pos = cursor.position()
        before = text[:pos]
        start = max(before.rfind(","), before.rfind("\n")) + 1
        while start < pos and text[start].isspace():
            start += 1
        return text[start:pos], start

    def _refresh_completion(self) -> None:
        if self._suppress_next_completion:
            self._suppress_next_completion = False
            self._suggest_completer.popup().hide()
            return
        if not self._tag_mode or not self._suggestions_enabled():
            self._suggest_completer.popup().hide()
            return
        prefix, _start = self._current_completion_prefix()
        prefix = prefix.strip()
        if not prefix or prefix.startswith(("(", ")", "\"")):
            self._suggest_completer.popup().hide()
            return
        suggestions = self._tag_suggestions(prefix)
        if not suggestions:
            self._suggest_completer.popup().hide()
            return
        if len(suggestions) == 1 and suggestions[0].casefold() == prefix.casefold():
            self._suggest_completer.popup().hide()
            return
        self._suggest_model.setStringList(suggestions)
        self._suggest_completer.setCompletionPrefix(prefix)
        rect = self.cursorRect()
        rect.setWidth(max(220, self._suggest_completer.popup().sizeHintForColumn(0) + 24))
        self._suggest_completer.complete(rect)

    def _insert_completion(self, completion: str) -> None:
        completion = single_line_text(completion)
        if not completion:
            return
        cursor = self.textCursor()
        _prefix, start = self._current_completion_prefix()
        cursor.setPosition(start)
        cursor.setPosition(self.textCursor().position(), QTextCursor.MoveMode.KeepAnchor)
        cursor.insertText(completion)
        self.setTextCursor(cursor)
        self._suppress_next_completion = True
        self._suggest_completer.popup().hide()

    def _visual_line_count(self) -> int:
        text = self.toPlainText()
        if not text:
            return 1
        fm = self.fontMetrics()
        wrap_w = max(1, self.viewport().width() - 12)
        visual_lines = 0
        for paragraph in text.split("\n"):
            if not paragraph:
                visual_lines += 1
                continue
            current_w = 0
            visual_lines += 1
            for ch in paragraph:
                ch_w = max(1, fm.horizontalAdvance(ch))
                if current_w > 0 and current_w + ch_w > wrap_w:
                    visual_lines += 1
                    current_w = ch_w
                else:
                    current_w += ch_w
        return max(1, visual_lines)

    def _adjust_height(self) -> None:
        line_count = self._visual_line_count()
        if line_count <= 1:
            target_h = self._ONE_LINE_HEIGHT
        else:
            line_h = max(1, self.fontMetrics().lineSpacing())
            target_h = min(self._MAX_HEIGHT, self._ONE_LINE_HEIGHT + (line_count - 1) * line_h)
        if self.height() != target_h:
            self.setFixedHeight(target_h)
            self.updateGeometry()
            if self.parentWidget():
                self.parentWidget().updateGeometry()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        QTimer.singleShot(0, self._adjust_height)

    def inputMethodEvent(self, event) -> None:
        super().inputMethodEvent(event)
        QTimer.singleShot(0, self._refresh_completion)

    def keyPressEvent(self, event) -> None:
        if self._suggest_completer.popup().isVisible() and event.key() in (
            Qt.Key.Key_Enter,
            Qt.Key.Key_Return,
            Qt.Key.Key_Tab,
            Qt.Key.Key_Backtab,
            Qt.Key.Key_Escape,
        ):
            event.ignore()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            ctrl = event.modifiers() & Qt.KeyboardModifier.ControlModifier
            if self._tag_mode or ctrl:
                self.returnPressed.emit()
                event.accept()
                return
        super().keyPressEvent(event)
        QTimer.singleShot(0, self._refresh_completion)

    @staticmethod
    def _tile_local_text(tile) -> str:
        """タイルをタグ入力欄へ戻すための現地語テキストを返す。"""
        if isinstance(tile, TagTile):
            return single_line_text(tile.tag_local or tile.source_text or tile.tag_name)
        if isinstance(tile, NaturalTextTile):
            return single_line_text(tile.source_text or tile.display_label or tile.text or tile.translated_text)
        if isinstance(tile, GroupTile):
            parts = []
            for child in tile.tiles:
                text = _AutoExpandTextEdit._tile_local_text(child)
                if text:
                    parts.append(text)
            return ", ".join(parts)
        return single_line_text(
            getattr(tile, "tag_local", "")
            or getattr(tile, "source_text", "")
            or getattr(tile, "tag_name", "")
            or getattr(tile, "text", "")
        )

    @staticmethod
    def _dragged_tile_local_text() -> str:
        try:
            import ui.tile_drag as tile_drag
            src = tile_drag.get_drag()
        except Exception:
            return ""
        tile = getattr(src, "tile", None)
        return _AutoExpandTextEdit._tile_local_text(tile) if tile is not None else ""

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasFormat(TILE_MIME) and self._dragged_tile_local_text():
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if event.mimeData().hasFormat(TILE_MIME) and self._dragged_tile_local_text():
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        if event.mimeData().hasFormat(TILE_MIME):
            text = self._dragged_tile_local_text()
            if text:
                self.setPlainText(text)
                cursor = self.textCursor()
                cursor.movePosition(QTextCursor.MoveOperation.End)
                self.setTextCursor(cursor)
                event.setDropAction(Qt.DropAction.MoveAction)
                event.accept()
                return
        super().dropEvent(event)


class _BlockInputEditorDialog(QDialog):
    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("block.input_editor_title"))
        self.resize(560, 360)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        self._edit = QPlainTextEdit()
        self._edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self._edit.document().setDocumentMargin(6)
        self._edit.setViewportMargins(0, 0, 0, 0)
        self._edit.setPlainText(text or "")
        self._edit.setStyleSheet(
            f"QPlainTextEdit {{ background: {SURFACE0}; color: {TEXT}; "
            f"border: 1px solid {SURFACE2}; border-radius: 4px; padding: 0px; "
            f"selection-background-color: {ACCENT}; }}"
            f"QPlainTextEdit:focus {{ border-color: {GREEN}; }}"
        )
        root.addWidget(self._edit, stretch=1)

        buttons = QDialogButtonBox()
        self._input_btn = buttons.addButton(
            tr("block.input_editor_apply"),
            QDialogButtonBox.ButtonRole.AcceptRole,
        )
        self._input_btn.setStyleSheet(themed_button_style("add"))
        buttons.accepted.connect(self.accept)
        root.addWidget(buttons)

        self._edit.setFocus()
        cursor = self._edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self._edit.setTextCursor(cursor)

    def text(self) -> str:
        return self._edit.toPlainText()


class BlockWidget(QFrame):
    """
    1ブロックを表示・編集するウィジェット。

    Signals:
        block_changed(): タイルの追加/削除/変更/並べ替えが起きたとき
    """

    block_changed             = Signal()
    block_focused             = Signal()
    translate_requested       = Signal(str, str)  # (text, mode) mode="danboard"|"natural"
    translate_cancel_requested = Signal()          # インラインパネルのキャンセルボタン
    bulk_translate_requested  = Signal()           # 中央ペインの未翻訳タイル一括翻訳
    auto_classify_requested   = Signal()           # 中央ペイン全体の未登録プロンプト/タイルをLLM分類登録
    layout_changed            = Signal()           # データ変更なしの高さ変化（グループ展開/折りたたみ）
    browser_tag_dropped       = Signal(object)     # TagBrowser 由来で追加された TagTile

    def __init__(self, block: Block, parent=None, *, readonly: bool = False):
        super().__init__(parent)
        self.block = block
        self._readonly = bool(readonly)
        self._tile_widgets: list[QWidget] = []   # TileWidget | GroupWidget
        self._input_is_natural = False   # 入力モード: False=タグ / True=自然文
        self._drop_index: int = -1       # ドロップ中の挿入位置（-1 = 非ドラッグ中）
        self._locked = False             # 編集ロック状態
        self._collapsed = False          # 折りたたみ状態
        self._active_translate_btn = None            # 翻訳中のボタン参照
        self._translated_text: str | None = None     # 翻訳結果テキスト
        self._translate_mode: str = "danboard"       # "danboard" | "natural"
        self._is_translating: bool = False           # 翻訳中は入力・追加をロック
        self._source_at_translate: str | None = None # 翻訳時点の原文（ダイアログ用）
        self._edit_warned: bool = False              # 確認ダイアログ表示済みフラグ
        self._user_editing: bool = False             # プログラム更新中フラグ（True=ユーザー入力）
        self._from_input: bool = False               # 入力欄からの追加時のみ True（スクロールアンカー用）
        self._source_edit_warning_active: bool = False # textChanged 再入防止
        self._bulk_translate_available_callback = None
        self._build_ui()
        self._refresh_tiles()
        self._load_collapsed_state()
        self._apply_readonly_state()

    # ── UI構築 ──────────────────────────────────────────

    def _build_ui(self) -> None:
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            f"BlockWidget {{ background-color: {SURFACE0}; border: 1px solid {SURFACE2}; border-radius: 6px; }}"
        )
        # 縦方向は Expanding: ビューポートの余白を受け取ってタイルエリアに渡す
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        # ドロップ受付を有効化
        self.setAcceptDrops(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── ヘッダー ────────────────────────────────────
        hdr_color = BLOCK_HEADER_COLORS.get(self.block.position, SURFACE1)
        header = QWidget()
        self._header = header
        header.setStyleSheet(
            f"background-color: {hdr_color}; border-bottom: 1px solid {SURFACE2};"
            f"border-radius: 6px 6px 0 0;"
        )
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(self._show_block_context_menu)
        hdr_lay = QHBoxLayout(header)
        hdr_lay.setContentsMargins(8, 4, 8, 4)
        hdr_lay.setSpacing(6)

        # 折りたたみボタン（先頭）
        self._collapse_btn = QToolButton()
        self._collapse_btn.setText("▼")
        self._collapse_btn.setFixedSize(20, 20)
        self._collapse_btn.setToolTip(tr("block.collapse_tooltip"))
        self._collapse_btn.clicked.connect(self._toggle_collapse)
        self._collapse_btn.setStyleSheet(
            "QToolButton { background: transparent; border: none; color: inherit; "
            + EMOJI_ICON_SS + " }"
        )
        hdr_lay.addWidget(self._collapse_btn)

        _label_key = (
            "block.label.negative"
            if self.block.block_type == "negative"
            else f"block.label.{self.block.position}"
        )
        display_label = self.block.label or tr(_label_key)
        self._title_label = QLabel(display_label)
        self._title_label.setFont(ui_font(bold=True))
        self._title_label.setStyleSheet(f"color: {TEXT}; background: transparent; border: none;")
        hdr_lay.addWidget(self._title_label)

        hdr_lay.addStretch()

        self._shuffle_cb = QCheckBox(tr("block.shuffle"))
        self._shuffle_cb.setChecked(self.block.randomize)
        self._shuffle_cb.setToolTip(tr("block.shuffle_tooltip"))
        self._shuffle_cb.stateChanged.connect(self._on_shuffle_changed)
        hdr_lay.addWidget(self._shuffle_cb)

        self._lock_btn = QToolButton()
        self._lock_btn.setFixedSize(28, 20)
        self._lock_btn.setStyleSheet(
            f"QToolButton {{ background: transparent; color: {TEXT}; "
            f"border: 1px solid {SURFACE2}; border-radius: 3px; "
            f"padding: 0px; font-size: 13pt; }}"
            f"QToolButton:hover {{ background: {SURFACE2}; border-color: {TEXT}; }}"
        )
        self._lock_btn.clicked.connect(self._toggle_lock)
        hdr_lay.addWidget(self._lock_btn)
        self._apply_lock_state()

        root.addWidget(header)

        # ── タイルエリア ─────────────────────────────────
        self._tiles_container = QWidget()
        self._tiles_container.setStyleSheet(
            f"background-color: {SURFACE0}; border: none;"
        )
        self._tiles_container.setMinimumHeight(36)
        self._tiles_container.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._flow = FlowLayout(h_spacing=4, v_spacing=4)
        self._flow.setContentsMargins(4, 4, 4, 4)
        self._tiles_container.setLayout(self._flow)
        self._tiles_container.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tiles_container.customContextMenuRequested.connect(self._show_block_context_menu)
        root.addWidget(self._tiles_container)

        # ── ドロップ挿入インジケーター（tiles_container の子）──
        self._drop_indicator = QFrame(self._tiles_container)
        self._drop_indicator.setFixedWidth(3)
        self._drop_indicator.setStyleSheet(
            f"background-color: {ACCENT}; border-radius: 1px; border: none;"
        )
        self._drop_indicator.hide()

        # ── ロックオーバーレイ（ロック中にブロック本体全体を不活性化）──
        # BlockWidget 自身の子にすることで、内部の GroupWidget など全子ウィジェットより
        # 確実に前面に配置でき、Z-オーダー競合を避ける。
        self._lock_overlay = QWidget(self)
        self._lock_overlay.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._lock_overlay.setStyleSheet("background-color: rgba(20, 20, 30, 100);")
        self._lock_overlay.hide()

        # ── 入力行 ──────────────────────────────────────
        input_bar = QWidget()
        input_bar.setStyleSheet(
            f"background-color: {SURFACE1}; border-top: 1px solid {SURFACE2};"
            f"border-radius: 0 0 6px 6px;"
        )
        input_lay = QVBoxLayout(input_bar)
        input_lay.setContentsMargins(6, 4, 6, 4)
        input_lay.setSpacing(4)

        input_row = QHBoxLayout()
        input_row.setContentsMargins(0, 0, 0, 0)
        input_row.setSpacing(4)

        self._input_editor_btn = QToolButton()
        self._input_editor_btn.setText("✎")
        self._input_editor_btn.setFixedSize(28, 28)
        self._input_editor_btn.setToolTip(tr("block.input_editor_tooltip"))
        self._input_editor_btn.setStyleSheet(
            f"QToolButton {{ background: {SURFACE0}; color: {ACCENT}; "
            f"border: 1px solid {ACCENT}; border-radius: 4px; padding: 0; "
            f"{EMOJI_ICON_SS} }}"
            f"QToolButton:hover {{ background: {SURFACE2}; }}"
            f"QToolButton:disabled {{ color: #585b70; border-color: #313244; }}"
        )
        self._input_editor_btn.clicked.connect(self._open_input_editor)
        input_row.addWidget(self._input_editor_btn, alignment=Qt.AlignmentFlag.AlignTop)

        self._input = _AutoExpandTextEdit()
        self._input.setPlaceholderText(tr("block.input_placeholder_tag"))
        self._input.returnPressed.connect(self._add_word_tile_from_input)
        self._input.textChanged.connect(lambda _text="": self._on_input_text_changed())
        # フォーカスを取得したら block_focused を emit する
        self._input.installEventFilter(self)
        input_row.addWidget(self._input, stretch=1)
        input_lay.addLayout(input_row)

        input_btn_lay = QHBoxLayout()
        input_btn_lay.setContentsMargins(0, 0, 0, 0)
        input_btn_lay.setSpacing(4)

        input_btn_lay.addStretch()

        self._translate_danboard_btn = QPushButton(tr("block.word_translate_btn"))
        self._translate_danboard_btn.setMinimumWidth(72)
        self._translate_danboard_btn.setToolTip(tr("block.translate_danboard_tooltip"))
        self._translate_danboard_btn.setStyleSheet(themed_button_style("translate"))
        self._translate_danboard_btn.clicked.connect(self._on_translate_danboard_click)
        input_btn_lay.addWidget(self._translate_danboard_btn)

        self._add_btn = QPushButton(tr("block.word_add_btn"))
        self._add_btn.setMinimumWidth(64)
        self._add_btn.setStyleSheet(themed_button_style("add"))
        self._add_btn.clicked.connect(self._add_word_tile_from_input)
        input_btn_lay.addWidget(self._add_btn)

        self._translate_natural_btn = QPushButton(tr("block.natural_translate_btn"))
        self._translate_natural_btn.setMinimumWidth(72)
        self._translate_natural_btn.setToolTip(tr("block.translate_natural_tooltip"))
        self._translate_natural_btn.setStyleSheet(themed_button_style("translate"))
        self._translate_natural_btn.clicked.connect(self._on_translate_natural_click)
        input_btn_lay.addWidget(self._translate_natural_btn)

        self._natural_add_btn = QPushButton(tr("block.natural_add_btn"))
        self._natural_add_btn.setMinimumWidth(64)
        self._natural_add_btn.setToolTip(tr("block.natural_add_tooltip"))
        self._natural_add_btn.setStyleSheet(themed_button_style("add"))
        self._natural_add_btn.clicked.connect(self._add_natural_tile_from_input)
        input_btn_lay.addWidget(self._natural_add_btn)
        input_lay.addLayout(input_btn_lay)

        self._input_bar = input_bar
        root.addWidget(input_bar)

        # ── 翻訳結果エリア（翻訳完了後に表示）────────────
        self._translate_result_area = QPlainTextEdit()
        self._translate_result_area.setReadOnly(True)
        self._translate_result_area.setPlaceholderText(tr("block.translate_result_label"))
        self._translate_result_area.setFixedHeight(60)
        self._translate_result_area.setFont(ui_font(-1))
        self._translate_result_area.setStyleSheet(
            f"QPlainTextEdit {{ background: {SURFACE0}; color: {TEXT}; "
            f"border: 1px solid {ACCENT}; border-radius: 3px; padding: 2px; }}"
        )
        self._translate_result_area.hide()
        root.addWidget(self._translate_result_area)

        # ── 翻訳インラインパネル（翻訳中のみ表示）────────
        self._translate_panel = QWidget()
        self._translate_panel.setStyleSheet(
            f"background-color: {SURFACE1}; border-top: 1px solid {SURFACE2};"
        )
        tp_lay = QVBoxLayout(self._translate_panel)
        tp_lay.setContentsMargins(6, 4, 6, 4)
        tp_lay.setSpacing(3)

        tp_top = QHBoxLayout()
        tp_top.setSpacing(4)
        self._translate_status_lbl = QLabel(tr("translate_panel.status_translating"))
        self._translate_status_lbl.setFont(ui_font(-1))
        self._translate_status_lbl.setStyleSheet(f"color: {SUBTEXT}; background: transparent;")
        tp_top.addWidget(self._translate_status_lbl, stretch=1)
        _tp_cancel = QPushButton(tr("translate_panel.cancel_btn"))
        _tp_cancel.setFont(ui_font(-1))
        _tp_cancel.setFixedHeight(20)
        _tp_cancel.setStyleSheet(
            f"QPushButton {{ background: {SURFACE1}; color: {SUBTEXT}; "
            f"border: 1px solid {SURFACE2}; border-radius: 3px; padding: 0 8px; }}"
            f"QPushButton:hover {{ color: {RED}; border-color: {RED}; }}"
        )
        _tp_cancel.clicked.connect(self.translate_cancel_requested)
        tp_top.addWidget(_tp_cancel)
        tp_lay.addLayout(tp_top)

        self._translate_thinking_edit = QTextEdit()
        self._translate_thinking_edit.setReadOnly(True)
        self._translate_thinking_edit.setFixedHeight(96)
        self._translate_thinking_edit.setFont(ui_font(-2))
        self._translate_thinking_edit.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self._translate_thinking_edit.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self._translate_thinking_edit.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._translate_thinking_edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._translate_thinking_edit.setAcceptRichText(False)
        self._translate_thinking_edit.setStyleSheet(
            f"QTextEdit {{ background: {SURFACE0}; color: {SUBTEXT}; "
            f"border: 1px solid {SURFACE2}; border-radius: 3px; padding: 2px; }}"
        )
        tp_lay.addWidget(self._translate_thinking_edit)

        self._translate_panel.hide()
        root.addWidget(self._translate_panel)
        self._update_input_button_states()

    # ── タイル表示 ──────────────────────────────────────

    def _refresh_tiles(self) -> None:
        """block.tilesの内容でタイルウィジェットを再構築する"""
        while self._flow.count():
            item = self._flow.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        self._tile_widgets.clear()

        for tile in self.block.tiles:
            w = self._make_tile_widget(tile)
            self._flow.addWidget(w)
            self._tile_widgets.append(w)

        self._flow.invalidate()
        self._update_tile_container_height()
        self.updateGeometry()

    # ── 高さ管理 ────────────────────────────────────────

    def _update_tile_container_height(self) -> None:
        """タイル数に合わせたタイルエリアの最低高さを設定する"""
        w = self._tiles_container.width()
        if w <= 0:
            w = self.width()
        if w <= 0:
            return
        h = max(36, self._flow.heightForWidth(w))
        self._tiles_container.setMinimumHeight(h)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_tile_container_height()
        self.updateGeometry()
        if hasattr(self, "_lock_overlay") and self._locked:
            self._update_lock_overlay_geometry()

    # ── ドラッグ & ドロップ ──────────────────────────────

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self._readonly:
            event.ignore()
            return
        if self._locked:
            event.ignore()
            return
        if self._has_supported_image_url(event):
            event.acceptProposedAction()
            return
        from ui.tag_drag import BROWSER_MIME, GROUP_BROWSER_MIME
        if event.mimeData().hasFormat(TILE_MIME):
            import ui.tile_drag as tile_drag
            if tile_drag.get_drag() is not None:
                event.acceptProposedAction()
                return
        if event.mimeData().hasFormat(BROWSER_MIME):
            import ui.tag_drag as tag_drag
            if self._can_accept_browser_tag(tag_drag.get_drag()):
                event.acceptProposedAction()
                return
            event.ignore()
            return
        if event.mimeData().hasFormat(GROUP_BROWSER_MIME):
            event.acceptProposedAction()
            return
        if event.mimeData().hasFormat(_PROMPT_TEXT_MIME):
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if self._readonly:
            event.ignore()
            return
        if self._has_supported_image_url(event):
            event.acceptProposedAction()
            return
        from ui.tag_drag import BROWSER_MIME, GROUP_BROWSER_MIME
        import ui.tile_drag as tile_drag
        if event.mimeData().hasFormat(TILE_MIME):
            pos_in_ct = self._tiles_container.mapFrom(self, event.position().toPoint())
            src = tile_drag.get_drag()
            shift_down = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            # ドロップ先がタイルのラベル文字の上 → グループ化モード（インジケーター非表示）
            target_tw = self._find_tile_widget_at(pos_in_ct)
            if target_tw is not None and src is not None and target_tw is not src:
                pos_in_tile = target_tw.mapFrom(self._tiles_container, pos_in_ct)
                group_to_group = (
                    isinstance(getattr(src, "tile", None), GroupTile)
                    and isinstance(getattr(target_tw, "tile", None), GroupTile)
                )
                if shift_down and (group_to_group or self._is_drop_on_label(target_tw, pos_in_tile)):
                    self._drop_indicator.hide()
                    event.acceptProposedAction()
                    return
            idx = self._find_drop_index(pos_in_ct)
            self._drop_index = idx
            self._show_drop_indicator(idx)
            event.acceptProposedAction()
        elif event.mimeData().hasFormat(BROWSER_MIME):
            import ui.tag_drag as tag_drag
            if self._can_accept_browser_tag(tag_drag.get_drag()):
                event.acceptProposedAction()
            else:
                event.ignore()
        elif event.mimeData().hasFormat(GROUP_BROWSER_MIME):
            event.acceptProposedAction()
        elif event.mimeData().hasFormat(_PROMPT_TEXT_MIME):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:
        self._drop_index = -1
        self._drop_indicator.hide()

    # ── 中央ペインへのタイル追加 → 同名タグ発光（一般化フック） ──────────────
    #
    # 「タグブラウザから D&D したとき」だけでなく、中央ペインにタイルが
    # 増えたあらゆる経路（ブラウザ／保存グループ／文章プロンプト／他ブロック
    # からのタイル移動、そして将来増えるドロップ元）を 1 か所でカバーするため、
    # ドロップ処理の前後でタグタイルの集合を差分し、新たに増えた TagTile に
    # ついてのみ browser_tag_dropped を発火する。受け手（PromptEditor）が
    # 全ブロックの同名タグを play_duplicate_hint() で光らせる。

    def _iter_all_tag_tiles(self) -> "list[TagTile]":
        """このブロック配下（グループ内も含む）の全 TagTile を返す。"""
        out: list[TagTile] = []

        def rec(tiles):
            for t in tiles:
                if isinstance(t, TagTile):
                    out.append(t)
                elif isinstance(t, GroupTile):
                    rec(t.tiles)

        rec(self.block.tiles)
        return out

    def _snapshot_tag_tile_ids(self) -> set:
        """現在の TagTile 実体の id 集合（差分検出用スナップショット）。"""
        return {id(t) for t in self._iter_all_tag_tiles()}

    def _emit_glow_for_new_tags(self, before_ids: set) -> None:
        """before_ids 以降に増えた TagTile について発光通知シグナルを送る。"""
        for tile in self._iter_all_tag_tiles():
            if id(tile) not in before_ids:
                self.browser_tag_dropped.emit(tile)

    def dropEvent(self, event: QDropEvent) -> None:
        """中央ペインへのドロップ共通入口。

        ドロップ元の種類に依存せず、「ドロップ後に増えたタグタイル」を
        検出して同名タグの発光を通知する。将来ドロップ元が増えても、
        個別の手当なしにこの 1 か所で自動的にカバーされる。
        """
        if self._handle_supported_image_drop(event):
            return
        before = self._snapshot_tag_tile_ids()
        self._do_drop(event)
        self._emit_glow_for_new_tags(before)

    @staticmethod
    def _has_supported_image_url(event) -> bool:
        mime = event.mimeData()
        if not mime.hasUrls():
            return False
        return any(
            url.isLocalFile() and url.toLocalFile().lower().endswith((".png", ".webp"))
            for url in mime.urls()
        )

    def _handle_supported_image_drop(self, event: QDropEvent) -> bool:
        mime = event.mimeData()
        if not mime.hasUrls():
            return False

        for url in mime.urls():
            if not url.isLocalFile():
                continue
            path = url.toLocalFile()
            if not path.lower().endswith((".png", ".webp")):
                continue
            event.acceptProposedAction()

            window = self.window()
            handler = getattr(window, "_on_png_dropped", None)
            if callable(handler):
                QTimer.singleShot(0, lambda p=path: handler(p))
            return True

        return False

    def _do_drop(self, event: QDropEvent) -> None:
        if self._readonly:
            event.ignore()
            return
        from ui.tag_drag import BROWSER_MIME, GROUP_BROWSER_MIME
        import ui.tile_drag as tile_drag

        self._drop_index = -1
        self._drop_indicator.hide()

        # ── 文章プロンプト一覧からのドロップ ──────────────────────
        if event.mimeData().hasFormat(_PROMPT_TEXT_MIME):
            raw = event.mimeData().data(_PROMPT_TEXT_MIME).data()
            try:
                prompt_text_id = int(raw.decode())
            except (ValueError, UnicodeDecodeError):
                event.ignore()
                return
            record = _library_db.fetchone(
                "SELECT * FROM prompt_texts WHERE id = ?", (prompt_text_id,)
            )
            if record:
                tile = NaturalTextTile(
                    text=record["translated_text"] or record["source_text"],
                    source_text=record["source_text"],
                    translated_text=record["translated_text"] or record["source_text"],
                    display_label=record["display_label"] or "",
                )
                pos_in_ct = self._tiles_container.mapFrom(self, event.position().toPoint())
                idx = self._find_drop_index(pos_in_ct)
                self.block.tiles.insert(idx, tile)
                self._refresh_tiles()
                self.block_changed.emit()
            event.acceptProposedAction()
            return

        # ── 保存グループのドロップ ─────────────────────────────
        if event.mimeData().hasFormat(GROUP_BROWSER_MIME):
            import ui.tag_drag as tag_drag
            import json
            drag_info = tag_drag.get_group_drag()
            if drag_info and drag_info.get("group_json"):
                group = GroupTile.from_dict(
                    json.loads(drag_info["group_json"]),
                    name_override=drag_info.get("preset_name") or None,
                    restore_ui_state=False,
                )
                pos_in_ct = self._tiles_container.mapFrom(self, event.position().toPoint())
                idx = self._find_drop_index(pos_in_ct)
                self.block.tiles.insert(idx, group)
                self._refresh_tiles()
                self.block_changed.emit()
            event.acceptProposedAction()
            return

        # ── ブラウザタグのドロップ ──────────────────────────────
        if event.mimeData().hasFormat(BROWSER_MIME):
            import ui.tag_drag as tag_drag
            info = tag_drag.get_drag()
            if self._can_accept_browser_tag(info):
                from core.prompt_builder import TagTile
                tile = TagTile(
                    tag_name=info["name_en"],
                    tag_local=info["name_local"],
                    category=info["category"],
                    dictionary_key=info.get("dictionary_key", ""),
                )
                # ドロップ位置に挿入
                pos_in_ct = self._tiles_container.mapFrom(self, event.position().toPoint())
                idx = self._find_drop_index(pos_in_ct)
                self.block.tiles.insert(idx, tile)
                self._refresh_tiles()
                self.block_changed.emit()
                # 発光通知は dropEvent ラッパーの差分検出が一括で行う。
            if self._can_accept_browser_tag(info):
                event.acceptProposedAction()
            else:
                event.ignore()
            return

        # ── タイル間 D&D ────────────────────────────────────────
        src_widget = tile_drag.get_drag()

        if src_widget is None:
            event.ignore()
            return

        pos_in_ct  = self._tiles_container.mapFrom(self, event.position().toPoint())
        dragged    = src_widget.tile
        source_bw  = self._find_source_block(src_widget)
        source_readonly = bool(getattr(source_bw, "_readonly", False))
        if source_readonly:
            dragged = self._clone_tile_for_drop(dragged)

        # ── ドロップ先がタイルのラベル文字の上なら グループ化 ────────────
        target_tw = self._find_tile_widget_at(pos_in_ct)
        if target_tw is not None and target_tw is not src_widget:
            pos_in_tile = target_tw.mapFrom(self._tiles_container, pos_in_ct)
            shift_down = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            target_tile = target_tw.tile
            group_to_group = isinstance(dragged, GroupTile) and isinstance(target_tile, GroupTile)
            if shift_down and (group_to_group or self._is_drop_on_label(target_tw, pos_in_tile)):
                # 同一タイルへのドロップは無視
                if dragged is not target_tile:
                    # ターゲットが既に GroupTile → そこへ追加
                    if isinstance(target_tile, GroupTile):
                        if hasattr(target_tw, "_can_accept_drop_tile") and not target_tw._can_accept_drop_tile(dragged):
                            if hasattr(target_tw, "_show_max_depth_warning"):
                                target_tw._show_max_depth_warning()
                            event.ignore()
                            return
                        source_group = self._find_source_group(src_widget)
                        if source_group is target_tw:
                            tile_drag.clear_drag()
                            event.acceptProposedAction()
                            return
                        source_group_idx = (
                            self._index_by_identity(self.block.tiles, source_group.tile)
                            if source_bw is self and source_group is not None
                            else -1
                        )
                        source_top_idx = (
                            self._index_by_identity(self.block.tiles, dragged)
                            if source_bw is self and source_group is None
                            else -1
                        )
                        target_has_group = (
                            hasattr(target_tw, "_contains_group")
                            and target_tw._contains_group(target_tile)
                        )
                        wrap_groups = (
                            group_to_group
                            and not getattr(target_tw, "_expanded", False)
                            and not target_has_group
                        )
                        if wrap_groups and hasattr(target_tw, "_can_wrap_groups"):
                            if not target_tw._can_wrap_groups(0, target_tile, dragged):
                                target_tw._show_max_depth_warning()
                                event.ignore()
                                return
                        elif group_to_group and hasattr(target_tw, "_can_add_group_child"):
                            if not target_tw._can_add_group_child(target_tw._depth, dragged):
                                target_tw._show_max_depth_warning()
                                event.ignore()
                                return
                        self._remove_tile_from_block_or_group(src_widget, source_bw)
                        if wrap_groups:
                            tgt_idx = self._index_by_identity(self.block.tiles, target_tile)
                            if tgt_idx < 0:
                                event.ignore()
                                return
                            target_tile.ui_expanded = False
                            dragged.ui_expanded = False
                            grp_name = _tile_short_name(target_tile) or _tile_short_name(dragged)
                            if not grp_name:
                                grp_name = f"Grp{random.randint(100, 999)}"
                            grp = GroupTile(name=grp_name)
                            grp.tiles = [target_tile, dragged]
                            grp.ui_expanded = True
                            self.block.tiles[tgt_idx] = grp
                        else:
                            if isinstance(dragged, GroupTile):
                                dragged.ui_expanded = False
                            target_tile.tiles.append(dragged)
                            if group_to_group and target_has_group:
                                target_tw._expanded = True
                                target_tw.tile.ui_expanded = True
                        if source_bw is self:
                            if source_top_idx >= 0:
                                self._remove_tile_widget(source_top_idx)
                            elif source_group is not None and source_group_idx >= 0:
                                self._sync_group_widget_after_child_removed(source_group_idx, source_group)
                            if wrap_groups:
                                target_widget_idx = self._index_widget_by_identity(target_tw)
                                if target_widget_idx >= 0:
                                    self._replace_tile_widget(target_widget_idx, grp)
                                else:
                                    self._refresh_tiles()
                            elif hasattr(target_tw, "_refresh_sub_tiles"):
                                target_tw._refresh_sub_tiles()
                                target_tw._inner.setVisible(target_tw._expanded)
                                target_tw.tile.ui_expanded = target_tw._expanded
                                target_tw._expand_btn.setText("▼" if target_tw._expanded else "▶")
                                target_tw._update_inner_height()
                            self._on_group_geometry_changed()
                            tile_drag.clear_drag()
                            self.block_changed.emit()
                            event.acceptProposedAction()
                            return
                    else:
                        # 新規グループを作成してターゲットを置き換え
                        tgt_idx = self._index_by_identity(self.block.tiles, target_tile)
                        if tgt_idx < 0:
                            event.ignore()
                            return
                        grp_name = _tile_short_name(target_tile) or _tile_short_name(dragged)
                        if not grp_name:
                            grp_name = f"Grp{random.randint(100, 999)}"
                        grp = GroupTile(name=grp_name)
                        grp.tiles = [target_tile, dragged]
                        self.block.tiles[tgt_idx] = grp
                        source_group = self._find_source_group(src_widget)
                        source_group_idx = (
                            self._index_by_identity(self.block.tiles, source_group.tile)
                            if source_bw is self and source_group is not None
                            else -1
                        )
                        source_top_idx = (
                            self._index_by_identity(self.block.tiles, dragged)
                            if source_bw is self and source_group is None
                            else -1
                        )
                        self._remove_tile_from_block_or_group(src_widget, source_bw,
                                                              skip_tile=grp)
                        if source_bw is self:
                            if source_top_idx >= 0:
                                self._remove_tile_widget(source_top_idx)
                            elif source_group is not None and source_group_idx >= 0:
                                self._sync_group_widget_after_child_removed(source_group_idx, source_group)
                            target_widget_idx = self._index_widget_by_identity(target_tw)
                            if target_widget_idx >= 0:
                                self._replace_tile_widget(target_widget_idx, grp)
                            else:
                                self._refresh_tiles()
                            tile_drag.clear_drag()
                            self.block_changed.emit()
                            event.acceptProposedAction()
                            return
                    if source_bw is not self:
                        source_bw._normalize_groups()
                        source_bw._refresh_tiles()
                        source_bw._update_tile_container_height()
                        source_bw.block_changed.emit()
                    tile_drag.clear_drag()
                    self._normalize_groups()
                    self._refresh_tiles()
                    self.block_changed.emit()
                    event.acceptProposedAction()
                    return

        # ── 通常の挿入（タイルの間） ───────────────────────────────
        drop_idx = self._find_drop_index(pos_in_ct)

        src_idx = self._index_by_identity(self.block.tiles, dragged)
        if source_bw is self and src_idx >= 0:
            # ── 同一ブロック内の直接並べ替え ──
            if src_idx == drop_idx or src_idx + 1 == drop_idx:
                tile_drag.clear_drag()
                event.acceptProposedAction()
                return
            self.block.tiles.pop(src_idx)
            adjusted = drop_idx if drop_idx <= src_idx else drop_idx - 1
            self.block.tiles.insert(adjusted, dragged)
            self._move_tile_widget(src_idx, adjusted)
            tile_drag.clear_drag()
            self.block_changed.emit()
            event.acceptProposedAction()
            return
        else:
            # ── ブロック間移動 or グループ内からの取り出し ──
            source_group = self._find_source_group(src_widget)
            source_top_idx = (
                self._index_by_identity(source_bw.block.tiles, dragged)
                if source_bw is not self
                else -1
            )
            source_group_idx = (
                self._index_by_identity(source_bw.block.tiles, source_group.tile)
                if source_bw is not self and source_group is not None
                else -1
            )
            self._remove_tile_from_block_or_group(src_widget, source_bw)
            if source_bw is self and source_group is not None and source_group_idx >= 0:
                remaining = len(source_group.tile.tiles)
                if remaining >= 2:
                    self.block.tiles.insert(drop_idx, dragged)
                    source_group._refresh_sub_tiles()
                    self._insert_tile_widget(drop_idx, dragged)
                    tile_drag.clear_drag()
                    self.block_changed.emit()
                    event.acceptProposedAction()
                    return
                if remaining == 1:
                    survivor = source_group.tile.tiles[0]
                    self.block.tiles[source_group_idx] = survivor
                    self.block.tiles.insert(drop_idx, dragged)
                    self._replace_tile_widget(source_group_idx, survivor)
                    self._insert_tile_widget(drop_idx, dragged)
                    tile_drag.clear_drag()
                    self.block_changed.emit()
                    event.acceptProposedAction()
                    return
                self.block.tiles.pop(source_group_idx)
                adjusted_drop = drop_idx - 1 if drop_idx > source_group_idx else drop_idx
                self.block.tiles.insert(adjusted_drop, dragged)
                self._remove_tile_widget(source_group_idx)
                self._insert_tile_widget(adjusted_drop, dragged)
                tile_drag.clear_drag()
                self.block_changed.emit()
                event.acceptProposedAction()
                return

            self.block.tiles.insert(drop_idx, dragged)
            if source_bw is not self:
                if source_top_idx >= 0:
                    source_bw._remove_tile_widget(source_top_idx)
                elif source_group is not None and source_group_idx >= 0:
                    source_bw._sync_group_widget_after_child_removed(source_group_idx, source_group)
                else:
                    source_bw._normalize_groups()
                    source_bw._refresh_tiles()
                    source_bw._update_tile_container_height()
                source_bw.block_changed.emit()
                self._insert_tile_widget(drop_idx, dragged)
                tile_drag.clear_drag()
                self.block_changed.emit()
                event.acceptProposedAction()
                return

        tile_drag.clear_drag()
        self._normalize_groups()
        self._refresh_tiles()
        self.block_changed.emit()
        event.acceptProposedAction()

    # ── ドロップ位置計算 ────────────────────────────────

    def _find_drop_index(self, pos: QPoint) -> int:
        """
        tiles_container 座標上のマウス位置から挿入インデックスを返す (0〜len)。

        FlowLayout の行を考慮したアルゴリズム:
          ・タイルの左半分  → そのタイルの前（= index i）
          ・タイルの右半分  → そのタイルの後（次タイルが同じ行なら継続して判定）
          ・あるタイルより上 → そのタイルの前
          ・全タイルより下  → 末尾
        """
        px, py = pos.x(), pos.y()
        n = len(self._tile_widgets)
        if n == 0:
            return 0

        for i, tw in enumerate(self._tile_widgets):
            ty  = tw.y()
            th  = tw.height()
            tx  = tw.x()
            tw_w = tw.width()

            if py < ty:
                # マウスがこのタイルより上 → このタイルの前
                return i

            if py < ty + th:
                # マウスがこのタイルの行にある
                if px < tx + tw_w // 2:
                    return i  # 左半分 → このタイルの前
                # 右半分: 次タイルが同じ行なら継続
                next_same_row = (
                    i + 1 < n and
                    self._tile_widgets[i + 1].y() == ty
                )
                if not next_same_row:
                    return i + 1  # 行末 → このタイルの後

        return n  # マウスが全タイルより下 → 末尾

    def _show_drop_indicator(self, idx: int) -> None:
        """挿入位置を示す縦線インジケーターを配置・表示する"""
        n = len(self._tile_widgets)
        if n == 0:
            self._drop_indicator.hide()
            return

        if idx < n:
            ref = self._tile_widgets[idx]
            x = max(0, ref.x() - 3)
            y = ref.y()
            h = ref.height()
        else:
            ref = self._tile_widgets[-1]
            x = ref.x() + ref.width() + 1
            y = ref.y()
            h = ref.height()

        self._drop_indicator.setGeometry(x, y, 3, h)
        self._drop_indicator.show()
        self._drop_indicator.raise_()

    @staticmethod
    def _find_source_block(tw: QWidget) -> "BlockWidget":
        """タイル/グループウィジェットが属する BlockWidget を親チェーンから探す"""
        w = tw.parent()
        while w is not None:
            if isinstance(w, BlockWidget):
                return w
            w = w.parent()
        raise RuntimeError("タイルウィジェットの親に BlockWidget が見つかりません")

    def _find_tile_widget_at(self, pos: QPoint) -> QWidget | None:
        """tiles_container 座標上のマウス位置にあるタイル/グループウィジェットを返す"""
        for tw in self._tile_widgets:
            if (tw.x() <= pos.x() < tw.x() + tw.width() and
                    tw.y() <= pos.y() < tw.y() + tw.height()):
                return tw
        return None

    @staticmethod
    def _find_source_group(tw: QWidget):
        from ui.group_widget import GroupWidget
        w = tw.parent()
        while w is not None:
            if isinstance(w, GroupWidget):
                return w
            if isinstance(w, BlockWidget):
                return None
            w = w.parent()
        return None

    @staticmethod
    def _can_accept_browser_tag(info: dict | None) -> bool:
        if not info:
            return False
        return not bool(info.get("is_nav")) and int(info.get("child_count") or 0) == 0

    @staticmethod
    def _is_drop_on_label(tw: QWidget, pos_in_tile: QPoint) -> bool:
        """
        ドロップ先ウィジェット tw の中でポイントがラベル（タグ文字）の上にあるか判定する。
        TileWidget の is_over_label() を使い、それ以外（ボタン類など）では False を返す。
        グループ化はラベル文字の上にドロップした時のみ行う。
        """
        from ui.tile_widget import TileWidget as _TileWidget
        if isinstance(tw, _TileWidget):
            return tw.is_over_label(pos_in_tile)
        # GroupWidget など: ラベル相当のものがあれば将来的に対応、今はラベル領域全体をグループ化対象にする
        return True

    def _remove_tile_from_block_or_group(
        self,
        src_widget: QWidget,
        source_bw: "BlockWidget",
        skip_tile=None,
    ) -> None:
        """
        ドラッグ元ウィジェットのタイルをソースから削除する。
        skip_tile: この tile はスキップ（グループ化で既に置き換え済みの場合）
        """
        dragged = src_widget.tile
        if getattr(source_bw, "_readonly", False):
            return
        if dragged is skip_tile:
            return
        idx = self._index_by_identity(source_bw.block.tiles, dragged)
        if idx >= 0:
            source_bw.block.tiles.pop(idx)
            return
        # GroupWidget 内にある場合（グループからの取り出し）
        from ui.group_widget import GroupWidget
        p = src_widget.parent()
        while p is not None:
            if isinstance(p, GroupWidget):
                idx = self._index_by_identity(p.tile.tiles, dragged)
                if idx >= 0:
                    p.tile.tiles.pop(idx)
                    return
            p = p.parent()

    @staticmethod
    def _index_by_identity(items: list, target) -> int:
        """値が同じタイルが複数あっても、ドラッグ元の実体だけを探す。"""
        for i, item in enumerate(items):
            if item is target:
                return i
        return -1

    @staticmethod
    def _clone_tile_for_drop(tile):
        if isinstance(tile, GroupTile):
            return GroupTile.from_dict(tile.to_dict(include_ui_state=True))
        if isinstance(tile, TagTile):
            return TagTile.from_dict(tile.to_dict())
        if isinstance(tile, NaturalTextTile):
            return NaturalTextTile.from_dict(tile.to_dict())
        return tile

    # ── スロット ────────────────────────────────────────

    def _on_shuffle_changed(self, state: int) -> None:
        if self._readonly:
            return
        self.block.randomize = bool(state)
        self.block_changed.emit()

    def _toggle_lock(self) -> None:
        if self._readonly:
            return
        self._on_lock_changed(not self._locked)

    def _update_lock_overlay_geometry(self) -> None:
        """ロックオーバーレイをヘッダー直下のブロック本体全体に配置する。"""
        if not hasattr(self, "_lock_overlay") or not hasattr(self, "_header"):
            return
        hdr_h = self._header.height()
        self._lock_overlay.setGeometry(0, hdr_h, self.width(), self.height() - hdr_h)

    def _apply_lock_state(self) -> None:
        self._lock_btn.setText("🔒" if self._locked else "🔓")
        self._lock_btn.setToolTip(
            tr("block.lock_locked_tooltip") if self._locked else tr("block.lock_unlocked_tooltip")
        )
        if hasattr(self, "_lock_overlay"):
            if self._locked:
                self._update_lock_overlay_geometry()
                self._lock_overlay.show()
                self._lock_overlay.raise_()
            else:
                self._lock_overlay.hide()
        if hasattr(self, "_input_bar"):
            self._input_bar.setVisible(not self._locked and not self._collapsed)

    def _on_lock_changed(self, locked: bool) -> None:
        self._locked = locked
        self._apply_lock_state()
        self._input.setEnabled(not locked)
        if hasattr(self, "_input_editor_btn"):
            self._input_editor_btn.setEnabled(not locked)
        if hasattr(self, "_mode_btn"):
            self._mode_btn.setEnabled(not locked)
        self._update_input_button_states()

    def _open_input_editor(self) -> None:
        if self._readonly or self._locked:
            return
        dlg = _BlockInputEditorDialog(self._input.toPlainText(), self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self._input.setPlainText(dlg.text())
        cursor = self._input.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self._input.setTextCursor(cursor)
        self._input.setFocus()
        self._on_input_text_changed()

    def _copy_local_text(self) -> None:
        QApplication.clipboard().setText(self.block.compile_local(include_disabled=True))
        QToolTip.showText(QCursor.pos(), tr("editor.copy_done"))

    def _show_block_context_menu(self, pos: QPoint) -> None:
        """ヘッダー／タイル領域の右クリックで表示するブロック操作コンテキストメニュー。"""
        if self._readonly:
            return
        menu = QMenu(self)

        # ── 見出し（ブロック名） ──
        _label_key = (
            "block.label.negative"
            if self.block.block_type == "negative"
            else f"block.label.{self.block.position}"
        )
        block_label = self.block.label or tr(_label_key)
        title_lbl = QLabel(f"  {block_label}  ")
        title_lbl.setStyleSheet(
            f"color: {ACCENT}; font-weight: bold; padding: 4px 8px; background: transparent;"
        )
        title_wa = QWidgetAction(menu)
        title_wa.setDefaultWidget(title_lbl)
        menu.addAction(title_wa)
        menu.addSeparator()

        # ── 全タイルグループ展開 ──
        expand_action = QAction(tr("block.ctx_expand_all_groups"), menu)
        expand_action.triggered.connect(self._expand_all_groups)
        menu.addAction(expand_action)

        # ── 全タイルグループ畳む ──
        collapse_action = QAction(tr("block.ctx_collapse_all_groups"), menu)
        collapse_action.triggered.connect(self._collapse_all_groups)
        menu.addAction(collapse_action)

        menu.addSeparator()

        # ── 中央ペインの未翻訳タイル一括翻訳 ──
        bulk_translate_action = QAction(tr("block.ctx_bulk_translate"), menu)
        if self._bulk_translate_available_callback is not None:
            has_bulk_targets = bool(self._bulk_translate_available_callback())
        else:
            has_bulk_targets = bool(self._collect_untranslated_tiles())
        can_bulk_translate = (
            not self._locked
            and not self._is_translating
            and has_bulk_targets
        )
        bulk_translate_action.setEnabled(can_bulk_translate)
        if not can_bulk_translate:
            bulk_translate_action.setToolTip(tr("block.ctx_bulk_translate_disabled_empty"))
        bulk_translate_action.triggered.connect(self.bulk_translate_requested)
        menu.addAction(bulk_translate_action)

        classify_action = QAction(tr("block.ctx_auto_classify"), menu)
        classify_action.setEnabled(not self._locked and not self._is_translating)
        classify_action.triggered.connect(self.auto_classify_requested)
        menu.addAction(classify_action)

        menu.addSeparator()

        # ── ブロック現地語コピー ──
        copy_action = QAction(tr("block.ctx_copy_local"), menu)
        copy_action.triggered.connect(self._copy_local_text)
        menu.addAction(copy_action)

        # ── ブロッククリア ──
        clear_action = QAction(tr("block.clear"), menu)
        clear_action.setEnabled(not self._locked)
        clear_action.triggered.connect(self._clear_tiles)
        menu.addAction(clear_action)

        menu.exec(QCursor.pos())

    # ── 折りたたみ ─────────────────────────────────────

    def _collapse_key(self) -> str:
        return f"block_collapsed_{self.block.block_type}_{self.block.position}"

    def _load_collapsed_state(self) -> None:
        row = _app_db.fetchone("SELECT value FROM app_settings WHERE key=?", (self._collapse_key(),))
        if row and row["value"] == "1":
            self._collapsed = True
            self._apply_collapse_state(animate=False)

    def _toggle_collapse(self) -> None:
        self._collapsed = not self._collapsed
        self._apply_collapse_state()
        _app_db.execute(
            "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
            (self._collapse_key(), "1" if self._collapsed else "0"),
        )

    def _apply_collapse_state(self, animate: bool = True) -> None:
        self._tiles_container.setVisible(not self._collapsed)
        self._input_bar.setVisible(not self._collapsed and not self._locked)
        self._collapse_btn.setText("▶" if self._collapsed else "▼")
        self.updateGeometry()

    def _toggle_input_mode(self) -> None:
        self._input_is_natural = not self._input_is_natural
        self._input.set_tag_mode(not self._input_is_natural)
        self._apply_mode_style()
        placeholder = (
            tr("block.input_placeholder_natural")
            if self._input_is_natural
            else tr("block.input_placeholder_tag_mode")
        )
        self._input.setPlaceholderText(placeholder)
        self._input.setFocus()

    def _apply_readonly_state(self) -> None:
        if not self._readonly:
            return
        self.setAcceptDrops(False)
        for widget_name in (
            "_shuffle_cb", "_lock_btn", "_input_bar",
            "_translate_result_area", "_translate_panel",
        ):
            widget = getattr(self, widget_name, None)
            if widget is not None:
                widget.hide()

    def _apply_mode_style(self) -> None:
        if not hasattr(self, "_mode_btn"):
            return
        if self._input_is_natural:
            self._mode_btn.setText(tr("block.mode_natural"))
            self._mode_btn.setStyleSheet(
                f"QPushButton {{ background-color: #0f3a1a; color: #a6e3a1; border: 1px solid {SURFACE2}; border-radius: 3px; }}"
            )
        else:
            self._mode_btn.setText(tr("block.mode_tag"))
            self._mode_btn.setStyleSheet(
                f"QPushButton {{ background-color: {SURFACE1}; color: {SUBTEXT}; border: 1px solid {SURFACE2}; border-radius: 3px; }}"
            )

    @staticmethod
    def _normalize_lines(raw: str) -> list[str]:
        """改行コードを正規化して空行を除いた行リストを返す。"""
        normalized = raw.replace("\r\n", "\n").replace("\r", "\n")
        return [ln for ln in normalized.splitlines() if ln.strip()]

    def _input_text(self) -> str:
        return self._input.toPlainText().strip()

    def _has_addable_text(self) -> bool:
        return bool(self._input_text() or (self._translated_text or "").strip())

    def _update_input_button_states(self) -> None:
        if not hasattr(self, "_translate_danboard_btn"):
            return
        can_edit = not self._locked and not self._is_translating
        has_input = bool(self._input_text())
        has_addable = self._has_addable_text()
        self._translate_danboard_btn.setEnabled(can_edit and has_input)
        self._translate_natural_btn.setEnabled(can_edit and has_input)
        self._add_btn.setEnabled(can_edit and has_addable)
        self._natural_add_btn.setEnabled(can_edit and has_addable)

    def is_locked(self) -> bool:
        return self._locked

    def is_translating(self) -> bool:
        return self._is_translating

    def _collect_untranslated_tiles(self) -> list[object]:
        """このブロック直下の未翻訳タイルを返す。GroupTile 配下は対象外。"""
        targets: list[object] = []
        for tile in self.block.tiles:
            if isinstance(tile, TagTile):
                if (tile.tag_local or "").strip():
                    continue
                row = _library_db.fetchone(
                    "SELECT label FROM tag_labels WHERE tag_name = ?",
                    (tile.tag_name,),
                )
                if not (row and (row["label"] or "").strip()):
                    targets.append(tile)
            elif isinstance(tile, NaturalTextTile):
                source = (tile.source_text or "").strip()
                translated = (tile.translated_text or "").strip()
                if (not source) or (source == translated):
                    targets.append(tile)
        return targets

    def untranslated_tile_count(self) -> int:
        return len(self._collect_untranslated_tiles())

    def untranslated_tiles(self) -> list[object]:
        return self._collect_untranslated_tiles()

    def set_bulk_translate_available_callback(self, callback) -> None:
        self._bulk_translate_available_callback = callback

    @staticmethod
    def _active_translate_button_style() -> str:
        if is_light_theme():
            bg = "#fff0b3"
            hover = "#ffe28a"
            border = "#d18400"
            fg = "#3b2a00"
        else:
            bg = "#f9e2af"
            hover = "#fab387"
            border = "#f9e2af"
            fg = "#1e1e2e"
        return (
            f"QPushButton {{ background-color: {bg}; color: {fg}; "
            f"border: 2px solid {border}; border-radius: 4px; padding: 4px 12px; "
            f"font-weight: bold; }}"
            f"QPushButton:hover {{ background-color: {hover}; }}"
            f"QPushButton:disabled {{ background-color: {bg}; color: {fg}; "
            f"border: 2px solid {border}; }}"
        )

    def _set_translate_button_active(self, active_btn=None) -> None:
        for btn in (self._translate_danboard_btn, self._translate_natural_btn):
            btn.setStyleSheet(themed_button_style("translate"))
        if active_btn is not None:
            active_btn.setStyleSheet(self._active_translate_button_style())

    def _reset_input_after_add(self) -> None:
        self._user_editing = True
        self._input.clear()
        self._user_editing = False
        self._translated_text = None
        self._source_at_translate = None
        self._edit_warned = False
        self._translate_result_area.hide()
        self._update_input_button_states()

        self._refresh_tiles()
        self._from_input = True
        self.block_changed.emit()
        self._from_input = False

    def _natural_tiles_from_input(self, text: str) -> list[NaturalTextTile]:
        if self._translated_text is not None and self._translate_mode in ("natural", "auto"):
            source = self._source_at_translate or text
            src_lines   = self._normalize_lines(source)
            trans_lines = self._normalize_lines(self._translated_text)
            if src_lines and len(src_lines) == len(trans_lines):
                return [
                    NaturalTextTile(text=t, source_text=s, translated_text=t)
                    for s, t in zip(src_lines, trans_lines)
                ]
            translated = self._translated_text
            return [NaturalTextTile(
                text=translated,
                source_text=source,
                translated_text=translated,
            )]
        lines = self._normalize_lines(text)
        return [
            NaturalTextTile(text=ln, source_text=ln, translated_text="")
            for ln in (lines if lines else [text])
        ]

    def _add_word_tile_from_input(self) -> None:
        text = self._input_text()
        if not text and self._translated_text is None:
            return

        if self._translated_text is not None and self._translate_mode in ("danboard", "auto"):
            source = self._source_at_translate or text
            tiles = self._parse_translated_tag_input(source, self._translated_text)
        else:
            tiles = self._parse_tag_input(text)
        before = self._snapshot_tag_tile_ids()
        for tile in tiles:
            if isinstance(tile, TagTile):
                tile = self._enrich_tile_from_db(tile)
            self.block.add_tile(tile)
        self._reset_input_after_add()
        # D&D と同じ差分検出で、入力／翻訳追加でも増えたタグの同名発光を通知する。
        self._emit_glow_for_new_tags(before)

    def _add_natural_tile_from_input(self) -> None:
        text = self._input_text()
        if not text and self._translated_text is None:
            return
        for tile in self._natural_tiles_from_input(text):
            self.block.add_tile(tile)
        self._reset_input_after_add()

    def _add_tile_from_input(self) -> None:
        """後方互換用: Enter や古い接続からは単語追加として扱う。"""
        self._add_word_tile_from_input()

    @staticmethod
    def _enrich_tile_from_db(tile: "TagTile") -> "TagTile":
        """
        TagTile の tag_name を DB（tags テーブル）で検索し、
        カテゴリ・日本語名・英語名を補完して返す。

        - 英語 (name_en) でマッチ → category / tag_local を補完
        - 日本語 (name_ja) でマッチ → tag_name を name_en に変換 + category 付与
        - マッチなし → tile をそのまま返す
        """
        tag = tile.tag_name.strip()
        if not tag:
            return tile
        try:
            row = _library_db.fetchone(
                "SELECT name_en, name_local, category FROM tags "
                "WHERE COALESCE(is_nav_only, 0) = 0 "
                "  AND (LOWER(name_en) = LOWER(?) OR LOWER(COALESCE(name_local,'')) = LOWER(?)) "
                "LIMIT 1",
                (tag, tag),
            )
        except Exception:
            return tile
        if not row:
            return tile
        return TagTile(
            tag_name        = row["name_en"] or tile.tag_name,
            tag_local       = row["name_local"] or tile.tag_local,
            category        = row["category"] or tile.category,
            emphasis        = tile.emphasis,
            strength_level  = tile.strength_level,
            is_locked       = tile.is_locked,
            is_trigger_word = tile.is_trigger_word,
            enabled         = tile.enabled,
            lora_source_key = tile.lora_source_key,
            source_text     = tile.source_text,
            translated_text = tile.translated_text,
        )

    def _parse_translated_tag_input(self, source_text: str, translated_text: str) -> list:
        """
        ダンボール語翻訳結果をタグ化するとき、原文タグと翻訳タグを先頭から1:1対応させる。
        DB未登録タグでも中央表示が空にならないよう、原文を tag_local にも残す。
        """
        source_tiles = [
            tile for tile in self._parse_tag_input(source_text)
            if isinstance(tile, TagTile)
        ]
        source_names = [tile.tag_name for tile in source_tiles if tile.tag_name.strip()]
        if len(source_names) <= 1:
            raw = single_line_text(translated_text)
            if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
                raw = raw[1:-1].strip()
            if not raw:
                return []
            source = source_names[0] if source_names else single_line_text(source_text) or raw
            tile = TagTile(tag_name=raw)
            tile.source_text = source
            tile.translated_text = raw
            tile.tag_local = source
            return [tile]

        parsed_tiles = self._parse_tag_input(translated_text)

        tag_index = 0
        for tile in parsed_tiles:
            if not isinstance(tile, TagTile):
                continue
            if source_names:
                source = source_names[min(tag_index, len(source_names) - 1)]
            else:
                source = tile.tag_name
            tile.source_text = source
            tile.translated_text = tile.tag_name
            if not tile.tag_local:
                tile.tag_local = source
            tag_index += 1
        return parsed_tiles

    @staticmethod
    def _parse_tag_input(text: str) -> list:
        """
        タグモードの入力をパースして TagTile / NaturalTextTile のリストを返す。

        ルール（優先順）:
          1. (...) で囲まれた部分  → TagTile 1個（括弧ごと保存、内部カンマで分割しない）
                                     例: (worst quality, low quality:1.4) → 1タイル
          2. それ以外             → カンマで分割して TagTile
                                     強調度は「tag:1.3」形式（コロン後が数値の場合のみ）
        """
        tiles = []
        i = 0
        n = len(text)
        buf: list[str] = []

        def flush_buf() -> None:
            joined = ''.join(buf).strip()
            buf.clear()
            for raw in joined.split(','):
                raw = raw.strip()
                if not raw:
                    continue
                emphasis = 1.0
                if ':' in raw:
                    tag_part, _, num_part = raw.rpartition(':')
                    try:
                        emphasis = float(num_part)
                        raw = tag_part.strip()
                    except ValueError:
                        pass
                if raw:
                    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
                        raw = raw[1:-1].strip()
                    tiles.append(TagTile(tag_name=raw, emphasis=emphasis))

        while i < n:
            ch = text[i]

            if ch == '(':
                flush_buf()
                depth = 1
                j = i + 1
                while j < n and depth > 0:
                    if text[j] == '(':
                        depth += 1
                    elif text[j] == ')':
                        depth -= 1
                    j += 1
                raw = text[i : j].strip()
                if raw:
                    tiles.append(TagTile(tag_name=raw, emphasis=1.0))
                i = j
                while i < n and text[i] in ' ,':
                    i += 1

            else:
                buf.append(ch)
                i += 1

        flush_buf()
        return tiles

    def _clear_tiles(self) -> None:
        if not self.block.tiles:
            return
        ret = QMessageBox.question(
            self,
            tr("block.clear_confirm_title"),
            tr("block.clear_confirm_msg", label=self._title_label.text()),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        self.block.tiles.clear()
        self._refresh_tiles()
        self.block_changed.emit()

    def _on_tile_delete(self, w: QWidget) -> None:
        # ウィジェットのインデックスで削除（値が等しい重複タイルがあっても正しい方を消す）
        removed = False
        try:
            idx = self._tile_widgets.index(w)
            if 0 <= idx < len(self.block.tiles):
                self.block.tiles.pop(idx)
                self._remove_tile_widget(idx)
                removed = True
        except ValueError:
            # フォールバック: タイルオブジェクトで検索
            tile = w.tile
            idx = self._index_by_identity(self.block.tiles, tile)
            if idx >= 0:
                self.block.tiles.pop(idx)
                self._remove_tile_widget(idx)
                removed = True
        if removed:
            self.block_changed.emit()

    def _on_group_ungroup_requested(self, w: QWidget) -> None:
        idx = self._index_widget_by_identity(w)
        if idx < 0:
            idx = self._index_by_identity(self.block.tiles, w.tile)
        if idx < 0 or not isinstance(getattr(w, "tile", None), GroupTile):
            return
        children = list(w.tile.tiles)
        self.block.tiles.pop(idx)
        for offset, child in enumerate(children):
            self.block.tiles.insert(idx + offset, child)
        self._refresh_tiles()
        self.block_changed.emit()

    def _on_group_tile_changed(self) -> None:
        """
        GroupWidget から tile_changed を受け取る。
        グループ内タイルが1個になった場合はグループを解消して展開する。
        0個の場合はグループ自体を削除する。
        それ以外でも高さを再計算する（タイル追加時など）。
        """
        sender = self.sender()
        if isinstance(sender, QWidget) and hasattr(sender, "tile"):
            idx = self._index_widget_by_identity(sender)
            if idx >= 0 and isinstance(sender.tile, GroupTile) and len(sender.tile.tiles) <= 1:
                self._sync_group_widget_after_child_removed(idx, sender)
                self._on_group_geometry_changed()
                self.block_changed.emit()
                return
        if self._normalize_groups():
            self._refresh_tiles()
        # 解消がなくても高さ・レイアウトを更新（グループへのタイル追加時に必要）
        self._on_group_geometry_changed()
        self.block_changed.emit()

    def _normalize_groups(self) -> bool:
        """空または1要素になったトップレベルグループを確定状態へ畳む。"""
        changed = False
        new_tiles: list = []
        for tile in self.block.tiles:
            if isinstance(tile, GroupTile):
                if len(tile.tiles) == 0:
                    changed = True
                elif len(tile.tiles) == 1:
                    new_tiles.append(tile.tiles[0])
                    changed = True
                else:
                    new_tiles.append(tile)
            else:
                new_tiles.append(tile)
        if changed:
            self.block.tiles[:] = new_tiles
        return changed

    def _on_group_geometry_changed(self) -> None:
        """GroupWidget の展開/折りたたみでブロックの最低高さを再計算する"""
        self._flow.invalidate()
        self._update_tile_container_height()
        self.updateGeometry()
        if hasattr(self, "_lock_overlay") and self._locked:
            self._update_lock_overlay_geometry()
            self._lock_overlay.raise_()

    def refresh_tile_styles(self) -> None:
        """タグブラウザ側のカテゴリ変更を、表示中タイルの色へ即時反映する。"""
        for widget in self._tile_widgets:
            if hasattr(widget, "refresh_tile_styles"):
                widget.refresh_tile_styles()
            elif hasattr(widget, "_apply_style"):
                widget._apply_style()
        self._flow.invalidate()
        self._update_tile_container_height()
        self.updateGeometry()

    def refresh_tile_display(self) -> None:
        """タイルの1段/2段表示切替を即時反映する。"""
        for widget in self._tile_widgets:
            if hasattr(widget, "refresh_tile_display"):
                widget.refresh_tile_display()
            elif hasattr(widget, "refresh"):
                widget.refresh()
        self._flow.invalidate()
        self._update_tile_container_height()
        self.updateGeometry()

    def _collapse_all_groups(self) -> None:
        """このブロック内の開いているグループタイルをすべて畳む。"""
        def _collapse_data(tiles: list) -> bool:
            changed = False
            for tile in tiles:
                if isinstance(tile, GroupTile):
                    if tile.ui_expanded:
                        changed = True
                    tile.ui_expanded = False
                    changed = _collapse_data(tile.tiles) or changed
            return changed

        changed = _collapse_data(self.block.tiles)
        for widget in self._tile_widgets:
            if hasattr(widget, "collapse_all_groups"):
                changed = widget.collapse_all_groups() or changed
        if changed:
            self._refresh_tiles()
            self.block_changed.emit()
        else:
            self._on_group_geometry_changed()

    def _expand_all_groups(self) -> None:
        """このブロック内のグループタイルをすべて展開する。"""
        def _expand_data(tiles: list) -> bool:
            changed = False
            for tile in tiles:
                if isinstance(tile, GroupTile):
                    if not tile.ui_expanded:
                        changed = True
                    tile.ui_expanded = True
                    changed = _expand_data(tile.tiles) or changed
            return changed

        changed = _expand_data(self.block.tiles)
        for widget in self._tile_widgets:
            if hasattr(widget, "expand_all_groups"):
                changed = widget.expand_all_groups() or changed
        if changed:
            self._refresh_tiles()
            self.block_changed.emit()
        else:
            self._on_group_geometry_changed()

    def _make_tile_widget(self, tile) -> QWidget:
        """トップレベル用の TileWidget/GroupWidget を作り、必要なシグナルを接続する。"""
        if isinstance(tile, GroupTile):
            from ui.group_widget import GroupWidget
            w: QWidget = GroupWidget(tile, parent=self._tiles_container, readonly=self._readonly)
            w.delete_requested.connect(self._on_tile_delete)
            w.ungroup_requested.connect(self._on_group_ungroup_requested)
            w.tile_changed.connect(self._on_group_tile_changed)
            w.geometry_changed.connect(self._on_group_geometry_changed)
            w.move_requested.connect(self._on_tile_move_requested)
            return w

        w = TileWidget(tile, parent=self._tiles_container, readonly=self._readonly)
        w.delete_requested.connect(self._on_tile_delete)
        w.tile_changed.connect(self.block_changed.emit)
        w.tile_replaced.connect(self._on_tile_replaced)
        w.move_requested.connect(self._on_tile_move_requested)
        return w

    def _on_tile_replaced(self, w: QWidget, new_tile) -> None:
        if self._locked:
            return
        idx = self._index_widget_by_identity(w)
        if idx < 0 or idx >= len(self.block.tiles):
            return
        self.block.tiles[idx] = new_tile
        self._replace_tile_widget(idx, new_tile)
        self.block_changed.emit()

    def _on_tile_move_requested(self, w: QWidget, delta: int) -> None:
        idx = self._index_widget_by_identity(w)
        new_idx = idx + delta
        if idx < 0 or not (0 <= new_idx < len(self.block.tiles)):
            return
        self.block.tiles.insert(new_idx, self.block.tiles.pop(idx))
        self._move_tile_widget(idx, new_idx)
        self.block_changed.emit()

    def _insert_tile_widget(self, index: int, tile) -> None:
        w = self._make_tile_widget(tile)
        self._flow.insertWidget(index, w)
        self._tile_widgets.insert(index, w)
        w.show()
        self._flow.invalidate()
        self._update_tile_container_height()
        self.updateGeometry()
        self.layout_changed.emit()

    def _remove_tile_widget(self, index: int) -> None:
        if not (0 <= index < len(self._tile_widgets)):
            return
        item = self._flow.takeAt(index)
        w = self._tile_widgets.pop(index)
        if item and item.widget() and item.widget() is not w:
            item.widget().hide()
            item.widget().deleteLater()
        w.hide()
        w.deleteLater()
        self._flow.invalidate()
        self._update_tile_container_height()
        self.updateGeometry()
        self.layout_changed.emit()

    def _replace_tile_widget(self, index: int, tile) -> None:
        self._remove_tile_widget(index)
        self._insert_tile_widget(index, tile)

    def _move_tile_widget(self, source_index: int, dest_index: int) -> None:
        if source_index == dest_index:
            return
        if not (0 <= source_index < len(self._tile_widgets)):
            return
        dest_index = max(0, min(dest_index, len(self._tile_widgets) - 1))
        item = self._flow.takeAt(source_index)
        widget = self._tile_widgets.pop(source_index)
        if item is None:
            self._refresh_tiles()
            return
        self._flow.insertItem(dest_index, item)
        self._tile_widgets.insert(dest_index, widget)
        self._flow.invalidate()
        self._update_tile_container_height()
        self.updateGeometry()
        self.layout_changed.emit()

    def _index_widget_by_identity(self, target: QWidget) -> int:
        for i, widget in enumerate(self._tile_widgets):
            if widget is target:
                return i
        return -1

    def _sync_group_widget_after_child_removed(self, index: int, group_widget) -> None:
        remaining = len(group_widget.tile.tiles)
        if remaining >= 2:
            group_widget._refresh_sub_tiles()
            group_widget._update_inner_height()
            self._on_group_geometry_changed()
            return
        if remaining == 1:
            survivor = group_widget.tile.tiles[0]
            self.block.tiles[index] = survivor
            self._replace_tile_widget(index, survivor)
            return
        self.block.tiles.pop(index)
        self._remove_tile_widget(index)

    def find_tag_matches(self, tag_name: str, *, exclude_tile=None) -> list[tuple[QWidget, list[object]]]:
        """このブロック内から tag_name に一致する TagTile のウィジェットを探す。"""
        from ui.group_widget import GroupWidget

        target = (tag_name or "").strip().lower()
        if not target:
            return []

        matches: list[tuple[QWidget, list[object]]] = []
        for tile, widget in zip(self.block.tiles, self._tile_widgets):
            if isinstance(tile, TagTile):
                if tile is exclude_tile:
                    continue
                if (tile.tag_name or "").strip().lower() == target:
                    matches.append((widget, []))
                continue

            if isinstance(tile, GroupTile) and isinstance(widget, GroupWidget):
                matches.extend(widget.find_tag_matches(tag_name, exclude_tile=exclude_tile))
        return matches

    def find_widget_for_tile(self, target_tile) -> QWidget | None:
        """このブロック内から target_tile に対応するウィジェットを返す。"""
        from ui.group_widget import GroupWidget

        for tile, widget in zip(self.block.tiles, self._tile_widgets):
            if tile is target_tile:
                return widget
            if isinstance(tile, GroupTile) and isinstance(widget, GroupWidget):
                found = widget.find_widget_for_tile(target_tile)
                if found is not None:
                    return found
        return None

    # ── イベントフィルタ ─────────────────────────────────

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        """入力フィールドのフォーカス取得時にブロックフォーカスを通知する。
        翻訳ボタンは常時表示のためフォーカス変化による show/hide は行わない。
        """
        if obj is self._input:
            if event.type() == QEvent.Type.FocusIn:
                self.block_focused.emit()
        return super().eventFilter(obj, event)

    def mousePressEvent(self, event) -> None:
        """ブロック本体をクリックしたときもフォーカスを通知"""
        super().mousePressEvent(event)
        self.block_focused.emit()

    # ── 外部API ─────────────────────────────────────────

    def _on_input_text_changed(self) -> None:
        """ユーザーが原文を編集したとき、翻訳結果が表示中なら確認ダイアログを出す。"""
        self._update_input_button_states()
        if self._user_editing:
            return
        if self._translated_text is None:
            return
        if self._source_edit_warning_active:
            return

        self._source_edit_warning_active = True
        QTimer.singleShot(0, self._confirm_source_edit_after_change)

    def _confirm_source_edit_after_change(self) -> None:
        """textChanged の再入を避けて、原文変更確認を次イベントで処理する。"""
        if self._translated_text is None:
            self._source_edit_warning_active = False
            return

        dlg = QMessageBox(self)
        dlg.setWindowTitle(tr("block.edit_source_title"))
        dlg.setText(tr("block.edit_source_body"))
        clear_btn = dlg.addButton(tr("block.edit_clear_translation"), QMessageBox.ButtonRole.AcceptRole)
        restore_btn = dlg.addButton(tr("block.edit_restore_source"), QMessageBox.ButtonRole.RejectRole)
        dlg.setDefaultButton(restore_btn)
        dlg.exec()

        if dlg.clickedButton() is clear_btn:
            self._translated_text = None
            self._source_at_translate = None
            self._edit_warned = False
            self._translate_result_area.hide()
        else:
            # 原文を翻訳時点の内容に戻す
            if self._source_at_translate is not None:
                self._user_editing = True
                self._input.setPlainText(self._source_at_translate)
                self._user_editing = False
            self._edit_warned = False

        self._source_edit_warning_active = False
        self._update_input_button_states()

    def _on_translate_danboard_click(self) -> None:
        """ダンボール語（タグ）翻訳を要求する。"""
        self.block_focused.emit()
        text = self._input.toPlainText().strip()
        if text:
            self._translate_mode = "danboard"
            self._translated_text = None
            self._translate_result_area.hide()
            self._active_translate_btn = self._translate_danboard_btn
            self.translate_requested.emit(text, "danboard")

    def _on_translate_natural_click(self) -> None:
        """自然言語（英文）翻訳を要求する。"""
        self.block_focused.emit()
        text = self._input.toPlainText().strip()
        if text:
            self._translate_mode = "natural"
            self._translated_text = None
            self._translate_result_area.hide()
            self._active_translate_btn = self._translate_natural_btn
            self.translate_requested.emit(text, "natural")

    def set_translating(self, translating: bool) -> None:
        """翻訳中フラグを切り替える。翻訳中はこのブロックの入力を拒否する。"""
        self._is_translating = translating
        if translating and self._active_translate_btn is not None:
            self._set_translate_button_active(self._active_translate_btn)
            if self._active_translate_btn is self._translate_natural_btn:
                self._active_translate_btn.setText("⏳ " + tr("block.natural_translate_btn"))
            else:
                self._active_translate_btn.setText("⏳ " + tr("block.word_translate_btn"))
        else:
            self._set_translate_button_active(None)
            self._translate_danboard_btn.setText(tr("block.word_translate_btn"))
            self._translate_natural_btn.setText(tr("block.natural_translate_btn"))
        self._input.setEnabled(not translating)
        self._update_input_button_states()

    def set_translate_result(self, text: str) -> None:
        """翻訳完了時に、結果を使って自動でタイルを追加する（単語翻訳追加 / 文章翻訳追加）。"""
        self.set_translating(False)

        text = (text or "").strip()
        if not text:
            return

        self._translated_text = text
        self._source_at_translate = self._input.toPlainText().strip()
        self._edit_warned = False

        # 翻訳が終わったら自動でタイルを追加する。
        # _add_*_tile_from_input が _translated_text / _translate_mode を参照して
        # 訳文タイルを生成し、_reset_input_after_add で入力欄・結果エリアを片付ける。
        if self._translate_mode == "natural":
            self._add_natural_tile_from_input()
        else:
            self._add_word_tile_from_input()

    def set_global_translating(self, translating: bool) -> None:
        """このブロックが翻訳対象でない場合に翻訳ボタンだけ無効化する。
        翻訳中の多重起動を防ぐため MainWindow から全ブロックに一括適用される。
        """
        self._is_translating = translating
        self._update_input_button_states()

    # ── 翻訳インラインパネル ────────────────────────────

    def show_translate_panel(self) -> None:
        """翻訳パネルを入力欄の下に表示する。"""
        self._translate_thinking_edit.clear()
        self._translate_status_lbl.setText(tr("translate_panel.status_translating"))
        self._translate_status_lbl.setStyleSheet(f"color: {SUBTEXT}; background: transparent;")
        self._translate_panel.show()
        self.updateGeometry()
        if self.parent():
            self.parent().updateGeometry()

    def hide_translate_panel(self) -> None:
        """翻訳パネルを折りたたんで非表示にする。"""
        self._translate_panel.hide()
        self.updateGeometry()
        if self.parent():
            self.parent().updateGeometry()

    def append_translate_thinking(self, text: str) -> None:
        """Thinking チャンクを追記する。"""
        cursor = self._translate_thinking_edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        self._translate_thinking_edit.setTextCursor(cursor)
        self._translate_thinking_edit.ensureCursorVisible()

    def append_translate_status(self, text: str) -> None:
        """ステータスラベルを更新する。"""
        self._translate_status_lbl.setText(text)

    def show_translate_failure(self, message: str) -> None:
        """翻訳失敗をユーザーが見える形でパネルに残す。"""
        self.set_translating(False)
        self._translate_thinking_edit.hide()
        self._translate_status_lbl.setStyleSheet(f"color: {RED}; background: transparent;")
        self._translate_status_lbl.setText(tr("main.translate_failed", error=message))
        self._translate_panel.show()
        self.updateGeometry()
        if self.parent():
            self.parent().updateGeometry()

    def clear_translate_status(self) -> None:
        """ステータスラベルとシンキングパネルをクリアする（複数行翻訳の行間で使用）。"""
        self._translate_status_lbl.setText("")
        self._translate_status_lbl.setStyleSheet(f"color: {SUBTEXT}; background: transparent;")
        self._translate_thinking_edit.clear()

    def reload(self) -> None:
        """外部から block が変更されたとき呼ぶ"""
        self.retranslate_and_restyle()
        self._refresh_tiles()

    def retranslate_and_restyle(self) -> None:
        """言語・テーマ・フォント設定を現在のブロックへ即時反映する。"""
        self._shuffle_cb.setChecked(self.block.randomize)
        _label_key = (
            "block.label.negative"
            if self.block.block_type == "negative"
            else f"block.label.{self.block.position}"
        )
        self._title_label.setText(self.block.label or tr(_label_key))
        self._collapse_btn.setToolTip(tr("block.collapse_tooltip"))
        self._shuffle_cb.setText(tr("block.shuffle"))
        self._shuffle_cb.setToolTip(tr("block.shuffle_tooltip"))
        self._apply_lock_state()
        self._input_editor_btn.setToolTip(tr("block.input_editor_tooltip"))
        self._input.setPlaceholderText(
            tr("block.input_placeholder_natural") if self._input_is_natural else tr("block.input_placeholder_tag")
        )
        self._translate_danboard_btn.setText(tr("block.word_translate_btn"))
        self._translate_danboard_btn.setToolTip(tr("block.translate_danboard_tooltip"))
        self._add_btn.setText(tr("block.word_add_btn"))
        self._translate_natural_btn.setText(tr("block.natural_translate_btn"))
        self._translate_natural_btn.setToolTip(tr("block.translate_natural_tooltip"))
        self._natural_add_btn.setText(tr("block.natural_add_btn"))
        self._natural_add_btn.setToolTip(tr("block.natural_add_tooltip"))
        self._translate_result_area.setPlaceholderText(tr("block.translate_result_label"))
        self._translate_status_lbl.setText(tr("translate_panel.status_translating") if self._is_translating else "")
