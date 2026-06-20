"""
生成履歴レビューダイアログ

生成パラメータの確認・評価・メモ記録を1画面で行う。
サムネイルは DB の thumbnail_data BLOB を優先し、
なければ InvokeAI API から取得・圧縮して DB に保存する。
"""
from __future__ import annotations

import io
import sqlite3
from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QVBoxLayout, QGridLayout, QLabel, QPushButton,
    QTabWidget, QWidget, QTextEdit, QLineEdit, QCheckBox,
    QComboBox, QFrame, QSizePolicy, QFileDialog,
)
from PySide6.QtCore import Qt, Signal, QBuffer, QByteArray, QIODeviceBase
from PySide6.QtGui import QFont, QPixmap, QImageWriter, QCloseEvent

import db.env_db as _env_db
import db.history_db as _history_db
import core.local_storage as local_storage
from core.i18n import tr
from ui.star_widget import StarWidget
from ui.styles import SURFACE0, SURFACE1, SURFACE2, TEXT, SUBTEXT, ACCENT, GREEN, RED, themed_button_style

if TYPE_CHECKING:
    from api.invoke_client import InvokeClient

# ステータス値リスト
_STATUS_KEYS = ["draft", "candidate", "approved", "rejected", "archived"]


class ReviewDialog(QDialog):
    """
    生成詳細 + レビューダイアログ。

    Signals:
        load_requested(gen_id: int):
            「エディタにロード」ボタンが押されたとき
    """

    load_requested = Signal(int)
    review_saved   = Signal(int)   # gen_id: 保存のたびに emit（即時反映用）

    def __init__(
        self,
        gen_id: int,
        client: "InvokeClient | None" = None,
        parent=None,
    ):
        super().__init__(parent)
        self._gen_id = gen_id
        self._client = client

        # DB からデータを読み込む
        self._row = _history_db.fetchone(
            """
            SELECT g.id, g.invoke_image_name,
                   g.sent_positive_prompt, g.sent_negative_prompt,
                   g.model_name, g.model_base, g.seed, g.cfg_scale,
                   g.steps, g.scheduler, g.width, g.height, g.created_at,
                   g.local_path,
                   g.loras_json,
                   g.template_id,
                   r.rating, r.status, r.title, r.review_text, r.is_favorite
            FROM generations g
            LEFT JOIN image_reviews r ON r.generation_id = g.id
            WHERE g.id = ?
            """,
            (gen_id,),
        )

        self._dirty = False

        self.setWindowTitle(tr("review.dialog_title", id=gen_id))
        self.setModal(True)
        self.resize(820, 600)
        self.setStyleSheet(f"QDialog {{ background-color: {SURFACE0}; }}")
        self._build_ui()
        self._load_thumbnail()

        # ウィジェット構築後にダーティ追跡を開始（構築時の setValue 等は無視）
        self._star_widget.rating_changed.connect(self._set_dirty)
        self._status_combo.currentIndexChanged.connect(self._set_dirty)
        self._fav_cb.stateChanged.connect(self._set_dirty)
        self._title_edit.textChanged.connect(self._set_dirty)
        self._notes_edit.textChanged.connect(self._set_dirty)

    # ── UI 構築 ─────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # コンテンツ行（左カラム + 右タブ）
        content = QHBoxLayout()
        content.setSpacing(10)
        content.addWidget(self._build_left_panel())
        content.addWidget(self._build_tabs(), stretch=1)
        root.addLayout(content, stretch=1)

        # ボタン行
        root.addLayout(self._build_btn_row())

    def _build_left_panel(self) -> QWidget:
        """左カラム: サムネイル + サムネイル変更ボタン + 生成日時 + パラメータ"""
        w = QWidget()
        w.setFixedWidth(260)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        # サムネイル
        self._thumb_label = QLabel(tr("review.no_image"))
        self._thumb_label.setFixedSize(244, 244)
        self._thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb_label.setStyleSheet(
            f"background-color: {SURFACE1}; border: 1px solid {SURFACE2}; "
            f"color: {SUBTEXT}; border-radius: 4px;"
        )
        lay.addWidget(self._thumb_label)

        # サムネイル変更ボタン
        change_thumb_btn = QPushButton(tr("review.change_thumb_btn"))
        change_thumb_btn.setFont(QFont("Segoe UI", 8))
        change_thumb_btn.setStyleSheet(
            f"QPushButton {{ background-color: {SURFACE1}; color: {SUBTEXT}; "
            f"border: 1px solid {SURFACE2}; border-radius: 4px; padding: 3px 8px; }}"
            f"QPushButton:hover {{ color: {TEXT}; background-color: {SURFACE2}; }}"
        )
        change_thumb_btn.clicked.connect(self._change_thumbnail)
        lay.addWidget(change_thumb_btn)

        # 生成日時
        created = str((self._row["created_at"] if self._row else "") or "")[:16]
        date_lbl = QLabel(created)
        date_lbl.setFont(QFont("Segoe UI", 8))
        date_lbl.setStyleSheet(f"color: {SUBTEXT}; background: transparent;")
        date_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(date_lbl)

        # 生成パラメータ
        lay.addWidget(self._build_params_frame())
        lay.addStretch()
        return w

    def _build_params_frame(self) -> QFrame:
        """モデル / シード / サイズ等をコンパクトに表示"""
        frame = QFrame()
        frame.setStyleSheet(
            f"QFrame {{ background-color: {SURFACE1}; border: 1px solid {SURFACE2}; "
            f"border-radius: 4px; }}"
        )
        # QGridLayout を使うことで、値ラベルの wordWrap による heightForWidth が
        # 正しく伝播し、折り返した行同士が縦方向に重ならない。
        grid = QGridLayout(frame)
        grid.setContentsMargins(6, 4, 6, 4)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(3)
        grid.setColumnStretch(0, 0)
        grid.setColumnStretch(1, 1)

        r = self._row
        row_idx = 0

        def add_row(label: str, value) -> int:
            nonlocal row_idx
            if not value:
                return row_idx
            lbl = QLabel(label)
            lbl.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
            lbl.setStyleSheet(f"color: {SUBTEXT}; background: transparent; border: none;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            val = QLabel(str(value))
            val.setFont(QFont("Consolas", 8))
            val.setStyleSheet(f"color: {TEXT}; background: transparent; border: none;")
            val.setWordWrap(True)
            val.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            sp = val.sizePolicy()
            sp.setVerticalPolicy(QSizePolicy.Policy.MinimumExpanding)
            val.setSizePolicy(sp)
            grid.addWidget(lbl, row_idx, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            grid.addWidget(val, row_idx, 1)
            row_idx += 1
            return row_idx

        if r:
            # ベースモデル → モデル → テンプレートの順で表示
            base = str(r["model_base"] or "")
            add_row(tr("review.base_model_label"), base.upper() or None)
            add_row(tr("review.model_label"),     r["model_name"] or None)
            tmpl_id = r["template_id"] if "template_id" in r.keys() else None
            tmpl_row = _env_db.fetchone("SELECT name FROM templates WHERE id=?", (tmpl_id,)) if tmpl_id else None
            tmpl_name = tmpl_row["name"] if tmpl_row else None
            if tmpl_name:
                add_row(tr("review.template_label"), tmpl_name)
            add_row(tr("review.seed_label"),      r["seed"])
            add_row(tr("review.steps_label"),     r["steps"])
            add_row(tr("review.scheduler_label"), r["scheduler"] or None)
            if r["width"] and r["height"]:
                add_row(tr("review.size_label"),  f'{r["width"]} × {r["height"]}')
            if r["cfg_scale"]:
                add_row(tr("review.cfg_label"),   r["cfg_scale"])

            # LoRA 表示: 個々の行に分けると wordWrap で縦に潰れて読めなくなるため、
            # 改行区切りで 1 つの値ラベルにまとめる。
            loras_raw = r["loras_json"] if "loras_json" in r.keys() else None
            if loras_raw:
                try:
                    import json as _json
                    loras_list = _json.loads(loras_raw)
                    lora_parts = []
                    for lora in (loras_list or []):
                        if isinstance(lora, dict):
                            # InvokeAI形式: {"lora": {"name": "..."}, "weight": 0.8}
                            # または簡易形式: {"name": "...", "weight": 0.8}
                            name   = (lora.get("name") or
                                      (lora.get("lora") or {}).get("name") or
                                      (lora.get("lora") or {}).get("alias") or "?")
                            weight = lora.get("weight", 1.0)
                            lora_parts.append(f"{name} ({weight:.2f})")
                    if lora_parts:
                        add_row("LoRA:", "\n".join(lora_parts))
                except Exception:
                    pass

        return frame

    def _build_tabs(self) -> QTabWidget:
        tabs = QTabWidget()
        tabs.addTab(self._build_prompt_tab(), tr("review.tab_prompt"))
        tabs.addTab(self._build_review_tab(), tr("review.tab_review"))
        return tabs

    def _build_prompt_tab(self) -> QWidget:
        """プロンプト表示タブ（読み取り専用）"""
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(6)
        r = self._row

        pos_hdr = QLabel(tr("review.pos_label"))
        pos_hdr.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        pos_hdr.setStyleSheet(f"color: {ACCENT}; background: transparent;")
        lay.addWidget(pos_hdr)

        self._pos_text = QTextEdit()
        self._pos_text.setReadOnly(True)
        self._pos_text.setPlainText((r["sent_positive_prompt"] or "") if r else "")
        self._pos_text.setFont(QFont("Consolas", 9))
        self._pos_text.setStyleSheet(
            f"QTextEdit {{ background: {SURFACE1}; color: {TEXT}; "
            f"border: 1px solid {SURFACE2}; border-radius: 3px; }}"
        )
        lay.addWidget(self._pos_text, stretch=3)

        neg_hdr = QLabel(tr("review.neg_label"))
        neg_hdr.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        neg_hdr.setStyleSheet(f"color: {RED}; background: transparent;")
        lay.addWidget(neg_hdr)

        self._neg_text = QTextEdit()
        self._neg_text.setReadOnly(True)
        self._neg_text.setPlainText((r["sent_negative_prompt"] or "") if r else "")
        self._neg_text.setFont(QFont("Consolas", 9))
        self._neg_text.setStyleSheet(
            f"QTextEdit {{ background: {SURFACE1}; color: {TEXT}; "
            f"border: 1px solid {SURFACE2}; border-radius: 3px; }}"
        )
        lay.addWidget(self._neg_text, stretch=2)

        return w

    def _build_review_tab(self) -> QWidget:
        """レビュー入力タブ"""
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(10)
        r = self._row

        # ── 評価 ────────────────────────────────────────
        rating_row = QHBoxLayout()
        rlbl = QLabel(tr("review.rating_label"))
        rlbl.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        rlbl.setStyleSheet(f"color: {SUBTEXT}; background: transparent;")
        rlbl.setFixedWidth(72)
        rating_row.addWidget(rlbl)
        self._star_widget = StarWidget(
            rating=int(r["rating"] or 0) if r else 0,
            font_size=15,
        )
        rating_row.addWidget(self._star_widget)
        rating_row.addStretch()
        lay.addLayout(rating_row)

        # ── ステータス + お気に入り ───────────────────────
        status_row = QHBoxLayout()
        slbl = QLabel(tr("review.status_label"))
        slbl.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        slbl.setStyleSheet(f"color: {SUBTEXT}; background: transparent;")
        slbl.setFixedWidth(72)
        status_row.addWidget(slbl)

        self._status_combo = QComboBox()
        for key in _STATUS_KEYS:
            self._status_combo.addItem(tr(f"review.status_{key}"), key)
        current = (r["status"] or "draft") if r else "draft"
        self._status_combo.setCurrentIndex(
            _STATUS_KEYS.index(current) if current in _STATUS_KEYS else 0
        )
        status_row.addWidget(self._status_combo)
        status_row.addSpacing(16)

        self._fav_cb = QCheckBox(tr("review.favorite_label"))
        self._fav_cb.setChecked(bool(r["is_favorite"]) if r and r["is_favorite"] else False)
        self._fav_cb.setStyleSheet(f"color: {TEXT}; background: transparent;")
        status_row.addWidget(self._fav_cb)
        status_row.addStretch()
        lay.addLayout(status_row)

        # ── 1行コメント ─────────────────────────────────
        tlbl = QLabel(tr("review.comment_label"))
        tlbl.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        tlbl.setStyleSheet(f"color: {SUBTEXT}; background: transparent;")
        lay.addWidget(tlbl)

        self._title_edit = QLineEdit((r["title"] or "") if r else "")
        self._title_edit.setFont(QFont("Segoe UI", 9))
        self._title_edit.setPlaceholderText(tr("review.comment_placeholder"))
        lay.addWidget(self._title_edit)

        # ── メモ ─────────────────────────────────────────
        nlbl = QLabel(tr("review.notes_label"))
        nlbl.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        nlbl.setStyleSheet(f"color: {SUBTEXT}; background: transparent;")
        lay.addWidget(nlbl)

        self._notes_edit = QTextEdit()
        self._notes_edit.setPlainText((r["review_text"] or "") if r else "")
        self._notes_edit.setFont(QFont("Segoe UI", 9))
        self._notes_edit.setPlaceholderText(tr("review.notes_placeholder"))
        self._notes_edit.setStyleSheet(
            f"QTextEdit {{ background: {SURFACE1}; color: {TEXT}; "
            f"border: 1px solid {SURFACE2}; border-radius: 3px; }}"
        )
        lay.addWidget(self._notes_edit, stretch=1)

        return w

    def _build_btn_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addStretch()

        load_btn = QPushButton(tr("review.load_btn"))
        load_btn.setFont(QFont("Segoe UI", 9))
        load_btn.setStyleSheet(themed_button_style("accent"))
        load_btn.clicked.connect(self._on_load)
        row.addWidget(load_btn)

        save_btn = QPushButton(tr("review.save_btn"))
        save_btn.setFont(QFont("Segoe UI", 9))
        save_btn.setStyleSheet(themed_button_style("success"))
        save_btn.clicked.connect(self._save_review)
        row.addWidget(save_btn)

        save_close_btn = QPushButton(tr("review.save_close_btn"))
        save_close_btn.setFont(QFont("Segoe UI", 9))
        save_close_btn.setStyleSheet(themed_button_style("success", bold=True))
        save_close_btn.clicked.connect(self._save_and_close)
        row.addWidget(save_close_btn)

        close_btn = QPushButton(tr("review.close_btn"))
        close_btn.setFont(QFont("Segoe UI", 9))
        close_btn.setStyleSheet(
            f"QPushButton {{ background-color: {SURFACE1}; color: {TEXT}; "
            f"border: 1px solid {SURFACE2}; border-radius: 4px; padding: 5px 16px; }}"
            f"QPushButton:hover {{ color: {RED}; border-color: {RED}; }}"
        )
        close_btn.clicked.connect(self._try_close)
        row.addWidget(close_btn)

        return row

    # ── サムネイル読み込み ────────────────────────────────

    def _load_thumbnail(self) -> None:
        """DB の thumbnail_data BLOB を優先し、なければ API から取得して保存・表示する。"""
        row = _history_db.fetchone(
            "SELECT thumbnail_data FROM generations WHERE id=?", (self._gen_id,)
        )
        if row and row["thumbnail_data"]:
            pix = QPixmap()
            if pix.loadFromData(bytes(row["thumbnail_data"])) and not pix.isNull():
                self._thumb_label.setPixmap(pix.scaled(
                    244, 244,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                ))
                self._thumb_label.setText("")
                return

        if not self._row or not self._row["invoke_image_name"]:
            return

        data = self._fetch_and_store_thumb(self._row["invoke_image_name"])
        if data is None:
            return

        pix = QPixmap()
        if pix.loadFromData(data) and not pix.isNull():
            self._thumb_label.setPixmap(pix.scaled(
                244, 244,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
            self._thumb_label.setText("")

    def _fetch_and_store_thumb(self, image_name: str) -> bytes | None:
        """API からサムネイルを取得し、圧縮して DB に保存してバイト列を返す。"""
        if self._client is None:
            return None
        try:
            raw = self._client.image_thumbnail(image_name)
        except Exception:
            return None
        from PIL import Image
        img = Image.open(io.BytesIO(raw))
        img.thumbnail((256, 256))
        buf = io.BytesIO()
        img.save(buf, format="WEBP", quality=80)
        thumb_bytes = buf.getvalue()
        _history_db.execute(
            "UPDATE generations SET thumbnail_data=? WHERE id=?",
            (sqlite3.Binary(thumb_bytes), self._gen_id),
        )
        return thumb_bytes

    # ── カスタムサムネイル変更 ───────────────────────────

    def _change_thumbnail(self) -> None:
        """ファイルダイアログで画像を選択し、圧縮して DB に保存する。"""
        path, _ = QFileDialog.getOpenFileName(
            self,
            tr("review.change_thumb_title"),
            "",
            "Images (*.png *.jpg *.jpeg *.webp);;All Files (*)",
        )
        if not path:
            return

        from PIL import Image
        try:
            img = Image.open(path)
        except Exception:
            return
        img.thumbnail((256, 256))
        buf = io.BytesIO()
        img.save(buf, format="WEBP", quality=80)
        thumb_bytes = buf.getvalue()
        _history_db.execute(
            "UPDATE generations SET thumbnail_data=? WHERE id=?",
            (sqlite3.Binary(thumb_bytes), self._gen_id),
        )
        self._load_thumbnail()

    # ── スロット ─────────────────────────────────────────

    def _save_review(self) -> None:
        """レビュー内容を image_reviews テーブルに保存する（UPSERT）"""
        _history_db.execute(
            """
            INSERT INTO image_reviews
                (generation_id, rating, status, title, review_text, is_favorite,
                 updated_at)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(generation_id) DO UPDATE SET
                rating      = excluded.rating,
                status      = excluded.status,
                title       = excluded.title,
                review_text = excluded.review_text,
                is_favorite = excluded.is_favorite,
                updated_at  = CURRENT_TIMESTAMP
            """,
            (
                self._gen_id,
                self._star_widget.rating,
                self._status_combo.currentData(),
                self._title_edit.text().strip(),
                self._notes_edit.toPlainText(),
                int(self._fav_cb.isChecked()),
            ),
        )
        self._saved = True
        self._dirty = False
        self.review_saved.emit(self._gen_id)

    def _save_and_close(self) -> None:
        """保存してダイアログを閉じる"""
        self._save_review()
        self.accept()

    def _try_close(self) -> None:
        """未保存確認後に閉じる"""
        if self._dirty:
            from PySide6.QtWidgets import QMessageBox
            reply = QMessageBox.question(
                self, "未保存の変更",
                "変更が保存されていません。保存せずに閉じますか？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        self.accept()

    def _set_dirty(self) -> None:
        self._dirty = True

    def closeEvent(self, event: QCloseEvent) -> None:
        """ウィンドウの × ボタンで閉じようとしたとき未保存確認"""
        if self._dirty:
            from PySide6.QtWidgets import QMessageBox
            reply = QMessageBox.question(
                self, "未保存の変更",
                "変更が保存されていません。保存せずに閉じますか？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        super().closeEvent(event)

    def _on_load(self) -> None:
        """エディタにロードして閉じる"""
        self.load_requested.emit(self._gen_id)
        self.accept()

    # ── 外部 API ─────────────────────────────────────────

    @property
    def was_saved(self) -> bool:
        """ダイアログを閉じる前にレビューが保存されたか"""
        return getattr(self, "_saved", False)
