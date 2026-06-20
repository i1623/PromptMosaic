from __future__ import annotations

from PySide6.QtCore import QThread, QTimer, Signal, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QComboBox,
    QPushButton,
    QVBoxLayout,
)

import db.app_db as _app_db
import db.env_db as _env_db
from api.invoke_client import InvokeClient
from core.i18n import available_languages, current_language, set_language, tr
from ui.model_browser import _SyncWorker, _base_label
from ui.styles import ACCENT, GREEN, RED, SUBTEXT, SURFACE0, SURFACE1, SURFACE2, TEXT, ui_font, themed_button_style


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
    ng = Signal(str)

    def __init__(self, client: InvokeClient, parent=None):
        super().__init__(parent)
        self._client = client

    def run(self) -> None:
        try:
            self.ok.emit(self._client.fetch_current_template())
        except Exception as exc:
            self.ng.emit(str(exc))


class InvokeSetupDialog(QDialog):
    """Initial and repeatable InvokeAI connection setup dialog."""

    setup_changed = Signal()
    language_changed = Signal()

    def __init__(self, client: InvokeClient, parent=None):
        super().__init__(parent)
        self._client = client
        self._conn_worker: _SetupConnWorker | None = None
        self._sync_worker: _SyncWorker | None = None
        self._fetch_worker: _FetchTemplateWorker | None = None
        self._connected = False
        self._last_conn_state = "checking"

        self.setWindowTitle(tr("invoke_setup.title"))
        self.setMinimumWidth(680)
        self.resize(780, 620)
        self.setStyleSheet(f"QDialog {{ background-color: {SURFACE0}; color: {TEXT}; }}")

        self._retry_timer = QTimer(self)
        self._retry_timer.setInterval(3000)
        self._retry_timer.timeout.connect(self._check_connection)

        self._build_ui()
        self._refresh_model_status()
        self._check_connection()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

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

        self._intro = QLabel(tr("invoke_setup.description"))
        self._intro.setWordWrap(True)
        self._intro.setStyleSheet(f"color: {TEXT};")
        root.addWidget(self._intro)

        self._conn_label = QLabel(tr("invoke_setup.conn_checking"))
        self._conn_label.setWordWrap(True)
        self._conn_label.setStyleSheet(f"color: {SUBTEXT}; background: {SURFACE1}; padding: 8px; border-radius: 4px;")
        root.addWidget(self._conn_label)

        self._version_check = QCheckBox(tr("invoke_setup.version_confirm"))
        self._version_check.setStyleSheet(f"color: {TEXT};")
        self._version_check.setEnabled(False)
        self._version_check.toggled.connect(self._update_buttons)
        root.addWidget(self._version_check)

        action_row = QHBoxLayout()
        self._retry_btn = QPushButton(tr("invoke_setup.retry"))
        self._retry_btn.clicked.connect(self._check_connection)
        action_row.addWidget(self._retry_btn)

        self._sync_btn = QPushButton(tr("invoke_setup.sync_models"))
        self._sync_btn.setStyleSheet(themed_button_style("accent"))
        self._sync_btn.clicked.connect(self._sync_models)
        action_row.addWidget(self._sync_btn)

        self._fetch_btn = QPushButton(tr("invoke_setup.fetch_template"))
        self._fetch_btn.setStyleSheet(themed_button_style("success"))
        self._fetch_btn.clicked.connect(self._fetch_template)
        action_row.addWidget(self._fetch_btn)
        action_row.addStretch(1)
        root.addLayout(action_row)

        self._summary_label = QLabel("")
        self._summary_label.setStyleSheet(f"color: {SUBTEXT};")
        root.addWidget(self._summary_label)

        self._model_list = QListWidget()
        self._model_list.setStyleSheet(
            f"QListWidget {{ background: {SURFACE1}; color: {TEXT}; border: 1px solid {SURFACE2}; border-radius: 4px; }}"
            f"QListWidget::item {{ padding: 4px; }}"
        )
        root.addWidget(self._model_list, stretch=1)

        self._note = QLabel(tr("invoke_setup.template_note"))
        self._note.setWordWrap(True)
        self._note.setStyleSheet(f"color: {SUBTEXT};")
        root.addWidget(self._note)

        self._buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_btn = self._buttons.button(QDialogButtonBox.StandardButton.Close)
        if close_btn is not None:
            close_btn.setText(tr("invoke_setup.close"))
        self._buttons.rejected.connect(self.reject)
        root.addWidget(self._buttons)

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
        self._intro.setText(tr("invoke_setup.description"))
        self._version_check.setText(tr("invoke_setup.version_confirm"))
        self._retry_btn.setText(tr("invoke_setup.retry"))
        self._sync_btn.setText(tr("invoke_setup.sync_models"))
        self._fetch_btn.setText(tr("invoke_setup.fetch_template"))
        self._note.setText(tr("invoke_setup.template_note"))
        close_btn = self._buttons.button(QDialogButtonBox.StandardButton.Close)
        if close_btn is not None:
            close_btn.setText(tr("invoke_setup.close"))
        self._populate_language_combo(selected)
        if self._last_conn_state == "ok":
            self._conn_label.setText(tr("invoke_setup.conn_ok"))
        elif self._last_conn_state == "ng":
            self._conn_label.setText(tr("invoke_setup.conn_waiting"))
        else:
            self._conn_label.setText(tr("invoke_setup.conn_checking"))
        self._refresh_model_status()

    def _set_busy(self, busy: bool) -> None:
        self._retry_btn.setEnabled(not busy)
        self._sync_btn.setEnabled(not busy and self._connected and self._version_check.isChecked())
        self._fetch_btn.setEnabled(not busy and self._connected and self._version_check.isChecked())

    def _update_buttons(self) -> None:
        self._set_busy(False)

    def _check_connection(self) -> None:
        if self._conn_worker and self._conn_worker.isRunning():
            return
        self._last_conn_state = "checking"
        self._conn_label.setText(tr("invoke_setup.conn_checking"))
        self._conn_label.setStyleSheet(f"color: {SUBTEXT}; background: {SURFACE1}; padding: 8px; border-radius: 4px;")
        self._set_busy(True)
        self._conn_worker = _SetupConnWorker(self._client, self)
        self._conn_worker.ok.connect(self._on_conn_ok)
        self._conn_worker.ng.connect(self._on_conn_ng)
        self._conn_worker.start()

    def _on_conn_ok(self) -> None:
        self._connected = True
        self._last_conn_state = "ok"
        self._retry_timer.stop()
        self._conn_label.setText(tr("invoke_setup.conn_ok"))
        self._conn_label.setStyleSheet(f"color: {GREEN}; background: {SURFACE1}; padding: 8px; border-radius: 4px;")
        self._version_check.setEnabled(True)
        self._update_buttons()

    def _on_conn_ng(self, _msg: str) -> None:
        self._connected = False
        self._last_conn_state = "ng"
        self._conn_label.setText(tr("invoke_setup.conn_waiting"))
        self._conn_label.setStyleSheet(f"color: {RED}; background: {SURFACE1}; padding: 8px; border-radius: 4px;")
        self._version_check.setEnabled(False)
        self._set_busy(False)
        if not self._retry_timer.isActive():
            self._retry_timer.start()

    def _sync_models(self) -> None:
        if self._sync_worker and self._sync_worker.isRunning():
            return
        self._set_busy(True)
        self._summary_label.setText(tr("invoke_setup.syncing"))
        self._sync_worker = _SyncWorker(self._client, self)
        self._sync_worker.finished.connect(self._on_sync_done)
        self._sync_worker.error.connect(self._on_sync_failed)
        self._sync_worker.start()

    def _on_sync_done(self) -> None:
        self._summary_label.setText(tr("invoke_setup.sync_done"))
        self._refresh_model_status()
        self.setup_changed.emit()
        self._update_buttons()

    def _on_sync_failed(self, msg: str) -> None:
        QMessageBox.warning(self, tr("invoke_setup.sync_failed_title"), msg)
        self._summary_label.setText(tr("invoke_setup.sync_failed"))
        self._update_buttons()

    def _fetch_template(self) -> None:
        if self._fetch_worker and self._fetch_worker.isRunning():
            return
        self._set_busy(True)
        self._summary_label.setText(tr("invoke_setup.fetching_template"))
        self._fetch_worker = _FetchTemplateWorker(self._client, self)
        self._fetch_worker.ok.connect(self._on_fetch_done)
        self._fetch_worker.ng.connect(self._on_fetch_failed)
        self._fetch_worker.start()

    def _on_fetch_done(self, result: dict) -> None:
        self._summary_label.setText(
            tr(
                "invoke_setup.fetch_done",
                name=result.get("name", ""),
                base=result.get("base", ""),
            )
        )
        self._refresh_model_status()
        self.setup_changed.emit()
        self._update_buttons()

    def _on_fetch_failed(self, msg: str) -> None:
        QMessageBox.information(self, tr("invoke_setup.fetch_failed_title"), msg)
        self._summary_label.setText(tr("invoke_setup.fetch_failed"))
        self._update_buttons()

    def _refresh_model_status(self) -> None:
        self._model_list.clear()
        bases = _env_db.fetchall(
            "SELECT COALESCE(base,'') AS base, COUNT(*) AS model_count FROM models "
            "WHERE type='main' AND COALESCE(base,'') != 'sdxl-refiner' AND available=1 "
            "GROUP BY COALESCE(base,'') "
            "ORDER BY COALESCE(base,'zzz')"
        )
        template_counts = {
            r["base"]: int(r["template_count"] or 0)
            for r in _env_db.fetchall(
                "SELECT base, COUNT(*) AS template_count FROM templates GROUP BY base"
            )
            if r["base"]
        }
        if not bases:
            self._summary_label.setText(tr("invoke_setup.no_models"))
            return
        ready = 0
        for row in bases:
            base = row["base"] or ""
            model_count = int(row["model_count"] or 0)
            template_count = template_counts.get(base, 0)
            ok = template_count > 0
            if ok:
                ready += 1
            mark = tr("invoke_setup.base_ready") if ok else tr("invoke_setup.base_missing")
            item = QListWidgetItem(
                tr(
                    "invoke_setup.base_row",
                    status=mark,
                    base=_base_label(base),
                    models=model_count,
                    templates=template_count,
                )
            )
            item.setForeground(QColor(GREEN if ok else RED))
            self._model_list.addItem(item)
        self._summary_label.setText(
            tr("invoke_setup.summary", ready=ready, total=len(bases))
        )

    def reject(self) -> None:
        QMessageBox.information(
            self,
            tr("invoke_setup.close_notice_title"),
            tr("invoke_setup.close_notice_msg"),
        )
        super().reject()
