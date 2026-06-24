"""
メインウィンドウ

レイアウト:
  ToolBar
  ┌──────────┬──────────────────────┬──────────┐
  │ 左ペイン  │   中央ペイン          │ 右ペイン │
  │(タグBr.)  │  (プロンプトEditor)  │(履歴等)  │
  └──────────┴──────────────────────┴──────────┘
  StatusBar

左右ペインはトグルボタンで折りたたみ可能（QSplitter + setSizes）。
"""
from __future__ import annotations

import datetime
from datetime import timezone
import json
import hashlib
import random
import sys

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QSplitter, QToolBar, QTabWidget,
    QStatusBar, QLabel, QToolButton, QMessageBox,
    QSizePolicy, QSpinBox, QDoubleSpinBox, QComboBox, QLineEdit,
    QCheckBox, QVBoxLayout, QHBoxLayout, QDialog, QPushButton, QTextEdit,
    QDialogButtonBox, QListWidget, QProgressBar, QStackedWidget, QButtonGroup,
    QFileDialog, QProgressDialog, QScrollArea, QFrame,
)
from PySide6.QtCore import Qt, QTimer, QSize, QUrl, QThread, Signal, QEvent, QPropertyAnimation
from PySide6.QtGui import QFont, QAction, QIcon, QDragEnterEvent, QDropEvent, QTextCursor, QPixmap

from api.invoke_client import InvokeClient, InvokeConnectionError
from core.invoke_prompt import parse_invoke_prompt
from core.prompt_builder import GroupTile, NaturalTextTile, PromptDocument, TagTile
from core.png_meta import read_png_meta
from core.i18n import set_language, tr
from ui.meta_dialog import MetaDialog
from ui.prompt_editor import PromptEditor
from ui.tag_browser import TagBrowser
from ui.side_panel import SidePanel, HISTORY_GENERATION_MIME
from ui.model_browser import ModelBrowser, LoRABrowser, LoRAChipBar, _base_label
from ui.prompt_text_browser import PromptTextBrowser
from ui.group_preset_browser import GroupPresetBrowser
from ui.generation_plan_dialog import GenerationPlanDialog
import ui.styles as styles
from ui.styles import (
    ACCENT, COMBO_ARROW_URL, EMOJI_ICON_SS, GREEN, RED, SURFACE0, SURFACE1,
    SURFACE2, TEXT, SUBTEXT, ui_font, themed_button_style,
)
import db.app_db as _app_db
import db.env_db as _env_db
import db.history_db as _history_db
import db.library_db as _library_db
import core.local_storage as local_storage
from api.lm_client import LMClient, LMStudioError, translation_fallback_from_thinking
from core.text_sanitize import single_line_text

# ベース別スケジューラーリスト（フォールバック用ハードコード、Invoke OpenAPI仕様に基づく）
# 接続時に fetch_scheduler_map() で上書きされる
_SCHEDULERS: dict[str, list[str]] = {
    "sdxl":    ["ddim", "ddpm", "deis", "deis_k", "lms", "lms_k", "pndm",
                "heun", "heun_k", "euler", "euler_k", "euler_a",
                "kdpm_2", "kdpm_2_k", "kdpm_2_a", "kdpm_2_a_k",
                "dpmpp_2s", "dpmpp_2s_k", "dpmpp_2m", "dpmpp_2m_k",
                "dpmpp_2m_sde", "dpmpp_2m_sde_k", "dpmpp_3m", "dpmpp_3m_k",
                "dpmpp_sde", "dpmpp_sde_k", "er_sde", "unipc", "unipc_k", "lcm", "tcd"],
    "sd-1":    ["ddim", "ddpm", "deis", "deis_k", "lms", "lms_k", "pndm",
                "heun", "heun_k", "euler", "euler_k", "euler_a",
                "kdpm_2", "kdpm_2_k", "kdpm_2_a", "kdpm_2_a_k",
                "dpmpp_2s", "dpmpp_2s_k", "dpmpp_2m", "dpmpp_2m_k",
                "dpmpp_2m_sde", "dpmpp_2m_sde_k", "dpmpp_3m", "dpmpp_3m_k",
                "dpmpp_sde", "dpmpp_sde_k", "er_sde", "unipc", "unipc_k", "lcm", "tcd"],
    "flux":    ["euler", "heun", "lcm"],
    "flux2":   ["euler", "heun", "lcm"],
    "z-image": ["euler", "heun", "lcm"],
    "anima":   ["euler", "heun", "dpmpp_2m", "dpmpp_2m_sde", "er_sde", "lcm"],
}
# ベース → デノイザーノードタイプのマッピング（fetch_scheduler_map のキーと対応）
_BASE_DENOISE_NODE: dict[str, str] = {
    "sdxl":    "denoise_latents",
    "sd-1":    "denoise_latents",
    "flux":    "flux_denoise",
    "flux2":   "flux2_denoise",
    "z-image": "z_image_denoise",
    "anima":   "anima_denoise",
}
_DEFAULT_STEPS: dict[str, int] = {
    "sdxl": 30, "sd-1": 30, "flux": 30, "flux2": 9, "z-image": 9, "anima": 60,
}


def _get_setting(key: str, default: str) -> str:
    """app_settings から値を読む（なければ default を返す）"""
    row = _app_db.fetchone(
        "SELECT value FROM app_settings WHERE key = ?", (key,)
    )
    return row["value"] if row else default


def _set_setting(key: str, value: str) -> None:
    """app_settings に値を保存する。"""
    _app_db.execute(
        "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
        (key, value),
    )


# ── ポップアップだけ幅を広げるコンボボックス ─────────────────────────────

_COMBO_BASE_LABEL_ROLE = Qt.ItemDataRole.UserRole + 1
_COMBO_NOTE_ROLE = Qt.ItemDataRole.UserRole + 2


class _WidePopupComboBox(QComboBox):
    """現在値に合わせて本体幅を調整し、ドロップダウンだけ必要分広げるコンボ。"""

    def __init__(
        self,
        parent=None,
        *,
        min_width: int = 80,
        max_width: int | None = None,
        width_reserve: int = 820,
    ):
        super().__init__(parent)
        self._min_body_width = min_width
        self._max_body_width = max_width
        self._width_reserve = width_reserve
        self._popup_line_edit = None
        self.currentTextChanged.connect(lambda *_: QTimer.singleShot(0, self.adjust_to_current_text))

    def setEditable(self, editable: bool) -> None:
        super().setEditable(editable)
        self._install_line_edit_popup_filter()

    def _install_line_edit_popup_filter(self) -> None:
        line_edit = self.lineEdit() if self.isEditable() else None
        if line_edit is None or line_edit is self._popup_line_edit:
            return
        if self._popup_line_edit is not None:
            self._popup_line_edit.removeEventFilter(self)
        self._popup_line_edit = line_edit
        self._popup_line_edit.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:
        if (
            obj is self._popup_line_edit
            and event.type() == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.LeftButton
        ):
            QTimer.singleShot(0, self.showPopup)
        return super().eventFilter(obj, event)

    def show_leading_text(self) -> None:
        if self.isEditable() and self.lineEdit() is not None:
            self.lineEdit().setAlignment(Qt.AlignmentFlag.AlignLeft)
            self.lineEdit().setCursorPosition(0)

    def set_adaptive_width(self, min_width: int, max_width: int | None = None) -> None:
        self._min_body_width = min_width
        self._max_body_width = max_width
        self.adjust_to_current_text()

    def set_width_reserve(self, width_reserve: int) -> None:
        self._width_reserve = width_reserve
        self.adjust_to_current_text()

    def _available_width_cap(self) -> int:
        cap = self._max_body_width
        win = self.window()
        if win is not None:
            # Keep room for the controls to the right of the combo.
            screen_cap = max(self._min_body_width, win.width() - self._width_reserve)
            cap = min(cap, screen_cap) if cap is not None else screen_cap
        return max(self._min_body_width, cap or self._min_body_width)

    def _text_width(self, text: str) -> int:
        return self.fontMetrics().horizontalAdvance(text or "") + 42

    def adjust_to_current_text(self) -> None:
        text = ""
        idx = self.currentIndex()
        if idx >= 0:
            base_label = self.itemData(idx, _COMBO_BASE_LABEL_ROLE)
            if base_label:
                text = str(base_label)
        if not text:
            text = self.lineEdit().text() if self.isEditable() and self.lineEdit() is not None else self.currentText()
        lines = text.splitlines()
        text = lines[0].strip() if lines else ""
        target = max(self._min_body_width, self._text_width(text))
        target = min(target, self._available_width_cap())
        if self.width() != target:
            self.setFixedWidth(target)

    def showPopup(self) -> None:
        view = self.view()
        # 全アイテムの最大表示幅を計算してポップアップに適用
        content_w = view.sizeHintForColumn(0) + 24   # スクロールバー余白
        view.setMinimumWidth(max(self.width(), min(content_w, self._available_width_cap())))
        super().showPopup()

    def hidePopup(self) -> None:
        self.view().setMinimumWidth(0)
        super().hidePopup()


class _AdoptHistoryDialog(QDialog):
    """履歴行を、選択した履歴マップノードの子として単独接続するダイアログ。"""

    def __init__(self, adoptee: dict, candidates: list[dict], parent=None):
        super().__init__(parent)
        self._adoptee = adoptee
        self._candidates = candidates
        self._selected_parent: tuple[str, int] | None = None
        self.setWindowTitle(tr("adopt_history.title"))
        self.resize(560, 520)
        geo = _get_setting("adopt_history_geometry", "")
        if geo:
            try:
                from PySide6.QtCore import QByteArray
                self.restoreGeometry(QByteArray.fromHex(geo.encode("ascii")))
            except Exception:
                pass

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)
        body = QHBoxLayout()
        body.setSpacing(10)
        root.addLayout(body, stretch=1)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        holder = QWidget()
        self._list_lay = QVBoxLayout(holder)
        self._list_lay.setContentsMargins(2, 2, 2, 2)
        self._list_lay.setSpacing(6)
        self._list_lay.addStretch(1)
        self._scroll.setWidget(holder)
        body.addWidget(self._scroll, stretch=1)

        side = QFrame()
        side.setFixedWidth(190)
        side.setStyleSheet(
            f"QFrame {{ background: {SURFACE1}; border: 1px solid {SURFACE2}; border-radius: 4px; }}"
            f"QLabel {{ color: {TEXT}; background: transparent; border: none; }}"
        )
        side_lay = QVBoxLayout(side)
        side_lay.setContentsMargins(8, 8, 8, 8)
        side_lay.setSpacing(8)
        self._selected_lbl = QLabel(tr("adopt_history.no_parent"))
        self._selected_lbl.setWordWrap(True)
        side_lay.addWidget(self._selected_lbl)
        side_lay.addWidget(self._thumb_label_for(adoptee, 96), alignment=Qt.AlignmentFlag.AlignCenter)
        self._adoptee_lbl = QLabel(tr("adopt_history.adoptee_label", n=adoptee["history_id"]))
        self._adoptee_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        side_lay.addWidget(self._adoptee_lbl)
        side_lay.addStretch(1)
        self._ok_btn = QPushButton(tr("adopt_history.apply"))
        self._ok_btn.setEnabled(False)
        self._ok_btn.clicked.connect(self.accept)
        side_lay.addWidget(self._ok_btn)
        body.addWidget(side)

        for row in candidates:
            self._add_candidate(row)

    def closeEvent(self, event) -> None:
        _set_setting("adopt_history_geometry", bytes(self.saveGeometry().toHex()).decode("ascii"))
        super().closeEvent(event)

    def accept(self) -> None:
        _set_setting("adopt_history_geometry", bytes(self.saveGeometry().toHex()).decode("ascii"))
        super().accept()

    def selected_parent(self) -> tuple[str, int] | None:
        return self._selected_parent

    def _thumb_label_for(self, row: dict, size: int) -> QLabel:
        lbl = QLabel()
        lbl.setFixedSize(size, size)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pix = QPixmap()
        data = row.get("thumbnail_data")
        if data and pix.loadFromData(bytes(data)) and not pix.isNull():
            lbl.setPixmap(pix.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        else:
            lbl.setText("🖼")
        lbl.setStyleSheet(f"background: {SURFACE0}; color: {SUBTEXT}; border: 1px solid {SURFACE2}; border-radius: 3px;")
        return lbl

    def _add_candidate(self, row: dict) -> None:
        if (row["history_db"], row["history_id"]) == (self._adoptee["history_db"], self._adoptee["history_id"]):
            return
        btn = QToolButton()
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        btn.setText(tr("adopt_history.candidate_label", n=row["history_id"]))
        btn.setCheckable(True)
        btn.setAutoExclusive(True)
        pix = QPixmap()
        data = row.get("thumbnail_data")
        if data and pix.loadFromData(bytes(data)) and not pix.isNull():
            btn.setIcon(QIcon(pix))
            btn.setIconSize(QSize(96, 96))
        btn.setMinimumHeight(108)
        btn.setStyleSheet(
            f"QToolButton {{ background: {SURFACE1}; color: {TEXT}; border: 1px solid {SURFACE2}; border-radius: 4px; padding: 4px; }}"
            f"QToolButton:checked {{ border: 2px solid {GREEN}; background: {SURFACE2}; }}"
            f"QToolButton:hover {{ border-color: {ACCENT}; }}"
        )
        btn.clicked.connect(lambda _=False, r=row: self._select_parent(r))
        self._list_lay.insertWidget(max(0, self._list_lay.count() - 1), btn)

    def _select_parent(self, row: dict) -> None:
        self._selected_parent = (str(row["history_db"]), int(row["history_id"]))
        self._selected_lbl.setText(tr("adopt_history.parent_label", n=row["history_id"]))
        self._ok_btn.setEnabled(True)


# ── UTC タイムスタンプ変換（複数ワーカーから共用） ────────────────────────

def _utc_to_local(ts: str) -> str:
    """Invoke の UTC タイムスタンプをローカル時刻文字列に変換する。"""
    if not ts:
        return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        ts_clean = ts.replace("Z", "+00:00")
        dt = datetime.datetime.fromisoformat(ts_clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, AttributeError):
        return ts[:19]


# ── 接続確認ワーカー ──────────────────────────────────────────────────────

class _ConnCheckWorker(QThread):
    """
    queue_status() をバックグラウンドで実行するワーカー。
    メインスレッドで同期 HTTP 待ちが発生して UI がブロックされるのを防ぐ。

    Signals:
        conn_ok(pending, in_progress, completed): 接続成功
        conn_ng():                                接続失敗
    """
    conn_ok = Signal(int, int, int)
    conn_ng = Signal()

    def __init__(self, client: "InvokeClient", parent=None):
        super().__init__(parent)
        self._client = client

    def run(self) -> None:
        try:
            status = self._client.queue_status()
            q = status.get("queue", {})
            self.conn_ok.emit(
                q.get("pending",     0),
                q.get("in_progress", 0),
                q.get("completed",   0),
            )
        except Exception:
            self.conn_ng.emit()


# ── ボード一覧取得ワーカー ────────────────────────────────────────────────

class _BoardListWorker(QThread):
    """Invoke のボード一覧をバックグラウンドで取得するワーカー。"""

    loaded = Signal(list)
    failed = Signal(str)

    def __init__(self, client: "InvokeClient", parent=None):
        super().__init__(parent)
        self._client = client

    def run(self) -> None:
        try:
            self.loaded.emit(self._client.boards_list(limit=200))
        except Exception as exc:
            self.failed.emit(str(exc))


class _NodeFullImageWorker(QThread):
    """履歴マップのプレビュー用フル画像を Invoke からバックグラウンド取得する。

    ノードクリック時はまずローカル/サムネイルを即時表示し、フル画像はこのワーカーで
    取得して後から差し替える（UIスレッドを同期 HTTP でブロックしないため）。
    """

    loaded = Signal(object, object)  # (node_key, image_bytes)

    def __init__(self, client: "InvokeClient", node_key, image_name: str, parent=None):
        super().__init__(parent)
        self._client = client
        self._node_key = node_key
        self._image_name = image_name

    def run(self) -> None:
        try:
            data = self._client.image_full(self._image_name)
        except Exception:
            return
        if data:
            self.loaded.emit(self._node_key, data)


# ── 履歴バックフィル同期ワーカー ──────────────────────────────────────────

class _HistorySyncWorker(QThread):
    """
    Invoke 画像一覧 API → DB バックフィル をバックグラウンドで実行するワーカー。
    images_list() + N × image_metadata() をメインスレッド外で行う。

    Signals:
        synced(new_count, ids):   完了（新規紐付け件数と更新された generation_id を返す）
        generation_aborted(ids):  全アイテムが cancelled/failed になった generation_id リスト
    """
    synced             = Signal(int, list)
    copy_failed        = Signal(str)        # 画像コピー失敗時（メッセージを渡す）
    generation_aborted = Signal(list, str)  # キャンセル/失敗確定の generation_id リストと履歴名

    def __init__(self, client: "InvokeClient", history_name: str, parent=None):
        super().__init__(parent)
        self._client = client
        # 開始時点のアクティブ履歴名を固定する。実行中にユーザーがアクティブ履歴を
        # 切り替えても、読み書き先がすり替わらないようにするため
        # （_history_db モジュール関数はコール時点のアクティブ履歴を解決してしまう）。
        self._history_name = history_name

    @property
    def history_name(self) -> str:
        return self._history_name

    def run(self) -> None:
        import json as _json

        hdb = _history_db.for_history(self._history_name)

        # まだ全画像が収集されていないエントリを対象にする：
        #   ① 最初の画像がまだ来ていない (invoke_image_name IS NULL)
        #   ② image_count が記録済み（新規生成）で、かつ枚数分揃っていない
        #
        # ※ image_count IS NULL の旧エントリは既に処理済みのため対象外
        pending = hdb.fetchall(
            """SELECT g.id, g.invoke_queue_item_ids, g.group_id,
                      g.image_count,
                      g.invoke_image_name
               FROM generations g
               WHERE g.invoke_queue_item_ids IS NOT NULL
                 AND (
                   g.invoke_image_name IS NULL
                   OR (
                     g.image_count IS NOT NULL
                     AND g.image_count > (
                         SELECT COUNT(*) FROM generation_images
                         WHERE generation_id = g.id
                     )
                   )
                 )"""
        )
        if not pending:
            self.synced.emit(0, [])
            return

        new_count = 0
        changed_gen_ids: set[int] = set()
        aborted_gen_ids: list[int] = []

        for row in pending:
            gen_id          = row["id"]
            group_id        = row["group_id"]
            image_count     = row["image_count"] or 1  # NULL は旧エントリ（1枚扱い）
            first_image_set = row["invoke_image_name"] is not None

            try:
                item_ids = _json.loads(row["invoke_queue_item_ids"])
            except Exception:
                continue
            ordered_item_ids: list[int] = []
            for item_id in item_ids:
                if isinstance(item_id, int):
                    ordered_item_ids.append(item_id)
            # Invoke からの item_ids は新しいものが先頭に並ぶため、
            # ユーザーが指定した 1 枚目は末尾側になる。
            item_order_map = {
                item_id: idx
                for idx, item_id in enumerate(reversed(ordered_item_ids))
            }

            # 処理済みの item_id を取得（再ダウンロード防止）
            existing = hdb.fetchall(
                "SELECT invoke_item_id FROM generation_images "
                "WHERE generation_id=? AND invoke_item_id IS NOT NULL",
                (gen_id,),
            )
            done_item_ids = {r["invoke_item_id"] for r in existing}

            # 保存先フォルダ確認（一度だけ）
            has_local = local_storage.has_group_folder(group_id, db=hdb)
            dest_dir  = local_storage.resolve_folder_path(group_id, db=hdb) if has_local else None
            if has_local and dest_dir and not local_storage.is_drive_accessible(dest_dir):
                self.copy_failed.emit(
                    f"保存先ドライブにアクセスできません: {dest_dir}"
                )
                return

            # このジェネレーションにまだ完了待ちアイテムが残っているか
            gen_still_pending = False
            gen_new_image     = False

            for item_id in item_ids:
                if item_id in done_item_ids:
                    continue  # 処理済みはスキップ

                try:
                    # get_queue_item を直接使い、ステータスと画像名を1回で取得する
                    item_info = self._client.get_queue_item(item_id)
                    status    = item_info.get("status", "")

                    if status == "completed":
                        # 完了: 画像名を抽出
                        image_name: str | None = None
                        for result in item_info.get("session", {}).get("results", {}).values():
                            if result.get("type") == "image_output":
                                image_name = result.get("image", {}).get("image_name")
                                break

                        if not image_name:
                            gen_still_pending = True  # 完了だが画像がない → 異常系、様子見
                            continue

                        # ローカル保存
                        saved_local: str | None = None
                        if dest_dir:
                            try:
                                image_bytes = self._client.image_full(image_name)
                                saved_path  = local_storage.copy_image(
                                    image_bytes, image_name, dest_dir
                                )
                                saved_local = str(saved_path)
                            except OSError as e:
                                self.copy_failed.emit(str(e))
                                return

                        # generation_images に記録
                        sort_order = item_order_map.get(item_id)
                        if sort_order is None:
                            sort_order = len(ordered_item_ids) + len(done_item_ids)
                        hdb.execute(
                            """INSERT OR IGNORE INTO generation_images
                               (generation_id, sort_order, invoke_item_id,
                                invoke_image_name, local_path)
                               VALUES (?,?,?,?,?)""",
                            (gen_id, sort_order, item_id, image_name, saved_local),
                        )
                        new_count     += 1
                        gen_new_image  = True
                        changed_gen_ids.add(int(gen_id))

                    elif status in ("canceled", "cancelled", "failed", "error"):
                        # Invoke の正式値は "canceled"（L1つ）。旧綴りも保険で残す
                        # 終端失敗状態: このアイテムからは画像が来ない
                        pass  # gen_still_pending は変えない

                    else:
                        # "pending" / "in_progress" / 不明: まだ待つ
                        gen_still_pending = True

                except Exception:
                    # 接続エラー等も終端失敗（canceled/failed/error）と同列に扱う。
                    # Invoke のキューは再起動で再開されないため待つ意味がない。
                    # 削除対象になるのは画像を1枚も取得できていない行のみ
                    # （下の aborted 判定条件）なので、画像取り込み済みの行が
                    # 一時的な接続エラーで消えることはない。
                    pass

            rep_row = hdb.fetchone(
                """SELECT invoke_image_name, local_path
                   FROM generation_images
                   WHERE generation_id=?
                   ORDER BY sort_order ASC, id ASC
                   LIMIT 1""",
                (gen_id,),
            )
            if rep_row and rep_row["invoke_image_name"]:
                hdb.execute(
                    "UPDATE generations SET invoke_image_name=?, local_path=? WHERE id=?",
                    (rep_row["invoke_image_name"], rep_row["local_path"], gen_id),
                )
                changed_gen_ids.add(int(gen_id))
                first_image_set = True

            # 未処理アイテムが全て終端失敗かつ画像が一枚も取れていないなら中断確定
            if not gen_still_pending and not first_image_set and not gen_new_image:
                aborted_gen_ids.append(gen_id)

        if aborted_gen_ids:
            # 削除を伴うため、ワーカー開始時点で固定した履歴名も渡す
            self.generation_aborted.emit(aborted_gen_ids, self._history_name)

        self.synced.emit(new_count, sorted(changed_gen_ids))


# ── 送信キューワーカー（バッチ生成エンジン）─────────────────────────────────

class _SendQueueWorker(QThread):
    """
    送信キュー（send_queue.db）の全ユニットを順次 Invoke へ送るワーカー。

    完了を待たずに一気に送る（残数は Invoke のキューカウンター=ステータスバーで
    見える）。1ユニット = generate_batch() 1回分。送信したら sent_item_ids を
    キューレコードに控え（クラッシュ時の二重送信防止）、unit_sent を emit する。
    履歴行への item_ids 割当とレコード削除はメインスレッド側が行う。
    失敗時は停止し failed を emit（呼び出し元がユーザー中止と同じ処理を行う）。

    Signals:
        unit_sent(int, list, str, list): (unit_id, gen_ids, history_name, item_ids)
        all_done():                      全ユニット送信完了
        failed(str):                     送信失敗（メッセージ）
    """
    unit_sent = Signal(int, list, str, list)
    all_done  = Signal()
    failed    = Signal(str)

    def __init__(self, client: "InvokeClient", parent=None):
        super().__init__(parent)
        self._client = client
        self._abort = False

    def stop(self) -> None:
        self._abort = True

    def run(self) -> None:
        import db.send_queue_db as _sq
        try:
            units = _sq.pending_units()
        except Exception as e:
            self.failed.emit(str(e))
            return
        for row in units:
            if self._abort:
                return
            unit_id      = int(row["id"])
            gen_ids      = _sq.unit_generation_ids(row)
            history_name = str(row["history_name"] or "")
            item_ids     = _sq.unit_sent_item_ids(row)
            if item_ids is None:
                try:
                    payload = json.loads(row["payload"])
                    item_ids = [int(i) for i in (self._client.generate_batch(
                        payload["pos"], payload["neg"], payload["seeds"],
                        **payload.get("gen_params", {}),
                        model_key=payload.get("model_key") or None,
                        model_name=payload.get("model_name") or None,
                        model_base=payload.get("model_base") or None,
                        template_id=payload.get("template_id"),
                        loras=payload.get("loras") or None,
                    ) or [])]
                    _sq.mark_sent(unit_id, item_ids)
                except Exception as e:
                    self.failed.emit(str(e))
                    return
            self.unit_sent.emit(unit_id, gen_ids, history_name, list(item_ids))
        self.all_done.emit()


class _CancelItemsWorker(QThread):
    """発行済み item_id を Invoke で個別キャンセルするワーカー（ベストエフォート）。

    完了済みアイテムへのキャンセルは無害。接続不可ならそれ以降は諦める
    （Invoke 再起動でキューは消えているので実害なし）。
    """

    def __init__(self, client: "InvokeClient", item_ids: list[int], parent=None):
        super().__init__(parent)
        self._client = client
        self._item_ids = list(item_ids)

    def run(self) -> None:
        for iid in self._item_ids:
            try:
                self._client.cancel_queue_item(int(iid))
            except Exception:
                return  # 接続不可（以降も無理なので打ち切り）


# ── 翻訳ワーカー（ストリーミング）──────────────────────────────────────────

class _TranslateStreamWorker(QThread):
    """
    LM Studio ネイティブ SSE API でストリーミング翻訳を実行するワーカー。

    Signals:
        status_update(str):  フェーズ通知
        thinking_chunk(str): reasoning テキストデルタ
        content_chunk(str):  本文テキストデルタ
        finished(str):       翻訳完了（結果全文）
        failed(str):         翻訳失敗（エラーメッセージ）
    """
    status_update    = Signal(str)
    thinking_chunk   = Signal(str)
    content_chunk    = Signal(str)
    translation_done = Signal(str)   # 'finished' は QThread 組み込みシグナルと衝突するため別名
    failed           = Signal(str)

    def __init__(
        self,
        client: LMClient,
        text: str,
        system_prompt: str,
        model: str,
        parent=None,
    ):
        super().__init__(parent)
        self._client        = client
        self._text          = text
        self._system_prompt = system_prompt
        self._model         = model
        self._cancel        = [False]

    def cancel(self) -> None:
        self._cancel[0] = True

    def cancel_and_wait(self, timeout_ms: int = 2000) -> None:
        self.cancel()
        if self.isRunning():
            self.wait(timeout_ms)

    def run(self) -> None:
        try:
            content_buf: list[str] = []
            thinking_buf: list[str] = []
            for ev_type, ev_data in self._client.translate_stream(
                self._text, self._system_prompt, self._model,
                cancel_flag=self._cancel,
            ):
                if self._cancel[0]:
                    return
                if ev_type == "status":
                    self.status_update.emit(ev_data)
                elif ev_type == "thinking":
                    thinking_buf.append(ev_data)
                    self.thinking_chunk.emit(ev_data)
                elif ev_type == "content":
                    content_buf.append(ev_data)
                    self.content_chunk.emit(ev_data)
                elif ev_type == "done":
                    result = "".join(content_buf).strip()
                    if not result:
                        result = translation_fallback_from_thinking("".join(thinking_buf))
                    self.translation_done.emit(result)
        except LMStudioError as e:
            self.failed.emit(str(e))
        except Exception as e:
            self.failed.emit(str(e))


class _UnregisteredPromptTranslateDialog(QDialog):
    """PNGプロンプト内の未登録タグを順次翻訳し、完了したものから中央ペインへ反映する。"""

    def __init__(self, items: list[dict], model: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("未登録タグの翻訳")
        self.setModal(True)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setMinimumSize(640, 480)
        self._items = list(items)
        self._total = len(items)
        self._index = 0
        self._model = model
        self._worker: _TranslateStreamWorker | None = None
        self._cancelled = False
        self._build_ui()

    def _build_ui(self) -> None:
        self.setStyleSheet(f"QDialog {{ background-color: {SURFACE0}; color: {TEXT}; }}")
        lay = QVBoxLayout(self)
        lay.setSpacing(8)

        self._summary = QLabel(f"未登録タグ {self._total} 件を翻訳します")
        self._summary.setFont(ui_font(bold=True))
        lay.addWidget(self._summary)

        self._progress = QProgressBar()
        self._progress.setRange(0, max(1, self._total))
        self._progress.setValue(0)
        lay.addWidget(self._progress)

        self._current = QLabel("")
        self._current.setStyleSheet(f"color: {SUBTEXT};")
        lay.addWidget(self._current)

        self._pending = QListWidget()
        self._pending.setStyleSheet(
            f"QListWidget {{ background: {SURFACE1}; color: {TEXT}; "
            f"border: 1px solid {SURFACE2}; border-radius: 4px; }}"
        )
        for item in self._items:
            tile = item["tile"]
            self._pending.addItem(tile.tag_name)
        lay.addWidget(self._pending, 1)

        self._thinking = QTextEdit()
        self._thinking.setReadOnly(True)
        self._thinking.setPlaceholderText("thinking")
        self._thinking.setFixedHeight(130)
        self._thinking.setStyleSheet(
            f"QTextEdit {{ background: #11111b; color: {SUBTEXT}; "
            f"border: 1px solid {SURFACE2}; border-radius: 4px; padding: 4px; }}"
        )
        lay.addWidget(self._thinking)

        buttons = QDialogButtonBox()
        self._cancel_btn = buttons.addButton("キャンセル", QDialogButtonBox.ButtonRole.RejectRole)
        self._cancel_btn.clicked.connect(self._cancel)
        lay.addWidget(buttons)

    def start(self) -> None:
        QTimer.singleShot(0, self._start_next)

    def _system_prompt(self) -> str:
        return (
            "You translate Stable Diffusion prompt tags into the app's local language, Japanese.\n"
            "Input is one English Danbooru-style tag or short phrase.\n"
            "Output only a concise Japanese display label. "
            "Do not output explanations, markdown, quotes, category names, or alternatives."
        )

    def _start_next(self) -> None:
        if self._cancelled:
            self.reject()
            return
        if not self._model.strip():
            QMessageBox.warning(
                self,
                tr("main.translate_lm_not_configured_title"),
                tr("main.translate_lm_not_configured"),
            )
            self.reject()
            return
        if self._index >= self._total:
            self.accept()
            return

        item = self._items[self._index]
        tile = item["tile"]
        tag = tile.tag_name.strip()
        self._thinking.clear()
        self._current.setText(f"翻訳中: {tag}  ({self._index + 1}/{self._total})")

        lm_url = _get_setting("lm_endpoint", "http://localhost:1234")
        provider = _get_setting("lm_provider", "lmstudio")
        try:
            chunk_timeout = float(_get_setting("lm_chunk_timeout", "60"))
        except ValueError:
            chunk_timeout = 60.0
        client = LMClient(base_url=lm_url, chunk_timeout=chunk_timeout, provider=provider)
        if self._index == 0:
            status = client.check_connection()
            if not status.ok:
                self._on_failed(status.message)
                return
        self._worker = _TranslateStreamWorker(
            client, tag, self._system_prompt(), self._model, parent=self
        )
        self._worker.status_update.connect(self._set_status)
        self._worker.thinking_chunk.connect(self._append_thinking)
        self._worker.translation_done.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _set_status(self, text: str) -> None:
        item = self._items[self._index]
        tag = item["tile"].tag_name
        self._current.setText(f"{tag}: {text}")

    def _append_thinking(self, text: str) -> None:
        cursor = self._thinking.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        self._thinking.setTextCursor(cursor)

    def _finish_current(self, translated: str) -> None:
        item = self._items[self._index]
        tile = item["tile"]
        translated = single_line_text(translated)
        if translated:
            tile.tag_local = translated
        tile.source_text = tile.source_text or tile.tag_name
        tile.translated_text = tile.tag_name

        add_tile = item["add_tile"]
        add_tile(tile, item["block_type"], item["position"], item["index"])

        if self._pending.count() > 0:
            self._pending.takeItem(0)
        self._index += 1
        self._progress.setValue(self._index)
        self._worker = None
        QTimer.singleShot(0, self._start_next)

    def _on_done(self, text: str) -> None:
        self._finish_current(text)

    def _on_failed(self, _msg: str) -> None:
        self._finish_current("")

    def _cancel(self) -> None:
        self._cancelled = True
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel_and_wait()
        self._worker = None
        self.reject()

    def closeEvent(self, event) -> None:
        self._cancel()
        super().closeEvent(event)



class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("PromptMosaic")
        self.resize(1400, 860)

        self._client = InvokeClient()

        # 送信キュー残骸の掃除（残っていれば前回は異常終了。送信済み item を
        # キャンセルして空に戻す。自動再開はしない=プランはプロンプトから再実行できる）
        self._check_send_queue_residue()
        # 整合性修復: 前回セッションが終了処理なしで落ちた場合に残る一時状態の行を
        # 破棄する（本来は closeEvent で完結する。純DB処理・UI構築前に同期実行）
        self._finalize_transient_generations()

        self._scheduler_map: dict[str, list[str]] = {}   # fetch_scheduler_map() で取得
        self._current_group_id: int | None = None        # 現在の記録先グループ
        self._send_queue_worker: "_SendQueueWorker | None" = None  # 送信キューワーカー
        self._cancel_worker: "_CancelItemsWorker | None" = None    # item キャンセルワーカー
        self._plan_item_ids: list[int] = []   # 実行中プランで発行された item_id（中止用）
        self._plan_sent_images: int = 0       # 実行中プランで送信した画像数（ステータス用）
        self._conn_worker: _ConnCheckWorker | None = None  # 接続確認ワーカー
        self._board_worker: _BoardListWorker | None = None # ボード一覧取得ワーカー
        self._hist_worker: _HistorySyncWorker | None = None # 履歴同期ワーカー
        self._translate_worker: _TranslateStreamWorker | None = None  # 翻訳ワーカー
        self._translate_target_bw = None   # 翻訳中のブロックウィジェット（直接参照）
        self._translate_lines: list[str] | None = None   # 複数行翻訳: 対象行リスト
        self._translate_results: list[str] = []          # 複数行翻訳: 収集済み結果
        self._translate_line_index: int = 0              # 複数行翻訳: 現在処理中の行番号
        self._translate_mode_cache: str = "danboard"     # 複数行翻訳: モード保存用
        self._translate_has_failure: bool = False        # 複数行翻訳: いずれかの行が失敗したか
        self._lm_prompt_window = None                           # フローティングプロンプト編集ウィンドウ
        self._lm_model_meta: dict[str, dict] = {}               # LM Studio モデル詳細
        self._selected_model_key: str = ""                 # 選択中モデルの Invoke キー
        self._selected_plan_id: int | None = None          # 選択中マルチモデルプラン
        self._current_base: str = "sdxl"                   # 選択中モデルのベース種別
        self._last_model_key_by_base: dict[str, str] = {}  # ベース別の直前選択モデル
        self._current_template_id: int | None = None       # 選択中テンプレートの id
        self._current_template_name: str = ""              # 選択中テンプレートの表示名
        self._negative_supported: bool = True              # 現在テンプレートがネガティブを実送信できるか
        self._boards: list[dict] = []                      # Invoke ボード一覧
        self._editor_dirty: bool = False                   # プロンプト編集済みフラグ
        self._history_full_drop_widgets: set[QWidget] = set()
        self._history_prompt_drop_widgets: set[QWidget] = set()
        self._history_map_dialog = None
        self._history_map_dialog_focus: tuple[str, int] | None = None
        self._history_map_opened_node: tuple[str, int] | None = None
        # 「ここ以下のみ表示」の起点（None=全体表示）
        self._history_map_view_root: tuple[str, int] | None = self._settings_node_key("history_map_view_root")
        self._generation_busy = False
        self._generation_progress: QProgressDialog | None = None

        # 左右ペインの表示状態
        self._left_visible  = True
        self._right_visible = True
        # QSplitter のサイズ記憶（折りたたみ前）
        self._left_size  = 240
        self._right_size = 300

        self._build_toolbar()
        self._build_params_bar()
        self._build_central()
        self._build_statusbar()
        self._restore_window_state()
        self._apply_app_icon()          # カスタムアイコンを適用
        self._restore_last_prompt()
        self._restore_model_selection() # 保存済みモデル選択を復元
        self._apply_negative_prompt_ui()
        self._restore_lora_state()      # 保存済みLoRA選択を復元
        self._restore_last_group()      # 保存済み生成先グループを復元
        self._ensure_current_group()
        self._update_generation_buttons()

        self._install_history_drop_targets()

        # PNG ドラッグ & ドロップを有効化
        self.setAcceptDrops(True)

        # 接続チェックタイマー（30秒ごと）
        self._conn_timer = QTimer(self)
        self._conn_timer.timeout.connect(self._check_connection)
        self._conn_timer.start(30_000)
        self._check_connection()
        QTimer.singleShot(900, self._check_external_inbox)
        QTimer.singleShot(1200, self._auto_open_invoke_setup_if_needed)

        # 生成完了待ちポーリング
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._on_poll_tick)
        self._pending_gen_ids: list[int] = []
        self._poll_count = 0

    # ── ツールバー ───────────────────────────────────────

    # ── 正方形ツールボタン共通ヘルパー ───────────────────
    @staticmethod
    def _square_btn(text: str, tooltip: str = "") -> QToolButton:
        """テキストを emoji 1文字にして正方形で表示するツールボタンを作成する"""
        btn = QToolButton()
        btn.setText(text)
        if tooltip:
            btn.setToolTip(tooltip)
        btn.setFixedSize(28, 28)
        # グローバル padding を上書きして絵文字が省略されないようにする。
        # 絵文字サイズはフォント設定に追従させず 12pt 固定（EMOJI_ICON_SS）
        btn.setStyleSheet(
            "QToolButton { padding: 0px; " + EMOJI_ICON_SS + " }"
            "QToolButton:hover { background-color: #3a3a4a; }"
            "QToolButton:pressed { background-color: #89b4fa; color: #1e1e2e; }"
        )
        return btn

    @staticmethod
    def _short_toolbar_label(label: str, fallback: str = "❌", max_chars: int = 6) -> str:
        label = str(label or "").strip()
        if not label:
            return fallback
        return label if len(label) <= max_chars else fallback

    @staticmethod
    def _set_toolbar_widget_visible(widget: QWidget | None, action, visible: bool) -> None:
        if widget is not None:
            widget.setVisible(visible)
        if action is not None:
            action.setVisible(visible)

    def _build_toolbar(self) -> None:
        """
        Row 1 — 生成アクション行
        ◀タグ | ▶即時生成  ⚡生成  Count | W  H  Seed  🎲  | 🔀 ──── Settings | ▶履歴
        """
        tb = QToolBar("メインツールバー")
        self._main_toolbar = tb
        tb.setMovable(False)
        tb.setIconSize(QSize(16, 16))
        self.addToolBar(tb)

        # ── 左ペイン トグル ───────────────────────────────
        self._btn_left = QToolButton()
        self._btn_left.setText("◀")
        self._btn_left.setStyleSheet("QToolButton { " + EMOJI_ICON_SS + " }")
        self._btn_left.setToolTip(tr("main.btn_left_tooltip"))
        self._btn_left.setCheckable(True)
        self._btn_left.setChecked(True)
        self._btn_left.clicked.connect(self._toggle_left)
        tb.addWidget(self._btn_left)

        tb.addSeparator()

        # ── recall / generate ────────────────────────────
        self._btn_recall = QToolButton()
        self._btn_recall.setText(tr("main.btn_recall"))
        self._btn_recall.setToolTip(tr("main.btn_recall_tooltip"))
        self._btn_recall.setStyleSheet(themed_button_style("accent"))
        self._btn_recall.clicked.connect(self._send_recall)
        tb.addWidget(self._btn_recall)

        self._btn_gen = QToolButton()
        self._btn_gen.setText(tr("main.btn_generate"))
        self._btn_gen.setToolTip(tr("main.btn_generate_tooltip"))
        self._btn_gen.setStyleSheet(themed_button_style("success"))
        self._btn_gen.clicked.connect(self._generate)
        tb.addWidget(self._btn_gen)

        # 生成プラン中止（送信中または未回収の生成がある間だけ有効）
        self._btn_cancel_plan = QToolButton()
        self._btn_cancel_plan.setText(self._short_toolbar_label(tr("main.btn_stop")))
        self._btn_cancel_plan.setToolTip(tr("main.btn_cancel_plan_tooltip"))
        self._btn_cancel_plan.clicked.connect(self._cancel_generation_plan)
        self._btn_cancel_plan.setEnabled(False)
        tb.addWidget(self._btn_cancel_plan)

        self._history_one_cb = QCheckBox(tr("main.history_one_checkbox"))
        self._history_one_cb.setToolTip(tr("main.history_one_tooltip"))
        self._history_one_cb.setChecked(_get_setting("gen_history_one", "0") == "1")
        self._history_one_cb.toggled.connect(
            lambda v: _set_setting("gen_history_one", "1" if v else "0")
        )
        tb.addWidget(self._history_one_cb)

        self._history_map_cb = QCheckBox(tr("main.history_map_checkbox"))
        self._history_map_cb.setToolTip(tr("main.history_map_record_tooltip"))
        self._history_map_cb.setChecked(_get_setting("gen_history_map", "1") == "1")
        self._history_map_cb.toggled.connect(
            lambda v: _set_setting("gen_history_map", "1" if v else "0")
        )
        tb.addWidget(self._history_map_cb)

        self._update_generation_buttons()

        # ── Count ────────────────────────────────────────
        self._count_label = QLabel("Count:")
        tb.addWidget(self._count_label)
        self._count_spin = QSpinBox()
        self._count_spin.setRange(1, 999)
        self._count_spin.setFixedWidth(76)
        self._count_spin.setToolTip(tr("main.count_tooltip"))
        try:
            self._count_spin.setValue(int(_get_setting("gen_count", "1")))
        except ValueError:
            self._count_spin.setValue(1)
        tb.addWidget(self._count_spin)

        tb.addSeparator()

        # ── Width ────────────────────────────────────────
        self._width_label = QLabel("W:")
        tb.addWidget(self._width_label)
        self._width_spin = QSpinBox()
        self._width_spin.setRange(64, 4096)
        self._width_spin.setSingleStep(64)
        self._width_spin.setFixedWidth(76)
        self._width_spin.setToolTip(tr("main.width_tooltip"))
        try:
            self._width_spin.setValue(int(_get_setting("gen_width", "1024")))
        except ValueError:
            self._width_spin.setValue(1024)
        self._width_spin.editingFinished.connect(
            lambda: self._snap_dimension(self._width_spin)
        )
        tb.addWidget(self._width_spin)

        # ── Height ───────────────────────────────────────
        self._height_label = QLabel("H:")
        tb.addWidget(self._height_label)
        self._height_spin = QSpinBox()
        self._height_spin.setRange(64, 4096)
        self._height_spin.setSingleStep(64)
        self._height_spin.setFixedWidth(76)
        self._height_spin.setToolTip(tr("main.height_tooltip"))
        try:
            self._height_spin.setValue(int(_get_setting("gen_height", "1024")))
        except ValueError:
            self._height_spin.setValue(1024)
        self._height_spin.editingFinished.connect(
            lambda: self._snap_dimension(self._height_spin)
        )
        tb.addWidget(self._height_spin)

        # ── Seed ─────────────────────────────────────────
        self._seed_icon = QLabel("🌱")
        self._seed_icon.setStyleSheet(EMOJI_ICON_SS)  # 絵文字は12pt固定
        self._seed_icon.setToolTip(tr("main.seed_icon_tooltip"))
        tb.addWidget(self._seed_icon)
        self._seed_random_cb = QCheckBox()
        self._seed_random_cb.setToolTip(tr("main.seed_random_tooltip"))
        tb.addWidget(self._seed_random_cb)
        self._seed_spin = QSpinBox()
        self._seed_spin.setRange(0, 2_147_483_647)
        self._seed_spin.setFixedWidth(85)
        self._seed_spin.setToolTip(tr("main.seed_value_tooltip"))
        tb.addWidget(self._seed_spin)
        self._seed_random_cb.toggled.connect(lambda v: self._seed_spin.setEnabled(not v))

        # シード固定トグル: 🔒のとき count 全枚を同じシードで生成（🔓=従来動作）。
        # ランダムCBと併用時は「生成回ごとに新しいシードを1つ採番し、その回の
        # 全枚に使う」。プロンプトのランダム要素でバリエーションを出しつつ
        # 構図を保つための機能
        self._seed_fixed_btn = self._square_btn("🔓", tr("main.seed_fixed_tooltip"))
        self._seed_fixed_btn.setCheckable(True)
        # checked 状態が一目で分かるようアクセント背景を追加
        self._seed_fixed_btn.setStyleSheet(
            self._seed_fixed_btn.styleSheet()
            + "QToolButton:checked { background-color: #89b4fa; color: #1e1e2e; }"
        )
        self._seed_fixed_btn.toggled.connect(
            lambda v: self._seed_fixed_btn.setText("🔒" if v else "🔓")
        )
        tb.addWidget(self._seed_fixed_btn)

        # 起動時の値を復元
        _saved_seed = _get_setting("gen_seed", "-1")
        if _saved_seed == "-1" or not _saved_seed:
            self._seed_random_cb.setChecked(True)
        else:
            try:
                v = int(_saved_seed)
                self._seed_spin.setValue(max(0, min(2_147_483_647, v)))
                self._seed_random_cb.setChecked(False)
            except ValueError:
                self._seed_random_cb.setChecked(True)
        self._seed_fixed_btn.setChecked(_get_setting("gen_seed_fixed", "0") == "1")

        self._btn_rand = self._square_btn("🎲", tr("main.seed_randomize_tooltip"))
        self._btn_rand.clicked.connect(self._randomize_seed)
        tb.addWidget(self._btn_rand)

        tb.addSeparator()

        # ── Shuffle（正方形）────────────────────────────
        self._btn_shuffle = self._square_btn("🔀", tr("main.btn_shuffle_tooltip"))
        self._btn_shuffle.clicked.connect(self._shuffle_and_preview)
        tb.addWidget(self._btn_shuffle)

        # ── スペーサー ────────────────────────────────────
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)

        # ── タイル表示モード（2段→上1段→下1段 の巡回）─────
        # ボタンの表示は「現在の状態」のミニチュア（押した結果ではない）。
        self._tile_display_btn = QToolButton()
        self._tile_display_btn.clicked.connect(self._on_tile_display_mode_clicked)
        self._update_tile_display_btn()
        tb.addWidget(self._tile_display_btn)

        # ── オールクリア ──────────────────────────────────
        self._btn_clear_all = self._square_btn("♻️", tr("main.btn_clear_all_tooltip"))
        self._btn_clear_all.clicked.connect(self._all_clear)
        tb.addWidget(self._btn_clear_all)

        # ── 設定（アイコンのみ） ───────────────────────────
        self._btn_settings = self._square_btn("⚙", tr("main.btn_settings_tooltip"))
        self._btn_settings.clicked.connect(self._open_settings)
        tb.addWidget(self._btn_settings)

        tb.addSeparator()

        # ── 右ペイン トグル ───────────────────────────────
        self._btn_right = QToolButton()
        self._btn_right.setText("▶")
        self._btn_right.setStyleSheet("QToolButton { " + EMOJI_ICON_SS + " }")
        self._btn_right.setToolTip(tr("main.btn_right_tooltip"))
        self._btn_right.setCheckable(True)
        self._btn_right.setChecked(True)
        self._btn_right.clicked.connect(self._toggle_right)
        tb.addWidget(self._btn_right)

    # ── 生成パラメータバー（ツールバー2段目/3段目） ───────

    def _build_params_bar(self) -> None:
        """
        Row 2 — Invoke 生成パラメータ行
        Model  🔄 | Steps  CFG | Sched | Board
        Row 3 — ローカルLLM行
        Trans LM  🔄  🧩
        """
        self.addToolBarBreak()
        pb = QToolBar("モデルパラメータ")
        self._params_toolbar = pb
        pb.setMovable(False)
        pb.setIconSize(QSize(14, 14))
        self.addToolBar(pb)

        # ── Model ─────────────────────────────────────────
        self._model_label = QLabel("Model:")
        pb.addWidget(self._model_label)
        self._model_base_combo = QComboBox()
        self._model_base_combo.setFixedWidth(110)
        self._model_base_combo.setToolTip(tr("main.model_base_tooltip"))
        self._model_base_combo.currentIndexChanged.connect(self._on_model_base_combo_changed)
        pb.addWidget(self._model_base_combo)

        self._model_combo = _WidePopupComboBox(min_width=172, max_width=172)
        self._model_combo.setFixedWidth(172)
        self._model_combo.setToolTip(tr("main.model_label_tooltip"))
        self._model_combo.setStyleSheet(
            f"QComboBox {{ color: {TEXT}; padding: 0 26px 0 4px; min-height: 20px; "
            f"border: 1px solid {SURFACE2}; border-radius: 3px; background: {SURFACE0}; }}"
            f"QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: top right; "
            f"width: 22px; border: none; background: transparent; }}"
            f"QComboBox::down-arrow {{ image: url(\"{COMBO_ARROW_URL}\"); width: 10px; height: 10px; }}"
        )
        self._model_combo.currentIndexChanged.connect(self._on_model_combo_changed)
        pb.addWidget(self._model_combo)

        self._btn_plan_edit = self._square_btn("📋", tr("main.plan_edit_tooltip"))
        self._btn_plan_edit.clicked.connect(self._open_generation_plan_dialog)
        self._btn_plan_edit_action = pb.addWidget(self._btn_plan_edit)

        self._btn_model_reload = self._square_btn("🔄", tr("main.model_reload_tooltip"))
        self._btn_model_reload.clicked.connect(self._refresh_models)
        self._btn_model_reload_action = pb.addWidget(self._btn_model_reload)
        self._populate_base_plan_combo()
        self._populate_model_combo()
        self._apply_model_mode_ui()

        pb.addSeparator()

        # ── Steps ─────────────────────────────────────────
        self._steps_label = QLabel("Steps:")
        pb.addWidget(self._steps_label)
        self._steps_spin = QSpinBox()
        self._steps_spin.setRange(1, 150)
        self._steps_spin.setFixedWidth(58)
        self._steps_spin.setToolTip(tr("main.steps_tooltip"))
        try:
            self._steps_spin.setValue(int(_get_setting("gen_steps", "30")))
        except ValueError:
            self._steps_spin.setValue(30)
        pb.addWidget(self._steps_spin)

        # ── CFG ───────────────────────────────────────────
        self._cfg_label = QLabel("CFG:")
        pb.addWidget(self._cfg_label)
        self._cfg_spin = QDoubleSpinBox()
        self._cfg_spin.setRange(1.0, 20.0)
        self._cfg_spin.setSingleStep(0.5)
        self._cfg_spin.setDecimals(1)
        self._cfg_spin.setFixedWidth(58)
        self._cfg_spin.setToolTip(tr("main.cfg_tooltip"))
        try:
            self._cfg_spin.setValue(float(_get_setting("gen_cfg", "7.0")))
        except ValueError:
            self._cfg_spin.setValue(7.0)
        pb.addWidget(self._cfg_spin)

        pb.addSeparator()

        # ── Scheduler ─────────────────────────────────────
        self._sched_label = QLabel("Sched:")
        pb.addWidget(self._sched_label)
        self._sched_combo = QComboBox()
        self._sched_combo.addItems([
            "euler", "euler_a", "dpm_2", "dpm_2_a", "heun",
            "ddim", "lms", "dpm++_2m", "dpm++_2m_sde", "dpm++_sde",
        ])
        self._sched_combo.setFixedWidth(115)
        self._sched_combo.setToolTip(tr("main.scheduler_tooltip"))
        saved_sched = _get_setting("gen_scheduler", "euler")
        idx = self._sched_combo.findText(saved_sched)
        if idx >= 0:
            self._sched_combo.setCurrentIndex(idx)
        pb.addWidget(self._sched_combo)

        pb.addSeparator()

        # ── Invoke 保存先ボード ─────────────────────────
        self._board_label = QLabel(tr("main.board_label"))
        pb.addWidget(self._board_label)
        self._board_combo = _WidePopupComboBox(min_width=180, max_width=360)
        self._board_combo.setToolTip(tr("main.board_tooltip"))
        self._board_combo.setStyleSheet(
            f"QComboBox {{ color: {TEXT}; padding: 0 26px 0 4px; min-height: 20px; "
            f"border: 1px solid {SURFACE2}; border-radius: 3px; background: {SURFACE0}; }}"
            f"QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: top right; "
            f"width: 22px; border: none; background: transparent; }}"
            f"QComboBox::down-arrow {{ image: url(\"{COMBO_ARROW_URL}\"); width: 10px; height: 10px; }}"
        )
        self._board_combo.currentIndexChanged.connect(self._on_board_combo_changed)
        pb.addWidget(self._board_combo)
        self._restore_board_selection()
        QTimer.singleShot(0, self._refresh_boards)

        self._btn_board_reload = self._square_btn("🔄", tr("main.board_reload_tooltip"))
        self._btn_board_reload.clicked.connect(self._refresh_boards)
        pb.addWidget(self._btn_board_reload)

        # ── ローカルLLM行 ─────────────────────────────────
        self.addToolBarBreak()
        lb = QToolBar("ローカルLLM")
        self._lm_toolbar = lb
        lb.setMovable(False)
        lb.setIconSize(QSize(14, 14))
        self.addToolBar(lb)

        self._lm_label = QLabel(tr("main.translate_lm_label"))
        lb.addWidget(self._lm_label)
        self._lm_model_combo = _WidePopupComboBox(min_width=360, max_width=10000, width_reserve=0)
        self._lm_model_combo.view().setUniformItemSizes(False)
        self._lm_model_combo.view().setWordWrap(True)
        self._lm_model_combo.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._lm_model_combo.setEditable(True)
        self._lm_model_combo.lineEdit().setAlignment(Qt.AlignmentFlag.AlignLeft)
        self._lm_model_combo.lineEdit().setReadOnly(True)
        self._lm_model_combo.lineEdit().setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self._lm_model_combo.setToolTip(tr("main.translate_lm_tooltip"))
        # 保存済みモデルをまず復元（同期前でも表示されるように）
        _saved_lm = _get_setting("lm_translate_model", "")
        if _saved_lm:
            self._lm_model_combo.addItem(_saved_lm)
            self._lm_model_combo.setCurrentText(_saved_lm)
        self._lm_model_combo.currentTextChanged.connect(self._save_lm_model_selection)
        self._lm_model_combo.currentIndexChanged.connect(self._on_lm_model_changed)
        self._lm_model_combo.currentTextChanged.connect(self._queue_lm_combo_display_sync)
        self._lm_model_combo.activated.connect(lambda *_: self._queue_lm_combo_display_sync())
        lb.addWidget(self._lm_model_combo)

        self._btn_lm_reload = self._square_btn("🔄", tr("main.translate_lm_reload_tooltip"))
        self._btn_lm_reload.clicked.connect(self._refresh_lm_models)
        lb.addWidget(self._btn_lm_reload)

        self._lm_prompt_btn = self._square_btn("🧩", tr("main.translate_prompt_tooltip"))
        self._lm_prompt_btn.clicked.connect(self._toggle_lm_prompt_editor)
        lb.addWidget(self._lm_prompt_btn)

        self._lm_note_edit = QLineEdit()
        self._lm_note_edit.setFixedWidth(360)
        self._lm_note_edit.setPlaceholderText(tr("main.translate_lm_note_placeholder"))
        self._lm_note_edit.setToolTip(tr("main.translate_lm_note_tooltip"))
        self._lm_note_edit.setStyleSheet(
            f"QLineEdit {{ color: {TEXT}; padding: 0 4px; "
            f"border: 1px solid {SURFACE2}; border-radius: 3px; background: {SURFACE0}; }}"
        )
        self._lm_note_edit.textEdited.connect(self._save_lm_model_note)
        lb.addWidget(self._lm_note_edit)
        self._load_lm_model_note()

    @staticmethod
    def _snap_dimension(spin: "QSpinBox", multiple: int = 8) -> None:
        """幅・高さスピンボックスの値を multiple の倍数（最近傍）に補正する。
        Invoke は幅・高さが 8 の倍数でないと 422 エラーを返す。
        """
        v = spin.value()
        snapped = max(spin.minimum(), min(spin.maximum(), round(v / multiple) * multiple))
        if v != snapped:
            spin.setValue(snapped)

    def _get_gen_params(self) -> dict:
        """
        パラメータバーの現在値を dict で返す。

        base_seed < 0 のとき呼び出し元がランダムシードを生成する。
        base_seed >= 0 のとき呼び出し元が base_seed, base_seed+1, ... とインクリメントする。
        seed_fixed=True かつ base_seed >= 0 のときは全枚 base_seed のまま固定する。
        """
        if self._seed_random_cb.isChecked():
            base_seed = -1
        else:
            base_seed = self._seed_spin.value()

        # ベース別 CFG ポリシー（入力境界での確定）: flux2 等は CFG を 1.0 に固定する。
        # 履歴呼び出し等で spin に別値が入っても、ここで必ずロック値に揃える。
        from core.gen_params import cfg_is_locked, LOCKED_CFG_VALUE
        cfg_value = (
            LOCKED_CFG_VALUE if cfg_is_locked(self._current_base)
            else self._cfg_spin.value()
        )

        return {
            "base_seed": base_seed,
            "seed_fixed": self._seed_fixed_btn.isChecked(),
            "steps":     self._steps_spin.value(),
            "cfg_scale": cfg_value,
            "scheduler": self._sched_combo.currentText(),
            "width":     round(self._width_spin.value()  / 8) * 8,
            "height":    round(self._height_spin.value() / 8) * 8,
            "count":     self._count_spin.value(),
            "model_key":  self._selected_model_key,
            "model_base": self._current_base,
            "template_id": self._current_template_id,
            "loras":      self._lora_bar.get_loras(),
            "board_id":    self._current_board_id(),
        }

    def _has_generation_template(self) -> bool:
        row = _env_db.fetchone("SELECT 1 FROM templates LIMIT 1")
        return row is not None

    def _current_base_has_template(self) -> bool:
        """生成可能か: 現在のモデルのベースにテンプレートがあるか。
        プラン時は行ごとに解決するため全体の有無で判定。"""
        if self._is_plan_mode():
            return self._has_generation_template()
        base = self._current_base
        if not base:
            return self._has_generation_template()
        row = _env_db.fetchone("SELECT 1 FROM templates WHERE base=? LIMIT 1", (base,))
        return row is not None

    def _has_prompt_for_generation(self) -> bool:
        if not hasattr(self, "_editor"):
            return False
        try:
            return bool(self._editor.compile_positive().strip())
        except Exception:
            return False

    def _set_generation_busy(self, busy: bool) -> None:
        self._generation_busy = busy
        self._update_generation_buttons()

    def _update_generation_buttons(self) -> None:
        if not hasattr(self, "_btn_gen"):
            return
        buttons = [self._btn_gen]
        if hasattr(self, "_btn_recall"):
            buttons.append(self._btn_recall)
        option_widgets = [
            w for w in (
                getattr(self, "_history_one_cb", None),
                getattr(self, "_history_map_cb", None),
            )
            if w is not None
        ]

        if self._generation_busy:
            for btn in buttons:
                btn.setEnabled(False)
            for w in option_widgets:
                w.setEnabled(False)
            return

        if not self._current_base_has_template():
            for btn in buttons:
                btn.setEnabled(False)
                btn.setToolTip(tr("main.generate_disabled_no_template"))
            for w in option_widgets:
                w.setEnabled(False)
            return

        if not self._has_prompt_for_generation():
            for btn in buttons:
                btn.setEnabled(False)
                btn.setToolTip(tr("main.generate_disabled_empty_prompt"))
            for w in option_widgets:
                w.setEnabled(False)
            return

        if not self._is_plan_mode() and not self._selected_model_key:
            for btn in buttons:
                btn.setEnabled(False)
                btn.setToolTip(tr("main.generate_disabled_no_model"))
            for w in option_widgets:
                w.setEnabled(False)
            return

        if self._is_plan_mode() and self._selected_plan_id is None:
            for btn in buttons:
                btn.setEnabled(False)
                btn.setToolTip(tr("main.generate_disabled_no_plan"))
            for w in option_widgets:
                w.setEnabled(False)
            return

        self._btn_gen.setEnabled(True)
        self._btn_gen.setToolTip(tr("main.btn_generate_tooltip"))
        if hasattr(self, "_btn_recall"):
            self._btn_recall.setEnabled(True)
            self._btn_recall.setToolTip(tr("main.btn_recall_tooltip"))
        for w in option_widgets:
            w.setEnabled(True)

    def _randomize_seed(self) -> None:
        self._seed_spin.setValue(random.randint(0, 2_147_483_647))
        self._seed_random_cb.setChecked(False)

    def _populate_base_plan_combo(self) -> None:
        if not hasattr(self, "_model_base_combo"):
            return
        current_data = self._model_base_combo.currentData() if hasattr(self, "_model_base_combo") else None
        had_base_options = any(
            self._model_base_combo.itemData(i) != "__plan__"
            for i in range(self._model_base_combo.count())
        )
        keep_plan = current_data == "__plan__" and had_base_options
        current = self._current_base or ""
        rows = _env_db.fetchall(
            "SELECT DISTINCT base FROM models "
            "WHERE type='main' AND available=1 AND COALESCE(base,'')!='sdxl-refiner' "
            "ORDER BY base"
        )
        bases = [r["base"] or "sdxl" for r in rows]
        self._model_base_combo.blockSignals(True)
        self._model_base_combo.clear()
        for base in bases:
            self._model_base_combo.addItem(_base_label(base), base)
        self._model_base_combo.addItem(tr("main.model_mode_plan"), "__plan__")
        if keep_plan:
            self._model_base_combo.setCurrentIndex(self._model_base_combo.findData("__plan__"))
        elif current in bases:
            self._model_base_combo.setCurrentIndex(self._model_base_combo.findData(current))
        elif bases:
            self._current_base = bases[0]
            self._model_base_combo.setCurrentIndex(0)
        self._model_base_combo.blockSignals(False)

    def _populate_model_combo(self) -> None:
        if not hasattr(self, "_model_combo"):
            return
        if self._is_plan_mode():
            import db.generation_plan_db as _plan_db
            current_id = self._selected_plan_id
            self._model_combo.blockSignals(True)
            self._model_combo.clear()
            for plan in _plan_db.list_plans():
                self._model_combo.addItem(str(plan["name"]), {
                    "kind": "plan",
                    "plan_id": int(plan["id"]),
                })
            if self._model_combo.count() == 0:
                self._model_combo.addItem(tr("main.plan_none_label"), None)
            idx = -1
            if current_id is not None:
                for i in range(self._model_combo.count()):
                    data = self._model_combo.itemData(i)
                    if isinstance(data, dict) and data.get("plan_id") == current_id:
                        idx = i
                        break
            if idx < 0:
                idx = 0
            self._model_combo.setCurrentIndex(idx)
            data = self._model_combo.itemData(idx)
            self._selected_plan_id = (
                int(data.get("plan_id")) if isinstance(data, dict) and data.get("kind") == "plan" else None
            )
            self._selected_model_key = ""
            self._model_combo.blockSignals(False)
            self._model_combo.setToolTip(tr("main.plan_combo_tooltip"))
            self._model_combo.adjust_to_current_text()
            return

        current_key = self._selected_model_key
        base_filter = self._current_base or ""
        rows = _env_db.fetchall(
            "SELECT invoke_key, name, base, variant FROM models "
            "WHERE type='main' AND COALESCE(base,'') != 'sdxl-refiner' "
            "  AND available=1 AND (?='' OR base=?) "
            "ORDER BY name",
            (base_filter, base_filter),
        )
        self._model_combo.blockSignals(True)
        self._model_combo.clear()
        self._model_combo.addItem(tr("plan.model_placeholder"), "")
        for row in rows:
            name = row["name"] or row["invoke_key"]
            base = row["base"] or "sdxl"
            self._model_combo.addItem(name, {
                "invoke_key": row["invoke_key"],
                "name": name,
                "base": base,
                "variant": row["variant"] or "",
            })
        if current_key:
            for i in range(self._model_combo.count()):
                data = self._model_combo.itemData(i)
                if isinstance(data, dict) and data.get("invoke_key") == current_key:
                    self._model_combo.setCurrentIndex(i)
                    break
        self._model_combo.blockSignals(False)
        self._model_combo.setToolTip(tr("main.model_label_tooltip"))
        self._model_combo.adjust_to_current_text()

    def _populate_plan_combo(self) -> None:
        if self._is_plan_mode():
            self._populate_model_combo()

    def _model_key_base(self, model_key: str) -> str:
        if not model_key:
            return ""
        row = _env_db.fetchone("SELECT base FROM models WHERE invoke_key=?", (model_key,))
        return (row["base"] or "") if row else ""

    def _remember_selected_model_for_base(self) -> None:
        key = self._selected_model_key
        if not key:
            return
        base = self._model_key_base(key) or self._current_base
        if base:
            self._last_model_key_by_base[base] = key

    def _restore_selected_model_for_base(self, base: str) -> None:
        key = self._last_model_key_by_base.get(base, "")
        if not key:
            if self._model_key_base(self._selected_model_key) == base:
                return
            self._selected_model_key = ""
            return
        row = _env_db.fetchone(
            "SELECT invoke_key FROM models WHERE invoke_key=? AND type='main' AND available=1",
            (key,),
        )
        self._selected_model_key = key if row else ""

    def _is_plan_mode(self) -> bool:
        return (
            hasattr(self, "_model_base_combo")
            and self._model_base_combo.currentData() == "__plan__"
        )

    def _on_model_base_combo_changed(self, _index: int) -> None:
        self._remember_selected_model_for_base()
        if self._is_plan_mode():
            self._selected_model_key = ""
            if hasattr(self, "_model_browser"):
                self._model_browser.set_current_model_key("")
            self._populate_model_combo()
            if hasattr(self, "_seed_fixed_btn"):
                self._seed_fixed_btn.setChecked(True)
            if hasattr(self, "_count_spin"):
                self._count_spin.setValue(1)
        else:
            data = self._model_base_combo.currentData()
            if isinstance(data, str) and data:
                self._current_base = data
                self._restore_selected_model_for_base(data)
                self._populate_model_combo()
                self._apply_base_ui(data)
                if hasattr(self, "_lora_bar"):
                    self._filter_incompatible_loras(data)
        self._apply_model_mode_ui()
        self._update_generation_buttons()

    def _apply_model_mode_ui(self) -> None:
        if not hasattr(self, "_model_combo"):
            return
        plan_mode = self._is_plan_mode()
        self._model_combo.setVisible(True)
        self._set_toolbar_widget_visible(
            getattr(self, "_btn_plan_edit", None),
            getattr(self, "_btn_plan_edit_action", None),
            plan_mode,
        )
        self._set_toolbar_widget_visible(
            getattr(self, "_btn_model_reload", None),
            getattr(self, "_btn_model_reload_action", None),
            not plan_mode,
        )

    def _clear_current_model_selection(self) -> None:
        self._selected_model_key = ""
        self._current_template_id = None
        self._current_template_name = ""
        if hasattr(self, "_model_browser"):
            self._model_browser.set_current_model_key("")
        if hasattr(self, "_model_combo"):
            self._model_combo.blockSignals(True)
            self._model_combo.setCurrentIndex(0 if self._model_combo.count() else -1)
            self._model_combo.blockSignals(False)

    def _current_plan_defaults(self) -> dict:
        return {
            "model_key": self._selected_model_key,
            "steps": self._steps_spin.value() if hasattr(self, "_steps_spin") else 30,
            "cfg_scale": self._cfg_spin.value() if hasattr(self, "_cfg_spin") else 7.0,
            "scheduler": self._sched_combo.currentText() if hasattr(self, "_sched_combo") else "euler",
            "loras": self._lora_bar.get_loras() if hasattr(self, "_lora_bar") else [],
        }

    def _open_generation_plan_dialog(self) -> None:
        dlg = GenerationPlanDialog(self, defaults=self._current_plan_defaults())
        if self._selected_plan_id is not None:
            dlg.editor.refresh_plans(self._selected_plan_id)
        dlg.editor.plans_changed.connect(self._populate_plan_combo)
        dlg.plan_selected.connect(
            lambda pid: (setattr(self, "_selected_plan_id", int(pid) if pid else None), self._populate_plan_combo())
        )
        dlg.exec()
        self._populate_plan_combo()

    def _current_board_id(self) -> str | None:
        if not hasattr(self, "_board_combo"):
            return None
        data = self._board_combo.currentData()
        return str(data) if data else None

    def _restore_board_selection(self) -> None:
        """起動時は保存済みボードだけ復元し、Invoke への一覧取得は行わない。"""
        if not hasattr(self, "_board_combo"):
            return
        saved_board_id = _get_setting("selected_board_id", "")
        self._board_combo.blockSignals(True)
        self._board_combo.clear()
        self._board_combo.addItem(tr("main.board_template_default"), "")
        if saved_board_id:
            label = tr("main.board_saved_placeholder", id=saved_board_id[:8])
            self._board_combo.addItem(label, saved_board_id)
            self._board_combo.setCurrentIndex(1)
        self._board_combo.blockSignals(False)
        self._board_combo.adjust_to_current_text()

    def _refresh_boards(self) -> None:
        """Invoke のボード一覧を非同期取得し、保存先コンボに反映する。"""
        if not hasattr(self, "_board_combo"):
            return
        if self._board_worker is not None and self._board_worker.isRunning():
            return
        self._board_worker = _BoardListWorker(self._client, parent=self)
        self._board_worker.loaded.connect(self._on_boards_loaded)
        self._board_worker.failed.connect(self._on_boards_load_failed)
        self._board_worker.finished.connect(self._on_boards_worker_finished)
        self._board_worker.start()

    def _on_boards_loaded(self, boards: list) -> None:
        self._apply_board_list(boards)

    def _on_boards_load_failed(self, msg: str) -> None:
        self._show_status(tr("main.board_load_fail", error=msg), error=True)

    def _on_boards_worker_finished(self) -> None:
        self._board_worker = None

    def _apply_board_list(self, boards: list[dict]) -> None:
        """取得済みボード一覧を保存先コンボに反映する。"""
        if not hasattr(self, "_board_combo"):
            return
        self._boards = boards
        saved_board_id = _get_setting("selected_board_id", "")
        current_board_id = self._current_board_id() or saved_board_id

        self._board_combo.blockSignals(True)
        self._board_combo.clear()
        self._board_combo.addItem(tr("main.board_template_default"), "")
        selected_idx = 0
        for board in boards:
            board_id = board.get("board_id") or ""
            if not board_id:
                continue
            name = board.get("board_name") or board_id[:8]
            count = board.get("image_count")
            label = (
                f"{name} ({count})"
                if isinstance(count, int)
                else str(name)
            )
            self._board_combo.addItem(label, board_id)
            if board_id == current_board_id:
                selected_idx = self._board_combo.count() - 1
        self._board_combo.setCurrentIndex(selected_idx)
        self._board_combo.blockSignals(False)
        self._board_combo.adjust_to_current_text()

    def _on_board_combo_changed(self, _index: int) -> None:
        board_id = self._current_board_id() or ""
        _app_db.execute(
            "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
            ("selected_board_id", board_id),
        )

    def _on_model_combo_changed(self, _index: int) -> None:
        data = self._model_combo.currentData()
        if self._is_plan_mode():
            self._selected_plan_id = (
                int(data.get("plan_id")) if isinstance(data, dict) and data.get("kind") == "plan" else None
            )
            self._update_generation_buttons()
            return
        if not isinstance(data, dict):
            self._clear_current_model_selection()
            self._update_generation_buttons()
            return
        key = data.get("invoke_key") or ""
        if not key or key == self._selected_model_key:
            return
        self._on_model_chosen(
            key,
            data.get("name") or key,
            data.get("base") or "sdxl",
            data.get("variant") or "",
        )

    def _revert_model_combo(self) -> None:
        """モデルコンボを現在の _selected_model_key の項目に戻す（信号抑止）。
        テンプレ選択がキャンセル/未取得でモデル切替を中断した時に呼ぶ。"""
        if not hasattr(self, "_model_combo"):
            return
        key = self._selected_model_key
        self._model_combo.blockSignals(True)
        try:
            target = -1
            for i in range(self._model_combo.count()):
                data = self._model_combo.itemData(i)
                if isinstance(data, dict) and data.get("invoke_key") == key:
                    target = i
                    break
            if target >= 0:
                self._model_combo.setCurrentIndex(target)
        finally:
            self._model_combo.blockSignals(False)

    def _set_left_mode(self, mode: str, *, animate: bool = True) -> None:
        if not hasattr(self, "_left_stack"):
            return
        index_map = {"materials": 0, "models": 1, "loras": 2}
        idx = index_map.get(mode, 0)
        if self._left_stack.currentIndex() != idx:
            self._left_stack.setCurrentIndex(idx)
        for key, btn in self._left_mode_buttons.items():
            btn.blockSignals(True)
            btn.setChecked(key == mode)
            btn.blockSignals(False)
        self._apply_left_mode_button_style()
        if mode == "models" and hasattr(self, "_model_browser"):
            self._model_browser.set_current_model_key(self._selected_model_key)
            self._model_browser.reveal_current_model()

    def _apply_left_mode_button_style(self) -> None:
        if not hasattr(self, "_left_mode_buttons"):
            return
        base_style = (
            "QPushButton {{ background: {bg}; color: {fg}; "
            "border: 1px solid {border}; border-radius: 4px; padding: 4px 6px; }}"
            "QPushButton:hover {{ border-color: {hover}; color: {hover}; }}"
        )
        for btn in self._left_mode_buttons.values():
            if btn.isChecked():
                btn.setStyleSheet(base_style.format(bg=SURFACE1, fg=ACCENT, border=ACCENT, hover=ACCENT))
            else:
                btn.setStyleSheet(base_style.format(bg=SURFACE0, fg=SUBTEXT, border=SURFACE2, hover=TEXT))

    def _update_left_pane_texts(self) -> None:
        if not hasattr(self, "_left_mode_buttons"):
            return
        self._left_mode_buttons["materials"].setText(tr("main.left_mode_prompt"))
        self._left_mode_buttons["models"].setText(tr("main.left_mode_models"))
        self._left_mode_buttons["loras"].setText(tr("main.left_mode_loras"))
        self._prompt_mode_label.setText(tr("main.left_prompt_header"))
        self._prompt_search_edit.setPlaceholderText(tr("main.prompt_global_search_placeholder"))
        self._prompt_search_clear_btn.setToolTip(tr("model_browser.search_clear_tooltip"))
        self._material_tabs.setTabText(0, tr("main.prompt_tab_tags"))
        self._material_tabs.setTabText(1, tr("main.prompt_tab_groups"))
        self._material_tabs.setTabText(2, tr("main.prompt_tab_texts"))
        for i in range(self._material_tabs.count()):
            self._material_tabs.setTabToolTip(i, "")
        self._update_prompt_search_count_label()
        self._apply_left_mode_button_style()

    def _on_prompt_global_search_changed(self, text: str) -> None:
        self._prompt_search_clear_btn.setVisible(bool(text))
        for browser in (self._tag_browser, self._group_preset_browser, self._prompt_text_browser):
            if hasattr(browser, "set_search_query"):
                browser.set_search_query(text)
        self._update_prompt_search_count_label()

    def _update_prompt_search_count_label(self) -> None:
        if not hasattr(self, "_prompt_search_count_label"):
            return
        text = self._prompt_search_edit.text().strip() if hasattr(self, "_prompt_search_edit") else ""
        if not text:
            self._prompt_search_count_label.setText(tr("main.prompt_global_search_counts_empty"))
            return
        tags = self._tag_browser.visible_count() if hasattr(self._tag_browser, "visible_count") else 0
        groups = self._group_preset_browser.visible_count() if hasattr(self._group_preset_browser, "visible_count") else 0
        texts = self._prompt_text_browser.visible_count() if hasattr(self._prompt_text_browser, "visible_count") else 0
        self._prompt_search_count_label.setText(
            tr("main.prompt_global_search_counts", tags=tags, groups=groups, texts=texts)
        )

    # ── 中央ウィジェット（スプリッター） ──────────────────

    def _build_central(self) -> None:
        # ── LoRAチップバー + スプリッターをまとめるコンテナ ──────────────
        container = QWidget()
        container_lay = QVBoxLayout(container)
        container_lay.setContentsMargins(0, 0, 0, 0)
        container_lay.setSpacing(0)

        # LoRAチップバー（Row2直下）
        self._lora_bar = LoRAChipBar()
        self._lora_bar.changed.connect(self._on_lora_bar_changed)
        self._lora_bar.add_requested.connect(self._focus_lora_tab)
        self._lora_bar.lora_enabled_changed.connect(self._on_lora_tile_enable_changed)
        self._lora_bar.lora_removed.connect(self._on_lora_tile_removed)
        self._lora_bar.history_lora_dropped.connect(self._on_history_lora_dropped)
        container_lay.addWidget(self._lora_bar)

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setHandleWidth(3)
        container_lay.addWidget(self._splitter, stretch=1)

        self.setCentralWidget(container)

        # ── 左ペイン: 上位モード（プロンプト / モデル / LoRA） ─────────────
        self._left_panel = QWidget()
        self._left_panel.setMinimumWidth(170)
        left_lay = QVBoxLayout(self._left_panel)
        left_lay.setContentsMargins(3, 3, 3, 3)
        left_lay.setSpacing(4)

        self._left_mode_group = QButtonGroup(self)
        self._left_mode_group.setExclusive(True)
        self._left_mode_buttons: dict[str, QPushButton] = {}
        mode_row = QHBoxLayout()
        mode_row.setSpacing(3)
        for key, label_key in (
            ("materials", "main.left_mode_prompt"),
            ("models", "main.left_mode_models"),
            ("loras", "main.left_mode_loras"),
        ):
            btn = QPushButton(tr(label_key))
            btn.setCheckable(True)
            btn.setMinimumHeight(28)
            btn.clicked.connect(lambda _checked=False, k=key: self._set_left_mode(k))
            self._left_mode_group.addButton(btn)
            self._left_mode_buttons[key] = btn
            mode_row.addWidget(btn)
        left_lay.addLayout(mode_row)

        self._left_stack = QStackedWidget()
        left_lay.addWidget(self._left_stack, stretch=1)

        self._prompt_page = QWidget()
        prompt_lay = QVBoxLayout(self._prompt_page)
        prompt_lay.setContentsMargins(0, 0, 0, 0)
        prompt_lay.setSpacing(4)

        prompt_header_row = QHBoxLayout()
        prompt_header_row.setContentsMargins(0, 0, 0, 0)
        prompt_header_row.setSpacing(4)

        self._prompt_mode_label = QLabel(tr("main.left_prompt_header"))
        self._prompt_mode_label.setFont(ui_font(bold=True))
        self._prompt_mode_label.setStyleSheet(f"color: {ACCENT}; padding: 2px 4px;")
        prompt_header_row.addWidget(self._prompt_mode_label, stretch=1)

        prompt_lay.addLayout(prompt_header_row)

        prompt_search_row = QHBoxLayout()
        prompt_search_row.setSpacing(2)
        self._prompt_search_edit = QLineEdit()
        self._prompt_search_edit.setPlaceholderText(tr("main.prompt_global_search_placeholder"))
        self._prompt_search_edit.setFixedHeight(24)
        self._prompt_search_edit.setFont(ui_font(-1))
        self._prompt_search_edit.setStyleSheet(
            f"QLineEdit {{ background: {SURFACE0}; color: {TEXT}; "
            f"border: 1px solid {SURFACE2}; border-radius: 3px; padding: 0 4px; }}"
        )
        self._prompt_search_edit.textChanged.connect(self._on_prompt_global_search_changed)
        prompt_search_row.addWidget(self._prompt_search_edit)

        self._prompt_search_clear_btn = QToolButton()
        self._prompt_search_clear_btn.setText("×")
        self._prompt_search_clear_btn.setFixedSize(24, 24)
        self._prompt_search_clear_btn.setToolTip(tr("model_browser.search_clear_tooltip"))
        self._prompt_search_clear_btn.setVisible(False)
        self._prompt_search_clear_btn.clicked.connect(self._prompt_search_edit.clear)
        prompt_search_row.addWidget(self._prompt_search_clear_btn)
        prompt_lay.addLayout(prompt_search_row)

        self._prompt_search_count_label = QLabel()
        self._prompt_search_count_label.setFont(ui_font(-2))
        self._prompt_search_count_label.setStyleSheet(f"color: {SUBTEXT}; padding: 0 4px;")
        prompt_lay.addWidget(self._prompt_search_count_label)

        self._material_tabs = QTabWidget()
        self._material_tabs.setTabPosition(QTabWidget.TabPosition.North)
        self._material_tabs.tabBar().setExpanding(True)
        self._material_tabs.tabBar().setStyleSheet(
            "QTabBar::tab { min-width: 42px; padding: 4px 6px; font-size: 10pt; }"
        )

        self._tag_browser = TagBrowser(show_presets=False, show_header_icon=False)
        self._tag_browser.tag_selected.connect(self._on_tag_selected)
        self._tag_browser.tag_updated.connect(self._on_tag_updated)
        self._tag_browser.tag_categories_changed.connect(self._on_tag_categories_changed)
        self._material_tabs.addTab(self._tag_browser, tr("main.prompt_tab_tags"))
        self._material_tabs.setTabToolTip(0, "")

        self._group_preset_browser = GroupPresetBrowser()
        self._group_preset_browser.group_double_clicked.connect(self._on_group_preset_add_to_center)
        self._material_tabs.addTab(self._group_preset_browser, tr("main.prompt_tab_groups"))
        self._material_tabs.setTabToolTip(1, "")

        self._prompt_text_browser = PromptTextBrowser()
        self._prompt_text_browser.item_double_clicked.connect(self._on_prompt_text_add_to_center)
        self._material_tabs.addTab(self._prompt_text_browser, tr("main.prompt_tab_texts"))
        self._material_tabs.setTabToolTip(2, "")

        prompt_lay.addWidget(self._material_tabs, stretch=1)
        self._left_stack.addWidget(self._prompt_page)
        self._update_prompt_search_count_label()

        self._model_browser = ModelBrowser(client=self._client)
        self._model_browser.model_chosen.connect(self._on_model_chosen)
        self._left_stack.addWidget(self._model_browser)

        self._lora_browser = LoRABrowser(client=self._client)
        self._lora_browser.lora_toggled.connect(self._on_lora_toggled)
        self._left_stack.addWidget(self._lora_browser)
        self._set_left_mode("materials", animate=False)

        self._splitter.addWidget(self._left_panel)

        # ── 中央ペイン ────────────────────────────────────────────────────
        self._editor = PromptEditor()
        self._editor.setMinimumWidth(400)
        self._editor.prompt_changed.connect(self._on_prompt_changed)
        self._editor.materials_changed.connect(self._refresh_material_browsers)
        self._editor.translate_requested.connect(self._on_translate_requested)
        self._editor.translate_cancelled.connect(self._on_translate_cancel)
        self._editor.history_map_requested.connect(self._open_history_map)
        self._editor.history_stack_requested.connect(
            self._jump_to_history_stack
        )
        self._editor.history_stack_clear_requested.connect(self._clear_history_stack)
        self._editor.lineage_jump_requested.connect(self._on_lineage_jump_to_parent)
        self._editor.lineage_become_root_requested.connect(self._on_lineage_become_root)
        self._editor.lineage_heir_prev_requested.connect(lambda: self._shift_heir(-1))
        self._editor.lineage_heir_next_requested.connect(lambda: self._shift_heir(+1))
        parent_child_map = getattr(self._editor, "parent_child_map", None)
        if parent_child_map is not None:
            parent_child_map.node_clicked.connect(
                lambda db, gid: self._on_history_map_node_clicked(
                    db, gid, source="central"
                )
            )
            parent_child_map.jump_requested.connect(
                lambda db, gid: self._jump_to_editor_history_node(
                    db, gid, from_map=True, source="central"
                )
            )
            parent_child_map.preview_requested.connect(self._show_history_map_node_preview)
            parent_child_map.edit_requested.connect(self._edit_history_map_node)
            parent_child_map.stack_requested.connect(self._push_history_stack)
            parent_child_map.color_requested.connect(self._change_history_background_color)
            parent_child_map.text_color_requested.connect(self._change_history_text_color)
            parent_child_map.line_color_requested.connect(self._change_history_line_color)
            parent_child_map.show_subtree_requested.connect(self._show_history_map_subtree)
            parent_child_map.show_full_requested.connect(self._show_history_map_full)
            parent_child_map.detach_requested.connect(self._detach_editor_history_subtree)
            parent_child_map.erase_requested.connect(self._erase_editor_history_node)
            parent_child_map.delete_requested.connect(self._delete_editor_history_single)
            parent_child_map.bulk_erase_requested.connect(self._bulk_erase_editor_history_nodes)
            parent_child_map.bulk_delete_requested.connect(self._bulk_delete_editor_history_nodes)
            parent_child_map.reparent_requested.connect(self._reparent_editor_history_node)
        QTimer.singleShot(0, self._refresh_lineage_card)
        QTimer.singleShot(0, self._refresh_history_stack_buttons)
        # 前回履歴マップを開いたまま終了していたら再現する
        QTimer.singleShot(0, self._restore_history_map_state)
        QTimer.singleShot(0, self._restore_center_history_map_view_state)
        self._splitter.addWidget(self._editor)

        # ── 右ペイン ──────────────────────────────────────────────────────
        self._side_panel = SidePanel(client=self._client)
        self._side_panel.setMinimumWidth(200)
        self._side_panel.load_generation_requested.connect(self._load_generation)
        self._side_panel.full_load_generation_requested.connect(self._full_load_generation)
        self._side_panel.sync_history_requested.connect(self._sync_history)
        self._side_panel.group_focus_changed.connect(self._on_group_focus_changed)
        self._side_panel.history_tile_mode_changed.connect(self._on_history_tile_mode_changed)
        self._side_panel.history_tile_generation_changed.connect(self._on_history_tile_generation_changed)
        self._side_panel.history_map_requested.connect(self._open_history_map)
        self._side_panel.history_reconnect_requested.connect(self._open_adopt_history_dialog)
        self._side_panel.history_changed.connect(self._on_history_rows_changed)
        self._splitter.addWidget(self._side_panel)

        # 初期サイズ比
        self._splitter.setSizes([self._left_size, 860, self._right_size])
        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.setStretchFactor(2, 0)

    # ── ステータスバー ───────────────────────────────────

    def _build_statusbar(self) -> None:
        sb = self.statusBar()

        self._status_conn = QLabel(tr("main.status_checking"))
        self._status_conn.setFont(QFont("Segoe UI", 9))
        sb.addWidget(self._status_conn)

        sb.addPermanentWidget(QLabel("  "))

        self._status_queue = QLabel("")
        self._status_queue.setFont(QFont("Segoe UI", 9))
        sb.addPermanentWidget(self._status_queue)

        from core.version import APP_VERSION
        _ver_lbl = QLabel(f"v{APP_VERSION}")
        _ver_lbl.setFont(QFont("Segoe UI", 8))
        _ver_lbl.setStyleSheet(f"color: {SUBTEXT}; padding: 0 6px;")
        sb.addPermanentWidget(_ver_lbl)

    # ── トグル ───────────────────────────────────────────

    def _toggle_left(self) -> None:
        sizes = self._splitter.sizes()
        if self._left_visible:
            self._left_size = sizes[0] or self._left_size
            self._splitter.setSizes([0, sizes[1] + sizes[0], sizes[2]])
        else:
            self._splitter.setSizes([self._left_size, sizes[1] - self._left_size, sizes[2]])
        self._left_visible = not self._left_visible
        self._btn_left.setChecked(self._left_visible)

    def _toggle_right(self) -> None:
        sizes = self._splitter.sizes()
        if self._right_visible:
            self._right_size = sizes[2] or self._right_size
            self._splitter.setSizes([sizes[0], sizes[1] + sizes[2], 0])
        else:
            self._splitter.setSizes([sizes[0], sizes[1] - self._right_size, self._right_size])
        self._right_visible = not self._right_visible
        self._btn_right.setChecked(self._right_visible)

    def _on_history_tile_mode_changed(self, enabled: bool) -> None:
        if enabled:
            sizes = self._splitter.sizes()
            if len(sizes) >= 3:
                self._history_tile_normal_splitter_sizes = sizes
                left = sizes[0]
                available = max(2, sum(sizes) - left)
                self._splitter.setSizes([left, available // 2, available - available // 2])
            self._right_visible = True
            self._btn_right.setChecked(True)
            self._side_panel.show()
            return
        self._lora_bar.set_split_mode(False)
        sizes = getattr(self, "_history_tile_normal_splitter_sizes", None)
        if sizes:
            self._splitter.setSizes(sizes)

    def _on_history_tile_generation_changed(self, generation_id) -> None:
        if generation_id is None:
            self._lora_bar.set_split_mode(False)
            return
        loras = self._history_loras_for_generation(int(generation_id))
        self._lora_bar.set_split_mode(True, loras)

    def _history_loras_for_generation(self, generation_id: int) -> list[dict]:
        row = _history_db.fetchone(
            "SELECT loras_json FROM generations WHERE id=?",
            (generation_id,),
        )
        if not row or not row["loras_json"]:
            return []
        try:
            raw_loras = json.loads(row["loras_json"])
        except Exception:
            return []
        result: list[dict] = []
        for info in raw_loras if isinstance(raw_loras, list) else []:
            if not isinstance(info, dict):
                continue
            key = info.get("invoke_key", "")
            if not key:
                continue
            db_row = _env_db.fetchone(
                "SELECT name, base, invoke_hash FROM models WHERE invoke_key=?",
                (key,),
            )
            result.append({
                "invoke_key": key,
                "name": info.get("name") or ((db_row["name"] or "") if db_row else "") or key,
                "base": info.get("base") or ((db_row["base"] or "") if db_row else "") or "sdxl",
                "hash": info.get("hash") or ((db_row["invoke_hash"] or "") if db_row else ""),
                "weight": float(info.get("weight", 0.75)),
                "enabled": bool(info.get("enabled", True)),
            })
        return result

    # ── 履歴 D&D ロード ──────────────────────────────────

    def _install_history_drop_targets(self) -> None:
        full_roots = [
            w for w in (
                getattr(self, "_main_toolbar", None),
                getattr(self, "_params_toolbar", None),
                getattr(self, "_lm_toolbar", None),
            )
            if w is not None
        ]
        prompt_roots = [getattr(self, "_editor", None)] if hasattr(self, "_editor") else []

        self._history_full_drop_widgets = self._install_drop_filter_tree(full_roots)
        self._history_prompt_drop_widgets = self._install_drop_filter_tree(
            prompt_roots,
            accept_children=False,
        )
    def _install_drop_filter_tree(
        self,
        roots: list[QWidget],
        *,
        accept_children: bool = True,
    ) -> set[QWidget]:
        widgets: set[QWidget] = set()
        for root in roots:
            if root is None:
                continue
            stack = [root, *root.findChildren(QWidget)]
            root.setAcceptDrops(True)
            for idx, widget in enumerate(stack):
                if idx == 0 or accept_children:
                    widget.setAcceptDrops(True)
                widget.installEventFilter(self)
                widgets.add(widget)
        return widgets

    def eventFilter(self, obj, event) -> bool:
        if event.type() in (
            QEvent.Type.DragEnter,
            QEvent.Type.DragMove,
            QEvent.Type.Drop,
        ) and self._history_drop_mode_for_obj(obj) is not None:
            mime = event.mimeData()
            if mime.hasFormat(HISTORY_GENERATION_MIME):
                mode = self._history_drop_mode_for_obj(obj)
                if event.type() in (QEvent.Type.DragEnter, QEvent.Type.DragMove):
                    event.acceptProposedAction()
                    return True
                gen_id = self._generation_id_from_mime(mime)
                if gen_id is None:
                    event.ignore()
                    return True
                event.acceptProposedAction()
                if mode == "full":
                    QTimer.singleShot(0, lambda gid=gen_id: self._full_load_generation(gid))
                else:
                    QTimer.singleShot(0, lambda gid=gen_id: self._load_generation(gid))
                return True
        return super().eventFilter(obj, event)

    def _history_drop_mode_for_obj(self, obj) -> str | None:
        if obj in self._history_full_drop_widgets:
            return "full"
        if obj in self._history_prompt_drop_widgets:
            return "prompt"
        return None

    @staticmethod
    def _generation_id_from_mime(mime) -> int | None:
        try:
            return int(bytes(mime.data(HISTORY_GENERATION_MIME)).decode("ascii"))
        except (TypeError, ValueError, UnicodeDecodeError):
            return None

    # ── Invoke 操作 ────────────────────────────────────

    @staticmethod
    def _build_seeds(base_seed: int, seed_fixed: bool, count: int) -> list[int]:
        """
        count 枚分のシードを計算する。
          🔓 base_seed < 0  → 1枚ごとにランダム（従来動作）
          🔓 base_seed >= 0 → base_seed からの連番（従来動作）
          🔒 base_seed < 0  → 生成回ごとに新しいシードを1つ採番し、全枚に使う
          🔒 base_seed >= 0 → 全枚 base_seed のまま

        ランダムの上限はシードスピンボックスと同じ int32 最大値に揃える
        （履歴のシードをそのまま UI に持ち回れるようにするため）。
        """
        if seed_fixed:
            seed = random.randint(0, 2_147_483_647) if base_seed < 0 else base_seed
            return [seed] * count
        if base_seed < 0:
            return [random.randint(0, 2_147_483_647) for _ in range(count)]
        return [base_seed + i for i in range(count)]

    def _confirm_fixed_seed_variation(self, seed_fixed: bool, count: int) -> bool:
        """
        シード固定（🔒）で複数枚生成するとき、プロンプトに変動要素（random/sequential
        グループ・ブロックシャッフル）が無ければ同一画像を量産するだけになるため、
        ダイアログを表示して生成を中止する。続行してよければ True。
        （ランダムシード併用時も🔒なら回内は同一シードのため対象）
        """
        if not seed_fixed or count <= 1:
            return True
        if self._editor.document.has_variation_sources(
            include_negative=self._negative_supported
        ):
            return True
        QMessageBox.warning(
            self,
            tr("main.seed_fixed_no_variation_title"),
            tr("main.seed_fixed_no_variation_msg", count=count),
        )
        return False

    @staticmethod
    def _append_prompt_tail(base: str, parts: list[str]) -> str:
        text = (base or "").strip()
        seen = {p.strip() for p in text.split(",") if p.strip()}
        extra: list[str] = []
        for part in parts:
            value = (part or "").strip()
            if not value:
                continue
            for piece in [p.strip() for p in value.split(",") if p.strip()]:
                if piece not in seen:
                    seen.add(piece)
                    extra.append(piece)
        if not extra:
            return text
        return ", ".join([p for p in [text, ", ".join(extra)] if p])

    def _lora_prompt_tails(self, loras: list[dict]) -> tuple[list[str], list[str]]:
        pos: list[str] = []
        neg: list[str] = []
        for lora in loras:
            if not lora.get("enabled", True):
                continue
            key = lora.get("invoke_key") or lora.get("lora_key") or ""
            if not key:
                continue
            for row in _env_db.fetchall(
                "SELECT trigger_words FROM lora_trigger_sets WHERE invoke_key=? ORDER BY sort_order, id",
                (key,),
            ):
                if row["trigger_words"]:
                    pos.append(str(row["trigger_words"]))
            for row in _env_db.fetchall(
                "SELECT neg_words FROM lora_neg_prompt_sets WHERE invoke_key=? ORDER BY sort_order, id",
                (key,),
            ):
                if row["neg_words"]:
                    neg.append(str(row["neg_words"]))
        return pos, neg

    def _plan_active_rows(self) -> tuple[dict | None, list[dict]]:
        import db.generation_plan_db as _plan_db
        if self._selected_plan_id is None:
            return None, []
        plan = _plan_db.get_plan(self._selected_plan_id)
        if not plan:
            return None, []
        rows = [
            row for row in plan["rows"]
            if row.get("enabled") and not row.get("model_missing")
        ]
        return plan, rows

    def _confirm_plan_count(self, rows: list[dict]) -> int | None:
        count = self._count_spin.value() if hasattr(self, "_count_spin") else 1
        if count == 1:
            return 1
        total = sum(int(r.get("image_count") or 1) for r in rows) * count
        box = QMessageBox(self)
        box.setWindowTitle(tr("main.plan_count_title"))
        box.setText(tr("main.plan_count_msg", count=count, total=total))
        cont_btn = box.addButton(tr("main.plan_count_continue"), QMessageBox.ButtonRole.AcceptRole)
        one_btn = box.addButton(tr("main.plan_count_force_one"), QMessageBox.ButtonRole.ActionRole)
        cancel_btn = box.addButton(tr("main.record_all_cancel"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel_btn)
        box.exec()
        clicked = box.clickedButton()
        if clicked is cont_btn:
            return count
        if clicked is one_btn:
            self._count_spin.setValue(1)
            return 1
        return None

    def _plan_loras_for_send(self, row: dict) -> list[dict]:
        result: list[dict] = []
        for lora in row.get("loras") or []:
            if not lora.get("enabled", True) or lora.get("missing"):
                continue
            result.append({
                "invoke_key": lora.get("lora_key") or lora.get("invoke_key") or "",
                "name": lora.get("name") or "",
                "base": lora.get("base") or row.get("model_base") or "",
                "weight": float(lora.get("weight") or 0.75),
                "enabled": True,
                "hash": lora.get("hash", ""),
            })
        return result

    def _generate_plan_impl(self, record_mode: str, *, record_map: bool = True) -> None:
        if self._send_queue_worker and self._send_queue_worker.isRunning():
            return

        plan, rows = self._plan_active_rows()
        if not plan or not rows:
            QMessageBox.information(self, tr("main.plan_empty_title"), tr("main.plan_empty_msg"))
            return
        multiplier = self._confirm_plan_count(rows)
        if multiplier is None:
            return

        params = self._get_gen_params()
        base_seed = params.pop("base_seed")
        seed_fixed = params.pop("seed_fixed", False)
        params.pop("count", None)
        params.pop("model_key", None)
        params.pop("model_base", None)
        params.pop("template_id", None)
        params.pop("loras", None)

        max_count = max(int(row.get("image_count") or 1) * multiplier for row in rows)
        # プランはモデル巡回自体が変動パターンなので、複数行なら固定シード警告を出さない。
        if len(rows) == 1 and not self._confirm_fixed_seed_variation(seed_fixed, max_count):
            return

        live_doc = self._editor.document
        live_doc.reset_selection_log()
        base_neg = self._effective_negative_prompt()
        base_pos: list[str] = []
        snap_docs: list[PromptDocument] = []
        first_sel: dict[int, list] = {}
        for i in range(max_count):
            base_pos.append(self._editor.compile_positive())
            snap_docs.append(live_doc.snapshot_with_last_selection())
            if i == 0:
                first_sel = {
                    id(g): list(g._last_selected)
                    for g in live_doc._all_group_tiles()
                    if g.mode in ("random", "sequential") and g._last_selected is not None
                }
        if not base_pos or not base_pos[0]:
            self._show_status(tr("main.error_empty_prompt"), error=True)
            return
        for snap in snap_docs:
            self._strip_unsupported_negative(snap)
        seeds = self._build_seeds(base_seed, seed_fixed, max_count)

        self._ensure_current_group()
        if record_mode != "none" and local_storage.has_group_folder(self._current_group_id):
            dest = local_storage.resolve_folder_path(self._current_group_id)
            if not local_storage.is_drive_accessible(dest):
                QMessageBox.warning(
                    self,
                    tr("main.save_dest_error_title"),
                    tr("main.save_dest_error_msg", dest=dest),
                )
                return

        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        memo_text = self._editor.get_memo().strip()

        def _save_memo(gid: int) -> None:
            if not memo_text:
                return
            _history_db.execute(
                "INSERT OR IGNORE INTO image_reviews (generation_id) VALUES (?)", (gid,))
            _history_db.execute(
                "UPDATE image_reviews SET review_text=?, updated_at=CURRENT_TIMESTAMP WHERE generation_id=?",
                (memo_text, gid),
            )

        def _insert_generation(row: dict, pos: str, neg: str, seed: int, image_count: int, loras: list[dict]) -> int:
            loras_json = json.dumps(loras, ensure_ascii=False) if loras else None
            cur = _history_db.execute(
                """INSERT INTO generations
                   (sent_positive_prompt, sent_negative_prompt, created_at, group_id,
                    seed, steps, cfg_scale, scheduler, width, height, loras_json,
                    invoke_key, model_name, model_base, template_id, image_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    pos, neg, now, self._current_group_id,
                    seed,
                    int(row.get("steps") or 30),
                    float(row.get("cfg_scale") or 7.0),
                    row.get("scheduler") or "euler",
                    params["width"], params["height"], loras_json,
                    row.get("model_key") or None,
                    row.get("model_name") or None,
                    row.get("model_base") or None,
                    None,
                    image_count,
                ),
            )
            return int(cur.lastrowid)

        from db.connections import get_active_history_name
        import db.send_queue_db as _sq
        history_name = get_active_history_name()
        all_gen_ids: list[int] = []
        seq = 1
        self._plan_item_ids = []
        self._plan_sent_images = 0
        for row in rows:
            row_count = int(row.get("image_count") or 1) * multiplier
            loras = self._plan_loras_for_send(row)
            lora_pos, lora_neg = self._lora_prompt_tails(loras)
            extra_pos = [row.get("extra_positive") or ""] + lora_pos
            extra_neg = [row.get("extra_negative") or ""] + lora_neg
            pos_list = [self._append_prompt_tail(base_pos[i], extra_pos) for i in range(row_count)]
            neg = self._append_prompt_tail(base_neg, extra_neg)
            row_seeds = seeds[:row_count]
            gen_ids: list[int] = []

            # 実送信パラメータは「行の保存値（Steps/CFG/Scheduler）」を使う。
            # W/H/Board は共通（params 由来）。GUI の Steps/CFG/Scheduler は使わない
            # （プランは行ごとにモデル別の最適値を持つため。DB記録(_insert_generation)も
            #  同じ行の値を保存しており、実生成と履歴の値が一致する）。
            row_params = dict(params)
            row_params["steps"]     = int(row.get("steps") or 30)
            row_params["cfg_scale"] = float(row.get("cfg_scale") or 7.0)
            row_params["scheduler"] = row.get("scheduler") or "euler"

            if record_mode == "all":
                for i in range(row_count):
                    gid = _insert_generation(row, pos_list[i], neg, row_seeds[i], 1, loras)
                    gen_ids.append(gid)
                    snap_docs[i].save_to_db(gid)
                    _save_memo(gid)
            elif record_mode == "single":
                gid = _insert_generation(row, pos_list[0], neg, row_seeds[0], row_count, loras)
                gen_ids.append(gid)
                snap_docs[0].save_to_db(gid)
                _save_memo(gid)

            if gen_ids:
                all_gen_ids.extend(gen_ids)
                for gid in gen_ids:
                    self._start_completion_polling(gid)

            _sq.enqueue_unit(
                seq=seq,
                history_name=history_name if gen_ids else None,
                generation_ids=gen_ids or None,
                payload={
                    "pos": pos_list, "neg": neg, "seeds": row_seeds,
                    "gen_params": row_params,
                    "model_key": row.get("model_key") or "",
                    "model_name": row.get("model_name") or "",
                    "model_base": row.get("model_base") or "",
                    "template_id": None,
                    "loras": loras,
                },
            )
            seq += 1

        if all_gen_ids and record_map:
            if record_mode == "all" or len(all_gen_ids) > 1:
                import db.hmap_db as _hmap_db
                from db.connections import get_active_history_name as _gahn
                active_db = _gahn()
                parent = self._current_editor_history_node()
                parent_db = parent[0] if parent else None
                parent_id = parent[1] if parent else None
                for gid in all_gen_ids:
                    _hmap_db.record_node(active_db, gid, parent_db, parent_id)
                if parent is None:
                    self._set_current_editor_history_node(active_db, all_gen_ids[0])
                self._history_map_dialog_focus = (active_db, all_gen_ids[0])
                self._refresh_history_map_dialog()
            else:
                self._record_editor_history_node(all_gen_ids[0])
        elif all_gen_ids:
            self._refresh_lineage_card()
            self._refresh_history_map_dialog()

        self._apply_selection_to_live_doc(first_sel)
        self._editor.set_preview_text(base_pos[0], base_neg)
        self._side_panel.refresh_history()
        self._start_send_queue()

    def _send_recall(self) -> None:
        """
        プロンプトをパラメータバーの設定で即時生成する（DB保存・ポーリングなし）。
        バックグラウンドワーカーで実行し UI をブロックしない。
        """
        if self._is_plan_mode():
            self._generate_plan_impl("none", record_map=False)
            return
        if self._send_queue_worker and self._send_queue_worker.isRunning():
            return

        params     = self._get_gen_params()
        count      = params.pop("count")
        base_seed  = params.pop("base_seed")
        seed_fixed = params.pop("seed_fixed", False)
        model_key  = params.pop("model_key", "")
        model_base = params.pop("model_base", "")
        template_id = params.pop("template_id", None)
        loras      = params.pop("loras", [])   # generate_batch の kwarg 重複を防ぐ

        if not self._confirm_fixed_seed_variation(seed_fixed, count):
            return

        # count 回コンパイル → GroupTile の random/sequential が生成ごとに進む
        pos_list = [self._editor.compile_positive() for _ in range(count)]
        neg      = self._effective_negative_prompt()
        # 実際に送信するプロンプトをそのままプレビューに表示（シャッフル結果が毎回反映される）
        self._editor.set_preview_text(pos_list[0], neg)
        if not pos_list[0]:
            self._show_status(tr("main.error_empty_prompt"), error=True)
            return

        seeds = self._build_seeds(base_seed, seed_fixed, count)

        _model_name_r = ""
        if model_key:
            _mrow_r = _env_db.fetchone("SELECT name FROM models WHERE invoke_key=?", (model_key,))
            _model_name_r = (_mrow_r["name"] or "") if _mrow_r else ""

        # ── 送信キューへ投入（記録なし=履歴行に紐づかないユニット）──
        import db.send_queue_db as _sq
        self._plan_item_ids = []
        self._plan_sent_images = 0
        _sq.enqueue_unit(
            seq=1,
            history_name=None,
            generation_ids=None,
            payload={
                "pos": pos_list, "neg": neg, "seeds": seeds,
                "gen_params": params,
                "model_key": model_key or "",
                "model_name": _model_name_r or "",
                "model_base": model_base or "",
                "template_id": template_id,
                "loras": loras,
            },
        )
        self._start_send_queue()

    def _generate(self) -> None:
        """履歴生成: チェック状態に従って履歴行数と履歴マップ記録を決める。"""
        record_one = bool(getattr(self, "_history_one_cb", None) and self._history_one_cb.isChecked())
        record_map = bool(getattr(self, "_history_map_cb", None) and self._history_map_cb.isChecked())
        record_mode = "single" if record_one else "all"
        if self._is_plan_mode():
            self._generate_plan_impl(record_mode, record_map=record_map)
            return
        count = self._count_spin.value() if hasattr(self, "_count_spin") else 1
        # 変動要素ガードを4択より先に出す（4択で選んだ後に中止になるのを避ける）
        if not self._confirm_fixed_seed_variation(
            self._seed_fixed_btn.isChecked(), count
        ):
            return
        # 大量の履歴行は履歴ツリーの肥大・動作低下を招くため、Count≥10 かつ
        # 1件モードOFFでは記録方法を確認する。
        if count >= 10 and record_mode == "all":
            box = QMessageBox(self)
            box.setWindowTitle(tr("main.record_all_confirm_title"))
            box.setText(tr("main.record_all_confirm_msg", count=count))
            all_btn    = box.addButton(tr("main.record_all_continue"),
                                       QMessageBox.ButtonRole.AcceptRole)
            single_btn = box.addButton(tr("main.record_all_to_single"),
                                       QMessageBox.ButtonRole.ActionRole)
            norec_btn  = box.addButton(tr("main.record_all_to_norecord"),
                                       QMessageBox.ButtonRole.ActionRole)
            cancel_btn = box.addButton(tr("main.record_all_cancel"),
                                       QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(cancel_btn)
            box.exec()
            clicked = box.clickedButton()
            if clicked is cancel_btn or clicked is None:
                return
            if clicked is single_btn:
                self._generate_impl("single", record_map=record_map)
                return
            if clicked is norec_btn:
                self._send_recall()
                return
        self._generate_impl(record_mode, record_map=record_map)

    def _generate_single(self) -> None:
        """履歴1件生成: 代表1件のみ記録する（従来の記録生成と同じ動作）。"""
        if self._is_plan_mode():
            self._generate_plan_impl("single", record_map=True)
            return
        self._generate_impl("single", record_map=True)

    def _generate_impl(self, record_mode: str, *, record_map: bool = True) -> None:
        """
        バックグラウンドワーカーで count 枚キューに追加する（UIスレッドをブロックしない）。
        DB登録はワーカー開始前にメインスレッドで行う。

        record_mode:
            "single": 1バッチ = 1履歴行。代表値（1枚目）のみ記録する。
            "all":    1枚 = 1履歴行。count 行を登録し、各行に異なるプロンプト/シードを
                      記録する。タイルスナップショット・メモは全行に複製する。
            record_map: True の時だけ履歴マップへ系譜ノードを追加する。
        """
        # 前のワーカーが動いていれば無視（ボタンは無効なので通常ここには来ない）
        if self._send_queue_worker and self._send_queue_worker.isRunning():
            return

        params     = self._get_gen_params()
        count      = params.pop("count")
        base_seed  = params.pop("base_seed")
        seed_fixed = params.pop("seed_fixed", False)
        model_key  = params.pop("model_key", "")
        model_base = params.pop("model_base", "")
        template_id = params.pop("template_id", None)
        loras      = params.pop("loras", [])

        if not self._confirm_fixed_seed_variation(seed_fixed, count):
            return

        # count 回コンパイル → GroupTile の random/sequential が生成ごとに進む。
        # 各回の「選択されたタイル」を子の履歴スナップショットとして退避する
        # （親=中央ペインのドキュメントは不変。子は選択タイルのみON・他OFFで記録）。
        live_doc = self._editor.document
        live_doc.reset_selection_log()
        neg = self._effective_negative_prompt()  # 先に1回（ネガ側の選択も記録に含める）
        pos_list: list[str] = []
        snap_docs: list[PromptDocument] = []
        first_sel: dict[int, list] = {}
        for i in range(count):
            pos_list.append(self._editor.compile_positive())
            snap_docs.append(live_doc.snapshot_with_last_selection())
            if i == 0:
                # 1枚目（=記録後の継承権者）の選択状態。全コンパイル終了後に
                # 中央ペインへ反映する（先に反映すると2枚目以降の選択肢が消える）
                first_sel = {
                    id(g): list(g._last_selected)
                    for g in live_doc._all_group_tiles()
                    if g.mode in ("random", "sequential") and g._last_selected is not None
                }
        # 実際に送信するプロンプトをそのままプレビューに表示（シャッフル結果が毎回反映される）
        self._editor.set_preview_text(pos_list[0], neg)
        if not pos_list[0]:
            self._show_status(tr("main.error_empty_prompt"), error=True)
            return

        # シードを事前に全計算（DB登録の代表値 = 最初の1枚のシード）
        seeds = self._build_seeds(base_seed, seed_fixed, count)

        # ── 保存先ドライブの事前チェック ──────────────────────
        self._ensure_current_group()
        if local_storage.has_group_folder(self._current_group_id):
            _dest = local_storage.resolve_folder_path(self._current_group_id)
            if not local_storage.is_drive_accessible(_dest):
                QMessageBox.warning(
                    self,
                    tr("main.save_dest_error_title"),
                    tr("main.save_dest_error_msg", dest=_dest),
                )
                return

        # ── DB保存 ────────────────────────────────────────────
        # APIコールより前に登録することでポーリングをすぐ開始できる
        now        = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        loras_json = json.dumps(loras, ensure_ascii=False) if loras else None

        # モデル名をDBから取得（INSERT前に確定させる）
        _model_name = ""
        if model_key:
            _mrow = _env_db.fetchone("SELECT name FROM models WHERE invoke_key=?", (model_key,))
            _model_name = (_mrow["name"] or "") if _mrow else ""

        def _insert_generation(pos: str, seed: int, image_count: int) -> int:
            cur = _history_db.execute(
                """INSERT INTO generations
                   (sent_positive_prompt, sent_negative_prompt, created_at, group_id,
                    seed, steps, cfg_scale, scheduler, width, height, loras_json,
                    invoke_key, model_name, model_base, template_id, image_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    pos, neg, now, self._current_group_id,
                    seed,
                    params["steps"], params["cfg_scale"], params["scheduler"],
                    params["width"],  params["height"], loras_json,
                    model_key or None, _model_name or None, model_base or None,
                    template_id, image_count,
                ),
            )
            return int(cur.lastrowid)

        # 子の履歴スナップショット: 各回の選択状態を反映済み（非対応ネガは除去）
        for snap in snap_docs:
            self._strip_unsupported_negative(snap)
        memo_text = self._editor.get_memo().strip()

        def _save_memo(gid: int) -> None:
            if not memo_text:
                return
            _history_db.execute(
                "INSERT OR IGNORE INTO image_reviews (generation_id) VALUES (?)", (gid,))
            _history_db.execute(
                "UPDATE image_reviews SET review_text=?, updated_at=CURRENT_TIMESTAMP WHERE generation_id=?",
                (memo_text, gid),
            )

        gen_ids: list[int] = []
        if record_mode == "all" and count > 1:
            # ── 全件記録: 1枚 = 1履歴行 ───────────────────────
            # スナップショットは各回の選択状態（選択タイルのみON）を行ごとに保存。
            # メモは全行に複製
            for i in range(count):
                gid = _insert_generation(pos_list[i], seeds[i], image_count=1)
                gen_ids.append(gid)
                snap_docs[i].save_to_db(gid)
                _save_memo(gid)

        else:
            # ── 履歴1件: 1バッチ = 1履歴行（代表値＝1枚目）────
            gid = _insert_generation(pos_list[0], seeds[0], image_count=count)
            gen_ids.append(gid)
            snap_docs[0].save_to_db(gid)
            _save_memo(gid)

        if gen_ids and record_map:
            if record_mode == "all" and len(gen_ids) > 1:
                # 系譜: 全行を「同じ親から並列の子」として記録する。
                # _record_editor_history_node を行ごとに呼ぶと current が移動して
                # 鎖状（親→子→孫…）になるため、親を先に確定して record_node を直接使う。
                import db.hmap_db as _hmap_db
                from db.connections import get_active_history_name as _gahn
                active_db = _gahn()
                parent    = self._current_editor_history_node()
                parent_db = parent[0] if parent else None
                parent_id = parent[1] if parent else None
                for gid in gen_ids:
                    _hmap_db.record_node(active_db, gid, parent_db, parent_id)
                if parent is None:
                    self._set_current_editor_history_node(active_db, gen_ids[0])
                self._history_map_dialog_focus = (active_db, gen_ids[0])
                self._refresh_history_map_dialog()
            else:
                self._record_editor_history_node(gen_ids[0])
        elif gen_ids:
            self._refresh_lineage_card()
            self._refresh_history_map_dialog()

        # 中央ペインの選択状態は生成時スナップショットに合わせるが、
        # current は自動で子へ移動しない（明示操作でだけ移動する）。
        self._apply_selection_to_live_doc(first_sel)
        self._editor.set_preview_text(pos_list[0], neg)  # プレビューは実送信文字列を維持

        self._side_panel.refresh_history()
        for gid in gen_ids:
            self._start_completion_polling(gid)

        # ── 送信キューへ投入（このバッチ=1ユニット）───────────
        from db.connections import get_active_history_name
        import db.send_queue_db as _sq
        history_name = get_active_history_name()
        self._plan_item_ids = []
        self._plan_sent_images = 0
        _sq.enqueue_unit(
            seq=1,
            history_name=history_name,
            generation_ids=gen_ids,
            payload={
                "pos": pos_list, "neg": neg, "seeds": seeds,
                "gen_params": params,
                "model_key": model_key or "",
                "model_name": _model_name or "",
                "model_base": model_base or "",
                "template_id": template_id,
                "loras": loras,
            },
        )
        self._start_send_queue()

    def _assign_item_ids_to_rows(
        self, gen_ids: list, item_ids: list, history_name: str = "",
    ) -> None:
        """
        enqueue で得た item_ids をユニットの履歴行へ割り当てる（記録なしは gen_ids 空）。

        gen_ids が1件なら item_ids をまとめて保存（履歴1件モード）。複数件なら
        行ごとに1つずつ割り当てる。キュー item_id は作成順の自動連番で、バッチ内
        アイテムは batch data のインデックス順に作成されるため、
        昇順 = seeds/pos_list のインデックス順（応答の並びに依らずソートで確定）。
        """
        if not gen_ids:
            return  # 記録なし生成
        if not item_ids:
            # enqueue は成功扱いだが item_id が返らない異常系。
            # この行には以後画像も中断判定も来ないため、中断と同じ扱いで削除する。
            self._on_generation_aborted(list(gen_ids), history_name or None)
            return

        hdb = _history_db.for_history(history_name) if history_name else _history_db
        if len(gen_ids) == 1:
            hdb.execute(
                "UPDATE generations SET invoke_queue_item_ids = ? WHERE id = ?",
                (json.dumps(item_ids), gen_ids[0]),
            )
            return

        ordered = sorted(int(i) for i in item_ids)
        unmatched: list[int] = []
        for idx, gid in enumerate(gen_ids):
            if idx < len(ordered):
                hdb.execute(
                    "UPDATE generations SET invoke_queue_item_ids = ? WHERE id = ?",
                    (json.dumps([ordered[idx]]), gid),
                )
            else:
                unmatched.append(int(gid))
        if unmatched:
            # item_id が割り当てられなかった行は回収不能 → 中断と同じ扱いで削除
            self._on_generation_aborted(unmatched, history_name or None)

    # ── 送信キュー（バッチ生成エンジン）─────────────────────
    #   ①プラン構築: 履歴行を作り、送信データ（generate_batch の引数JSON）を
    #     send_queue.db（一時バッファ）へ貯める
    #   ②送信: _SendQueueWorker が一気に順次 enqueue → item_ids を履歴行へ書いた
    #     レコードは即削除（正常時はキューは常に空）→ 回収は既存の同期機構
    #   中止/失敗: 以降を送らず、未送信ユニットの行を破棄し、
    #     送信済み未受信の item を個別キャンセル（終端失敗→既存の行自動削除）

    def _start_send_queue(self) -> None:
        """send_queue の未処理ユニットの送信を開始する（実行中なら何もしない）。"""
        if self._send_queue_worker and self._send_queue_worker.isRunning():
            return
        import db.send_queue_db as _sq
        pending_count = len(_sq.pending_units())
        self._set_generation_busy(True)
        self._btn_gen.setText("⏳ 送信中…")
        self._btn_cancel_plan.setEnabled(True)
        # 進捗ダイアログはマルチユニット送信（プラン）でのみ表示する。
        # 単一モデル生成は 1 ユニット=サブ秒で送信が終わるため、ダイアログは
        # 一瞬出て消えるだけで邪魔。複数ユニットの送信中だけ、その間メインUIを
        # 触らせない目的でモーダル表示する（ユーザー要望 2026-06-13）。
        if pending_count > 1:
            dlg = QProgressDialog(
                tr("main.plan_progress_label"),
                tr("main.record_all_cancel"),   # 普通の「キャンセル」ボタン
                0,
                pending_count,
                self,
            )
            dlg.setWindowTitle(tr("main.plan_progress_title"))
            dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
            dlg.setMinimumDuration(0)
            dlg.setValue(0)
            dlg.canceled.connect(self._cancel_generation_plan)
            self._generation_progress = dlg
            dlg.show()
        worker = _SendQueueWorker(self._client, parent=self)
        worker.unit_sent.connect(self._on_send_unit_sent)
        worker.all_done.connect(self._on_send_queue_done)
        worker.failed.connect(self._on_send_queue_failed)
        self._send_queue_worker = worker
        worker.start()

    def _on_send_unit_sent(
        self, unit_id: int, gen_ids: list, history_name: str, item_ids: list,
    ) -> None:
        """ユニット送信完了: item_ids を履歴行へ割り当て、キューレコードを削除する。"""
        import db.send_queue_db as _sq
        self._plan_item_ids.extend(int(i) for i in item_ids)
        self._plan_sent_images += len(item_ids)
        if self._generation_progress is not None:
            self._generation_progress.setValue(self._generation_progress.value() + 1)
        self._assign_item_ids_to_rows(
            [int(g) for g in gen_ids], [int(i) for i in item_ids], history_name,
        )
        _sq.delete_unit(int(unit_id))

    def _close_send_progress(self) -> None:
        """
        送信プログレスダイアログを安全に閉じる（プログラムによる正常クローズ用）。

        ⚠ Codex/ClaudeCode 共有メモ（2026-06-13 修正の要点）:
        QProgressDialog.close() は **canceled シグナルを発火する**（hide() は発火しない）。
        `_start_send_queue` で canceled→_cancel_generation_plan を接続しているため、
        送信成功後に close() で閉じると _cancel_generation_plan が誤発火し、
        送ったばかりの Invoke item を即キャンセルしてしまう（画像が出ない不具合の真因）。
        対策: 正常クローズ時はシグナルを切ってから hide()+deleteLater で破棄する。
        ユーザーの中止操作（Cancelボタン/×/ESC）は接続が生きたまま発火するので従来どおり機能する。
        """
        dlg = self._generation_progress
        if dlg is None:
            return
        self._generation_progress = None
        try:
            dlg.canceled.disconnect(self._cancel_generation_plan)
        except (TypeError, RuntimeError):
            pass  # 既に切断済み/破棄済み
        dlg.hide()
        dlg.deleteLater()

    def _on_send_queue_done(self) -> None:
        self._close_send_progress()
        self._set_generation_busy(False)
        self._btn_gen.setText(tr("main.btn_generate"))
        self._show_status(tr("main.status_generate_ok", count=self._plan_sent_images))
        self._update_cancel_plan_btn()
        self._check_connection()

    def _on_send_queue_failed(self, msg: str) -> None:
        """送信失敗: いつもの操作の繰り返しで失敗は想定外 → ユーザー中止と同じ全停止。"""
        self._show_status(msg, error=True)
        self._cancel_generation_plan()

    def _cancel_generation_plan(self) -> None:
        """
        中止: 以降のユニットを送らず、未送信ユニットの履歴行を破棄し、
        送信済み未受信の item を Invoke で個別キャンセルする。
        キャンセルされた item は終端失敗となり、既存の同期機構が行を自動削除する。
        """
        import db.send_queue_db as _sq
        worker = self._send_queue_worker
        worker_running = bool(worker and worker.isRunning())

        # 誤発火ガード: 中止すべきものが何も無い状態での呼び出し（想定外のシグナル等）
        # では、ダイアログだけ閉じて副作用（item キャンセル・履歴破棄・エラー表示）を出さない。
        # ※ 正常完了直後は _plan_item_ids に回収待ち item が残るため has_residue=True となり
        #   ここは通らない（_close_send_progress 側でそもそも誤発火しないのが第一の防御）。
        has_residue = (
            worker_running
            or bool(self._plan_item_ids)
            or bool(self._pending_gen_ids)
            or bool(_sq.pending_units())
        )
        if not has_residue:
            self._close_send_progress()
            return

        self._close_send_progress()
        if worker_running:
            worker.stop()
            worker.wait(3000)

        # 残ユニットの後始末（送信済み=保険で控え、未送信=履歴行ごと破棄）
        for row in _sq.pending_units():
            sent = _sq.unit_sent_item_ids(row)
            if sent:
                self._plan_item_ids.extend(sent)
            else:
                gen_ids = _sq.unit_generation_ids(row)
                if gen_ids and row["history_name"]:
                    self._purge_aborted_generations(gen_ids, str(row["history_name"]))
                    for gid in gen_ids:
                        try:
                            self._pending_gen_ids.remove(gid)
                        except ValueError:
                            pass
            _sq.delete_unit(int(row["id"]))
        if not self._pending_gen_ids:
            self._poll_timer.stop()

        # 発行済み item を個別キャンセル（ベストエフォート・別スレッド）
        if self._plan_item_ids:
            ids = list(dict.fromkeys(self._plan_item_ids))
            self._plan_item_ids = []
            self._cancel_worker = _CancelItemsWorker(self._client, ids, parent=self)
            self._cancel_worker.start()

        self._set_generation_busy(False)
        self._btn_gen.setText(tr("main.btn_generate"))
        self._btn_cancel_plan.setEnabled(False)
        self._refresh_lineage_card()
        self._refresh_history_map_dialog()
        self._side_panel.refresh_history()
        self._show_status(tr("main.status_plan_cancelled"), error=True)

    def _update_cancel_plan_btn(self) -> None:
        """⏹の有効状態: 送信中または未回収の生成が残っている間だけ押せる。"""
        if not hasattr(self, "_btn_cancel_plan"):
            return
        sending = bool(self._send_queue_worker and self._send_queue_worker.isRunning())
        self._btn_cancel_plan.setEnabled(sending or bool(self._pending_gen_ids))

    def _check_send_queue_residue(self) -> None:
        """
        起動時: 送信キューにレコードが残っていれば前回セッションは異常終了。
        送信済み item をキャンセル（ベストエフォート）して全クリアする。

        自動再開はしない（落ちた後に再開機能は不要というユーザー決定。プランは
        送信時のプロンプトが残っているため手動で再実行できる）。
        紐づく一時行は直後の整合性修復（_finalize_transient_generations）が破棄する。
        """
        import db.send_queue_db as _sq
        try:
            rows = _sq.pending_units()
        except Exception:
            return
        if not rows:
            return
        for row in rows:
            for iid in (_sq.unit_sent_item_ids(row) or []):
                try:
                    self._client.cancel_queue_item(int(iid))
                except Exception:
                    break  # 接続不可なら以降も無理（Invoke再起動ならキュー自体が空）
        _sq.clear_all()

    def _shutdown_send_queue(self) -> None:
        """
        終了時: 中止と同じ処理でキューを空にする（不変条件「正常終了時にレコードなし」）。
        未送信ユニットの行は直後の一時行破棄が片づける。
        item キャンセルは同期・ベストエフォート（接続不可なら即諦める）。
        """
        import db.send_queue_db as _sq
        worker = self._send_queue_worker
        if worker and worker.isRunning():
            worker.stop()
            worker.wait(3000)
        rows = _sq.pending_units()
        if not rows and not self._pending_gen_ids:
            return  # プランは動いていない（キャンセルすべき item もない）
        sent = list(self._plan_item_ids)
        for row in rows:
            sent.extend(_sq.unit_sent_item_ids(row) or [])
        _sq.clear_all()
        for iid in dict.fromkeys(sent):
            try:
                self._client.cancel_queue_item(int(iid))
            except Exception:
                break

    def _start_completion_polling(self, gen_id: int) -> None:
        """
        生成IDを登録し、完了待ちポーリングを開始する。
        既にポーリング中の場合は gen_id を追加するだけ（タイマーは継続）。
        """
        self._pending_gen_ids.append(gen_id)
        if not self._poll_timer.isActive():
            self._poll_count = 0
            self._poll_timer.start(5000)  # 5秒ごと

    def _on_poll_tick(self) -> None:
        """
        ポーリングタイマーのコールバック。
        未紐付け生成が残っている間は同期を繰り返す。期限は5分（5秒×60回）だが、
        画像が届くたびにカウントをリセットして延長する（_on_hist_sync_done）。
        大量 Count の逐次生成が5分を超えても、進捗がある限り打ち切らない。
        """
        MAX_POLLS = 60  # 進捗なしで5分続いたら打ち切り

        self._poll_count += 1
        if self._poll_count > MAX_POLLS or not self._pending_gen_ids:
            self._poll_timer.stop()
            self._pending_gen_ids.clear()
            return

        # バックフィル同期を実行
        self._sync_history()

        # 完了した pending IDs を除外
        # 完了条件: ① 最初の画像が紐付き済み、かつ ② image_count 枚全て保存済み
        still_pending = []
        for gid in list(self._pending_gen_ids):
            row = _history_db.fetchone(
                """SELECT g.invoke_image_name,
                          COALESCE(g.image_count, 1) AS image_count,
                          (SELECT COUNT(*) FROM generation_images
                           WHERE generation_id = g.id) AS saved_count
                   FROM generations g WHERE g.id = ?""",
                (gid,),
            )
            if not row:
                still_pending.append(gid)
            elif not row["invoke_image_name"]:
                still_pending.append(gid)  # 最初の画像がまだ未到着
            elif row["saved_count"] < row["image_count"]:
                still_pending.append(gid)  # 全枚数が揃っていない

        self._pending_gen_ids = still_pending
        if not self._pending_gen_ids:
            self._poll_timer.stop()
            self._plan_item_ids = []  # 全て回収済み → 中止対象なし
        self._update_cancel_plan_btn()

    def _shuffle_and_preview(self) -> None:
        """シャッフルを実行してプレビューを更新する（再コンパイルで自動反映）"""
        # PromptEditorの_update_previewは compile() を呼ぶたびにシャッフルが走る
        self._editor._update_preview()
        self._show_status(tr("main.status_shuffle_ok"))

    # ── オールクリア ────────────────────────────────────

    def _all_clear(self) -> None:
        """全ブロック・LoRAをクリアし、保存グループの "Positive"/"Negative" をデフォルト配置する。"""
        import json
        from core.prompt_builder import GroupTile

        confirm_msg = tr("main.clear_all_confirm_msg")
        if self._current_editor_history_node() is not None:
            confirm_msg += "\n\n" + tr("lineage.break_note")
        reply = QMessageBox.question(
            self,
            tr("main.clear_all_confirm_title"),
            confirm_msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # 1. 保存グループ（group_presets テーブル）から "Positive" / "Negative" を検索
        def _load_preset(name: str) -> GroupTile | None:
            row = _library_db.fetchone(
                "SELECT name, group_json FROM group_presets WHERE name=? LIMIT 1", (name,)
            )
            if row and row["group_json"]:
                try:
                    return GroupTile.from_dict(
                        json.loads(row["group_json"]),
                        name_override=row["name"] or None,
                        restore_ui_state=False,
                    )
                except Exception:
                    pass
            return None

        pos_group = _load_preset("Positive")
        neg_group = _load_preset("Negative")

        # 2. 全ブロックをクリア
        for bw in self._editor.all_block_widgets():
            bw.block.tiles.clear()

        # 3. LoRAをクリア
        self._lora_bar.set_loras([])
        self._lora_browser.set_selected_keys([])

        # 4. 保存グループが見つかっていればグループとして配置
        if pos_group is not None:
            self._editor._bw_top.block.tiles.append(pos_group)
        if neg_group is not None:
            self._editor._bw_neg.block.tiles.append(neg_group)

        # 5. 全ブロックを再描画
        for bw in self._editor.all_block_widgets():
            bw.reload()
        self._editor._update_preview()
        self._detach_current_editor_history_lineage()

    # ── 設定ダイアログ ───────────────────────────────────

    def _open_settings(self) -> None:
        """設定ダイアログを開く"""
        from ui.settings_dialog import SettingsDialog
        before = {
            key: _get_setting(key, default)
            for key, default in (
                ("language", "ja"),
                ("theme", "dark"),
                ("font_size", "10"),
                ("tile_local_only_display", "0"),
                ("show_nsfw", "0"),
                ("app_icon_path", ""),
                ("unregistered_tile_bg", "#001d9c"),
                ("unregistered_tile_border", "#91821f"),
                ("unregistered_tile_fg", "#d2bf2e"),
                ("history_text_color_dark", ""),
                ("history_text_color_light", ""),
                ("history_line_color_dark", ""),
                ("history_line_color_light", ""),
            )
        }
        dlg = SettingsDialog(client=self._client, parent=self)
        dlg.invoke_setup_requested.connect(lambda: QTimer.singleShot(0, self._open_invoke_setup))
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        after = {
            key: _get_setting(key, default)
            for key, default in (
                ("language", "ja"),
                ("theme", "dark"),
                ("font_size", "10"),
                ("tile_local_only_display", "0"),
                ("show_nsfw", "0"),
                ("app_icon_path", ""),
                ("unregistered_tile_bg", "#001d9c"),
                ("unregistered_tile_border", "#91821f"),
                ("unregistered_tile_fg", "#d2bf2e"),
                ("history_text_color_dark", ""),
                ("history_text_color_light", ""),
                ("history_line_color_dark", ""),
                ("history_line_color_light", ""),
            )
        }
        live_style_changed = any(before[key] != after[key] for key in ("language", "font_size"))
        theme_changed = before["theme"] != after["theme"]
        nsfw_changed = before["show_nsfw"] != after["show_nsfw"]
        unreg_color_changed = (
            before["unregistered_tile_bg"] != after["unregistered_tile_bg"]
            or before["unregistered_tile_border"] != after["unregistered_tile_border"]
            or before["unregistered_tile_fg"] != after["unregistered_tile_fg"]
        )
        if live_style_changed:
            self._apply_runtime_settings(theme_override=before["theme"] if theme_changed else None)
            self._refresh_runtime_ui()
        elif before["tile_local_only_display"] != after["tile_local_only_display"]:
            self._apply_tile_display_setting()

        if unreg_color_changed:
            for bw in self._editor.all_block_widgets():
                bw.refresh_tile_styles()

        history_color_changed = any(
            before[key] != after[key]
            for key in (
                "history_text_color_dark", "history_text_color_light",
                "history_line_color_dark", "history_line_color_light",
            )
        )
        if history_color_changed:
            styles.reload_history_colors()
            self._side_panel.refresh_history()
            self._refresh_lineage_two_row_view()
            self._refresh_history_map_dialog()

        if nsfw_changed:
            self._apply_nsfw_setting()

        if before["app_icon_path"] != after["app_icon_path"]:
            self._apply_app_icon()

        if theme_changed:
            self._show_status(tr("settings.theme_restart_status"))

    def _reset_history_window_positions(self) -> None:
        """設定画面から履歴マップ/画像ビューアの保存位置をデフォルトに戻す。"""
        _set_setting("history_map_geometry", "")
        _set_setting("history_image_viewer_geometry", "")
        _set_setting("history_image_viewer_open", "0")
        _set_setting("history_image_viewer_history_db", "")
        _set_setting("history_image_viewer_history_id", "")
        if self._history_map_dialog is not None:
            if hasattr(self._history_map_dialog, "reset_window_geometry"):
                self._history_map_dialog.reset_window_geometry()
            if hasattr(self._history_map_dialog, "reset_image_viewer_geometry"):
                self._history_map_dialog.reset_image_viewer_geometry()
        self._show_status(tr("settings.history_windows_reset_done"))

    def _auto_open_invoke_setup_if_needed(self) -> None:
        """モデルまたは生成テンプレートが未取得なら、Invoke セットアップへ誘導する。"""
        has_model = _env_db.fetchone(
            "SELECT 1 FROM models WHERE type='main' AND available=1 LIMIT 1"
        ) is not None
        if has_model and self._has_generation_template():
            return
        self._open_invoke_setup()

    def _open_invoke_setup(self) -> None:
        from ui.invoke_setup_dialog import InvokeSetupDialog
        dlg = InvokeSetupDialog(self._client, self)
        dlg.setup_changed.connect(self._on_invoke_setup_changed)
        dlg.language_changed.connect(self._on_invoke_setup_language_changed)
        dlg.exec()

    def _on_invoke_setup_changed(self) -> None:
        self._model_browser.refresh()
        self._lora_browser.refresh()
        self._populate_base_plan_combo()
        self._populate_model_combo()
        self._apply_model_mode_ui()
        self._update_generation_buttons()

    def _on_invoke_setup_language_changed(self) -> None:
        self._apply_runtime_settings()
        self._refresh_runtime_ui()

    # ── 翻訳（LM Studio）────────────────────────────────

    def _set_all_global_translating(self, translating: bool) -> None:
        """全ブロックの翻訳ボタンを一括で有効/無効にする。
        翻訳中は全ブロックの翻訳ボタンを無効化して多重起動を防ぐ。
        """
        for bw in self._editor.all_block_widgets():
            bw.set_global_translating(translating)

    def _on_translate_requested(self, target, text: str, mode: str = "danboard") -> None:
        """翻訳要求：ブロック内インラインパネルを展開してストリーミング開始（非ブロッキング）。
        target は発火元 BlockWidget が直接渡す（共有状態に依存しない確実な参照）。
        mode: "danboard"=ダンボール語タグ翻訳 / "natural"=自然言語翻訳
        複数行入力（natural モード）の場合は行ごとに順次ワーカーを起動する。
        """
        if not text.strip():
            return
        if self._translate_worker and self._translate_worker.isRunning():
            self._show_status(tr("main.translate_already_running"))
            return

        if not self._translation_lm_configured():
            msg = tr("main.translate_lm_not_configured")
            if target is not None:
                target.show_translate_panel()
                target.show_translate_failure(msg)
            self._show_status(tr("main.translate_failed", error=msg))
            return

        if target is None:
            return
        self._translate_target_bw = target
        self._translate_mode_cache = mode

        # natural モードで複数行の場合は行分割して順次翻訳
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        lines = [ln for ln in normalized.splitlines() if ln.strip()]
        if mode == "natural" and len(lines) > 1:
            self._translate_lines = lines
            self._translate_results = [""] * len(lines)
            self._translate_line_index = 0
            self._translate_has_failure = False
            # パネルを表示してロックし、最初の行を開始
            target.set_translating(True)
            target.show_translate_panel()
            self._set_all_global_translating(True)
            self._editor.refresh_layout()
            self._start_translate_line(lines[0], mode)
            return

        # 単行または danboard モード: 従来通り全体を1回で翻訳
        self._translate_lines = None
        self._translate_results = []
        self._translate_line_index = 0
        self._start_translate_single(target, text, mode)

    def _get_sys_prompt(self, mode: str) -> str:
        """モードに応じたシステムプロンプトを返す。"""
        if mode == "natural":
            sp = (
                self._lm_prompt_window.get_prompt_natural()
                if self._lm_prompt_window is not None
                else ""
            )
            if not sp:
                from ui.lm_prompt_editor import _DEFAULT_NATURAL_PROMPT
                sp = _DEFAULT_NATURAL_PROMPT
        else:
            sp = (
                self._lm_prompt_window.get_prompt()
                if self._lm_prompt_window is not None
                else ""
            )
            if not sp:
                from ui.lm_prompt_editor import _DEFAULT_PROMPT
                sp = _DEFAULT_PROMPT
        return sp

    def _start_translate_single(self, target, text: str, mode: str) -> None:
        """単行翻訳ワーカーを起動し、パネル表示・ロックを行う。"""
        lm_url = _get_setting("lm_endpoint", "http://localhost:1234")
        provider = _get_setting("lm_provider", "lmstudio")
        model  = self._current_lm_model_id()
        try:
            chunk_timeout = float(_get_setting("lm_chunk_timeout", "60"))
        except ValueError:
            chunk_timeout = 60.0
        client = LMClient(base_url=lm_url, chunk_timeout=chunk_timeout, provider=provider)
        status = client.check_connection()
        if not status.ok:
            self._on_translate_failed(status.message)
            return
        self._translate_worker = _TranslateStreamWorker(
            client, text, self._get_sys_prompt(mode), model, parent=self
        )
        self._translate_worker.status_update.connect(target.append_translate_status)
        self._translate_worker.thinking_chunk.connect(target.append_translate_thinking)
        self._translate_worker.translation_done.connect(self._on_translate_done)
        self._translate_worker.failed.connect(self._on_translate_failed)
        target.set_translating(True)
        target.show_translate_panel()
        self._set_all_global_translating(True)
        self._editor.refresh_layout()
        self._translate_worker.start()

    def _start_translate_line(self, line_text: str, mode: str) -> None:
        """複数行翻訳中の1行ワーカーを起動する。パネル表示・ロックは呼び出し元が済ませていること。"""
        target = self._translate_target_bw
        if target is None:
            return
        lm_url = _get_setting("lm_endpoint", "http://localhost:1234")
        provider = _get_setting("lm_provider", "lmstudio")
        model  = self._current_lm_model_id()
        try:
            chunk_timeout = float(_get_setting("lm_chunk_timeout", "60"))
        except ValueError:
            chunk_timeout = 60.0
        client = LMClient(base_url=lm_url, chunk_timeout=chunk_timeout, provider=provider)
        idx    = self._translate_line_index
        if idx == 0:
            status = client.check_connection()
            if not status.ok:
                self._on_translate_failed(status.message)
                return
        total  = len(self._translate_lines or [])
        target.clear_translate_status()
        target.append_translate_status(
            tr("main.translate_line_progress", current=idx + 1, total=total, preview=line_text[:30])
        )
        self._translate_worker = _TranslateStreamWorker(
            client, line_text, self._get_sys_prompt(mode), model, parent=self
        )
        self._translate_worker.status_update.connect(target.append_translate_status)
        self._translate_worker.thinking_chunk.connect(target.append_translate_thinking)
        self._translate_worker.translation_done.connect(self._on_translate_done)
        self._translate_worker.failed.connect(self._on_translate_failed)
        self._translate_worker.start()

    def _on_translate_done(self, result: str) -> None:
        """翻訳完了：単行の場合はパネルを閉じて結果を反映。複数行の場合は次行を起動。"""
        self._translate_worker = None
        result = (result or "").strip()

        # 複数行翻訳モード
        if self._translate_lines is not None:
            target = self._translate_target_bw
            # 失敗（空結果）は原文フォールバック
            src_line = self._translate_lines[self._translate_line_index]
            self._translate_results[self._translate_line_index] = result if result else src_line
            if not result:
                self._translate_has_failure = True  # 空結果 = 失敗行

            self._translate_line_index += 1
            if self._translate_line_index < len(self._translate_lines):
                # まだ行が残っている → 次行を起動
                next_line = self._translate_lines[self._translate_line_index]
                self._start_translate_line(next_line, self._translate_mode_cache)
                return

            # 全行完了: まとめて結果を渡す
            all_results = "\n".join(self._translate_results)
            had_failure = self._translate_has_failure

            # 状態クリア
            self._translate_lines = None
            self._translate_results = []
            self._translate_line_index = 0
            self._translate_target_bw = None
            self._set_all_global_translating(False)
            if target is None:
                return
            self._editor.capture_block_scroll_anchor(target)
            target.set_translating(False)
            target.hide_translate_panel()
            self._editor.refresh_layout()
            if all_results:
                target.set_translate_result(all_results)
                self._editor.restore_block_scroll_anchor()
                if had_failure:
                    target.show_translate_failure(tr("block.translate_partial_failure"))
                    self._show_status(tr("block.translate_partial_failure"))
                else:
                    preview = all_results[:40] + ("…" if len(all_results) > 40 else "")
                    self._show_status(tr("main.translate_done", preview=preview))
            else:
                msg = tr("main.translate_empty_result")
                target.show_translate_failure(msg)
                self._editor.restore_block_scroll_anchor()
                self._show_status(tr("main.translate_failed", error=msg))
            return

        # 単行翻訳モード（従来通り）
        target = self._translate_target_bw
        self._translate_target_bw = None
        self._set_all_global_translating(False)
        if target is None:
            return

        self._editor.capture_block_scroll_anchor(target)
        target.set_translating(False)
        target.hide_translate_panel()
        self._editor.refresh_layout()
        if result:
            target.set_translate_result(result)
            self._editor.restore_block_scroll_anchor()
            preview = result[:40] + ("…" if len(result) > 40 else "")
            self._show_status(tr("main.translate_done", preview=preview))
        else:
            msg = tr("main.translate_empty_result")
            target.show_translate_failure(msg)
            self._editor.restore_block_scroll_anchor()
            self._show_status(tr("main.translate_failed", error=msg))

    def _on_translate_failed(self, msg: str) -> None:
        """翻訳失敗：単行の場合はパネルを閉じてエラー表示。複数行の場合は原文で埋めて続行。"""
        self._translate_worker = None

        # 複数行翻訳モード: 失敗行を原文で埋めて次行に進む
        if self._translate_lines is not None:
            src_line = self._translate_lines[self._translate_line_index]
            self._translate_results[self._translate_line_index] = src_line  # 原文フォールバック
            self._translate_has_failure = True
            self._translate_line_index += 1
            if self._translate_line_index < len(self._translate_lines):
                next_line = self._translate_lines[self._translate_line_index]
                self._start_translate_line(next_line, self._translate_mode_cache)
                return
            # 全行処理済み（失敗あり）
            all_results = "\n".join(self._translate_results)
            target = self._translate_target_bw
            self._translate_lines = None
            self._translate_results = []
            self._translate_line_index = 0
            self._translate_target_bw = None
            self._set_all_global_translating(False)
            if target is not None:
                target.set_translating(False)
                target.hide_translate_panel()
                self._editor.refresh_layout()
                if all_results:
                    target.set_translate_result(all_results)
                    target.show_translate_failure(tr("block.translate_partial_failure"))
            self._show_status(tr("block.translate_partial_failure"))
            return

        # 単行翻訳モード（従来通り）
        target = self._translate_target_bw
        self._translate_target_bw = None
        self._set_all_global_translating(False)
        if target is not None:
            target.set_translating(False)
            target.show_translate_failure(msg)
        self._show_status(tr("main.translate_failed", error=msg))

    def _on_translate_cancel(self) -> None:
        """ユーザーがキャンセルボタンを押した：ワーカーを停止してパネルを閉じる。"""
        target = self._translate_target_bw
        self._translate_target_bw = None
        if self._translate_worker and self._translate_worker.isRunning():
            self._translate_worker.cancel_and_wait()
        self._translate_worker = None
        # 複数行翻訳状態もクリア
        self._translate_lines = None
        self._translate_results = []
        self._translate_line_index = 0
        self._translate_has_failure = False
        self._set_all_global_translating(False)
        if target is not None:
            target.hide_translate_panel()
            target.set_translating(False)
            self._editor.refresh_layout()

    def _refresh_lm_models(self) -> None:
        """LM Studio からモデル一覧を取得してコンボボックスに反映する。"""
        lm_url = _get_setting("lm_endpoint", "http://localhost:1234")
        provider = _get_setting("lm_provider", "lmstudio")
        try:
            client = LMClient(base_url=lm_url, provider=provider)
            models = client.models_list_detailed()
            current = self._current_lm_model_id()
            notes = self._lm_notes()
            self._lm_model_meta = {}
            self._lm_model_combo.blockSignals(True)
            self._lm_model_combo.clear()
            for m in models:
                mid = m.get("key") or m.get("id") or ""
                if mid:
                    self._lm_model_meta[mid] = m
                    note = notes.get(mid, "")
                    self._lm_model_combo.addItem(self._format_lm_combo_popup_label(m, note), mid)
                    self._set_lm_combo_note_data(
                        self._lm_model_combo.count() - 1,
                        note,
                    )
            # 以前の選択を復元
            idx = self._lm_model_combo.findData(current)
            if idx >= 0:
                self._lm_model_combo.setCurrentIndex(idx)
            elif current:
                self._lm_model_combo.setEditText(current)
            self._lm_model_combo.blockSignals(False)
            self._sync_lm_combo_display_label()
            self._lm_model_combo.adjust_to_current_text()
            self._load_lm_model_note()
            # blockSignals 中は currentTextChanged が発火しないため明示的に保存
            self._save_lm_model_selection(self._current_lm_model_id())
        except Exception:
            pass  # LM Studio が起動していない場合は静かに無視

    @staticmethod
    def _format_lm_size(size_bytes) -> str:
        try:
            size = float(size_bytes)
        except (TypeError, ValueError):
            return ""
        if size <= 0:
            return ""
        gib = size / (1024 ** 3)
        return f"{gib:.1f}GB"

    def _format_lm_model_label(self, model: dict) -> str:
        mid = model.get("key") or model.get("id") or ""
        label = model.get("display_name") or mid
        details: list[str] = []
        params = model.get("params_string")
        if params:
            details.append(str(params))
        quant = model.get("quantization")
        if isinstance(quant, dict) and quant.get("name"):
            details.append(str(quant["name"]))
        elif isinstance(quant, str):
            details.append(quant)
        size = self._format_lm_size(model.get("size_bytes"))
        if size:
            details.append(size)
        if details:
            label = f"{label} [{', '.join(details)}]"
        return label

    def _format_lm_combo_popup_label(self, model: dict, note: str) -> str:
        label = self._format_lm_model_label(model)
        note = (note or "").strip()
        return f"{label}\n  {note}" if note else label

    def _current_lm_model_id(self) -> str:
        if not hasattr(self, "_lm_model_combo"):
            return ""
        data = self._lm_model_combo.currentData()
        if isinstance(data, str) and data:
            return data
        return self._lm_model_combo.currentText().strip()

    def _translation_lm_configured(self) -> bool:
        return bool(self._current_lm_model_id().strip())

    def _lm_notes(self) -> dict:
        raw = _get_setting("lm_model_notes_json", "{}")
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _set_lm_combo_note_data(self, idx: int, note: str) -> None:
        if idx < 0:
            return
        note = note or ""
        model = self._lm_model_combo.itemData(idx)
        meta = self._lm_model_meta.get(model, {"id": model})
        base_label = self._format_lm_model_label(meta)
        self._lm_model_combo.setItemText(idx, self._format_lm_combo_popup_label(meta, note))
        self._lm_model_combo.setItemData(idx, base_label, _COMBO_BASE_LABEL_ROLE)
        self._lm_model_combo.setItemData(idx, note, _COMBO_NOTE_ROLE)
        if note:
            height = self._lm_model_combo.fontMetrics().height() * 2 + 10
            self._lm_model_combo.setItemData(idx, QSize(0, height), Qt.ItemDataRole.SizeHintRole)
        else:
            self._lm_model_combo.setItemData(idx, None, Qt.ItemDataRole.SizeHintRole)

    def _sync_lm_combo_display_label(self) -> None:
        if not hasattr(self, "_lm_model_combo") or not self._lm_model_combo.isEditable():
            return
        idx = self._lm_model_combo.currentIndex()
        if idx < 0 or self._lm_model_combo.lineEdit() is None:
            return
        label = self._lm_model_combo.itemData(idx, _COMBO_BASE_LABEL_ROLE)
        if not label:
            label = self._lm_model_combo.currentText().splitlines()[0].strip()
        self._lm_model_combo.lineEdit().blockSignals(True)
        self._lm_model_combo.lineEdit().setText(label)
        self._lm_model_combo.lineEdit().setCursorPosition(0)
        self._lm_model_combo.lineEdit().blockSignals(False)

    def _queue_lm_combo_display_sync(self, *_args) -> None:
        QTimer.singleShot(0, self._sync_lm_combo_display_label)
        QTimer.singleShot(30, self._sync_lm_combo_display_label)

    def _load_lm_model_note(self) -> None:
        if not hasattr(self, "_lm_note_edit"):
            return
        note = self._lm_notes().get(self._current_lm_model_id(), "")
        self._lm_note_edit.blockSignals(True)
        self._lm_note_edit.setText(note)
        self._lm_note_edit.blockSignals(False)

    def _save_lm_model_note(self, text: str) -> None:
        model = self._current_lm_model_id()
        if not model:
            return
        notes = self._lm_notes()
        text = text.strip()
        if text:
            notes[model] = text
        else:
            notes.pop(model, None)
        _app_db.execute(
            "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
            ("lm_model_notes_json", json.dumps(notes, ensure_ascii=False)),
        )
        self._update_lm_combo_note_label(model, text)

    def _update_lm_combo_note_label(self, model: str, note: str) -> None:
        idx = self._lm_model_combo.findData(model)
        if idx < 0:
            return
        meta = self._lm_model_meta.get(model, {"id": model})
        self._set_lm_combo_note_data(idx, note)
        self._lm_model_combo.view().doItemsLayout()
        if idx == self._lm_model_combo.currentIndex():
            self._sync_lm_combo_display_label()
            self._lm_model_combo.adjust_to_current_text()

    def _on_lm_model_changed(self, _index: int) -> None:
        model = self._current_lm_model_id()
        if model:
            self._save_lm_model_selection(model)
        self._sync_lm_combo_display_label()
        self._load_lm_model_note()

    def _save_lm_model_selection(self, model: str) -> None:
        """翻訳LMコンボの現在値を即保存し、タイル編集ダイアログ側にも反映させる。"""
        self._lm_model_combo.adjust_to_current_text()
        _app_db.execute(
            "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
            ("lm_translate_model", self._current_lm_model_id() or (model or "").strip()),
        )

    def _toggle_lm_prompt_editor(self) -> None:
        """翻訳プロンプト編集ウィンドウを表示/非表示トグルする。"""
        if self._lm_prompt_window is None:
            from ui.lm_prompt_editor import LMPromptEditorWindow
            self._lm_prompt_window = LMPromptEditorWindow(parent=self)
        self._lm_prompt_window.toggle()

    # ── 接続確認（非同期） ────────────────────────────────

    def _check_connection(self) -> None:
        """接続確認をバックグラウンドワーカーに委譲する（UI をブロックしない）。"""
        if self._conn_worker and self._conn_worker.isRunning():
            return  # 前の確認がまだ実行中 → スキップ
        self._conn_worker = _ConnCheckWorker(self._client, parent=self)
        self._conn_worker.conn_ok.connect(self._on_conn_ok)
        self._conn_worker.conn_ng.connect(self._on_conn_ng)
        self._conn_worker.start()

    def _on_conn_ok(self, pending: int, in_prog: int, completed: int) -> None:
        self._status_conn.setText(tr("main.status_connected"))
        self._status_conn.setStyleSheet(f"color: {GREEN};")
        self._status_queue.setText(
            tr("main.status_queue", pending=pending, in_prog=in_prog, completed=completed)
        )

    def _on_conn_ng(self) -> None:
        self._status_conn.setText(tr("main.status_disconnected"))
        self._status_conn.setStyleSheet(f"color: {RED};")
        self._status_queue.setText("")

    # ── 履歴同期（Invoke → DB）非同期 ─────────────────────

    def _sync_history(self) -> None:
        """
        履歴バックフィル同期をバックグラウンドワーカーに委譲する（UI をブロックしない）。
        前のワーカーがまだ実行中の場合はスキップする（タイマー多重起動防止）。
        """
        self._check_external_inbox()
        if self._hist_worker and self._hist_worker.isRunning():
            return
        from db.connections import get_active_history_name
        self._hist_worker = _HistorySyncWorker(
            self._client, get_active_history_name(), parent=self,
        )
        self._hist_worker.synced.connect(self._on_hist_sync_done)
        self._hist_worker.copy_failed.connect(self._on_copy_failed)
        self._hist_worker.generation_aborted.connect(self._on_generation_aborted)
        self._hist_worker.start()

    def _on_hist_sync_done(self, new_count: int, changed_gen_ids: list) -> None:
        """履歴同期ワーカー完了時: UI を更新する（メインスレッドで実行される）。"""
        # 同期中にアクティブ履歴が切り替わった場合、gen_id は別履歴DBのものなので
        # 現在の右ペインには適用しない（データは正しいDBに書き込み済み）。
        from db.connections import get_active_history_name
        worker = self._hist_worker
        if worker is not None and worker.history_name != get_active_history_name():
            return
        if changed_gen_ids:
            self._side_panel.refresh_history_items([int(gid) for gid in changed_gen_ids])
            # 親・継承権者カード／履歴マップのサムネが画像到着で埋まるため更新
            self._refresh_lineage_card()
            self._refresh_history_map_dialog()
        if new_count > 0:
            # 画像が届いている間はポーリング期限（5分）を延長する。
            # 大量 Count の逐次生成では全体が5分を超えるため、進捗があれば打ち切らない
            if self._poll_timer.isActive():
                self._poll_count = 0
            self._show_status(tr("main.status_sync_ok", count=new_count))

    def _check_external_inbox(self) -> None:
        """外部受信箱を確認し、ユーザー確認後に通常履歴へ取り込む。"""
        try:
            from core import external_inbox
            rows = external_inbox.pending_rows()
        except Exception as exc:
            self._show_status(tr("main.external_inbox_check_fail", error=exc), error=True)
            return
        if not rows:
            return

        preview = external_inbox.preview_text(rows)
        reply = QMessageBox.question(
            self,
            tr("main.external_inbox_title"),
            tr("main.external_inbox_msg", count=len(rows), preview=preview),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            self._show_status(tr("main.external_inbox_deferred", count=len(rows)))
            return

        result = external_inbox.import_pending()
        self._side_panel.refresh_history()
        if result.failed:
            QMessageBox.warning(
                self,
                tr("main.external_inbox_partial_title"),
                tr(
                    "main.external_inbox_partial_msg",
                    imported=result.imported,
                    failed=result.failed,
                ),
            )
        else:
            self._show_status(tr("main.external_inbox_imported", count=result.imported))

    def _purge_aborted_generations(self, gen_ids: list[int], history_name: str) -> None:
        """
        generations 行と系譜ノードを削除する（純DB処理・UI更新は呼び出し側が行う）。

        系譜ノードは子を親へ付け替えてから削除する。current ポインタが削除ノードを
        指していた場合は親へ移す（親がなければクリア）。
        """
        hdb = _history_db.for_history(history_name)
        for gid in gen_ids:
            gid = int(gid)
            hdb.execute("DELETE FROM generations WHERE id=?", (gid,))
            self._remove_editor_history_node_relink(history_name, gid)

    def _remove_editor_history_node_relink(self, history_name: str, gid: int) -> None:
        """
        系譜ノードを1つ除去する（純DB処理）。子は本人の親へ直系接続し、
        current ポインタが本人を指していた場合は親へ移す（親がなければクリア）。
        """
        import db.hmap_db as _hmap_db
        node = _hmap_db.fetchone(
            "SELECT parent_db, parent_id FROM editor_history_nodes "
            "WHERE history_db=? AND history_id=?",
            (history_name, gid),
        )
        if node is None:
            return
        _hmap_db.execute(
            "UPDATE editor_history_nodes SET parent_db=?, parent_id=? "
            "WHERE parent_db=? AND parent_id=?",
            (node["parent_db"], node["parent_id"], history_name, gid),
        )
        _hmap_db.execute(
            "DELETE FROM editor_history_nodes WHERE history_db=? AND history_id=?",
            (history_name, gid),
        )
        if self._current_editor_history_node() == (history_name, gid):
            if node["parent_db"] is not None:
                _set_setting("editor_history_current_history_db", node["parent_db"])
                _set_setting("editor_history_current_history_id", str(node["parent_id"]))
            else:
                _set_setting("editor_history_current_history_db", "")
                _set_setting("editor_history_current_history_id", "")

    def _finalize_transient_generations(self) -> None:
        """
        一時状態（キュー追加中・画像待ち）のまま残った generations 行を破棄する。

        生成の一時状態はこのプロセスのセッションにのみ紐づく。セッション終了までに
        確定（画像取得）へ到達しなかった行は破棄し、「セッション開始時点で一時状態の
        行は存在しない」という不変条件を保つ。本来は終了処理で完結する。起動時の
        呼び出しはクラッシュ等で終了処理が走らなかった場合の整合性修復であり、
        同じ不変条件を適用するだけ（初回起動はテーブルが空なので自明に何もしない）。

        対象は「画像情報が一切ない行」のみ。外部インポート行（generation_mode あり）
        と画像を1枚でも持つ行は対象外。ネットワークアクセスは行わない。
        """
        from db.connections import list_history_names
        for name in list_history_names():
            try:
                hdb = _history_db.for_history(name)
                rows = hdb.fetchall(
                    """SELECT id FROM generations
                       WHERE deleted_at IS NULL
                         AND invoke_image_name IS NULL
                         AND local_path IS NULL
                         AND image_path IS NULL
                         AND thumbnail_data IS NULL
                         AND generation_mode IS NULL
                         AND NOT EXISTS (SELECT 1 FROM generation_images gi
                                         WHERE gi.generation_id = generations.id)"""
                )
            except Exception:
                continue  # 開けないDBはスキップ（ここで起動/終了を止めない）
            ids = [int(r["id"]) for r in rows]
            if ids:
                self._purge_aborted_generations(ids, name)

    def _on_generation_aborted(self, aborted_ids: list, history_name: str | None = None) -> None:
        """
        全アイテムがキャンセル/失敗（接続エラー含む）になった generation を処理する。

        ポーリング解除 → 行・系譜ノードの削除 → UI更新。
        """
        if not aborted_ids:
            return
        for gid in aborted_ids:
            try:
                self._pending_gen_ids.remove(gid)
            except ValueError:
                pass
        if not self._pending_gen_ids:
            self._poll_timer.stop()

        from db.connections import get_active_history_name
        if history_name is None:
            history_name = get_active_history_name()

        current_before = self._current_editor_history_node()
        self._purge_aborted_generations([int(g) for g in aborted_ids], history_name)
        if self._current_editor_history_node() != current_before:
            self._refresh_lineage_card()

        deleted_keys = {(history_name, int(g)) for g in aborted_ids}
        for key in deleted_keys:
            self._clear_history_map_opened_node_if_removed(key)
        if self._history_map_dialog_focus in deleted_keys:
            self._history_map_dialog_focus = self._current_editor_history_node()
        self._refresh_history_map_dialog()
        if history_name == get_active_history_name():
            self._side_panel.refresh_history()
        self._update_cancel_plan_btn()
        self._show_status(
            tr("main.status_aborted_removed", count=len(aborted_ids)), error=True
        )

    def _on_copy_failed(self, msg: str) -> None:
        """画像コピー失敗時: ポーリングを止めてエラーダイアログを表示する。"""
        self._poll_timer.stop()
        self._pending_gen_ids.clear()
        self._side_panel.refresh_history()
        QMessageBox.critical(
            self,
            tr("main.copy_fail_title"),
            tr("main.copy_fail_msg", msg=msg),
        )

    # ── 履歴ロード ───────────────────────────────────────

    def _confirm_load(self) -> bool:
        """ロード前確認。空なら即 True、未編集なら True、編集済みは確認ダイアログ。"""
        if self._editor.is_empty():
            return True
        if not self._editor_dirty:
            return True
        reply = QMessageBox.question(
            self, tr("main.overwrite_title"),
            tr("main.overwrite_msg"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    def _load_generation(self, generation_id: int) -> None:
        """履歴からプロンプトのみをロードする。"""
        self._load_generation_impl(generation_id, include_params=False)

    def _full_load_generation(self, generation_id: int) -> None:
        """右クリック「全てロード」用エントリポイント。"""
        self._load_generation_impl(generation_id, include_params=True)

    def _load_generation_impl(
        self,
        generation_id: int,
        *,
        include_params: bool,
        apply_seed: bool = True,
    ) -> bool:
        if not self._confirm_load():
            return False

        doc = PromptDocument.load_from_db(generation_id)
        if doc:
            self._apply_doc_respecting_locks(doc)
            self._editor_dirty = False
        else:
            self._show_status(tr("main.status_load_fail", id=generation_id), error=True)
            return False

        if not include_params:
            self._activate_editor_history_node(generation_id)
            self._show_status(tr("main.status_loaded", id=generation_id))
            return True

        row = _history_db.fetchone(
            "SELECT loras_json, invoke_key, model_name, seed, steps, cfg_scale, "
            "       scheduler, width, height, template_id "
            "FROM generations WHERE id=?",
            (generation_id,),
        )
        if not row:
            self._activate_editor_history_node(generation_id)
            self._show_status(tr("main.status_loaded", id=generation_id))
            return True

        # LoRA（履歴にLoRAがない場合は空にする）
        loras: list = []
        if row["loras_json"]:
            try:
                loras = json.loads(row["loras_json"])
            except Exception:
                pass
        self._lora_bar.set_loras(loras)
        self._lora_browser.set_selected_keys(self._lora_bar.get_selected_keys())
        self._relink_lora_trigger_words(loras)

        # レビューメモをエディタに反映
        memo_row = _history_db.fetchone(
            "SELECT review_text FROM image_reviews WHERE generation_id=?", (generation_id,))
        self._editor.set_memo((memo_row["review_text"] or "") if memo_row else "")

        # モデル: invoke_key 優先、なければ model_name でDB検索
        resolved_base: str = ""
        resolved_model_name: str = ""
        if row["invoke_key"]:
            key = row["invoke_key"]
            mrow = _env_db.fetchone("SELECT name, base FROM models WHERE invoke_key=?", (key,))
            resolved_model_name = (mrow["name"] or key) if mrow else key
            resolved_base = (mrow["base"] or "") if mrow else ""
            self._selected_model_key = key
        elif row["model_name"]:
            mrow = _env_db.fetchone(
                "SELECT invoke_key, name, base FROM models WHERE name=? AND available=1",
                (row["model_name"],),
            )
            if not mrow:
                mrow = _env_db.fetchone(
                    "SELECT invoke_key, name, base FROM models WHERE name=?",
                    (row["model_name"],),
                )
            if mrow:
                self._selected_model_key = mrow["invoke_key"]
                resolved_model_name = mrow["name"]
                resolved_base = mrow["base"] or ""
            else:
                resolved_model_name = row["model_name"]

        # ベース変更時: スケジューラー等 UI を切り替え
        if resolved_base and resolved_base != self._current_base:
            self._current_base = resolved_base
            self._apply_base_ui(resolved_base)

        # テンプレート解決（履歴の template_id → ベースのデフォルト → ベース内の最初の1件）
        if resolved_base:
            hist_tid = row["template_id"]
            trow = None
            if hist_tid:
                trow = _env_db.fetchone(
                    "SELECT id, name FROM templates WHERE id=? AND base=?",
                    (hist_tid, resolved_base),
                )
            if not trow:
                trow = _env_db.fetchone(
                    "SELECT id, name FROM templates WHERE base=? AND is_base_default=1",
                    (resolved_base,),
                )
            if not trow:
                trow = _env_db.fetchone(
                    "SELECT id, name FROM templates WHERE base=? ORDER BY id ASC LIMIT 1",
                    (resolved_base,),
                )
            if trow:
                self._current_template_id   = trow["id"]
                self._current_template_name = trow["name"]
            else:
                self._current_template_id   = None
                self._current_template_name = ""

        self._update_model_label(resolved_model_name)
        self._apply_negative_prompt_ui()

        # 生成パラメータ（DBに値がある項目のみ反映）
        if apply_seed and row["seed"] is not None and int(row["seed"]) >= 0:
            self._seed_spin.setValue(max(0, min(2_147_483_647, int(row["seed"]))))
            self._seed_random_cb.setChecked(False)
        if row["steps"]:
            self._steps_spin.setValue(int(row["steps"]))
        if row["cfg_scale"]:
            self._cfg_spin.setValue(float(row["cfg_scale"]))
        if row["scheduler"]:
            idx = self._sched_combo.findText(str(row["scheduler"]))
            if idx >= 0:
                self._sched_combo.setCurrentIndex(idx)
        if row["width"]:
            self._width_spin.setValue(int(row["width"]))
        if row["height"]:
            self._height_spin.setValue(int(row["height"]))

        self._activate_editor_history_node(generation_id)
        self._show_status(tr("main.status_full_loaded", id=generation_id))
        return True

    def _record_editor_history_node(self, gen_id: int) -> None:
        import db.hmap_db as _hmap_db
        from db.connections import get_active_history_name
        active_db = get_active_history_name()
        parent = self._current_editor_history_node()
        parent_db = parent[0] if parent else None
        parent_id = parent[1] if parent else None
        _hmap_db.record_node(active_db, gen_id, parent_db, parent_id)
        if parent is None:
            self._set_current_editor_history_node(active_db, gen_id)
        self._history_map_dialog_focus = (active_db, gen_id)
        self._refresh_history_map_dialog()
        self._side_panel.refresh_history_items([int(gen_id)])

    def _seed_editor_history_root(self, gen_id: int) -> None:
        row = _history_db.fetchone("SELECT id FROM generations WHERE id=?", (gen_id,))
        if not row:
            return
        import db.hmap_db as _hmap_db
        from db.connections import get_active_history_name
        active_db = get_active_history_name()
        _hmap_db.record_node(active_db, gen_id, None, None)
        self._set_current_editor_history_node(active_db, gen_id)
        self._history_map_dialog_focus = (active_db, gen_id)
        self._refresh_history_map_dialog()
        self._side_panel.refresh_history_items([int(gen_id)])

    def _activate_editor_history_node(self, gen_id: int) -> None:
        import db.hmap_db as _hmap_db
        from db.connections import get_active_history_name
        active_db = get_active_history_name()
        if _hmap_db.fetchone(
            "SELECT 1 FROM editor_history_nodes WHERE history_db=? AND history_id=?",
            (active_db, gen_id),
        ):
            self._set_current_editor_history_node(active_db, gen_id)
            self._history_map_dialog_focus = (active_db, gen_id)
            self._refresh_history_map_dialog()
            self._side_panel.refresh_history_items([int(gen_id)])
            return
        self._seed_editor_history_root(gen_id)

    def _current_editor_history_node(self) -> tuple[str, int] | None:
        db = _get_setting("editor_history_current_history_db", "")
        id_str = _get_setting("editor_history_current_history_id", "")
        if db and id_str.isdigit():
            return (db, int(id_str))
        return None

    def _set_current_editor_history_node(self, history_db: str, gen_id: int) -> None:
        _set_setting("editor_history_current_history_db", history_db)
        _set_setting("editor_history_current_history_id", str(gen_id))
        self._refresh_lineage_card()

    def _lineage_row_item(self, history_db: str, gen_id: int) -> dict | None:
        from pathlib import Path
        from db.connections import history_db_path
        if not history_db_path(history_db).exists():
            return None
        try:
            row = _history_db.for_history(history_db).fetchone(
                "SELECT thumbnail_data, local_path, deleted_at FROM generations WHERE id=?",
                (gen_id,),
            )
        except Exception:
            return None
        if row is None or row["deleted_at"] is not None:
            return None
        preview_pix = None
        local_path = str(row["local_path"] or "")
        if local_path and Path(local_path).exists():
            pix = QPixmap(local_path)
            if not pix.isNull():
                preview_pix = pix
        return {
            "history_db": history_db,
            "history_id": int(gen_id),
            "thumbnail_data": row["thumbnail_data"],
            "preview_pixmap": preview_pix,
        }

    def _refresh_lineage_two_row_view(self) -> None:
        view = getattr(self._editor, "parent_child_map", None)
        if view is None:
            return
        current = self._current_editor_history_node()
        if current is None:
            view.rebuild([], None, None)
            return
        from db.connections import get_active_history_name
        nodes = self._fetch_editor_history_nodes(current)
        view.set_view_restricted(self._history_map_view_root is not None)
        view.set_active_history_name(get_active_history_name())
        if current is not None and hasattr(view, "set_history_background_color"):
            view.set_history_background_color(self._history_background_color(*current))
        if current is not None and hasattr(view, "set_history_text_color"):
            view.set_history_text_color(self._history_text_color(*current))
        if current is not None and hasattr(view, "set_history_line_color"):
            view.set_history_line_color(self._history_line_color(*current))
        view.rebuild(nodes, current, None)

    def _scroll_lineage_strip_to_current(self) -> None:
        view = getattr(self._editor, "parent_child_map", None)
        if view is not None:
            view.center_on_current()

    def _center_visible_history_maps(self) -> None:
        if self._history_map_dialog is not None and self._history_map_dialog.isVisible():
            self._history_map_dialog.scroll_to_current()
        view = getattr(self._editor, "parent_child_map", None)
        if view is not None:
            view.center_on_current()

    def _restore_center_history_map_view_state(self) -> None:
        view = getattr(self._editor, "parent_child_map", None)
        if view is not None and hasattr(view, "restore_saved_view_state"):
            view.restore_saved_view_state()

    def _history_root_key(self, history_db: str, gen_id: int) -> tuple[str, int]:
        try:
            import db.hmap_db as _hmap_db
            root = _hmap_db.find_root(history_db, int(gen_id))
            return root or (history_db, int(gen_id))
        except Exception:
            return (history_db, int(gen_id))

    def _history_map_is_single_node(self, history_db: str, gen_id: int) -> bool:
        try:
            import db.hmap_db as _hmap_db
            node = _hmap_db.fetchone(
                "SELECT parent_db FROM editor_history_nodes "
                "WHERE history_db=? AND history_id=?",
                (history_db, int(gen_id)),
            )
            if node is None or node["parent_db"] is not None:
                return False
            child = _hmap_db.fetchone(
                "SELECT 1 FROM editor_history_nodes "
                "WHERE parent_db=? AND parent_id=? LIMIT 1",
                (history_db, int(gen_id)),
            )
            return child is None
        except Exception:
            return False

    def _history_background_setting_key(self, history_db: str, gen_id: int) -> str:
        root_db, root_id = self._history_root_key(history_db, gen_id)
        safe_db = "".join(ch if ch.isalnum() else "_" for ch in root_db)
        return f"history_bg_color_{safe_db}_{root_id}"

    def _auto_history_background_color(self, history_db: str, gen_id: int) -> str:
        root_db, root_id = self._history_root_key(history_db, gen_id)
        digest = hashlib.sha1(f"{root_db}:{root_id}".encode("utf-8")).hexdigest()
        hue = int(digest[:8], 16) % 360
        # 暗色UIで読める、かつ似にくい彩度/明度に寄せる。
        from PySide6.QtGui import QColor
        color = QColor()
        color.setHsv(hue, 95, 78)
        return color.name()

    def _history_background_color(self, history_db: str, gen_id: int) -> str:
        if self._history_map_is_single_node(history_db, gen_id):
            return SURFACE0
        key = self._history_background_setting_key(history_db, gen_id)
        color = _get_setting(key, "")
        if color:
            return color
        color = self._auto_history_background_color(history_db, gen_id)
        _set_setting(key, color)
        return color

    def _change_history_background_color(self, history_db: str, gen_id: int) -> None:
        from PySide6.QtWidgets import QColorDialog
        from PySide6.QtGui import QColor
        current = QColor(self._history_background_color(history_db, gen_id))
        chosen = QColorDialog.getColor(current, self, tr("history_map.history_bg_color_title"))
        if not chosen.isValid():
            return
        _set_setting(self._history_background_setting_key(history_db, gen_id), chosen.name())
        self._refresh_history_stack_buttons()
        self._side_panel.refresh_history()
        self._refresh_history_map_dialog()
        # 画像ウィンドウの背景も同じ共有色なので即同期する。
        self._apply_image_viewer_background()

    # ── 履歴の文字色 / 系統線色（ツリー単位の上書き。未設定はテーマ別の設定既定）──
    def _history_text_color_setting_key(self, history_db: str, gen_id: int) -> str:
        root_db, root_id = self._history_root_key(history_db, gen_id)
        safe_db = "".join(ch if ch.isalnum() else "_" for ch in root_db)
        return f"history_text_color_{safe_db}_{root_id}"

    def _history_line_color_setting_key(self, history_db: str, gen_id: int) -> str:
        root_db, root_id = self._history_root_key(history_db, gen_id)
        safe_db = "".join(ch if ch.isalnum() else "_" for ch in root_db)
        return f"history_line_color_{safe_db}_{root_id}"

    def _history_text_color(self, history_db: str, gen_id: int) -> str:
        """ツリー個別の上書き > 設定のテーマ別既定(styles.HISTORY_TEXT)。"""
        color = _get_setting(self._history_text_color_setting_key(history_db, gen_id), "")
        return color or styles.HISTORY_TEXT

    def _history_line_color(self, history_db: str, gen_id: int) -> str:
        color = _get_setting(self._history_line_color_setting_key(history_db, gen_id), "")
        return color or styles.HISTORY_LINE

    def _change_history_text_color(self, history_db: str, gen_id: int) -> None:
        from PySide6.QtWidgets import QColorDialog
        from PySide6.QtGui import QColor
        current = QColor(self._history_text_color(history_db, gen_id))
        chosen = QColorDialog.getColor(current, self, tr("history_map.history_text_color_title"))
        if not chosen.isValid():
            return
        _set_setting(self._history_text_color_setting_key(history_db, gen_id), chosen.name())
        self._side_panel.refresh_history()
        self._refresh_lineage_two_row_view()
        self._refresh_history_map_dialog()

    def _change_history_line_color(self, history_db: str, gen_id: int) -> None:
        from PySide6.QtWidgets import QColorDialog
        from PySide6.QtGui import QColor
        current = QColor(self._history_line_color(history_db, gen_id))
        chosen = QColorDialog.getColor(current, self, tr("history_map.history_line_color_title"))
        if not chosen.isValid():
            return
        _set_setting(self._history_line_color_setting_key(history_db, gen_id), chosen.name())
        self._refresh_lineage_two_row_view()
        self._refresh_history_map_dialog()

    def _history_stack_items(self) -> list[dict]:
        try:
            raw = json.loads(_get_setting("history_current_stack", "[]") or "[]")
        except Exception:
            raw = []
        items: list[dict] = []
        for item in raw:
            try:
                hdb = str(item["history_db"])
                hid = int(item["history_id"])
            except Exception:
                continue
            items.append({
                "history_db": hdb,
                "history_id": hid,
                "color": self._history_background_color(hdb, hid),
            })
        return items[-5:]

    def _save_history_stack_items(self, items: list[dict]) -> None:
        slim = [
            {"history_db": str(item["history_db"]), "history_id": int(item["history_id"])}
            for item in items[-5:]
        ]
        _set_setting("history_current_stack", json.dumps(slim, ensure_ascii=False))

    def _push_history_stack(self, history_db: str, gen_id: int) -> None:
        key = (history_db, int(gen_id))
        items = [
            item for item in self._history_stack_items()
            if (item["history_db"], int(item["history_id"])) != key
        ]
        items.append({
            "history_db": history_db,
            "history_id": int(gen_id),
            "color": self._history_background_color(history_db, int(gen_id)),
        })
        items = items[-5:]
        self._save_history_stack_items(items)
        self._refresh_history_stack_buttons()

    def _jump_to_history_stack(self, history_db: str, gen_id: int) -> None:
        self._history_map_dialog_focus = (history_db, int(gen_id))
        self._history_map_opened_node = (history_db, int(gen_id))
        self._show_history_map_node_preview(history_db, int(gen_id), activate_viewer=True)
        self._jump_to_editor_history_node(history_db, int(gen_id), from_map=True)
        QTimer.singleShot(0, self._center_visible_history_maps)
        QTimer.singleShot(50, self._center_visible_history_maps)

    def _clear_history_stack(self) -> None:
        """現在地スタックを空にする（スタッククリアボタン）。"""
        self._save_history_stack_items([])
        self._refresh_history_stack_buttons()

    def _refresh_history_stack_buttons(self) -> None:
        if hasattr(self._editor, "set_history_stack_buttons"):
            self._editor.set_history_stack_buttons(self._history_stack_items())

    # ── 系譜ナビ（親カード／開祖ボタン） ─────────────────

    def _refresh_lineage_card(self) -> None:
        """中央ペインの親カード＋継承権者カードを現在の系譜状態に合わせて更新する。"""
        self._refresh_lineage_two_row_view()
        card = getattr(self._editor, "lineage_card", None)
        heir_card = getattr(self._editor, "lineage_heir_card", None)
        if card is None:
            return
        self._lineage_parent_key: tuple[str, int] | None = None
        # 継承権者の兄弟リスト（◀▶切替用キャッシュ）と現在順位
        self._lineage_heir_siblings: list[tuple[str, int]] = []
        self._lineage_heir_rank: int = 0

        current = self._current_editor_history_node()
        if current is None:
            card.set_none()
            if heir_card is not None:
                heir_card.set_none()
            return

        import db.hmap_db as _hmap_db
        row = _hmap_db.fetchone(
            "SELECT parent_db, parent_id FROM editor_history_nodes "
            "WHERE history_db=? AND history_id=?",
            current,
        )
        if row is None or row["parent_db"] is None:
            # 開祖（または未記録ノード）: 親なし。継承権者は current のみ（切替なし）
            card.set_root()
            self._update_heir_card(heir_card, current, [current])
            return

        parent_db = str(row["parent_db"])
        parent_id = int(row["parent_id"])

        from db.connections import history_db_path
        gen_row = None
        if history_db_path(parent_db).exists():
            try:
                gen_row = _history_db.for_history(parent_db).fetchone(
                    "SELECT thumbnail_data FROM generations WHERE id=?",
                    (parent_id,),
                )
            except Exception:
                gen_row = None
        if gen_row is None:
            card.set_missing(parent_id)
        else:
            card.set_parent(parent_id, gen_row["thumbnail_data"])
            self._lineage_parent_key = (parent_db, parent_id)

        # ── 継承権者: 同じ親を持つ兄弟（#番号昇順 = 継承順位）────
        siblings: list[tuple[str, int]] = []
        for sib in _hmap_db.fetchall(
            "SELECT history_db, history_id FROM editor_history_nodes "
            "WHERE parent_db=? AND parent_id=? ORDER BY history_id ASC",
            (parent_db, parent_id),
        ):
            key = (str(sib["history_db"]), int(sib["history_id"]))
            if key == current:
                siblings.append(key)
                continue
            # 生成行が実在し、ゴミ箱に入っていない兄弟だけが継承候補
            if not history_db_path(key[0]).exists():
                continue
            try:
                sib_row = _history_db.for_history(key[0]).fetchone(
                    "SELECT deleted_at FROM generations WHERE id=?", (key[1],)
                )
            except Exception:
                continue
            if sib_row is not None and sib_row["deleted_at"] is None:
                siblings.append(key)
        if current not in siblings:
            siblings.append(current)
            siblings.sort(key=lambda k: k[1])
        self._update_heir_card(heir_card, current, siblings)

    def _update_heir_card(
        self,
        heir_card,
        current: tuple[str, int],
        siblings: list[tuple[str, int]],
    ) -> None:
        """継承権者カードの表示と ◀▶ 切替キャッシュを更新する。"""
        self._lineage_heir_siblings = siblings
        self._lineage_heir_rank = siblings.index(current)
        if heir_card is None:
            return
        from db.connections import history_db_path
        thumb = None
        preview_pix = None
        if history_db_path(current[0]).exists():
            try:
                cur_row = _history_db.for_history(current[0]).fetchone(
                    "SELECT thumbnail_data, local_path FROM generations WHERE id=?", (current[1],)
                )
                thumb = cur_row["thumbnail_data"] if cur_row else None
                if cur_row:
                    from pathlib import Path
                    from PySide6.QtGui import QPixmap
                    local_path = str(cur_row["local_path"] or "")
                    if local_path and Path(local_path).exists():
                        pix = QPixmap(local_path)
                        if not pix.isNull():
                            preview_pix = pix
            except Exception:
                thumb = None
                preview_pix = None
        heir_card.set_heir(
            current[1], thumb, self._lineage_heir_rank, len(siblings), preview_pix
        )

    def _shift_heir(self, delta: int) -> None:
        """◀▶: 継承権者を兄弟の間で切り替える。current ポインタを移動し、
        中央ペインのタイル ON/OFF 状態もその継承権者の記録に合わせる
        （プロンプト全体のロードは行わない。↩で切替前に戻せる）。"""
        siblings = getattr(self, "_lineage_heir_siblings", [])
        rank = getattr(self, "_lineage_heir_rank", 0)
        new_rank = rank + delta
        if not siblings or not (0 <= new_rank < len(siblings)):
            return
        new_db, new_id = siblings[new_rank]
        self._set_current_editor_history_node(new_db, new_id)
        self._history_map_dialog_focus = (new_db, new_id)
        self._apply_heir_tile_state(new_db, new_id)
        self._refresh_history_map_dialog()

    def _apply_heir_tile_state(self, history_db: str, gen_id: int) -> None:
        """
        継承権者の記録スナップショットのタイル ON/OFF 状態を中央ペインへ反映する。

        兄弟は同一バッチ生成のため構造（タイル種別・並び）が一致する前提で、
        一致するレベルだけ enabled をコピーする（中央ペインが編集済みで構造が
        変わっている部分は触らない）。プレビューはその継承権者の実送信文字列に更新。
        """
        from db.connections import get_active_history_name
        if history_db != get_active_history_name():
            return  # 別履歴DBの兄弟（通常は発生しない）は追従対象外
        heir_doc = PromptDocument.load_from_db(gen_id)
        if heir_doc is None:
            return
        if not self._copy_enabled_states(heir_doc, self._editor.document):
            return
        self._editor.refresh_tiles_from_document()
        row = _history_db.fetchone(
            "SELECT sent_positive_prompt, sent_negative_prompt "
            "FROM generations WHERE id=?",
            (gen_id,),
        )
        if row:
            self._editor.set_preview_text(
                row["sent_positive_prompt"] or "",
                row["sent_negative_prompt"] or "",
            )

    @staticmethod
    def _copy_enabled_states(src_doc: PromptDocument, dst_doc: PromptDocument) -> bool:
        """src の各タイルの enabled を、構造が一致する範囲で dst へコピーする。
        変更があれば True。タイル数や種別が食い違うレベルはスキップする。"""
        from core.prompt_builder import GroupTile
        changed = False

        def _walk(src_tiles: list, dst_tiles: list) -> None:
            nonlocal changed
            if len(src_tiles) != len(dst_tiles):
                return
            for s, d in zip(src_tiles, dst_tiles):
                if getattr(s, "tile_type", "") != getattr(d, "tile_type", ""):
                    return
            for s, d in zip(src_tiles, dst_tiles):
                se = bool(getattr(s, "enabled", True))
                if bool(getattr(d, "enabled", True)) != se:
                    d.enabled = se
                    changed = True
                if isinstance(s, GroupTile) and isinstance(d, GroupTile):
                    _walk(s.tiles, d.tiles)

        for side_s, side_d in (
            (src_doc.positive, dst_doc.positive),
            (src_doc.negative, dst_doc.negative),
        ):
            for pos_name in ("top", "middle", "bottom"):
                _walk(side_s.block(pos_name).tiles, side_d.block(pos_name).tiles)
        return changed

    def _on_lineage_jump_to_parent(self) -> None:
        """親カードクリック: 親の設定（シードを除く）をロードして現在ノードを親に移す。"""
        key = getattr(self, "_lineage_parent_key", None)
        if key is None:
            return
        if not self._confirm_load():
            return
        self._jump_to_editor_history_node(*key)

    def _on_lineage_become_root(self) -> None:
        """✂ボタン: 現在ノードを親から切り離して開祖（新しいルート）にする。"""
        current = self._current_editor_history_node()
        if current is None:
            self._show_status(tr("editor.lineage_none"))
            return
        import db.hmap_db as _hmap_db
        row = _hmap_db.fetchone(
            "SELECT parent_db FROM editor_history_nodes "
            "WHERE history_db=? AND history_id=?",
            current,
        )
        if row is None or row["parent_db"] is None:
            self._show_status(tr("lineage.already_root"))
            return
        reply = QMessageBox.question(
            self,
            tr("lineage.become_root_confirm_title"),
            tr("lineage.become_root_confirm_msg"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._detach_editor_history_subtree(*current)

    def _fetch_editor_history_nodes(self, focus: tuple[str, int] | None = None):
        from ui.history_map_dialog import HistoryMapNode
        import db.hmap_db as _hmap_db
        from db.connections import get_active_history_name, get_history_conn, history_db_path

        target = focus if focus is not None else self._current_editor_history_node()
        if target is None:
            return []

        rows = None
        if self._history_map_view_root is not None:
            # 「ここ以下のみ表示」: 起点の子孫だけを列挙する
            if _hmap_db.fetchone(
                "SELECT 1 FROM editor_history_nodes WHERE history_db=? AND history_id=?",
                self._history_map_view_root,
            ):
                rows = _hmap_db.fetch_tree_nodes(*self._history_map_view_root)
            else:
                self._history_map_view_root = None  # 起点が消えたら全体表示に戻す

        if rows is None:
            root = _hmap_db.find_root(*target)
            if root is None:
                _hmap_db.record_node(target[0], target[1], None, None)
                root = _hmap_db.find_root(*target)
            if root is None:
                return []
            rows = _hmap_db.fetch_tree_nodes(*root)
        nodes: list[HistoryMapNode] = []
        for row in rows:
            h_db = row["history_db"]
            h_id = int(row["history_id"])
            deleted_at = None
            rating = 0
            db_exists = history_db_path(h_db).exists()
            if db_exists:
                try:
                    gen_row = get_history_conn(h_db).execute(
                        "SELECT g.deleted_at, r.rating "
                        "FROM generations g "
                        "LEFT JOIN image_reviews r ON r.generation_id = g.id "
                        "WHERE g.id=?", (h_id,)
                    ).fetchone()
                    deleted_at = gen_row["deleted_at"] if gen_row else None
                    rating = int(gen_row["rating"] or 0) if gen_row else 0
                except Exception:
                    deleted_at = None
            else:
                deleted_at = "missing_db"
            nodes.append(HistoryMapNode(
                history_db=h_db,
                history_id=h_id,
                parent_db=row["parent_db"],
                parent_id=int(row["parent_id"]) if row["parent_id"] is not None else None,
                created_at=str(row["created_at"] or ""),
                deleted_at=deleted_at,
                rating=rating,
            ))
        return sorted(nodes, key=lambda n: (n.created_at or "", n.history_id), reverse=True)

    def _open_history_map(
        self,
        generation_id: int | None = None,
        *,
        activate: bool = True,
        restore_state: bool = False,
    ) -> None:
        from ui.history_map_dialog import HistoryMapDialog
        from db.connections import get_active_history_name

        # マップアイコンクリックが「今表示中と同じ履歴ツリー内」かを後で判定するため、
        # 現在地（＝マップが映しているツリー）の根を先に控えておく。
        old_current = self._current_editor_history_node()

        if not restore_state:
            viewer_was_open = self._history_image_viewer_is_open()
            if generation_id is not None:
                self._history_map_dialog_focus = (get_active_history_name(), int(generation_id))
                self._history_map_opened_node = self._history_map_dialog_focus
                self._activate_editor_history_node(int(generation_id))
                # 別履歴のマップを開いたとき、開いている画像ビューアが前の履歴の画像を
                # 残したままにならないよう、新しいノードの画像へ追従させる。
                # （focus/opened は上で設定済みなので set_focus=False）
                if viewer_was_open:
                    self._show_history_map_node_preview(
                        get_active_history_name(), int(generation_id),
                        activate_viewer=False, set_focus=False,
                    )
            else:
                self._history_map_dialog_focus = self._current_editor_history_node()
                self._history_map_opened_node = None
                # ノード指定なしで開いても、開いている画像ビューアの画像は消さず、
                # 現在地ノードの画像へ追従させる（拡大マップを開くと画像が消える不具合の修正）。
                if viewer_was_open and self._history_map_dialog_focus is not None:
                    cur_db, cur_gid = self._history_map_dialog_focus
                    self._show_history_map_node_preview(
                        cur_db, int(cur_gid),
                        activate_viewer=False, set_focus=False,
                    )
            # 通常の開き直しは全体表示・倍率100%から。終了時復元だけ前回状態を優先する。
            self._history_map_view_root = None
            if self._history_map_dialog is not None and not self._history_map_dialog.isVisible():
                self._history_map_dialog.reset_zoom()

        if self._history_map_dialog is None:
            self._history_map_dialog = HistoryMapDialog(self)
            if hasattr(self._history_map_dialog, "restore_saved_geometry"):
                self._history_map_dialog.restore_saved_geometry()
            self._history_map_dialog.node_clicked.connect(
                lambda db, gid: self._on_history_map_node_clicked(
                    db, gid, source="enlarged"
                )
            )
            self._history_map_dialog.jump_requested.connect(
                lambda db, gid: self._jump_to_editor_history_node(
                    db, gid, from_map=True, source="enlarged"
                )
            )
            self._history_map_dialog.preview_requested.connect(self._show_history_map_node_preview)
            self._history_map_dialog.edit_requested.connect(self._edit_history_map_node)
            self._history_map_dialog.stack_requested.connect(self._push_history_stack)
            self._history_map_dialog.color_requested.connect(self._change_history_background_color)
            self._history_map_dialog.text_color_requested.connect(self._change_history_text_color)
            self._history_map_dialog.line_color_requested.connect(self._change_history_line_color)
            self._history_map_dialog.show_subtree_requested.connect(self._show_history_map_subtree)
            self._history_map_dialog.show_full_requested.connect(self._show_history_map_full)
            self._history_map_dialog.detach_requested.connect(self._detach_editor_history_subtree)
            self._history_map_dialog.erase_requested.connect(self._erase_editor_history_node)
            self._history_map_dialog.delete_requested.connect(self._delete_editor_history_single)
            self._history_map_dialog.bulk_erase_requested.connect(self._bulk_erase_editor_history_nodes)
            self._history_map_dialog.bulk_delete_requested.connect(self._bulk_delete_editor_history_nodes)
            self._history_map_dialog.reparent_requested.connect(self._reparent_editor_history_node)
        already_visible = self._history_map_dialog.isVisible()
        self._refresh_history_map_dialog()
        if hasattr(self._history_map_dialog, "set_raise_on_next_show"):
            self._history_map_dialog.set_raise_on_next_show(activate)
        if hasattr(self._history_map_dialog, "set_show_without_activating"):
            self._history_map_dialog.set_show_without_activating(not activate)
        self._history_map_dialog.show()
        if activate:
            self._history_map_dialog.raise_()
        elif hasattr(self._history_map_dialog, "set_show_without_activating"):
            self._history_map_dialog.set_show_without_activating(False)
        if already_visible:
            # 既に開いている場合 showEvent が発火しないため明示的にスクロール
            self._history_map_dialog.scroll_to_current()
        if generation_id is not None:
            new_key = (get_active_history_name(), int(generation_id))
            same_tree = (
                old_current is not None
                and self._history_root_key(*new_key) == self._history_root_key(*old_current)
            )
            if same_tree:
                # 同じ履歴マップ内のノードへ: ノードクリックと同様に青枠の移動アニメを
                # 再生する（全再構築後に再生するよう singleShot で遅延）。
                QTimer.singleShot(
                    0, lambda o=old_current, n=new_key: self._play_map_move_animation(o, n)
                )
                # 現在地への中央移動（センタリング）は必須機能。枠移動と必ず両立させる。
                panel = getattr(self._editor, "parent_child_map", None)
                if panel is not None and hasattr(panel, "scroll_to_current_animated"):
                    QTimer.singleShot(0, panel.scroll_to_current_animated)
                if already_visible and hasattr(self._history_map_dialog, "scroll_to_current_animated"):
                    QTimer.singleShot(0, self._history_map_dialog.scroll_to_current_animated)
            else:
                # 別ツリーへ切り替え/新規表示は、アニメ元が無いので即時に現在地表示。
                QTimer.singleShot(0, self._center_visible_history_maps)

    @staticmethod
    def _settings_node_key(prefix: str) -> tuple[str, int] | None:
        db = _get_setting(f"{prefix}_db", "")
        id_str = _get_setting(f"{prefix}_id", "")
        if db and id_str.isdigit():
            return (db, int(id_str))
        return None

    @staticmethod
    def _write_settings_node_key(prefix: str, key: tuple[str, int] | None) -> list[tuple[str, str]]:
        if key is None:
            return [(f"{prefix}_db", ""), (f"{prefix}_id", "")]
        return [(f"{prefix}_db", key[0]), (f"{prefix}_id", str(key[1]))]

    def _restore_history_map_state(self) -> None:
        """前回セッションで履歴マップ/画像ビューアを開いたまま終了していたら再現する。"""
        restore_map = _get_setting("history_map_open", "0") == "1"
        restore_viewer = _get_setting("history_image_viewer_open", "0") == "1"
        if not restore_map and not restore_viewer:
            return
        self._history_map_dialog_focus = self._settings_node_key("history_map_focus")
        self._history_map_opened_node = self._settings_node_key("history_map_opened")
        self._history_map_view_root = self._settings_node_key("history_map_view_root")
        if self._history_map_dialog_focus is None:
            self._history_map_dialog_focus = self._current_editor_history_node()
        self._open_history_map(activate=False, restore_state=True)
        geo_hex = _get_setting("history_map_geometry", "")
        if geo_hex and self._history_map_dialog is not None:
            try:
                from PySide6.QtCore import QByteArray
                self._history_map_dialog.restoreGeometry(
                    QByteArray.fromHex(geo_hex.encode("ascii"))
                )
                if hasattr(self._history_map_dialog, "restore_saved_geometry"):
                    self._history_map_dialog.restore_saved_geometry()
            except Exception:
                pass  # ジオメトリ復元に失敗しても開くこと自体は維持
        if self._history_map_dialog is not None and hasattr(self._history_map_dialog, "restore_map_view_state"):
            try:
                zoom = float(_get_setting("history_map_zoom", "1.0"))
            except ValueError:
                zoom = 1.0
            try:
                hscroll = int(_get_setting("history_map_hscroll", "0"))
            except ValueError:
                hscroll = 0
            try:
                vscroll = int(_get_setting("history_map_vscroll", "0"))
            except ValueError:
                vscroll = 0
            self._history_map_dialog.restore_map_view_state(
                zoom=zoom,
                hscroll=hscroll,
                vscroll=vscroll,
            )
        if restore_viewer:
            # 画像ビューアは常に「開いているノード（点線枠）」の画像を表示するのが正。
            # opened を最優先にすることで、別履歴へ切り替えた状態と食い違って保存された
            # 古いビューア対象（前バージョンの不具合で残ったもの）も自己修復する。
            viewer_key = self._history_map_opened_node
            if viewer_key is None:
                db = _get_setting("history_image_viewer_history_db", "")
                id_str = _get_setting("history_image_viewer_history_id", "")
                if db and id_str.isdigit():
                    viewer_key = (db, int(id_str))
            if viewer_key is not None:
                QTimer.singleShot(
                    0,
                    lambda k=viewer_key:
                        self._show_history_map_node_preview(
                            k[0], int(k[1]), activate_viewer=False, set_focus=False,
                        ),
                )

    def _on_history_rows_changed(self) -> None:
        """
        右ペインで履歴行が変化した（ゴミ箱出入り・移動等）。
        開いている履歴マップのグレーアウト表示と系譜カードを追従させる。
        """
        self._refresh_lineage_card()
        if self._history_map_dialog is not None and self._history_map_dialog.isVisible():
            self._refresh_history_map_dialog()

    def _refresh_history_map_dialog(self) -> None:
        if self._history_map_dialog is None:
            self._refresh_lineage_two_row_view()
            return
        from db.connections import get_active_history_name
        focus = self._history_map_dialog_focus or self._current_editor_history_node()
        current = self._current_editor_history_node()
        nodes = self._fetch_editor_history_nodes(focus)
        self._history_map_dialog.set_view_restricted(self._history_map_view_root is not None)
        self._history_map_dialog.set_active_history_name(get_active_history_name())
        bg_key = focus or current
        if bg_key is not None and hasattr(self._history_map_dialog, "set_history_background_color"):
            self._history_map_dialog.set_history_background_color(self._history_background_color(*bg_key))
        if bg_key is not None and hasattr(self._history_map_dialog, "set_history_text_color"):
            self._history_map_dialog.set_history_text_color(self._history_text_color(*bg_key))
        if bg_key is not None and hasattr(self._history_map_dialog, "set_history_line_color"):
            self._history_map_dialog.set_history_line_color(self._history_line_color(*bg_key))
        self._history_map_dialog.rebuild(nodes, current, self._history_map_opened_node)
        self._refresh_lineage_two_row_view()

    def _history_image_viewer_is_open(self) -> bool:
        """画像ビューア（拡大マップ/中央マップのどちらが持っていても）が表示中か。"""
        hosts = (self._history_map_dialog, getattr(self._editor, "parent_child_map", None))
        for host in hosts:
            viewer = getattr(host, "_image_viewer", None) if host is not None else None
            try:
                if viewer is not None and viewer.isVisible():
                    return True
            except RuntimeError:
                pass
        return False

    def _on_history_map_node_clicked(self, history_db: str, gen_id: int, *, source: str = "") -> None:
        """履歴マップの左クリック。

        常に現在地を移動する。画像ウィンドウが開いている場合は画像も更新し、
        閉じている場合は右ペインが開いていれば該当履歴をツリー内で主張表示する。
        """
        viewer_open = self._history_image_viewer_is_open()
        gen_id = int(gen_id)
        self._jump_to_editor_history_node(history_db, gen_id, from_map=True, source=source)
        if viewer_open:
            self._show_history_map_node_preview(history_db, gen_id)
            return

        QTimer.singleShot(0, lambda db=history_db, gid=gen_id: self._focus_history_item_from_map_click(db, gid))

    def _focus_history_item_from_map_click(self, history_db: str, gen_id: int) -> None:
        try:
            from db.connections import get_active_history_name
            active_history = get_active_history_name()
        except Exception:
            active_history = ""
        if history_db != active_history:
            return
        if not getattr(self, "_right_visible", False) or not self._side_panel.isVisible():
            return
        if hasattr(self._side_panel, "focus_history_generation"):
            self._side_panel.focus_history_generation(int(gen_id), animate=True, flash=True)

    def _open_image_viewer(self):
        """現在開いている画像ビューア（拡大/中央マップのいずれか）を返す。無ければ None。"""
        hosts = (self._history_map_dialog, getattr(self._editor, "parent_child_map", None))
        for host in hosts:
            viewer = getattr(host, "_image_viewer", None) if host is not None else None
            try:
                if viewer is not None and viewer.isVisible():
                    return viewer
            except RuntimeError:
                pass
        return None

    def _apply_image_viewer_background(self) -> None:
        """画像ウィンドウの背景色を、表示中ノードの履歴背景色（=マップ背景色と同一の
        共有値 `_history_background_color`）に合わせる。色を編集すると両方が同期する。"""
        viewer = self._open_image_viewer()
        if viewer is None:
            return
        key = getattr(viewer, "_node_key", None)
        if key is None:
            return
        viewer.set_background_color(self._history_background_color(key[0], int(key[1])))

    def _clear_history_map_opened_node_if_removed(self, removed_key: tuple[str, int]) -> None:
        """表示中ビューアの対象ノードが消えたら、ビューアは残して画像だけ空にする。"""
        if self._history_map_opened_node != removed_key:
            return
        self._history_map_opened_node = None
        self._history_map_dialog_focus = self._current_editor_history_node()
        if self._history_map_dialog is not None:
            self._history_map_dialog.clear_node_preview()

    def _history_map_nav_targets(self, history_db: str, gen_id: int) -> dict[str, tuple[str, int] | None]:
        """画像ビューアの ▲▼◀▶ 用。親・代表子・前後の兄弟を返す。"""
        import db.hmap_db as _hmap_db

        key = (history_db, int(gen_id))
        row = _hmap_db.fetchone(
            "SELECT parent_db, parent_id FROM editor_history_nodes "
            "WHERE history_db=? AND history_id=?",
            key,
        )
        if row is None:
            return {"parent": None, "child": None, "prev": None, "next": None}

        parent = None
        if row["parent_db"] is not None and row["parent_id"] is not None:
            parent = (str(row["parent_db"]), int(row["parent_id"]))

        children = _hmap_db.fetchall(
            "SELECT history_db, history_id FROM editor_history_nodes "
            "WHERE parent_db=? AND parent_id=? "
            "ORDER BY created_at DESC, history_id DESC",
            key,
        )
        child = (
            (str(children[0]["history_db"]), int(children[0]["history_id"]))
            if children else None
        )

        # 兄弟（前後＝◀▶）は「同じ親を持つノード」のみ。開祖(root, parent=None)は
        # 親が無い＝兄弟も無いので prev/next は無し。以前は parent_db IS NULL の
        # 全ノード（＝全履歴・全ツリーの開祖）を兄弟扱いし、別ツリー/別履歴の開祖へ
        # 飛べてしまっていた（#101→#80→#283→#1061 等）ため、root では出さない。
        prev_key = next_key = None
        if parent is not None:
            siblings = _hmap_db.fetchall(
                "SELECT history_db, history_id FROM editor_history_nodes "
                "WHERE parent_db=? AND parent_id=? "
                "ORDER BY created_at DESC, history_id DESC",
                parent,
            )
            sibling_keys = [
                (str(sib["history_db"]), int(sib["history_id"]))
                for sib in siblings
            ]
            try:
                idx = sibling_keys.index(key)
            except ValueError:
                idx = -1
            prev_key = sibling_keys[idx - 1] if idx > 0 else None
            next_key = sibling_keys[idx + 1] if idx >= 0 and idx + 1 < len(sibling_keys) else None
        return {"parent": parent, "child": child, "prev": prev_key, "next": next_key}

    def _show_history_map_node_preview(
        self, history_db: str, gen_id: int, *, activate_viewer: bool = True,
        set_focus: bool = True,
    ) -> None:
        """
        履歴マップのノードシングルクリック: 大きめプレビューを表示する。

        set_focus=False のときは focus/opened ノード（＝マップのハイライトや点線枠）を
        変更しない。画像ビューアの「画像だけ」を更新したい場合に使う（例: 終了時に
        開いていたビューアの復元。focus/opened は設定から別途復元済みのため、ここで
        ビューアのノードに上書きしてしまうと、別履歴へ切り替えた状態と食い違う）。

        画像の優先順:
          ① PromptMosaic 保存画像（local_path、100%表示）
          ② Invoke の元画像（invoke_image_name を API 取得、100%表示）
          ③ 履歴サムネイル（実寸）
          ④ どれもない時は 100x100 グレー＋中央に「画像無し」
        画像ビューア側で拡大縮小するため、ここでは元画像のサイズを保つ。
        """
        from pathlib import Path
        from PySide6.QtGui import QPixmap, QPainter, QColor
        from db.connections import history_db_path

        preview_host = self._history_map_dialog
        if preview_host is None:
            preview_host = getattr(self._editor, "parent_child_map", None)
        if preview_host is None or not hasattr(preview_host, "show_node_preview"):
            return
        node_key = (history_db, int(gen_id))
        if set_focus:
            self._history_map_dialog_focus = node_key
            self._history_map_opened_node = node_key
            self._refresh_history_map_dialog()

        row = None
        if history_db_path(history_db).exists():
            try:
                row = _history_db.for_history(history_db).fetchone(
                    "SELECT g.invoke_image_name, g.local_path, g.thumbnail_data, "
                    "COALESCE(gg.name, '') AS group_name "
                    "FROM generations g "
                    "LEFT JOIN generation_groups gg ON gg.id = g.group_id "
                    "WHERE g.id=?",
                    (gen_id,),
                )
            except Exception:
                row = None

        pix: QPixmap | None = None
        pending_full_image: str | None = None  # 後からバックグラウンド取得する Invoke 画像名
        if row:
            # ① PromptMosaic 保存画像（100%・ローカルなので即時・差し替え不要）
            local_path = str(row["local_path"] or "")
            if local_path and Path(local_path).exists():
                p = QPixmap(local_path)
                if not p.isNull():
                    pix = p
            if pix is None:
                # ③ まず履歴サムネ（実寸）を即時表示して体感を速くする。
                if row["thumbnail_data"]:
                    p = QPixmap()
                    if p.loadFromData(bytes(row["thumbnail_data"])) and not p.isNull():
                        pix = p
                # ② Invoke 元画像（100%）は同期取得せず、バックグラウンドで取得して
                #    後からビューアの画像だけ差し替える（UIスレッドをブロックしない）。
                if row["invoke_image_name"]:
                    pending_full_image = str(row["invoke_image_name"])

        # ④ 画像無し
        if pix is None:
            pix = QPixmap(100, 100)
            pix.fill(QColor("#808080"))
            painter = QPainter(pix)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(
                pix.rect(), Qt.AlignmentFlag.AlignCenter,
                tr("history_map.preview_no_image"),
            )
            painter.end()

        group_name = str(row["group_name"] or "") if row else ""
        group_label = group_name or tr("history_map.preview_group_none")
        header = (
            f'{tr("history_map.node_label", n=gen_id)} / '
            f'{tr("history_map.preview_group_label", group=group_label)}'
        )
        preview_host.show_node_preview(
            header,
            pix,
            node_key=node_key,
            nav=self._history_map_nav_targets(history_db, gen_id),
            activate=activate_viewer,
        )
        # 画像ウィンドウの背景色を、このノードの履歴背景色（マップと共有）に合わせる。
        self._apply_image_viewer_background()
        if pending_full_image:
            self._start_node_full_image_fetch(node_key, pending_full_image, preview_host)

    def _start_node_full_image_fetch(self, node_key, image_name: str, preview_host) -> None:
        """フル画像をバックグラウンド取得し、ビューアが同じノードを表示中なら差し替える。"""
        workers = self.__dict__.setdefault("_node_image_workers", [])
        worker = _NodeFullImageWorker(self._client, node_key, image_name, self)

        def _on_loaded(nk, data, host=preview_host) -> None:
            from PySide6.QtGui import QPixmap
            p = QPixmap()
            if not p.loadFromData(bytes(data)) or p.isNull():
                return
            if hasattr(host, "update_node_image"):
                host.update_node_image(nk, p)

        def _cleanup(w=worker) -> None:
            if w in workers:
                workers.remove(w)
            w.deleteLater()

        worker.loaded.connect(_on_loaded)
        worker.finished.connect(_cleanup)
        workers.append(worker)
        worker.start()

    def _jump_to_editor_history_node(
        self, history_db: str, gen_id: int, *, from_map: bool = False, source: str = "",
    ) -> None:
        # source: 操作元のマップ。"central"=中央ペイン / "enlarged"=拡大ダイアログ。
        # 操作したマップ自身はスクロールさせず（ユーザーが見ている）、反対側のマップを
        # 現在地へ滑らかにスクロール追従させる。それ以外（系譜カード等）は両方追従。
        from db.connections import get_history_conn, history_db_path
        db_path = history_db_path(history_db)
        if not db_path.exists():
            self._show_status(tr("history_map.target_removed"), error=True)
            self._open_history_map()
            return
        try:
            row = get_history_conn(history_db).execute(
                "SELECT id, seed FROM generations WHERE id=?", (gen_id,)
            ).fetchone()
        except Exception:
            row = None
        if not row:
            self._show_status(tr("history_map.target_removed"), error=True)
            self._open_history_map()
            return
        old_current = self._current_editor_history_node()
        if self._load_generation_for_history_jump(gen_id, history_db):
            # 履歴のシード値をスピンボックスにだけ反映する。
            # シード固定でプロンプト起因の変化を見る用途のため、ランダムCB・
            # 🔓/🔒 固定トグルの状態は変えない（値だけ持ってくる）
            try:
                seed = int(row["seed"]) if row["seed"] is not None else -1
            except (TypeError, ValueError):
                seed = -1
            if seed >= 0:
                self._seed_spin.setValue(max(0, min(2_147_483_647, seed)))
            self._set_current_editor_history_node(history_db, gen_id)
            self._history_map_dialog_focus = (history_db, gen_id)
            # ジャンプしてもマップは閉じない（連続ジャンプを許す）
            self._refresh_history_map_dialog()
            # 現在地へのスクロール追従（操作したマップ自身は動かさない）。
            self._follow_current_in_maps(source)
            if from_map:
                # 移動アニメ（青枠が旧→新ノードへ滑る）。
                # クリックは apply→preview の順で発火し、preview 側の再構築
                # (_refresh_history_map_dialog の scene.clear()) が走る。ここで同期的に
                # 枠を作ると直後の preview 再構築で枠が破棄されてアニメが見えないため、
                # すべての同期再構築が終わった次のイベントループで生成・再生する。
                new_key = (history_db, gen_id)
                old_key = old_current
                QTimer.singleShot(
                    0, lambda o=old_key, n=new_key: self._play_map_move_animation(o, n)
                )

    def _play_map_move_animation(self, old_key, new_key) -> None:
        """中央/拡大マップで、旧→新ノードへ青枠が滑る移動アニメを再生する。"""
        if self._history_map_dialog is not None:
            self._history_map_dialog.play_move_animation(old_key, new_key)
        panel = getattr(self._editor, "parent_child_map", None)
        if panel is not None and hasattr(panel, "play_move_animation"):
            panel.play_move_animation(old_key, new_key)

    def _follow_current_in_maps(self, source: str = "") -> None:
        """現在地ノードへ各マップを滑らかにスクロールさせる。

        source=="central"（中央ペイン操作）なら中央は動かさず拡大ダイアログのみ、
        source=="enlarged"（拡大ダイアログ操作）なら拡大は動かさず中央のみ、
        それ以外（生成・系譜カード等）は表示中の両方を現在地へ追従させる。
        """
        panel = getattr(self._editor, "parent_child_map", None)
        if source != "central" and panel is not None and hasattr(panel, "scroll_to_current_animated"):
            panel.scroll_to_current_animated()
        dlg = self._history_map_dialog
        if (source != "enlarged" and dlg is not None and dlg.isVisible()
                and hasattr(dlg, "scroll_to_current_animated")):
            dlg.scroll_to_current_animated()

    def _edit_history_map_node(self, history_db: str, gen_id: int) -> None:
        """履歴マップの「編集」: 右ペインと同じ履歴の編集ダイアログを開く。"""
        from db.connections import get_active_history_name
        if history_db != get_active_history_name():
            return  # メニュー側で無効化済み（保険。別履歴DBの行は編集対象外）
        from ui.review_dialog import ReviewDialog
        dlg = ReviewDialog(gen_id, client=self._client, parent=self)
        dlg.load_requested.connect(self._load_generation)
        dlg.review_saved.connect(
            lambda _gid: (
                self._side_panel.refresh_history(),
                self._refresh_history_map_dialog(),  # ★評価等をマップへ反映
            )
        )
        dlg.exec()

    def _detach_editor_history_subtree(self, history_db: str, gen_id: int) -> None:
        import db.hmap_db as _hmap_db
        row = _hmap_db.fetchone(
            "SELECT 1 FROM editor_history_nodes WHERE history_db=? AND history_id=?",
            (history_db, gen_id),
        )
        if not row:
            return
        _hmap_db.detach_subtree(history_db, gen_id)
        self._history_map_dialog_focus = (history_db, gen_id)
        self._set_current_editor_history_node(history_db, gen_id)
        self._refresh_history_map_dialog()
        self._show_status(tr("history_map.detached"))

    # ── 履歴マップ コンテキストメニュー ──────────────────
    #   ここ以下のみ表示／全体表示＝表示の絞り込みのみ（DB不変）
    #   別系統にして移動＝系譜リンク操作（detach_subtree）
    #   消去＝ノード除去のみ（子は親へ接続・ゴミ箱なし・警告なし）
    #   削除＝消去＋ゴミ箱行き（警告あり）

    def _show_history_map_subtree(self, history_db: str, gen_id: int) -> None:
        """ここ以下のみ表示: このノードの子孫だけをマップに表示する。"""
        self._history_map_view_root = (history_db, gen_id)
        self._refresh_history_map_dialog()

    def _show_history_map_full(self) -> None:
        """全体表示: 「ここ以下のみ表示」の絞り込みを解除する。"""
        self._history_map_view_root = None
        self._refresh_history_map_dialog()

    def _erase_editor_history_node(self, history_db: str, gen_id: int) -> None:
        """消去: 系譜ノードをマップから取り除き、子を親へ直系接続する。
        履歴行は触らない（ゴミ箱に行かない）。警告なし。"""
        import db.hmap_db as _hmap_db
        node = _hmap_db.fetchone(
            "SELECT parent_db, parent_id FROM editor_history_nodes "
            "WHERE history_db=? AND history_id=?",
            (history_db, gen_id),
        )
        if node is None or node["parent_db"] is None:
            return  # 未登録 or 開祖（開祖は消せない）
        self._remove_editor_history_node_relink(history_db, gen_id)
        self._clear_history_map_opened_node_if_removed((history_db, gen_id))
        parent_key = (str(node["parent_db"]), int(node["parent_id"]))
        if self._history_map_view_root == (history_db, gen_id):
            self._history_map_view_root = None
        if self._history_map_dialog_focus == (history_db, gen_id):
            self._history_map_dialog_focus = parent_key
        self._refresh_lineage_card()
        self._refresh_history_map_dialog()
        self._show_status(tr("history_map.erased_status"))

    def _normal_history_node_keys(self, keys: list) -> list[tuple[str, int]]:
        result: list[tuple[str, int]] = []
        seen: set[tuple[str, int]] = set()
        for key in keys or []:
            if not isinstance(key, (tuple, list)) or len(key) != 2:
                continue
            try:
                norm = (str(key[0]), int(key[1]))
            except Exception:
                continue
            if norm not in seen:
                seen.add(norm)
                result.append(norm)
        return result

    def _nearest_unselected_history_parent(
        self,
        key: tuple[str, int],
        selected: set[tuple[str, int]],
        parents: dict[tuple[str, int], tuple[str, int] | None],
    ) -> tuple[str, int] | None:
        import db.hmap_db as _hmap_db
        current = parents.get(key)
        seen: set[tuple[str, int]] = set()
        while current is not None and current in selected and current not in seen:
            seen.add(current)
            if current not in parents:
                row = _hmap_db.fetchone(
                    "SELECT parent_db, parent_id FROM editor_history_nodes "
                    "WHERE history_db=? AND history_id=?",
                    current,
                )
                parents[current] = (
                    (str(row["parent_db"]), int(row["parent_id"]))
                    if row is not None and row["parent_db"] is not None
                    else None
                )
            current = parents.get(current)
        return current

    def _remove_editor_history_nodes_relink(
        self, keys: list[tuple[str, int]]
    ) -> list[tuple[str, int]]:
        """複数系譜ノードを間引く。範囲外の子は最初の選択外祖先へつなぎ直す。"""
        import db.hmap_db as _hmap_db
        selected: set[tuple[str, int]] = set()
        parents: dict[tuple[str, int], tuple[str, int] | None] = {}
        for key in keys:
            row = _hmap_db.fetchone(
                "SELECT parent_db, parent_id FROM editor_history_nodes "
                "WHERE history_db=? AND history_id=?",
                key,
            )
            if row is None or row["parent_db"] is None:
                continue  # 未登録/開祖は一括処理でも消さない
            selected.add(key)
            parents[key] = (str(row["parent_db"]), int(row["parent_id"]))
        if not selected:
            return []

        with _hmap_db.transaction() as conn:
            for key in selected:
                new_parent = self._nearest_unselected_history_parent(key, selected, parents)
                children = conn.execute(
                    "SELECT history_db, history_id FROM editor_history_nodes "
                    "WHERE parent_db=? AND parent_id=?",
                    key,
                ).fetchall()
                for child in children:
                    child_key = (str(child["history_db"]), int(child["history_id"]))
                    if child_key in selected:
                        continue
                    if new_parent is None:
                        conn.execute(
                            "UPDATE editor_history_nodes SET parent_db=NULL, parent_id=NULL "
                            "WHERE history_db=? AND history_id=?",
                            child_key,
                        )
                    else:
                        conn.execute(
                            "UPDATE editor_history_nodes SET parent_db=?, parent_id=? "
                            "WHERE history_db=? AND history_id=?",
                            (new_parent[0], new_parent[1], child_key[0], child_key[1]),
                        )
            for key in selected:
                conn.execute(
                    "DELETE FROM editor_history_nodes WHERE history_db=? AND history_id=?",
                    key,
                )

        current = self._current_editor_history_node()
        if current in selected:
            parent = self._nearest_unselected_history_parent(current, selected, parents)
            if parent is None:
                _set_setting("editor_history_current_history_db", "")
                _set_setting("editor_history_current_history_id", "")
            else:
                _set_setting("editor_history_current_history_db", parent[0])
                _set_setting("editor_history_current_history_id", str(parent[1]))
        return list(selected)

    def _after_bulk_history_map_remove(self, removed: list[tuple[str, int]]) -> None:
        removed_set = set(removed)
        for key in removed:
            self._clear_history_map_opened_node_if_removed(key)
        if self._history_map_view_root in removed_set:
            self._history_map_view_root = None
        if self._history_map_dialog_focus in removed_set:
            self._history_map_dialog_focus = self._current_editor_history_node()
        self._refresh_lineage_card()
        self._refresh_history_map_dialog()

    def _bulk_erase_editor_history_nodes(self, keys: list) -> None:
        norm = self._normal_history_node_keys(keys)
        removed = self._remove_editor_history_nodes_relink(norm)
        if not removed:
            return
        self._after_bulk_history_map_remove(removed)
        self._show_status(tr("history_map.bulk_erased_status", n=len(removed)))

    def _bulk_delete_editor_history_nodes(self, keys: list) -> None:
        norm = self._normal_history_node_keys(keys)
        if not norm:
            return
        reply = QMessageBox.question(
            self,
            tr("history_map.bulk_delete_confirm_title"),
            tr("history_map.bulk_delete_confirm_msg", n=len(norm)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        removed = self._remove_editor_history_nodes_relink(norm)
        if not removed:
            return
        import datetime as _dt
        from db.connections import get_active_history_name, history_db_path
        touched_active = False
        for history_db, gen_id in removed:
            if not history_db_path(history_db).exists():
                continue
            try:
                _history_db.for_history(history_db).execute(
                    "UPDATE generations SET deleted_at=? "
                    "WHERE id=? AND deleted_at IS NULL",
                    (_dt.datetime.now(), gen_id),
                )
                touched_active = touched_active or history_db == get_active_history_name()
            except Exception:
                pass
        self._after_bulk_history_map_remove(removed)
        if touched_active:
            self._side_panel.refresh_history()
        self._show_status(tr("history_map.bulk_deleted_status", n=len(removed)))

    def _is_history_map_ancestor(
        self, ancestor: tuple[str, int], child: tuple[str, int]
    ) -> bool:
        import db.hmap_db as _hmap_db
        current = child
        seen: set[tuple[str, int]] = set()
        while current not in seen:
            seen.add(current)
            row = _hmap_db.fetchone(
                "SELECT parent_db, parent_id FROM editor_history_nodes "
                "WHERE history_db=? AND history_id=?",
                current,
            )
            if row is None or row["parent_db"] is None:
                return False
            parent = (str(row["parent_db"]), int(row["parent_id"]))
            if parent == ancestor:
                return True
            current = parent
        return False

    def _reparent_editor_history_node(
        self, child_db: str, child_id: int, parent_db: str, parent_id: int
    ) -> None:
        """直系内のみ、子孫ノードの親を祖先ノードへ付け替える。"""
        child = (str(child_db), int(child_id))
        parent = (str(parent_db), int(parent_id))
        if child == parent:
            return
        import db.hmap_db as _hmap_db
        row = _hmap_db.fetchone(
            "SELECT parent_db, parent_id FROM editor_history_nodes "
            "WHERE history_db=? AND history_id=?",
            child,
        )
        if row is None or row["parent_db"] is None:
            return
        current_parent = (str(row["parent_db"]), int(row["parent_id"]))
        if current_parent == parent:
            return
        if not self._is_history_map_ancestor(parent, child):
            self._show_status(tr("history_map.reparent_invalid_status"), error=True)
            return
        _hmap_db.execute(
            "UPDATE editor_history_nodes SET parent_db=?, parent_id=? "
            "WHERE history_db=? AND history_id=?",
            (parent[0], parent[1], child[0], child[1]),
        )
        self._history_map_dialog_focus = child
        self._refresh_lineage_card()
        self._refresh_history_map_dialog()
        self._show_status(tr("history_map.reparented_status"))

    def _delete_editor_history_single(self, history_db: str, gen_id: int) -> None:
        """削除: 子を親へ直系接続して本人のノードを除去し、履歴行をゴミ箱へ（警告あり）。"""
        import db.hmap_db as _hmap_db
        from db.connections import get_active_history_name, history_db_path
        node = _hmap_db.fetchone(
            "SELECT parent_db, parent_id FROM editor_history_nodes "
            "WHERE history_db=? AND history_id=?",
            (history_db, gen_id),
        )
        if node is None or node["parent_db"] is None:
            return  # 開祖は消せない
        reply = QMessageBox.question(
            self,
            tr("history_map.delete_single_confirm_title"),
            tr("history_map.delete_single_confirm_msg"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        touched = False
        if history_db_path(history_db).exists():
            try:
                import datetime as _dt
                _history_db.for_history(history_db).execute(
                    "UPDATE generations SET deleted_at=? "
                    "WHERE id=? AND deleted_at IS NULL",
                    (_dt.datetime.now(), gen_id),
                )
                touched = True
            except Exception:
                pass
        self._remove_editor_history_node_relink(history_db, gen_id)
        self._clear_history_map_opened_node_if_removed((history_db, gen_id))
        if self._history_map_view_root == (history_db, gen_id):
            self._history_map_view_root = None
        if self._history_map_dialog_focus == (history_db, gen_id):
            self._history_map_dialog_focus = (str(node["parent_db"]), int(node["parent_id"]))
        self._refresh_lineage_card()
        self._refresh_history_map_dialog()
        if touched and history_db == get_active_history_name():
            self._side_panel.refresh_history()
        self._show_status(tr("history_map.deleted_single_status"))

    def _active_history_row_for_adoption(self, history_db: str, gen_id: int) -> dict | None:
        item = self._lineage_row_item(history_db, gen_id)
        if item is not None:
            return item
        try:
            row = _history_db.for_history(history_db).fetchone(
                "SELECT thumbnail_data, deleted_at FROM generations WHERE id=?",
                (gen_id,),
            )
        except Exception:
            return None
        if row is None or row["deleted_at"] is not None:
            return None
        return {
            "history_db": history_db,
            "history_id": int(gen_id),
            "thumbnail_data": row["thumbnail_data"],
            "preview_pixmap": None,
        }

    def _open_adopt_history_dialog(self, gen_id: int) -> None:
        """右ペイン履歴から、対象履歴を別の履歴マップ親へ単独接続する。"""
        from db.connections import get_active_history_name
        history_db = get_active_history_name()
        adoptee = self._active_history_row_for_adoption(history_db, int(gen_id))
        if adoptee is None:
            self._show_status(tr("history_map.target_removed"), error=True)
            return
        rows = _history_db.fetchall(
            "SELECT id, thumbnail_data FROM generations "
            "WHERE deleted_at IS NULL ORDER BY id DESC"
        )
        candidates: list[dict] = []
        for row in rows:
            item = self._active_history_row_for_adoption(history_db, int(row["id"]))
            if item is not None:
                candidates.append(item)
        dlg = _AdoptHistoryDialog(adoptee, candidates, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        parent = dlg.selected_parent()
        if parent is None:
            return
        self._adopt_history_node((history_db, int(gen_id)), parent)

    def _adopt_history_node(self, child: tuple[str, int], parent: tuple[str, int]) -> None:
        """単独養子: child だけを parent の子にし、元の子孫は元親へつなぎ直す。"""
        if child == parent:
            return
        import db.hmap_db as _hmap_db
        parent_exists = _hmap_db.fetchone(
            "SELECT 1 FROM editor_history_nodes WHERE history_db=? AND history_id=?",
            parent,
        )
        with _hmap_db.transaction() as conn:
            if parent_exists is None:
                conn.execute(
                    "INSERT OR REPLACE INTO editor_history_nodes "
                    "(history_db, history_id, parent_db, parent_id, created_at) "
                    "VALUES (?, ?, NULL, NULL, CURRENT_TIMESTAMP)",
                    parent,
                )
            old = conn.execute(
                "SELECT parent_db, parent_id FROM editor_history_nodes "
                "WHERE history_db=? AND history_id=?",
                child,
            ).fetchone()
            old_parent = None
            if old is not None and old["parent_db"] is not None:
                old_parent = (str(old["parent_db"]), int(old["parent_id"]))
            if old is not None:
                if old_parent is None:
                    conn.execute(
                        "UPDATE editor_history_nodes SET parent_db=NULL, parent_id=NULL "
                        "WHERE parent_db=? AND parent_id=?",
                        child,
                    )
                else:
                    conn.execute(
                        "UPDATE editor_history_nodes SET parent_db=?, parent_id=? "
                        "WHERE parent_db=? AND parent_id=?",
                        (old_parent[0], old_parent[1], child[0], child[1]),
                    )
                conn.execute(
                    "DELETE FROM editor_history_nodes WHERE history_db=? AND history_id=?",
                    child,
                )
            conn.execute(
                "INSERT OR REPLACE INTO editor_history_nodes "
                "(history_db, history_id, parent_db, parent_id, created_at) "
                "VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)",
                (child[0], child[1], parent[0], parent[1]),
            )
        self._history_map_dialog_focus = child
        self._history_map_opened_node = child
        self._refresh_lineage_card()
        self._refresh_history_map_dialog()
        self._side_panel.refresh_history()
        self._show_status(tr("adopt_history.done", child=child[1], parent=parent[1]))

    def _load_generation_for_history_jump(self, generation_id: int, history_db_name: str | None = None) -> bool:
        """履歴マップ用。ロック状態に関係なく生成時点の中央ペイン状態を復元する。"""
        from db.connections import get_active_history_name, set_active_history, history_db_path
        target_db = history_db_name or get_active_history_name()
        if not history_db_path(target_db).exists():
            self._show_status(tr("history_map.target_removed"), error=True)
            return False

        prev_active = get_active_history_name()
        needs_switch = (target_db != prev_active)
        if needs_switch:
            set_active_history(target_db)
        try:
            doc = PromptDocument.load_from_db(generation_id)
            if doc is None:
                self._show_status(tr("main.status_load_fail", id=generation_id), error=True)
                return False

            memo_row = _history_db.fetchone(
                "SELECT review_text FROM image_reviews WHERE generation_id=?",
                (generation_id,),
            )
            row = _history_db.fetchone(
                "SELECT loras_json, invoke_key, model_name, steps, cfg_scale, "
                "       scheduler, width, height, template_id "
                "FROM generations WHERE id=?",
                (generation_id,),
            )
        finally:
            if needs_switch:
                set_active_history(prev_active)

        self._editor.set_document(doc)
        self._editor_dirty = False
        self._editor.set_memo((memo_row["review_text"] or "") if memo_row else "")

        if row:
            loras: list = []
            if row["loras_json"]:
                try:
                    loras = json.loads(row["loras_json"])
                except Exception:
                    pass
            self._lora_bar.set_loras(loras)
            self._lora_browser.set_selected_keys(self._lora_bar.get_selected_keys())
            self._relink_lora_trigger_words(loras)

            resolved_base: str = ""
            resolved_model_name: str = ""
            if row["invoke_key"]:
                key = row["invoke_key"]
                mrow = _env_db.fetchone("SELECT name, base FROM models WHERE invoke_key=?", (key,))
                resolved_model_name = (mrow["name"] or key) if mrow else key
                resolved_base = (mrow["base"] or "") if mrow else ""
                self._selected_model_key = key
            elif row["model_name"]:
                mrow = _env_db.fetchone(
                    "SELECT invoke_key, name, base FROM models WHERE name=? AND available=1",
                    (row["model_name"],),
                )
                if not mrow:
                    mrow = _env_db.fetchone(
                        "SELECT invoke_key, name, base FROM models WHERE name=?",
                        (row["model_name"],),
                    )
                if mrow:
                    self._selected_model_key = mrow["invoke_key"]
                    resolved_model_name = mrow["name"]
                    resolved_base = mrow["base"] or ""
                else:
                    resolved_model_name = row["model_name"]

            if resolved_base and resolved_base != self._current_base:
                self._current_base = resolved_base
                self._apply_base_ui(resolved_base)

            if resolved_base:
                hist_tid = row["template_id"]
                trow = None
                if hist_tid:
                    trow = _env_db.fetchone(
                        "SELECT id, name FROM templates WHERE id=? AND base=?",
                        (hist_tid, resolved_base),
                    )
                if not trow:
                    trow = _env_db.fetchone(
                        "SELECT id, name FROM templates WHERE base=? AND is_base_default=1",
                        (resolved_base,),
                    )
                if not trow:
                    trow = _env_db.fetchone(
                        "SELECT id, name FROM templates WHERE base=? ORDER BY id ASC LIMIT 1",
                        (resolved_base,),
                    )
                if trow:
                    self._current_template_id = trow["id"]
                    self._current_template_name = trow["name"]
                else:
                    self._current_template_id = None
                    self._current_template_name = ""

            self._update_model_label(resolved_model_name)
            self._apply_negative_prompt_ui()
            if row["steps"]:
                self._steps_spin.setValue(int(row["steps"]))
            if row["cfg_scale"]:
                self._cfg_spin.setValue(float(row["cfg_scale"]))
            if row["scheduler"]:
                idx = self._sched_combo.findText(str(row["scheduler"]))
                if idx >= 0:
                    self._sched_combo.setCurrentIndex(idx)
            if row["width"]:
                self._width_spin.setValue(int(row["width"]))
            if row["height"]:
                self._height_spin.setValue(int(row["height"]))

        self._show_status(tr("main.status_full_loaded", id=generation_id))
        return True

    def _apply_doc_respecting_locks(self, doc: PromptDocument) -> None:
        """
        ロック中のブロックは現在の内容を保持し、非ロックブロックのみ新しいdocで更新する。
        """
        block_map = {
            (bw.block.block_type, bw.block.position): bw
            for bw in self._editor.all_block_widgets()
        }
        # 新しいdocの各ブロックをロック状態に応じて適用
        new_blocks = [
            doc.positive.top, doc.positive.middle, doc.positive.bottom,
            doc.negative.middle,
        ]
        for new_block in new_blocks:
            key = (new_block.block_type, new_block.position)
            bw = block_map.get(key)
            if bw and not bw._locked:
                bw.block.tiles = new_block.tiles
                bw.block.randomize = new_block.randomize
                bw.reload()

        self._editor._update_preview()

    def _relink_lora_trigger_words(self, loras: list) -> None:
        """
        履歴ロード後、lora_source_key が未設定のトリガーワードタイルを
        各 LoRA のトリガーワードセットと照合して補完する。

        ・TagTile: is_trigger_word=True かつ lora_source_key 空 → 照合して補完
        ・GroupTile: lora_source_key 空 → 子 TagTile の照合結果から伝播
          （新形式は to_dict/from_dict で保存済みなので旧データ救済用）
        ・ネガティブプロンプトセットも同様に照合する
        """
        if not loras:
            return

        from ui.block_widget import BlockWidget as _BW
        from core.prompt_builder import GroupTile as _GT

        # invoke_key → (pos_tags, neg_tags) のマップ
        lora_pos_triggers: dict[str, set[str]] = {}
        lora_neg_triggers: dict[str, set[str]] = {}

        for lora_info in loras:
            key = lora_info.get("invoke_key", "")
            if not key:
                continue

            pos_tags: set[str] = set()
            for s in _env_db.fetchall(
                "SELECT trigger_words FROM lora_trigger_sets WHERE invoke_key=?", (key,)
            ):
                if s["trigger_words"]:
                    for t in _BW._parse_tag_input(s["trigger_words"]):
                        if isinstance(t, TagTile):
                            pos_tags.add(t.tag_name.lower())
            if pos_tags:
                lora_pos_triggers[key] = pos_tags

            neg_tags: set[str] = set()
            for s in _env_db.fetchall(
                "SELECT neg_words FROM lora_neg_prompt_sets WHERE invoke_key=?", (key,)
            ):
                if s["neg_words"]:
                    for t in _BW._parse_tag_input(s["neg_words"]):
                        if isinstance(t, TagTile):
                            neg_tags.add(t.tag_name.lower())
            if neg_tags:
                lora_neg_triggers[key] = neg_tags

        all_triggers = {**lora_pos_triggers, **lora_neg_triggers}
        if not all_triggers:
            return

        def _relink_tag(tile: TagTile) -> bool:
            """TagTile の lora_source_key を補完。変更があれば True を返す。"""
            if not (tile.is_trigger_word and not tile.lora_source_key):
                return False
            name_lower = tile.tag_name.lower()
            for lora_key, triggers in all_triggers.items():
                if name_lower in triggers:
                    tile.lora_source_key = lora_key
                    return True
            return False

        def _relink_tiles(tiles: list) -> bool:
            """タイルリストを再帰的に処理。変更があれば True を返す。"""
            changed = False
            for tile in tiles:
                if isinstance(tile, _GT):
                    # GroupTile の子タイルを再帰処理
                    if _relink_tiles(tile.tiles):
                        changed = True
                    # GroupTile 自体の lora_source_key が空なら子から伝播
                    if not tile.lora_source_key:
                        child_keys = {
                            getattr(c, "lora_source_key", "")
                            for c in tile.tiles
                            if getattr(c, "lora_source_key", "")
                        }
                        if len(child_keys) == 1:
                            tile.lora_source_key = child_keys.pop()
                            changed = True
                elif isinstance(tile, TagTile):
                    if _relink_tag(tile):
                        changed = True
            return changed

        for bw in self._editor.all_block_widgets():
            if _relink_tiles(bw.block.tiles):
                bw._refresh_tiles()

    # ── ドラッグ & ドロップ ──────────────────────────────────

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # type: ignore[override]
        mime = event.mimeData()
        if mime.hasUrls():
            for url in mime.urls():
                if url.isLocalFile() and url.toLocalFile().lower().endswith(('.png', '.webp')):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:  # type: ignore[override]
        for url in event.mimeData().urls():
            if url.isLocalFile():
                path = url.toLocalFile()
                if path.lower().endswith(('.png', '.webp')):
                    event.acceptProposedAction()
                    QTimer.singleShot(0, lambda p=path: self._on_png_dropped(p))
                    return
        event.ignore()

    def _on_png_dropped(self, path: str) -> None:
        """PNG ファイルがドロップされたとき: メタデータを読んでダイアログを開く"""
        try:
            meta = read_png_meta(path)
        except Exception as e:
            self._show_status(tr("main.drop_error", error=e), error=True)
            return

        dlg = MetaDialog(meta, parent=self)
        dlg.load_prompt_requested.connect(
            lambda positive, negative, model_name, loras, m=meta:
                self._on_meta_load(positive, negative, model_name, loras, m)
        )
        dlg.exec()

    def _on_meta_load(
        self,
        positive: str,
        negative: str,
        model_name: str,
        loras: list,
        meta: dict | None = None,
    ) -> None:
        """
        MetaDialog の「エディタにロード」ボタンが押されたとき。

        モデル名 / LoRA が含まれていた場合は positive の bottom ブロックに
        OFF の参照タイルとして追加する。生成設定には自動反映しない。
        """
        if self._translate_worker and self._translate_worker.isRunning():
            self._show_status(tr("main.translate_already_running"))
            return

        invoke_meta = isinstance(meta, dict) and meta.get("source_format") == "invokeai"
        if invoke_meta:
            registered_pos, pending_pos = self._split_registered_prompt_tiles(
                positive, parser=parse_invoke_prompt
            )
            registered_neg, pending_neg = self._split_registered_prompt_tiles(
                negative, parser=parse_invoke_prompt
            )
        else:
            registered_pos, pending_pos = self._split_registered_prompt_tiles(positive)
            registered_neg, pending_neg = self._split_registered_prompt_tiles(negative)
        pending = []

        def _pending_parts(entry):
            if len(entry) >= 3:
                return entry
            tile, index = entry
            return tile, index, None

        for entry in pending_pos:
            tile, index, add_override = _pending_parts(entry)
            pending.append({
                "tile": tile,
                "block_type": "positive",
                "position": "middle",
                "index": index,
                "add_tile": add_override or self._editor.add_tile_to_prompt_block,
            })
        for entry in pending_neg:
            tile, index, add_override = _pending_parts(entry)
            pending.append({
                "tile": tile,
                "block_type": "negative",
                "position": "middle",
                "index": index,
                "add_tile": add_override or self._editor.add_tile_to_prompt_block,
            })

        translate_pending = False
        if pending and self._translation_lm_configured():
            choice = QMessageBox(self)
            choice.setWindowTitle("未登録タグ")
            choice.setText(f"未登録タグが {len(pending)} 件あります。ローカルLLMで翻訳しますか？")
            translate_btn = choice.addButton("翻訳する", QMessageBox.ButtonRole.AcceptRole)
            no_translate_btn = choice.addButton("翻訳しない", QMessageBox.ButtonRole.DestructiveRole)
            cancel_btn = choice.addButton("キャンセル", QMessageBox.ButtonRole.RejectRole)
            choice.setDefaultButton(translate_btn)
            choice.exec()

            clicked = choice.clickedButton()
            if clicked is cancel_btn:
                return
            translate_pending = clicked is not no_translate_btn

        # 系譜が切れる操作なので、現在ノードがあるときは確認する
        if self._current_editor_history_node() is not None:
            reply = QMessageBox.question(
                self,
                tr("lineage.break_confirm_title"),
                tr("lineage.break_confirm_msg"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        self._editor.load_prompt_tiles(registered_pos, registered_neg)
        self._detach_current_editor_history_lineage()

        reference_tiles = []
        model_name = (model_name or "").strip()
        model_applied = False
        lora_applied_names: set[str] = set()
        if invoke_meta:
            model_applied, lora_applied_names = self._apply_invoke_meta_settings(
                meta or {}, model_name, loras
            )

        if model_name and not model_applied:
            reference_tiles.append(TagTile(
                tag_name="Model: " + model_name,
                is_locked=True,
                enabled=False,
            ))

        if loras:
            seen_loras = set()
            for lora in loras:
                name = (lora.get("name") or "").strip()
                if not name:
                    continue
                try:
                    weight = float(lora.get("weight", 1.0))
                except (TypeError, ValueError):
                    weight = 1.0
                key = (name, weight)
                if key in seen_loras:
                    continue
                seen_loras.add(key)
                lora_key = str(lora.get("key") or "").strip()
                lora_hash = str(lora.get("hash") or "").strip()
                if any(ref in lora_applied_names for ref in (name, lora_key, lora_hash) if ref):
                    continue
                reference_tiles.append(TagTile(
                    tag_name=f"LoRA: {name} (weight: {weight:.2f})",
                    is_locked=True,
                    enabled=False,
                ))

        if reference_tiles:
            bottom = self._editor.document.positive.bottom
            for tile in reference_tiles:
                bottom.add_tile(tile)
            # 参照タイルを反映するためドキュメントを再セット
            self._editor.set_document(self._editor.document)

        if pending:
            if not translate_pending:
                for item in pending:
                    item["add_tile"](
                        item["tile"], item["block_type"], item["position"], item["index"]
                    )
            else:
                dlg = _UnregisteredPromptTranslateDialog(
                    pending,
                    self._current_lm_model_id(),
                    parent=self,
                )
                dlg.start()
                dlg.exec()

        self._show_status(tr("main.drop_loaded"))

    def _detach_current_editor_history_lineage(self) -> None:
        _set_setting("editor_history_current_history_db", "")
        _set_setting("editor_history_current_history_id", "")
        self._history_map_dialog_focus = None
        self._refresh_history_map_dialog()
        self._refresh_lineage_card()

    def _split_registered_prompt_tiles(self, prompt: str, *, parser=None) -> tuple[list, list]:
        """プロンプトをタイル化し、DB登録済みと未登録タグに分ける。"""
        if not prompt.strip():
            return [], []
        from ui.block_widget import BlockWidget
        from core.prompt_builder import TagTile
        from ui.tile_widget import _find_registered_tag

        registered = []
        pending = []
        parse = parser or BlockWidget._parse_tag_input
        in_place_add = lambda *_args: self._editor.set_document(self._editor.document)

        def visit(tile, index: int, *, in_group: bool = False):
            if isinstance(tile, GroupTile):
                new_children = []
                group_pending = []
                for child_idx, child in enumerate(tile.tiles):
                    processed, child_pending = visit(child, child_idx, in_group=True)
                    if processed is not None:
                        new_children.append(processed)
                    group_pending.extend(child_pending)
                tile.tiles = new_children
                return tile, group_pending
            if not isinstance(tile, TagTile):
                return tile, []
            row = _find_registered_tag(tile.tag_name)
            if row:
                return BlockWidget._enrich_tile_from_db(tile), []
            else:
                tile.source_text = tile.source_text or tile.tag_name
                tile.translated_text = tile.tag_name
                if in_group:
                    return tile, [(tile, index, in_place_add)]
                return None, [(tile, index)]

        for index, tile in enumerate(parse(prompt)):
            processed, item_pending = visit(tile, index)
            if processed is not None:
                registered.append(processed)
            pending.extend(item_pending)
        return registered, pending

    def _apply_invoke_meta_settings(
        self,
        meta: dict,
        model_name: str,
        loras: list,
    ) -> tuple[bool, set[str]]:
        """Invokeメタデータを、現環境に存在する範囲で生成UIへ反映する。"""
        model_applied = self._apply_invoke_meta_model(
            model_name,
            meta.get("model_base") or "",
            model_key=meta.get("model_key") or "",
            model_hash=meta.get("model_hash") or "",
        )
        applied_loras = self._apply_invoke_meta_loras(loras)

        seed = meta.get("seed")
        try:
            if seed is not None and int(seed) >= 0:
                self._seed_spin.setValue(max(0, min(2_147_483_647, int(seed))))
                self._seed_random_cb.setChecked(False)
        except (TypeError, ValueError):
            pass

        try:
            if meta.get("steps") is not None:
                self._steps_spin.setValue(int(meta["steps"]))
        except (TypeError, ValueError):
            pass
        try:
            if meta.get("cfg_scale") is not None:
                self._cfg_spin.setValue(float(meta["cfg_scale"]))
        except (TypeError, ValueError):
            pass
        try:
            if meta.get("width"):
                self._width_spin.setValue(int(meta["width"]))
            if meta.get("height"):
                self._height_spin.setValue(int(meta["height"]))
        except (TypeError, ValueError):
            pass

        scheduler = str(meta.get("scheduler") or "").strip()
        if scheduler:
            idx = self._sched_combo.findText(scheduler)
            if idx >= 0:
                self._sched_combo.setCurrentIndex(idx)

        return model_applied, applied_loras

    def _apply_invoke_meta_model(
        self,
        model_name: str,
        model_base: str = "",
        *,
        model_key: str = "",
        model_hash: str = "",
    ) -> bool:
        model_name = (model_name or "").strip()
        model_key = (model_key or "").strip()
        model_hash = (model_hash or "").strip()
        if not (model_name or model_key or model_hash):
            return False

        row = _env_db.fetchone(
            "SELECT invoke_key, name, base FROM models "
            "WHERE type='main' AND available=1 "
            "  AND (name=? OR invoke_key=? OR invoke_hash=? OR invoke_key=? OR invoke_hash=?) "
            "ORDER BY CASE WHEN invoke_key=? THEN 0 WHEN invoke_hash=? THEN 1 WHEN name=? THEN 2 ELSE 3 END "
            "LIMIT 1",
            (model_name, model_name, model_name, model_key, model_hash, model_key, model_hash, model_name),
        )
        if not row:
            return False

        base = row["base"] or model_base or self._current_base
        if base and base != self._current_base:
            self._current_base = base
            self._apply_base_ui(base)
        if base:
            self._select_default_template_for_base(base)

        self._selected_model_key = row["invoke_key"]
        self._update_model_label(row["name"] or model_name)
        self._apply_negative_prompt_ui()
        return True

    def _select_default_template_for_base(self, base: str) -> None:
        trow = _env_db.fetchone(
            "SELECT id, name FROM templates WHERE base=? AND is_base_default=1",
            (base,),
        )
        if not trow:
            trow = _env_db.fetchone(
                "SELECT id, name FROM templates WHERE base=? ORDER BY id ASC LIMIT 1",
                (base,),
            )
        if trow:
            self._current_template_id = trow["id"]
            self._current_template_name = trow["name"]
        else:
            self._current_template_id = None
            self._current_template_name = ""

    def _apply_invoke_meta_loras(self, loras: list) -> set[str]:
        applied_refs: set[str] = set()
        if not loras:
            return applied_refs

        current = self._lora_bar.get_loras()
        selected = {item.get("invoke_key") for item in current}
        for lora in loras:
            name = str(lora.get("name") or "").strip() if isinstance(lora, dict) else ""
            key = str(lora.get("key") or "").strip() if isinstance(lora, dict) else ""
            hash_value = str(lora.get("hash") or "").strip() if isinstance(lora, dict) else ""
            if not (name or key or hash_value):
                continue
            row = _env_db.fetchone(
                "SELECT invoke_key, name, base FROM models "
                "WHERE type='lora' AND available=1 "
                "  AND (name=? OR invoke_key=? OR invoke_hash=? OR invoke_key=? OR invoke_hash=?) "
                "ORDER BY CASE WHEN invoke_key=? THEN 0 WHEN invoke_hash=? THEN 1 WHEN name=? THEN 2 ELSE 3 END "
                "LIMIT 1",
                (name, name, name, key, hash_value, key, hash_value, name),
            )
            if not row:
                continue
            try:
                weight = float(lora.get("weight", 0.75))
            except (TypeError, ValueError, AttributeError):
                weight = 0.75
            info = {
                "invoke_key": row["invoke_key"],
                "name": row["name"] or name,
                "base": row["base"] or "",
                "weight": weight,
                "enabled": True,
            }
            if info["invoke_key"] not in selected:
                self._lora_bar.add_lora(info)
                selected.add(info["invoke_key"])
            applied_refs.update(ref for ref in (name, key, hash_value) if ref)

        if applied_refs:
            self._lora_browser.set_selected_keys(self._lora_bar.get_selected_keys())
        return applied_refs

    # ── グループフォーカス ───────────────────────────────

    def _on_group_focus_changed(self, group_id) -> None:
        """サイドパネルで選択されたグループを生成先に設定する"""
        self._current_group_id = group_id
        # 保存先の表示は右ペインのパンくずに委任（ツールバーラベルは削除済み）

    # ── モデルブラウザ連携 ───────────────────────────────

    def _on_model_chosen(self, invoke_key: str, name: str, base: str, variant: str) -> None:
        """ModelBrowser でモデルがダブルクリックされたとき、ラベル・キー・UIを更新する。"""
        # variant で txt2img 非対応モデルを判定（名前依存より確実）
        if variant in ("dev_fill",):
            QMessageBox.warning(
                self,
                tr("main.model_unsupported_title"),
                tr("main.model_unsupported_msg", name=name),
            )
            return

        # テンプレート選択（複数時のみ一覧から1クリック選択。0個/キャンセルは中断）
        from ui.template_dialog import choose_template_for_model
        template_id = choose_template_for_model(self, name, base)
        if template_id is None:
            # 0個 or キャンセル → モデル選択を中断し、コンボを前のモデルへ戻す
            self._revert_model_combo()
            self._show_status(tr("main.template_not_selected"), error=True)
            return

        row_t = _env_db.fetchone(
            "SELECT name FROM templates WHERE id=?", (template_id,),
        )
        self._current_template_id = template_id
        self._current_template_name = row_t["name"] if row_t else ""
        self._apply_negative_prompt_ui()

        if base != self._current_base:
            self._current_base = base
        self._apply_base_ui(base)
        self._populate_model_combo()

        self._selected_model_key = invoke_key
        if base:
            self._last_model_key_by_base[base] = invoke_key
        self._update_model_label(name)

        # 非対応 LoRA を除去
        self._filter_incompatible_loras(base)

        # 自動読み込みLoRAを追加
        self._add_auto_loras(invoke_key)

        # モデル注釈の既定値があれば静かに適用（無ければ UI はそのまま）
        self._apply_model_default_params(invoke_key)

        self._show_status(
            f"モデル選択: {name} ({base}) / テンプレート: {self._current_template_name}"
        )
        self._update_generation_buttons()

    def _update_model_label(self, model_name: str) -> None:
        """モデルコンボと左ペインの現在モデル強調を反映する。"""
        self._remember_selected_model_for_base()
        self._sync_current_model_browser()
        if not hasattr(self, "_model_combo"):
            return
        self._model_combo.blockSignals(True)
        idx = -1
        if self._selected_model_key:
            for i in range(self._model_combo.count()):
                data = self._model_combo.itemData(i)
                if isinstance(data, dict) and data.get("invoke_key") == self._selected_model_key:
                    idx = i
                    break
        if idx >= 0:
            self._model_combo.setCurrentIndex(idx)
        elif model_name:
            self._model_combo.addItem(model_name, {
                "invoke_key": self._selected_model_key,
                "name": model_name,
                "base": self._current_base,
                "variant": "",
            })
            self._model_combo.setCurrentIndex(self._model_combo.count() - 1)
        self._model_combo.blockSignals(False)
        self._model_combo.adjust_to_current_text()
        if self._current_template_name:
            self._model_combo.setToolTip(
                f"{tr('main.model_label_tooltip')}\n{model_name} / {self._current_template_name}"
            )
        else:
            self._model_combo.setToolTip(tr("main.model_label_tooltip"))

    def _sync_current_model_browser(self) -> None:
        """現在モデルキーを左ペインのモデルブラウザ表示へ同期する。"""
        if hasattr(self, "_model_browser"):
            self._model_browser.set_current_model_key(self._selected_model_key)

    def _add_auto_loras(self, model_key: str) -> None:
        """model_auto_loras に登録されたLoRAをLoRAバーに自動追加する。"""
        rows = _env_db.fetchall(
            "SELECT lora_key, weight FROM model_auto_loras "
            "WHERE model_key=? ORDER BY sort_order, id",
            (model_key,),
        )
        if not rows:
            return
        for r in rows:
            lora_info = _env_db.fetchone(
                "SELECT invoke_key, name, base FROM models WHERE invoke_key=? AND type='lora'",
                (r["lora_key"],),
            )
            if not lora_info:
                continue
            info = {
                "invoke_key": lora_info["invoke_key"],
                "name":       lora_info["name"] or lora_info["invoke_key"],
                "base":       lora_info["base"] or "",
                "weight":     r["weight"],
                "enabled":    True,
            }
            self._lora_bar.add_lora(info)
            self._add_lora_sets(r["lora_key"])
        self._lora_browser.set_selected_keys(self._lora_bar.get_selected_keys())

    def _filter_incompatible_loras(self, base: str) -> None:
        """現在のLoRAバーからベース非対応のLoRAを削除する。"""
        current = self._lora_bar.get_loras()
        incompatible = [l for l in current if l.get("base", "") and l.get("base") != base]
        if not incompatible:
            return
        removed_names = [l.get("name", "") for l in incompatible]
        for l in incompatible:
            # lora_removed シグナル経由でトリガーワードも除去される
            self._lora_bar.remove_lora(l["invoke_key"])
        self._lora_browser.set_selected_keys(self._lora_bar.get_selected_keys())
        QMessageBox.information(
            self,
            tr("main.lora_incompatible_title"),
            tr("main.lora_incompatible_msg",
               base=base, names="\n• ".join(removed_names)),
        )



    def _apply_base_ui(self, base: str) -> None:
        """ベース種別に合わせてスケジューラーリストとデフォルトStepsを切り替える。

        Invoke から取得済みの _scheduler_map を優先使用し、
        未取得またはベース不明の場合はハードコードの _SCHEDULERS にフォールバック。
        """
        if hasattr(self, "_model_base_combo") and base:
            idx = self._model_base_combo.findData(base)
            if idx >= 0 and idx != self._model_base_combo.currentIndex():
                self._model_base_combo.blockSignals(True)
                self._model_base_combo.setCurrentIndex(idx)
                self._model_base_combo.blockSignals(False)
                self._apply_model_mode_ui()
        if hasattr(self, "_lora_browser"):
            self._lora_browser.set_base_filter(base)

        node_type = _BASE_DENOISE_NODE.get(base)
        if node_type and node_type in self._scheduler_map:
            schedulers = self._scheduler_map[node_type]
        else:
            schedulers = _SCHEDULERS.get(base, _SCHEDULERS["sdxl"])

        has_scheduler = len(schedulers) > 0
        self._sched_combo.setEnabled(has_scheduler)

        current_sched = self._sched_combo.currentText()

        self._sched_combo.blockSignals(True)
        self._sched_combo.clear()
        self._sched_combo.addItems(schedulers)
        idx = self._sched_combo.findText(current_sched)
        self._sched_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._sched_combo.blockSignals(False)

        # ベース別 CFG ポリシー（状態制御）: flux2 等は CFG を 1.0 固定・編集不可にする。
        # 入力段階で正しい値に固定することで、グラフ生成側で値を書き換える必要がない。
        from core.gen_params import cfg_is_locked, LOCKED_CFG_VALUE
        if hasattr(self, "_cfg_spin"):
            if cfg_is_locked(base):
                self._cfg_spin.blockSignals(True)
                self._cfg_spin.setValue(LOCKED_CFG_VALUE)
                self._cfg_spin.blockSignals(False)
                self._cfg_spin.setEnabled(False)
                self._cfg_spin.setToolTip(tr("main.cfg_locked_tooltip"))
            else:
                self._cfg_spin.setEnabled(True)
                self._cfg_spin.setToolTip(tr("main.cfg_tooltip"))

        self._apply_negative_prompt_ui()

    def _template_supports_negative_prompt(self) -> bool:
        """選択中テンプレートがネガティブプロンプトを実生成へ送れるか判定する。"""
        if self._current_template_id is None:
            return True
        row = _env_db.fetchone(
            "SELECT cache_key FROM templates WHERE id=?",
            (self._current_template_id,),
        )
        if not row:
            return True
        return InvokeClient.template_supports_negative(row["cache_key"])

    def _apply_negative_prompt_ui(self) -> None:
        """現在のモデル/テンプレートに合わせてネガティブ欄の表示を切り替える。"""
        if not hasattr(self, "_editor"):
            return
        supported = self._template_supports_negative_prompt()
        self._negative_supported = supported
        self._editor.set_negative_enabled(supported)

    def _effective_negative_prompt(self) -> str:
        """実送信・履歴保存に使うネガティブプロンプト。非対応なら空文字にする。"""
        if not self._negative_supported:
            return ""
        return self._editor.compile_negative()

    def _document_for_generation(self) -> PromptDocument:
        """生成履歴へ保存する PromptDocument。非対応モデルではネガティブを保持しない。"""
        doc = self._editor.document.clone()
        self._strip_unsupported_negative(doc)
        return doc

    def _strip_unsupported_negative(self, doc: PromptDocument) -> None:
        """非対応モデルではスナップショットからネガティブを取り除く。"""
        if not self._negative_supported:
            for blk in (doc.negative.top, doc.negative.middle, doc.negative.bottom):
                blk.tiles.clear()

    def _apply_selection_to_live_doc(self, first_sel: dict[int, list]) -> None:
        """
        生成1枚目の選択状態（random/sequential グループの選択タイルのみON）を
        中央ペインのドキュメントに反映する。継承権者カード＝右ペインの履歴行と
        同じ状態になる。タイルUIを再構築し、UNDO で生成前の状態に戻せる。
        """
        if not first_sel:
            return
        doc = self._editor.document
        changed = False
        for g in doc._all_group_tiles():
            sel = first_sel.get(id(g))
            if sel is None:
                continue
            keep = set(sel)
            for i, t in enumerate(g.tiles):
                new_state = i in keep
                if bool(getattr(t, "enabled", True)) != new_state:
                    t.enabled = new_state
                    changed = True
        if changed:
            self._editor.refresh_tiles_from_document()

    def _apply_model_default_params(self, invoke_key: str) -> None:
        """モデル注釈のデフォルト値(default_steps/cfg/scheduler)が設定されていれば
        確認なしで静かに適用する。設定が無ければ UI はそのまま（前回使用値は記憶しない）。
        画像サイズは構図に直結するためモデル選択では変更しない。
        スケジューラのベース別リスト切替は別途 _apply_base_ui が行う（現状維持）。"""
        mrow = _env_db.fetchone(
            "SELECT default_steps, default_cfg, default_scheduler FROM models WHERE invoke_key=?",
            (invoke_key,),
        )
        if not mrow:
            return
        from core.gen_params import cfg_is_locked

        # スケジューラ（注釈の既定があれば適用）
        sched = mrow["default_scheduler"]
        if sched and self._sched_combo.isEnabled():
            idx = self._sched_combo.findText(str(sched))
            if idx >= 0:
                self._sched_combo.setCurrentIndex(idx)

        # ステップ数
        if mrow["default_steps"] is not None:
            self._steps_spin.setValue(int(mrow["default_steps"]))

        # CFG（flux2 等のロック対象ベースは 1.0 固定のまま触らない）
        if (
            mrow["default_cfg"] is not None
            and mrow["default_cfg"] > 0.0
            and not cfg_is_locked(self._current_base)
        ):
            self._cfg_spin.setValue(float(mrow["default_cfg"]))

    # ── LoRAブラウザ連携 ─────────────────────────────────

    def _on_lora_toggled(self, info: dict) -> None:
        """LoRABrowser のダブルクリックでLoRAを追加/削除する。"""
        if info.get("action") == "add":
            self._lora_bar.add_lora(info)
            # トリガーワード・ネガティブセットをチェックボックスで選択して追加
            self._add_lora_sets(info["invoke_key"])
        else:
            self._lora_bar.remove_lora(info["invoke_key"])
        # LoRAブラウザの選択状態を同期
        self._lora_browser.set_selected_keys(self._lora_bar.get_selected_keys())

    def _on_history_lora_dropped(self, invoke_key: str) -> None:
        row = _env_db.fetchone(
            "SELECT invoke_key, name, base, invoke_hash FROM models WHERE invoke_key=?",
            (invoke_key,),
        )
        if row:
            info = {
                "invoke_key": row["invoke_key"],
                "name": row["name"] or invoke_key,
                "base": row["base"] or "sdxl",
                "hash": row["invoke_hash"] or "",
                "weight": 0.75,
                "enabled": True,
            }
        else:
            info = {
                "invoke_key": invoke_key,
                "name": invoke_key,
                "base": "sdxl",
                "hash": "",
                "weight": 0.75,
                "enabled": True,
            }
        already_selected = invoke_key in self._lora_bar.get_selected_keys()
        self._lora_bar.add_lora(info)
        if not already_selected:
            self._add_lora_sets_auto(invoke_key)
        else:
            self._show_status(tr("main.lora_history_already_added"))
        self._lora_browser.set_selected_keys(self._lora_bar.get_selected_keys())

    def _add_lora_sets_auto(self, invoke_key: str) -> None:
        """履歴LoRA D&D用。全トリガーセットを確認なしで展開する。"""
        pos_sets = _env_db.fetchall(
            "SELECT label, trigger_words FROM lora_trigger_sets "
            "WHERE invoke_key=? ORDER BY sort_order",
            (invoke_key,),
        )
        neg_sets = _env_db.fetchall(
            "SELECT label, neg_words FROM lora_neg_prompt_sets "
            "WHERE invoke_key=? ORDER BY sort_order",
            (invoke_key,),
        )
        pos_variants = [
            (r["label"] or tr("main.lora_trigger_default_label"), (r["trigger_words"] or "").strip())
            for r in pos_sets
            if (r["trigger_words"] or "").strip()
        ]
        neg_variants = [
            (r["label"] or "ネガティブ", (r["neg_words"] or "").strip())
            for r in neg_sets
            if (r["neg_words"] or "").strip()
        ]
        self._add_lora_trigger_variants(invoke_key, pos_variants, neg_variants)

    def _add_lora_sets(self, invoke_key: str) -> None:
        """
        LoRA のトリガーワードセット・ネガティブプロンプトセットを選択ダイアログで
        ユーザーに提示し、チェックされたセットをそれぞれのブロックに追加する。
        """
        row = _env_db.fetchone(
            "SELECT name FROM models WHERE invoke_key=?", (invoke_key,)
        )
        if not row:
            return
        lora_name = row["name"] or invoke_key

        # ポジティブ トリガーワードセット
        pos_sets = _env_db.fetchall(
            "SELECT label, trigger_words FROM lora_trigger_sets "
            "WHERE invoke_key=? ORDER BY sort_order",
            (invoke_key,),
        )
        pos_variants: list[tuple[str, str]] = [
            (r["label"] or tr("main.lora_trigger_default_label"), r["trigger_words"].strip())
            for r in pos_sets
            if (r["trigger_words"] or "").strip()
        ]

        # ネガティブプロンプトセット
        neg_sets = _env_db.fetchall(
            "SELECT label, neg_words FROM lora_neg_prompt_sets "
            "WHERE invoke_key=? ORDER BY sort_order",
            (invoke_key,),
        )
        neg_variants: list[tuple[str, str]] = [
            (r["label"] or "ネガティブ", r["neg_words"].strip())
            for r in neg_sets
            if (r["neg_words"] or "").strip()
        ]

        # セットが何もなければ何もしない
        if not pos_variants and not neg_variants:
            return

        # 選択ダイアログを表示
        selected_pos, selected_neg = self._show_lora_sets_dialog(
            lora_name, pos_variants, neg_variants
        )

        self._add_lora_trigger_variants(invoke_key, selected_pos, selected_neg)

    def _add_lora_trigger_variants(
        self,
        invoke_key: str,
        selected_pos: list[tuple[str, str]],
        selected_neg: list[tuple[str, str]],
    ) -> None:
        from ui.block_widget import BlockWidget
        from core.prompt_builder import TagTile, GroupTile

        def _build_tiles(text: str) -> list:
            tiles = []
            for tile in BlockWidget._parse_tag_input(text):
                if isinstance(tile, TagTile):
                    tile = BlockWidget._enrich_tile_from_db(tile)
                    tile.is_trigger_word = True
                    tile.lora_source_key = invoke_key
                tiles.append(tile)
            return tiles

        # 選択されたポジティブセットを末尾ブロックへ追加
        for label, trigger_text in selected_pos:
            tiles = _build_tiles(trigger_text)
            if not tiles:
                continue
            if len(tiles) == 1:
                self._editor.add_tile_to_bottom(tiles[0])
            else:
                grp = GroupTile(name=label)   # セット名をグループ名に使用
                grp.tiles = tiles
                grp.lora_source_key = invoke_key
                self._editor.add_tile_to_bottom(grp)

        # 選択されたネガティブセットをネガティブブロックへ追加
        for label, neg_text in selected_neg:
            tiles = _build_tiles(neg_text)
            if not tiles:
                continue
            if len(tiles) == 1:
                self._editor.add_tile_to_negative(tiles[0])
            else:
                grp = GroupTile(name=label)   # セット名をグループ名に使用
                grp.tiles = tiles
                grp.lora_source_key = invoke_key
                self._editor.add_tile_to_negative(grp)

    @staticmethod
    def _show_lora_sets_dialog(
        lora_name: str,
        pos_variants: list[tuple[str, str]],
        neg_variants: list[tuple[str, str]],
    ) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
        """
        トリガーワードセットとネガティブプロンプトセットをチェックボックスで選択する
        ダイアログ。チェックされたセットを (selected_pos, selected_neg) で返す。
        キャンセル時はどちらも空リストを返す。
        """
        from PySide6.QtWidgets import (
            QDialog, QVBoxLayout, QLabel, QCheckBox,
            QDialogButtonBox, QFrame, QScrollArea, QWidget,
        )
        from ui.styles import SURFACE0, SURFACE1, SURFACE2, TEXT, SUBTEXT, ACCENT, RED, ui_font

        dlg = QDialog()
        dlg.setWindowTitle(f"LoRA セット選択:  {lora_name}")
        dlg.setMinimumWidth(460)
        dlg.setStyleSheet(f"QDialog {{ background: {SURFACE0}; color: {TEXT}; }}")

        outer = QVBoxLayout(dlg)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        # スクロール領域（セット数が多い場合に対応）
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setSpacing(6)
        lay.setContentsMargins(4, 4, 4, 4)
        scroll.setWidget(inner)
        outer.addWidget(scroll, stretch=1)

        pos_checks: list[tuple[QCheckBox, str, str]] = []
        neg_checks: list[tuple[QCheckBox, str, str]] = []

        if pos_variants:
            sec = QLabel("▸ ポジティブ  トリガーワード")
            sec.setFont(ui_font(bold=True))
            sec.setStyleSheet(f"color: {ACCENT};")
            lay.addWidget(sec)
            for label, words in pos_variants:
                frame = QFrame()
                frame.setStyleSheet(
                    f"QFrame {{ background: {SURFACE1}; border: 1px solid {SURFACE2}; "
                    f"border-radius: 4px; }}"
                )
                fl = QVBoxLayout(frame)
                fl.setContentsMargins(8, 4, 8, 4)
                fl.setSpacing(2)
                cb = QCheckBox(label)
                cb.setFont(ui_font(bold=True))
                cb.setChecked(True)
                fl.addWidget(cb)
                preview = words if len(words) <= 100 else words[:100] + "…"
                pl = QLabel(preview)
                pl.setFont(ui_font(-1))
                pl.setStyleSheet(f"color: {SUBTEXT};")
                pl.setWordWrap(True)
                fl.addWidget(pl)
                lay.addWidget(frame)
                pos_checks.append((cb, label, words))

        if neg_variants:
            sec2 = QLabel("▸ ネガティブ  プロンプト")
            sec2.setFont(ui_font(bold=True))
            sec2.setStyleSheet(f"color: {RED};")
            lay.addWidget(sec2)
            for label, words in neg_variants:
                frame = QFrame()
                frame.setStyleSheet(
                    f"QFrame {{ background: {SURFACE1}; border: 1px solid {SURFACE2}; "
                    f"border-radius: 4px; }}"
                )
                fl = QVBoxLayout(frame)
                fl.setContentsMargins(8, 4, 8, 4)
                fl.setSpacing(2)
                cb = QCheckBox(label)
                cb.setFont(ui_font(bold=True))
                cb.setChecked(True)
                fl.addWidget(cb)
                preview = words if len(words) <= 100 else words[:100] + "…"
                pl = QLabel(preview)
                pl.setFont(ui_font(-1))
                pl.setStyleSheet(f"color: {SUBTEXT};")
                pl.setWordWrap(True)
                fl.addWidget(pl)
                lay.addWidget(frame)
                neg_checks.append((cb, label, words))

        # ダイアログの高さをセット数に合わせて調整
        total = len(pos_variants) + len(neg_variants)
        dlg.setMinimumHeight(min(640, total * 88 + 120))

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        outer.addWidget(btns)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return [], []

        selected_pos = [(lbl, wds) for cb, lbl, wds in pos_checks if cb.isChecked()]
        selected_neg = [(lbl, wds) for cb, lbl, wds in neg_checks if cb.isChecked()]
        return selected_pos, selected_neg

    def _on_lora_tile_enable_changed(self, invoke_key: str, enabled: bool) -> None:
        """LoRA の有効/無効に合わせてそのトリガーワードタイル（GroupTile内含む）を ON/OFF する。"""
        from core.prompt_builder import TagTile, GroupTile

        def _set_enabled(tiles: list) -> bool:
            changed = False
            for tile in tiles:
                if isinstance(tile, GroupTile):
                    # GroupTile 自体が LoRA グループなら ON/OFF
                    if getattr(tile, "lora_source_key", "") == invoke_key:
                        tile.enabled = enabled
                        changed = True
                    else:
                        changed |= _set_enabled(tile.tiles)
                elif isinstance(tile, TagTile):
                    if getattr(tile, "lora_source_key", "") == invoke_key:
                        tile.enabled = enabled
                        changed = True
            return changed

        for bw in self._editor.all_block_widgets():
            if _set_enabled(bw.block.tiles):
                bw._refresh_tiles()

    def _on_lora_tile_removed(self, invoke_key: str) -> None:
        """LoRA 削除時にそのトリガーワードタイル（GroupTile含む）も削除する。"""
        from core.prompt_builder import TagTile, GroupTile

        def _is_lora_tile(tile) -> bool:
            return (isinstance(tile, (TagTile, GroupTile))
                    and getattr(tile, "lora_source_key", "") == invoke_key)

        def _filter(tiles: list) -> tuple[list, bool]:
            new_tiles = []
            changed = False
            for tile in tiles:
                if _is_lora_tile(tile):
                    changed = True
                elif isinstance(tile, GroupTile):
                    inner, c = _filter(tile.tiles)
                    tile.tiles = inner
                    changed |= c
                    new_tiles.append(tile)
                else:
                    new_tiles.append(tile)
            return new_tiles, changed

        for bw in self._editor.all_block_widgets():
            new_tiles, changed = _filter(bw.block.tiles)
            if changed:
                bw.block.tiles = new_tiles
                bw._refresh_tiles()
                bw.block_changed.emit()

    def _on_lora_bar_changed(self) -> None:
        """LoRAチップバーが変化したとき、LoRAブラウザの選択表示を同期する。"""
        self._lora_browser.set_selected_keys(self._lora_bar.get_selected_keys())

    def _focus_lora_tab(self) -> None:
        """LoRAチップバーの「+LoRA」ボタンから現在モデル用LoRAを開く。"""
        self._set_left_mode("loras")
        # 左ペインが折りたたまれていれば開く
        if not self._left_visible:
            self._toggle_left()
        base = self._current_base
        if self._selected_model_key:
            row = _env_db.fetchone(
                "SELECT base FROM models WHERE invoke_key=?", (self._selected_model_key,)
            )
            if row and row["base"]:
                base = row["base"]
        self._lora_browser.focus_base_group(base)

    # ── タグブラウザ連携 ─────────────────────────────────

    def _on_prompt_text_add_to_center(self, prompt_text_id: int) -> None:
        """文章プロンプト一覧からダブルクリック/D&D で中央ペインに NaturalTextTile を追加する。"""
        from core.prompt_builder import NaturalTextTile
        record = _library_db.fetchone(
            "SELECT * FROM prompt_texts WHERE id = ?", (prompt_text_id,)
        )
        if record is None:
            return
        tile = NaturalTextTile(
            text=record["translated_text"] or record["source_text"],
            source_text=record["source_text"],
            translated_text=record["translated_text"] or record["source_text"],
            display_label=record["display_label"] or "",
        )
        self._editor.add_tile_to_focused(tile)

    def _on_group_preset_add_to_center(self, group_json: str, preset_name: str = "") -> None:
        """タイルグループ一覧からダブルクリックで中央ペインに GroupTile を追加する。"""
        import json
        from core.prompt_builder import GroupTile

        try:
            group = GroupTile.from_dict(
                json.loads(group_json),
                name_override=preset_name or None,
                restore_ui_state=False,
            )
        except Exception:
            return
        self._editor.add_tile_to_focused(group)

    def _on_tag_selected(self, tag_name: str, tag_local: str, category: str = "", dictionary_key: str = "") -> None:
        """
        TagBrowser でタグがクリックされたとき、
        フォーカス中のブロックに TagTile を追加する。
        category を渡してタイルを正しい色で表示する。
        """
        from core.prompt_builder import TagTile
        tile = TagTile(
            tag_name=tag_name,
            tag_local=tag_local,
            category=category,
            dictionary_key=dictionary_key,
        )
        self._editor.add_tile_to_focused(tile)

    def _on_tag_updated(self, old_name_en: str, new_name_en: str,
                        new_name_local: str, new_category: str) -> None:
        """
        TagBrowser でタグが編集・保存されたとき、
        エディタ内の一致するタイルを全て更新する。
        """
        from core.prompt_builder import TagTile, GroupTile

        def _update_tiles(tiles: list) -> bool:
            changed = False
            for tile in tiles:
                if isinstance(tile, GroupTile):
                    changed |= _update_tiles(tile.tiles)
                elif isinstance(tile, TagTile) and tile.tag_name == old_name_en:
                    tile.tag_name  = new_name_en
                    tile.tag_local = new_name_local
                    tile.category  = new_category
                    changed = True
            return changed

        for bw in self._editor.all_block_widgets():
            if _update_tiles(bw.block.tiles):
                bw._refresh_tiles()

    def _on_tag_categories_changed(self) -> None:
        """
        TagBrowser でタグ移動によりカテゴリ色が変わったとき、
        中央ペインの表示中タイルも即時に現在カテゴリへ合わせる。
        """
        for bw in self._editor.all_block_widgets():
            bw.refresh_tile_styles()

    def _current_tile_display_mode(self) -> str:
        """"0"=2段 / "1"=上1段（現地語）/ "2"=下1段（英語）"""
        value = _get_setting("tile_local_only_display", "0")
        return value if value in ("0", "1", "2") else "0"

    def _current_language_display_name(self) -> str:
        """現在のUI言語の自言語表示名（例: 日本語 / English / 한국어）を返す。"""
        from core.i18n import available_languages, current_language
        code = current_language()
        for c, name in available_languages():
            if c == code:
                return name
        return code

    def _update_tile_display_btn(self) -> None:
        """ボタンに現在の表示状態を示す。

        常に1行表示（2行にするとツールバーの高さが伸び縮みしてしまうため）。
        2段=「<現地語名>/Prompt」/ 上1段=「<現地語名>」/ 下1段=「Prompt」
        ※「ENGLISH」ではなく「Prompt」: 下段の実体は送信されるプロンプト側であり、
          将来プロンプトが英語以外のモデルにも対応できる表記にしている。
        """
        mode = self._current_tile_display_mode()
        local_name = self._current_language_display_name()
        if mode == "1":
            label = local_name
        elif mode == "2":
            label = "Prompt"
        else:
            label = f"{local_name}/Prompt"
        self._tile_display_btn.setText(label)
        self._tile_display_btn.setToolTip(tr("main.tile_display_cycle_tooltip"))

    def _on_tile_display_mode_clicked(self) -> None:
        order = ("0", "1", "2")
        current = self._current_tile_display_mode()
        next_mode = order[(order.index(current) + 1) % len(order)]
        _set_setting("tile_local_only_display", next_mode)
        self._apply_tile_display_setting()

    def _apply_tile_display_setting(self) -> None:
        self._update_tile_display_btn()
        for bw in self._editor.all_block_widgets():
            bw.refresh_tile_display()
        self._editor.refresh_layout()

    def _refresh_runtime_ui(self) -> None:
        """設定変更を現在のウィンドウへその場で反映する。"""
        self.setWindowTitle("PromptMosaic")
        self._btn_left.setToolTip(tr("main.btn_left_tooltip"))
        self._btn_right.setToolTip(tr("main.btn_right_tooltip"))
        self._btn_recall.setText(tr("main.btn_recall"))
        self._btn_recall.setToolTip(tr("main.btn_recall_tooltip"))
        self._btn_gen.setText(tr("main.btn_generate"))
        self._btn_gen.setToolTip(tr("main.btn_generate_tooltip"))
        self._btn_cancel_plan.setText(self._short_toolbar_label(tr("main.btn_stop")))
        self._btn_cancel_plan.setToolTip(tr("main.btn_cancel_plan_tooltip"))
        self._history_one_cb.setText(tr("main.history_one_checkbox"))
        self._history_one_cb.setToolTip(tr("main.history_one_tooltip"))
        self._history_map_cb.setText(tr("main.history_map_checkbox"))
        self._history_map_cb.setToolTip(tr("main.history_map_record_tooltip"))
        self._count_spin.setToolTip(tr("main.count_tooltip"))
        self._width_spin.setToolTip(tr("main.width_tooltip"))
        self._height_spin.setToolTip(tr("main.height_tooltip"))
        self._seed_icon.setToolTip(tr("main.seed_icon_tooltip"))
        self._seed_random_cb.setToolTip(tr("main.seed_random_tooltip"))
        self._seed_spin.setToolTip(tr("main.seed_value_tooltip"))
        self._btn_rand.setToolTip(tr("main.seed_randomize_tooltip"))
        self._btn_shuffle.setToolTip(tr("main.btn_shuffle_tooltip"))
        self._btn_clear_all.setToolTip(tr("main.btn_clear_all_tooltip"))
        self._btn_settings.setToolTip(tr("main.btn_settings_tooltip"))
        self._model_base_combo.setToolTip(tr("main.model_base_tooltip"))
        self._model_combo.setToolTip(
            tr("main.plan_combo_tooltip") if self._is_plan_mode() else tr("main.model_label_tooltip")
        )
        self._btn_model_reload.setToolTip(tr("main.model_reload_tooltip"))
        self._steps_spin.setToolTip(tr("main.steps_tooltip"))
        self._cfg_spin.setToolTip(tr("main.cfg_tooltip"))
        self._sched_combo.setToolTip(tr("main.scheduler_tooltip"))
        self._board_label.setText(tr("main.board_label"))
        self._board_combo.setToolTip(tr("main.board_tooltip"))
        self._btn_board_reload.setToolTip(tr("main.board_reload_tooltip"))
        self._lm_label.setText(tr("main.translate_lm_label"))
        self._lm_model_combo.setToolTip(tr("main.translate_lm_tooltip"))
        self._btn_lm_reload.setToolTip(tr("main.translate_lm_reload_tooltip"))
        self._lm_prompt_btn.setToolTip(tr("main.translate_prompt_tooltip"))
        self._lm_note_edit.setPlaceholderText(tr("main.translate_lm_note_placeholder"))
        self._lm_note_edit.setToolTip(tr("main.translate_lm_note_tooltip"))
        self._apply_model_mode_ui()
        self._update_left_pane_texts()

        self._apply_tile_display_setting()
        self._apply_main_control_styles()
        self._refresh_child_views_for_style_settings()
        self._check_connection()

    def _apply_main_control_styles(self) -> None:
        self._btn_recall.setStyleSheet(themed_button_style("accent"))
        self._btn_gen.setStyleSheet(themed_button_style("success"))
        if hasattr(self, "_btn_cancel_plan"):
            self._btn_cancel_plan.setStyleSheet(themed_button_style("danger"))
        option_style = (
            f"QCheckBox {{ color: {SUBTEXT}; background: transparent; spacing: 3px; }}"
            f"QCheckBox::indicator {{ width: 14px; height: 14px; }}"
            f"QCheckBox::indicator:checked {{ background: {ACCENT}; border: 1px solid {ACCENT}; }}"
            f"QCheckBox::indicator:unchecked {{ background: {SURFACE0}; border: 1px solid {SURFACE2}; }}"
        )
        if hasattr(self, "_history_one_cb"):
            self._history_one_cb.setStyleSheet(option_style)
        if hasattr(self, "_history_map_cb"):
            self._history_map_cb.setStyleSheet(option_style)
        combo_style = (
            f"QComboBox {{ color: {TEXT}; padding: 0 26px 0 4px; min-height: 20px; "
            f"border: 1px solid {SURFACE2}; border-radius: 3px; background: {SURFACE0}; }}"
            f"QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: top right; "
            f"width: 22px; border: none; background: transparent; }}"
            f"QComboBox::down-arrow {{ image: url(\"{COMBO_ARROW_URL}\"); width: 10px; height: 10px; }}"
        )
        self._model_base_combo.setStyleSheet(combo_style)
        self._model_combo.setStyleSheet(combo_style)
        self._board_combo.setStyleSheet(combo_style)
        self._lm_note_edit.setStyleSheet(
            f"QLineEdit {{ color: {TEXT}; padding: 0 4px; "
            f"border: 1px solid {SURFACE2}; border-radius: 3px; background: {SURFACE0}; }}"
        )

    def _refresh_child_views_for_style_settings(self) -> None:
        self._editor.retranslate_and_restyle()
        self._tag_browser.reload()
        for panel in (self._model_browser, self._lora_browser, self._side_panel):
            if hasattr(panel, "retranslate_and_restyle"):
                panel.retranslate_and_restyle()
            panel.setStyleSheet(panel.styleSheet())
        self._group_preset_browser.retranslate_and_restyle()
        self._prompt_text_browser.retranslate_and_restyle()
        self._prompt_text_browser.setStyleSheet(self._prompt_text_browser.styleSheet())

    def _apply_nsfw_setting(self) -> None:
        """NSFW表示設定を、該当する一覧だけへ即時反映する。"""
        show_nsfw = _get_setting("show_nsfw", "0") == "1"
        if hasattr(self._tag_browser, "set_show_nsfw"):
            self._tag_browser.set_show_nsfw(show_nsfw)
        else:
            self._tag_browser.reload()
        self._model_browser.refresh()
        self._lora_browser.refresh()
        self._prompt_text_browser.refresh()
        self._side_panel.refresh_for_nsfw_setting()

    def _apply_runtime_settings(self, theme_override: str | None = None) -> None:
        """保存済みの言語・テーマ・フォントを現在のプロセスへ再適用する。"""
        lang = _get_setting("language", "ja")
        theme = theme_override or _get_setting("theme", "dark")
        try:
            font_pt = int(_get_setting("font_size", "10"))
        except (ValueError, TypeError):
            font_pt = 10

        set_language(lang)

        import ui.styles as styles
        styles.configure_theme(theme)
        styles.load_categories_from_db()

        app = QApplication.instance()
        if app is not None:
            styles.apply_palette(app, font_pt=font_pt)

        self._sync_style_bindings(styles)

    @staticmethod
    def _sync_style_bindings(styles_module) -> None:
        """`from ui.styles import SURFACE0` 形式で掴んだ色定数を最新化する。"""
        names = (
            "BASE", "SURFACE0", "SURFACE1", "SURFACE2", "TEXT", "SUBTEXT",
            "ACCENT", "GREEN", "RED", "YELLOW", "OVERLAY", "MANTLE",
            "CATEGORY_COLORS", "BLOCK_HEADER_COLORS",
        )
        for module_name, module in list(sys.modules.items()):
            if module_name == "ui.styles" or module_name.startswith("ui."):
                for name in names:
                    if hasattr(module, name):
                        setattr(module, name, getattr(styles_module, name))

    # ── モデルピッカー ───────────────────────────────────

    def _refresh_models(self) -> None:
        """
        ツールバーの 🔄 ボタン用。
        ModelBrowser / LoRABrowser の同期（Invoke → DB）を実行する。
        スケジューラーリストも Invoke から再取得する。
        """
        # スケジューラーマップを更新（非ブロッキング、失敗時は既存マップを維持）
        try:
            fetched = self._client.fetch_scheduler_map()
            if fetched:
                self._scheduler_map = fetched
                # 現在のベースのスケジューラーリストを即時反映
                self._apply_base_ui(self._current_base)
        except Exception:
            pass

        if hasattr(self, "_model_browser"):
            self._populate_base_plan_combo()
            self._populate_model_combo()
            self._model_browser._sync()
            if getattr(self._model_browser, "_sync_worker", None) is not None:
                self._model_browser._sync_worker.finished.connect(self._populate_base_plan_combo)
                self._model_browser._sync_worker.finished.connect(self._populate_model_combo)

    # ── ウィンドウ状態復元 ───────────────────────────────

    def _restore_window_state(self) -> None:
        """
        起動時にウィンドウサイズ・位置・スプリッター位置を復元する。
        保存データがないときはデフォルト値のまま。
        """
        w_str = _get_setting("window_width",  "")
        h_str = _get_setting("window_height", "")
        if w_str and h_str:
            try:
                self.resize(int(w_str), int(h_str))
            except ValueError:
                pass

        x_str = _get_setting("window_x", "")
        y_str = _get_setting("window_y", "")
        if x_str and y_str:
            try:
                self.move(int(x_str), int(y_str))
            except ValueError:
                pass

        sizes_str = _get_setting("splitter_sizes", "")
        if sizes_str:
            try:
                sizes = json.loads(sizes_str)
                if len(sizes) == 3 and all(isinstance(s, int) for s in sizes):
                    self._splitter.setSizes(sizes)
                    # 折りたたみサイズ記憶も更新
                    if sizes[0] > 0:
                        self._left_size = sizes[0]
                    if sizes[2] > 0:
                        self._right_size = sizes[2]
            except (ValueError, TypeError):
                pass

    def _restore_lora_state(self) -> None:
        """
        起動時に保存済みの LoRA 選択状態を復元する。
        closeEvent で保存した last_loras_json をロードする。
        """
        loras_json = _get_setting("last_loras_json", "")
        if not loras_json:
            return
        try:
            loras = json.loads(loras_json)
            if loras and isinstance(loras, list):
                self._lora_bar.set_loras(loras)
                self._lora_browser.set_selected_keys(self._lora_bar.get_selected_keys())
        except Exception:
            pass  # 復元失敗時は空のままにする

    def _restore_last_group(self) -> None:
        """起動時に前回の生成先グループを復元する。"""
        gid_str = _get_setting("last_group_id", "")
        if not gid_str:
            return
        try:
            gid = int(gid_str)
        except ValueError:
            return
        row = _history_db.fetchone("SELECT id FROM generation_groups WHERE id=?", (gid,))
        if not row:
            return
        self._side_panel.restore_group_id(gid)

    def _ensure_current_group(self) -> int:
        """現在の生成先グループを必ず有効なグループIDにする。"""
        if self._current_group_id is not None:
            row = _history_db.fetchone(
                "SELECT id FROM generation_groups WHERE id=?",
                (self._current_group_id,),
            )
            if row:
                return self._current_group_id

        gid = _history_db.ensure_default_generation_group()
        self._current_group_id = gid
        self._side_panel.restore_group_id(gid)
        return gid

    def _restore_model_selection(self) -> None:
        """
        起動時に保存済みの選択モデルをラベルに反映する。
        DB に名前があればそれを表示し、なければキーを短縮表示する。
        テンプレートはベースに1個だけ登録されている時のみ自動採用する。
        """
        saved_key = _get_setting("selected_model_key", "")
        if not saved_key:
            return
        self._selected_model_key = saved_key
        row = _env_db.fetchone(
            "SELECT name, base FROM models WHERE invoke_key=?", (saved_key,))
        model_name = ""
        if row and row["name"]:
            model_name = row["name"]
            if row["base"]:
                self._current_base = row["base"]
                self._apply_base_ui(row["base"])
                self._populate_model_combo()
                # テンプレートが1個だけなら自動採用
                t_rows = _env_db.fetchall(
                    "SELECT id, name FROM templates WHERE base=? "
                    "ORDER BY is_base_default DESC, name ASC",
                    (row["base"],),
                )
                if len(t_rows) == 1:
                    self._current_template_id = t_rows[0]["id"]
                    self._current_template_name = t_rows[0]["name"]
                self._apply_negative_prompt_ui()
        else:
            # DB未同期の場合はキーの先頭16文字を表示
            model_name = saved_key[:16] + "…"
        self._update_model_label(model_name)

    def _apply_app_icon(self) -> None:
        """設定アイコン、または同梱のPromptMosaic既定アイコンを適用する。"""
        from core.app_icon import apply_app_icon
        apply_app_icon(self)

    # ── ユーティリティ ───────────────────────────────────

    def _restore_last_prompt(self) -> None:
        """起動時に前回終了時のプロンプトを復元する"""
        from core.prompt_builder import PromptDocument
        json_str = _get_setting("last_prompt_json", "")
        if not json_str:
            return
        try:
            doc = PromptDocument.from_json(json_str)
            self._editor.set_document(doc)
        except Exception:
            pass  # 復元失敗時は空のままにする

    def _on_prompt_changed(self) -> None:
        self._editor_dirty = True
        self._update_generation_buttons()

    def _refresh_material_browsers(self) -> None:
        if hasattr(self, "_tag_browser"):
            self._tag_browser.reload()
        if hasattr(self, "_group_preset_browser"):
            self._group_preset_browser.refresh()
        if hasattr(self, "_prompt_text_browser"):
            self._prompt_text_browser.refresh()
        if hasattr(self, "_update_prompt_search_count_label"):
            self._update_prompt_search_count_label()

    def _show_status(self, msg: str, error: bool = False) -> None:
        color = RED if error else GREEN
        self.statusBar().showMessage(msg, 5000)

    def closeEvent(self, event) -> None:
        if self._translate_worker and self._translate_worker.isRunning():
            self._translate_worker.cancel_and_wait()
        self._translate_worker = None
        self._side_panel.save_notes_if_dirty()
        if hasattr(self._side_panel, "save_history_tree_state"):
            self._side_panel.save_history_tree_state()
        center_map = getattr(self._editor, "parent_child_map", None)
        if center_map is not None and hasattr(center_map, "save_view_state_now"):
            center_map.save_view_state_now()
        viewer_open = False
        viewer_key: tuple[str, int] | None = None
        map_view_state: dict[str, float | int] = {"zoom": 1.0, "hscroll": 0, "vscroll": 0}
        if self._history_map_dialog is not None and hasattr(self._history_map_dialog, "image_viewer_state"):
            viewer_open, viewer_key = self._history_map_dialog.image_viewer_state()
        if self._history_map_dialog is not None and hasattr(self._history_map_dialog, "map_view_state"):
            try:
                map_view_state = self._history_map_dialog.map_view_state()
            except Exception:
                map_view_state = {"zoom": 1.0, "hscroll": 0, "vscroll": 0}
        # パラメータバーの値を保存
        params_to_save = [
            ("gen_seed",           "-1" if self._seed_random_cb.isChecked() else str(self._seed_spin.value())),
            ("gen_seed_fixed",     "1" if self._seed_fixed_btn.isChecked() else "0"),
            ("gen_steps",          str(self._steps_spin.value())),
            ("gen_cfg",            str(self._cfg_spin.value())),
            ("gen_scheduler",      self._sched_combo.currentText()),
            ("gen_width",          str(self._width_spin.value())),
            ("gen_height",         str(self._height_spin.value())),
            ("gen_count",          str(self._count_spin.value())),
            ("gen_history_one",    "1" if self._history_one_cb.isChecked() else "0"),
            ("gen_history_map",    "1" if self._history_map_cb.isChecked() else "0"),
            ("selected_model_key", self._selected_model_key),
            ("selected_board_id", self._current_board_id() or ""),
            # ウィンドウ状態（サイズ・位置・スプリッター）
            ("window_width",       str(self.width())),
            ("window_height",      str(self.height())),
            ("window_x",           str(self.x())),
            ("window_y",           str(self.y())),
            ("splitter_sizes",     json.dumps(self._splitter.sizes())),
            # プロンプトエディタの状態を保存（次回起動時に復元）
            ("last_prompt_json",   self._document_for_generation().to_json()),
            # LoRA選択状態を保存（次回起動時に復元）
            ("last_loras_json",    json.dumps(self._lora_bar.get_loras(), ensure_ascii=False)),
            # 翻訳LMモデル選択を保存（次回起動時に復元）
            ("lm_translate_model", self._current_lm_model_id()),
            # 生成先グループIDを保存（次回起動時に復元）
            ("last_group_id", str(self._current_group_id) if self._current_group_id else ""),
            # 履歴マップを開いたまま終了したら次回起動時に再現する
            ("history_map_open",
             "1" if (self._history_map_dialog is not None
                     and self._history_map_dialog.isVisible()) else "0"),
            ("history_map_geometry",
             bytes(self._history_map_dialog.saveGeometry().toHex()).decode("ascii")
             if (self._history_map_dialog is not None
                 and self._history_map_dialog.isVisible()) else ""),
            ("history_image_viewer_open", "1" if viewer_open and viewer_key is not None else "0"),
            ("history_image_viewer_history_db", viewer_key[0] if viewer_open and viewer_key is not None else ""),
            ("history_image_viewer_history_id", str(viewer_key[1]) if viewer_open and viewer_key is not None else ""),
            ("history_map_zoom", str(map_view_state.get("zoom", 1.0))),
            ("history_map_hscroll", str(map_view_state.get("hscroll", 0))),
            ("history_map_vscroll", str(map_view_state.get("vscroll", 0))),
        ]
        params_to_save.extend(self._write_settings_node_key("history_map_focus", self._history_map_dialog_focus))
        params_to_save.extend(self._write_settings_node_key("history_map_opened", self._history_map_opened_node))
        params_to_save.extend(self._write_settings_node_key("history_map_view_root", self._history_map_view_root))
        for key, value in params_to_save:
            _app_db.execute(
                "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
                (key, value),
            )
        # セッション終了: 中止と同じ処理で送信キューを空にし（不変条件の維持）、
        # 一時状態（キュー追加中・画像待ち）のまま確定に至らなかった生成行を破棄する
        # （一時状態はセッションに紐づくため、ここで終端させる）
        self._poll_timer.stop()
        self._shutdown_send_queue()
        self._finalize_transient_generations()
        from db.connections import close_all
        self._client.close()
        close_all()
        super().closeEvent(event)


