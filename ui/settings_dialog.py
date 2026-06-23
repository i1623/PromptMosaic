"""
設定ダイアログ

タブ構成:
  - 表示 (Appearance): 言語 / テーマ / フォントサイズ / タイル表示 / NSFW / アイコン
  - 接続 (Connection): InvokeAI URL / キューID
  - 生成管理 (Generation): InvokeAIテンプレート管理 / マルチモデルプラン
  - データ (Data): バックアップ案内 / キャッシュ管理

保存時: app_settings テーブルへ INSERT OR REPLACE で書き込む。
URL / キューID は client にも即時反映する。
表示設定は呼び出し元のメインウィンドウで保存後すぐに反映する。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget,
    QLabel, QComboBox, QLineEdit, QPushButton, QFormLayout,
    QSizePolicy, QCheckBox, QFileDialog, QTableWidget, QTableWidgetItem,
    QHeaderView, QMessageBox, QInputDialog, QAbstractItemView,
    QGroupBox, QScrollArea, QApplication,
    QColorDialog, QGridLayout,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QBrush


import db.app_db as _app_db
import db.env_db as _env_db
from core.i18n import tr, available_languages
from ui.styles import (
    SURFACE0, SURFACE1, SURFACE2, TEXT, SUBTEXT, ACCENT, GREEN, RED, ui_font,
    history_default_text_color, history_default_line_color,
)
from ui.generation_plan_dialog import GenerationPlanEditor

if TYPE_CHECKING:
    from api.invoke_client import InvokeClient


UNREGISTERED_TILE_DEFAULT_BG = "#001d9c"
UNREGISTERED_TILE_DEFAULT_BORDER = "#91821f"
UNREGISTERED_TILE_DEFAULT_FG = "#d2bf2e"


def _read_setting(key: str, default: str) -> str:
    row = _app_db.fetchone(
        "SELECT value FROM app_settings WHERE key = ?", (key,)
    )
    return row["value"] if row else default


def _write_setting(key: str, value: str) -> None:
    _app_db.execute(
        "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
        (key, value),
    )


def _settings_button_style(kind: str = "normal", *, bold: bool = False) -> str:
    if kind == "accent":
        fg, bg, hover, border = ACCENT, "#1a2a3a", "#2a4a6a", ACCENT
    elif kind == "success":
        fg, bg, hover, border = GREEN, "#1a3a1a", "#2a5a2a", GREEN
    elif kind == "danger":
        fg, bg, hover, border = RED, "#3a1a1a", "#5a2a2a", RED
    else:
        fg, bg, hover, border = TEXT, SURFACE1, SURFACE2, SURFACE2
    weight = "font-weight: bold;" if bold else ""
    return (
        f"QPushButton {{ background-color: {bg}; color: {fg}; "
        f"border: 1px solid {border}; border-radius: 4px; "
        f"padding: 4px 12px; {weight} }}"
        f"QPushButton:hover {{ background-color: {hover}; }}"
        f"QPushButton:pressed {{ background-color: {ACCENT}; color: {SURFACE0}; }}"
    )


class _UnregisteredTilePreview(QLabel):
    def __init__(self, parent=None):
        super().__init__(tr("settings.unreg_tile_preview"), parent)
        self._bg = QColor(UNREGISTERED_TILE_DEFAULT_BG)
        self._fg = QColor(UNREGISTERED_TILE_DEFAULT_FG)
        self._border = QColor(UNREGISTERED_TILE_DEFAULT_BORDER)
        self.setFixedHeight(24)
        self.setMinimumWidth(110)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def set_colors(self, bg: str, fg: str, border: str) -> None:
        bg_color = QColor(bg)
        fg_color = QColor(fg)
        border_color = QColor(border)
        self._bg = bg_color if bg_color.isValid() else QColor(UNREGISTERED_TILE_DEFAULT_BG)
        self._fg = fg_color if fg_color.isValid() else QColor(UNREGISTERED_TILE_DEFAULT_FG)
        self._border = border_color if border_color.isValid() else QColor(UNREGISTERED_TILE_DEFAULT_BORDER)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)
        pen = QPen(self._border, 2)
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(QBrush(self._bg))
        painter.drawRoundedRect(rect, 4, 4)
        painter.setPen(self._fg)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.text())


class SettingsDialog(QDialog):
    invoke_setup_requested = Signal()

    def __init__(
        self,
        client: "InvokeClient | None" = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._client = client
        self._loading = True  # loading 中は auto_save を無効にする

        self.setWindowTitle(tr("settings.title"))
        self.setModal(True)
        self.resize(480, 440)
        self.setStyleSheet(f"QDialog {{ background-color: {SURFACE0}; }}")

        self._build_ui()
        self._connect_auto_save_signals()
        self._load_values()

    # ── UI 構築 ─────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_appearance_tab(), tr("settings.tab_appearance"))
        self._tabs.addTab(self._build_connection_tab(), tr("settings.tab_connection"))
        self._tabs.addTab(self._build_templates_tab(),  tr("settings.tab_generation"))
        self._tabs.addTab(self._build_data_tab(),       tr("settings.tab_data"))
        root.addWidget(self._tabs, stretch=1)

        note = QLabel(tr("settings.restart_note"))
        note.setFont(ui_font(-2))
        note.setStyleSheet(f"color: {SUBTEXT}; background: transparent;")
        note.setWordWrap(True)
        root.addWidget(note)

        root.addLayout(self._build_btn_row())

    def _build_appearance_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setContentsMargins(12, 12, 12, 12)
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        # 言語 — "Language:" はどの言語でも共通なのでハードコード
        lang_lbl = QLabel("Language:")
        lang_lbl.setStyleSheet(f"color: {TEXT}; background: transparent;")
        self._lang_combo = QComboBox()
        for code, name in available_languages():
            self._lang_combo.addItem(name, code)
        form.addRow(lang_lbl, self._lang_combo)

        # テーマ
        theme_lbl = QLabel(tr("settings.theme_label"))
        theme_lbl.setStyleSheet(f"color: {TEXT}; background: transparent;")
        self._theme_combo = QComboBox()
        self._theme_combo.addItem(tr("settings.theme_dark"),  "dark")
        self._theme_combo.addItem(tr("settings.theme_light"), "light")
        form.addRow(theme_lbl, self._theme_combo)

        # フォントサイズ
        font_lbl = QLabel(tr("settings.font_size_label"))
        font_lbl.setStyleSheet(f"color: {TEXT}; background: transparent;")
        self._font_combo = QComboBox()
        self._font_combo.addItem(tr("settings.font_small"),  "9")
        self._font_combo.addItem(tr("settings.font_normal"), "10")
        self._font_combo.addItem(tr("settings.font_large"),  "12")
        self._font_combo.addItem(tr("settings.font_xlarge"), "14")
        form.addRow(font_lbl, self._font_combo)

        # タイル表示
        tile_display_lbl = QLabel(tr("settings.tile_display_label"))
        tile_display_lbl.setStyleSheet(f"color: {TEXT}; background: transparent;")
        self._tile_display_combo = QComboBox()
        self._tile_display_combo.addItem(tr("settings.tile_display_two"), "0")
        self._tile_display_combo.addItem(tr("settings.tile_display_one"), "1")
        self._tile_display_combo.addItem(tr("settings.tile_display_en"), "2")
        form.addRow(tile_display_lbl, self._tile_display_combo)

        # タグ入力サジェスト
        suggest_lbl = QLabel(tr("settings.tag_suggestions_label"))
        suggest_lbl.setStyleSheet(f"color: {TEXT}; background: transparent;")
        self._tag_suggestions_cb = QCheckBox(tr("settings.tag_suggestions_checkbox"))
        self._tag_suggestions_cb.setStyleSheet(f"color: {TEXT}; background: transparent;")
        form.addRow(suggest_lbl, self._tag_suggestions_cb)

        # NSFW
        nsfw_lbl = QLabel(tr("settings.nsfw_label"))
        nsfw_lbl.setStyleSheet(f"color: {TEXT}; background: transparent;")
        self._nsfw_cb = QCheckBox(tr("settings.nsfw_checkbox"))
        self._nsfw_cb.setStyleSheet(f"color: {TEXT}; background: transparent;")
        form.addRow(nsfw_lbl, self._nsfw_cb)

        # 無所属タイル色設定
        unreg_grid = QGridLayout()
        unreg_grid.setContentsMargins(0, 0, 0, 0)
        unreg_grid.setHorizontalSpacing(8)
        unreg_grid.setVerticalSpacing(4)
        unreg_grid.setColumnStretch(2, 1)

        def _unreg_label(text: str) -> QLabel:
            label = QLabel(text)
            label.setStyleSheet(f"color: {TEXT}; background: transparent;")
            label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            return label

        self._unreg_bg_btn = QPushButton()
        self._unreg_bg_btn.setFixedSize(110, 24)
        self._unreg_bg_btn.setToolTip(tr("settings.color_pick_tooltip"))
        self._unreg_bg_btn.clicked.connect(
            lambda: self._pick_color(self._unreg_bg_btn, tr("settings.unreg_tile_bg_label"))
        )
        unreg_grid.addWidget(_unreg_label(tr("settings.unreg_tile_bg_label")), 0, 0)
        unreg_grid.addWidget(self._unreg_bg_btn, 0, 1)

        self._unreg_border_btn = QPushButton()
        self._unreg_border_btn.setFixedSize(110, 24)
        self._unreg_border_btn.setToolTip(tr("settings.color_pick_tooltip"))
        self._unreg_border_btn.clicked.connect(
            lambda: self._pick_color(self._unreg_border_btn, tr("settings.unreg_tile_border_label"))
        )
        unreg_grid.addWidget(_unreg_label(tr("settings.unreg_tile_border_label")), 1, 0)
        unreg_grid.addWidget(self._unreg_border_btn, 1, 1)

        self._unreg_fg_btn = QPushButton()
        self._unreg_fg_btn.setFixedSize(110, 24)
        self._unreg_fg_btn.setToolTip(tr("settings.color_pick_tooltip"))
        self._unreg_fg_btn.clicked.connect(
            lambda: self._pick_color(self._unreg_fg_btn, tr("settings.unreg_tile_fg_label"))
        )
        unreg_grid.addWidget(_unreg_label(tr("settings.unreg_tile_fg_label")), 2, 0)
        unreg_grid.addWidget(self._unreg_fg_btn, 2, 1)

        self._unreg_bg_preview = self._make_unreg_preview_label()
        unreg_grid.addWidget(self._unreg_bg_preview, 3, 1)
        reset_unreg_btn = QPushButton(tr("settings.unreg_tile_reset_default"))
        reset_unreg_btn.setFixedHeight(24)
        reset_unreg_btn.setStyleSheet(_settings_button_style())
        reset_unreg_btn.clicked.connect(self._reset_unreg_tile_colors)
        unreg_grid.addWidget(reset_unreg_btn, 3, 2)
        unreg_w = QWidget()
        unreg_w.setLayout(unreg_grid)
        form.addRow(unreg_w)

        # 履歴の色（テーマ別の既定。履歴ツリーごとの右クリック上書きが最優先）
        hist_lbl = QLabel(tr("settings.history_colors_label"))
        hist_lbl.setStyleSheet(f"color: {TEXT}; background: transparent; font-weight: bold;")
        form.addRow(hist_lbl)

        hist_grid = QGridLayout()
        hist_grid.setContentsMargins(0, 0, 0, 0)
        hist_grid.setHorizontalSpacing(8)
        hist_grid.setVerticalSpacing(4)
        hist_grid.setColumnStretch(3, 1)

        def _hist_label(text: str) -> QLabel:
            label = QLabel(text)
            label.setStyleSheet(f"color: {TEXT}; background: transparent;")
            label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            return label

        def _hist_color_btn(title: str) -> QPushButton:
            btn = QPushButton()
            btn.setFixedSize(110, 24)
            btn.setToolTip(tr("settings.color_pick_tooltip"))
            btn.clicked.connect(lambda: self._pick_color(btn, title))
            return btn

        # 列見出し（ダーク / ライト）
        dark_hdr = QLabel(tr("settings.theme_dark"))
        dark_hdr.setStyleSheet(f"color: {SUBTEXT}; background: transparent;")
        dark_hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        light_hdr = QLabel(tr("settings.theme_light"))
        light_hdr.setStyleSheet(f"color: {SUBTEXT}; background: transparent;")
        light_hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hist_grid.addWidget(dark_hdr, 0, 1)
        hist_grid.addWidget(light_hdr, 0, 2)

        self._hist_text_dark_btn = _hist_color_btn(tr("settings.history_text_color_label"))
        self._hist_text_light_btn = _hist_color_btn(tr("settings.history_text_color_label"))
        hist_grid.addWidget(_hist_label(tr("settings.history_text_color_label")), 1, 0)
        hist_grid.addWidget(self._hist_text_dark_btn, 1, 1)
        hist_grid.addWidget(self._hist_text_light_btn, 1, 2)

        self._hist_line_dark_btn = _hist_color_btn(tr("settings.history_line_color_label"))
        self._hist_line_light_btn = _hist_color_btn(tr("settings.history_line_color_label"))
        hist_grid.addWidget(_hist_label(tr("settings.history_line_color_label")), 2, 0)
        hist_grid.addWidget(self._hist_line_dark_btn, 2, 1)
        hist_grid.addWidget(self._hist_line_light_btn, 2, 2)

        reset_hist_btn = QPushButton(tr("settings.history_colors_reset_default"))
        reset_hist_btn.setFixedHeight(24)
        reset_hist_btn.setStyleSheet(_settings_button_style())
        reset_hist_btn.clicked.connect(self._reset_history_colors)
        hist_grid.addWidget(reset_hist_btn, 3, 3)
        hist_w = QWidget()
        hist_w.setLayout(hist_grid)
        form.addRow(hist_w)

        # アプリアイコン
        icon_lbl = QLabel(tr("settings.icon_label"))
        icon_lbl.setStyleSheet(f"color: {TEXT}; background: transparent;")
        icon_row = QHBoxLayout()
        self._icon_edit = QLineEdit()
        self._icon_edit.setPlaceholderText(tr("settings.icon_placeholder"))
        self._icon_edit.setReadOnly(True)
        self._icon_edit.setStyleSheet(f"color: {TEXT}; background: {SURFACE1};")
        icon_row.addWidget(self._icon_edit)
        icon_btn = QPushButton(tr("settings.icon_btn"))
        icon_btn.setFixedWidth(70)
        icon_btn.clicked.connect(self._pick_icon)
        icon_row.addWidget(icon_btn)
        icon_w = QWidget()
        icon_w.setLayout(icon_row)
        form.addRow(icon_lbl, icon_w)

        history_pos_lbl = QLabel(tr("settings.history_windows_label"))
        history_pos_lbl.setStyleSheet(f"color: {TEXT}; background: transparent;")
        history_pos_btn = QPushButton(tr("settings.history_windows_reset_btn"))
        history_pos_btn.setFixedHeight(24)
        history_pos_btn.setStyleSheet(_settings_button_style())
        history_pos_btn.clicked.connect(self._reset_history_window_positions)
        form.addRow(history_pos_lbl, history_pos_btn)

        return w

    @staticmethod
    def _apply_color_to_btn(btn: "QPushButton", hex_color: str) -> None:
        color = QColor(hex_color)
        if not color.isValid():
            color = QColor("#313244")
        hex_color = color.name()
        btn.setText(hex_color)
        try:
            r = int(hex_color[1:3], 16)
            g = int(hex_color[3:5], 16)
            b = int(hex_color[5:7], 16)
            lum = 0.299 * r + 0.587 * g + 0.114 * b
        except (ValueError, IndexError):
            lum = 128.0
        btn.setStyleSheet("")
        btn.update()
        parent = btn.window()
        if parent is not None and hasattr(parent, "_update_unreg_preview"):
            parent._update_unreg_preview()

    @staticmethod
    def _make_unreg_preview_label() -> _UnregisteredTilePreview:
        return _UnregisteredTilePreview()

    def _update_unreg_preview(self) -> None:
        if not hasattr(self, "_unreg_bg_preview"):
            return
        bg = self._unreg_bg_btn.text().strip() or UNREGISTERED_TILE_DEFAULT_BG
        fg = self._unreg_fg_btn.text().strip() or UNREGISTERED_TILE_DEFAULT_FG
        border = self._unreg_border_btn.text().strip() or UNREGISTERED_TILE_DEFAULT_BORDER
        if not QColor(bg).isValid():
            bg = UNREGISTERED_TILE_DEFAULT_BG
        if not QColor(fg).isValid():
            fg = UNREGISTERED_TILE_DEFAULT_FG
        if not QColor(border).isValid():
            border = UNREGISTERED_TILE_DEFAULT_BORDER
        self._unreg_bg_preview.set_colors(bg, fg, border)

    def _reset_unreg_tile_colors(self) -> None:
        self._apply_color_to_btn(self._unreg_bg_btn, UNREGISTERED_TILE_DEFAULT_BG)
        self._apply_color_to_btn(self._unreg_border_btn, UNREGISTERED_TILE_DEFAULT_BORDER)
        self._apply_color_to_btn(self._unreg_fg_btn, UNREGISTERED_TILE_DEFAULT_FG)
        self._auto_save()

    def _reset_history_colors(self) -> None:
        self._apply_color_to_btn(self._hist_text_dark_btn, history_default_text_color("dark"))
        self._apply_color_to_btn(self._hist_text_light_btn, history_default_text_color("light"))
        self._apply_color_to_btn(self._hist_line_dark_btn, history_default_line_color("dark"))
        self._apply_color_to_btn(self._hist_line_light_btn, history_default_line_color("light"))
        self._auto_save()

    def _reset_history_window_positions(self) -> None:
        _write_setting("history_map_geometry", "")
        _write_setting("history_image_viewer_geometry", "")
        parent = self.parent()
        if parent is not None and hasattr(parent, "_reset_history_window_positions"):
            parent._reset_history_window_positions()

    def _pick_color(self, btn: "QPushButton", title: str) -> None:
        color = QColorDialog.getColor(QColor(btn.text()), self, title)
        if color.isValid():
            self._apply_color_to_btn(btn, color.name())
            self._auto_save()

    def _build_connection_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setContentsMargins(12, 12, 12, 12)
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        endpoint_lbl = QLabel(tr("settings.endpoint_label"))
        endpoint_lbl.setStyleSheet(f"color: {TEXT}; background: transparent;")
        self._endpoint_edit = QLineEdit()
        self._endpoint_edit.setPlaceholderText("http://127.0.0.1:9090")
        form.addRow(endpoint_lbl, self._endpoint_edit)

        queue_lbl = QLabel(tr("settings.queue_id_label"))
        queue_lbl.setStyleSheet(f"color: {TEXT}; background: transparent;")
        self._queue_id_edit = QLineEdit()
        self._queue_id_edit.setPlaceholderText("default")
        form.addRow(queue_lbl, self._queue_id_edit)

        setup_row = QHBoxLayout()
        setup_btn = QPushButton(tr("settings.invoke_setup_btn"))
        setup_btn.setStyleSheet(_settings_button_style("accent"))
        setup_btn.clicked.connect(self._request_invoke_setup)
        setup_row.addWidget(setup_btn)
        setup_row.addStretch(1)
        setup_w = QWidget()
        setup_w.setLayout(setup_row)
        form.addRow(QLabel(""), setup_w)

        sep = QLabel("")
        sep.setFixedHeight(8)
        form.addRow(sep)

        lm_provider_lbl = QLabel(tr("settings.lm_provider_label"))
        lm_provider_lbl.setStyleSheet(f"color: {TEXT}; background: transparent;")
        self._lm_provider_combo = QComboBox()
        self._lm_provider_combo.addItem("LM Studio", "lmstudio")
        self._lm_provider_combo.addItem("llama.cpp", "llama_cpp")
        self._lm_provider_combo.addItem("Ollama", "ollama")
        form.addRow(lm_provider_lbl, self._lm_provider_combo)

        lm_lbl = QLabel(tr("settings.lm_endpoint_label"))
        lm_lbl.setStyleSheet(f"color: {TEXT}; background: transparent;")
        self._lm_endpoint_edit = QLineEdit()
        self._lm_endpoint_edit.setPlaceholderText("http://localhost:1234")
        self._lm_endpoint_edit.setToolTip(tr("settings.lm_endpoint_tooltip"))
        form.addRow(lm_lbl, self._lm_endpoint_edit)

        sep2 = QLabel("")
        sep2.setFixedHeight(8)
        form.addRow(sep2)

        classify_provider_lbl = QLabel(tr("settings.lm_classify_provider_label"))
        classify_provider_lbl.setStyleSheet(f"color: {TEXT}; background: transparent;")
        self._lm_classify_provider_combo = QComboBox()
        self._lm_classify_provider_combo.addItem(tr("settings.lm_classify_use_translate"), "")
        self._lm_classify_provider_combo.addItem("LM Studio", "lmstudio")
        self._lm_classify_provider_combo.addItem("llama.cpp", "llama_cpp")
        self._lm_classify_provider_combo.addItem("Ollama", "ollama")
        form.addRow(classify_provider_lbl, self._lm_classify_provider_combo)

        classify_endpoint_lbl = QLabel(tr("settings.lm_classify_endpoint_label"))
        classify_endpoint_lbl.setStyleSheet(f"color: {TEXT}; background: transparent;")
        self._lm_classify_endpoint_edit = QLineEdit()
        self._lm_classify_endpoint_edit.setPlaceholderText(tr("settings.lm_classify_endpoint_placeholder"))
        form.addRow(classify_endpoint_lbl, self._lm_classify_endpoint_edit)

        classify_model_lbl = QLabel(tr("settings.lm_classify_model_label"))
        classify_model_lbl.setStyleSheet(f"color: {TEXT}; background: transparent;")
        self._lm_classify_model_edit = QLineEdit()
        self._lm_classify_model_edit.setPlaceholderText(tr("settings.lm_classify_model_placeholder"))
        form.addRow(classify_model_lbl, self._lm_classify_model_edit)

        classify_seed_lbl = QLabel(tr("settings.lm_classify_seed_label"))
        classify_seed_lbl.setStyleSheet(f"color: {TEXT}; background: transparent;")
        self._lm_classify_seed_edit = QLineEdit()
        self._lm_classify_seed_edit.setPlaceholderText("0")
        form.addRow(classify_seed_lbl, self._lm_classify_seed_edit)

        classify_temp_lbl = QLabel(tr("settings.lm_classify_temperature_label"))
        classify_temp_lbl.setStyleSheet(f"color: {TEXT}; background: transparent;")
        self._lm_classify_temperature_edit = QLineEdit()
        self._lm_classify_temperature_edit.setPlaceholderText("0")
        form.addRow(classify_temp_lbl, self._lm_classify_temperature_edit)

        classify_prompt_lbl = QLabel(tr("settings.lm_classify_prompt_label"))
        classify_prompt_lbl.setStyleSheet(f"color: {TEXT}; background: transparent;")
        self._lm_classify_prompt_edit = QLineEdit()
        self._lm_classify_prompt_edit.setPlaceholderText(tr("settings.lm_classify_prompt_placeholder"))
        form.addRow(classify_prompt_lbl, self._lm_classify_prompt_edit)

        return w

    def _request_invoke_setup(self) -> None:
        self.invoke_setup_requested.emit()
        self.reject()

    # ── テンプレート管理タブ ────────────────────────────

    def _build_templates_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        tmpl_group = QGroupBox(tr("settings.generation_templates_section"))
        tmpl_lay = QVBoxLayout(tmpl_group)
        tmpl_lay.setSpacing(8)

        info = QLabel(tr("settings.tmpl_info"))
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {SUBTEXT}; background: transparent;")
        tmpl_lay.addWidget(info)

        # ボタン列
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        def _styled(text: str, kind: str = "normal") -> QPushButton:
            b = QPushButton(text)
            b.setStyleSheet(_settings_button_style(kind))
            return b

        # テンプレートの「取得」は初期セットアップ（InvokeAI データ取得）に集約。
        # ここでは管理（名前変更・複製・既定設定・削除）のみ。
        self._btn_tmpl_rename = _styled(tr("settings.tmpl_btn_rename"))
        self._btn_tmpl_rename.clicked.connect(self._on_template_rename)
        btn_row.addWidget(self._btn_tmpl_rename)

        self._btn_tmpl_duplicate = _styled(tr("settings.tmpl_btn_duplicate"))
        self._btn_tmpl_duplicate.clicked.connect(self._on_template_duplicate)
        btn_row.addWidget(self._btn_tmpl_duplicate)

        self._btn_tmpl_set_default = _styled(tr("settings.tmpl_btn_set_default"))
        self._btn_tmpl_set_default.clicked.connect(self._on_template_set_default)
        btn_row.addWidget(self._btn_tmpl_set_default)

        self._btn_tmpl_delete = _styled(tr("settings.tmpl_btn_delete"), "danger")
        self._btn_tmpl_delete.clicked.connect(self._on_template_delete)
        btn_row.addWidget(self._btn_tmpl_delete)

        btn_row.addStretch()
        tmpl_lay.addLayout(btn_row)

        # テーブル
        self._tmpl_table = QTableWidget(0, 4)
        self._tmpl_table.setHorizontalHeaderLabels([
            tr("settings.tmpl_col_name"),
            tr("settings.tmpl_col_base"),
            tr("settings.tmpl_col_file"),
            tr("settings.tmpl_col_updated"),
        ])
        self._tmpl_table.verticalHeader().setVisible(False)
        self._tmpl_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._tmpl_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._tmpl_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        hdr = self._tmpl_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        tmpl_lay.addWidget(self._tmpl_table, stretch=1)

        lay.addWidget(tmpl_group, stretch=1)

        plan_group = QGroupBox(tr("settings.generation_plans_section"))
        plan_lay = QVBoxLayout(plan_group)
        self._plan_editor = GenerationPlanEditor(plan_group)
        plan_lay.addWidget(self._plan_editor)
        lay.addWidget(plan_group, stretch=1)

        self._refresh_templates_table()
        return w

    def _refresh_templates_table(self) -> None:
        rows = _env_db.fetchall(
            "SELECT id, name, base, cache_key, is_base_default, updated_at "
            "FROM templates ORDER BY base ASC, is_base_default DESC, name ASC"
        )
        self._tmpl_table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            name = r["name"]
            if r["is_base_default"]:
                name = f"★ {name}"
            items = [
                QTableWidgetItem(name),
                QTableWidgetItem(r["base"]),
                QTableWidgetItem(r["cache_key"]),
                QTableWidgetItem(str(r["updated_at"] or "")[:19]),
            ]
            items[0].setData(Qt.ItemDataRole.UserRole, r["id"])
            for col, it in enumerate(items):
                self._tmpl_table.setItem(i, col, it)

    def _selected_template_id(self) -> int | None:
        row = self._tmpl_table.currentRow()
        if row < 0:
            QMessageBox.information(self, tr("settings.tmpl_select_title"), tr("settings.tmpl_select_msg"))
            return None
        item = self._tmpl_table.item(row, 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _on_template_rename(self) -> None:
        tid = self._selected_template_id()
        if tid is None:
            return
        row = _env_db.fetchone("SELECT name, base FROM templates WHERE id=?", (tid,))
        if not row:
            return
        new_name, ok = QInputDialog.getText(
            self, tr("settings.tmpl_rename_title"), tr("settings.tmpl_rename_prompt", base=row["base"]), text=row["name"]
        )
        if not ok:
            return
        new_name = new_name.strip()
        if not new_name:
            QMessageBox.warning(self, tr("settings.tmpl_rename_title"), tr("settings.tmpl_rename_empty_msg"))
            return
        # UNIQUE(base, name) 衝突チェック
        dup = _env_db.fetchone(
            "SELECT 1 FROM templates WHERE base=? AND name=? AND id!=?",
            (row["base"], new_name, tid),
        )
        if dup:
            QMessageBox.warning(self, tr("settings.tmpl_rename_title"), tr("settings.tmpl_rename_dup_msg"))
            return
        _env_db.execute(
            "UPDATE templates SET name=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (new_name, tid),
        )
        self._refresh_templates_table()

    def _on_template_duplicate(self) -> None:
        tid = self._selected_template_id()
        if tid is None:
            return
        row = _env_db.fetchone(
            "SELECT name, base, cache_key FROM templates WHERE id=?", (tid,)
        )
        if not row:
            return
        new_name, ok = QInputDialog.getText(
            self, tr("settings.tmpl_duplicate_title"),
            tr("settings.tmpl_duplicate_prompt", base=row["base"]),
            text=f"{row['name']}{tr('settings.tmpl_copy_suffix')}",
        )
        if not ok:
            return
        new_name = new_name.strip()
        if not new_name:
            return
        # 新 cache_key: 既存を避けて連番
        from pathlib import Path
        from api.invoke_client import InvokeClient
        src_path = InvokeClient._template_cache_path(row["cache_key"])
        if not src_path.exists():
            QMessageBox.warning(self, tr("settings.tmpl_duplicate_fail_title"), tr("settings.tmpl_duplicate_fail_msg"))
            return
        suffix = 2
        while True:
            new_key = f"{row['cache_key']}_copy{suffix}"
            if not _env_db.fetchone(
                "SELECT 1 FROM templates WHERE cache_key=?", (new_key,)
            ) and not InvokeClient._template_cache_path(new_key).exists():
                break
            suffix += 1
        dst_path = InvokeClient._template_cache_path(new_key)
        dst_path.write_bytes(src_path.read_bytes())
        # UNIQUE(base, name) 衝突対策
        base_name = new_name
        n = 2
        while _env_db.fetchone(
            "SELECT 1 FROM templates WHERE base=? AND name=?",
            (row["base"], new_name),
        ):
            new_name = f"{base_name} ({n})"
            n += 1
        _env_db.execute(
            "INSERT INTO templates (name, base, cache_key, is_base_default) "
            "VALUES (?, ?, ?, 0)",
            (new_name, row["base"], new_key),
        )
        self._refresh_templates_table()

    def _on_template_set_default(self) -> None:
        tid = self._selected_template_id()
        if tid is None:
            return
        row = _env_db.fetchone("SELECT base FROM templates WHERE id=?", (tid,))
        if not row:
            return
        _env_db.execute(
            "UPDATE templates SET is_base_default=0 WHERE base=?", (row["base"],)
        )
        _env_db.execute(
            "UPDATE templates SET is_base_default=1, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (tid,),
        )
        self._refresh_templates_table()

    def _on_template_delete(self) -> None:
        tid = self._selected_template_id()
        if tid is None:
            return
        row = _env_db.fetchone(
            "SELECT name, base, cache_key, is_base_default FROM templates WHERE id=?",
            (tid,),
        )
        if not row:
            return
        reply = QMessageBox.question(
            self, tr("settings.tmpl_delete_confirm_title"),
            tr("settings.tmpl_delete_confirm_msg", name=row["name"], base=row["base"], cache_key=row["cache_key"]),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        from api.invoke_client import InvokeClient
        path = InvokeClient._template_cache_path(row["cache_key"])
        try:
            if path.exists():
                path.unlink()
        except Exception as exc:
            QMessageBox.warning(self, tr("settings.tmpl_delete_fail_title"), tr("settings.tmpl_delete_fail_msg", error=exc))
            return
        _env_db.execute("DELETE FROM templates WHERE id=?", (tid,))
        # 既定を失ったベースについて、残存の1件を既定に昇格
        if row["is_base_default"]:
            nxt = _env_db.fetchone(
                "SELECT id FROM templates WHERE base=? ORDER BY id ASC LIMIT 1",
                (row["base"],),
            )
            if nxt:
                _env_db.execute(
                    "UPDATE templates SET is_base_default=1 WHERE id=?",
                    (nxt["id"],),
                )
        self._refresh_templates_table()

    # ── データ管理タブ ──────────────────────────────────

    def _build_data_tab(self) -> QWidget:
        """タグデータ、バックアップ案内、キャッシュ管理を行うタブ。"""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")

        w = QWidget()
        w.setStyleSheet("background: transparent;")
        scroll.setWidget(w)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(16)

        _ss_group = (
            f"QGroupBox {{ color: {ACCENT}; border: 1px solid {SURFACE2}; "
            f"border-radius: 4px; margin-top: 8px; padding-top: 4px; "
            f"font-weight: bold; background: {SURFACE0}; }}"
            f"QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px; }}"
        )
        _ss_desc = f"color: {SUBTEXT}; background: transparent; padding: 2px 0;"
        _ss_btn = (
            f"QPushButton {{ background: {SURFACE1}; color: {TEXT}; "
            f"border: 1px solid {SURFACE2}; border-radius: 4px; padding: 5px 14px; }}"
            f"QPushButton:hover {{ background: {SURFACE2}; }}"
        )

        # ── バックアップ案内セクション ────────────────────
        backup_grp = QGroupBox(tr("settings.data_backup_section"))
        backup_grp.setStyleSheet(_ss_group)
        backup_lay = QVBoxLayout(backup_grp)
        backup_lay.setSpacing(8)

        backup_desc = QLabel(tr("settings.data_backup_desc"))
        backup_desc.setWordWrap(True)
        backup_desc.setStyleSheet(_ss_desc)
        backup_lay.addWidget(backup_desc)
        lay.addWidget(backup_grp)

        # ── キャッシュ管理セクション ────────────────────
        cache_grp = QGroupBox(tr("settings.data_cache_section"))
        cache_grp.setStyleSheet(_ss_group)
        cache_lay = QVBoxLayout(cache_grp)
        cache_lay.setSpacing(8)

        cache_desc = QLabel(tr("settings.data_cache_desc"))
        cache_desc.setWordWrap(True)
        cache_desc.setStyleSheet(_ss_desc)
        cache_lay.addWidget(cache_desc)

        self._suggestions_rebuild_on_startup_cb = QCheckBox(
            tr("settings.data_suggestions_rebuild_on_startup")
        )
        self._suggestions_rebuild_on_startup_cb.setStyleSheet(
            f"color: {TEXT}; background: transparent;"
        )
        cache_lay.addWidget(self._suggestions_rebuild_on_startup_cb)

        cache_btn_row = QHBoxLayout()
        cache_btn_row.setSpacing(8)

        btn_rebuild_index = QPushButton(tr("settings.data_rebuild_index_btn"))
        btn_rebuild_index.setStyleSheet(_ss_btn)
        btn_rebuild_index.setToolTip(tr("settings.data_rebuild_index_tip"))
        btn_rebuild_index.clicked.connect(self._on_rebuild_index)
        cache_btn_row.addWidget(btn_rebuild_index)

        btn_rebuild_suggestions = QPushButton(tr("settings.data_rebuild_suggestions_btn"))
        btn_rebuild_suggestions.setStyleSheet(_ss_btn)
        btn_rebuild_suggestions.setToolTip(tr("settings.data_rebuild_suggestions_tip"))
        btn_rebuild_suggestions.clicked.connect(self._on_rebuild_suggestions)
        cache_btn_row.addWidget(btn_rebuild_suggestions)

        cache_btn_row.addStretch()
        cache_lay.addLayout(cache_btn_row)
        lay.addWidget(cache_grp)

        lay.addStretch()
        return scroll

    # ── データ管理アクション ────────────────────────────

    def _on_rebuild_index(self) -> None:
        try:
            from db.discovery import rebuild_index
            rebuild_index()
            QMessageBox.information(
                self, tr("settings.data_rebuild_done_title"),
                tr("settings.data_rebuild_index_done_msg"),
            )
        except Exception as e:
            QMessageBox.critical(
                self, tr("settings.data_rebuild_error_title"),
                tr("settings.data_rebuild_error_msg", error=str(e)),
            )

    def _on_rebuild_suggestions(self) -> None:
        try:
            from db.discovery import rebuild_suggestions
            rebuild_suggestions()
            QMessageBox.information(
                self, tr("settings.data_rebuild_done_title"),
                tr("settings.data_rebuild_suggestions_done_msg"),
            )
        except Exception as e:
            QMessageBox.critical(
                self, tr("settings.data_rebuild_error_title"),
                tr("settings.data_rebuild_error_msg", error=str(e)),
            )

    def _build_btn_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addStretch()

        save_btn = QPushButton(tr("settings.save_btn"))
        save_btn.setStyleSheet(_settings_button_style("success"))
        save_btn.clicked.connect(self._save_and_close)
        row.addWidget(save_btn)

        cancel_btn = QPushButton(tr("settings.cancel_btn"))
        cancel_btn.setStyleSheet(
            f"QPushButton {{ background-color: {SURFACE1}; color: {TEXT}; "
            f"border: 1px solid {SURFACE2}; border-radius: 4px; padding: 5px 16px; }}"
            f"QPushButton:hover {{ background-color: {SURFACE2}; }}"
        )
        cancel_btn.clicked.connect(self.reject)
        row.addWidget(cancel_btn)

        return row

    # ── 値の読み込み / 書き込み ────────────────────────────

    def _load_values(self) -> None:
        # 言語
        lang = _read_setting("language", "ja")
        idx = self._lang_combo.findData(lang)
        if idx >= 0:
            self._lang_combo.setCurrentIndex(idx)

        # テーマ
        theme = _read_setting("theme", "dark")
        idx = self._theme_combo.findData(theme)
        if idx >= 0:
            self._theme_combo.setCurrentIndex(idx)

        # フォントサイズ
        font_size = _read_setting("font_size", "10")
        idx = self._font_combo.findData(font_size)
        if idx >= 0:
            self._font_combo.setCurrentIndex(idx)

        # タイル表示
        tile_display = _read_setting("tile_local_only_display", "0")
        idx = self._tile_display_combo.findData(tile_display)
        if idx >= 0:
            self._tile_display_combo.setCurrentIndex(idx)

        # タグ入力サジェスト
        self._tag_suggestions_cb.setChecked(_read_setting("tag_input_suggestions_enabled", "1") != "0")

        # NSFW
        self._nsfw_cb.setChecked(_read_setting("show_nsfw", "0") == "1")

        # 無所属タイル色
        self._apply_color_to_btn(
            self._unreg_bg_btn,
            _read_setting("unregistered_tile_bg", UNREGISTERED_TILE_DEFAULT_BG),
        )
        self._apply_color_to_btn(
            self._unreg_border_btn,
            _read_setting("unregistered_tile_border", UNREGISTERED_TILE_DEFAULT_BORDER),
        )
        self._apply_color_to_btn(
            self._unreg_fg_btn,
            _read_setting("unregistered_tile_fg", UNREGISTERED_TILE_DEFAULT_FG),
        )

        # 履歴の色（テーマ別既定）
        self._apply_color_to_btn(
            self._hist_text_dark_btn,
            _read_setting("history_text_color_dark", history_default_text_color("dark")),
        )
        self._apply_color_to_btn(
            self._hist_text_light_btn,
            _read_setting("history_text_color_light", history_default_text_color("light")),
        )
        self._apply_color_to_btn(
            self._hist_line_dark_btn,
            _read_setting("history_line_color_dark", history_default_line_color("dark")),
        )
        self._apply_color_to_btn(
            self._hist_line_light_btn,
            _read_setting("history_line_color_light", history_default_line_color("light")),
        )

        # アイコン
        icon_path = _read_setting("app_icon_path", "")
        self._icon_edit.setText(icon_path)

        # 起動時 suggestions 再構築
        self._suggestions_rebuild_on_startup_cb.setChecked(
            _read_setting("suggestions_rebuild_on_startup", "0") == "1"
        )

        # InvokeAI URL
        endpoint = _read_setting("invoke_endpoint", "")
        if not endpoint and self._client is not None:
            endpoint = getattr(self._client, "endpoint", "") or ""
        self._endpoint_edit.setText(endpoint or "http://localhost:9090")

        # キューID
        queue_id = _read_setting("invoke_queue_id", "")
        if not queue_id and self._client is not None:
            queue_id = getattr(self._client, "queue_id", "") or ""
        self._queue_id_edit.setText(queue_id or "default")

        # Local LLM
        lm_provider = _read_setting("lm_provider", "lmstudio")
        idx = self._lm_provider_combo.findData(lm_provider)
        self._lm_provider_combo.setCurrentIndex(idx if idx >= 0 else 0)
        lm_endpoint = _read_setting("lm_endpoint", "http://localhost:1234")
        self._lm_endpoint_edit.setText(lm_endpoint)
        classify_provider = _read_setting("lm_classify_provider", "")
        idx = self._lm_classify_provider_combo.findData(classify_provider)
        self._lm_classify_provider_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._lm_classify_endpoint_edit.setText(_read_setting("lm_classify_endpoint", ""))
        self._lm_classify_model_edit.setText(_read_setting("lm_classify_model", ""))
        self._lm_classify_seed_edit.setText(_read_setting("lm_classify_seed", "0"))
        self._lm_classify_temperature_edit.setText(_read_setting("lm_classify_temperature", "0"))
        self._lm_classify_prompt_edit.setText(_read_setting("lm_classify_prompt", ""))
        self._loading = False  # 初期化完了: 以降は即時保存を有効化

    def _pick_icon(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr("settings.icon_btn"),
            "", "Images (*.png *.ico *.jpg *.svg)"
        )
        if path:
            self._icon_edit.setText(path)
            self._auto_save()

    def _connect_auto_save_signals(self) -> None:
        """各ウィジェットの変更シグナルを _auto_save に接続する。"""
        self._lang_combo.currentIndexChanged.connect(self._auto_save)
        self._theme_combo.currentIndexChanged.connect(self._auto_save)
        self._font_combo.currentIndexChanged.connect(self._auto_save)
        self._tile_display_combo.currentIndexChanged.connect(self._auto_save)
        self._tag_suggestions_cb.stateChanged.connect(self._auto_save)
        self._nsfw_cb.stateChanged.connect(self._auto_save)
        self._suggestions_rebuild_on_startup_cb.stateChanged.connect(self._auto_save)
        self._endpoint_edit.editingFinished.connect(self._auto_save)
        self._queue_id_edit.editingFinished.connect(self._auto_save)
        self._lm_provider_combo.currentIndexChanged.connect(self._auto_save)
        self._lm_endpoint_edit.editingFinished.connect(self._auto_save)
        self._lm_classify_provider_combo.currentIndexChanged.connect(self._auto_save)
        self._lm_classify_endpoint_edit.editingFinished.connect(self._auto_save)
        self._lm_classify_model_edit.editingFinished.connect(self._auto_save)
        self._lm_classify_seed_edit.editingFinished.connect(self._auto_save)
        self._lm_classify_temperature_edit.editingFinished.connect(self._auto_save)
        self._lm_classify_prompt_edit.editingFinished.connect(self._auto_save)

    def _auto_save(self) -> None:
        """loading 中以外は即時 DB 保存。"""
        if self._loading:
            return
        self._do_save()

    def _do_save(self) -> None:
        """全設定を DB に書き込み、クライアントにも反映する。"""
        _write_setting("language",   self._lang_combo.currentData())
        _write_setting("theme",      self._theme_combo.currentData())
        _write_setting("font_size",  self._font_combo.currentData())
        _write_setting("tile_local_only_display", self._tile_display_combo.currentData())
        _write_setting("tag_input_suggestions_enabled", "1" if self._tag_suggestions_cb.isChecked() else "0")
        _write_setting("show_nsfw",  "1" if self._nsfw_cb.isChecked() else "0")
        _write_setting("unregistered_tile_bg", self._unreg_bg_btn.text().strip() or UNREGISTERED_TILE_DEFAULT_BG)
        _write_setting("unregistered_tile_border", self._unreg_border_btn.text().strip() or UNREGISTERED_TILE_DEFAULT_BORDER)
        _write_setting("unregistered_tile_fg", self._unreg_fg_btn.text().strip() or UNREGISTERED_TILE_DEFAULT_FG)
        _write_setting("history_text_color_dark", self._hist_text_dark_btn.text().strip() or history_default_text_color("dark"))
        _write_setting("history_text_color_light", self._hist_text_light_btn.text().strip() or history_default_text_color("light"))
        _write_setting("history_line_color_dark", self._hist_line_dark_btn.text().strip() or history_default_line_color("dark"))
        _write_setting("history_line_color_light", self._hist_line_light_btn.text().strip() or history_default_line_color("light"))
        _write_setting("app_icon_path", self._icon_edit.text().strip())
        _write_setting(
            "suggestions_rebuild_on_startup",
            "1" if self._suggestions_rebuild_on_startup_cb.isChecked() else "0",
        )

        endpoint = self._endpoint_edit.text().strip() or "http://localhost:9090"
        queue_id = self._queue_id_edit.text().strip() or "default"
        _write_setting("invoke_endpoint", endpoint)
        _write_setting("invoke_queue_id", queue_id)
        _write_setting(
            "lm_endpoint",
            self._lm_endpoint_edit.text().strip() or "http://localhost:1234",
        )
        _write_setting("lm_provider", self._lm_provider_combo.currentData() or "lmstudio")
        _write_setting("lm_classify_provider", self._lm_classify_provider_combo.currentData() or "")
        _write_setting("lm_classify_endpoint", self._lm_classify_endpoint_edit.text().strip())
        _write_setting("lm_classify_model", self._lm_classify_model_edit.text().strip())
        _write_setting("lm_classify_seed", self._lm_classify_seed_edit.text().strip() or "0")
        _write_setting("lm_classify_temperature", self._lm_classify_temperature_edit.text().strip() or "0")
        _write_setting("lm_classify_prompt", self._lm_classify_prompt_edit.text().strip())

        if self._client is not None:
            if endpoint:
                self._client.endpoint = endpoint
            if queue_id:
                self._client.queue_id = queue_id

    def _save_and_close(self) -> None:
        self._do_save()
        self.accept()
