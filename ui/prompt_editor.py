"""
プロンプトエディタ（中央ペイン）

PromptDocumentを視覚的に編集するメインウィジェット。
・ポジティブ: 先頭/中間/末尾ブロック
・ネガティブ: 対応モデルのみ表示
・メモ欄: ネガティブブロック下に配置（レビューに保存）
・ブロック自体がタイル数に応じて縦伸び（スクロールは中央ペイン全体）
・コンパイル済み文字列をリアルタイムプレビュー（下部固定）
・UNDO スナップショット（500ms デバウンス、最大50件）
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QFrame, QTextEdit, QSizePolicy, QToolButton, QSplitter, QToolTip,
    QDialog, QDialogButtonBox, QProgressBar, QMessageBox,
)
from PySide6.QtCore import Signal, Qt, QTimer, QPoint, QThread
from PySide6.QtGui import QFont, QColor

from core.prompt_builder import PromptDocument, TagTile, NaturalTextTile, GroupTile, BlockType, BlockPosition
from core.i18n import tr
from core.lm_settings import DEFAULT_CLASSIFY_PROMPT, lm_seed, lm_temperature
from core.text_sanitize import single_line_text
from api.lm_client import LMClient, LMStudioError, translation_fallback_from_thinking
import db.app_db as _app_db
import db.library_db as _library_db
from db.group_preset_db import unique_group_name
from db.prompt_text_db import insert_prompt_text, exists_source_text
from ui.block_widget import BlockWidget
from ui.tile_widget import (
    _TileTranslateWorker,
    is_translation_model_configured,
    translation_model_missing_message,
)
from ui.styles import (
    SURFACE0, SURFACE1, SURFACE2, TEXT, SUBTEXT, ACCENT, RED,
    EMOJI_ICON_SS, ui_font,
)


def _get_setting(key: str, default: str = "") -> str:
    row = _app_db.fetchone("SELECT value FROM app_settings WHERE key=?", (key,))
    return row["value"] if row else default


def _readable_text_color(bg_hex: str) -> str:
    color = QColor(bg_hex)
    if not color.isValid():
        return TEXT
    r, g, b, _ = color.getRgb()
    luminance = 0.299 * r + 0.587 * g + 0.114 * b
    return "#cdd6f4" if luminance < 120 else "#1e1e2e"


class _AutoClassifyWorker(QThread):
    progress = Signal(int, int, str)
    thinking = Signal(str)
    status = Signal(str)
    done = Signal(int, int)
    failed = Signal(str)

    def __init__(self, items: list[dict], settings: dict, parent=None):
        super().__init__(parent)
        self._items = items
        self._settings = settings
        self._cancel = [False]

    def cancel(self) -> None:
        self._cancel[0] = True

    def cancel_and_wait(self, timeout_ms: int = 3000) -> None:
        self.cancel()
        if self.isRunning():
            self.wait(timeout_ms)

    def run(self) -> None:
        try:
            provider = self._settings["provider"]
            endpoint = self._settings["endpoint"]
            model = self._settings["model"]
            seed = self._settings["seed"]
            temperature = self._settings["temperature"]
            prompt_base = self._settings["prompt"]
            chunk_timeout = self._settings["chunk_timeout"]
            client = LMClient(base_url=endpoint, chunk_timeout=chunk_timeout, provider=provider)
            status = client.check_connection()
            if not status.ok:
                raise LMStudioError(status.message)

            registered = 0
            unclassifiable = 0
            total = len(self._items)
            for idx, item in enumerate(self._items, start=1):
                if self._cancel[0]:
                    break
                label = item["label"]
                self.progress.emit(idx, total, label)
                cats = item["categories"]
                if not cats:
                    if self._count_in_completion_dialog(item):
                        unclassifiable += 1
                    continue
                system_prompt = self._build_prompt(prompt_base, item)
                content_buf: list[str] = []
                thinking_buf: list[str] = []
                for ev_type, ev_data in client.classify_stream(
                    item["input"],
                    system_prompt,
                    model=model,
                    temperature=temperature,
                    seed=seed,
                    cancel_flag=self._cancel,
                ):
                    if self._cancel[0]:
                        break
                    if ev_type == "status":
                        self.status.emit(ev_data)
                    elif ev_type == "thinking":
                        thinking_buf.append(ev_data)
                        self.thinking.emit(ev_data)
                    elif ev_type == "content":
                        content_buf.append(ev_data)
                if self._cancel[0]:
                    break
                raw = "".join(content_buf).strip() or translation_fallback_from_thinking("".join(thinking_buf))
                parsed = self._parse_result(raw, {c["key"] for c in cats})
                if parsed is None:
                    if self._count_in_completion_dialog(item):
                        unclassifiable += 1
                    continue
                if self._register_item(item, parsed["category"], bool(parsed["is_nsfw"])):
                    if self._count_in_completion_dialog(item):
                        registered += 1
                else:
                    if self._count_in_completion_dialog(item):
                        unclassifiable += 1
            self.done.emit(registered, unclassifiable)
        except Exception as e:
            self.failed.emit(str(e))

    @staticmethod
    def _count_in_completion_dialog(item: dict) -> bool:
        return item.get("kind") != "prompt_text"

    @staticmethod
    def _build_prompt(base: str, item: dict) -> str:
        cats = "\n".join(f"- {c['key']}: {c['label']}" for c in item["categories"])
        return (
            f"{base}\n\n"
            f"Target type: {item['kind']}\n"
            f"Allowed categories:\n{cats}\n\n"
            "Return only JSON with keys category and is_nsfw. "
            "The category value must be one of the allowed keys."
        )

    @staticmethod
    def _parse_result(raw: str, allowed: set[str]) -> dict | None:
        text = (raw or "").strip()
        if not text:
            return None
        if text.startswith("```"):
            text = text.strip("`").strip()
            if text.lower().startswith("json"):
                text = text[4:].strip()
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start:end + 1]
        try:
            data = __import__("json").loads(text)
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        category = single_line_text(data.get("category"))
        if category not in allowed:
            return None
        return {"category": category, "is_nsfw": bool(data.get("is_nsfw", False))}

    @staticmethod
    def _register_item(item: dict, category: str, is_nsfw: bool) -> bool:
        kind = item["kind"]
        if kind == "tag":
            tile = item["tile"]
            name_en = single_line_text(tile.tag_name)
            name_local = single_line_text(tile.tag_local or tile.source_text)
            if not name_en or not name_local:
                return False
            row = _library_db.fetchone(
                "SELECT id FROM tags WHERE name_en=?",
                (name_en,),
            )
            if row:
                _library_db.execute(
                    """UPDATE tags
                       SET genre=?, category=?, is_nsfw=?, updated_at=CURRENT_TIMESTAMP
                       WHERE name_en=?""",
                    (category, category, 1 if is_nsfw else 0, name_en),
                )
            else:
                _library_db.execute(
                    """INSERT INTO tags
                       (name_en, name_local, genre, category, is_nav_only, is_nsfw)
                       VALUES (?, ?, ?, ?, 0, ?)""",
                    (
                        name_en,
                        name_local,
                        category,
                        category,
                        1 if is_nsfw else 0,
                    ),
                )
            tile.category = category
            return True
        if kind == "prompt_text":
            tile = item["tile"]
            source = single_line_text(tile.source_text)
            translated = single_line_text(tile.translated_text)
            if not source or not translated or exists_source_text(source):
                return False
            insert_prompt_text(
                source_text=source,
                translated_text=translated,
                display_label=single_line_text(tile.display_label or source),
                category=category,
                is_nsfw=is_nsfw,
            )
            return True
        if kind == "group":
            tile = item["tile"]
            name = unique_group_name(tile.name)
            data = tile.to_dict(include_ui_state=False)
            if isinstance(data, dict):
                data["name"] = name
            import json
            row = _library_db.fetchone("SELECT COALESCE(MAX(sort_order), 0) + 10 AS n FROM group_presets")
            _library_db.execute(
                """INSERT INTO group_presets
                   (name, group_json, sort_order, category, is_nsfw)
                   VALUES (?, ?, ?, ?, ?)""",
                (name, json.dumps(data, ensure_ascii=False), row["n"] if row else 10, category, 1 if is_nsfw else 0),
            )
            return True
        return False


class PromptEditor(QWidget):
    """
    PromptDocumentを表示・編集するウィジェット。

    Signals:
        prompt_changed(): プロンプト内容が変更されたとき
        materials_changed(): 左ペインのプロンプト/タイルDBが変更されたとき
    """

    prompt_changed      = Signal()
    materials_changed   = Signal()
    translate_requested = Signal(object, str, str)  # (BlockWidget, text, mode) — bwを直接渡して共有状態を排除
    translate_cancelled = Signal()                   # インラインパネルのキャンセル
    history_map_requested = Signal()
    lineage_jump_requested = Signal()        # 親カードクリック（親の設定をロード）
    lineage_become_root_requested = Signal()  # ✂ボタン（系譜を切って開祖になる）
    lineage_heir_prev_requested = Signal()   # 継承権者カード ◀（前の兄弟へ）
    lineage_heir_next_requested = Signal()   # 継承権者カード ▶（次の兄弟へ）
    lineage_strip_jump_requested = Signal(str, int)
    lineage_strip_goto_current_requested = Signal()
    history_stack_requested = Signal(str, int)
    history_stack_clear_requested = Signal()  # スタッククリアボタン

    def __init__(self, parent=None, *, readonly: bool = False):
        super().__init__(parent)
        self._readonly = bool(readonly)
        self._doc = PromptDocument()
        self._focused_bw: "BlockWidget | None" = None
        self._negative_enabled = True
        self._undo_stack: list[dict] = []
        self._redo_stack: list[dict] = []
        self._bulk_translate_worker: _TileTranslateWorker | None = None
        self._bulk_translate_targets: list[tuple[BlockWidget, object]] = []
        self._bulk_translate_total = 0
        self._bulk_translate_done = 0
        self._bulk_translate_failed = 0
        self._bulk_translate_cancelled = False
        self._bulk_translate_status_bw: BlockWidget | None = None
        self._bulk_translate_dialog: QDialog | None = None
        self._bulk_translate_label: QLabel | None = None
        self._bulk_translate_progress_bar: QProgressBar | None = None
        self._auto_classify_worker: _AutoClassifyWorker | None = None
        self._auto_classify_dialog: QDialog | None = None
        self._scroll_anchor_widget: QWidget | None = None
        self._scroll_anchor_y: int | None = None
        self._auto_classify_progress: QProgressBar | None = None
        self._auto_classify_label: QLabel | None = None
        self._auto_classify_thinking: QTextEdit | None = None
        self._undo_timer = QTimer()
        self._undo_timer.setSingleShot(True)
        self._undo_timer.timeout.connect(self._commit_undo_snapshot)
        self._build_ui()
        self._update_preview()
        # 初期スナップショット
        self._commit_undo_snapshot()

    # ── UI構築 ──────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._outer_scroll = QScrollArea()
        self._outer_scroll.setWidgetResizable(True)
        self._outer_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        # 上端に固定の境界線(やや明るい灰 SUBTEXT)。履歴マップ↔スクロール領域の境目を
        # 明示し、ブロックが唐突に切れて見えないようにする。
        self._outer_scroll.setStyleSheet(
            f"QScrollArea {{ border: none; border-top: 2px solid {SUBTEXT}; "
            "background: transparent; }"
        )

        self._content_widget = QWidget()
        self._content_widget.setStyleSheet("background: transparent;")
        self._content_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        content_lay = QVBoxLayout(self._content_widget)
        content_lay.setContentsMargins(6, 6, 6, 6)
        content_lay.setSpacing(16)

        # ── ポジティブ見出し ─────────────────────────────
        # スクロールエリアの外（root直下）に置き、ブロックをスクロールしても
        # 履歴マップボタン等が常に見えるようにする
        self._pos_hdr_widget = QWidget()
        pos_hdr_row = QHBoxLayout(self._pos_hdr_widget)
        pos_hdr_row.setContentsMargins(6, 6, 6, 0)
        self._pos_hdr = QLabel(tr("editor.positive_header"))
        self._pos_hdr.setFont(ui_font(1, bold=True))
        self._pos_hdr.setStyleSheet(f"color: {ACCENT}; padding: 2px 0;")
        pos_hdr_row.addWidget(self._pos_hdr)
        self._history_stack_buttons: list[QToolButton] = []

        pos_hdr_row.addStretch()

        # 絵文字アイコンはフォント設定に追従させず12pt固定（EMOJI_ICON_SS）
        _btn_ss = (
            "QToolButton {{ background: transparent; color: {fg}; "
            "border: 1px solid {fg}; border-radius: 3px; padding: 0; "
            + EMOJI_ICON_SS + " }}"
            "QToolButton:hover {{ background: {fg}; color: #1e1e2e; }}"
            "QToolButton:disabled {{ color: {dis}; border-color: {dis}; }}"
        )
        # スタッククリアボタン（ポジティブ見出しの隣）。スタックがある時だけ表示。
        self._history_stack_clear_btn = QToolButton()
        self._history_stack_clear_btn.setText("🧹")
        self._history_stack_clear_btn.setFixedSize(28, 24)
        self._history_stack_clear_btn.setToolTip(tr("editor.history_stack_clear_tooltip"))
        self._history_stack_clear_btn.setStyleSheet(_btn_ss.format(fg=SUBTEXT, dis="#555570"))
        self._history_stack_clear_btn.clicked.connect(self.history_stack_clear_requested.emit)
        self._history_stack_clear_btn.setVisible(False)
        pos_hdr_row.insertWidget(1, self._history_stack_clear_btn)

        self._history_map_btn = QToolButton()
        self._history_map_btn.setText("🗺️")  # 「マップを開く」ボタンは🗺️で統一
        self._history_map_btn.setFixedSize(28, 28)
        self._history_map_btn.setToolTip(tr("editor.history_map_tooltip"))
        self._history_map_btn.setStyleSheet(_btn_ss.format(fg=SUBTEXT, dis="#555570"))
        self._history_map_btn.clicked.connect(self.history_map_requested.emit)
        pos_hdr_row.addWidget(self._history_map_btn)

        self._undo_btn = QToolButton()
        self._undo_btn.setText("↩️")
        self._undo_btn.setFixedSize(28, 28)
        self._undo_btn.setToolTip(tr("main.btn_undo_tooltip"))
        self._undo_btn.setStyleSheet(_btn_ss.format(fg=SUBTEXT, dis="#555570"))
        self._undo_btn.clicked.connect(self._do_undo)
        pos_hdr_row.addWidget(self._undo_btn)

        self._redo_btn = QToolButton()
        self._redo_btn.setText("↪️")
        self._redo_btn.setFixedSize(28, 28)
        self._redo_btn.setToolTip(tr("main.btn_redo_tooltip"))
        self._redo_btn.setStyleSheet(_btn_ss.format(fg=SUBTEXT, dis="#555570"))
        self._redo_btn.clicked.connect(self._do_redo)
        pos_hdr_row.addWidget(self._redo_btn)

        self._pos_copy_btn = QPushButton(tr("editor.copy_btn"))
        self._pos_copy_btn.setFixedHeight(22)
        self._pos_copy_btn.setFont(ui_font(-1))
        self._pos_copy_btn.setToolTip(tr("editor.copy_positive_tooltip"))
        self._pos_copy_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {ACCENT}; "
            f"border: 1px solid {ACCENT}; border-radius: 3px; padding: 0 6px; }}"
            f"QPushButton:hover {{ background: {ACCENT}; color: #1e1e2e; }}"
        )
        self._pos_copy_btn.clicked.connect(self._copy_positive)
        pos_hdr_row.addWidget(self._pos_copy_btn)
        root.addWidget(self._pos_hdr_widget)

        from ui.history_map_dialog import HistoryMapPanel
        self.parent_child_map = HistoryMapPanel()
        if self._readonly:
            self.parent_child_map.hide()
        root.addWidget(self.parent_child_map)

        # ── 3ブロック ────────────────────────────────────
        pos = self._doc.positive
        self._bw_top    = BlockWidget(pos.top, readonly=self._readonly)
        self._bw_middle = BlockWidget(pos.middle, readonly=self._readonly)
        self._bw_bottom = BlockWidget(pos.bottom, readonly=self._readonly)

        for bw in (self._bw_top, self._bw_middle, self._bw_bottom):
            bw.block_changed.connect(self._on_prompt_changed)
            bw.block_focused.connect(lambda b=bw: self._on_block_focused(b))
            bw.translate_requested.connect(lambda text, mode, b=bw: self._on_bw_translate_requested(b, text, mode))
            bw.translate_cancel_requested.connect(lambda b=bw: self._on_bw_translate_cancel_requested(b))
            bw.bulk_translate_requested.connect(lambda b=bw: self._start_bulk_translate(b))
            bw.auto_classify_requested.connect(self._start_auto_classify)
            bw.set_bulk_translate_available_callback(self.has_bulk_translate_targets)
            bw.layout_changed.connect(self._refresh_content_size)
            bw.browser_tag_dropped.connect(self._on_browser_tag_dropped)
            content_lay.addWidget(bw)

        # ── ネガティブ見出し ─────────────────────────────
        self._neg_hdr_widget = QWidget()
        neg_hdr_row = QHBoxLayout(self._neg_hdr_widget)
        neg_hdr_row.setContentsMargins(0, 0, 0, 0)
        self._neg_hdr = QLabel(tr("editor.negative_header"))
        self._neg_hdr.setFont(ui_font(1, bold=True))
        self._neg_hdr.setStyleSheet(f"color: {RED}; padding: 2px 0;")
        neg_hdr_row.addWidget(self._neg_hdr)
        neg_hdr_row.addStretch()
        self._neg_copy_btn = QPushButton(tr("editor.copy_btn"))
        self._neg_copy_btn.setFixedHeight(22)
        self._neg_copy_btn.setFont(ui_font(-1))
        self._neg_copy_btn.setToolTip(tr("editor.copy_negative_tooltip"))
        self._neg_copy_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {RED}; "
            f"border: 1px solid {RED}; border-radius: 3px; padding: 0 6px; }}"
            f"QPushButton:hover {{ background: {RED}; color: #1e1e2e; }}"
        )
        self._neg_copy_btn.clicked.connect(self._copy_negative)
        neg_hdr_row.addWidget(self._neg_copy_btn)
        content_lay.addWidget(self._neg_hdr_widget)

        # ── ネガティブブロック ───────────────────────────
        neg = self._doc.negative
        neg.middle.label = tr("editor.negative_label")
        self._bw_neg = BlockWidget(neg.middle, readonly=self._readonly)
        self._bw_neg.block_changed.connect(self._on_prompt_changed)
        self._bw_neg.block_focused.connect(lambda b=self._bw_neg: self._on_block_focused(b))
        self._bw_neg.translate_requested.connect(lambda text, mode: self._on_bw_translate_requested(self._bw_neg, text, mode))
        self._bw_neg.translate_cancel_requested.connect(lambda: self._on_bw_translate_cancel_requested(self._bw_neg))
        self._bw_neg.bulk_translate_requested.connect(lambda: self._start_bulk_translate(self._bw_neg))
        self._bw_neg.auto_classify_requested.connect(self._start_auto_classify)
        self._bw_neg.set_bulk_translate_available_callback(self.has_bulk_translate_targets)
        self._bw_neg.layout_changed.connect(self._refresh_content_size)
        self._bw_neg.browser_tag_dropped.connect(self._on_browser_tag_dropped)
        content_lay.addWidget(self._bw_neg)

        # ── メモ欄 ───────────────────────────────────────
        memo_hdr_row = QHBoxLayout()
        memo_hdr_row.setContentsMargins(0, 4, 0, 0)
        self._memo_hdr = QLabel(tr("editor.memo_label"))
        self._memo_hdr.setFont(ui_font(-1, bold=True))
        self._memo_hdr.setStyleSheet(f"color: {SUBTEXT}; padding: 2px 0;")
        memo_hdr_row.addWidget(self._memo_hdr)
        memo_hdr_row.addStretch()
        content_lay.addLayout(memo_hdr_row)

        self._memo_edit = QTextEdit()
        self._memo_edit.setPlaceholderText(tr("editor.memo_placeholder"))
        self._memo_edit.setReadOnly(self._readonly)
        self._memo_edit.setFont(ui_font())
        self._memo_edit.setFixedHeight(72)
        self._memo_edit.setStyleSheet(
            f"QTextEdit {{ background: {SURFACE1}; color: {TEXT}; "
            f"border: 1px solid {SURFACE2}; border-radius: 4px; padding: 2px; }}"
        )
        content_lay.addWidget(self._memo_edit)

        content_lay.addStretch(1)

        self._outer_scroll.setWidget(self._content_widget)

        # ── コンパイル済みプレビュー ──────────────────────
        self._prev_frame = QFrame()
        self._prev_frame.setFrameShape(QFrame.Shape.StyledPanel)
        self._prev_frame.setStyleSheet(
            f"QFrame {{ background-color: {SURFACE1}; "
            f"border: none; border-top: 1px solid {SURFACE2}; }}"
        )
        prev_lay = QVBoxLayout(self._prev_frame)
        prev_lay.setContentsMargins(6, 4, 6, 4)
        prev_lay.setSpacing(3)

        # タイトル行（折りたたみボタン + ラベル + コピーボタン）
        title_row = QHBoxLayout()
        title_row.setSpacing(4)

        self._preview_toggle_btn = QToolButton()
        self._preview_toggle_btn.setText("▼")
        self._preview_toggle_btn.setFixedSize(16, 16)
        self._preview_toggle_btn.setStyleSheet(
            f"QToolButton {{ background: transparent; color: {SUBTEXT}; "
            f"border: none; padding: 0; font-size: 9px; }}"
            f"QToolButton:hover {{ color: {TEXT}; }}"
        )
        self._preview_toggle_btn.clicked.connect(self._toggle_preview_collapse)
        title_row.addWidget(self._preview_toggle_btn)

        self._prev_title = QLabel(tr("editor.preview_title"))
        self._prev_title.setFont(ui_font(-1))
        self._prev_title.setStyleSheet(
            f"color: {SUBTEXT}; border: none; background: transparent;"
        )
        title_row.addWidget(self._prev_title, stretch=1)

        from PySide6.QtWidgets import QApplication
        self._preview_local_copy_btn = QPushButton(tr("editor.copy_local_btn"))
        self._preview_local_copy_btn.setFixedHeight(18)
        self._preview_local_copy_btn.setFont(ui_font(-2))
        self._preview_local_copy_btn.setToolTip(tr("editor.copy_local_compiled_tooltip"))
        self._preview_local_copy_btn.setStyleSheet(
            f"QPushButton {{ background: {SURFACE1}; color: {SUBTEXT}; "
            f"border: 1px solid {SURFACE2}; border-radius: 3px; padding: 0 4px; }}"
            f"QPushButton:hover {{ background: {SURFACE2}; color: {TEXT}; }}"
        )
        self._preview_local_copy_btn.clicked.connect(self._copy_preview_local)
        title_row.addWidget(self._preview_local_copy_btn)

        self._preview_copy_btn = QPushButton(tr("editor.copy_btn"))
        self._preview_copy_btn.setFixedHeight(18)
        self._preview_copy_btn.setFont(ui_font(-2))
        self._preview_copy_btn.setToolTip(tr("editor.copy_compiled_tooltip"))
        self._preview_copy_btn.setStyleSheet(
            f"QPushButton {{ background: {SURFACE1}; color: {SUBTEXT}; "
            f"border: 1px solid {SURFACE2}; border-radius: 3px; padding: 0; }}"
            f"QPushButton:hover {{ background: {SURFACE2}; color: {TEXT}; }}"
        )
        self._preview_copy_btn.clicked.connect(self._copy_preview)
        title_row.addWidget(self._preview_copy_btn)
        prev_lay.addLayout(title_row)

        # テキストエリア（読み取り専用・スクロール可能）
        _base = QApplication.instance().font().pointSize() if QApplication.instance() else 10
        self._preview_edit = QTextEdit()
        self._preview_edit.setReadOnly(True)
        self._preview_edit.setFont(QFont("Consolas", max(7, _base - 1)))
        self._preview_edit.setMinimumHeight(40)
        self._preview_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._preview_edit.setStyleSheet(
            f"QTextEdit {{ background: transparent; color: {TEXT}; "
            f"border: none; padding: 0; }}"
            f"QScrollBar:vertical {{ width: 8px; background: {SURFACE1}; }}"
            f"QScrollBar::handle:vertical {{ background: {SURFACE2}; border-radius: 4px; }}"
        )
        self._preview_edit.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self._preview_edit.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        prev_lay.addWidget(self._preview_edit)

        # ── スプリッター（上: スクロールエリア / 下: プレビュー）──
        self._splitter = QSplitter(Qt.Orientation.Vertical)
        self._splitter.setChildrenCollapsible(False)
        self._splitter.setStyleSheet(
            f"QSplitter::handle:vertical {{ background: {SURFACE2}; height: 3px; }}"
        )
        self._splitter.addWidget(self._outer_scroll)
        self._splitter.addWidget(self._prev_frame)
        self._splitter.setStretchFactor(0, 1)
        self._splitter.setStretchFactor(1, 0)
        self._splitter.splitterMoved.connect(self._on_splitter_moved)
        root.addWidget(self._splitter)

        self._preview_collapsed = False
        self._saved_preview_height = 120
        self._load_preview_state()
        self._apply_readonly_state()

    # ── プレビュー折りたたみ ────────────────────────────

    def _toggle_preview_collapse(self) -> None:
        if not self._preview_collapsed:
            sz = self._splitter.sizes()
            if len(sz) > 1 and sz[1] >= 60:
                self._saved_preview_height = sz[1]
        self._preview_collapsed = not self._preview_collapsed
        self._apply_preview_collapse_state()
        _app_db.execute(
            "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
            ("preview_collapsed", "1" if self._preview_collapsed else "0"),
        )

    def _apply_preview_collapse_state(self) -> None:
        collapsed = self._preview_collapsed
        self._preview_edit.setVisible(not collapsed)
        self._preview_toggle_btn.setText("▶" if collapsed else "▼")
        if collapsed:
            # ヘッダー行のみ残して即座に最小化
            self._prev_frame.setMaximumHeight(28)
            def _collapse_now():
                total = sum(self._splitter.sizes())
                if total > 28:
                    self._splitter.setSizes([total - 28, 28])
            QTimer.singleShot(0, _collapse_now)
        else:
            self._prev_frame.setMaximumHeight(16777215)
            h = self._saved_preview_height
            def _restore_size():
                total = sum(self._splitter.sizes())
                if total > h:
                    self._splitter.setSizes([total - h, h])
            QTimer.singleShot(0, _restore_size)

    def _load_preview_state(self) -> None:
        row = _app_db.fetchone("SELECT value FROM app_settings WHERE key='preview_height'")
        if row:
            try:
                v = int(row["value"])
                if v >= 40:
                    self._saved_preview_height = v
            except (TypeError, ValueError):
                pass
        row = _app_db.fetchone("SELECT value FROM app_settings WHERE key='preview_collapsed'")
        if row and row["value"] == "1":
            self._preview_collapsed = True
            QTimer.singleShot(0, self._apply_preview_collapse_state)
        else:
            # 初期サイズを適用（展開時のデフォルト高さ）
            h = self._saved_preview_height
            def _set_initial():
                total = sum(self._splitter.sizes())
                if total > h:
                    self._splitter.setSizes([total - h, h])
            QTimer.singleShot(0, _set_initial)

    def _on_splitter_moved(self) -> None:
        if not self._preview_collapsed:
            sz = self._splitter.sizes()
            if len(sz) > 1 and sz[1] >= 40:
                self._saved_preview_height = sz[1]
                _app_db.execute(
                    "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
                    ("preview_height", str(sz[1])),
                )

    # ── サイズ更新 ──────────────────────────────────────

    def refresh_layout(self) -> None:
        """外部からレイアウト再計算を要求する（翻訳パネル表示切替後などに使用）。"""
        QTimer.singleShot(0, self._refresh_content_size)

    def capture_block_scroll_anchor(self, bw: "BlockWidget | None") -> None:
        """翻訳完了時など、タイル追加前の入力欄位置を維持するためのアンカーを保存する。"""
        self._scroll_anchor_widget = None
        self._scroll_anchor_y = None
        if bw is None:
            return
        try:
            iw = bw._input
            vbar = self._outer_scroll.verticalScrollBar()
            self._scroll_anchor_y = iw.mapTo(self._content_widget, QPoint(0, 0)).y() - vbar.value()
            self._scroll_anchor_widget = iw
        except Exception:
            self._scroll_anchor_widget = None
            self._scroll_anchor_y = None

    def restore_block_scroll_anchor(self) -> None:
        widget = self._scroll_anchor_widget
        anchor_y = self._scroll_anchor_y
        self._scroll_anchor_widget = None
        self._scroll_anchor_y = None
        if widget is None or anchor_y is None:
            return

        def _restore() -> None:
            try:
                new_y = widget.mapTo(self._content_widget, QPoint(0, 0)).y()
                vbar = self._outer_scroll.verticalScrollBar()
                vbar.setValue(max(0, new_y - anchor_y))
            except Exception:
                pass

        QTimer.singleShot(0, _restore)
        QTimer.singleShot(20, _restore)

    def retranslate_and_restyle(self) -> None:
        """言語・テーマ・フォント設定を現在のエディタへ即時反映する。"""
        self._pos_hdr.setText(tr("editor.positive_header"))
        self._neg_hdr.setText(tr("editor.negative_header"))
        self._memo_hdr.setText(tr("editor.memo_label"))
        self._prev_title.setText(tr("editor.preview_title"))
        self._history_map_btn.setToolTip(tr("editor.history_map_tooltip"))
        self._undo_btn.setToolTip(tr("main.btn_undo_tooltip"))
        self._redo_btn.setToolTip(tr("main.btn_redo_tooltip"))
        self._pos_copy_btn.setText(tr("editor.copy_btn"))
        self._pos_copy_btn.setToolTip(tr("editor.copy_positive_tooltip"))
        self._neg_copy_btn.setText(tr("editor.copy_btn"))
        self._neg_copy_btn.setToolTip(tr("editor.copy_negative_tooltip"))
        self._preview_local_copy_btn.setText(tr("editor.copy_local_btn"))
        self._preview_local_copy_btn.setToolTip(tr("editor.copy_local_compiled_tooltip"))
        self._preview_copy_btn.setText(tr("editor.copy_btn"))
        self._preview_copy_btn.setToolTip(tr("editor.copy_compiled_tooltip"))
        self._memo_edit.setPlaceholderText(tr("editor.memo_placeholder"))

        self._pos_hdr.setFont(ui_font(1, bold=True))
        self._neg_hdr.setFont(ui_font(1, bold=True))
        self._memo_hdr.setFont(ui_font(-1, bold=True))
        self._pos_copy_btn.setFont(ui_font(-1))
        self._neg_copy_btn.setFont(ui_font(-1))
        self._preview_local_copy_btn.setFont(ui_font(-2))
        self._preview_copy_btn.setFont(ui_font(-2))
        self._memo_edit.setFont(ui_font())

        self._pos_hdr.setStyleSheet(f"color: {ACCENT}; padding: 2px 0;")
        self._neg_hdr.setStyleSheet(f"color: {RED}; padding: 2px 0;")
        self._memo_hdr.setStyleSheet(f"color: {SUBTEXT}; padding: 2px 0;")
        self._pos_copy_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {ACCENT}; "
            f"border: 1px solid {ACCENT}; border-radius: 3px; padding: 0 6px; }}"
            f"QPushButton:hover {{ background: {ACCENT}; color: #1e1e2e; }}"
        )
        self._neg_copy_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {RED}; "
            f"border: 1px solid {RED}; border-radius: 3px; padding: 0 6px; }}"
            f"QPushButton:hover {{ background: {RED}; color: #1e1e2e; }}"
        )
        self._prev_frame.setStyleSheet(
            f"QFrame {{ background-color: {SURFACE1}; "
            f"border: none; border-top: 1px solid {SURFACE2}; }}"
        )
        self._preview_toggle_btn.setStyleSheet(
            f"QToolButton {{ background: transparent; color: {SUBTEXT}; "
            f"border: none; padding: 0; font-size: 9px; }}"
            f"QToolButton:hover {{ color: {TEXT}; }}"
        )
        self._prev_title.setStyleSheet(
            f"color: {SUBTEXT}; border: none; background: transparent;"
        )
        self._preview_local_copy_btn.setStyleSheet(
            f"QPushButton {{ background: {SURFACE1}; color: {SUBTEXT}; "
            f"border: 1px solid {SURFACE2}; border-radius: 3px; padding: 0 4px; }}"
            f"QPushButton:hover {{ background: {SURFACE2}; color: {TEXT}; }}"
        )
        self._preview_copy_btn.setStyleSheet(
            f"QPushButton {{ background: {SURFACE1}; color: {SUBTEXT}; "
            f"border: 1px solid {SURFACE2}; border-radius: 3px; padding: 0; }}"
            f"QPushButton:hover {{ background: {SURFACE2}; color: {TEXT}; }}"
        )
        self._memo_edit.setStyleSheet(
            f"QTextEdit {{ background: {SURFACE1}; color: {TEXT}; "
            f"border: 1px solid {SURFACE2}; border-radius: 4px; padding: 2px; }}"
        )
        if hasattr(self.parent_child_map, "retranslate_and_restyle"):
            self.parent_child_map.retranslate_and_restyle()

        self._doc.negative.middle.label = tr("editor.negative_label")
        for bw in self.all_block_widgets():
            if bw.block.block_type == "negative":
                bw.block.label = tr("editor.negative_label")
            bw.reload()
        self._update_preview()
        self.refresh_layout()

    def set_history_stack_buttons(self, items: list[dict]) -> None:
        row = self._pos_hdr_widget.layout()
        if row is None:
            return
        for btn in self._history_stack_buttons:
            row.removeWidget(btn)
            btn.deleteLater()
        self._history_stack_buttons = []
        # クリアボタンはスタックがある時だけ表示。チップはその右隣に並べる。
        self._history_stack_clear_btn.setVisible(bool(items))
        insert_at = 2
        for item in items[:5]:
            db = str(item.get("history_db") or "")
            gid = int(item.get("history_id") or 0)
            color = str(item.get("color") or SURFACE2)
            fg = _readable_text_color(color)
            btn = QToolButton()
            btn.setText(f"#{gid}")
            btn.setFixedHeight(24)
            btn.setToolTip(tr("history_map.stack_button_tooltip", n=gid))
            btn.setStyleSheet(
                f"QToolButton {{ background: {color}; color: {fg}; border: 1px solid {ACCENT}; "
                f"border-radius: 3px; padding: 0 6px; }}"
                f"QToolButton:hover {{ border-color: {TEXT}; }}"
            )
            btn.clicked.connect(lambda _=False, hdb=db, hid=gid: self.history_stack_requested.emit(hdb, hid))
            row.insertWidget(insert_at, btn)
            insert_at += 1
            self._history_stack_buttons.append(btn)

    def _refresh_content_size(self) -> None:
        lay = self._content_widget.layout()
        lay.activate()

        vp  = self._outer_scroll.viewport()
        vp_w, vp_h = vp.width(), vp.height()
        if vp_w <= 0:
            return

        need_h    = lay.sizeHint().height()
        content_h = max(need_h, vp_h)
        self._content_widget.resize(vp_w, content_h)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        QTimer.singleShot(0, self._refresh_content_size)

    # ── フォーカス追跡 ──────────────────────────────────

    def _on_block_focused(self, bw: "BlockWidget") -> None:
        self._focused_bw = bw

    def _on_bw_translate_requested(self, bw: "BlockWidget", text: str, mode: str) -> None:
        """翻訳リクエスト元の bw を直接シグナルに乗せて上位へ emit する。"""
        self._focused_bw = bw  # フォーカス状態も更新しておく
        self.translate_requested.emit(bw, text, mode)

    def _on_bw_translate_cancel_requested(self, bw: "BlockWidget") -> None:
        if self._bulk_translate_worker is not None:
            self._cancel_bulk_translate()
            return
        self.translate_cancelled.emit()

    def _visible_block_widgets(self) -> list["BlockWidget"]:
        widgets = [self._bw_top, self._bw_middle, self._bw_bottom]
        if self._negative_enabled:
            widgets.append(self._bw_neg)
        return [bw for bw in widgets if bw.isVisible()]

    def _collect_bulk_translate_targets(self) -> list[tuple["BlockWidget", object]]:
        targets: list[tuple[BlockWidget, object]] = []
        for bw in self._visible_block_widgets():
            if bw.is_locked():
                continue
            for tile in bw.untranslated_tiles():
                targets.append((bw, tile))
        return targets

    def has_bulk_translate_targets(self) -> bool:
        if self._bulk_translate_worker is not None:
            return False
        return bool(self._collect_bulk_translate_targets())

    def _start_bulk_translate(self, status_bw: "BlockWidget") -> None:
        if self._bulk_translate_worker is not None:
            return
        targets = self._collect_bulk_translate_targets()
        if not targets:
            QMessageBox.information(
                self,
                tr("block.bulk_translate_confirm_title"),
                tr("block.ctx_bulk_translate_disabled_empty"),
            )
            return
        if not is_translation_model_configured():
            QMessageBox.warning(
                self,
                tr("block.bulk_translate_confirm_title"),
                translation_model_missing_message(),
            )
            return

        # ── 開始確認ダイアログ ──────────────────────────────
        reply = QMessageBox.question(
            self,
            tr("block.bulk_translate_confirm_title"),
            tr("block.bulk_translate_confirm_msg", count=len(targets)),
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Ok,
        )
        if reply != QMessageBox.StandardButton.Ok:
            return

        self._focused_bw = status_bw
        self._bulk_translate_status_bw = status_bw
        self._bulk_translate_targets = targets
        self._bulk_translate_total = len(targets)
        self._bulk_translate_done = 0
        self._bulk_translate_failed = 0
        self._bulk_translate_cancelled = False

        for bw in self._visible_block_widgets():
            bw.set_global_translating(True)

        # ── 進捗ダイアログ構築（モーダル・Xボタン無し） ────
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("block.bulk_translate_dialog_title"))
        dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
        dlg.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.CustomizeWindowHint
        )
        dlg.setStyleSheet(f"QDialog {{ background-color: {SURFACE0}; }}")
        dlg.resize(380, 130)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(16, 16, 16, 12)
        lay.setSpacing(10)

        lbl = QLabel(tr("block.bulk_translate_progress", done=0, total=len(targets)))
        lbl.setStyleSheet(f"color: {TEXT}; background: transparent;")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(lbl)

        bar = QProgressBar()
        bar.setRange(0, len(targets))
        bar.setValue(0)
        lay.addWidget(bar)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        btns.rejected.connect(self._cancel_bulk_translate)
        lay.addWidget(btns)

        self._bulk_translate_dialog = dlg
        self._bulk_translate_label = lbl
        self._bulk_translate_progress_bar = bar

        # ダイアログの event loop が始まってから最初のアイテムを開始
        QTimer.singleShot(0, self._run_next_bulk_translate_item)
        dlg.exec()

        # ── exec() 復帰後: 結果ダイアログ ─────────────────
        done = self._bulk_translate_done
        total = self._bulk_translate_total
        if self._bulk_translate_cancelled:
            QMessageBox.information(
                self,
                tr("block.bulk_translate_result_title"),
                tr("block.bulk_translate_cancelled", done=done, total=total),
            )
        else:
            QMessageBox.information(
                self,
                tr("block.bulk_translate_result_title"),
                tr("block.bulk_translate_done", done=done, total=total),
            )

        # 最終クリーンアップ
        self._bulk_translate_total = 0
        self._bulk_translate_done = 0
        self._bulk_translate_failed = 0
        self._bulk_translate_cancelled = False

    def _run_next_bulk_translate_item(self) -> None:
        if self._bulk_translate_cancelled:
            self._finish_bulk_translate(cancelled=True)
            return
        if not self._bulk_translate_targets:
            self._finish_bulk_translate(cancelled=False)
            return

        bw, tile = self._bulk_translate_targets.pop(0)
        source = self._bulk_translate_source_text(tile)
        if not source:
            self._bulk_translate_failed += 1
            QTimer.singleShot(0, self._run_next_bulk_translate_item)
            return

        current = self._bulk_translate_done + self._bulk_translate_failed + 1
        if self._bulk_translate_label is not None:
            self._bulk_translate_label.setText(
                tr("block.bulk_translate_progress", done=current, total=self._bulk_translate_total)
            )
        if self._bulk_translate_progress_bar is not None:
            self._bulk_translate_progress_bar.setValue(current - 1)

        worker = _TileTranslateWorker(source, "natural", True, self)
        self._bulk_translate_worker = worker
        # thinking_chunk は一括翻訳中は使わない（ダイアログに溢れるため）
        worker.translation_done.connect(lambda text, b=bw, t=tile: self._on_bulk_translate_done(b, t, text))
        worker.failed.connect(lambda msg, b=bw, t=tile: self._on_bulk_translate_failed(b, t, msg))
        worker.start()

    @staticmethod
    def _bulk_translate_source_text(tile) -> str:
        if isinstance(tile, TagTile):
            return single_line_text(tile.tag_name).strip()
        if isinstance(tile, NaturalTextTile):
            return single_line_text(tile.translated_text or tile.text).strip()
        return ""

    def _apply_bulk_translation(self, bw: "BlockWidget", tile, text: str) -> bool:
        text = single_line_text(text).strip()
        if not text:
            return False
        if isinstance(tile, TagTile):
            tile.tag_local = text
            _library_db.execute(
                "INSERT OR REPLACE INTO tag_labels (tag_name, label, updated_at)"
                " VALUES (?, ?, CURRENT_TIMESTAMP)",
                (tile.tag_name, text),
            )
        elif isinstance(tile, NaturalTextTile):
            tile.source_text = text
        else:
            return False

        bw._refresh_tiles()
        bw.block_changed.emit()
        return True

    def _on_bulk_translate_done(self, bw: "BlockWidget", tile, text: str) -> None:
        self._bulk_translate_worker = None
        if self._apply_bulk_translation(bw, tile, text):
            self._bulk_translate_done += 1
        else:
            self._bulk_translate_failed += 1
        done = self._bulk_translate_done + self._bulk_translate_failed
        if self._bulk_translate_progress_bar is not None:
            self._bulk_translate_progress_bar.setValue(done)
        QTimer.singleShot(0, self._run_next_bulk_translate_item)

    def _on_bulk_translate_failed(self, bw: "BlockWidget", tile, msg: str) -> None:
        self._bulk_translate_worker = None
        self._bulk_translate_failed += 1
        done = self._bulk_translate_done + self._bulk_translate_failed
        if self._bulk_translate_progress_bar is not None:
            self._bulk_translate_progress_bar.setValue(done)
        QTimer.singleShot(0, self._run_next_bulk_translate_item)

    def _cancel_bulk_translate(self) -> None:
        self._bulk_translate_cancelled = True
        worker = self._bulk_translate_worker
        self._bulk_translate_targets = []
        if worker is not None and worker.isRunning():
            worker.cancel_and_wait()
        self._bulk_translate_worker = None
        self._finish_bulk_translate(cancelled=True)

    def _finish_bulk_translate(self, cancelled: bool) -> None:
        self._bulk_translate_cancelled = cancelled
        status_bw = self._bulk_translate_status_bw
        for bw in self._visible_block_widgets():
            bw.set_global_translating(False)
        # 翻訳パネル (thinking 領域) を閉じる
        if status_bw is not None:
            status_bw.hide_translate_panel()
        # 進捗ダイアログを閉じる → _start_bulk_translate の exec() が返る
        if self._bulk_translate_dialog is not None:
            self._bulk_translate_dialog.accept()
            self._bulk_translate_dialog = None
        self._bulk_translate_label = None
        self._bulk_translate_progress_bar = None
        self._bulk_translate_worker = None
        self._bulk_translate_targets = []
        self._bulk_translate_status_bw = None

    # ── LLM自動分類・登録 ───────────────────────────────

    def _start_auto_classify(self) -> None:
        if self._auto_classify_worker is not None:
            return
        items = self._collect_auto_classify_items()
        if not items:
            QMessageBox.information(self, tr("auto_classify.title"), tr("auto_classify.no_targets"))
            return
        if QMessageBox.question(
            self,
            tr("auto_classify.title"),
            tr("auto_classify.confirm", count=len(items)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        settings = self._auto_classify_settings()
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("auto_classify.title"))
        dlg.setModal(True)
        dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
        lay = QVBoxLayout(dlg)
        self._auto_classify_label = QLabel(tr("auto_classify.starting"))
        self._auto_classify_label.setStyleSheet(f"color: {TEXT};")
        lay.addWidget(self._auto_classify_label)
        self._auto_classify_progress = QProgressBar()
        self._auto_classify_progress.setRange(0, len(items))
        lay.addWidget(self._auto_classify_progress)
        self._auto_classify_thinking = QTextEdit()
        self._auto_classify_thinking.setReadOnly(True)
        self._auto_classify_thinking.setFixedHeight(140)
        self._auto_classify_thinking.setStyleSheet(
            f"QTextEdit {{ background: {SURFACE1}; color: {SUBTEXT}; border: 1px solid {SURFACE2}; }}"
        )
        lay.addWidget(self._auto_classify_thinking)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        lay.addWidget(btns)
        worker = _AutoClassifyWorker(items, settings, self)
        self._auto_classify_worker = worker
        self._auto_classify_dialog = dlg
        btns.rejected.connect(self._cancel_auto_classify)
        worker.progress.connect(self._on_auto_classify_progress)
        worker.status.connect(self._on_auto_classify_status)
        worker.thinking.connect(self._on_auto_classify_thinking)
        worker.done.connect(self._on_auto_classify_done)
        worker.failed.connect(self._on_auto_classify_failed)
        worker.start()
        dlg.exec()

    def _auto_classify_settings(self) -> dict:
        provider = "lmstudio"
        endpoint = _get_setting("lm_endpoint", "http://localhost:1234")
        model = _get_setting("lm_classify_model", "") or _get_setting("lm_translate_model", "")
        seed = lm_seed()
        temperature = lm_temperature()
        try:
            chunk_timeout = float(_get_setting("lm_chunk_timeout", "60"))
        except ValueError:
            chunk_timeout = 60.0
        prompt = _get_setting("lm_classify_prompt", "") or DEFAULT_CLASSIFY_PROMPT
        return {
            "provider": provider,
            "endpoint": endpoint,
            "model": model,
            "seed": seed,
            "temperature": temperature,
            "chunk_timeout": chunk_timeout,
            "prompt": prompt,
        }

    def _collect_auto_classify_items(self) -> list[dict]:
        tag_categories = self._category_rows(
            "SELECT key, COALESCE(NULLIF(label,''), key) AS label FROM tag_categories "
            "WHERE is_tag_genre=1 ORDER BY sort_order, key",
        )
        prompt_categories = self._category_rows(
            "SELECT key, label FROM prompt_text_categories ORDER BY sort_order, label"
        )
        group_categories = self._category_rows(
            "SELECT key, label FROM group_categories ORDER BY sort_order, label"
        )
        items: list[dict] = []
        seen_tags: set[str] = set()
        seen_prompt_sources: set[str] = set()
        seen_groups: set[int] = set()

        def visit(tile) -> None:
            if isinstance(tile, TagTile):
                name = single_line_text(tile.tag_name)
                local = single_line_text(tile.tag_local or tile.source_text)
                if not name or name in seen_tags:
                    return
                if not local:
                    return
                seen_tags.add(name)
                row = _library_db.fetchone(
                    "SELECT 1 FROM tags WHERE name_en=? LIMIT 1",
                    (name,),
                )
                if row:
                    return
                items.append({
                    "kind": "tag",
                    "tile": tile,
                    "label": local,
                    "input": local,
                    "categories": tag_categories,
                })
            elif isinstance(tile, NaturalTextTile):
                source = single_line_text(tile.source_text)
                translated = single_line_text(tile.translated_text)
                if not source or not translated:
                    return
                if not source or source in seen_prompt_sources:
                    return
                seen_prompt_sources.add(source)
                if exists_source_text(source):
                    return
                items.append({
                    "kind": "prompt_text",
                    "tile": tile,
                    "label": single_line_text(tile.display_label or source[:40]) or source[:40],
                    "input": source,
                    "categories": prompt_categories,
                })
            elif isinstance(tile, GroupTile):
                if id(tile) not in seen_groups:
                    seen_groups.add(id(tile))
                    items.append({
                        "kind": "group",
                        "tile": tile,
                        "label": single_line_text(tile.name),
                        "input": single_line_text(tile.name),
                        "categories": group_categories,
                    })
                for child in tile.tiles:
                    visit(child)

        widgets = [self._bw_top, self._bw_middle, self._bw_bottom]
        if self._negative_enabled:
            widgets.append(self._bw_neg)
        for bw in widgets:
            for tile in bw.block.tiles:
                visit(tile)
        return items

    @staticmethod
    def _category_rows(sql: str, params: tuple = ()) -> list[dict]:
        return [{"key": r["key"], "label": r["label"] or r["key"]} for r in _library_db.fetchall(sql, params)]

    def _on_auto_classify_progress(self, current: int, total: int, label: str) -> None:
        if self._auto_classify_progress is not None:
            self._auto_classify_progress.setValue(current - 1)
            self._auto_classify_progress.setMaximum(total)
        if self._auto_classify_label is not None:
            self._auto_classify_label.setText(tr("auto_classify.progress", current=current, total=total, label=label))
        if self._auto_classify_thinking is not None:
            self._auto_classify_thinking.clear()

    def _on_auto_classify_status(self, text: str) -> None:
        if self._auto_classify_label is not None and text:
            self._auto_classify_label.setText(text)

    def _on_auto_classify_thinking(self, text: str) -> None:
        if self._auto_classify_thinking is not None:
            self._auto_classify_thinking.insertPlainText(text)

    def _cancel_auto_classify(self) -> None:
        worker = self._auto_classify_worker
        if worker is not None:
            worker.cancel()

    def _on_auto_classify_done(self, registered: int, unclassifiable: int) -> None:
        worker = self._auto_classify_worker
        if worker is not None:
            worker.wait(100)
        self._auto_classify_worker = None
        if self._auto_classify_progress is not None:
            self._auto_classify_progress.setValue(self._auto_classify_progress.maximum())
        for bw in self._visible_block_widgets():
            bw._refresh_tiles()
        self._on_prompt_changed()
        try:
            from ui.group_preset_browser import GroupPresetBrowser
            GroupPresetBrowser.notify_presets_changed()
        except Exception:
            pass
        self.materials_changed.emit()
        dlg = self._auto_classify_dialog
        if dlg is not None:
            dlg.accept()
        self._auto_classify_dialog = None
        QMessageBox.information(
            self,
            tr("auto_classify.title"),
            tr("auto_classify.done", registered=registered, unclassifiable=unclassifiable),
        )

    def _on_auto_classify_failed(self, message: str) -> None:
        self._auto_classify_worker = None
        dlg = self._auto_classify_dialog
        if dlg is not None:
            dlg.reject()
        self._auto_classify_dialog = None
        QMessageBox.warning(self, tr("auto_classify.title"), tr("auto_classify.failed", error=message))

    # ── スロット ────────────────────────────────────────

    def _on_prompt_changed(self) -> None:
        # スクロールアンカーは「入力欄からのタイル追加」時のみ適用する。
        # D&D・削除・グループ操作など他の変更では発動しない。
        apply_anchor = (
            self._focused_bw is not None
            and getattr(self._focused_bw, "_from_input", False)
        )
        anchor_y: int | None = None
        anchor_widget = None
        if apply_anchor:
            try:
                iw = self._focused_bw._input
                vbar = self._outer_scroll.verticalScrollBar()
                anchor_y = iw.mapTo(self._content_widget, QPoint(0, 0)).y() - vbar.value()
                anchor_widget = iw
            except Exception:
                pass

        self._refresh_content_size()
        self._update_preview()
        self.prompt_changed.emit()
        self._undo_timer.start(500)

        # レイアウト確定後、入力欄が同じビューポート位置に留まるようスクロール補正
        if anchor_y is not None and anchor_widget is not None:
            def _restore_anchor():
                try:
                    new_y = anchor_widget.mapTo(self._content_widget, QPoint(0, 0)).y()
                    vbar = self._outer_scroll.verticalScrollBar()
                    vbar.setValue(max(0, new_y - anchor_y))
                except Exception:
                    pass
            QTimer.singleShot(0, _restore_anchor)

    def _on_browser_tag_dropped(self, tile) -> None:
        """TagBrowser から追加したタグが既に中央ペインにあれば柔らかく示す。"""
        tag_name = (getattr(tile, "tag_name", "") or "").strip()
        if not tag_name:
            return

        matches: list[tuple[QWidget, list[object]]] = []
        for bw in self.all_block_widgets():
            if not bw.isVisible():
                continue
            matches.extend(bw.find_tag_matches(tag_name, exclude_tile=tile))

        if not matches:
            return

        for _, group_chain in matches:
            for group_widget in group_chain:
                if hasattr(group_widget, "ensure_expanded"):
                    group_widget.ensure_expanded()

        self._refresh_content_size()
        self._content_widget.updateGeometry()

        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()

        hinted_widgets: list[QWidget] = []
        new_widget = self._find_widget_for_tile(tile)
        if new_widget is not None:
            hinted_widgets.append(new_widget)
        for widget, _ in matches:
            if widget not in hinted_widgets:
                hinted_widgets.append(widget)

        for widget in hinted_widgets:
            if hasattr(widget, "play_duplicate_hint"):
                widget.play_duplicate_hint()

    def _find_widget_for_tile(self, target_tile) -> QWidget | None:
        for bw in self.all_block_widgets():
            found = bw.find_widget_for_tile(target_tile)
            if found is not None:
                return found
        return None

    def _update_preview(self) -> None:
        pos_text, neg_text = self._doc.compile_for_preview()
        if not self._negative_enabled:
            neg_text = ""
        self._set_preview_text(pos_text, neg_text)

    def set_preview_text(self, pos: str, neg: str) -> None:
        """生成時など外部から直接プレビューを更新する（追加コンパイルなし）。"""
        if not self._negative_enabled:
            neg = ""
        self._set_preview_text(pos, neg)

    def _set_preview_text(self, pos: str, neg: str) -> None:
        preview = pos
        if neg:
            preview += f"\n[NEG] {neg}"
        self._preview_edit.setPlainText(preview or tr("editor.preview_empty"))

    def _copy_preview(self) -> None:
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(self._preview_edit.toPlainText())
        self._show_copy_done(self._preview_copy_btn)

    def _copy_preview_local(self) -> None:
        from PySide6.QtWidgets import QApplication
        pos, neg = self._doc.compile_local_for_preview()
        if not self._negative_enabled:
            neg = ""
        text = pos
        if neg:
            text += f"\n[NEG] {neg}"
        QApplication.clipboard().setText(text)
        self._show_copy_done(self._preview_local_copy_btn)

    @staticmethod
    def _show_copy_done(widget: QWidget) -> None:
        QToolTip.showText(
            widget.mapToGlobal(QPoint(0, widget.height())),
            tr("editor.copy_done"),
            widget,
        )

    # ── UNDO スナップショット ────────────────────────────

    def _snapshot_doc(self) -> dict:
        def _ser(block):
            return {"tiles": [t.to_dict() for t in block.tiles], "randomize": block.randomize}
        doc = self._doc
        return {
            "pos_top":    _ser(doc.positive.top),
            "pos_middle": _ser(doc.positive.middle),
            "pos_bottom": _ser(doc.positive.bottom),
            "neg_middle": _ser(doc.negative.middle),
        }

    def _commit_undo_snapshot(self) -> None:
        snap = self._snapshot_doc()
        if not self._undo_stack or self._undo_stack[-1] != snap:
            self._undo_stack.append(snap)
            self._redo_stack.clear()
            if len(self._undo_stack) > 50:
                self._undo_stack.pop(0)

    def _restore_snapshot(self, snap: dict) -> None:
        def _restore(block, data):
            block.tiles.clear()
            for td in data["tiles"]:
                tt = td.get("tile_type", "tag")
                if tt == "group":
                    block.tiles.append(GroupTile.from_dict(td))
                elif tt == "tag":
                    block.tiles.append(TagTile.from_dict(td))
                else:
                    block.tiles.append(NaturalTextTile.from_dict(td))
            block.randomize = data["randomize"]

        doc = self._doc
        _restore(doc.positive.top,    snap["pos_top"])
        _restore(doc.positive.middle, snap["pos_middle"])
        _restore(doc.positive.bottom, snap["pos_bottom"])
        _restore(doc.negative.middle, snap["neg_middle"])

        for bw in self.all_block_widgets():
            bw.reload()
        self._update_preview()

    def refresh_tiles_from_document(self) -> None:
        """
        ドキュメントを外部から直接変更した後に、タイルUI・プレビューを再構築し
        UNDO スナップショットを積む（↩ で変更前の状態に戻せる）。
        """
        for bw in self.all_block_widgets():
            bw.reload()
        self._refresh_content_size()
        self._update_preview()
        self._commit_undo_snapshot()

    def undo(self) -> bool:
        """直前のスナップショットを復元する。成功すれば True。"""
        if len(self._undo_stack) < 2:
            return False
        self._redo_stack.append(self._undo_stack.pop())
        snap = self._undo_stack[-1]
        self._restore_snapshot(snap)
        self.prompt_changed.emit()
        return True

    def redo(self) -> bool:
        """やり直し。成功すれば True。"""
        if not self._redo_stack:
            return False
        snap = self._redo_stack.pop()
        self._undo_stack.append(snap)
        self._restore_snapshot(snap)
        self.prompt_changed.emit()
        return True

    def can_undo(self) -> bool:
        return len(self._undo_stack) >= 2

    def can_redo(self) -> bool:
        return bool(self._redo_stack)

    def _do_undo(self) -> None:
        if self._readonly:
            return
        self.undo()

    def _do_redo(self) -> None:
        if self._readonly:
            return
        self.redo()

    # ── 外部API ─────────────────────────────────────────

    @property
    def document(self) -> PromptDocument:
        return self._doc

    def set_document(self, doc: PromptDocument) -> None:
        self._doc = doc
        pos = doc.positive
        neg = doc.negative

        self._bw_top.block    = pos.top
        self._bw_middle.block = pos.middle
        self._bw_bottom.block = pos.bottom
        self._bw_neg.block    = neg.middle

        for bw in (self._bw_top, self._bw_middle, self._bw_bottom, self._bw_neg):
            bw._readonly = self._readonly
            bw.reload()
            bw._apply_readonly_state()

        self._refresh_content_size()
        self._update_preview()
        # 新しいドキュメントで UNDO/REDOスタックをリセット
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._commit_undo_snapshot()

    def compile_positive(self) -> str:
        return self._doc.compile_positive()

    def compile_negative(self) -> str:
        if not self._negative_enabled:
            return ""
        return self._doc.compile_negative()

    def set_negative_enabled(self, enabled: bool) -> None:
        """現在のモデル/テンプレートがネガティブプロンプトに対応するかを反映する。"""
        enabled = bool(enabled)
        if self._negative_enabled == enabled:
            return
        self._negative_enabled = enabled
        self._neg_hdr_widget.setVisible(enabled)
        self._bw_neg.setVisible(enabled)
        if not enabled and self._focused_bw is self._bw_neg:
            self._focused_bw = self._bw_middle
        self._refresh_content_size()
        self._update_preview()

    def negative_enabled(self) -> bool:
        return self._negative_enabled

    def get_memo(self) -> str:
        return self._memo_edit.toPlainText()

    def set_memo(self, text: str) -> None:
        self._memo_edit.setPlainText(text or "")

    def _apply_readonly_state(self) -> None:
        if not self._readonly:
            return
        for widget in (
            self._history_map_btn,
            self._undo_btn,
            self._redo_btn,
            self._pos_copy_btn,
            self._neg_copy_btn,
            self._preview_local_copy_btn,
            self._preview_copy_btn,
        ):
            widget.hide()
        self._prev_frame.hide()
        self._memo_edit.setReadOnly(True)

    def _copy_positive(self) -> None:
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(self._doc.compile_positive())
        self._show_copy_done(self._pos_copy_btn)

    def _copy_negative(self) -> None:
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(self.compile_negative())
        self._show_copy_done(self._neg_copy_btn)

    def add_tile_to_bottom(self, tile) -> None:
        self._bw_bottom.block.add_tile(tile)
        self._bw_bottom._refresh_tiles()
        self._bw_bottom.block_changed.emit()

    def add_tile_to_negative(self, tile) -> None:
        """タイルをネガティブブロックに追加する。"""
        if not self._negative_enabled:
            return
        self._bw_neg.block.add_tile(tile)
        self._bw_neg._refresh_tiles()
        self._bw_neg.block_changed.emit()

    def load_prompt_tiles(self, positive_tiles: list, negative_tiles: list) -> None:
        """解析済みタイルを新しいドキュメントとしてロードする。"""
        doc = PromptDocument()
        for tile in positive_tiles:
            doc.positive.middle.add_tile(tile)
        for tile in negative_tiles:
            doc.negative.middle.add_tile(tile)
        self.set_document(doc)

    def add_tile_to_prompt_block(
        self,
        tile,
        block_type: str = BlockType.POSITIVE.value,
        position: str = BlockPosition.MIDDLE.value,
        index: int | None = None,
    ) -> None:
        """指定ブロックへタイルを追加し、該当ブロックだけ再描画する。"""
        if block_type == BlockType.NEGATIVE.value:
            if not self._negative_enabled:
                return
            bw = self._bw_neg
        elif position == BlockPosition.TOP.value:
            bw = self._bw_top
        elif position == BlockPosition.BOTTOM.value:
            bw = self._bw_bottom
        else:
            bw = self._bw_middle
        bw.block.add_tile(tile, index)
        bw._refresh_tiles()
        bw.block_changed.emit()

    def add_tile_to_focused(self, tile) -> None:
        target = self._focused_bw if self._focused_bw is not None else self._bw_middle
        target.block.add_tile(tile)
        target._refresh_tiles()
        target.block_changed.emit()

    def all_block_widgets(self):
        return [self._bw_top, self._bw_middle, self._bw_bottom, self._bw_neg]

    def is_empty(self) -> bool:
        widgets = [self._bw_top, self._bw_middle, self._bw_bottom]
        if self._negative_enabled:
            widgets.append(self._bw_neg)
        return all(not bw.block.tiles for bw in widgets)

    def _translate_target(self):
        return self._focused_bw

    def set_translating(self, translating: bool) -> None:
        t = self._translate_target()
        if t is not None:
            t.set_translating(translating)

    def set_translate_result(self, text: str) -> None:
        t = self._translate_target()
        if t is not None:
            t.set_translate_result(text)

    def show_translate_panel(self) -> None:
        t = self._translate_target()
        if t is not None:
            t.show_translate_panel()

    def hide_translate_panel(self) -> None:
        t = self._translate_target()
        if t is not None:
            t.hide_translate_panel()

    def append_translate_thinking(self, text: str) -> None:
        t = self._translate_target()
        if t is not None:
            t.append_translate_thinking(text)

    def append_translate_status(self, text: str) -> None:
        t = self._translate_target()
        if t is not None:
            t.append_translate_status(text)

    def load_prompts(self, positive: str, negative: str) -> None:
        doc = PromptDocument()

        pos_tiles = BlockWidget._parse_tag_input(positive) if positive.strip() else []
        for tile in pos_tiles:
            doc.positive.middle.add_tile(tile)

        neg_tiles = BlockWidget._parse_tag_input(negative) if negative.strip() else []
        for tile in neg_tiles:
            doc.negative.middle.add_tile(tile)

        self.set_document(doc)
