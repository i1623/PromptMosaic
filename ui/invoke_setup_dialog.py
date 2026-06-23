from __future__ import annotations

from PySide6.QtCore import QThread, QTimer, QElapsedTimer, Signal, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QVBoxLayout,
    QLabel,
    QMessageBox,
    QInputDialog,
    QComboBox,
    QPushButton,
    QToolButton,
    QWidget,
    QFrame,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
)

import db.app_db as _app_db
import db.env_db as _env_db
from api.invoke_client import InvokeClient, TemplateBaseMismatch
from core.i18n import available_languages, current_language, set_language, tr
from ui.model_browser import _SyncWorker, _base_label
from ui.styles import (
    ACCENT, GREEN, RED, YELLOW, SUBTEXT, SURFACE0, SURFACE1, SURFACE2, TEXT,
    ui_font, themed_button_style,
)


# ── 最低表示時間（演出）────────────────────────────────
_CONN_TO_SYNC_DELAY_MS = 3000   # 接続確認後、取得開始までの待ち
_SYNC_MIN_DISPLAY_MS = 3000     # 「取得中」を最低でも出す時間
# → ステップ1は最低 6 秒かかる


class _SetupConnWorker(QThread):
    ok = Signal()
    ng = Signal(str)

    def __init__(self, client: InvokeClient, parent=None):
        super().__init__(parent)
        self._client = client

    def run(self) -> None:
        try:
            self._client.queue_status()
            self.ok.emit()
        except Exception as exc:
            self.ng.emit(str(exc))


class _FetchTemplateWorker(QThread):
    ok = Signal(dict)
    mismatch = Signal(str)   # 期待ベース（行のベース）
    ng = Signal(str)

    def __init__(self, client: InvokeClient, expected_base: str, parent=None):
        super().__init__(parent)
        self._client = client
        self._expected_base = expected_base

    def run(self) -> None:
        try:
            res = self._client.fetch_template_graph(expected_base=self._expected_base)
            self.ok.emit(res)
        except TemplateBaseMismatch:
            self.mismatch.emit(self._expected_base)
        except Exception as exc:
            self.ng.emit(str(exc))


class InvokeSetupDialog(QDialog):
    """InvokeAI データ取得（接続→モデル/LoRA取得→ベース別テンプレート取得）。"""

    setup_changed = Signal()
    language_changed = Signal()

    # 列インデックス
    _COL_DELETE = 0
    _COL_BASE = 1
    _COL_NAME = 2
    _COL_FETCH = 3

    def __init__(self, client: InvokeClient, parent=None):
        super().__init__(parent)
        self._client = client
        self._conn_worker: _SetupConnWorker | None = None
        self._sync_worker: _SyncWorker | None = None
        self._fetch_worker: _FetchTemplateWorker | None = None
        self._connected = False
        self._step1_started = False
        self._step1_done = False
        self._sync_clock = QElapsedTimer()
        self._updating_table = False

        self.setWindowTitle(tr("invoke_setup.title"))
        self.setMinimumWidth(720)
        self.resize(820, 880)
        self.setStyleSheet(f"QDialog {{ background-color: {SURFACE0}; color: {TEXT}; }}")

        self._retry_timer = QTimer(self)
        self._retry_timer.setInterval(3000)
        self._retry_timer.timeout.connect(self._check_connection)

        self._build_ui()
        self._check_connection()

    # ── UI 構築 ────────────────────────────────────────
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        # ヘッダー（タイトル＋言語）
        header_row = QHBoxLayout()
        self._title = QLabel(tr("invoke_setup.heading"))
        self._title.setFont(ui_font(2, bold=True))
        self._title.setStyleSheet(f"color: {ACCENT};")
        header_row.addWidget(self._title, stretch=1)
        self._lang_label = QLabel("Language:")
        self._lang_label.setStyleSheet(f"color: {SUBTEXT};")
        header_row.addWidget(self._lang_label)
        self._lang_combo = QComboBox()
        self._populate_language_combo(current_language())
        self._lang_combo.currentIndexChanged.connect(self._on_language_changed)
        header_row.addWidget(self._lang_combo)
        root.addLayout(header_row)

        # 取得する情報の説明
        self._intro = QLabel(tr("invoke_setup.intro"))
        self._intro.setWordWrap(True)
        self._intro.setStyleSheet(f"color: {TEXT};")
        root.addWidget(self._intro)

        # ── ステップ1 ──────────────────────────────────
        self._step1_box = self._make_step_box(tr("invoke_setup.step1_title"))
        s1 = self._step1_box.layout()
        self._conn_label = QLabel(tr("invoke_setup.step1_waiting"))
        self._conn_label.setWordWrap(True)
        self._conn_label.setStyleSheet(
            f"color: {SUBTEXT}; background: {SURFACE0}; padding: 8px; border-radius: 4px;"
        )
        s1.addWidget(self._conn_label)
        self._retry_btn = QPushButton(tr("invoke_setup.retry"))
        self._retry_btn.clicked.connect(self._check_connection)
        rrow = QHBoxLayout()
        rrow.addWidget(self._retry_btn)
        rrow.addStretch(1)
        s1.addLayout(rrow)
        root.addWidget(self._step1_box)

        # ── ステップ2 ──────────────────────────────────
        self._step2_box = self._make_step_box(tr("invoke_setup.step2_title"))
        s2 = self._step2_box.layout()
        self._step2_info = QLabel(tr("invoke_setup.step2_info"))
        self._step2_info.setWordWrap(True)
        self._step2_info.setStyleSheet(f"color: {TEXT};")
        s2.addWidget(self._step2_info)

        # LoRA 必須の注意書き（目立つように枠＋色付き）
        self._step2_warn = QLabel(tr("invoke_setup.step2_lora_note"))
        self._step2_warn.setWordWrap(True)
        self._step2_warn.setStyleSheet(
            f"color: {YELLOW}; background: {SURFACE0}; font-weight: bold; "
            f"border: 1px solid {YELLOW}; border-radius: 4px; padding: 8px;"
        )
        s2.addWidget(self._step2_warn)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels([
            "", tr("invoke_setup.col_base"), tr("invoke_setup.col_name"), "",
        ])
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.SelectedClicked
        )
        self._table.setStyleSheet(
            f"QTableWidget {{ background: {SURFACE1}; color: {TEXT}; "
            f"border: 1px solid {SURFACE2}; border-radius: 4px; gridline-color: {SURFACE2}; }}"
            f"QHeaderView::section {{ background: {SURFACE0}; color: {SUBTEXT}; "
            f"border: none; padding: 4px; }}"
        )
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(self._COL_DELETE, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(self._COL_BASE, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(self._COL_NAME, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(self._COL_FETCH, QHeaderView.ResizeMode.ResizeToContents)
        self._table.itemChanged.connect(self._on_name_edited)
        s2.addWidget(self._table, stretch=1)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet(f"color: {SUBTEXT};")
        s2.addWidget(self._status_label)
        root.addWidget(self._step2_box, stretch=1)

        # 閉じる
        self._buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_btn = self._buttons.button(QDialogButtonBox.StandardButton.Close)
        if close_btn is not None:
            close_btn.setText(tr("invoke_setup.close"))
        self._buttons.rejected.connect(self.reject)
        root.addWidget(self._buttons)

        self._set_step2_enabled(False)

    def _make_step_box(self, title: str) -> QFrame:
        box = QFrame()
        box.setStyleSheet(
            f"QFrame {{ background: {SURFACE1}; border: 1px solid {SURFACE2}; border-radius: 6px; }}"
        )
        lay = QVBoxLayout(box)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(6)
        lbl = QLabel(title)
        lbl.setFont(ui_font(1, bold=True))
        lbl.setStyleSheet(f"color: {ACCENT}; background: transparent;")
        lay.addWidget(lbl)
        box._title_label = lbl  # type: ignore[attr-defined]
        return box

    def _set_step2_enabled(self, enabled: bool) -> None:
        self._step2_box.setEnabled(enabled)
        self._step2_box.setStyleSheet(
            f"QFrame {{ background: {SURFACE1}; border: 1px solid {SURFACE2}; border-radius: 6px; }}"
            + ("" if enabled else f"QFrame {{ color: {SUBTEXT}; }}")
        )

    # ── 言語 ────────────────────────────────────────────
    def _populate_language_combo(self, selected: str) -> None:
        self._lang_combo.blockSignals(True)
        self._lang_combo.clear()
        for code, name in available_languages():
            self._lang_combo.addItem(name, code)
        idx = self._lang_combo.findData(selected)
        if idx < 0:
            idx = self._lang_combo.findData(current_language())
        if idx >= 0:
            self._lang_combo.setCurrentIndex(idx)
        self._lang_combo.blockSignals(False)

    def _on_language_changed(self) -> None:
        lang = self._lang_combo.currentData()
        if not isinstance(lang, str) or not lang:
            return
        _app_db.execute(
            "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
            ("language", lang),
        )
        set_language(lang)
        self._retranslate()
        self.language_changed.emit()

    def _retranslate(self) -> None:
        selected = current_language()
        self.setWindowTitle(tr("invoke_setup.title"))
        self._title.setText(tr("invoke_setup.heading"))
        self._intro.setText(tr("invoke_setup.intro"))
        self._step1_box._title_label.setText(tr("invoke_setup.step1_title"))  # type: ignore[attr-defined]
        self._step2_box._title_label.setText(tr("invoke_setup.step2_title"))  # type: ignore[attr-defined]
        self._step2_info.setText(tr("invoke_setup.step2_info"))
        self._step2_warn.setText(tr("invoke_setup.step2_lora_note"))
        self._retry_btn.setText(tr("invoke_setup.retry"))
        self._table.setHorizontalHeaderLabels([
            "", tr("invoke_setup.col_base"), tr("invoke_setup.col_name"), "",
        ])
        close_btn = self._buttons.button(QDialogButtonBox.StandardButton.Close)
        if close_btn is not None:
            close_btn.setText(tr("invoke_setup.close"))
        self._populate_language_combo(selected)
        if self._step1_done:
            self._populate_table()

    # ── ステップ1: 接続→モデル/LoRA取得 ─────────────────
    def _check_connection(self) -> None:
        if self._conn_worker and self._conn_worker.isRunning():
            return
        if not self._step1_started:
            self._conn_label.setText(tr("invoke_setup.step1_checking"))
        self._conn_worker = _SetupConnWorker(self._client, self)
        self._conn_worker.ok.connect(self._on_conn_ok)
        self._conn_worker.ng.connect(self._on_conn_ng)
        self._conn_worker.start()

    def _on_conn_ok(self) -> None:
        self._connected = True
        self._retry_timer.stop()
        if self._step1_started:
            return
        self._step1_started = True
        self._retry_btn.setVisible(False)
        self._conn_label.setText(tr("invoke_setup.step1_detected"))
        self._conn_label.setStyleSheet(
            f"color: {GREEN}; background: {SURFACE0}; padding: 8px; border-radius: 4px;"
        )
        QTimer.singleShot(_CONN_TO_SYNC_DELAY_MS, self._start_sync)

    def _on_conn_ng(self, _msg: str) -> None:
        self._connected = False
        if self._step1_started:
            return
        self._conn_label.setText(tr("invoke_setup.step1_waiting"))
        self._conn_label.setStyleSheet(
            f"color: {RED}; background: {SURFACE0}; padding: 8px; border-radius: 4px;"
        )
        if not self._retry_timer.isActive():
            self._retry_timer.start()

    def _start_sync(self) -> None:
        if self._sync_worker and self._sync_worker.isRunning():
            return
        self._conn_label.setText(tr("invoke_setup.step1_syncing"))
        self._sync_clock.restart()
        self._sync_worker = _SyncWorker(self._client, self)
        self._sync_worker.finished.connect(self._on_sync_done)
        self._sync_worker.error.connect(self._on_sync_failed)
        self._sync_worker.start()

    def _on_sync_done(self) -> None:
        # 「取得中」を最低 _SYNC_MIN_DISPLAY_MS は見せる（瞬時でも演出）
        elapsed = self._sync_clock.elapsed()
        remain = max(0, _SYNC_MIN_DISPLAY_MS - elapsed)
        QTimer.singleShot(remain, self._finish_step1)

    def _on_sync_failed(self, msg: str) -> None:
        QMessageBox.warning(self, tr("invoke_setup.sync_failed_title"), msg)
        self._conn_label.setText(tr("invoke_setup.step1_sync_failed"))
        self._retry_btn.setVisible(True)
        self._step1_started = False  # 再試行できるように

    def _finish_step1(self) -> None:
        self._step1_done = True
        self._conn_label.setText(tr("invoke_setup.step1_done"))
        self._set_step2_enabled(True)
        self._populate_table()
        self.setup_changed.emit()

    # ── ステップ2: ベース別テンプレート取得 ──────────────
    def _setup_bases(self) -> list[str]:
        """表に出すベース: 導入済みmainモデルのベース ∪ テンプレを持つベース。"""
        bases: set[str] = set()
        for r in _env_db.fetchall(
            "SELECT DISTINCT COALESCE(base,'') AS base FROM models "
            "WHERE type='main' AND COALESCE(base,'') NOT IN ('', 'sdxl-refiner') AND available=1"
        ):
            if r["base"]:
                bases.add(r["base"])
        for r in _env_db.fetchall(
            "SELECT DISTINCT base FROM templates WHERE base IS NOT NULL AND base != ''"
        ):
            bases.add(r["base"])
        return sorted(bases)

    def _populate_table(self) -> None:
        self._updating_table = True
        self._table.setRowCount(0)
        bases = self._setup_bases()
        for base in bases:
            templates = _env_db.fetchall(
                "SELECT id, name FROM templates "
                "WHERE base=? ORDER BY is_base_default DESC, id ASC",
                (base,),
            )
            if templates:
                for t in templates:
                    self._add_row(base, dict(t))
            else:
                self._add_row(base, None)  # 未取得プレースホルダ
        self._updating_table = False

    def _add_row(self, base: str, t: dict | None) -> None:
        r = self._table.rowCount()
        self._table.insertRow(r)

        # 削除ボタン（テンプレがある行のみ・フォーカス移動なし）
        if t is not None:
            del_btn = QToolButton()
            del_btn.setText("🗑")
            del_btn.setToolTip(tr("invoke_setup.delete_tooltip"))
            del_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            del_btn.setStyleSheet(
                f"QToolButton {{ background: transparent; color: {SUBTEXT}; border: none; }}"
                f"QToolButton:hover {{ color: {RED}; }}"
            )
            tid = int(t["id"]); tname = str(t["name"])
            del_btn.clicked.connect(lambda _=False, i=tid, n=tname: self._delete_template(i, n))
            self._table.setCellWidget(r, self._COL_DELETE, del_btn)

        # ベース
        base_item = QTableWidgetItem(_base_label(base))
        base_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self._table.setItem(r, self._COL_BASE, base_item)

        # テンプレ名（取得済みのみ編集可。未取得は「未取得」表示）
        if t is not None:
            name_item = QTableWidgetItem(str(t["name"]))
            name_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsEditable)
            name_item.setData(Qt.ItemDataRole.UserRole, int(t["id"]))
            name_item.setForeground(QColor(TEXT))
        else:
            name_item = QTableWidgetItem(tr("invoke_setup.not_fetched"))
            name_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            name_item.setForeground(QColor(SUBTEXT))
        self._table.setItem(r, self._COL_NAME, name_item)

        # 取得ボタン（このベースとして取り込む）
        fetch_btn = QPushButton(tr("invoke_setup.fetch_for_base", base=_base_label(base)))
        fetch_btn.setStyleSheet(themed_button_style("success"))
        fetch_btn.clicked.connect(lambda _=False, b=base: self._fetch_for_base(b))
        self._table.setCellWidget(r, self._COL_FETCH, fetch_btn)

    def _on_name_edited(self, item: QTableWidgetItem) -> None:
        if self._updating_table or item.column() != self._COL_NAME:
            return
        tid = item.data(Qt.ItemDataRole.UserRole)
        if tid is None:
            return
        new_name = item.text().strip()
        if not InvokeClient.rename_template(int(tid), new_name):
            QMessageBox.information(
                self, tr("invoke_setup.rename_fail_title"),
                tr("invoke_setup.rename_fail_msg"),
            )
            self._populate_table()
            return
        self.setup_changed.emit()

    def _fetch_for_base(self, base: str) -> None:
        if self._fetch_worker and self._fetch_worker.isRunning():
            return
        self._status_label.setText(tr("invoke_setup.fetching", base=_base_label(base)))
        self._fetch_worker = _FetchTemplateWorker(self._client, base, self)
        self._fetch_worker.ok.connect(self._on_fetch_ok)
        self._fetch_worker.mismatch.connect(self._on_fetch_mismatch)
        self._fetch_worker.ng.connect(self._on_fetch_ng)
        self._fetch_worker.start()

    def _on_fetch_ok(self, result: dict) -> None:
        # 取得したグラフを命名ダイアログ（候補名プリセット・編集可）で保存する。
        graph = result.get("graph")
        base = result.get("base", "")
        if not graph or not base:
            self._status_label.setText("")
            return
        suggested = InvokeClient.suggested_template_name(base)
        name, ok = QInputDialog.getText(
            self,
            tr("invoke_setup.name_title"),
            tr("invoke_setup.name_label", base=_base_label(base)),
            text=suggested,
        )
        if not ok:
            self._status_label.setText("")
            return  # キャンセル＝保存しない
        saved = InvokeClient.save_fetched_template(graph, base, name)
        self._status_label.setText(
            tr("invoke_setup.fetch_ok", name=saved.get("name", ""),
               base=_base_label(base))
        )
        self._populate_table()
        self.setup_changed.emit()

    def _on_fetch_mismatch(self, base: str) -> None:
        label = _base_label(base)
        QMessageBox.information(
            self, tr("invoke_setup.no_template_title", base=label),
            tr("invoke_setup.no_template_msg", base=label),
        )
        self._status_label.setText("")

    def _on_fetch_ng(self, msg: str) -> None:
        QMessageBox.warning(self, tr("invoke_setup.fetch_failed_title"), msg)
        self._status_label.setText("")

    def _delete_template(self, template_id: int, name: str) -> None:
        ret = QMessageBox.question(
            self, tr("invoke_setup.delete_confirm_title"),
            tr("invoke_setup.delete_confirm_msg", name=name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        InvokeClient.delete_template(template_id)
        self._populate_table()
        self.setup_changed.emit()

    # ── 閉じる（未取得があれば確認）──────────────────────
    def _has_unfetched_base(self) -> bool:
        for base in self._setup_bases():
            cnt = _env_db.fetchone(
                "SELECT COUNT(*) AS c FROM templates WHERE base=?", (base,)
            )
            if not cnt or int(cnt["c"] or 0) == 0:
                return True
        return False

    def reject(self) -> None:
        if self._step1_done and self._has_unfetched_base():
            ret = QMessageBox.question(
                self, tr("invoke_setup.close_unfetched_title"),
                tr("invoke_setup.close_unfetched_msg"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if ret != QMessageBox.StandardButton.Yes:
                return
        QMessageBox.information(
            self, tr("invoke_setup.close_notice_title"),
            tr("invoke_setup.close_notice_msg"),
        )
        super().reject()
