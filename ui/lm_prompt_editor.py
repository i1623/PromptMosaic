"""
翻訳プロンプト編集ウィンドウ

フローティング・常手前・モーダル（Qt.Tool + WindowStaysOnTopHint）。
プロンプト変更は即座に app_settings へ保存される。

翻訳プロンプトを管理:
  - 文章翻訳用プロンプト: _SETTING_KEY_NATURAL / 文章翻訳用
  - タグ翻訳用プロンプト: _SETTING_KEY         / タグ翻訳用
  - 逆翻訳用プロンプト:   _SETTING_KEY_REVERSE / 逆翻訳用
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QTextEdit, QPushButton,
)
from PySide6.QtCore import Qt, QTimer

import db.app_db as _app_db
import db.env_db as _env_db
from core.i18n import tr
from ui.styles import SURFACE0, SURFACE1, SURFACE2, TEXT, SUBTEXT, ACCENT, GREEN, RED, ui_font

_DEFAULT_PROMPT = """\
You are a Stable Diffusion/Danbooru tag translator.
Convert the input words into English Danbooru-style tags.
Split the input words by commas and translate each item separately.
For one input word, output one tag.
Output only comma-separated English tags; do not include response messages, comments, explanations, or any extra text.

Input example: 猫耳,笑顔,幸せ
Output example: cat ears, smile, happy"""

_DEFAULT_NATURAL_PROMPT = """\
Translate the input language into natural English prose.
Output only the English natural sentence; do not include response messages, comments, explanations, or any extra text.
For one input sentence, output one sentence.

Input example: 猫耳の女の子が微笑んでいる
Output example: A smiling girl with cat ears."""

_DEFAULT_REVERSE_PROMPT = """\
あなたはStable Diffusion画像生成プロンプトの逆翻訳者です。
英語のDanbooruタグ、カンマ区切りタグ、または英語の自然文プロンプトを、現在のUI言語で使いやすい短い表現に翻訳してください。
出力は逆翻訳結果のみとし、説明文、ラベル、Markdown、引用符は含めないでください。"""

_SETTING_KEY         = "lm_translate_prompt"
_SETTING_KEY_NATURAL = "lm_translate_prompt_natural"
_SETTING_KEY_REVERSE = "lm_translate_prompt_reverse"


class LMPromptEditorWindow(QWidget):
    """
    翻訳用システムプロンプトを編集するフローティングウィンドウ。

    各翻訳用途のプロンプトを管理する。

    Qt.Tool フラグにより:
    - タスクバーに表示されない
    - 親ウィンドウに追従して最小化/復元される
    WindowStaysOnTopHint により常にメインウィンドウより手前に表示される。
    """

    def __init__(self, parent=None):
        super().__init__(
            parent,
            Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setWindowTitle(tr("lm_prompt_editor.title"))
        self.resize(560, 640)
        self.setMinimumSize(320, 300)
        self.setStyleSheet(f"QWidget {{ background: {SURFACE0}; color: {TEXT}; }}")
        self._build_ui()
        self._load_prompts()
        self._save_timer = QTimer()
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._do_save)

    # ─── _section_label: セクション見出しヘルパー ───────────────

    def _section_label(self, icon: str, text: str, color: str) -> QLabel:
        lbl = QLabel(f"{icon}  {text}")
        lbl.setFont(ui_font(-1, bold=True))
        lbl.setStyleSheet(f"color: {color}; background: transparent; padding: 2px 0;")
        return lbl

    # ─── UI構築 ──────────────────────────────────────────────────

    def _build_ui(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        # ── 文章翻訳プロンプト ────────────────────────────────
        lay.addWidget(self._section_label("📄", tr("lm_prompt_editor.natural_title"), GREEN))

        self._edit_natural = QTextEdit()
        self._edit_natural.setFont(ui_font())
        self._edit_natural.setStyleSheet(
            f"QTextEdit {{ background: {SURFACE1}; color: {TEXT}; "
            f"border: 1px solid {SURFACE2}; border-radius: 4px; padding: 4px; }}"
        )
        self._edit_natural.textChanged.connect(self._on_text_changed)
        lay.addWidget(self._edit_natural, stretch=1)

        reset_row1 = QHBoxLayout()
        reset_row1.setSpacing(6)
        reset_btn1 = QPushButton(tr("lm_prompt_editor.reset_btn"))
        reset_btn1.setFont(ui_font(-1))
        reset_btn1.setStyleSheet(
            f"QPushButton {{ background: {SURFACE1}; color: {SUBTEXT}; "
            f"border: 1px solid {SURFACE2}; border-radius: 3px; padding: 2px 8px; }}"
            f"QPushButton:hover {{ color: {TEXT}; border-color: {TEXT}; }}"
        )
        reset_btn1.clicked.connect(self._reset_natural)
        reset_row1.addWidget(reset_btn1)
        reset_row1.addStretch()
        lay.addLayout(reset_row1)

        # ── タグ翻訳プロンプト ────────────────────────────
        lay.addWidget(self._section_label("🏷️", tr("lm_prompt_editor.tag_title"), ACCENT))

        self._edit_danboard = QTextEdit()
        self._edit_danboard.setFont(ui_font())
        self._edit_danboard.setStyleSheet(
            f"QTextEdit {{ background: {SURFACE1}; color: {TEXT}; "
            f"border: 1px solid {SURFACE2}; border-radius: 4px; padding: 4px; }}"
        )
        self._edit_danboard.textChanged.connect(self._on_text_changed)
        lay.addWidget(self._edit_danboard, stretch=1)

        reset_row2 = QHBoxLayout()
        reset_row2.setSpacing(6)
        reset_btn2 = QPushButton(tr("lm_prompt_editor.reset_btn"))
        reset_btn2.setFont(ui_font(-1))
        reset_btn2.setStyleSheet(
            f"QPushButton {{ background: {SURFACE1}; color: {SUBTEXT}; "
            f"border: 1px solid {SURFACE2}; border-radius: 3px; padding: 2px 8px; }}"
            f"QPushButton:hover {{ color: {TEXT}; border-color: {TEXT}; }}"
        )
        reset_btn2.clicked.connect(self._reset_danboard)
        reset_row2.addWidget(reset_btn2)
        reset_row2.addStretch()

        self._status_lbl = QLabel("")
        self._status_lbl.setFont(ui_font(-1))
        self._status_lbl.setStyleSheet(f"color: {GREEN}; background: transparent;")
        reset_row2.addWidget(self._status_lbl)

        lay.addLayout(reset_row2)

        # ── 逆翻訳プロンプト ────────────────────────────────
        lay.addWidget(self._section_label("↩", tr("lm_prompt_editor.reverse_title"), GREEN))

        self._edit_reverse = QTextEdit()
        self._edit_reverse.setFont(ui_font())
        self._edit_reverse.setStyleSheet(
            f"QTextEdit {{ background: {SURFACE1}; color: {TEXT}; "
            f"border: 1px solid {SURFACE2}; border-radius: 4px; padding: 4px; }}"
        )
        self._edit_reverse.textChanged.connect(self._on_text_changed)
        lay.addWidget(self._edit_reverse, stretch=1)

        reset_row3 = QHBoxLayout()
        reset_row3.setSpacing(6)
        reset_btn3 = QPushButton(tr("lm_prompt_editor.reset_btn"))
        reset_btn3.setFont(ui_font(-1))
        reset_btn3.setStyleSheet(
            f"QPushButton {{ background: {SURFACE1}; color: {SUBTEXT}; "
            f"border: 1px solid {SURFACE2}; border-radius: 3px; padding: 2px 8px; }}"
            f"QPushButton:hover {{ color: {TEXT}; border-color: {TEXT}; }}"
        )
        reset_btn3.clicked.connect(self._reset_reverse)
        reset_row3.addWidget(reset_btn3)
        reset_row3.addStretch()
        lay.addLayout(reset_row3)

    # ─── 読み込み ─────────────────────────────────────────────────

    def _load_prompts(self) -> None:
        row = _app_db.fetchone(
            "SELECT value FROM app_settings WHERE key=?", (_SETTING_KEY,)
        )
        text_db = (row["value"] if row else "") or _DEFAULT_PROMPT
        self._edit_danboard.blockSignals(True)
        self._edit_danboard.setPlainText(text_db)
        self._edit_danboard.blockSignals(False)

        row2 = _app_db.fetchone(
            "SELECT value FROM app_settings WHERE key=?", (_SETTING_KEY_NATURAL,)
        )
        text_nat = (row2["value"] if row2 else "") or _DEFAULT_NATURAL_PROMPT
        self._edit_natural.blockSignals(True)
        self._edit_natural.setPlainText(text_nat)
        self._edit_natural.blockSignals(False)

        row3 = _env_db.fetchone(
            "SELECT value FROM env_settings WHERE key=?", (_SETTING_KEY_REVERSE,)
        )
        text_reverse = (row3["value"] if row3 else "") or _DEFAULT_REVERSE_PROMPT
        self._edit_reverse.blockSignals(True)
        self._edit_reverse.setPlainText(text_reverse)
        self._edit_reverse.blockSignals(False)

    # ─── 保存 ─────────────────────────────────────────────────────

    def _on_text_changed(self) -> None:
        self._save_timer.start(500)

    def _do_save(self) -> None:
        _app_db.execute(
            "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
            (_SETTING_KEY, self._edit_danboard.toPlainText()),
        )
        _app_db.execute(
            "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
            (_SETTING_KEY_NATURAL, self._edit_natural.toPlainText()),
        )
        _env_db.execute(
            "INSERT OR REPLACE INTO env_settings (key, value) VALUES (?, ?)",
            (_SETTING_KEY_REVERSE, self._edit_reverse.toPlainText()),
        )
        self._status_lbl.setText(tr("lm_prompt_editor.saved"))
        QTimer.singleShot(1500, lambda: self._status_lbl.setText(""))

    # ─── リセット ─────────────────────────────────────────────────

    def _reset_danboard(self) -> None:
        self._edit_danboard.setPlainText(_DEFAULT_PROMPT)

    def _reset_natural(self) -> None:
        self._edit_natural.setPlainText(_DEFAULT_NATURAL_PROMPT)

    def _reset_reverse(self) -> None:
        self._edit_reverse.setPlainText(_DEFAULT_REVERSE_PROMPT)

    # ─── 外部API ──────────────────────────────────────────────────

    def get_prompt(self) -> str:
        """ダンボール語翻訳プロンプトを返す。空の場合はデフォルトを返す。"""
        return self._edit_danboard.toPlainText().strip() or _DEFAULT_PROMPT

    def get_prompt_natural(self) -> str:
        """自然言語翻訳プロンプトを返す。空の場合はデフォルトを返す。"""
        return self._edit_natural.toPlainText().strip() or _DEFAULT_NATURAL_PROMPT

    def get_prompt_reverse(self) -> str:
        """逆翻訳プロンプトを返す。空の場合はデフォルトを返す。"""
        return self._edit_reverse.toPlainText().strip() or _DEFAULT_REVERSE_PROMPT

    def toggle(self) -> None:
        """表示/非表示をトグルする。"""
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()
            self.activateWindow()
