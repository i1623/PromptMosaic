"""
文章プロンプト編集ダイアログ

・display_label / rating / memo / is_nsfw を編集
・保存時に saved(id) シグナルを emit
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QPlainTextEdit, QCheckBox,
    QDialogButtonBox, QPushButton, QTextEdit, QWidget,
)
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QTextCursor, QTextOption

from core.i18n import tr
from db.prompt_text_db import update_prompt_text
from ui.star_widget import StarWidget
from ui.styles import SURFACE0, SURFACE1, SURFACE2, TEXT, SUBTEXT, RED, themed_button_style, ui_font
from ui.tile_widget import (
    _TileTranslateWorker,
    show_translation_compare_dialog,
)
from core.text_sanitize import single_line_text


class PromptTextEditDialog(QDialog):
    """
    文章プロンプト編集ダイアログ。

    Signals:
        saved(prompt_text_id): 保存成功時に emit
    """

    saved = Signal(int)

    def __init__(self, record: dict, parent=None):
        super().__init__(parent)
        self._record = record
        self.setWindowTitle(tr("prompt_text_edit.title"))
        self.setModal(True)
        self.resize(480, 360)
        self.setStyleSheet(f"QDialog {{ background-color: {SURFACE0}; }}")
        self._build_ui()

    # ── UI構築 ────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        lbl_style = f"color: {TEXT};"
        edit_style = (
            f"background: {SURFACE1}; color: {TEXT}; border: 1px solid {SURFACE2}; "
            f"border-radius: 3px; padding: 2px 4px;"
        )
        action_ss = (
            f"QPushButton {{ background: {SURFACE1}; color: {TEXT}; "
            f"border: 1px solid {SURFACE2}; border-radius: 3px; padding: 3px 8px; }}"
            f"QPushButton:hover {{ border-color: #89b4fa; }}"
            f"QPushButton:disabled {{ color: #45475a; border-color: #313244; }}"
        )

        # 表示名
        _display_lbl = QLabel(tr("prompt_text_edit.display_label"))
        _display_lbl.setStyleSheet(lbl_style)
        root.addWidget(_display_lbl)
        self._display_label_edit = QLineEdit(self._record.get("display_label") or "")
        self._display_label_edit.setStyleSheet(edit_style)
        root.addWidget(self._display_label_edit)

        # 原文（編集可）
        source_label_row = QHBoxLayout()
        source_label_row.setSpacing(4)
        _src_lbl = QLabel(tr("prompt_text_edit.source_text"))
        _src_lbl.setStyleSheet(lbl_style)
        source_label_row.addWidget(_src_lbl, 1)
        btn_translate_natural = QPushButton(tr("tile.natural_retranslate_btn"))
        btn_translate_natural.setFont(ui_font(-1))
        btn_translate_natural.setToolTip(tr("tile.natural_translate_tooltip"))
        btn_translate_natural.setStyleSheet(action_ss)
        source_label_row.addWidget(btn_translate_natural)
        root.addLayout(source_label_row)
        self._source_edit = QPlainTextEdit(self._record.get("source_text") or "")
        self._source_edit.setFixedHeight(60)
        self._source_edit.setStyleSheet(edit_style)
        root.addWidget(self._source_edit)

        # 訳文（編集可）
        translated_label_row = QHBoxLayout()
        translated_label_row.setSpacing(4)
        _trans_lbl = QLabel(tr("prompt_text_edit.translated_text"))
        _trans_lbl.setStyleSheet(lbl_style)
        translated_label_row.addWidget(_trans_lbl, 1)
        btn_reverse_natural = QPushButton(tr("tile.reverse_translate_btn"))
        btn_reverse_natural.setFont(ui_font(-1))
        btn_reverse_natural.setToolTip(tr("tile.natural_reverse_translate_tooltip"))
        btn_reverse_natural.setStyleSheet(action_ss)
        translated_label_row.addWidget(btn_reverse_natural)
        root.addLayout(translated_label_row)
        self._translated_edit = QPlainTextEdit(self._record.get("translated_text") or "")
        self._translated_edit.setFixedHeight(60)
        self._translated_edit.setStyleSheet(edit_style)
        root.addWidget(self._translated_edit)

        btn_cancel_translate = QPushButton(tr("translate_panel.cancel_btn"))
        btn_cancel_translate.setFont(ui_font(-1))
        btn_cancel_translate.setStyleSheet(action_ss)
        btn_cancel_translate.setEnabled(False)

        translate_panel = QWidget()
        translate_panel.setObjectName("translate_panel")
        translate_panel.setStyleSheet(
            "QWidget#translate_panel { background: transparent; border: none; }"
        )
        panel_lay = QVBoxLayout(translate_panel)
        panel_lay.setContentsMargins(0, 0, 0, 0)
        panel_lay.setSpacing(4)
        status_lbl = QLabel("")
        status_lbl.setFont(ui_font(-1))
        status_lbl.setStyleSheet(f"color: {SUBTEXT};")
        status_row = QHBoxLayout()
        status_row.setSpacing(4)
        status_row.addWidget(status_lbl)
        status_row.addStretch()
        status_row.addWidget(btn_cancel_translate)
        panel_lay.addLayout(status_row)

        thinking_edit = QTextEdit()
        thinking_edit.setReadOnly(True)
        thinking_edit.setFixedHeight(96)
        thinking_edit.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        thinking_edit.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        thinking_edit.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        thinking_edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        thinking_edit.setAcceptRichText(False)
        thinking_edit.setPlaceholderText(tr("translate_panel.thinking_label"))
        thinking_edit.setStyleSheet(
            f"QTextEdit {{ background: {SURFACE1}; color: {SUBTEXT}; "
            f"border: 1px solid {SURFACE2}; border-radius: 4px; padding: 4px; }}"
        )
        thinking_edit.hide()
        panel_lay.addWidget(thinking_edit)
        translate_panel.hide()
        root.addWidget(translate_panel)

        # フォームエリア
        form = QFormLayout()
        form.setSpacing(8)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # 評価
        self._star_widget = StarWidget(
            rating=self._record.get("rating") or 0,
            editable=True,
            font_size=14,
        )
        _lbl3 = QLabel(tr("prompt_text_edit.rating"))
        _lbl3.setStyleSheet(lbl_style)
        form.addRow(_lbl3, self._star_widget)

        # メモ
        self._memo_edit = QPlainTextEdit(self._record.get("memo") or "")
        self._memo_edit.setFixedHeight(80)
        self._memo_edit.setStyleSheet(edit_style)
        _lbl4 = QLabel(tr("prompt_text_edit.memo"))
        _lbl4.setStyleSheet(lbl_style)
        form.addRow(_lbl4, self._memo_edit)

        # NSFW
        self._nsfw_check = QCheckBox()
        self._nsfw_check.setChecked(bool(self._record.get("is_nsfw")))
        self._nsfw_check.setStyleSheet(f"color: {TEXT};")
        _lbl5 = QLabel(tr("prompt_text_edit.nsfw"))
        _lbl5.setStyleSheet(lbl_style)
        form.addRow(_lbl5, self._nsfw_check)

        root.addLayout(form)
        root.addStretch()

        # ボタン行
        btn_box = QDialogButtonBox()
        save_btn = btn_box.addButton(
            tr("prompt_text_edit.save"), QDialogButtonBox.ButtonRole.AcceptRole
        )
        cancel_btn = btn_box.addButton(
            tr("prompt_text_edit.cancel"), QDialogButtonBox.ButtonRole.RejectRole
        )
        save_btn.setStyleSheet(
            themed_button_style("success", bold=True)
        )
        cancel_btn.setStyleSheet(
            f"QPushButton {{ background: {SURFACE1}; color: {SUBTEXT}; "
            f"border: 1px solid {SURFACE2}; border-radius: 4px; padding: 4px 16px; }}"
        )

        worker: _TileTranslateWorker | None = None
        apply_target = "translated"
        translate_buttons = [btn_translate_natural, btn_reverse_natural]

        def _set_translating(translating: bool) -> None:
            for button in translate_buttons:
                button.setEnabled(not translating)
            btn_cancel_translate.setEnabled(translating)
            btn_box.setEnabled(not translating)

        def _append_thinking(text: str) -> None:
            if not thinking_edit.isVisible():
                thinking_edit.show()
            cursor = thinking_edit.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            cursor.insertText(text)
            thinking_edit.setTextCursor(cursor)
            thinking_edit.ensureCursorVisible()

        def _start_translate(src: str, reverse: bool, target: str) -> None:
            nonlocal worker, apply_target
            src = src.strip()
            if not src:
                translate_panel.show()
                status_lbl.setStyleSheet(f"color: {RED};")
                status_lbl.setText(tr("tile.translate_empty_source"))
                return
            apply_target = target
            translate_panel.show()
            thinking_edit.clear()
            thinking_edit.hide()
            status_lbl.setText(tr("translate_panel.status_translating"))
            status_lbl.setStyleSheet(f"color: {SUBTEXT};")
            _set_translating(True)
            worker = _TileTranslateWorker(src, "natural", reverse, self)
            worker.status_update.connect(status_lbl.setText)
            worker.thinking_chunk.connect(_append_thinking)
            worker.translation_done.connect(lambda text: _finish_translate(text))
            worker.failed.connect(lambda msg: _fail_translate(msg))
            worker.start()

        def _finish_translate(text: str) -> None:
            nonlocal worker
            worker = None
            text = single_line_text(text)
            if text:
                source_before = self._source_edit.toPlainText()
                translated_before = self._translated_edit.toPlainText()
                if apply_target == "source":
                    if show_translation_compare_dialog(
                        self,
                        title=tr("tile.reverse_result_title"),
                        result_label=tr("tile.reverse_result_label"),
                        source_text=source_before,
                        translated_text=translated_before,
                        result_text=text,
                        apply_label=tr("tile.apply_reverse_to_source_btn"),
                    ):
                        self._source_edit.setPlainText(text)
                else:
                    if show_translation_compare_dialog(
                        self,
                        title=tr("tile.retranslate_result_title"),
                        result_label=tr("tile.retranslate_result_label"),
                        source_text=source_before,
                        translated_text=translated_before,
                        result_text=text,
                        apply_label=tr("tile.apply_retranslate_to_translated_btn"),
                    ):
                        self._translated_edit.setPlainText(text)
                status_lbl.setText(tr("tile.translate_done"))
                status_lbl.setStyleSheet(f"color: {SUBTEXT};")
                _set_translating(False)
                thinking_edit.hide()
                translate_panel.hide()
            else:
                status_lbl.setStyleSheet(f"color: {RED};")
                status_lbl.setText(tr("main.translate_failed", error=tr("main.translate_empty_result")))
                _set_translating(False)
                thinking_edit.hide()
                translate_panel.show()

        def _fail_translate(msg: str) -> None:
            nonlocal worker
            worker = None
            status_lbl.setStyleSheet(f"color: {RED};")
            status_lbl.setText(tr("main.translate_failed", error=msg))
            _set_translating(False)
            thinking_edit.hide()
            translate_panel.show()

        def _cancel_translate() -> None:
            nonlocal worker
            if worker is not None and worker.isRunning():
                worker.cancel_and_wait()
            worker = None
            status_lbl.setText(tr("tile.translate_cancelled"))
            status_lbl.setStyleSheet(f"color: {SUBTEXT};")
            _set_translating(False)
            thinking_edit.hide()
            translate_panel.hide()

        btn_translate_natural.clicked.connect(
            lambda: _start_translate(self._source_edit.toPlainText(), False, "translated")
        )
        btn_reverse_natural.clicked.connect(
            lambda: _start_translate(self._translated_edit.toPlainText(), True, "source")
        )
        btn_cancel_translate.clicked.connect(_cancel_translate)
        self.finished.connect(
            lambda *_: worker.cancel_and_wait()
            if worker is not None and worker.isRunning()
            else None
        )

        btn_box.accepted.connect(self._on_save)
        btn_box.rejected.connect(self.reject)
        root.addWidget(btn_box)

    # ── スロット ──────────────────────────────────────────

    def _on_save(self) -> None:
        rating = self._star_widget.rating
        update_prompt_text(
            self._record["id"],
            source_text=self._source_edit.toPlainText().strip(),
            translated_text=self._translated_edit.toPlainText().strip(),
            display_label=self._display_label_edit.text().strip(),
            rating=rating if rating > 0 else None,
            memo=self._memo_edit.toPlainText().strip(),
            is_nsfw=1 if self._nsfw_check.isChecked() else 0,
        )
        self.saved.emit(self._record["id"])
        self.accept()
