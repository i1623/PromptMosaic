"""
タイルウィジェット（TagTile / NaturalTextTile 1個分の表示）

構造:
  TileWidget (QWidget)
    HBoxLayout
      _btn_container (VBox)
        row1: [ON][×]         ← 上段ラベルと同行
        row2: [+][−] (2段時)  ← 下段ラベルと同行（TagTile のみ）
      label VBox
        現地語テキスト (上段)
        ─ (薄い横区切り)
        英語プロンプト  (下段)

重要: QWidget サブクラスが stylesheet の背景・枠・角丸を描画するには
      paintEvent の明示的なオーバーライドが必須（Qt の仕様）。
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QDialog, QDialogButtonBox, QLineEdit,
    QSizePolicy, QApplication, QTextEdit, QStyleOption, QStyle,
)
from PySide6.QtCore import Signal, Qt, QPoint, QPointF, QRectF, QMimeData, QThread, Property, QSequentialAnimationGroup, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QFont, QDrag, QPainter, QTextCursor, QColor, QPen, QTextOption, QLinearGradient, QBrush

import db.app_db as _app_db
import db.library_db as _library_db
from api.lm_client import LMClient, LMStudioError, translation_fallback_from_thinking
from core.prompt_builder import TagTile, NaturalTextTile, AnyTile
from core.i18n import tr
from core.lm_settings import lm_seed, lm_temperature
from core.text_sanitize import single_line_text
from ui.styles import get_tile_style, tag_browser_chip_colors, is_light_theme, SURFACE0, SURFACE1, SURFACE2, TEXT, SUBTEXT, RED, ui_font


def _unregistered_tile_style() -> tuple[str, str, str]:
    bg = _get_setting("unregistered_tile_bg", "#001d9c")
    fg = _get_setting("unregistered_tile_fg", "#d2bf2e")
    border = _get_setting("unregistered_tile_border", "#91821f")
    return (
        f"background-color: {bg}; color: {fg}; border: 3px dashed {border}; "
        "border-radius: 4px; padding: 0;",
        f"border: none; color: {fg};",
        f"border: none; color: {fg};",
    )


def _saturated_border(color: str) -> QColor:
    """タイル外枠用の枠線色を返す。

    ダークテーマでは tag_browser_chip_colors() 由来の枠線が白っぽく見えるため、
    明度をやや落として白浮きを抑える。彩度は元の値のまま。ライトテーマは素のまま。
    """
    c = QColor(color)
    if is_light_theme():
        return c
    h, s, v, a = c.getHsv()
    if h < 0:  # 無彩色（hue=-1）はそのまま
        return c
    v = int(v * 0.85)
    c.setHsv(h, s, v, a)
    return c


def _dialog_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet(
        f"QLabel {{ color: {TEXT}; background: transparent; border: none; padding: 0; }}"
    )
    return label


def _equalize_ok_cancel_buttons(btns: QDialogButtonBox) -> None:
    buttons = [
        btns.button(QDialogButtonBox.StandardButton.Ok),
        btns.button(QDialogButtonBox.StandardButton.Cancel),
    ]
    buttons = [b for b in buttons if b is not None]
    if not buttons:
        return
    width = max(b.sizeHint().width() for b in buttons)
    for button in buttons:
        button.setFixedWidth(width)


def show_translation_compare_dialog(
    parent,
    *,
    title: str,
    result_label: str,
    source_text: str,
    translated_text: str,
    result_text: str,
    apply_label: str,
) -> bool:
    result_dlg = QDialog(parent)
    result_dlg.setWindowTitle(title)
    result_dlg.setMinimumWidth(620)
    result_dlg.setStyleSheet(f"QDialog {{ background-color: {SURFACE0}; color: {TEXT}; }}")
    result_lay = QVBoxLayout(result_dlg)
    result_lay.setSpacing(6)

    field_style = (
        f"QTextEdit {{ background: {SURFACE1}; color: {TEXT}; "
        f"border: 1px solid {SURFACE2}; border-radius: 4px; padding: 4px; }}"
    )

    def _add_readonly(label: str, text: str, height: int = 72) -> None:
        result_lay.addWidget(_dialog_label(label))
        edit = QTextEdit(text)
        edit.setReadOnly(True)
        edit.setMinimumHeight(height)
        edit.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        edit.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        edit.setAcceptRichText(False)
        edit.setStyleSheet(field_style)
        result_lay.addWidget(edit)

    _add_readonly(tr("tile.compare_source_label"), source_text)
    _add_readonly(tr("tile.compare_translated_label"), translated_text)
    _add_readonly(result_label, result_text, 96)

    result_buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    apply_btn = result_buttons.addButton(apply_label, QDialogButtonBox.ButtonRole.AcceptRole)
    apply_btn.clicked.connect(result_dlg.accept)
    result_buttons.rejected.connect(result_dlg.reject)
    result_lay.addWidget(result_buttons)
    return result_dlg.exec() == QDialog.DialogCode.Accepted


def _ui_language() -> str:
    row = _app_db.fetchone("SELECT value FROM app_settings WHERE key = 'language'")
    return row["value"] if row else "ja"


TILE_MIME = "application/x-invoke-tile"


def _tile_display_mode() -> str:
    """タイル表示モードを返す。

    "0" = 2段（上: 現地語 / 下: 英語）
    "1" = 上1段（現地語のみ。なければ英語フォールバック）
    "2" = 下1段（英語のみ）
    """
    row = _app_db.fetchone("SELECT value FROM app_settings WHERE key='tile_local_only_display'")
    value = row["value"] if row else "0"
    return value if value in ("0", "1", "2") else "0"

_OLD_CATEGORY_TO_GENRE: dict[str, str] = {
    "object": "object_artifact",
    "state": "human_expression",
    "quality": "quality_correction",
    "style": "art_style_medium",
    "composition": "pose_action_interaction",
    "lighting": "lighting_color_screen_effect",
    "action": "pose_action_interaction",
    "scene": "location_background",
}


def _normalize_tile_category(category: str) -> str:
    return _OLD_CATEGORY_TO_GENRE.get(category, category)


def _find_registered_tag(tag: str, dictionary_key: str = ""):
    tag = (tag or "").strip()
    if not tag:
        return None
    try:
        return _library_db.fetchone(
            "SELECT name_en, name_local, category FROM tags "
            "WHERE COALESCE(is_nav_only, 0) = 0 "
            "  AND (LOWER(name_en) = LOWER(?) OR LOWER(COALESCE(name_local,'')) = LOWER(?)) "
            "LIMIT 1",
            (tag, tag),
        )
    except Exception:
        return None


def _get_setting(key: str, default: str = "") -> str:
    row = _app_db.fetchone("SELECT value FROM app_settings WHERE key=?", (key,))
    return (row["value"] if row else "") or default


def translation_model_missing_message() -> str:
    return tr("main.translate_lm_not_configured")


def is_translation_model_configured() -> bool:
    return bool(_get_setting("lm_translate_model", "").strip())


def _ui_language_label() -> str:
    lang = _ui_language()
    return {
        "ja": "Japanese",
        "en": "English",
    }.get(lang, lang or "the current UI language")


def _stored_translate_prompt(mode: str) -> str:
    if mode == "natural":
        from ui.lm_prompt_editor import _DEFAULT_NATURAL_PROMPT
        return _get_setting("lm_translate_prompt_natural", _DEFAULT_NATURAL_PROMPT)
    if mode == "reverse":
        from ui.lm_prompt_editor import _DEFAULT_REVERSE_PROMPT
        return _get_setting("lm_translate_prompt_reverse", _DEFAULT_REVERSE_PROMPT)
    from ui.lm_prompt_editor import _DEFAULT_PROMPT
    return _get_setting("lm_translate_prompt", _DEFAULT_PROMPT)


def _tile_translate_prompt(mode: str, reverse: bool) -> str:
    target_lang = _ui_language_label()
    if reverse:
        base = _stored_translate_prompt("reverse")
        return (
            f"{base}\n\n"
            f"The target language follows the app language setting: {target_lang}.\n"
            f"Output only a concise, reusable {target_lang} translation."
        )
    base = _stored_translate_prompt(mode)
    if mode == "natural":
        return (
            f"{base}\n\n"
            f"The input language follows the app language setting: {target_lang}.\n"
            "Output only a natural English prompt sentence."
        )
    return (
        f"{base}\n\n"
        f"The input language follows the app language setting: {target_lang}.\n"
        "Output only English Danbooru-style comma-separated tags."
    )


class _TileTranslateWorker(QThread):
    status_update    = Signal(str)
    thinking_chunk   = Signal(str)
    content_chunk    = Signal(str)
    translation_done = Signal(str)
    failed           = Signal(str)

    def __init__(self, text: str, mode: str, reverse: bool, parent=None):
        super().__init__(parent)
        self._text = text
        self._endpoint = _get_setting("lm_endpoint", "http://localhost:1234")
        self._provider = _get_setting("lm_provider", "lmstudio")
        self._model = _get_setting("lm_translate_model", "")
        try:
            self._chunk_timeout = float(_get_setting("lm_chunk_timeout", "60"))
        except ValueError:
            self._chunk_timeout = 60.0
        self._system_prompt = _tile_translate_prompt(mode, reverse)
        self._cancel = [False]

    def cancel(self) -> None:
        self._cancel[0] = True

    def cancel_and_wait(self, timeout_ms: int = 2000) -> None:
        self.cancel()
        if self.isRunning():
            self.wait(timeout_ms)

    def run(self) -> None:
        try:
            if not self._model.strip():
                raise LMStudioError(translation_model_missing_message())
            client = LMClient(
                base_url=self._endpoint,
                chunk_timeout=self._chunk_timeout,
                provider=self._provider,
            )
            status = client.check_connection()
            if not status.ok:
                raise LMStudioError(status.message)
            content_buf: list[str] = []
            thinking_buf: list[str] = []
            for ev_type, ev_data in client.translate_stream(
                self._text,
                self._system_prompt,
                self._model,
                temperature=lm_temperature(),
                cancel_flag=self._cancel,
                seed=lm_seed(),
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


class TileWidget(QWidget):
    """
    1タイルを表示するウィジェット。

    Signals:
        delete_requested(TileWidget): 削除ボタンが押されたとき
        tile_changed():               タイルのデータが変更されたとき
        tile_replaced(TileWidget, AnyTile): タイル種別の変換などで別タイルに差し替えるとき
    """

    delete_requested = Signal(object)
    tile_changed     = Signal()
    tile_replaced    = Signal(object, object)
    move_requested   = Signal(object, int)

    def __init__(self, tile: AnyTile, parent=None, *, readonly: bool = False):
        super().__init__(parent)
        self.setObjectName("tile_widget")   # #tile_widget セレクタで border を自身にのみ限定
        self.tile = tile
        self._readonly = bool(readonly)
        self._drag_start: QPoint | None = None
        self._is_unregistered_tag = False
        self._tile_bg: QColor | None = None       # M1 テクスチャ描画用の土台色
        self._tile_border: QColor | None = None
        self._tile_textured = False               # paintEvent でタイル質感を描くか
        self._duplicate_hint_strength = 0.0
        self._duplicate_hint_anim: QSequentialAnimationGroup | None = None
        self._build_ui()
        self._apply_style()
        self._apply_readonly_state()

    # ── paintEvent: これがないと stylesheet の背景・枠・角丸が描画されない ──

    def paintEvent(self, event) -> None:
        opt = QStyleOption()
        opt.initFrom(self)
        p = QPainter(self)
        if getattr(self, "_tile_textured", False) and self._tile_bg is not None:
            # M1: 縦グラデ＋ベベルで「一枚のタイル」質感を描く（レイアウト不変）
            self._paint_tile_surface(p)
        else:
            self.style().drawPrimitive(QStyle.PrimitiveElement.PE_Widget, opt, p, self)
        if getattr(self, "_is_unregistered_tag", False):
            p.save()
            p.setPen(QColor(210, 15, 57, 55))
            f = self.font()
            f.setPointSize(max(10, f.pointSize() + 3))
            f.setBold(True)
            p.setFont(f)
            step_x = 28
            step_y = 22
            for y in range(-4, self.height() + step_y, step_y):
                for x in range(4, self.width() + step_x, step_x):
                    p.drawText(x, y + 16, "✓")
            p.restore()
        if self._duplicate_hint_strength > 0.0:
            p.save()
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            glow = QColor(255, 232, 140)
            outline = QColor(255, 196, 64)
            fill_alpha = int(78 * self._duplicate_hint_strength)
            line_alpha = int(190 * self._duplicate_hint_strength)
            glow.setAlpha(max(0, min(255, fill_alpha)))
            outline.setAlpha(max(0, min(255, line_alpha)))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(glow)
            p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 7, 7)
            for inset, alpha_scale, width in ((1, 0.95, 3), (3, 0.55, 2), (5, 0.28, 1)):
                stroke = QColor(outline)
                stroke.setAlpha(int(line_alpha * alpha_scale))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(stroke, width))
                p.drawRoundedRect(self.rect().adjusted(inset, inset, -inset, -inset), 7, 7)
            p.restore()

    def _paint_tile_surface(self, p: QPainter) -> None:
        """M1 タイル質感: 縦グラデーション＋上下ベベル＋外枠を描画する。

        色はカテゴリ土台色（self._tile_bg）から派生させるため、15カテゴリ・
        ダーク/ライト両テーマに自動追従する。文字は透明ラベルとして
        このグラデーションの上に乗る。
        """
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rf = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        bg = self._tile_bg

        grad = QLinearGradient(rf.topLeft(), rf.bottomLeft())
        grad.setColorAt(0.0, bg.lighter(122))
        grad.setColorAt(0.5, bg)
        grad.setColorAt(1.0, bg.darker(116))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(grad))
        p.drawRoundedRect(rf, 6, 6)

        # 上部内側ハイライト（面が手前に浮く）
        p.setPen(QPen(QColor(255, 255, 255, 46), 1))
        p.drawLine(QPointF(rf.left() + 6, rf.top() + 1.5),
                   QPointF(rf.right() - 6, rf.top() + 1.5))
        # 下部内側シャドウ
        p.setPen(QPen(QColor(0, 0, 0, 70), 1))
        p.drawLine(QPointF(rf.left() + 6, rf.bottom() - 1.5),
                   QPointF(rf.right() - 6, rf.bottom() - 1.5))

        # 外枠
        if self._tile_border is not None:
            p.setPen(QPen(self._tile_border, 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(rf, 6, 6)
        p.restore()

    def _get_duplicate_hint_strength(self) -> float:
        return self._duplicate_hint_strength

    def _set_duplicate_hint_strength(self, value: float) -> None:
        self._duplicate_hint_strength = max(0.0, min(1.0, float(value)))
        self.update()

    duplicate_hint_strength = Property(float, _get_duplicate_hint_strength, _set_duplicate_hint_strength)

    def play_duplicate_hint(self, pulses: int = 3, total_duration_ms: int = 1500) -> None:
        """重複注意の柔らかい点滅を一定回数だけ再生する。"""
        if self._duplicate_hint_anim is not None:
            self._duplicate_hint_anim.stop()
            self._duplicate_hint_anim.deleteLater()
            self._duplicate_hint_anim = None

        pulse_ms = max(120, total_duration_ms // max(1, pulses))
        rise_ms = max(70, int(pulse_ms * 0.35))
        fall_ms = max(70, pulse_ms - rise_ms)

        group = QSequentialAnimationGroup(self)
        for _ in range(max(1, pulses)):
            anim_up = QPropertyAnimation(self, b"duplicate_hint_strength", group)
            anim_up.setDuration(rise_ms)
            anim_up.setStartValue(0.0)
            anim_up.setEndValue(1.0)
            anim_up.setEasingCurve(QEasingCurve.Type.OutCubic)
            group.addAnimation(anim_up)

            anim_down = QPropertyAnimation(self, b"duplicate_hint_strength", group)
            anim_down.setDuration(fall_ms)
            anim_down.setStartValue(1.0)
            anim_down.setEndValue(0.0)
            anim_down.setEasingCurve(QEasingCurve.Type.InOutQuad)
            group.addAnimation(anim_down)

        group.finished.connect(lambda: self._set_duplicate_hint_strength(0.0))
        self._duplicate_hint_anim = group
        group.start()

    # ── UI構築 ──────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(4)

        # ── ボタンエリア（QWidget コンテナなし: レイアウトを直接追加） ─────
        # QWidget コンテナを使うと親の setStyleSheet が border をカスケードして
        # ボタン群を囲む枠が出現するため、QVBoxLayout をそのまま使う。
        self._btn_vbox = QVBoxLayout()
        self._btn_vbox.setContentsMargins(0, 0, 0, 0)
        self._btn_vbox.setSpacing(2)

        # row1: [ON][×] 常時 + TagTile では初期状態で [+][−] も同行
        self._btn_row1_lay = QHBoxLayout()
        self._btn_row1_lay.setContentsMargins(0, 0, 0, 0)
        self._btn_row1_lay.setSpacing(2)

        self._toggle_btn = QPushButton("ON")
        self._toggle_btn.setFixedSize(36, 17)
        self._toggle_btn.setFont(ui_font(-2, bold=True))
        self._toggle_btn.setToolTip(tr("tile.toggle_tooltip"))
        self._toggle_btn.clicked.connect(self._toggle_enabled)
        self._btn_row1_lay.addWidget(self._toggle_btn)

        self._del_btn = QPushButton("✕")
        self._del_btn.setFixedSize(14, 14)
        self._del_btn.setFont(ui_font(-1, bold=True))
        self._del_btn.setToolTip(tr("tile.delete_tooltip"))
        self._del_btn.setStyleSheet(
            "QPushButton { background: #3a2a2a; color: #f38ba8;"
            " border: 1px solid #c04060; border-radius: 2px; padding: 0; }"
            "QPushButton:hover { background: #f38ba8; color: #1e1e2e;"
            " border-color: #f38ba8; }"
        )
        self._del_btn.clicked.connect(lambda: self.delete_requested.emit(self))
        self._btn_row1_lay.addWidget(self._del_btn)

        # TagTile / NaturalTextTile: +/- は初期状態で row1 に並べる。2段表示時に row2 へ移動する。
        # row2 は QWidget コンテナなしの QHBoxLayout を直接 btn_vbox に追加。
        self._btn_row2_lay: QHBoxLayout | None = None
        self._strength_in_row2 = False
        if isinstance(self.tile, (TagTile, NaturalTextTile)):
            self._plus_btn = QPushButton("+")
            self._plus_btn.setFixedSize(14, 14)
            self._plus_btn.setFont(ui_font(-3, bold=True))
            self._plus_btn.setToolTip(tr("tile.strength_plus_tooltip"))
            self._plus_btn.setStyleSheet(
                "QPushButton { background: transparent; color: #a6e3a1;"
                " border: 1px solid #4a8a4a; border-radius: 2px; padding: 0; }"
                "QPushButton:hover { background: #2a5a2a; border-color: #a6e3a1; }"
            )
            self._plus_btn.clicked.connect(self._strength_plus)
            self._btn_row1_lay.addWidget(self._plus_btn)

            self._minus_btn = QPushButton("−")
            self._minus_btn.setFixedSize(14, 14)
            self._minus_btn.setFont(ui_font(-3, bold=True))
            self._minus_btn.setToolTip(tr("tile.strength_minus_tooltip"))
            self._minus_btn.setStyleSheet(
                "QPushButton { background: transparent; color: #f38ba8;"
                " border: 1px solid #8a4a4a; border-radius: 2px; padding: 0; }"
                "QPushButton:hover { background: #5a2a2a; border-color: #f38ba8; }"
            )
            self._minus_btn.clicked.connect(self._strength_minus)
            self._btn_row1_lay.addWidget(self._minus_btn)

            self._btn_row2_lay = QHBoxLayout()
            self._btn_row2_lay.setContentsMargins(0, 0, 0, 0)
            self._btn_row2_lay.setSpacing(2)

        self._btn_vbox.addLayout(self._btn_row1_lay)
        layout.addLayout(self._btn_vbox)

        # ── ラベルエリア（右・残り幅すべて） ──────────────────────────────
        c_lay = QVBoxLayout()
        c_lay.setContentsMargins(2, 1, 3, 1)
        c_lay.setSpacing(0)

        self._label = QLabel()
        self._label.setFont(ui_font())
        self._label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        c_lay.addWidget(self._label)

        # 上段/下段の横区切り（現地語あり時のみ表示）
        self._label_sep = QWidget()
        self._label_sep.setFixedHeight(1)
        self._label_sep.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._label_sep.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._label_sep.hide()
        c_lay.addWidget(self._label_sep)

        self._sub_label = QLabel()
        self._sub_label.setFont(ui_font(-2))
        self._sub_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._sub_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._sub_label.hide()
        c_lay.addWidget(self._sub_label)

        layout.addLayout(c_lay, 1)   # stretch=1 で残り幅を使用

        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setMaximumWidth(420)

        self._update_labels()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)

    # ── スタイル適用 ────────────────────────────────────

    def _apply_style(self) -> None:
        enabled = getattr(self.tile, "enabled", True)

        # ── カテゴリを先に確定（有効・無効共通） ─────────────────────────────
        category = self.tile.category if isinstance(self.tile, TagTile) else "__natural__"
        is_unregistered_tag = False
        if isinstance(self.tile, TagTile):
            row = _find_registered_tag(self.tile.tag_name)
            if row and row["category"]:
                self.tile.category = row["category"]
                category = row["category"]
                if row["name_local"] and not self.tile.tag_local:
                    self.tile.tag_local = row["name_local"]
            else:
                category = _normalize_tile_category(category)
                self.tile.category = category
                is_unregistered_tag = True

        # ── タイル本体スタイル: 有効・無効で背景/枠は共通 ────────────────────
        # ※ セレクタを付けない素のCSSを setStyleSheet するのは意図的。
        #    タイル本体だけでなく QLabel 子（_label / _sub_label）にも background-color が
        #    カスケードしてタイルの背景色が正しく文字部に乗る挙動に依存している。
        #    ダイアログ側で目立つ枠線がカスケード経由で乗る不具合は、
        #    ダイアログ内 QLabel 個別に明示スタイルを当てる方法で吸収する。
        self._is_unregistered_tag = is_unregistered_tag
        if is_unregistered_tag:
            tile_style, label_style, sub_style = _unregistered_tile_style()
            self.setStyleSheet(tile_style)
            self._label.setStyleSheet(label_style)
            self._sub_label.setStyleSheet(sub_style)
            self._tile_textured = False
            self._tile_bg = None
            self._tile_border = None
        else:
            style_category = "natural_feature" if category == "__natural__" else category
            bg, _fg, chip_border = tag_browser_chip_colors(style_category)
            # 本体は paintEvent で M1 質感を描くが、setStyleSheet は子ラベルへ
            # 文字色をカスケードさせる目的で従来どおり残す。
            self.setStyleSheet(
                get_tile_style(style_category).replace("border: 1px solid", "border: 2px solid")
                + " padding: 0;"
            )
            # ラベル背景を透明化し、本体グラデーションを文字の背後に透過させる。
            self._label.setStyleSheet("background: transparent; border: none;")
            self._sub_label.setStyleSheet(f"background: transparent; border: none; color: {SUBTEXT};")
            self._tile_bg = QColor(bg)
            self._tile_border = _saturated_border(chip_border)
            self._tile_textured = True

        # ── ラベルフォント ────────────────────────────────────────────────────
        for lbl in (self._label, self._sub_label):
            lf = lbl.font()
            lf.setStrikeOut(not enabled)
            lf.setUnderline(False)
            lf.setItalic(category == "__natural__")
            lbl.setFont(lf)
        if enabled and isinstance(self.tile, TagTile):
            lf = self._label.font()
            lf.setUnderline(self.tile.is_trigger_word)
            self._label.setFont(lf)

        # ── ON/OFF トグルボタン ───────────────────────────────────────────────
        if not enabled:
            self._toggle_btn.setText("OFF")
            self._toggle_btn.setStyleSheet(
                "QPushButton { background: transparent; color: #5a5a7a;"
                " border: 1px solid #4a4a6a; border-radius: 2px; padding: 0 2px; }"
                "QPushButton:hover { color: #a6adc8; border-color: #6a6a9a; }"
            )
        else:
            self._toggle_btn.setText("ON")
            self._toggle_btn.setStyleSheet(
                "QPushButton { background: transparent; color: #7ab87a;"
                " border: 1px solid #3a6a3a; border-radius: 2px; padding: 0 2px; }"
                "QPushButton:hover { color: #a6e3a1; border-color: #5a9a5a; }"
            )

    # ── ラベル更新 ──────────────────────────────────────

    def _make_label_texts(self) -> tuple[str, str]:
        """(上段: 現地語, 下段: 英語) を返す。下段が空なら1段表示。"""
        if isinstance(self.tile, TagTile):
            if not self.tile.tag_local:
                row = _library_db.fetchone(
                    "SELECT label FROM tag_labels WHERE tag_name = ?",
                    (self.tile.tag_name,),
                )
                if row:
                    self.tile.tag_local = row["label"]

            lv  = self.tile.strength_level
            s   = ("+" * lv) if lv > 0 else ("-" * abs(lv)) if lv < 0 else ""
            emp = abs(self.tile.emphasis - 1.0) > 1e-4
            e   = f"[{self.tile.emphasis:.4g}]" if emp else ""
            prefix = f"{s}{e} " if (s or e) else ""

            local = single_line_text(self.tile.tag_local)
            en    = single_line_text(self.tile.tag_name)
            mode  = _tile_display_mode()
            if mode == "1":   # 上1段: 現地語のみ
                return (local or f"{prefix}{en}", "")
            if mode == "2":   # 下1段: 英語のみ
                return (f"{prefix}{en}", "")
            return (local, f"{prefix}{en}") if local else (f"{prefix}{en}", "")

        else:
            display    = single_line_text(self.tile.display_label)
            source     = single_line_text(self.tile.source_text or self.tile.text)
            translated = single_line_text(self.tile.translated_text or self.tile.text)

            lv  = self.tile.strength_level
            s   = ("+" * lv) if lv > 0 else ("-" * abs(lv)) if lv < 0 else ""
            emp = abs(self.tile.emphasis - 1.0) > 1e-4
            e   = f"[{self.tile.emphasis:.4g}]" if emp else ""
            prefix = f"{s}{e} " if (s or e) else ""

            def _t(s: str, n: int = 30) -> str:
                return s[:n - 2] + "…" if len(s) > n else s

            mode = _tile_display_mode()
            if mode == "1":   # 上1段: 現地語側（表示ラベル→原文→訳の順）
                return _t(display or source or translated), ""
            if mode == "2":   # 下1段: 英語側（送信される訳文側）
                return f"{prefix}{_t(translated or source or display)}", ""
            if display:
                sub = _t(translated) if translated and translated != display else ""
                return _t(display), (f"{prefix}{sub}" if prefix and sub else sub)
            elif source and translated and source != translated:
                return _t(source), f"{prefix}{_t(translated)}"
            else:
                return f"{prefix}{_t(translated or source)}", ""

    def _update_labels(self) -> None:
        main, sub = self._make_label_texts()
        self._label.setText(main)
        if sub:
            self._sub_label.setText(sub)
            self._sub_label.show()
            self._move_strength_to_row2()
        else:
            self._label_sep.hide()
            self._sub_label.hide()
            self._move_strength_to_row1()
        self.updateGeometry()

    def _move_strength_to_row2(self) -> None:
        """TagTile 2段表示: +/- を row1 から row2（btn_vbox の 2段目）へ移動する。"""
        if self._btn_row2_lay is None or self._strength_in_row2:
            return
        self._btn_row1_lay.removeWidget(self._plus_btn)
        self._btn_row1_lay.removeWidget(self._minus_btn)
        self._btn_row2_lay.addWidget(self._plus_btn)
        self._btn_row2_lay.addWidget(self._minus_btn)
        self._plus_btn.show()
        self._minus_btn.show()
        self._btn_vbox.addLayout(self._btn_row2_lay)
        self._strength_in_row2 = True

    def _move_strength_to_row1(self) -> None:
        """TagTile 1段表示: +/- を row2 から row1（btn_vbox の 1段目）へ戻す。"""
        if self._btn_row2_lay is None or not self._strength_in_row2:
            return
        self._btn_row2_lay.removeWidget(self._plus_btn)
        self._btn_row2_lay.removeWidget(self._minus_btn)
        # btn_vbox から row2_lay を除去（index=1）
        while self._btn_vbox.count() > 1:
            self._btn_vbox.takeAt(1)
        self._btn_row1_lay.addWidget(self._plus_btn)
        self._btn_row1_lay.addWidget(self._minus_btn)
        self._plus_btn.show()
        self._minus_btn.show()
        self._strength_in_row2 = False

    # ── ダブルクリック → 編集 ──────────────────────────

    def mouseDoubleClickEvent(self, event):
        if self._readonly:
            event.ignore()
            return
        if isinstance(self.tile, TagTile):
            self._edit_emphasis()
        else:
            self._edit_text()

    def _edit_emphasis(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("tile.edit_tag_title", name=self.tile.tag_name))
        dlg.setMinimumWidth(620)
        dlg.setStyleSheet(f"QDialog {{ background-color: {SURFACE0}; color: {TEXT}; }}")
        lay = QVBoxLayout(dlg)
        lay.setSpacing(6)

        action_ss = (
            f"QPushButton {{ background: {SURFACE1}; color: {TEXT}; "
            f"border: 1px solid {SURFACE2}; border-radius: 3px; padding: 3px 8px; }}"
            f"QPushButton:hover {{ border-color: #89b4fa; }}"
            f"QPushButton:disabled {{ color: #45475a; border-color: #313244; }}"
        )

        lay.addWidget(_dialog_label(tr("tile.tag_name_label")))
        name_row = QHBoxLayout()
        name_row.setSpacing(4)
        name_edit = QLineEdit(self.tile.tag_name)
        name_edit.setPlaceholderText("tag name")
        name_row.addWidget(name_edit, 1)
        btn_to_local = QPushButton(tr("tile.reverse_translate_btn"))
        btn_to_local.setToolTip(tr("tile.reverse_translate_tooltip"))
        btn_to_local.setFont(ui_font(-1))
        btn_to_local.setStyleSheet(action_ss)
        name_row.addWidget(btn_to_local)
        lay.addLayout(name_row)

        lay.addWidget(_dialog_label(tr("tile.display_name_label")))
        display_row = QHBoxLayout()
        display_row.setSpacing(4)
        display_edit = QLineEdit(self.tile.tag_local)
        display_edit.setPlaceholderText(tr("tile.display_name_placeholder"))
        display_row.addWidget(display_edit, 1)
        btn_to_tag = QPushButton("🏷️")
        btn_to_tag.setToolTip(tr("tile.translate_to_danboard_tooltip_inline"))
        btn_to_tag.setFixedWidth(34)
        btn_to_natural = QPushButton("💬")
        btn_to_natural.setToolTip(tr("tile.translate_to_natural_tooltip_inline"))
        btn_to_natural.setFixedWidth(34)
        for b in (btn_to_tag, btn_to_natural):
            b.setFont(ui_font(-1))
            b.setStyleSheet(action_ss)
            display_row.addWidget(b)
        lay.addLayout(display_row)

        lay.addWidget(_dialog_label(tr("tile.emphasis_label")))
        emp_row = QHBoxLayout()
        emp_edit = QLineEdit(f"{self.tile.emphasis:.4g}")
        emp_edit.setPlaceholderText(tr("tile.emphasis_placeholder"))
        emp_row.addWidget(emp_edit)

        _step_ss = (
            "QPushButton { background: transparent; color: #cdd6f4;"
            " border: 1px solid #45475a; border-radius: 3px; padding: 0; }"
            "QPushButton:hover { background: #45475a; }"
        )
        def _emp_step(delta: float) -> None:
            try:
                v = float(emp_edit.text())
            except ValueError:
                v = 1.0
            emp_edit.setText(f"{v + delta:.4g}")

        btn_up = QPushButton("▲")
        btn_up.setFixedSize(28, 26)
        btn_up.setFont(ui_font(0))
        btn_up.setToolTip(tr("tile.emphasis_up_tooltip"))
        btn_up.setStyleSheet(_step_ss)
        btn_up.clicked.connect(lambda: _emp_step(0.05))
        btn_dn = QPushButton("▼")
        btn_dn.setFixedSize(28, 26)
        btn_dn.setFont(ui_font(0))
        btn_dn.setToolTip(tr("tile.emphasis_down_tooltip"))
        btn_dn.setStyleSheet(_step_ss)
        btn_dn.clicked.connect(lambda: _emp_step(-0.05))
        emp_row.addWidget(btn_up)
        emp_row.addWidget(btn_dn)
        lay.addLayout(emp_row)

        btn_cancel_translate = QPushButton(tr("translate_panel.cancel_btn"))
        btn_cancel_translate.setFont(ui_font(-1))
        btn_cancel_translate.setStyleSheet(action_ss)
        btn_cancel_translate.setEnabled(False)

        translate_panel = QWidget()
        # translate_panel 自身はタイル親のカスケードで緑枠が乗ってしまい、
        # status_lbl / thinking_edit のそれぞれの枠と二重に見えるので、
        # ID セレクタで自身のみ透明＋枠なしに上書きする（子には影響しない）。
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
        # status_lbl は stretch なしで文字幅にフィットさせる。
        # stretch=1 にすると緑のカスケード枠が行幅全体に広がり、キャンセルボタンと
        # 視覚的に重なって見える不具合の原因になる。
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
        lay.addWidget(translate_panel)

        worker: _TileTranslateWorker | None = None
        apply_target = "tag"
        translate_buttons = [btn_to_tag, btn_to_natural, btn_to_local]

        def _set_translating(translating: bool) -> None:
            for b in translate_buttons:
                b.setEnabled(not translating)
            btn_cancel_translate.setEnabled(translating)
            btns.setEnabled(not translating)

        def _append_thinking(text: str) -> None:
            if not thinking_edit.isVisible():
                thinking_edit.show()
            cursor = thinking_edit.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            cursor.insertText(text)
            thinking_edit.setTextCursor(cursor)
            thinking_edit.ensureCursorVisible()

        def _start_translate(src: str, mode: str, reverse: bool, target: str) -> None:
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
            worker = _TileTranslateWorker(src, mode, reverse, dlg)
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
                if apply_target == "display":
                    display_edit.setText(text)
                else:
                    name_edit.setText(text)
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

        btn_to_tag.clicked.connect(
            lambda: _start_translate(display_edit.text(), "danboard", False, "tag")
        )
        btn_to_natural.clicked.connect(
            lambda: _start_translate(display_edit.text(), "natural", False, "tag")
        )
        btn_to_local.clicked.connect(
            lambda: _start_translate(name_edit.text(), "natural", True, "display")
        )
        btn_cancel_translate.clicked.connect(_cancel_translate)
        dlg.finished.connect(
            lambda *_: worker.cancel_and_wait()
            if worker is not None and worker.isRunning()
            else None
        )

        replacement_tile: AnyTile | None = None
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        _equalize_ok_cancel_buttons(btns)
        footer_row = QHBoxLayout()
        footer_row.setSpacing(6)
        convert_btn = QPushButton(tr("tile.convert_to_natural_btn"))
        convert_btn.setFont(ui_font(-1))
        convert_btn.setStyleSheet(action_ss)
        footer_row.addWidget(convert_btn)
        footer_row.addStretch()
        footer_row.addWidget(btns)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        lay.addLayout(footer_row)

        def _convert_to_natural() -> None:
            nonlocal replacement_tile
            tag_name = single_line_text(name_edit.text())
            display = single_line_text(display_edit.text())
            source = display or single_line_text(self.tile.source_text) or tag_name
            translated = tag_name or single_line_text(self.tile.translated_text)
            if not source and not translated:
                return
            try:
                emphasis = float(emp_edit.text().strip())
            except ValueError:
                emphasis = self.tile.emphasis
            replacement_tile = NaturalTextTile(
                text=translated or source,
                source_text=source,
                translated_text=translated,
                display_label=display,
                enabled=self.tile.enabled,
                strength_level=self.tile.strength_level,
                emphasis=emphasis,
            )
            dlg.accept()

        convert_btn.clicked.connect(_convert_to_natural)

        name_edit.setFocus()
        name_edit.selectAll()

        if dlg.exec() == QDialog.DialogCode.Accepted:
            if replacement_tile is not None:
                self.tile_replaced.emit(self, replacement_tile)
                return
            new_name = name_edit.text().strip()
            if new_name:
                self.tile.tag_name = single_line_text(new_name)
            self.tile.tag_local = single_line_text(display_edit.text())
            self.tile.source_text = self.tile.tag_local
            self.tile.translated_text = single_line_text(self.tile.tag_name)
            try:
                self.tile.emphasis = float(emp_edit.text().strip())
            except ValueError:
                pass

            if self.tile.tag_local:
                _library_db.execute(
                    "INSERT OR REPLACE INTO tag_labels (tag_name, label, updated_at)"
                    " VALUES (?, ?, CURRENT_TIMESTAMP)",
                    (self.tile.tag_name, self.tile.tag_local),
                )
            else:
                _library_db.execute(
                    "DELETE FROM tag_labels WHERE tag_name = ?",
                    (self.tile.tag_name,),
                )

            self._update_labels()
            self.tile_changed.emit()

    def _strength_plus(self) -> None:
        self.tile.strength_level += 1
        self._update_labels()
        self.tile_changed.emit()

    def _strength_minus(self) -> None:
        self.tile.strength_level -= 1
        self._update_labels()
        self.tile_changed.emit()

    def _edit_text(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("tile.edit_text_title"))
        dlg.setMinimumWidth(620)
        dlg.setStyleSheet(f"QDialog {{ background-color: {SURFACE0}; color: {TEXT}; }}")
        lay = QVBoxLayout(dlg)
        lay.setSpacing(6)

        action_ss = (
            f"QPushButton {{ background: {SURFACE1}; color: {TEXT}; "
            f"border: 1px solid {SURFACE2}; border-radius: 3px; padding: 3px 8px; }}"
            f"QPushButton:hover {{ border-color: #89b4fa; }}"
            f"QPushButton:disabled {{ color: #45475a; border-color: #313244; }}"
        )

        lay.addWidget(_dialog_label(tr("tile.display_name_label")))
        display_edit = QLineEdit(self.tile.display_label)
        display_edit.setPlaceholderText(tr("tile.natural_display_placeholder"))
        lay.addWidget(display_edit)

        source_label_row = QHBoxLayout()
        source_label_row.setSpacing(4)
        source_label_row.addWidget(_dialog_label(tr("tile.source_text_label")), 1)
        btn_translate_natural = QPushButton(tr("tile.natural_retranslate_btn"))
        btn_translate_natural.setFont(ui_font(-1))
        btn_translate_natural.setToolTip(tr("tile.natural_translate_tooltip"))
        btn_translate_natural.setStyleSheet(action_ss)
        source_label_row.addWidget(btn_translate_natural)
        lay.addLayout(source_label_row)
        source_row = QHBoxLayout()
        source_row.setSpacing(4)
        source_edit = QTextEdit(self.tile.source_text or self.tile.text)
        source_edit.setMinimumHeight(80)
        source_row.addWidget(source_edit, 1)
        lay.addLayout(source_row)

        translated_label_row = QHBoxLayout()
        translated_label_row.setSpacing(4)
        translated_label_row.addWidget(_dialog_label(tr("tile.translated_text_label")), 1)
        btn_reverse_natural = QPushButton(tr("tile.reverse_translate_btn"))
        btn_reverse_natural.setFont(ui_font(-1))
        btn_reverse_natural.setToolTip(tr("tile.natural_reverse_translate_tooltip"))
        btn_reverse_natural.setStyleSheet(action_ss)
        translated_label_row.addWidget(btn_reverse_natural)
        lay.addLayout(translated_label_row)
        translated_row = QHBoxLayout()
        translated_row.setSpacing(4)
        translated_edit = QTextEdit(self.tile.translated_text or self.tile.text)
        translated_edit.setMinimumHeight(80)
        translated_row.addWidget(translated_edit, 1)
        lay.addLayout(translated_row)

        btn_cancel_translate = QPushButton(tr("translate_panel.cancel_btn"))
        btn_cancel_translate.setFont(ui_font(-1))
        btn_cancel_translate.setStyleSheet(action_ss)
        btn_cancel_translate.setEnabled(False)

        translate_panel = QWidget()
        # translate_panel 自身はタイル親のカスケードで緑枠が乗ってしまい、
        # status_lbl / thinking_edit のそれぞれの枠と二重に見えるので、
        # ID セレクタで自身のみ透明＋枠なしに上書きする（子には影響しない）。
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
        # status_lbl は stretch なしで文字幅にフィットさせる。
        # stretch=1 にすると緑のカスケード枠が行幅全体に広がり、キャンセルボタンと
        # 視覚的に重なって見える不具合の原因になる。
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
        lay.addWidget(translate_panel)

        worker: _TileTranslateWorker | None = None
        apply_target = "translated"
        translate_buttons = [btn_translate_natural, btn_reverse_natural]

        def _set_translating(translating: bool) -> None:
            for b in translate_buttons:
                b.setEnabled(not translating)
            btn_cancel_translate.setEnabled(translating)
            btns.setEnabled(not translating)

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
                status_lbl.setText(tr("tile.translate_empty_source"))
                return
            apply_target = target
            translate_panel.show()
            thinking_edit.clear()
            thinking_edit.hide()
            status_lbl.setText(tr("translate_panel.status_translating"))
            status_lbl.setStyleSheet(f"color: {SUBTEXT};")
            _set_translating(True)
            worker = _TileTranslateWorker(src, "natural", reverse, dlg)
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
                source_before = source_edit.toPlainText()
                translated_before = translated_edit.toPlainText()
                if apply_target == "source":
                    if show_translation_compare_dialog(
                        dlg,
                        title=tr("tile.reverse_result_title"),
                        result_label=tr("tile.reverse_result_label"),
                        source_text=source_before,
                        translated_text=translated_before,
                        result_text=text,
                        apply_label=tr("tile.apply_reverse_to_source_btn"),
                    ):
                        source_edit.setPlainText(text)
                else:
                    if show_translation_compare_dialog(
                        dlg,
                        title=tr("tile.retranslate_result_title"),
                        result_label=tr("tile.retranslate_result_label"),
                        source_text=source_before,
                        translated_text=translated_before,
                        result_text=text,
                        apply_label=tr("tile.apply_retranslate_to_translated_btn"),
                    ):
                        translated_edit.setPlainText(text)
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

        replacement_tile: AnyTile | None = None
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        _equalize_ok_cancel_buttons(btns)
        footer_row = QHBoxLayout()
        footer_row.setSpacing(6)
        convert_btn = QPushButton(tr("tile.convert_to_tag_btn"))
        convert_btn.setFont(ui_font(-1))
        convert_btn.setStyleSheet(action_ss)
        footer_row.addWidget(convert_btn)
        footer_row.addStretch()
        footer_row.addWidget(btns)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        lay.addLayout(footer_row)

        def _convert_to_tag() -> None:
            nonlocal replacement_tile
            source = single_line_text(source_edit.toPlainText())
            translated = single_line_text(translated_edit.toPlainText())
            display = single_line_text(display_edit.text())
            tag_name = translated or source
            if not tag_name:
                return
            replacement_tile = TagTile(
                tag_name=tag_name,
                tag_local=display or (source if source != tag_name else ""),
                emphasis=self.tile.emphasis,
                strength_level=self.tile.strength_level,
                enabled=self.tile.enabled,
                source_text=source,
                translated_text=tag_name,
            )
            dlg.accept()

        convert_btn.clicked.connect(_convert_to_tag)

        btn_translate_natural.clicked.connect(
            lambda: _start_translate(source_edit.toPlainText(), False, "translated")
        )
        btn_reverse_natural.clicked.connect(
            lambda: _start_translate(translated_edit.toPlainText(), True, "source")
        )
        btn_cancel_translate.clicked.connect(_cancel_translate)
        dlg.finished.connect(
            lambda *_: worker.cancel_and_wait()
            if worker is not None and worker.isRunning()
            else None
        )

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        if replacement_tile is not None:
            self.tile_replaced.emit(self, replacement_tile)
            return

        source     = single_line_text(source_edit.toPlainText())
        translated = single_line_text(translated_edit.toPlainText())
        display    = single_line_text(display_edit.text())
        if not source and not translated:
            return

        self.tile.source_text     = source
        self.tile.translated_text = translated
        self.tile.display_label   = display
        self.tile.text            = translated or source
        self._update_labels()
        self.tile_changed.emit()

    # ── ドラッグ ────────────────────────────────────────

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.pos()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_start = None
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            super().mouseMoveEvent(event)
            return
        if self._drag_start is None:
            super().mouseMoveEvent(event)
            return
        if (event.pos() - self._drag_start).manhattanLength() < QApplication.startDragDistance():
            super().mouseMoveEvent(event)
            return
        self._begin_drag()

    def _begin_drag(self) -> None:
        import ui.tile_drag as tile_drag
        tile_drag.set_drag(self)

        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(TILE_MIME, b"1")
        drag.setMimeData(mime)
        from ui.drag_pixmap import translucent_drag_pixmap
        drag.setPixmap(translucent_drag_pixmap(self.grab()))
        if self._drag_start is not None:
            drag.setHotSpot(self._drag_start)

        # readonly（履歴タイルクローン等）からのドラッグはコピー（カーソル表示も合わせる）
        drag.exec(
            Qt.DropAction.CopyAction if self._readonly else Qt.DropAction.MoveAction
        )

        tile_drag.clear_drag()
        self._drag_start = None

    def _toggle_enabled(self) -> None:
        if self._readonly:
            return
        if hasattr(self.tile, "enabled"):
            self.tile.enabled = not self.tile.enabled
            self._apply_style()
            self.tile_changed.emit()

    def _apply_readonly_state(self) -> None:
        if not self._readonly:
            return
        for btn_name in ("_toggle_btn", "_del_btn", "_plus_btn", "_minus_btn"):
            btn = getattr(self, btn_name, None)
            if btn is not None:
                btn.hide()

    # ── 外部 API ────────────────────────────────────────

    def is_over_label(self, pos_in_tile: "QPoint") -> bool:
        """
        ボタン列の右端以降をラベル領域と判定する。
        row1 の最右端ボタンの座標から境界を計算する。
        """
        # 左ボタン列と右移動ボタン列の間をラベル領域と判定する。
        if isinstance(self.tile, (TagTile, NaturalTextTile)) and not self._strength_in_row2:
            ref = self._minus_btn
        else:
            ref = self._del_btn
        left = ref.x() + ref.width() + self.layout().spacing()
        right = self.width() - self.layout().contentsMargins().right()
        return (left <= pos_in_tile.x() < right
                and 0 <= pos_in_tile.y() < self.height())

    def refresh(self) -> None:
        self._update_labels()
        self._apply_style()
