"""
モデル・LoRAブラウザ + LoRAチップバー

構成:
  ModelBrowser  — 左ペイン「モデル」タブ。DBのモデル一覧を表示し、
                  ダブルクリックで選択シグナルを発行する。
  LoRABrowser   — 左ペイン「LoRA」タブ。選択/解除をチップバーに反映。
  LoRAChipBar   — Row2 直下に配置する折り返しチップバー。
                  有効/無効トグル・weight編集・削除に対応。
"""
from __future__ import annotations

import io
import re
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QMenu, QInputDialog, QMessageBox,
    QFrame, QCheckBox, QSizePolicy, QFileDialog, QAbstractItemView,
    QDialog, QTextEdit, QLineEdit, QDialogButtonBox, QScrollArea,
    QComboBox, QToolButton, QStyledItemDelegate, QStyle, QApplication,
)
from PySide6.QtCore import Qt, QSize, QPoint, Signal, QThread, QTimer, QMimeData, QBuffer, QByteArray
from PySide6.QtGui import QPixmap, QIcon, QColor, QPainter, QPen, QBrush, QLinearGradient, QDrag, QDragEnterEvent, QDropEvent

import db.app_db as _app_db
import db.env_db as _env_db
from core.i18n import tr
import ui.styles as _styles
from ui.styles import (
    SURFACE0, SURFACE1, SURFACE2, TEXT, SUBTEXT, ACCENT, GREEN,
    ui_font,
)

if TYPE_CHECKING:
    from api.invoke_client import InvokeClient

_THUMB_SIZE = 64
_DEFAULT_LORA_THUMBS: dict[int, QPixmap] = {}
_MODEL_TITLE_ROLE = Qt.ItemDataRole.UserRole + 20
_MODEL_COMMENT_ROLE = Qt.ItemDataRole.UserRole + 21
_MODEL_CURRENT_ROLE = Qt.ItemDataRole.UserRole + 22
LORA_CHIP_MIME = "application/x-promptmosaic-lora-chip"

# ベースモデル表示名マッピング
_BASE_DISPLAY: dict[str, str] = {
    "sdxl":         "SDXL",
    "sd-1":         "SD 1.x",
    "sd-2":         "SD 2.x",
    "flux":         "FLUX",
    "flux2":        "FLUX2",
    "anima":        "Anima",
    "qwen-image":   "Qwen-Image",
    "z-image":      "Z-Image",
    "sdxl-refiner": "SDXL Refiner",  # フィルタされるが念のため
}

def _base_label(base: str) -> str:
    """ベースモデル識別子 → 表示名"""
    return _BASE_DISPLAY.get(base, base.upper() if base else tr("model_browser.base_unknown"))


# ── LoRA 大分類ジャンル定義 ───────────────────────────────────────────────────

_LORA_GENRES: list[tuple[str, str]] = [
    ("character_identity",            tr("lora_genre.character_identity")),
    ("human_expression",              tr("lora_genre.human_expression")),
    ("pose_action_interaction",       tr("lora_genre.pose_action_interaction")),
    ("clothing_accessory",            tr("lora_genre.clothing_accessory")),
    ("living_creature",               tr("lora_genre.living_creature")),
    ("object_artifact",               tr("lora_genre.object_artifact")),
    ("architecture_structure",        tr("lora_genre.architecture_structure")),
    ("location_background",           tr("lora_genre.location_background")),
    ("natural_feature",               tr("lora_genre.natural_feature")),
    ("phenomenon_event",              tr("lora_genre.phenomenon_event")),
    ("era_culture_worldview",         tr("lora_genre.era_culture_worldview")),
    ("art_style_medium",              tr("lora_genre.art_style_medium")),
    ("lighting_color_screen_effect",  tr("lora_genre.lighting_color_screen_effect")),
    ("quality_correction",            tr("lora_genre.quality_correction")),
    ("mixed_unsorted",                tr("lora_genre.mixed_unsorted")),
]

# key → 表示ラベル
_GENRE_LABEL: dict[str, str] = {k: v for k, v in _LORA_GENRES}
# key → ソート順（0始まり）
_GENRE_ORDER: dict[str, int] = {k: i for i, (k, _) in enumerate(_LORA_GENRES)}


def _genre_sort_case() -> str:
    """ジャンルキーを定義順にソートする SQL CASE 式を返す"""
    cases = " ".join(
        f"WHEN '{k}' THEN {i}"
        for i, (k, _) in enumerate(_LORA_GENRES)
    )
    return f"CASE COALESCE(lora_genre,'mixed_unsorted') {cases} ELSE 98 END"


def _lora_genres_from_db() -> list[tuple[str, str]]:
    rows = _env_db.fetchall(
        "SELECT key, label FROM lora_genres WHERE parent_id IS NULL "
        "ORDER BY sort_order, label"
    )
    if rows:
        return [(r["key"], r["label"]) for r in rows]
    return list(_LORA_GENRES)


def _lora_genre_label_map() -> dict[str, str]:
    return {key: label for key, label in _lora_genres_from_db()}


def _make_lora_genre_key(label: str) -> str:
    base = re.sub(r"[^0-9A-Za-z_]+", "_", label.strip().lower()).strip("_")
    if not base:
        base = "genre"
    key = base
    n = 2
    while _env_db.fetchone("SELECT 1 FROM lora_genres WHERE key=?", (key,)):
        key = f"{base}_{n}"
        n += 1
    return key


def _show_nsfw() -> bool:
    """app_settings の show_nsfw 設定を返す（デフォルト=非表示）"""
    row = _app_db.fetchone("SELECT value FROM app_settings WHERE key='show_nsfw'")
    return (row["value"] == "1") if row else False


# ── バックグラウンド同期ワーカー ───────────────────────────────────────────────

class _SyncWorker(QThread):
    """
    Invoke APIからモデル/LoRA一覧を取得してDBを更新するワーカー。

    Signals:
        finished(): 正常完了
        error(str): エラーメッセージ
    """
    finished = Signal()
    error    = Signal(str)

    def __init__(self, client: "InvokeClient", parent=None):
        super().__init__(parent)
        self._client = client

    def run(self) -> None:
        try:
            # Invoke 6.13 以降、フィルターなしの /api/v2/models/ は 0 件を返す。
            # クエリパラメータ名も変更された（type → model_type, base → base_models）。
            # main / lora を明示指定して個別取得し、結合して処理する。
            # ゼロ件の場合は DB を壊さないよう中断する。
            models: list = []
            for mtype in ("main", "lora"):
                data = self._client.models_list(model_type=mtype)
                models.extend(data.get("models") or data.get("items", []))

            if not models:
                raise RuntimeError(
                    "Invoke が main/lora モデルを 0 件返しました。"
                    "同期を中止します（既存データを保護）。"
                )

            invoke_keys: set[str] = set()
            for m in models:
                key = m.get("key") or m.get("id", "")
                if not key:
                    continue
                # API レスポンスの type フィールドをそのまま使う
                mtype = m.get("type", "")
                mbase = m.get("base", "")
                if mtype not in ("main", "lora"):
                    continue   # vae / controlnet / ip_adapter 等はスキップ
                # SDXL Refiner はモデルとして扱わない（別用途のモデル）
                if mtype == "main" and mbase == "sdxl-refiner":
                    continue
                invoke_keys.add(key)
                _env_db.execute(
                    """
                    INSERT INTO models
                        (invoke_key, invoke_hash, name, base, type, variant, available, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
                    ON CONFLICT(invoke_key) DO UPDATE SET
                        invoke_hash = excluded.invoke_hash,
                        name        = excluded.name,
                        base        = excluded.base,
                        type        = excluded.type,
                        variant     = excluded.variant,
                        available   = 1,
                        updated_at  = CURRENT_TIMESTAMP
                    """,
                    (key, m.get("hash", ""), m.get("name", key),
                     m.get("base", "sdxl"), mtype, m.get("variant") or None),
                )

            # Invokeに存在しないモデルを「削除済み」に
            if invoke_keys:
                placeholders = ",".join("?" * len(invoke_keys))
                _env_db.execute(
                    f"UPDATE models SET available=0 WHERE invoke_key NOT IN ({placeholders})",
                    tuple(invoke_keys),
                )

            self.finished.emit()
        except Exception as exc:
            self.error.emit(str(exc))


# ── 折り返しウィジェット (WrappingButtonBar と同じ仕組み) ─────────────────────

class _FlowWidget(QWidget):
    """
    子ウィジェットを左から右に並べ、右端で自動折り返しする。
    高さは内容に応じて自動調整される。
    """

    def __init__(self, h_gap: int = 4, v_gap: int = 4, parent=None):
        super().__init__(parent)
        self._items: list[QWidget] = []
        self._h_gap = h_gap
        self._v_gap = v_gap
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def addWidget(self, widget: QWidget) -> None:
        self._items.append(widget)
        widget.setParent(self)
        widget.show()
        self._reflow()

    def clearWidgets(self) -> None:
        for w in self._items:
            w.setParent(None)  # type: ignore[arg-type]
            w.deleteLater()
        self._items.clear()
        self.setFixedHeight(0)

    def _reflow(self) -> None:
        avail_w = max(1, self.width())
        x, y, row_h = 0, 0, 0
        for w in self._items:
            sh = w.sizeHint()
            bw, bh = max(sh.width(), 1), max(sh.height(), 1)
            if x + bw > avail_w and x > 0:
                x = 0
                y += row_h + self._v_gap
                row_h = 0
            w.move(x, y)
            w.resize(bw, bh)
            row_h = max(row_h, bh)
            x += bw + self._h_gap
        total_h = (y + row_h) if self._items else 0
        self.setFixedHeight(max(0, total_h))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._reflow()


# ── LoRA チップ ───────────────────────────────────────────────────────────────

class _LoRAChip(QFrame):
    """
    LoRAChipBar の1チップ。
    有効/無効チェックボックス・名前・weight・削除ボタンを持つ。
    """
    toggled_enabled = Signal(str, bool)   # (invoke_key, enabled)
    weight_changed  = Signal(str, float)  # (invoke_key, new_weight)
    remove_clicked  = Signal(str)         # (invoke_key)

    def __init__(self, lora: dict, parent=None, *, readonly: bool = False):
        super().__init__(parent)
        self._key     = lora["invoke_key"]
        self._name    = lora.get("name", "?")
        self._weight  = float(lora.get("weight", 0.75))
        self._enabled = bool(lora.get("enabled", True))
        self._readonly = bool(readonly)
        self._drag_start: QPoint | None = None
        self._build()

    def update_state(self, enabled: bool, weight: float) -> None:
        self._enabled = enabled
        self._weight  = weight
        self._weight_btn.setText(f"{weight:.2f}")
        self._refresh_style()

    def _build(self) -> None:
        hlay = QHBoxLayout(self)
        hlay.setContentsMargins(4, 2, 4, 2)
        hlay.setSpacing(3)

        # ① ON/OFF トグルボタン（TileWidget / GroupWidget と同スタイル）
        self._toggle_btn = QPushButton("ON" if self._enabled else "OFF")
        self._toggle_btn.setFixedSize(36, 17)
        self._toggle_btn.setFont(ui_font(-2, bold=True))
        self._toggle_btn.setToolTip(tr("model_browser.lora_toggle_tooltip"))
        self._toggle_btn.clicked.connect(self._toggle_enabled)
        hlay.addWidget(self._toggle_btn)

        # ② 削除ボタン（タグタイルと同じ位置 = ON/OFF の直後）
        rm = QPushButton("✕")
        rm.setFixedSize(16, 16)
        rm.setFont(ui_font(-2, bold=True))
        rm.setStyleSheet(
            "QPushButton { background: #3a2a2a; color: #f38ba8; "
            "border: 1px solid #8a4a4a; border-radius: 3px; padding: 0; }"
            "QPushButton:hover { background: #f38ba8; color: #1e1e2e; }"
        )
        rm.clicked.connect(lambda: self.remove_clicked.emit(self._key))
        hlay.addWidget(rm)

        # ③ 名前
        self._name_lbl = QLabel(self._name)
        self._name_lbl.setFont(ui_font(-1))
        self._name_lbl.setMaximumWidth(130)
        self._name_lbl.setStyleSheet("background: transparent; border: none;")
        hlay.addWidget(self._name_lbl)

        # ④ Weight ボタン（クリックで編集・幅を広めに確保）
        self._weight_btn = QPushButton(f"{self._weight:.2f}")
        self._weight_btn.setFixedSize(54, 18)
        self._weight_btn.setFont(ui_font(-2))
        self._weight_btn.setToolTip(tr("model_browser.lora_weight_tooltip"))
        self._weight_btn.setStyleSheet(
            "QPushButton { padding: 0 2px; }"
        )
        self._weight_btn.clicked.connect(self._edit_weight)
        hlay.addWidget(self._weight_btn)

        if self._readonly:
            self._toggle_btn.hide()
            rm.hide()
            self._weight_btn.setEnabled(False)
            self.setCursor(Qt.CursorShape.OpenHandCursor)

        # 全ウィジェット生成後にスタイル適用
        self._refresh_style()

    def _refresh_style(self) -> None:
        if self._enabled:
            self.setStyleSheet(
                f"QFrame {{ background: {SURFACE2}; border-radius: 4px; "
                f"border: 1px solid #89b4fa; }}"
            )
            self._toggle_btn.setText("ON")
            self._toggle_btn.setStyleSheet(
                "QPushButton { background: #1a3a1a; color: #a6e3a1; "
                "border: 1px solid #4a8a4a; border-radius: 2px; padding: 0 2px; }"
                "QPushButton:hover { background: #2a5a2a; }"
            )
        else:
            self.setStyleSheet(
                f"QFrame {{ background: {SURFACE1}; border-radius: 4px; "
                f"border: 1px solid {SURFACE2}; }}"
            )
            self._toggle_btn.setText("OFF")
            self._toggle_btn.setStyleSheet(
                "QPushButton { background: #2a2a3a; color: #a0a0c0; "
                "border: 1px solid #6060a0; border-radius: 2px; padding: 0 2px; }"
                "QPushButton:hover { color: #cdd6f4; border-color: #a6adc8; }"
            )

    def _toggle_enabled(self) -> None:
        if self._readonly:
            return
        self._enabled = not self._enabled
        self._refresh_style()
        self.toggled_enabled.emit(self._key, self._enabled)

    def _edit_weight(self) -> None:
        if self._readonly:
            return
        from ui.styles import SURFACE0, SURFACE2, TEXT, SUBTEXT
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("model_browser.lora_weight_title", name=self._name))
        dlg.setFixedWidth(260)
        dlg.setStyleSheet(f"QDialog {{ background-color: {SURFACE0}; }}")
        root = QVBoxLayout(dlg)
        root.setSpacing(6)

        root.addWidget(QLabel(tr("model_browser.lora_weight_label", name=self._name)))
        row = QHBoxLayout()
        edit = QLineEdit(f"{self._weight:.4g}")
        row.addWidget(edit)

        _ss = (
            "QPushButton { background: transparent; color: #cdd6f4; "
            "border: 1px solid #45475a; border-radius: 3px; padding: 0 4px; }"
            "QPushButton:hover { background: #45475a; }"
        )
        def _step(delta: float) -> None:
            try:
                v = float(edit.text())
            except ValueError:
                v = self._weight
            edit.setText(f"{v + delta:.4g}")
        btn_up = QPushButton("▲")
        btn_up.setFixedSize(28, 26)
        btn_up.setFont(ui_font(0))
        btn_up.setToolTip("+0.05")
        btn_up.setStyleSheet(_ss)
        btn_up.clicked.connect(lambda: _step(0.05))
        btn_dn = QPushButton("▼")
        btn_dn.setFixedSize(28, 26)
        btn_dn.setFont(ui_font(0))
        btn_dn.setToolTip("−0.05")
        btn_dn.setStyleSheet(_ss)
        btn_dn.clicked.connect(lambda: _step(-0.05))
        row.addWidget(btn_up)
        row.addWidget(btn_dn)
        root.addLayout(row)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        root.addWidget(btns)

        edit.setFocus(); edit.selectAll()
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            w = float(edit.text())
        except ValueError:
            return
        w = max(-1.0, min(2.0, w))
        self._weight = w
        self._weight_btn.setText(f"{w:.2f}")
        self.weight_changed.emit(self._key, w)

    def mousePressEvent(self, event) -> None:
        if self._readonly and event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.pos()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_start = None
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if not self._readonly:
            super().mouseMoveEvent(event)
            return
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            super().mouseMoveEvent(event)
            return
        if self._drag_start is None:
            super().mouseMoveEvent(event)
            return
        if (event.pos() - self._drag_start).manhattanLength() < QApplication.startDragDistance():
            super().mouseMoveEvent(event)
            return
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(LORA_CHIP_MIME, self._key.encode("utf-8"))
        drag.setMimeData(mime)
        drag.setPixmap(self.grab())
        drag.setHotSpot(self._drag_start)
        drag.exec(Qt.DropAction.CopyAction)
        self._drag_start = None


# ── LoRA チップバー ───────────────────────────────────────────────────────────

class LoRAChipBar(QWidget):
    """
    選択中のLoRAをチップとして折り返し表示するバー。
    ウィンドウ下部 (Row 2 直下) に配置する想定。

    Signals:
        changed():         LoRAリストが変更された
        add_requested():   「+追加」ボタンが押された
    """
    changed              = Signal()
    add_requested        = Signal()
    lora_enabled_changed = Signal(str, bool)  # (invoke_key, enabled)
    lora_removed         = Signal(str)         # (invoke_key)
    history_lora_dropped = Signal(str)         # (invoke_key)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loras: list[dict] = []  # {invoke_key, name, base, weight, enabled}
        self._split_mode = False
        self._history_loras: list[dict] = []
        self._build()

    # ── Public API ─────────────────────────────────────────────────────
    def get_loras(self) -> list[dict]:
        """現在のLoRAリスト (deep copy) を返す。"""
        return [dict(l) for l in self._loras]

    def set_loras(self, loras: list[dict]) -> None:
        """LoRAリストを置き換える（履歴ロード時などに使用）。"""
        self._loras = [dict(l) for l in loras]
        self._rebuild_chips()
        self.changed.emit()

    def add_lora(self, info: dict) -> None:
        """LoRAを追加する（重複は無視）。"""
        if any(l["invoke_key"] == info["invoke_key"] for l in self._loras):
            return
        self._loras.append({
            "invoke_key": info["invoke_key"],
            "name":       info.get("name", ""),
            "base":       info.get("base", "sdxl"),
            "weight":     float(info.get("weight", 0.75)),
            "enabled":    bool(info.get("enabled", True)),
        })
        self._rebuild_chips()
        self.changed.emit()

    def remove_lora(self, invoke_key: str) -> None:
        self._loras = [l for l in self._loras if l["invoke_key"] != invoke_key]
        self._rebuild_chips()
        self.changed.emit()
        self.lora_removed.emit(invoke_key)

    def get_selected_keys(self) -> set[str]:
        return {l["invoke_key"] for l in self._loras}

    def set_split_mode(self, enabled: bool, history_loras: list[dict] | None = None) -> None:
        self._split_mode = bool(enabled)
        self._history_loras = [dict(l) for l in (history_loras or [])]
        self._rebuild_chips()

    # ── 内部 ───────────────────────────────────────────────────────────
    def _build(self) -> None:
        self.setStyleSheet(
            f"LoRAChipBar {{ background: {SURFACE1}; border-top: 1px solid {SURFACE2}; }}"
        )
        self.setAcceptDrops(True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 2, 4, 2)
        outer.setSpacing(0)

        self._flow = _FlowWidget(h_gap=4, v_gap=3)
        outer.addWidget(self._flow)
        self._rebuild_chips()

    def _rebuild_chips(self) -> None:
        self._flow.clearWidgets()

        for lora in self._loras:
            chip = _LoRAChip(lora)
            chip.toggled_enabled.connect(self._on_chip_enabled)
            chip.weight_changed.connect(self._on_chip_weight)
            chip.remove_clicked.connect(self.remove_lora)
            self._flow.addWidget(chip)

        add_btn = QPushButton("+LoRA")
        add_btn.setFixedHeight(22)
        add_btn.setFont(ui_font(-1))
        add_btn.setStyleSheet(
            f"QPushButton {{ background: {SURFACE2}; color: {TEXT}; "
            f"border: 1px dashed {SUBTEXT}; border-radius: 3px; padding: 0 6px; }}"
            f"QPushButton:hover {{ border-color: {ACCENT}; color: {ACCENT}; }}"
        )
        add_btn.clicked.connect(self.add_requested.emit)
        self._flow.addWidget(add_btn)

        if self._split_mode:
            sep = QLabel(" | ")
            sep.setFont(ui_font(-1, bold=True))
            sep.setStyleSheet(f"color: {ACCENT}; padding: 0 4px;")
            self._flow.addWidget(sep)
            hist_label = QLabel(tr("model_browser.lora_history_section"))
            hist_label.setFont(ui_font(-1, bold=True))
            hist_label.setStyleSheet(f"color: {SUBTEXT};")
            self._flow.addWidget(hist_label)
            if self._history_loras:
                for lora in self._history_loras:
                    chip = _LoRAChip(lora, readonly=True)
                    self._flow.addWidget(chip)
            else:
                empty = QLabel(tr("model_browser.lora_history_empty"))
                empty.setFont(ui_font(-1))
                empty.setStyleSheet(f"color: {SUBTEXT};")
                self._flow.addWidget(empty)

        # バー全体の高さを flow に合わせる
        self.setVisible(True)

    def _on_chip_enabled(self, invoke_key: str, enabled: bool) -> None:
        for lora in self._loras:
            if lora["invoke_key"] == invoke_key:
                lora["enabled"] = enabled
                break
        self.changed.emit()
        self.lora_enabled_changed.emit(invoke_key, enabled)

    def _on_chip_weight(self, invoke_key: str, weight: float) -> None:
        for lora in self._loras:
            if lora["invoke_key"] == invoke_key:
                lora["weight"] = weight
                break
        self.changed.emit()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self._split_mode and event.mimeData().hasFormat(LORA_CHIP_MIME):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        if self._split_mode and event.mimeData().hasFormat(LORA_CHIP_MIME):
            try:
                key = bytes(event.mimeData().data(LORA_CHIP_MIME)).decode("utf-8")
            except UnicodeDecodeError:
                event.ignore()
                return
            self.history_lora_dropped.emit(key)
            event.acceptProposedAction()
            return
        super().dropEvent(event)


# ── 共通: サムネイル読み書き ──────────────────────────────────────────────────

def _compress_thumb(raw_bytes: bytes) -> bytes:
    """画像バイト列を 256×256 WebP quality=80 に圧縮して返す。"""
    from PIL import Image
    img = Image.open(io.BytesIO(raw_bytes))
    img.thumbnail((256, 256))
    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=80)
    return buf.getvalue()


def _load_thumb(invoke_key: str, size: int = _THUMB_SIZE) -> QPixmap | None:
    row = _env_db.fetchone(
        "SELECT thumbnail_data FROM models WHERE invoke_key=?", (invoke_key,)
    )
    if row and row["thumbnail_data"]:
        pix = QPixmap()
        if pix.loadFromData(bytes(row["thumbnail_data"])) and not pix.isNull():
            return pix.scaled(
                size, size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
    return None


def _save_thumb(invoke_key: str, raw_bytes: bytes) -> None:
    """圧縮してDBのBLOBとして保存する。"""
    thumb_bytes = _compress_thumb(raw_bytes)
    _env_db.execute(
        "UPDATE models SET thumbnail_data=?, updated_at=CURRENT_TIMESTAMP WHERE invoke_key=?",
        (sqlite3.Binary(thumb_bytes), invoke_key),
    )


def _default_lora_thumb(size: int = _THUMB_SIZE) -> QPixmap:
    """LoRA種別に寄りすぎない、汎用の抽象サムネイルを生成する。"""
    cached = _DEFAULT_LORA_THUMBS.get(size)
    if cached is not None:
        return cached

    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    grad = QLinearGradient(0, 0, size, size)
    grad.setColorAt(0.0, QColor(SURFACE2).lighter(118))
    grad.setColorAt(0.58, QColor(SURFACE1))
    grad.setColorAt(1.0, QColor(SURFACE0).darker(112))
    painter.setPen(QPen(QColor(SURFACE2).lighter(135), 1))
    painter.setBrush(QBrush(grad))
    painter.drawRoundedRect(1, 1, size - 2, size - 2, 10, 10)

    accent = QColor(ACCENT)
    soft = QColor(ACCENT)
    soft.setAlpha(54)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(soft))
    painter.drawEllipse(int(size * 0.17), int(size * 0.16), int(size * 0.34), int(size * 0.34))
    painter.drawEllipse(int(size * 0.49), int(size * 0.45), int(size * 0.32), int(size * 0.32))

    painter.setPen(QPen(QColor(SUBTEXT), max(1, size // 28)))
    painter.drawLine(int(size * 0.34), int(size * 0.35), int(size * 0.63), int(size * 0.58))
    painter.drawLine(int(size * 0.28), int(size * 0.66), int(size * 0.63), int(size * 0.58))

    painter.setPen(QPen(accent, max(1, size // 18)))
    painter.setBrush(QBrush(QColor(SURFACE0).lighter(112)))
    node_r = max(4, size // 11)
    for cx, cy in (
        (int(size * 0.33), int(size * 0.34)),
        (int(size * 0.65), int(size * 0.58)),
        (int(size * 0.28), int(size * 0.67)),
    ):
        painter.drawEllipse(cx - node_r, cy - node_r, node_r * 2, node_r * 2)

    painter.setPen(QPen(QColor(TEXT), 1))
    painter.drawRoundedRect(1, 1, size - 2, size - 2, 10, 10)
    painter.end()

    _DEFAULT_LORA_THUMBS[size] = pix
    return pix


def _set_thumb(invoke_key: str, src_path: str) -> None:
    """ファイルを読み込んで圧縮し、DBのBLOBとして保存する。"""
    raw = Path(src_path).read_bytes()
    _save_thumb(invoke_key, raw)


# ── 共通: sync ボタン付きヘッダー ─────────────────────────────────────────────

class _BrowserHeader(QHBoxLayout):
    """タイトルラベル + 同期ボタン の水平ヘッダー"""

    def __init__(self, title: str, sync_callback, parent=None):
        super().__init__(parent)
        self._lbl = QLabel(title)
        self._lbl.setFont(ui_font(bold=True))
        self._lbl.setStyleSheet(f"color: {ACCENT}; padding: 2px 4px;")
        self.addWidget(self._lbl)
        self.addStretch()
        self._sync_btn = QPushButton("")
        self._sync_btn.setFixedSize(24, 24)
        self._sync_btn.setIcon(QApplication.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        self._sync_btn.setIconSize(QSize(14, 14))
        self._sync_btn.setToolTip(tr("model_browser.sync_btn"))
        self._sync_btn.setFont(ui_font(-1, bold=True))
        self._sync_btn.setStyleSheet(
            f"QPushButton {{ background: {SURFACE0}; color: {ACCENT}; "
            f"border: 1px solid {SURFACE2}; border-radius: 3px; padding: 0; }}"
            f"QPushButton:hover {{ border-color: {ACCENT}; }}"
            f"QPushButton:disabled {{ color: {SUBTEXT}; }}"
        )
        self._sync_btn.clicked.connect(sync_callback)
        self.addWidget(self._sync_btn)

    def set_syncing(self, syncing: bool) -> None:
        self._sync_btn.setEnabled(not syncing)
        self._sync_btn.setToolTip(tr("model_browser.syncing_btn") if syncing else tr("model_browser.sync_btn"))

    def set_title(self, title: str) -> None:
        self._lbl.setText(title)
        self._lbl.setFont(ui_font(bold=True))
        self._lbl.setStyleSheet(f"color: {ACCENT}; padding: 2px 4px;")
        self._sync_btn.setText("")
        self._sync_btn.setIcon(QApplication.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        self._sync_btn.setToolTip(tr("model_browser.sync_btn"))


class _ModelListDelegate(QStyledItemDelegate):
    """Model rows with bold first line and compact parameter line."""

    def paint(self, painter, option, index) -> None:
        if index.data(_HDR_ROLE):
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            genre_key = index.data(_GRP_KEY) or "mixed_unsorted"
            color_key = "mixed_unsorted" if genre_key == "__unset__" else genre_key
            bg_hex, fg_hex = _styles.tag_browser_base_colors(color_key)
            rect = option.rect.adjusted(2, 2, -4, -2)
            painter.setBrush(QBrush(QColor(bg_hex)))
            painter.setPen(QPen(QColor(fg_hex), 1))
            painter.drawRoundedRect(rect, 4, 4)
            painter.setPen(QColor(fg_hex))
            painter.setFont(ui_font(-1, bold=True))
            painter.drawText(
                option.rect.adjusted(10, 0, -8, 0),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                index.data(Qt.ItemDataRole.DisplayRole) or "",
            )
            painter.restore()
            return
        painter.save()
        selected = bool(option.state & QStyle.StateFlag.State_Selected or index.data(_LORA_SELECTED))
        current = bool(index.data(_MODEL_CURRENT_ROLE))
        if selected:
            bg = QColor(SURFACE2)
        elif current:
            bg = QColor(36, 46, 50)
        else:
            bg = QColor(SURFACE0)
        painter.fillRect(option.rect, bg)
        if current:
            accent_rect = option.rect.adjusted(2, 3, -3, -3)
            painter.setPen(QPen(QColor(ACCENT), 2))
            painter.drawRoundedRect(accent_rect, 4, 4)
            painter.fillRect(option.rect.adjusted(2, 4, -(option.rect.width() - 5), -4), QColor(ACCENT))

        icon_size = max(48, option.decorationSize.width() or _THUMB_SIZE)
        thumb = option.rect.adjusted(4, 4, 0, 0)
        thumb.setSize(QSize(icon_size, icon_size))
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        if isinstance(icon, QIcon) and not icon.isNull():
            pix = icon.pixmap(icon_size, icon_size)
            painter.drawPixmap(thumb.left(), thumb.top(), pix)
        else:
            painter.fillRect(thumb, QColor(SURFACE1))

        text_left = option.rect.left() + icon_size + 12
        text_w = max(20, option.rect.right() - text_left - 4)
        line_h = option.fontMetrics.lineSpacing()
        y = option.rect.top() + max(4, (option.rect.height() - line_h * 3) // 2)
        name = str(index.data(Qt.ItemDataRole.UserRole + 1) or "")
        title = str(index.data(_MODEL_TITLE_ROLE) or "")
        params = str(index.data(Qt.ItemDataRole.UserRole + 4) or "")

        painter.setFont(ui_font(-1, bold=True))
        painter.setPen(QColor(TEXT))
        painter.drawText(text_left, y, text_w, line_h, Qt.AlignmentFlag.AlignLeft, name)
        painter.setFont(ui_font(-1))
        painter.setPen(QColor(TEXT if title else SUBTEXT))
        painter.drawText(text_left, y + line_h, text_w, line_h, Qt.AlignmentFlag.AlignLeft, title)
        painter.setFont(ui_font(-2))
        painter.setPen(QColor(SUBTEXT))
        painter.drawText(text_left, y + line_h * 2, text_w, line_h, Qt.AlignmentFlag.AlignLeft, params)
        painter.restore()

    def sizeHint(self, option, index) -> QSize:
        if index.data(_HDR_ROLE):
            return QSize(0, option.fontMetrics.lineSpacing() + 10)
        return QSize(0, max(_THUMB_SIZE + 8, option.fontMetrics.lineSpacing() * 3 + 12))


# ── ModelBrowser ──────────────────────────────────────────────────────────────

class ModelBrowser(QWidget):
    """
    メインモデル選択ブラウザ。

    Signals:
        model_chosen(invoke_key, name): ダブルクリックで選択されたモデル
    """
    model_chosen = Signal(str, str, str, str)  # (invoke_key, name, base, variant)

    def __init__(self, client: "InvokeClient | None" = None, parent=None):
        super().__init__(parent)
        self._client          = client
        self._sync_worker: _SyncWorker | None = None
        self._expanded_groups: set[str] = set()
        self._current_model_key = ""
        self._search_text     = ""
        self._build_ui()
        self.refresh()

    def set_client(self, client: "InvokeClient") -> None:
        self._client = client

    def set_current_model_key(self, invoke_key: str) -> None:
        """現在生成に使うモデルを、リスト選択とは別に強調表示する。"""
        invoke_key = invoke_key or ""
        if invoke_key == self._current_model_key:
            self._update_current_model_visual()
            return
        self._current_model_key = invoke_key
        self._update_current_model_visual()

    def reveal_current_model(self) -> None:
        """モデルタブを開いた時に、現在モデルのグループを展開して見える位置へ寄せる。"""
        if not self._current_model_key:
            return
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) != self._current_model_key:
                continue
            base = item.data(Qt.ItemDataRole.UserRole + 2) or ""
            if base:
                self._expanded_groups.add(base)
                self._apply_filter()
            item.setHidden(False)
            self._list.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)
            return

    # ── UI構築 ─────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        self._hdr = _BrowserHeader(tr("model_browser.model_header"), lambda: self._sync(notify=True))
        lay.addLayout(self._hdr)

        search_row = QHBoxLayout()
        search_row.setSpacing(2)
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText(tr("model_browser.model_search_placeholder"))
        self._search_edit.setFixedHeight(24)
        self._search_edit.setFont(ui_font(-1))
        self._search_edit.setStyleSheet(
            f"QLineEdit {{ background: {SURFACE0}; color: {TEXT}; "
            f"border: 1px solid {SURFACE2}; border-radius: 3px; padding: 0 4px; }}"
        )
        self._search_edit.textChanged.connect(self._on_search)
        search_row.addWidget(self._search_edit)

        self._model_clear_btn = QToolButton()
        self._model_clear_btn.setText("×")
        self._model_clear_btn.setFixedSize(24, 24)
        self._model_clear_btn.setToolTip(tr("model_browser.search_clear_tooltip"))
        self._model_clear_btn.setVisible(False)
        self._model_clear_btn.clicked.connect(self._search_edit.clear)
        self._search_edit.textChanged.connect(
            lambda t: self._model_clear_btn.setVisible(bool(t))
        )
        search_row.addWidget(self._model_clear_btn)
        lay.addLayout(search_row)

        self._list = QListWidget()
        self._list.setItemDelegate(_ModelListDelegate(self._list))
        self._list.setIconSize(QSize(_THUMB_SIZE, _THUMB_SIZE))
        self._list.setSpacing(2)
        self._list.setFont(ui_font())
        self._list.setStyleSheet(
            f"QListWidget {{ background: {SURFACE0}; border: 1px solid {SURFACE2}; "
            f"border-radius: 4px; }}"
            f"QListWidget::item {{ padding: 2px; }}"
            f"QListWidget::item:selected {{ background: {SURFACE2}; }}"
        )
        self._list.itemDoubleClicked.connect(self._on_double_click)
        self._list.itemClicked.connect(self._on_item_clicked)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_context_menu)
        self._list.setMouseTracking(True)
        self._list.viewport().setMouseTracking(True)
        self._list.viewport().installEventFilter(self)
        lay.addWidget(self._list, stretch=1)

        self._inline_title_edit = QLineEdit(self._list.viewport())
        self._inline_title_edit.hide()
        self._inline_title_edit.editingFinished.connect(self._finish_inline_title_edit)
        self._inline_title_item: QListWidgetItem | None = None
        self._memo_timer = QTimer(self)
        self._memo_timer.setSingleShot(True)
        self._memo_timer.setInterval(500)
        self._memo_timer.timeout.connect(self._show_memo_tip)
        self._memo_item: QListWidgetItem | None = None
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
        return super().eventFilter(obj, event)

    def _thumb_rect(self, item: QListWidgetItem):
        rect = self._list.visualItemRect(item)
        from PySide6.QtCore import QRect
        return QRect(rect.left() + 4, rect.top() + max(0, (rect.height() - _THUMB_SIZE) // 2), _THUMB_SIZE, _THUMB_SIZE)

    def _title_rect(self, item: QListWidgetItem):
        rect = self._list.visualItemRect(item)
        fm = self._list.fontMetrics()
        return rect.adjusted(_THUMB_SIZE + 12, fm.lineSpacing() + 4, -4, -(rect.height() - fm.lineSpacing() * 2 - 6))

    def _schedule_memo_tip(self, pos: QPoint) -> None:
        item = self._list.itemAt(pos)
        if not item or item.data(_HDR_ROLE):
            self._hide_memo_tip()
            return
        memo = str(item.data(_MODEL_COMMENT_ROLE) or "").strip()
        if not memo or not self._thumb_rect(item).contains(pos):
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
        memo = str(self._memo_item.data(_MODEL_COMMENT_ROLE) or "").strip()
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

    def _start_inline_title_edit(self, item: QListWidgetItem) -> None:
        key = item.data(Qt.ItemDataRole.UserRole)
        if not key:
            return
        self._inline_title_item = item
        self._inline_title_edit.setGeometry(self._title_rect(item))
        self._inline_title_edit.setText(item.data(_MODEL_TITLE_ROLE) or "")
        self._inline_title_edit.show()
        self._inline_title_edit.setFocus()
        self._inline_title_edit.selectAll()

    def _finish_inline_title_edit(self) -> None:
        item = self._inline_title_item
        if item is None:
            return
        self._inline_title_item = None
        self._inline_title_edit.hide()
        key = item.data(Qt.ItemDataRole.UserRole)
        if not key:
            return
        title = self._inline_title_edit.text().strip()
        _env_db.execute(
            "UPDATE models SET title=?, updated_at=CURRENT_TIMESTAMP WHERE invoke_key=?",
            (title or None, key),
        )
        item.setData(_MODEL_TITLE_ROLE, title)
        self._refresh_model_item_text(item)

    def _refresh_model_item_text(self, item: QListWidgetItem) -> None:
        name = item.data(Qt.ItemDataRole.UserRole + 1) or ""
        title = item.data(_MODEL_TITLE_ROLE) or ""
        params = item.data(Qt.ItemDataRole.UserRole + 4) or ""
        lines = [name, title]
        if params:
            lines.append(params)
        item.setText("\n".join(lines))

    def _on_search(self, text: str) -> None:
        self._search_text = text
        self._apply_filter()

    def _apply_filter(self) -> None:
        q = self._search_text.strip().lower()
        n = self._list.count()

        if q:
            # 検索モード: マッチしたアイテムを表示、所属グループを自動展開
            for i in range(n):
                item = self._list.item(i)
                if item.data(_HDR_ROLE):
                    continue
                key = item.data(Qt.ItemDataRole.UserRole)
                if not key:
                    continue
                item.setHidden(q not in item.text().lower())

            # マッチアイテムが属するグループを収集
            matched_groups: set[str] = set()
            current_grp: str | None = None
            for i in range(n):
                item = self._list.item(i)
                if item.data(_HDR_ROLE):
                    current_grp = item.data(_GRP_KEY)
                    continue
                if current_grp and not item.isHidden():
                    matched_groups.add(current_grp)

            # ヘッダー: マッチグループは展開表示、それ以外は非表示
            for i in range(n):
                item = self._list.item(i)
                if not item.data(_HDR_ROLE):
                    continue
                grp_key = item.data(_GRP_KEY)
                label   = item.data(_GRP_LABEL)
                if grp_key in matched_groups:
                    item.setHidden(False)
                    item.setText(f"▼ {label}")
                else:
                    item.setHidden(True)

            # マッチしないグループの子アイテムも非表示に
            current_grp = None
            group_visible = False
            for i in range(n):
                item = self._list.item(i)
                if item.data(_HDR_ROLE):
                    current_grp = item.data(_GRP_KEY)
                    group_visible = (current_grp in matched_groups)
                    continue
                if not group_visible:
                    item.setHidden(True)

        else:
            # ブラウズモード: _expanded_groups の状態に従って表示/非表示
            current_expanded = False
            for i in range(n):
                item = self._list.item(i)
                if item.data(_HDR_ROLE):
                    grp_key = item.data(_GRP_KEY)
                    label   = item.data(_GRP_LABEL)
                    current_expanded = (grp_key in self._expanded_groups)
                    item.setText(f"{'▼' if current_expanded else '▶'} {label}")
                    item.setHidden(False)
                else:
                    item.setHidden(not current_expanded)

    # ── データ ─────────────────────────────────────────────────────────
    def refresh(self) -> None:
        self._list.clear()

        nsfw_clause = "" if _show_nsfw() else "AND COALESCE(is_nsfw,0)=0"

        # 常に利用可能なモデルを全件表示
        sql = (f"SELECT * FROM models "
               f"WHERE type='main' AND COALESCE(base,'') != 'sdxl-refiner' "
               f"  AND available=1 "
               f"  {nsfw_clause} "
               f"ORDER BY COALESCE(base,'zzz'), name")

        rows = _env_db.fetchall(sql)

        current_base: str | None = None
        for row in rows:
            base = row["base"] or ""
            if base != current_base:
                current_base = base
                label = _base_label(base)
                hdr = QListWidgetItem(f"▶ {label}")
                hdr.setFlags(Qt.ItemFlag.ItemIsEnabled)
                hdr.setFont(ui_font(bold=True))
                hdr.setForeground(QColor(ACCENT))
                hdr.setData(_HDR_ROLE, True)
                hdr.setData(_GRP_KEY,  base)
                hdr.setData(_GRP_LABEL, label)
                self._list.addItem(hdr)
            self._add_item(row)

        if self._current_model_key:
            for row in rows:
                if row["invoke_key"] == self._current_model_key:
                    self._expanded_groups.add(row["base"] or "")
                    break
        self._apply_filter()
        self._update_current_model_visual()

    def retranslate_and_restyle(self) -> None:
        self._hdr.set_title(tr("model_browser.model_header"))
        self._search_edit.setPlaceholderText(tr("model_browser.model_search_placeholder"))
        self._model_clear_btn.setToolTip(tr("model_browser.search_clear_tooltip"))
        self.refresh()

    def _add_item(self, row) -> None:
        name      = row["name"] or row["invoke_key"]
        title     = row["title"] or ""
        comment   = row["comment"] or ""
        available = bool(row["available"])
        key       = row["invoke_key"]
        param_parts = []
        if row["default_steps"] is not None:
            param_parts.append(f"Steps {row['default_steps']}")
        if row["default_cfg"] is not None:
            param_parts.append(f"CFG {row['default_cfg']:g}")
        if row["default_scheduler"]:
            param_parts.append(str(row["default_scheduler"]))
        params = "  ".join(param_parts)

        lines = [name, title]
        if params:
            lines.append(params)
        if not available:
            lines.append(tr("model_browser.model_deleted"))

        item = QListWidgetItem("\n".join(lines))
        item.setData(Qt.ItemDataRole.UserRole,     key)
        item.setData(Qt.ItemDataRole.UserRole + 1, name)
        item.setData(Qt.ItemDataRole.UserRole + 2, row["base"] or "sdxl")
        item.setData(Qt.ItemDataRole.UserRole + 3, row["variant"] or "")
        item.setData(Qt.ItemDataRole.UserRole + 4, params)
        item.setData(_MODEL_TITLE_ROLE, title)
        item.setData(_MODEL_COMMENT_ROLE, comment)
        item.setData(_MODEL_CURRENT_ROLE, key == self._current_model_key)
        item.setSizeHint(QSize(0, max(_THUMB_SIZE + 8, 76)))

        pix = _load_thumb(key)
        if pix:
            item.setIcon(QIcon(pix))

        if not available:
            item.setForeground(QColor(SUBTEXT))
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)

        self._list.addItem(item)

    def _update_current_model_visual(self) -> None:
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.data(_HDR_ROLE):
                continue
            key = item.data(Qt.ItemDataRole.UserRole) or ""
            item.setData(_MODEL_CURRENT_ROLE, bool(key and key == self._current_model_key))
        self._list.viewport().update()

    # ── イベント ────────────────────────────────────────────────────────
    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        """ヘッダークリックでグループを展開/折りたたむ。"""
        if not item.data(_HDR_ROLE):
            return
        grp_key = item.data(_GRP_KEY)
        if grp_key in self._expanded_groups:
            self._expanded_groups.discard(grp_key)
        else:
            self._expanded_groups.add(grp_key)
        self._apply_filter()

    def _on_double_click(self, item: QListWidgetItem) -> None:
        if not (item.flags() & Qt.ItemFlag.ItemIsEnabled):
            return
        key     = item.data(Qt.ItemDataRole.UserRole)
        name    = item.data(Qt.ItemDataRole.UserRole + 1)
        base    = item.data(Qt.ItemDataRole.UserRole + 2) or "sdxl"
        variant = item.data(Qt.ItemDataRole.UserRole + 3) or ""
        if not key:
            return  # ヘッダー行はスキップ
        self.model_chosen.emit(key, name, base, variant)

    def _on_context_menu(self, pos: QPoint) -> None:
        item = self._list.itemAt(pos)
        if item is None:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        if not key:
            return  # ヘッダー行はスキップ
        base = item.data(Qt.ItemDataRole.UserRole + 2) or ""
        menu = QMenu(self)
        menu.addAction(tr("model_browser.model_annotate_action")).triggered.connect(
            lambda: self._annotate_model(key))
        menu.addSeparator()
        menu.addAction(tr("model_browser.template_reset_action")).triggered.connect(
            lambda: self._reset_template(key, base))
        menu.exec(self._list.viewport().mapToGlobal(pos))

    def _reset_template(self, invoke_key: str, base: str) -> None:
        """
        このモデルに対応するテンプレートキャッシュを削除する。
        次回生成時にInvokeから再取得される（VAEや設定を間違えて保存してしまった時の救済策）。
        """
        from pathlib import Path
        from api.invoke_client import InvokeClient

        files_to_delete: list[Path] = []

        # DB に記録された cache_key を優先参照（flux2 4B/9B の判別など）
        row = _env_db.fetchone(
            "SELECT template_cache_key FROM models WHERE invoke_key=?",
            (invoke_key,),
        )
        db_cache_key = row["template_cache_key"] if row else None
        if db_cache_key:
            files_to_delete.append(InvokeClient._template_cache_path(db_cache_key))
        elif base:
            # DB未記録の場合はベース名からファイルを推測
            safe_base = base.replace("-", "_")
            files_to_delete.append(InvokeClient._template_cache_path(safe_base))

        existing = [f for f in files_to_delete if f.exists()]
        if not existing:
            QMessageBox.information(
                self, tr("model_browser.template_reset_title"),
                tr("model_browser.template_reset_none"),
            )
            return

        names = "\n".join(f.name for f in existing)
        reply = QMessageBox.question(
            self, tr("model_browser.template_reset_title"),
            tr("model_browser.template_reset_confirm", names=names),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        for f in existing:
            try:
                f.unlink()
            except Exception as exc:
                QMessageBox.warning(self, tr("model_browser.delete_failed_title"), f"{f.name}\n{exc}")
                return

        # DB の template_cache_key もクリア
        _env_db.execute(
            "UPDATE models SET template_cache_key=NULL WHERE invoke_key=?",
            (invoke_key,),
        )

        QMessageBox.information(
            self, tr("model_browser.template_reset_title"),
            tr("model_browser.template_reset_done"),
        )

    def _annotate_model(self, key: str) -> None:
        dlg = _ModelAnnotateDialog(self, key=key)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        v = dlg.result_values
        _env_db.execute(
            "UPDATE models SET title=?, comment=?, is_nsfw=?, "
            "default_steps=?, default_cfg=?, default_scheduler=?, "
            "updated_at=CURRENT_TIMESTAMP WHERE invoke_key=?",
            (v["title"] or None, v["comment"], v["is_nsfw"],
             v["default_steps"], v["default_cfg"], v["default_scheduler"], key),
        )
        # 自動読み込みLoRAを保存（削除→再挿入）
        _env_db.execute(
            "DELETE FROM model_auto_loras WHERE model_key=?", (key,)
        )
        for i, entry in enumerate(v["auto_loras"]):
            _env_db.execute(
                "INSERT OR IGNORE INTO model_auto_loras (model_key, lora_key, weight, sort_order) "
                "VALUES (?, ?, ?, ?)",
                (key, entry["lora_key"], entry["weight"], i),
            )
        self.refresh()

    # ── 同期 ────────────────────────────────────────────────────────────
    def _sync(self, notify: bool = False) -> None:
        """notify=True はユーザーが同期ボタンを押した場合（完了ダイアログを出す）。
        プログラム起点の自動同期（接続時など）はダイアログを出さない。"""
        if not self._client:
            QMessageBox.warning(self, tr("model_browser.sync_title"), tr("model_browser.sync_not_connected"))
            return
        self._sync_notify = notify
        self._hdr.set_syncing(True)
        self._sync_worker = _SyncWorker(self._client)
        self._sync_worker.finished.connect(self._on_sync_done)
        self._sync_worker.error.connect(self._on_sync_error)
        self._sync_worker.start()

    def _on_sync_done(self) -> None:
        self._hdr.set_syncing(False)
        self.refresh()
        if getattr(self, "_sync_notify", False):
            self._sync_notify = False
            QMessageBox.information(
                self,
                tr("model_browser.sync_done_title"),
                tr("model_browser.sync_done_msg"),
            )

    def _on_sync_error(self, msg: str) -> None:
        self._sync_notify = False
        self._hdr.set_syncing(False)
        QMessageBox.warning(self, tr("model_browser.sync_error_title"), msg)


# ── LoRA 統合編集ダイアログ ──────────────────────────────────────────────────

class _TriggerSetRow(QFrame):
    """トリガーワードセットの1行（見出し + タグ文字列 + 削除ボタン）"""

    remove_requested = Signal(object)  # self

    def __init__(self, label: str, trigger_words: str, is_first: bool = False, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(138)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            f"QFrame {{ background: {SURFACE1}; border: 1px solid {SURFACE2}; "
            f"border-radius: 4px; }}"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(3)

        # 見出し行
        hdr = QHBoxLayout()
        hdr.setSpacing(4)
        lbl_lbl = QLabel(tr("model_browser.trigger_label_heading"))
        lbl_lbl.setFont(ui_font(-1))
        lbl_lbl.setFixedWidth(44)
        hdr.addWidget(lbl_lbl)
        self._label_edit = QLineEdit(label)
        self._label_edit.setPlaceholderText(tr("model_browser.trigger_default_placeholder") if is_first else "Custom")
        self._label_edit.setFont(ui_font(-1))
        hdr.addWidget(self._label_edit, stretch=1)

        del_btn = QPushButton("✕")
        del_btn.setFixedSize(18, 18)
        del_btn.setFont(ui_font(-2, bold=True))
        del_btn.setStyleSheet(
            "QPushButton { background: #3a2a2a; color: #f38ba8; "
            "border: 1px solid #8a4a4a; border-radius: 3px; padding: 0; }"
            "QPushButton:hover { background: #f38ba8; color: #1e1e2e; }"
        )
        del_btn.clicked.connect(lambda: self.remove_requested.emit(self))
        hdr.addWidget(del_btn)
        lay.addLayout(hdr)

        # タグ入力欄
        tag_lbl = QLabel(tr("model_browser.trigger_tag_heading"))
        tag_lbl.setFont(ui_font(-1))
        lay.addWidget(tag_lbl)
        self._words_edit = QTextEdit(trigger_words)
        self._words_edit.setFixedHeight(70)
        self._words_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._words_edit.setFont(ui_font(-1))
        self._words_edit.setPlaceholderText(tr("model_browser.trigger_tag_placeholder"))
        lay.addWidget(self._words_edit)

    @property
    def label(self) -> str:
        return self._label_edit.text().strip()

    @property
    def trigger_words(self) -> str:
        return self._words_edit.toPlainText().strip()


class _AutoLoRARow(QFrame):
    """自動読み込みLoRAの1行 (名前 + weight + 削除ボタン)"""

    remove_requested = Signal(object)  # self

    def __init__(self, lora_key: str, lora_name: str, weight: float, parent=None):
        super().__init__(parent)
        self._lora_key = lora_key
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            f"QFrame {{ background: {SURFACE1}; border: 1px solid {SURFACE2}; "
            f"border-radius: 4px; }}"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 3, 6, 3)
        lay.setSpacing(6)

        name_lbl = QLabel(lora_name or lora_key)
        name_lbl.setFont(ui_font(-1))
        lay.addWidget(name_lbl, stretch=1)

        from PySide6.QtWidgets import QDoubleSpinBox as _QDbl
        weight_lbl = QLabel("weight:")
        weight_lbl.setFont(ui_font(-1))
        lay.addWidget(weight_lbl)

        self._weight_spin = _QDbl()
        self._weight_spin.setRange(-1.0, 2.0)
        self._weight_spin.setSingleStep(0.05)
        self._weight_spin.setDecimals(2)
        self._weight_spin.setValue(weight)
        self._weight_spin.setFixedWidth(70)
        self._weight_spin.setFont(ui_font(-1))
        lay.addWidget(self._weight_spin)

        del_btn = QPushButton("✕")
        del_btn.setFixedSize(18, 18)
        del_btn.setFont(ui_font(-2, bold=True))
        del_btn.setStyleSheet(
            "QPushButton { background: #3a2a2a; color: #f38ba8; "
            "border: 1px solid #8a4a4a; border-radius: 3px; padding: 0; }"
            "QPushButton:hover { background: #f38ba8; color: #1e1e2e; }"
        )
        del_btn.clicked.connect(lambda: self.remove_requested.emit(self))
        lay.addWidget(del_btn)

    @property
    def lora_key(self) -> str:
        return self._lora_key

    @property
    def weight(self) -> float:
        return self._weight_spin.value()


class _LoRAEditDialog(QDialog):
    """
    LoRA の全情報（サムネイル・タイトル・コメント・トリガーワードセット群）を
    1つのダイアログで編集する。
    """

    def __init__(self, parent=None, *, key: str):
        super().__init__(parent)
        self.setWindowTitle(tr("model_browser.lora_edit_title"))
        self.setFixedSize(560, 760)
        self._key = key
        row = _env_db.fetchone(
            "SELECT name, title, comment, COALESCE(is_nsfw,0) AS is_nsfw "
            "FROM models WHERE invoke_key=?",
            (key,),
        )
        self._name = (row["name"] if row else key) or key

        # 既存のトリガーセットを読み込む
        existing_sets = _env_db.fetchall(
            "SELECT label, trigger_words FROM lora_trigger_sets "
            "WHERE invoke_key=? ORDER BY sort_order",
            (key,),
        )
        # 既存のネガティブプロンプトセットを読み込む
        existing_neg_sets = _env_db.fetchall(
            "SELECT label, neg_words FROM lora_neg_prompt_sets "
            "WHERE invoke_key=? ORDER BY sort_order",
            (key,),
        )
        self._build(
            title        = (row["title"]   or "") if row else "",
            comment      = (row["comment"] or "") if row else "",
            is_nsfw      = bool(row["is_nsfw"]) if row else False,
            trigger_sets = [(r["label"], r["trigger_words"]) for r in existing_sets],
            neg_sets     = [(r["label"], r["neg_words"]) for r in existing_neg_sets],
        )

    def _build(self, title: str, comment: str, is_nsfw: bool,
               trigger_sets: list[tuple[str, str]],
               neg_sets: list[tuple[str, str]] | None = None) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(10, 10, 10, 10)

        body_scroll = QScrollArea()
        body_scroll.setWidgetResizable(True)
        body_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        body = QWidget()
        body.setStyleSheet("background: transparent;")
        body_root = QVBoxLayout(body)
        body_root.setSpacing(8)
        body_root.setContentsMargins(0, 0, 0, 0)
        body_scroll.setWidget(body)
        root.addWidget(body_scroll, stretch=1)

        # LoRA名（読み取り専用）
        name_lbl = QLabel(f"LoRA:  {self._name}")
        name_lbl.setFont(ui_font(bold=True))
        name_lbl.setStyleSheet(f"color: {ACCENT};")
        body_root.addWidget(name_lbl)

        # ── サムネイル ───────────────────────────────────────────
        thumb_wrap = QWidget()
        thumb_wrap.setFixedHeight(104)
        thumb_row = QHBoxLayout()
        thumb_wrap.setLayout(thumb_row)
        thumb_row.setContentsMargins(0, 0, 0, 0)
        self._thumb_lbl = QLabel()
        self._thumb_lbl.setFixedSize(96, 96)
        self._thumb_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb_lbl.setStyleSheet(
            f"background: {SURFACE1}; color: {SUBTEXT}; border: 1px solid {SURFACE2}; border-radius: 4px;"
        )
        self._refresh_thumb_preview()
        thumb_row.addWidget(self._thumb_lbl)
        thumb_btns = QVBoxLayout()
        thumb_btns.setSpacing(4)
        change_btn = QPushButton(tr("model_browser.change_thumb_btn"))
        change_btn.clicked.connect(self._change_thumb)
        thumb_btns.addWidget(change_btn)
        self._paste_btn = QPushButton(tr("model_browser.paste_thumb_btn"))
        self._paste_btn.clicked.connect(self._paste_thumb)
        thumb_btns.addWidget(self._paste_btn)
        clear_btn = QPushButton(tr("model_browser.clear_thumb_btn"))
        clear_btn.clicked.connect(self._clear_thumb)
        thumb_btns.addWidget(clear_btn)
        thumb_btns.addStretch()
        thumb_row.addLayout(thumb_btns, stretch=1)
        body_root.addWidget(thumb_wrap)
        self._update_paste_btn()
        from PySide6.QtWidgets import QApplication as _App
        _App.clipboard().changed.connect(self._update_paste_btn)

        # ── タイトル ─────────────────────────────────────────────
        body_root.addWidget(QLabel(tr("model_browser.title_label")))
        self._title_edit = QLineEdit(title)
        self._title_edit.setPlaceholderText(tr("model_browser.lora_title_placeholder"))
        body_root.addWidget(self._title_edit)

        # ── コメント ─────────────────────────────────────────────
        body_root.addWidget(QLabel(tr("model_browser.comment_label")))
        self._comment_edit = QTextEdit(comment)
        self._comment_edit.setFixedHeight(60)
        self._comment_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._comment_edit.setPlaceholderText(tr("model_browser.comment_placeholder"))
        body_root.addWidget(self._comment_edit)

        # ── ジャンル ─────────────────────────────────────────────
        body_root.addWidget(QLabel(tr("model_browser.lora_genre_section_label")))
        self._genre_combo = QComboBox()
        self._genre_combo.addItem(tr("model_browser.lora_genre_unset"), None)
        for gkey, glabel in _lora_genres_from_db():
            self._genre_combo.addItem(glabel, gkey)
        cur_genre = _env_db.fetchone(
            "SELECT lora_genre FROM models WHERE invoke_key=?", (self._key,)
        )
        if cur_genre and cur_genre["lora_genre"]:
            idx = self._genre_combo.findData(cur_genre["lora_genre"])
            if idx >= 0:
                self._genre_combo.setCurrentIndex(idx)
        body_root.addWidget(self._genre_combo)

        # ── トリガーワードセット（スクロール可能）────────────────
        body_root.addWidget(QLabel(tr("model_browser.trigger_sets_label")))

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFixedHeight(240)
        self._scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._scroll.setStyleSheet(
            f"QScrollArea {{ background: {SURFACE0}; border: 1px solid {SURFACE2}; border-radius: 4px; }}"
        )

        self._rows_container = QWidget()
        self._rows_container.setStyleSheet(f"background: {SURFACE0};")
        self._rows_layout = QVBoxLayout(self._rows_container)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(4)
        self._scroll.setWidget(self._rows_container)
        body_root.addWidget(self._scroll)

        self._rows: list[_TriggerSetRow] = []

        # 既存データを行として追加
        if trigger_sets:
            for i, (lbl, words) in enumerate(trigger_sets):
                self._add_row(lbl, words, is_first=(i == 0))
        else:
            # 新規: デフォルト1行
            self._add_row(tr("model_browser.trigger_default_placeholder"), "", is_first=True)

        # 追加ボタン（ポジティブ）: セット一覧の中に置く
        self._add_trigger_btn = QPushButton(tr("model_browser.add_trigger_set_btn"))
        self._add_trigger_btn.setFont(ui_font(-1))
        self._add_trigger_btn.setStyleSheet(
            f"QPushButton {{ background: {SURFACE1}; color: {ACCENT}; "
            f"border: 1px solid {ACCENT}; border-radius: 4px; padding: 4px 8px; }}"
            f"QPushButton:hover {{ background: {SURFACE2}; }}"
        )
        self._add_trigger_btn.clicked.connect(self._on_add_row)
        self._rows_layout.addWidget(self._add_trigger_btn)
        self._rows_layout.addStretch()

        # ── ネガティブプロンプトセット ───────────────────────────
        from ui.styles import RED
        neg_sec_lbl = QLabel(tr("model_browser.neg_prompt_section"))
        neg_sec_lbl.setFont(ui_font(bold=True))
        neg_sec_lbl.setStyleSheet(f"color: {RED};")
        body_root.addWidget(neg_sec_lbl)

        self._neg_scroll = QScrollArea()
        self._neg_scroll.setWidgetResizable(True)
        self._neg_scroll.setFixedHeight(180)
        self._neg_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._neg_scroll.setStyleSheet(
            f"QScrollArea {{ background: {SURFACE0}; border: 1px solid {SURFACE2}; border-radius: 4px; }}"
        )

        self._neg_rows_container = QWidget()
        self._neg_rows_container.setStyleSheet(f"background: {SURFACE0};")
        self._neg_rows_layout = QVBoxLayout(self._neg_rows_container)
        self._neg_rows_layout.setContentsMargins(0, 0, 0, 0)
        self._neg_rows_layout.setSpacing(4)
        self._neg_scroll.setWidget(self._neg_rows_container)
        body_root.addWidget(self._neg_scroll)

        self._neg_rows: list[_TriggerSetRow] = []

        if neg_sets:
            for i, (lbl, words) in enumerate(neg_sets):
                self._add_neg_row(lbl, words, is_first=(i == 0))
        else:
            self._add_neg_row(tr("model_browser.neg_default_label", n=1), "", is_first=True)

        self._add_neg_btn = QPushButton(tr("model_browser.add_neg_set_btn"))
        self._add_neg_btn.setFont(ui_font(-1))
        self._add_neg_btn.setStyleSheet(
            f"QPushButton {{ background: {SURFACE1}; color: {RED}; "
            f"border: 1px solid {RED}; border-radius: 4px; padding: 4px 8px; }}"
            f"QPushButton:hover {{ background: {SURFACE2}; }}"
        )
        self._add_neg_btn.clicked.connect(self._on_add_neg_row)
        self._neg_rows_layout.addWidget(self._add_neg_btn)
        self._neg_rows_layout.addStretch()

        # ── NSFWフラグ ───────────────────────────────────────────
        self._nsfw_cb = QCheckBox(tr("model_browser.lora_nsfw_cb"))
        self._nsfw_cb.setChecked(is_nsfw)
        body_root.addWidget(self._nsfw_cb)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    def _add_row(self, label: str = "", words: str = "", is_first: bool = False) -> None:
        row = _TriggerSetRow(label, words, is_first=is_first, parent=self._rows_container)
        row.remove_requested.connect(self._on_remove_row)
        insert_at = self._rows_layout.indexOf(getattr(self, "_add_trigger_btn", None))
        if insert_at < 0:
            insert_at = self._rows_layout.count()
        self._rows_layout.insertWidget(insert_at, row)
        self._rows.append(row)

    def _on_add_row(self) -> None:
        self._add_row("", "", is_first=False)
        # スクロールを末尾へ
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, lambda: self._scroll.verticalScrollBar().setValue(
            self._scroll.verticalScrollBar().maximum()
        ))

    def _on_remove_row(self, row: "_TriggerSetRow") -> None:
        if len(self._rows) <= 1:
            return   # 最低1行は残す
        self._rows.remove(row)
        self._rows_layout.removeWidget(row)
        row.deleteLater()

    def _add_neg_row(self, label: str = "", words: str = "", is_first: bool = False) -> None:
        row = _TriggerSetRow(label, words, is_first=is_first, parent=self._neg_rows_container)
        row.remove_requested.connect(self._on_remove_neg_row)
        insert_at = self._neg_rows_layout.indexOf(getattr(self, "_add_neg_btn", None))
        if insert_at < 0:
            insert_at = self._neg_rows_layout.count()
        self._neg_rows_layout.insertWidget(insert_at, row)
        self._neg_rows.append(row)

    def _on_add_neg_row(self) -> None:
        self._add_neg_row("", "", is_first=False)
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, lambda: self._neg_scroll.verticalScrollBar().setValue(
            self._neg_scroll.verticalScrollBar().maximum()
        ))

    def _on_remove_neg_row(self, row: "_TriggerSetRow") -> None:
        if len(self._neg_rows) <= 1:
            return   # 最低1行は残す
        self._neg_rows.remove(row)
        self._neg_rows_layout.removeWidget(row)
        row.deleteLater()

    def _refresh_thumb_preview(self) -> None:
        pix = _load_thumb(self._key, size=96)
        if pix:
            self._thumb_lbl.setPixmap(pix)
            self._thumb_lbl.setText("")
        else:
            from PySide6.QtGui import QPixmap as _QPixmap
            self._thumb_lbl.setPixmap(_QPixmap())
            self._thumb_lbl.setText(tr("model_browser.no_thumb"))

    def _change_thumb(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr("model_browser.select_thumb_title"), "",
            tr("model_browser.image_filter"),
        )
        if path:
            _set_thumb(self._key, path)
            self._refresh_thumb_preview()

    def _paste_thumb(self) -> None:
        from PySide6.QtWidgets import QApplication as _App
        img = _App.clipboard().image()
        if img.isNull():
            return
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QBuffer.OpenModeFlag.WriteOnly)
        img.save(buf, "PNG")
        _save_thumb(self._key, bytes(ba))
        self._refresh_thumb_preview()

    def _update_paste_btn(self) -> None:
        from PySide6.QtWidgets import QApplication as _App
        has_image = not _App.clipboard().image().isNull()
        self._paste_btn.setEnabled(has_image)

    def _clear_thumb(self) -> None:
        _env_db.execute(
            "UPDATE models SET thumbnail_data=NULL, updated_at=CURRENT_TIMESTAMP WHERE invoke_key=?",
            (self._key,),
        )
        self._refresh_thumb_preview()

    @property
    def result_values(self) -> dict:
        sets = []
        for i, row in enumerate(self._rows):
            words = row.trigger_words
            if not words:
                continue
            label = row.label or (tr("model_browser.trigger_default_placeholder") if i == 0 else "Custom")
            sets.append({"label": label, "trigger_words": words})

        neg_sets = []
        for i, row in enumerate(self._neg_rows):
            words = row.trigger_words  # _TriggerSetRow の trigger_words プロパティを流用
            if not words:
                continue
            label = row.label or tr("model_browser.neg_default_label", n=i + 1)
            neg_sets.append({"label": label, "neg_words": words})

        return {
            "title":         self._title_edit.text().strip(),
            "comment":       self._comment_edit.toPlainText().strip(),
            "trigger_sets":  sets,
            "neg_prompt_sets": neg_sets,
            "is_nsfw":       1 if self._nsfw_cb.isChecked() else 0,
            "lora_genre":    self._genre_combo.currentData(),  # None = 未設定
        }


# ── モデル注釈編集ダイアログ ──────────────────────────────────────────────────

class _ModelAnnotateDialog(QDialog):
    """
    モデルの注釈（サムネイル・タイトル・コメント・NSFW）を編集するダイアログ。
    """

    def __init__(self, parent=None, *, key: str):
        super().__init__(parent)
        self.setWindowTitle(tr("model_browser.model_edit_title"))
        self.setMinimumWidth(500)
        self._key = key
        row = _env_db.fetchone(
            "SELECT name, base, title, comment, COALESCE(is_nsfw,0) AS is_nsfw, "
            "default_steps, default_cfg, default_scheduler "
            "FROM models WHERE invoke_key=?",
            (key,),
        )
        self._name = (row["name"] if row else key) or key
        self._base = (row["base"] or "") if row else ""
        # 既存の自動読み込みLoRAを取得
        auto_rows = _env_db.fetchall(
            "SELECT lora_key, weight FROM model_auto_loras "
            "WHERE model_key=? ORDER BY sort_order, id",
            (key,),
        )
        self._initial_auto_loras = [
            {"lora_key": r["lora_key"], "weight": r["weight"]} for r in auto_rows
        ]
        self._build(
            title             = (row["title"]   or "") if row else "",
            comment           = (row["comment"] or "") if row else "",
            is_nsfw           = bool(row["is_nsfw"]) if row else False,
            default_steps     = row["default_steps"] if row and row["default_steps"] is not None else None,
            default_cfg       = row["default_cfg"] if row and row["default_cfg"] is not None else None,
            default_scheduler = (row["default_scheduler"] or "") if row else "",
        )

    def _build(self, title: str, comment: str, is_nsfw: bool,
               default_steps: int | None = None,
               default_cfg: float | None = None,
               default_scheduler: str = "") -> None:
        lay = QVBoxLayout(self)
        lay.setSpacing(8)

        name_lbl = QLabel(tr("model_browser.model_name_label", name=self._name))
        name_lbl.setFont(ui_font(bold=True))
        name_lbl.setStyleSheet(f"color: {ACCENT};")
        lay.addWidget(name_lbl)

        # ── サムネイル ───────────────────────────────────────────
        thumb_row = QHBoxLayout()
        self._thumb_lbl = QLabel()
        self._thumb_lbl.setFixedSize(96, 96)
        self._thumb_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb_lbl.setStyleSheet(
            f"background: {SURFACE1}; color: {SUBTEXT}; border: 1px solid {SURFACE2}; border-radius: 4px;"
        )
        self._refresh_thumb_preview()
        thumb_row.addWidget(self._thumb_lbl)

        thumb_btns = QVBoxLayout()
        thumb_btns.setSpacing(4)
        change_btn = QPushButton(tr("model_browser.change_thumb_btn"))
        change_btn.clicked.connect(self._change_thumb)
        thumb_btns.addWidget(change_btn)
        self._paste_btn = QPushButton(tr("model_browser.paste_thumb_btn"))
        self._paste_btn.clicked.connect(self._paste_thumb)
        thumb_btns.addWidget(self._paste_btn)
        clear_btn = QPushButton(tr("model_browser.clear_thumb_btn"))
        clear_btn.clicked.connect(self._clear_thumb)
        thumb_btns.addWidget(clear_btn)
        thumb_btns.addStretch()
        thumb_row.addLayout(thumb_btns, stretch=1)
        lay.addLayout(thumb_row)
        self._update_paste_btn()
        from PySide6.QtWidgets import QApplication as _App
        _App.clipboard().changed.connect(self._update_paste_btn)

        # ── タイトル ─────────────────────────────────────────────
        lay.addWidget(QLabel(tr("model_browser.title_label")))
        self._title_edit = QLineEdit(title)
        self._title_edit.setPlaceholderText(tr("model_browser.model_title_placeholder"))
        lay.addWidget(self._title_edit)

        # ── コメント ─────────────────────────────────────────────
        lay.addWidget(QLabel(tr("model_browser.comment_label")))
        self._comment_edit = QTextEdit(comment)
        self._comment_edit.setFixedHeight(72)
        self._comment_edit.setPlaceholderText(tr("model_browser.comment_placeholder"))
        lay.addWidget(self._comment_edit)

        # ── デフォルト生成パラメータ ─────────────────────────────
        from PySide6.QtWidgets import QSpinBox as _QSpinBox, QDoubleSpinBox as _QDblSpin
        params_frame = QFrame()
        params_frame.setFrameShape(QFrame.Shape.StyledPanel)
        params_lay = QVBoxLayout(params_frame)
        params_lay.setContentsMargins(6, 4, 6, 4)
        params_lay.setSpacing(4)

        params_lbl = QLabel(tr("model_browser.default_params_label"))
        params_lbl.setFont(ui_font(bold=True))
        params_lay.addWidget(params_lbl)

        row_steps = QHBoxLayout()
        row_steps.addWidget(QLabel(tr("model_browser.default_steps_label")))
        self._def_steps_spin = _QSpinBox()
        self._def_steps_spin.setRange(0, 300)
        self._def_steps_spin.setSpecialValueText(tr("model_browser.default_param_unset"))
        self._def_steps_spin.setValue(default_steps if default_steps is not None else 0)
        row_steps.addWidget(self._def_steps_spin)
        row_steps.addStretch()
        params_lay.addLayout(row_steps)

        row_cfg = QHBoxLayout()
        row_cfg.addWidget(QLabel(tr("model_browser.default_cfg_label")))
        self._def_cfg_spin = _QDblSpin()
        # 有効な CFG は「未設定(0.0=特殊値)」または 1.0 以上のみ。
        # 1.0 未満は全モデルで不正（1.0=ガイダンスなし）なので、(0,1) のギャップは
        # 直前値からの増減方向で 0.0（未設定）か 1.0 に寄せて選べないようにする。
        self._def_cfg_spin.setRange(0.0, 30.0)
        self._def_cfg_spin.setSingleStep(0.5)
        self._def_cfg_spin.setDecimals(1)
        self._def_cfg_spin.setSpecialValueText(tr("model_browser.default_param_unset"))
        self._def_cfg_spin.setValue(default_cfg if default_cfg is not None else 0.0)
        self._def_cfg_prev = self._def_cfg_spin.value()

        def _snap_cfg(v: float) -> None:
            if 0.0 < v < 1.0:
                target = 1.0 if v >= self._def_cfg_prev else 0.0
                self._def_cfg_spin.blockSignals(True)
                self._def_cfg_spin.setValue(target)
                self._def_cfg_spin.blockSignals(False)
                v = target
            self._def_cfg_prev = v

        self._def_cfg_spin.valueChanged.connect(_snap_cfg)
        row_cfg.addWidget(self._def_cfg_spin)
        row_cfg.addStretch()
        params_lay.addLayout(row_cfg)

        row_sched = QHBoxLayout()
        row_sched.addWidget(QLabel(tr("model_browser.default_scheduler_label")))
        self._def_sched_combo = QComboBox()
        self._def_sched_combo.addItem(tr("model_browser.default_param_unset"), "")
        schedulers = {
            "sdxl": ["euler", "euler_a", "dpmpp_2m", "dpmpp_2m_sde", "ddim", "lms", "heun", "unipc"],
            "sd-1": ["euler", "euler_a", "dpmpp_2m", "dpmpp_2m_sde", "ddim", "lms", "heun", "unipc"],
            "flux": ["euler", "heun", "lcm"],
            "flux2": ["euler", "heun", "lcm"],
            "z-image": ["euler", "heun", "lcm"],
            "anima": ["euler", "heun", "dpmpp_2m", "dpmpp_2m_sde", "er_sde", "lcm"],
        }.get(self._base or "sdxl", ["euler", "heun", "lcm"])
        if default_scheduler and default_scheduler not in schedulers:
            schedulers = [default_scheduler] + schedulers
        for sched in schedulers:
            self._def_sched_combo.addItem(sched, sched)
        if default_scheduler:
            idx = self._def_sched_combo.findData(default_scheduler)
            if idx >= 0:
                self._def_sched_combo.setCurrentIndex(idx)
        row_sched.addWidget(self._def_sched_combo, stretch=1)
        params_lay.addLayout(row_sched)

        lay.addWidget(params_frame)

        # ── 自動読み込みLoRA ─────────────────────────────────────
        auto_frame = QFrame()
        auto_frame.setFrameShape(QFrame.Shape.StyledPanel)
        auto_lay = QVBoxLayout(auto_frame)
        auto_lay.setContentsMargins(6, 4, 6, 4)
        auto_lay.setSpacing(4)

        auto_hdr = QLabel(tr("model_browser.auto_lora_label"))
        auto_hdr.setFont(ui_font(bold=True))
        auto_lay.addWidget(auto_hdr)

        # 追加行: コンボボックス + 追加ボタン
        add_row = QHBoxLayout()
        add_row.setSpacing(4)
        self._lora_combo = QComboBox()
        self._lora_combo.setEditable(True)
        self._lora_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._lora_combo.lineEdit().setPlaceholderText(tr("model_browser.auto_lora_search_placeholder"))
        self._lora_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._lora_combo.setFont(ui_font(-1))
        self._populate_lora_combo()
        add_row.addWidget(self._lora_combo, stretch=1)
        add_btn = QPushButton(tr("model_browser.auto_lora_add_btn"))
        add_btn.setFont(ui_font(-1))
        add_btn.clicked.connect(self._add_auto_lora)
        add_row.addWidget(add_btn)
        auto_lay.addLayout(add_row)

        # 選択済みLoRAリスト
        self._auto_lora_list_lay = QVBoxLayout()
        self._auto_lora_list_lay.setSpacing(2)
        self._auto_lora_rows: list[_AutoLoRARow] = []
        for entry in self._initial_auto_loras:
            self._add_auto_lora_row(entry["lora_key"], entry["weight"])
        auto_lay.addLayout(self._auto_lora_list_lay)

        lay.addWidget(auto_frame)

        # ── NSFWフラグ ───────────────────────────────────────────
        self._nsfw_cb = QCheckBox(tr("model_browser.model_nsfw_cb"))
        self._nsfw_cb.setChecked(is_nsfw)
        lay.addWidget(self._nsfw_cb)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _populate_lora_combo(self) -> None:
        """LoRAコンボボックスにモデルと同ベースのLoRAを一覧表示する。"""
        self._lora_combo.clear()
        if self._base:
            rows = _env_db.fetchall(
                "SELECT invoke_key, COALESCE(NULLIF(title,''), name) AS disp_name "
                "FROM models WHERE type='lora' AND available=1 AND base=? "
                "ORDER BY disp_name",
                (self._base,),
            )
        else:
            rows = []
        if not rows:
            rows = _env_db.fetchall(
                "SELECT invoke_key, COALESCE(NULLIF(title,''), name) AS disp_name "
                "FROM models WHERE type='lora' AND available=1 "
                "ORDER BY COALESCE(base,'zzz'), disp_name",
            )
        self._combo_key_map: dict[str, str] = {}
        for r in rows:
            self._combo_key_map[r["disp_name"]] = r["invoke_key"]
            self._lora_combo.addItem(r["disp_name"])
        self._lora_combo.setCurrentIndex(-1)
        self._lora_combo.lineEdit().clear()

    def _add_auto_lora(self) -> None:
        """コンボで選択中のLoRAを自動読み込みリストに追加する。"""
        text = self._lora_combo.currentText().strip()
        if not text:
            return
        lora_key = self._combo_key_map.get(text)
        if lora_key is None:
            low = text.lower()
            for name, key in self._combo_key_map.items():
                if low in name.lower():
                    lora_key = key
                    break
        if lora_key is None:
            return
        if any(r.lora_key == lora_key for r in self._auto_lora_rows):
            return
        self._add_auto_lora_row(lora_key, 0.75)
        self._lora_combo.setCurrentIndex(-1)
        self._lora_combo.lineEdit().clear()

    def _add_auto_lora_row(self, lora_key: str, weight: float) -> None:
        row_data = _env_db.fetchone(
            "SELECT COALESCE(NULLIF(title,''), name) AS disp_name FROM models WHERE invoke_key=?",
            (lora_key,),
        )
        name = row_data["disp_name"] if row_data else lora_key
        row = _AutoLoRARow(lora_key, name, weight)
        row.remove_requested.connect(self._remove_auto_lora_row)
        self._auto_lora_rows.append(row)
        self._auto_lora_list_lay.addWidget(row)

    def _remove_auto_lora_row(self, row: "_AutoLoRARow") -> None:
        self._auto_lora_rows.remove(row)
        self._auto_lora_list_lay.removeWidget(row)
        row.deleteLater()

    def _refresh_thumb_preview(self) -> None:
        pix = _load_thumb(self._key, size=96)
        if pix:
            self._thumb_lbl.setPixmap(pix)
            self._thumb_lbl.setText("")
        else:
            from PySide6.QtGui import QPixmap as _QPixmap
            self._thumb_lbl.setPixmap(_QPixmap())
            self._thumb_lbl.setText(tr("model_browser.no_thumb"))

    def _change_thumb(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr("model_browser.select_thumb_title"), "",
            tr("model_browser.image_filter"),
        )
        if path:
            _set_thumb(self._key, path)
            self._refresh_thumb_preview()

    def _paste_thumb(self) -> None:
        from PySide6.QtWidgets import QApplication as _App
        img = _App.clipboard().image()
        if img.isNull():
            return
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QBuffer.OpenModeFlag.WriteOnly)
        img.save(buf, "PNG")
        _save_thumb(self._key, bytes(ba))
        self._refresh_thumb_preview()

    def _update_paste_btn(self) -> None:
        from PySide6.QtWidgets import QApplication as _App
        has_image = not _App.clipboard().image().isNull()
        self._paste_btn.setEnabled(has_image)

    def _clear_thumb(self) -> None:
        _env_db.execute(
            "UPDATE models SET thumbnail_data=NULL, updated_at=CURRENT_TIMESTAMP WHERE invoke_key=?",
            (self._key,),
        )
        self._refresh_thumb_preview()

    @property
    def result_values(self) -> dict:
        steps_val = self._def_steps_spin.value()
        cfg_val   = self._def_cfg_spin.value()
        sched_val = str(self._def_sched_combo.currentData() or "").strip()
        auto_loras = [
            {"lora_key": r.lora_key, "weight": r.weight}
            for r in self._auto_lora_rows
        ]
        return {
            "title":             self._title_edit.text().strip(),
            "comment":           self._comment_edit.toPlainText().strip(),
            "is_nsfw":           1 if self._nsfw_cb.isChecked() else 0,
            "default_steps":     steps_val if steps_val > 0 else None,
            "default_cfg":       cfg_val if cfg_val > 0.0 else None,
            "default_scheduler": sched_val or None,
            "auto_loras":        auto_loras,
        }


# ── LoRABrowser ───────────────────────────────────────────────────────────────

_LORA_KEY  = Qt.ItemDataRole.UserRole
_LORA_NAME = Qt.ItemDataRole.UserRole + 1
_LORA_BASE = Qt.ItemDataRole.UserRole + 2
_LORA_DISP = Qt.ItemDataRole.UserRole + 3   # 元の表示テキスト（選択マーク除去用）
_LORA_RESERVED_THUMB = Qt.ItemDataRole.UserRole + 4
_LORA_HAS_THUMB = Qt.ItemDataRole.UserRole + 5
_LORA_SELECTED = Qt.ItemDataRole.UserRole + 6
_LORA_AVAILABLE = Qt.ItemDataRole.UserRole + 7
_LORA_SEARCH_TEXT = Qt.ItemDataRole.UserRole + 8

# グループ折りたたみ用ロール（ModelBrowser / LoRABrowser 共通）
_HDR_ROLE  = Qt.ItemDataRole.UserRole + 10  # True → ヘッダー行
_GRP_KEY   = Qt.ItemDataRole.UserRole + 11  # グループキー（折りたたみ状態管理用）
_GRP_LABEL = Qt.ItemDataRole.UserRole + 12  # グループラベル（矢印プレフィックスなし）


class _LoRAListDelegate(QStyledItemDelegate):
    """LoRA rows with fixed thumbnail and text geometry."""

    def paint(self, painter, option, index) -> None:
        if index.data(_HDR_ROLE):
            super().paint(painter, option, index)
            return

        painter.save()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        painter.fillRect(option.rect, QColor(SURFACE2 if selected else SURFACE0))

        reserve_thumb = bool(index.data(_LORA_RESERVED_THUMB))
        text_left = option.rect.left() + 4
        if reserve_thumb:
            thumb = option.rect.adjusted(4, 4, 0, 0)
            thumb.setSize(QSize(_THUMB_SIZE, _THUMB_SIZE))
            icon = index.data(Qt.ItemDataRole.DecorationRole)
            if isinstance(icon, QIcon) and not icon.isNull():
                pix = icon.pixmap(_THUMB_SIZE, _THUMB_SIZE)
                px = thumb.left() + (_THUMB_SIZE - pix.width()) // 2
                py = thumb.top() + (_THUMB_SIZE - pix.height()) // 2
                painter.drawPixmap(px, py, pix)
            else:
                painter.fillRect(thumb, QColor(SURFACE1))
            text_left = option.rect.left() + _THUMB_SIZE + 12

        text_w = max(20, option.rect.right() - text_left - 10)
        line_h = option.fontMetrics.lineSpacing()
        line_count = 3 if reserve_thumb else 1
        y = option.rect.top() + max(4, (option.rect.height() - line_h * line_count) // 2)

        name = str(index.data(_LORA_NAME) or "")
        if index.data(_LORA_SELECTED):
            name = "✓  " + name
        title = str(index.data(_MODEL_TITLE_ROLE) or "")
        comment = str(index.data(_MODEL_COMMENT_ROLE) or "")

        painter.setPen(QColor(TEXT))
        painter.setFont(ui_font(-1))
        painter.drawText(
            text_left, y, text_w, line_h,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            option.fontMetrics.elidedText(name, Qt.TextElideMode.ElideRight, text_w),
        )
        if reserve_thumb:
            painter.drawText(
                text_left, y + line_h, text_w, line_h,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                option.fontMetrics.elidedText(title, Qt.TextElideMode.ElideRight, text_w),
            )
            painter.setPen(QColor(SUBTEXT))
            painter.drawText(
                text_left, y + line_h * 2, text_w, line_h,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                option.fontMetrics.elidedText(comment, Qt.TextElideMode.ElideRight, text_w),
            )

        painter.restore()

    def sizeHint(self, option, index) -> QSize:
        if index.data(_HDR_ROLE):
            return super().sizeHint(option, index)
        if index.data(_LORA_RESERVED_THUMB):
            return QSize(0, max(_THUMB_SIZE + 8, option.fontMetrics.lineSpacing() * 3 + 12))
        return QSize(0, option.fontMetrics.lineSpacing() + 12)


class LoRABrowser(QWidget):
    """
    LoRA選択ブラウザ。ダブルクリックで追加/削除を切り替える。

    Signals:
        lora_toggled(dict): {"action":"add"|"remove", "invoke_key", "name",
                              "base", "weight"}
    """
    lora_toggled = Signal(dict)

    def __init__(self, client: "InvokeClient | None" = None, parent=None):
        super().__init__(parent)
        self._client          = client
        self._selected_keys: set[str] = set()
        self._sync_worker: _SyncWorker | None = None
        self._expanded_groups: set[str] = set()
        self._search_text     = ""
        self._genre_filter    = "all"
        self._base_filter     = ""
        self._build_ui()
        self.refresh()

    def set_client(self, client: "InvokeClient") -> None:
        self._client = client

    def set_selected_keys(self, keys: set[str]) -> None:
        """チップバーの現在選択を反映して表示を更新する。"""
        self._selected_keys = set(keys)
        self._update_visual()

    def set_base_filter(self, base: str) -> None:
        """選択中の画像生成モデルと同じベースのLoRAだけを表示する。"""
        base = base or ""
        if base == self._base_filter:
            return
        self._base_filter = base
        self._expanded_groups = {f"base:{base}"} if base else set()
        self.refresh()

    def focus_base_group(self, base: str) -> None:
        """現在モデルと同じベースのLoRAグループを展開して表示する。"""
        base = base or ""
        self._base_filter = base
        self._genre_filter = "all"
        idx = self._genre_view_combo.findData("all")
        if idx >= 0 and self._genre_view_combo.currentIndex() != idx:
            self._genre_view_combo.blockSignals(True)
            self._genre_view_combo.setCurrentIndex(idx)
            self._genre_view_combo.blockSignals(False)
        self._search_text = ""
        self._search_edit.clear()
        grp_key = f"base:{base}"
        self._expanded_groups.add(grp_key)
        self.refresh()
        self._scroll_to_group(grp_key)

    # ── UI構築 ─────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        self._hdr = _BrowserHeader("LoRA", lambda: self._sync(notify=True))
        lay.addLayout(self._hdr)

        lora_search_row = QHBoxLayout()
        lora_search_row.setSpacing(2)
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText(tr("model_browser.lora_search_placeholder"))
        self._search_edit.setFixedHeight(24)
        self._search_edit.setFont(ui_font(-1))
        self._search_edit.setStyleSheet(
            f"QLineEdit {{ background: {SURFACE0}; color: {TEXT}; "
            f"border: 1px solid {SURFACE2}; border-radius: 3px; padding: 0 4px; }}"
        )
        self._search_edit.textChanged.connect(self._on_search)
        lora_search_row.addWidget(self._search_edit)

        self._lora_clear_btn = QToolButton()
        self._lora_clear_btn.setText("×")
        self._lora_clear_btn.setFixedSize(24, 24)
        self._lora_clear_btn.setToolTip(tr("lora_browser.search_clear_tooltip"))
        self._lora_clear_btn.setVisible(False)
        self._lora_clear_btn.clicked.connect(self._search_edit.clear)
        self._search_edit.textChanged.connect(
            lambda t: self._lora_clear_btn.setVisible(bool(t))
        )
        lora_search_row.addWidget(self._lora_clear_btn)
        lay.addLayout(lora_search_row)

        self._hint_lbl = QLabel(tr("model_browser.double_click_hint"))
        self._hint_lbl.setFont(ui_font(-2))
        self._hint_lbl.setStyleSheet(f"color: {SUBTEXT};")
        lay.addWidget(self._hint_lbl)

        # ジャンル表示
        genre_row = QHBoxLayout()
        self._genre_view_combo = QComboBox()
        self._genre_view_combo.setFont(ui_font(-1))
        self._genre_view_combo.addItem(tr("model_browser.lora_view_all"), "all")
        self._genre_view_combo.addItem(tr("model_browser.lora_view_categorized"), "categorized")
        self._genre_view_combo.addItem(tr("model_browser.lora_view_uncategorized"), "__unset__")
        self._genre_view_combo.currentIndexChanged.connect(self._on_genre_changed)
        genre_row.addWidget(self._genre_view_combo, stretch=1)

        self._add_genre_btn = QToolButton()
        self._add_genre_btn.setText("+")
        self._add_genre_btn.setToolTip(tr("model_browser.lora_genre_add"))
        self._add_genre_btn.setFixedSize(24, 24)
        self._add_genre_btn.clicked.connect(self._add_lora_genre)
        genre_row.addWidget(self._add_genre_btn)
        lay.addLayout(genre_row)

        self._list = QListWidget()
        self._list.setItemDelegate(_LoRAListDelegate(self._list))
        self._list.setIconSize(QSize(_THUMB_SIZE, _THUMB_SIZE))
        self._list.setSpacing(2)
        self._list.setFont(ui_font())
        self._list.setStyleSheet(
            f"QListWidget {{ background: {SURFACE0}; border: 1px solid {SURFACE2}; "
            f"border-radius: 4px; }}"
            f"QListWidget::item {{ padding: 2px; }}"
            f"QListWidget::item:selected {{ background: {SURFACE2}; }}"
        )
        self._list.itemDoubleClicked.connect(self._on_double_click)
        self._list.itemClicked.connect(self._on_item_clicked)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_context_menu)
        self._list.setMouseTracking(True)
        self._list.viewport().setMouseTracking(True)
        self._list.viewport().installEventFilter(self)
        lay.addWidget(self._list, stretch=1)

        self._inline_title_edit = QLineEdit(self._list.viewport())
        self._inline_title_edit.hide()
        self._inline_title_edit.editingFinished.connect(self._finish_inline_title_edit)
        self._inline_title_item: QListWidgetItem | None = None
        self._memo_timer = QTimer(self)
        self._memo_timer.setSingleShot(True)
        self._memo_timer.setInterval(500)
        self._memo_timer.timeout.connect(self._show_memo_tip)
        self._memo_item: QListWidgetItem | None = None
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
        return super().eventFilter(obj, event)

    def _thumb_rect(self, item: QListWidgetItem):
        rect = self._list.visualItemRect(item)
        from PySide6.QtCore import QRect
        return QRect(rect.left() + 4, rect.top() + max(0, (rect.height() - _THUMB_SIZE) // 2), _THUMB_SIZE, _THUMB_SIZE)

    def _title_rect(self, item: QListWidgetItem):
        rect = self._list.visualItemRect(item)
        fm = self._list.fontMetrics()
        left = _THUMB_SIZE + 12 if item.data(_LORA_RESERVED_THUMB) else 4
        top = rect.top() + max(4, (rect.height() - fm.lineSpacing() * 3) // 2) + fm.lineSpacing()
        from PySide6.QtCore import QRect
        return QRect(
            rect.left() + left,
            top,
            max(24, rect.width() - left - 12),
            fm.lineSpacing() + 4,
        )

    def _schedule_memo_tip(self, pos: QPoint) -> None:
        item = self._list.itemAt(pos)
        if not item or item.data(_HDR_ROLE):
            self._hide_memo_tip()
            return
        memo = str(item.data(_MODEL_COMMENT_ROLE) or "").strip()
        if not memo or not self._thumb_rect(item).contains(pos):
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
        memo = str(self._memo_item.data(_MODEL_COMMENT_ROLE) or "").strip()
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

    def _start_inline_title_edit(self, item: QListWidgetItem) -> None:
        key = item.data(_LORA_KEY)
        if not key:
            return
        self._inline_title_item = item
        self._inline_title_edit.setGeometry(self._title_rect(item))
        self._inline_title_edit.setText(item.data(_MODEL_TITLE_ROLE) or "")
        self._inline_title_edit.show()
        self._inline_title_edit.setFocus()
        self._inline_title_edit.selectAll()

    def _finish_inline_title_edit(self) -> None:
        item = self._inline_title_item
        if item is None:
            return
        self._inline_title_item = None
        self._inline_title_edit.hide()
        key = item.data(_LORA_KEY)
        if not key:
            return
        title = self._inline_title_edit.text().strip()
        _env_db.execute(
            "UPDATE models SET title=?, updated_at=CURRENT_TIMESTAMP WHERE invoke_key=?",
            (title or None, key),
        )
        item.setData(_MODEL_TITLE_ROLE, title)
        self._refresh_lora_item_text(item)

    def _refresh_lora_item_text(self, item: QListWidgetItem) -> None:
        name = item.data(_LORA_NAME) or ""
        title = item.data(_MODEL_TITLE_ROLE) or ""
        comment = item.data(_MODEL_COMMENT_ROLE) or ""
        has_thumb = bool(item.data(_LORA_HAS_THUMB))
        reserve_thumb = bool(has_thumb or title or comment)
        item.setData(_LORA_RESERVED_THUMB, reserve_thumb)
        if reserve_thumb and not has_thumb and item.icon().isNull():
            item.setIcon(QIcon(_default_lora_thumb()))
        elif not reserve_thumb:
            item.setIcon(QIcon())
            item.setSizeHint(QSize())
        if reserve_thumb:
            item.setSizeHint(QSize(0, max(_THUMB_SIZE + 8, self._list.fontMetrics().lineSpacing() * 3 + 12)))
        if reserve_thumb:
            lines = [name, title, comment]
        else:
            lines = [name]
        disp = "\n".join(lines)
        item.setData(_LORA_DISP, disp)
        item.setData(_LORA_SEARCH_TEXT, " ".join([name, title, comment]).lower())
        item.setText(disp)

    def _on_genre_changed(self) -> None:
        self._genre_filter = self._genre_view_combo.currentData() or "all"
        self._expand_all_groups_after_refresh = True
        self.refresh()

    def _on_search(self, text: str) -> None:
        self._search_text = text
        self._apply_filter()

    def _apply_filter(self) -> None:
        q = self._search_text.strip().lower()
        n = self._list.count()

        if q:
            # 検索モード: マッチしたアイテムを表示、所属グループを自動展開
            for i in range(n):
                item = self._list.item(i)
                if item.data(_HDR_ROLE):
                    continue
                key = item.data(_LORA_KEY)
                if not key:
                    continue
                haystack = item.data(_LORA_SEARCH_TEXT) or item.data(_LORA_DISP) or item.text()
                item.setHidden(q not in str(haystack).lower())

            # マッチアイテムが属するグループを収集
            matched_groups: set[str] = set()
            current_grp: str | None = None
            for i in range(n):
                item = self._list.item(i)
                if item.data(_HDR_ROLE):
                    current_grp = item.data(_GRP_KEY)
                    continue
                if current_grp and not item.isHidden():
                    matched_groups.add(current_grp)

            # ヘッダー: マッチグループは展開表示、それ以外は非表示
            for i in range(n):
                item = self._list.item(i)
                if not item.data(_HDR_ROLE):
                    continue
                grp_key = item.data(_GRP_KEY)
                label   = item.data(_GRP_LABEL)
                if grp_key in matched_groups:
                    item.setHidden(False)
                    item.setText(f"▼ {label}")
                else:
                    item.setHidden(True)

            # マッチしないグループの子アイテムも非表示に
            current_grp = None
            group_visible = False
            for i in range(n):
                item = self._list.item(i)
                if item.data(_HDR_ROLE):
                    current_grp = item.data(_GRP_KEY)
                    group_visible = (current_grp in matched_groups)
                    continue
                if not group_visible:
                    item.setHidden(True)

        else:
            # ブラウズモード: _expanded_groups の状態に従って表示/非表示
            current_expanded = False
            for i in range(n):
                item = self._list.item(i)
                if item.data(_HDR_ROLE):
                    grp_key = item.data(_GRP_KEY)
                    label   = item.data(_GRP_LABEL)
                    current_expanded = (grp_key in self._expanded_groups)
                    item.setText(f"{'▼' if current_expanded else '▶'} {label}")
                    item.setHidden(False)
                else:
                    item.setHidden(not current_expanded)

    # ── データ ─────────────────────────────────────────────────────────
    def _add_genre_header(self, grp_key: str, label: str) -> None:
        hdr = QListWidgetItem(f"▶ {label}")
        hdr.setFlags(Qt.ItemFlag.ItemIsEnabled)
        hdr.setFont(ui_font(bold=True))
        hdr.setForeground(QColor(ACCENT))
        hdr.setBackground(QColor(SURFACE1))
        hdr.setData(_HDR_ROLE, True)
        hdr.setData(_GRP_KEY, grp_key)
        hdr.setData(_GRP_LABEL, label)
        self._list.addItem(hdr)

    def refresh(self) -> None:
        self._list.clear()

        nsfw_clause = "" if _show_nsfw() else "AND COALESCE(is_nsfw,0)=0"
        base_clause = "AND COALESCE(base,'') = ?" if self._base_filter else ""
        params = (self._base_filter,) if self._base_filter else ()
        genre = self._genre_filter  # "all" / "categorized" / "__unset__"

        if genre == "__unset__":
            genre_clause = "AND (lora_genre IS NULL OR lora_genre = '')"
        elif genre == "categorized":
            genre_clause = "AND lora_genre IS NOT NULL AND lora_genre != ''"
        else:
            genre_clause = ""

        sql = (
            f"SELECT m.* FROM models m "
            f"LEFT JOIN lora_genres g ON m.lora_genre = g.key "
            f"WHERE m.type='lora' {nsfw_clause} "
            f"{base_clause} {genre_clause} "
            f"ORDER BY CASE WHEN m.lora_genre IS NULL OR m.lora_genre='' THEN 999999 "
            f"ELSE COALESCE(g.sort_order, 999998) END, "
            f"COALESCE(NULLIF(m.lora_genre,''),'__unset__'), "
            f"COALESCE(m.base,'zzz'), COALESCE(NULLIF(m.title,''), m.name), m.name"
        )
        rows = _env_db.fetchall(sql, params)
        genre_labels = _lora_genre_label_map()

        current_group: str | None = None
        for row in rows:
            gkey = (row["lora_genre"] or "").strip() or "__unset__"
            if gkey not in genre_labels and gkey != "__unset__":
                _env_db.execute(
                    "UPDATE models SET lora_genre=NULL, updated_at=CURRENT_TIMESTAMP WHERE invoke_key=?",
                    (row["invoke_key"],),
                )
                gkey = "__unset__"
            if gkey != current_group:
                current_group = gkey
                label = (
                    tr("model_browser.lora_genre_unset_group")
                    if gkey == "__unset__"
                    else genre_labels.get(gkey, gkey)
                )
                self._add_genre_header(gkey, label)
            self._add_item(row)

        if self._list.count() == 0:
            hint = QListWidgetItem(tr("model_browser.lora_no_results"))
            hint.setFlags(Qt.ItemFlag.NoItemFlags)
            hint.setForeground(QColor(SUBTEXT))
            hint.setFont(ui_font(-1))
            self._list.addItem(hint)

        if getattr(self, "_expand_all_groups_after_refresh", False):
            self._expanded_groups.update(self._current_header_group_keys())
            self._expand_all_groups_after_refresh = False
        elif not self._expanded_groups:
            self._expanded_groups.update(self._current_header_group_keys())

        self._update_visual()
        self._apply_filter()

    def retranslate_and_restyle(self) -> None:
        self._hdr.set_title("LoRA")
        self._hint_lbl.setText(tr("model_browser.double_click_hint"))
        current = self._genre_view_combo.currentData()
        self._genre_view_combo.blockSignals(True)
        self._genre_view_combo.clear()
        self._genre_view_combo.addItem(tr("model_browser.lora_view_all"), "all")
        self._genre_view_combo.addItem(tr("model_browser.lora_view_categorized"), "categorized")
        self._genre_view_combo.addItem(tr("model_browser.lora_view_uncategorized"), "__unset__")
        idx = self._genre_view_combo.findData(current)
        if idx >= 0:
            self._genre_view_combo.setCurrentIndex(idx)
        self._genre_view_combo.blockSignals(False)
        self._add_genre_btn.setToolTip(tr("model_browser.lora_genre_add"))
        self._search_edit.setPlaceholderText(tr("model_browser.lora_search_placeholder"))
        self._lora_clear_btn.setToolTip(tr("lora_browser.search_clear_tooltip"))
        self.refresh()

    def _current_header_group_keys(self) -> set[str]:
        keys: set[str] = set()
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.data(_HDR_ROLE):
                key = item.data(_GRP_KEY)
                if key:
                    keys.add(str(key))
        return keys

    def _add_item(self, row) -> None:
        name      = row["name"] or row["invoke_key"]
        title     = row["title"] or ""
        comment   = row["comment"] or ""
        available = bool(row["available"])
        key       = row["invoke_key"]
        gkey      = row["lora_genre"] if row["lora_genre"] else None
        display_name = name if available else tr("model_browser.lora_deleted_prefix", name=name)

        pix = _load_thumb(key)
        reserve_thumb = bool(pix or title or comment)
        if reserve_thumb or title or comment:
            lines = [display_name, title, comment]
        else:
            lines = [display_name]
        # ジャンルは検索・分類用に保持し、カード表示は3行に固定する。
        if self._genre_filter == "all" and gkey:
            genre_label = f"[{_lora_genre_label_map().get(gkey, gkey)}]"
        else:
            genre_label = ""
        if not available:
            if len(lines) >= 3:
                lines[2] = (lines[2] + " " if lines[2] else "") + tr("model_browser.lora_deleted")
            else:
                lines.append(tr("model_browser.lora_deleted"))

        disp = "\n".join(lines)

        item = QListWidgetItem(disp)
        item.setData(_LORA_KEY,  key)
        item.setData(_LORA_NAME, display_name)
        item.setData(_LORA_BASE, row["base"] or "sdxl")
        item.setData(_LORA_DISP, disp)
        item.setData(_LORA_SEARCH_TEXT, " ".join([name, title, comment]).lower())
        item.setData(_LORA_RESERVED_THUMB, reserve_thumb)
        item.setData(_LORA_HAS_THUMB, bool(pix))
        item.setData(_LORA_AVAILABLE, available)
        item.setData(_MODEL_TITLE_ROLE, title)
        item.setData(_MODEL_COMMENT_ROLE, comment)
        item.setData(Qt.ItemDataRole.UserRole + 30, genre_label)
        item.setForeground(QColor(TEXT))
        item.setBackground(QColor(SURFACE0))

        if pix:
            item.setIcon(QIcon(pix))
        elif reserve_thumb:
            item.setIcon(QIcon(_default_lora_thumb()))
        if reserve_thumb:
            item.setSizeHint(QSize(0, max(_THUMB_SIZE + 8, self._list.fontMetrics().lineSpacing() * 3 + 12)))

        if not available:
            item.setForeground(QColor(SUBTEXT))

        self._list.addItem(item)

    def _update_visual(self) -> None:
        """選択中のLoRAにチェックマークを付ける。"""
        for i in range(self._list.count()):
            item = self._list.item(i)
            key  = item.data(_LORA_KEY)
            if not key:
                continue  # ベースモデルヘッダー行はスキップ
            disp = item.data(_LORA_DISP) or ""
            if key in self._selected_keys:
                # 先頭行に ✓ を追加
                lines  = disp.split("\n")
                lines[0] = "✓  " + lines[0]
                item.setText("\n".join(lines))
                item.setData(_LORA_SELECTED, True)
                item.setBackground(QColor(SURFACE2))
                item.setForeground(QColor(TEXT))
            else:
                item.setText(disp)
                item.setData(_LORA_SELECTED, False)
                item.setBackground(QColor(SURFACE0))
                item.setForeground(QColor(TEXT))

    def _scroll_to_group(self, grp_key: str) -> None:
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.data(_HDR_ROLE) and item.data(_GRP_KEY) == grp_key:
                self._list.setCurrentItem(item)
                self._list.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtTop)
                return

    # ── イベント ────────────────────────────────────────────────────────
    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        """ヘッダークリックでグループを展開/折りたたむ。"""
        if not item.data(_HDR_ROLE):
            return
        grp_key = item.data(_GRP_KEY)
        if grp_key in self._expanded_groups:
            self._expanded_groups.discard(grp_key)
        else:
            self._expanded_groups.add(grp_key)
        self._apply_filter()

    def _on_double_click(self, item: QListWidgetItem) -> None:
        key  = item.data(_LORA_KEY)
        if not key:
            return  # ヘッダー行はスキップ
        if item.data(_LORA_AVAILABLE) is False:
            QMessageBox.information(
                self,
                tr("model_browser.lora_deleted_title"),
                tr("model_browser.lora_deleted_msg"),
            )
            return
        name = item.data(_LORA_NAME)
        base = item.data(_LORA_BASE)

        if key in self._selected_keys:
            # 解除
            self._selected_keys.discard(key)
            self.lora_toggled.emit({"action": "remove", "invoke_key": key})
        else:
            # 追加: weight を尋ねる
            text, ok = QInputDialog.getText(
                self, "LoRA weight", f"{name}\nweight (-1.0〜2.0):", text="0.75")
            if not ok:
                return
            try:
                weight = max(-1.0, min(2.0, float(text)))
            except ValueError:
                weight = 0.75
            self._selected_keys.add(key)
            # invoke_hash を DB から取得してメタデータ記録に使う
            _hr = _env_db.fetchone(
                "SELECT invoke_hash FROM models WHERE invoke_key=?", (key,)
            )
            invoke_hash = (_hr["invoke_hash"] or "") if _hr else ""
            self.lora_toggled.emit({
                "action": "add", "invoke_key": key,
                "hash": invoke_hash,
                "name": name, "base": base, "weight": weight,
            })
        self._update_visual()

    def _on_context_menu(self, pos: QPoint) -> None:
        item = self._list.itemAt(pos)
        menu = QMenu(self)
        if item is None:
            menu.addAction(tr("model_browser.lora_genre_add")).triggered.connect(self._add_lora_genre)
            menu.exec(self._list.viewport().mapToGlobal(pos))
            return
        if item.data(_HDR_ROLE):
            genre_key = item.data(_GRP_KEY)
            if genre_key != "__unset__":
                menu.addAction(tr("model_browser.lora_genre_rename")).triggered.connect(
                    lambda: self._rename_lora_genre(genre_key))
                menu.addAction(tr("model_browser.lora_genre_delete")).triggered.connect(
                    lambda: self._delete_lora_genre(genre_key))
                menu.addSeparator()
            menu.addAction(tr("model_browser.lora_genre_add")).triggered.connect(self._add_lora_genre)
            menu.exec(self._list.viewport().mapToGlobal(pos))
            return
        key = item.data(_LORA_KEY)
        if not key:
            return
        menu.addAction(tr("model_browser.lora_annotate_action")).triggered.connect(
            lambda: self._edit_lora(key))
        menu.addSeparator()
        menu.addAction(tr("model_browser.lora_genre_add")).triggered.connect(self._add_lora_genre)
        menu.exec(self._list.viewport().mapToGlobal(pos))

    def _add_lora_genre(self) -> None:
        label, ok = QInputDialog.getText(
            self,
            tr("model_browser.lora_genre_add_title"),
            tr("model_browser.lora_genre_name_label"),
        )
        label = label.strip()
        if not ok or not label:
            return
        key = _make_lora_genre_key(label)
        row = _env_db.fetchone("SELECT COALESCE(MAX(sort_order),0) AS m FROM lora_genres")
        sort_order = int(row["m"] or 0) + 10 if row else 100
        _env_db.execute(
            "INSERT INTO lora_genres (key, label, sort_order) VALUES (?,?,?)",
            (key, label, sort_order),
        )
        self._expanded_groups.add(key)
        self.refresh()

    def _rename_lora_genre(self, key: str) -> None:
        row = _env_db.fetchone("SELECT label FROM lora_genres WHERE key=?", (key,))
        if not row:
            return
        label, ok = QInputDialog.getText(
            self,
            tr("model_browser.lora_genre_rename_title"),
            tr("model_browser.lora_genre_name_label"),
            text=row["label"] or key,
        )
        label = label.strip()
        if not ok or not label:
            return
        _env_db.execute(
            "UPDATE lora_genres SET label=?, updated_at=CURRENT_TIMESTAMP WHERE key=?",
            (label, key),
        )
        self.refresh()

    def _delete_lora_genre(self, key: str) -> None:
        row = _env_db.fetchone("SELECT label FROM lora_genres WHERE key=?", (key,))
        if not row:
            return
        ret = QMessageBox.question(
            self,
            tr("model_browser.lora_genre_delete_title"),
            tr("model_browser.lora_genre_delete_msg", name=row["label"] or key),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        with _env_db.transaction() as conn:
            conn.execute(
                "UPDATE models SET lora_genre=NULL, updated_at=CURRENT_TIMESTAMP WHERE lora_genre=?",
                (key,),
            )
            conn.execute("DELETE FROM lora_genres WHERE key=?", (key,))
        self._expanded_groups.discard(key)
        self._expanded_groups.add("__unset__")
        self.refresh()

    def _edit_lora(self, key: str) -> None:
        """LoRA の統合編集ダイアログを開く"""
        dlg = _LoRAEditDialog(self, key=key)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        v = dlg.result_values

        # models テーブル更新（タイトル・コメント・NSFW・ジャンル）
        _env_db.execute(
            "UPDATE models SET title=?, comment=?, is_nsfw=?, lora_genre=?, "
            "updated_at=CURRENT_TIMESTAMP WHERE invoke_key=?",
            (v["title"] or None, v["comment"] or None, v["is_nsfw"],
             v.get("lora_genre"), key),
        )

        # lora_trigger_sets を全置換（DELETE → INSERT）
        with _env_db.transaction() as conn:
            conn.execute(
                "DELETE FROM lora_trigger_sets WHERE invoke_key=?", (key,)
            )
            for order, ts in enumerate(v["trigger_sets"]):
                conn.execute(
                    "INSERT INTO lora_trigger_sets "
                    "(invoke_key, sort_order, label, trigger_words) VALUES (?,?,?,?)",
                    (key, order, ts["label"], ts["trigger_words"]),
                )

        # lora_neg_prompt_sets を全置換（DELETE → INSERT）
        with _env_db.transaction() as conn:
            conn.execute(
                "DELETE FROM lora_neg_prompt_sets WHERE invoke_key=?", (key,)
            )
            for order, ns in enumerate(v.get("neg_prompt_sets", [])):
                conn.execute(
                    "INSERT INTO lora_neg_prompt_sets "
                    "(invoke_key, sort_order, label, neg_words) VALUES (?,?,?,?)",
                    (key, order, ns["label"], ns["neg_words"]),
                )

        self.refresh()

    # ── 同期 ────────────────────────────────────────────────────────────
    def _sync(self, notify: bool = False) -> None:
        """notify=True はユーザーが同期ボタンを押した場合（完了ダイアログを出す）。
        プログラム起点の自動同期（接続時など）はダイアログを出さない。"""
        if not self._client:
            QMessageBox.warning(self, tr("model_browser.sync_title"), tr("model_browser.sync_not_connected"))
            return
        self._sync_notify = notify
        self._hdr.set_syncing(True)
        self._sync_worker = _SyncWorker(self._client)
        self._sync_worker.finished.connect(self._on_sync_done)
        self._sync_worker.error.connect(self._on_sync_error)
        self._sync_worker.start()

    def _on_sync_done(self) -> None:
        self._hdr.set_syncing(False)
        self.refresh()
        if getattr(self, "_sync_notify", False):
            self._sync_notify = False
            QMessageBox.information(
                self,
                tr("model_browser.sync_done_title"),
                tr("model_browser.sync_done_msg"),
            )

    def _on_sync_error(self, msg: str) -> None:
        self._sync_notify = False
        self._hdr.set_syncing(False)
        QMessageBox.warning(self, tr("model_browser.sync_error_title"), msg)

