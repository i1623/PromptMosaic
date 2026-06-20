"""
PNGメタデータ表示ダイアログ

InvokeAI生成PNGのメタデータを表示し、エディタへのロードを提供する。
"""
from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QScrollArea, QWidget, QFrame,
    QGridLayout, QTextEdit, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont

from core.i18n import tr
from ui.styles import SURFACE0, SURFACE1, SURFACE2, TEXT, SUBTEXT, ACCENT, RED, GREEN, themed_button_style


class MetaDialog(QDialog):
    """
    PNG メタデータ表示ダイアログ。

    Signals:
        load_prompt_requested(positive, negative, model_name, loras):
            「エディタにロード」ボタンを押したとき。
            loras は [{"name": str, "weight": float}, ...] の形式。
    """

    load_prompt_requested = Signal(str, str, str, list)

    def __init__(self, meta: dict[str, Any] | None, parent=None):
        super().__init__(parent)
        self._meta = meta
        self.setWindowTitle(tr("meta_dialog.title"))
        self.setModal(True)
        self.resize(640, 520)
        self.setStyleSheet(f"QDialog {{ background-color: {SURFACE0}; }}")
        self._build_ui()

    # ── UI構築 ──────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        if self._meta is None:
            lbl = QLabel(tr("meta_dialog.no_meta"))
            lbl.setFont(QFont("Segoe UI", 10))
            lbl.setStyleSheet(f"color: {SUBTEXT};")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            root.addWidget(lbl, stretch=1)
        else:
            root.addWidget(self._build_meta_area(), stretch=1)

        root.addLayout(self._build_btn_row())

    def _build_meta_area(self) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
        )

        container = QWidget()
        container.setStyleSheet("background: transparent;")
        grid = QGridLayout(container)
        grid.setContentsMargins(4, 4, 4, 4)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        grid.setColumnMinimumWidth(0, 110)

        self._row_idx = 0
        m = self._meta

        self._add_row(grid, tr("meta_dialog.positive"),
                      m.get("positive_prompt", ""), multiline=True)
        self._add_row(grid, tr("meta_dialog.negative"),
                      m.get("negative_prompt", ""), multiline=True)

        if m.get("source_format"):
            self._add_row(grid, tr("meta_dialog.source_format"),
                          _source_format_label(m.get("source_format", "")))

        # 区切り線
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"border: none; background-color: {SURFACE2}; max-height: 1px;")
        grid.addWidget(sep, self._row_idx, 0, 1, 2)
        self._row_idx += 1

        model_str = m.get("model_name", "") or "—"
        if m.get("model_base"):
            model_str += f"  [{m['model_base']}]"
        self._add_row(grid, tr("meta_dialog.model"), model_str)

        if m.get("seed") is not None:
            self._add_row(grid, tr("meta_dialog.seed"), str(m["seed"]))
        if m.get("cfg_scale") is not None:
            self._add_row(grid, tr("meta_dialog.cfg"), str(m["cfg_scale"]))
        if m.get("steps") is not None:
            self._add_row(grid, tr("meta_dialog.steps"), str(m["steps"]))
        if m.get("scheduler"):
            self._add_row(grid, tr("meta_dialog.scheduler"), m["scheduler"])
        if m.get("width") and m.get("height"):
            self._add_row(grid, tr("meta_dialog.size"),
                          f'{m["width"]} × {m["height"]}')

        if m.get("loras"):
            lora_lines = "\n".join(
                f'{lo["name"]}  (weight: {lo["weight"]})' for lo in m["loras"]
            )
            self._add_row(grid, tr("meta_dialog.loras"), lora_lines,
                          multiline=len(m["loras"]) > 2)

        if m.get("warnings"):
            self._add_row(grid, tr("meta_dialog.warnings"),
                          "\n".join(str(w) for w in m["warnings"]),
                          multiline=True)

        # 下詰めのスペーサー
        grid.setRowStretch(self._row_idx, 1)

        scroll.setWidget(container)
        return scroll

    def _add_row(self, grid: QGridLayout, label: str, value: str,
                 multiline: bool = False) -> None:
        row = self._row_idx

        lbl = QLabel(label)
        lbl.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        lbl.setStyleSheet(f"color: {SUBTEXT}; background: transparent;")
        lbl.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)
        grid.addWidget(lbl, row, 0)

        if multiline:
            val_w = QTextEdit()
            val_w.setReadOnly(True)
            val_w.setPlainText(value or "—")
            val_w.setFont(QFont("Consolas", 9))
            val_w.setStyleSheet(
                f"QTextEdit {{ background: {SURFACE1}; color: {TEXT}; "
                f"border: 1px solid {SURFACE2}; border-radius: 3px; padding: 3px; }}"
            )
            val_w.setMinimumHeight(60)
            val_w.setMaximumHeight(120)
            val_w.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
            )
            grid.addWidget(val_w, row, 1)
        else:
            val_w = QLabel(value or "—")
            val_w.setFont(QFont("Consolas", 9))
            val_w.setStyleSheet(
                f"color: {TEXT}; background: {SURFACE1}; "
                f"border: 1px solid {SURFACE2}; border-radius: 3px; padding: 3px 6px;"
            )
            val_w.setWordWrap(True)
            val_w.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            grid.addWidget(val_w, row, 1)

        self._row_idx += 1

    def _build_btn_row(self) -> QHBoxLayout:
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        if self._meta is not None:
            load_btn = QPushButton(tr("meta_dialog.load_btn"))
            load_btn.setFont(QFont("Segoe UI", 9))
            load_btn.setStyleSheet(themed_button_style("success"))
            load_btn.clicked.connect(self._on_load)
            can_load = bool(
                (self._meta.get("positive_prompt") or "").strip()
                or (self._meta.get("negative_prompt") or "").strip()
                or (self._meta.get("model_name") or "").strip()
                or self._meta.get("loras")
            )
            load_btn.setEnabled(can_load)
            btn_row.addWidget(load_btn)

        close_btn = QPushButton(tr("meta_dialog.close_btn"))
        close_btn.setFont(QFont("Segoe UI", 9))
        close_btn.setStyleSheet(
            f"QPushButton {{ background-color: {SURFACE1}; color: {TEXT}; "
            f"border: 1px solid {SURFACE2}; border-radius: 4px; padding: 5px 18px; }}"
            f"QPushButton:hover {{ color: {ACCENT}; border-color: {ACCENT}; }}"
        )
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)

        return btn_row

    # ── スロット ────────────────────────────────────────────

    def _on_load(self) -> None:
        if self._meta is None:
            return
        pos   = self._meta.get("positive_prompt") or ""
        neg   = self._meta.get("negative_prompt") or ""
        model = self._meta.get("model_name") or ""
        loras = self._meta.get("loras") or []        # [{"name": ..., "weight": ...}, ...]
        self.load_prompt_requested.emit(pos, neg, model, loras)
        self.accept()


def _source_format_label(source_format: str) -> str:
    labels = {
        "invokeai": "InvokeAI",
        "a1111": "AUTOMATIC1111 / Forge",
        "comfyui": "ComfyUI",
        "comfyui_webp": "ComfyUI WebP",
        "novelai": "NovelAI",
        "chatgpt_c2pa": "ChatGPT / GPT-4o C2PA",
    }
    return labels.get(source_format, source_format or "—")
