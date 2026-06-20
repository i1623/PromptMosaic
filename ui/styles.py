"""
テーマ・カラー定義
Catppuccin Mocha（ダーク）/ Catppuccin Latte（ライト）をサポート。

configure_theme(theme) でモジュールグローバルを書き換え、
apply_palette(app) で現在のグローバルを QPalette + スタイルシートに適用する。
"""
from pathlib import Path

from PySide6.QtGui import QPalette, QColor, QFont
from PySide6.QtWidgets import QApplication


# ─────────────────────────────────────────
# ベースカラー（グローバル変数 — configure_theme() が書き換える）
# ─────────────────────────────────────────
BASE       = "#1e1e2e"
SURFACE0   = "#313244"
SURFACE1   = "#45475a"
SURFACE2   = "#585b70"
TEXT       = "#cdd6f4"
SUBTEXT    = "#a6adc8"
ACCENT     = "#89b4fa"
GREEN      = "#a6e3a1"
RED        = "#f38ba8"
YELLOW     = "#f9e2af"
OVERLAY    = "#6c7086"
MANTLE     = "#181825"
# 履歴の文字色 / 履歴マップ系統線の色（テーマ別の既定。設定で上書き可）。
# 設定キー: history_text_color_<theme> / history_line_color_<theme>。
# 未設定ならテーマ素の TEXT / SUBTEXT。さらに履歴ツリー単位の右クリック上書きが最優先。
HISTORY_TEXT = TEXT
HISTORY_LINE = SUBTEXT
COMBO_ARROW_URL = (Path(__file__).resolve().parents[1] / "assets" / "combo_arrow_down.svg").as_posix()

# ─────────────────────────────────────────
# カテゴリラベル（キー → 表示名）
# ─────────────────────────────────────────
CATEGORY_LABELS: dict[str, str] = {
    "all":         "すべて",
    "character_identity":           "キャラクター・人物",
    "human_expression":             "人物表現",
    "pose_action_interaction":      "ポーズ・動作",
    "clothing_accessory":           "服装・装飾",
    "living_creature":              "生物",
    "object_artifact":              "物品・人工物",
    "architecture_structure":       "建築・構造物",
    "location_background":          "場所・背景",
    "natural_feature":              "自然物",
    "phenomenon_event":             "現象・イベント",
    "era_culture_worldview":        "時代・文化",
    "art_style_medium":             "画風・媒体",
    "lighting_color_screen_effect": "光・色・画面効果",
    "quality_correction":           "品質・補正",
    "mixed_unsorted":               "99",
    "object":      "物体",
    "state":       "状態",
    "quality":     "品質",
    "style":       "スタイル",
    "composition": "構図",
    "lighting":    "照明",
    "action":      "動作",
    "scene":       "情景",
}

# ─────────────────────────────────────────
# 8カテゴリ カラー (背景色, テキスト色)
# ─────────────────────────────────────────
CATEGORY_COLORS: dict[str, tuple[str, str]] = {
    "character_identity":           ("#2d1f4e", "#cba6f7"),
    "human_expression":             ("#3d1b2e", "#f38ba8"),
    "pose_action_interaction":      ("#1b2d4e", "#89b4fa"),
    "clothing_accessory":           ("#3d2a1b", "#fab387"),
    "living_creature":              ("#1b3d1b", "#a6e3a1"),
    "object_artifact":              ("#2a2a3a", "#a6adc8"),
    "architecture_structure":       ("#1b3333", "#89dceb"),
    "location_background":          ("#383818", "#f9e2af"),
    "natural_feature":              ("#1b3822", "#b6f27c"),
    "phenomenon_event":             ("#3d2810", "#fab387"),
    "era_culture_worldview":        ("#3d1b3d", "#f5c2e7"),
    "art_style_medium":             ("#1e1e3d", "#b4befe"),
    "lighting_color_screen_effect": ("#3d1010", "#f38ba8"),
    "quality_correction":           ("#0f3a1a", "#a6e3a1"),
    "mixed_unsorted":               ("#252535", "#6c7086"),
    "object":      ("#1a3a5c", "#89b4fa"),
    "state":       ("#3a3010", "#f9e2af"),
    "quality":     ("#0f3a1a", "#a6e3a1"),
    "style":       ("#2e1a3a", "#cba6f7"),
    "composition": ("#3a2000", "#fab387"),
    "lighting":    ("#3a1010", "#f38ba8"),
    "action":      ("#2a1a0a", "#e8c9a0"),
    "scene":       ("#3a1025", "#f5c2e7"),
    "":            ("#2a2a3a", "#a6adc8"),
}
CATEGORY_COLORS_LIGHT: dict[str, tuple[str, str]] = {}
CATEGORY_COLORS_DARK: dict[str, tuple[str, str]] = {}

# ブロック位置ごとのヘッダー色
BLOCK_HEADER_COLORS: dict[str, str] = {
    "top":    "#1a3a2a",
    "middle": "#2a2a3a",
    "bottom": "#2a1a3a",
}

# ブロックラベル（テーマ非依存）
BLOCK_LABELS: dict[str, str] = {
    "top":    "先頭ブロック（固定）",
    "middle": "中間ブロック",
    "bottom": "末尾ブロック（固定）",
}


# ─────────────────────────────────────────
# テーマ設定
# ─────────────────────────────────────────

def configure_theme(theme: str) -> None:
    """
    モジュールグローバルのカラー定数を指定テーマで上書きする。

    theme: "dark"  → Catppuccin Mocha（デフォルト）
           "light" → Catppuccin Latte

    必ず UI モジュールを import する前に呼ぶこと。
    """
    global BASE, SURFACE0, SURFACE1, SURFACE2, TEXT, SUBTEXT, ACCENT
    global GREEN, RED, YELLOW, OVERLAY, MANTLE
    global CATEGORY_COLORS, BLOCK_HEADER_COLORS

    if theme == "light":
        # ── Catppuccin Latte ──────────────────────────
        BASE     = "#eff1f5"
        SURFACE0 = "#e6e9ef"
        SURFACE1 = "#ccd0da"
        SURFACE2 = "#acb0be"
        TEXT     = "#4c4f69"
        SUBTEXT  = "#6c6f85"
        ACCENT   = "#1e66f5"
        GREEN    = "#40a02b"
        RED      = "#d20f39"
        YELLOW   = "#df8e1d"
        OVERLAY  = "#8c8fa1"
        MANTLE   = "#dce0e8"

        CATEGORY_COLORS = {
            "character_identity":           ("#e5d8f5", "#8839ef"),
            "human_expression":             ("#f5d8e0", "#d20f39"),
            "pose_action_interaction":      ("#d8e4f5", "#1e66f5"),
            "clothing_accessory":           ("#f5e8d8", "#fe640b"),
            "living_creature":              ("#d8f5d8", "#40a02b"),
            "object_artifact":              ("#e8e8f0", "#6c6f85"),
            "architecture_structure":       ("#d8f5f5", "#04a5e5"),
            "location_background":          ("#f5f0d8", "#df8e1d"),
            "natural_feature":              ("#d8f5e0", "#40a02b"),
            "phenomenon_event":             ("#f5ead8", "#fe640b"),
            "era_culture_worldview":        ("#f5d8f0", "#ea76cb"),
            "art_style_medium":             ("#e8d8f5", "#7287fd"),
            "lighting_color_screen_effect": ("#f5d8d8", "#d20f39"),
            "quality_correction":           ("#d8f5d8", "#40a02b"),
            "mixed_unsorted":               ("#e8e8e8", "#9ca0b0"),
            "object":      ("#c5d8f6", "#1e66f5"),
            "state":       ("#f5edcc", "#df8e1d"),
            "quality":     ("#c8e8c6", "#40a02b"),
            "style":       ("#e0d0f0", "#8839ef"),
            "composition": ("#f5dcc5", "#fe640b"),
            "lighting":    ("#f5ccd0", "#d20f39"),
            "action":      ("#c0eff7", "#00838f"),
            "scene":       ("#f5cce8", "#ea76cb"),
            "":            ("#dce0e8", "#6c6f85"),
        }

        BLOCK_HEADER_COLORS = {
            "top":    "#d0e8d8",
            "middle": "#e6e9ef",
            "bottom": "#e0d0f0",
        }

    else:
        # ── Catppuccin Mocha（ダーク・デフォルト） ────
        BASE     = "#1e1e2e"
        SURFACE0 = "#313244"
        SURFACE1 = "#45475a"
        SURFACE2 = "#585b70"
        TEXT     = "#cdd6f4"
        SUBTEXT  = "#a6adc8"
        ACCENT   = "#89b4fa"
        GREEN    = "#a6e3a1"
        RED      = "#f38ba8"
        YELLOW   = "#f9e2af"
        OVERLAY  = "#6c7086"
        MANTLE   = "#181825"

        CATEGORY_COLORS = {
            "character_identity":           ("#2d1f4e", "#cba6f7"),
            "human_expression":             ("#3d1b2e", "#f38ba8"),
            "pose_action_interaction":      ("#1b2d4e", "#89b4fa"),
            "clothing_accessory":           ("#3d2a1b", "#fab387"),
            "living_creature":              ("#1b3d1b", "#a6e3a1"),
            "object_artifact":              ("#2a2a3a", "#a6adc8"),
            "architecture_structure":       ("#1b3333", "#89dceb"),
            "location_background":          ("#383818", "#f9e2af"),
            "natural_feature":              ("#1b3822", "#b6f27c"),
            "phenomenon_event":             ("#3d2810", "#fab387"),
            "era_culture_worldview":        ("#3d1b3d", "#f5c2e7"),
            "art_style_medium":             ("#1e1e3d", "#b4befe"),
            "lighting_color_screen_effect": ("#3d1010", "#f38ba8"),
            "quality_correction":           ("#0f3a1a", "#a6e3a1"),
            "mixed_unsorted":               ("#252535", "#6c7086"),
            "object":      ("#1a3a5c", "#89b4fa"),
            "state":       ("#3a3010", "#f9e2af"),
            "quality":     ("#0f3a1a", "#a6e3a1"),
            "style":       ("#2e1a3a", "#cba6f7"),
            "composition": ("#3a2000", "#fab387"),
            "lighting":    ("#3a1010", "#f38ba8"),
            "action":      ("#0a2a2e", "#89dceb"),
            "scene":       ("#3a1025", "#f5c2e7"),
            "":            ("#2a2a3a", "#a6adc8"),
        }

        BLOCK_HEADER_COLORS = {
            "top":    "#1a3a2a",
            "middle": "#2a2a3a",
            "bottom": "#2a1a3a",
        }

    # 履歴文字色 / 系統線色のテーマ別既定を設定から読み込む（未設定はテーマ素の色）。
    reload_history_colors()


def _read_app_color(key: str, default: str) -> str:
    """app_settings の色文字列を返す。未設定/DB未初期化なら default。"""
    try:
        import db.app_db as _app_db
        row = _app_db.fetchone("SELECT value FROM app_settings WHERE key=?", (key,))
        if row and row["value"]:
            return str(row["value"])
    except Exception:
        pass
    return default


def reload_history_colors() -> None:
    """設定 history_text_color_<theme> / history_line_color_<theme> から
    HISTORY_TEXT / HISTORY_LINE を更新する。テーマ切替時・設定変更後に呼ぶ。"""
    global HISTORY_TEXT, HISTORY_LINE
    theme = "light" if is_light_theme() else "dark"
    HISTORY_TEXT = _read_app_color(f"history_text_color_{theme}", TEXT)
    HISTORY_LINE = _read_app_color(f"history_line_color_{theme}", SUBTEXT)


def history_default_text_color(theme: str | None = None) -> str:
    """指定テーマ(省略時は現在)の履歴文字色の『テーマ素の既定』(=TEXT)を返す。
    設定UIの『既定に戻す』用。"""
    if theme is None:
        theme = "light" if is_light_theme() else "dark"
    return "#4c4f69" if theme == "light" else "#cdd6f4"


def history_default_line_color(theme: str | None = None) -> str:
    """指定テーマの系統線色の『テーマ素の既定』(=SUBTEXT)を返す。"""
    if theme is None:
        theme = "light" if is_light_theme() else "dark"
    return "#6c6f85" if theme == "light" else "#a6adc8"


_TAG_BROWSER_COLORS_LIGHT: dict[str, tuple[str, str]] = {
    "character_identity":           ("#e5d8f5", "#8839ef"),
    "human_expression":             ("#f5d8e0", "#d20f39"),
    "pose_action_interaction":      ("#d8e4f5", "#1e66f5"),
    "clothing_accessory":           ("#f5e8d8", "#fe640b"),
    "living_creature":              ("#d8f5d8", "#40a02b"),
    "object_artifact":              ("#e8e8f0", "#6c6f85"),
    "architecture_structure":       ("#d8f5f5", "#04a5e5"),
    "location_background":          ("#f5f0d8", "#df8e1d"),
    "natural_feature":              ("#d8f5e0", "#40a02b"),
    "phenomenon_event":             ("#f5ead8", "#fe640b"),
    "era_culture_worldview":        ("#f5d8f0", "#ea76cb"),
    "art_style_medium":             ("#e8d8f5", "#7287fd"),
    "lighting_color_screen_effect": ("#f5d8d8", "#d20f39"),
    "quality_correction":           ("#d8f5d8", "#40a02b"),
    "mixed_unsorted":               ("#e8e8e8", "#9ca0b0"),
}

_TAG_BROWSER_COLORS_DARK: dict[str, tuple[str, str]] = {
    "character_identity":           ("#2d1f4e", "#cba6f7"),
    "human_expression":             ("#3d1b2e", "#f38ba8"),
    "pose_action_interaction":      ("#1b2d4e", "#89b4fa"),
    "clothing_accessory":           ("#3d2a1b", "#fab387"),
    "living_creature":              ("#1b3d1b", "#a6e3a1"),
    "object_artifact":              ("#2a2a3a", "#a6adc8"),
    "architecture_structure":       ("#1b3333", "#89dceb"),
    "location_background":          ("#383818", "#f9e2af"),
    "natural_feature":              ("#1b3822", "#b6f27c"),
    "phenomenon_event":             ("#3d2810", "#fab387"),
    "era_culture_worldview":        ("#3d1b3d", "#f5c2e7"),
    "art_style_medium":             ("#1e1e3d", "#b4befe"),
    "lighting_color_screen_effect": ("#3d1010", "#f38ba8"),
    "quality_correction":           ("#0f3a1a", "#a6e3a1"),
    "mixed_unsorted":               ("#252535", "#6c7086"),
}


def tag_browser_chip_colors(category: str) -> tuple[str, str, str]:
    """タグブラウザのタグチップと同じ (background, foreground, border) を返す。"""
    bg, fg = tag_browser_base_colors(category)
    is_light = is_light_theme()
    bg_draw = QColor(bg).darker(104).name() if is_light else QColor(bg).lighter(130).name()
    fg_draw = QColor(fg).darker(150).name() if is_light else fg
    border = QColor(fg_draw).darker(112 if is_light else 120).name()
    return bg_draw, fg_draw, border


def tag_browser_base_colors(category: str) -> tuple[str, str]:
    """タグブラウザのジャンル基準色 (background, foreground) を返す。"""
    is_light = QColor(BASE).lightness() > 128
    table = _TAG_BROWSER_COLORS_LIGHT if is_light else _TAG_BROWSER_COLORS_DARK
    fallback = ("#e8e8e8", "#9ca0b0") if is_light else ("#2a2a3a", "#a6adc8")
    theme_colors = CATEGORY_COLORS_LIGHT if is_light else CATEGORY_COLORS_DARK
    if category in theme_colors:
        return theme_colors[category]
    if category in CATEGORY_COLORS:
        stored = CATEGORY_COLORS.get(category, fallback)
        if is_light and stored == _TAG_BROWSER_COLORS_DARK.get(category):
            return table.get(category, fallback)
        if is_light and category == "" and stored == ("#2a2a3a", "#a6adc8"):
            return fallback
        return stored
    return table.get(category, fallback)


def tag_browser_default_base_colors(category: str) -> tuple[str, str]:
    """現在テーマにおけるタグブラウザ既定ジャンル色を返す。"""
    is_light = QColor(BASE).lightness() > 128
    return tag_browser_default_base_colors_for_theme(category, "light" if is_light else "dark")


def tag_browser_default_base_colors_for_theme(category: str, theme: str) -> tuple[str, str]:
    """指定テーマにおけるタグブラウザ既定ジャンル色を返す。"""
    is_light = theme == "light"
    table = _TAG_BROWSER_COLORS_LIGHT if is_light else _TAG_BROWSER_COLORS_DARK
    fallback = ("#e8e8e8", "#9ca0b0") if is_light else ("#2a2a3a", "#a6adc8")
    bg, fg = table.get(category, fallback)
    return bg, fg


def get_tile_style(category: str, selected: bool = False) -> str:
    """タイルの背景色・テキスト色をスタイルシートで返す（現在のグローバルを参照）"""
    bg, fg, chip_border = tag_browser_chip_colors(category)
    border = ACCENT if selected else chip_border
    return (
        f"background-color: {bg};"
        f"color: {fg};"
        f"border: 1px solid {border};"
        f"border-radius: 4px;"
        f"padding: 2px 6px;"
    )


def is_light_theme() -> bool:
    return QColor(BASE).lightness() > 128


def themed_button_style(kind: str = "normal", *, bold: bool = False) -> str:
    """現在テーマに合わせた小型ボタン用スタイル。kind: normal/accent/success/danger/translate/add."""
    if kind == "translate":
        fg = TEXT
        bg = MANTLE if not is_light_theme() else "#f7f8fb"
        hover = SURFACE0 if not is_light_theme() else "#eef0f6"
        border = OVERLAY
        border_css = "1px solid"
        extra = ""
    elif kind == "add":
        fg = BASE if not is_light_theme() else "#ffffff"
        bg = TEXT if not is_light_theme() else "#4c4f69"
        hover = "#ffffff" if not is_light_theme() else "#5c5f77"
        border = TEXT if not is_light_theme() else "#4c4f69"
        border_css = "2px solid"
        extra = "font-weight: bold;"
    elif kind == "accent":
        fg = ACCENT
        bg = "#d8e4f5" if is_light_theme() else "#1a2a3a"
        hover = "#c5d8f6" if is_light_theme() else "#2a4a6a"
        border = ACCENT
        border_css = "1px solid"
        extra = ""
    elif kind == "success":
        fg = GREEN
        bg = "#d8f5d8" if is_light_theme() else "#1a3a1a"
        hover = "#c8e8c6" if is_light_theme() else "#2a5a2a"
        border = GREEN
        border_css = "1px solid"
        extra = ""
    elif kind == "danger":
        fg = RED
        bg = "#f5d8d8" if is_light_theme() else "#3a1a1a"
        hover = "#f5ccd0" if is_light_theme() else "#5a2a2a"
        border = RED
        border_css = "1px solid"
        extra = ""
    else:
        fg = TEXT
        bg = SURFACE1
        hover = SURFACE2
        border = SURFACE2
        border_css = "1px solid"
        extra = ""
    weight = "font-weight: bold;" if bold else extra
    pressed_fg = BASE
    pressed_bg = fg if kind in {"accent", "translate", "success", "add", "danger"} else ACCENT
    return (
        f"QPushButton, QToolButton {{ background-color: {bg}; color: {fg}; "
        f"border: {border_css} {border}; border-radius: 4px; padding: 4px 12px; {weight} }}"
        f"QPushButton:hover, QToolButton:hover {{ background-color: {hover}; }}"
        f"QPushButton:pressed, QToolButton:pressed {{ background-color: {pressed_bg}; color: {pressed_fg}; }}"
    )


def apply_palette(app: QApplication, font_pt: int = 10) -> None:
    """
    現在のグローバルカラーを QPalette + スタイルシートとしてアプリ全体に適用する。
    configure_theme() を呼んだ後に実行すること。
    f-string はこの関数が呼ばれた時点で評価される（モジュールロード時ではない）。
    """
    app.setStyle("Fusion")
    # Fusion が内部フォントをリセットすることがあるため、
    # スタイルシート適用前にベースフォントを確定させる
    app.setFont(QFont("Segoe UI", font_pt))
    p = QPalette()

    def c(hex_: str) -> QColor:
        return QColor(hex_)

    p.setColor(QPalette.ColorRole.Window,          c(BASE))
    p.setColor(QPalette.ColorRole.WindowText,      c(TEXT))
    p.setColor(QPalette.ColorRole.Base,            c(SURFACE0))
    p.setColor(QPalette.ColorRole.AlternateBase,   c(SURFACE1))
    p.setColor(QPalette.ColorRole.ToolTipBase,     c(SURFACE1))
    p.setColor(QPalette.ColorRole.ToolTipText,     c(TEXT))
    p.setColor(QPalette.ColorRole.Text,            c(TEXT))
    p.setColor(QPalette.ColorRole.Button,          c(SURFACE1))
    p.setColor(QPalette.ColorRole.ButtonText,      c(TEXT))
    p.setColor(QPalette.ColorRole.BrightText,      c("#ffffff"))
    p.setColor(QPalette.ColorRole.Highlight,       c(ACCENT))
    p.setColor(QPalette.ColorRole.HighlightedText, c(BASE))
    p.setColor(QPalette.ColorRole.Link,            c(ACCENT))
    p.setColor(QPalette.ColorRole.Mid,             c(SURFACE2))
    p.setColor(QPalette.ColorRole.Dark,            c(SURFACE0))
    p.setColor(QPalette.ColorRole.Shadow,          c(MANTLE))

    # 無効状態
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text,       c(OVERLAY))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, c(OVERLAY))

    app.setPalette(p)

    # 追加スタイルシート（パレットで表現しにくい部分）
    # f-string は呼び出し時点のグローバルを参照する
    # font-size を明示することで setStyle("Fusion") によるリセットを防ぐ
    _fpt = font_pt
    app.setStyleSheet(f"""
        QWidget {{
            font-family: "Segoe UI";
            font-size: {_fpt}pt;
        }}
        QToolBar {{
            background-color: {SURFACE0};
            border-bottom: 1px solid {SURFACE2};
            spacing: 4px;
            padding: 2px 4px;
        }}
        QToolBar QToolButton {{
            background-color: {SURFACE1};
            color: {TEXT};
            border: 1px solid {SURFACE2};
            border-radius: 4px;
            padding: 3px 6px;
            min-width: 0px;
        }}
        QToolBar QToolButton:hover {{
            background-color: {SURFACE2};
        }}
        QToolBar QToolButton:pressed {{
            background-color: {ACCENT};
            color: {BASE};
        }}
        QSplitter::handle {{
            background-color: {SURFACE2};
            width: 3px;
        }}
        QSplitter::handle:hover {{
            background-color: {ACCENT};
        }}
        QScrollBar:vertical {{
            background: {SURFACE0};
            width: 8px;
            margin: 0;
        }}
        QScrollBar::handle:vertical {{
            background: {SURFACE2};
            border-radius: 4px;
            min-height: 20px;
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0;
        }}
        QScrollBar:horizontal {{
            background: {SURFACE0};
            height: 8px;
        }}
        QScrollBar::handle:horizontal {{
            background: {SURFACE2};
            border-radius: 4px;
            min-width: 20px;
        }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
            width: 0;
        }}
        QGroupBox {{
            border: 1px solid {SURFACE2};
            border-radius: 6px;
            margin-top: 8px;
            padding-top: 4px;
            font-weight: bold;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            left: 8px;
            padding: 0 4px;
            color: {SUBTEXT};
        }}
        QLineEdit {{
            background-color: {SURFACE0};
            color: {TEXT};
            border: 1px solid {SURFACE2};
            border-radius: 4px;
            padding: 3px 6px;
        }}
        QLineEdit:focus {{
            border-color: {ACCENT};
        }}
        QTextEdit {{
            background-color: {SURFACE0};
            color: {TEXT};
            border: 1px solid {SURFACE2};
            border-radius: 4px;
        }}
        QCheckBox {{
            color: {SUBTEXT};
            spacing: 4px;
        }}
        QCheckBox::indicator {{
            width: 14px;
            height: 14px;
            border: 1px solid {SURFACE2};
            border-radius: 3px;
            background-color: {SURFACE0};
        }}
        QCheckBox::indicator:checked {{
            background-color: {ACCENT};
            border-color: {ACCENT};
        }}
        QPushButton {{
            background-color: {SURFACE1};
            color: {TEXT};
            border: 1px solid {SURFACE2};
            border-radius: 4px;
            padding: 4px 12px;
        }}
        QPushButton:hover {{
            background-color: {SURFACE2};
        }}
        QPushButton:pressed {{
            background-color: {ACCENT};
            color: {BASE};
        }}
        QTabWidget::pane {{
            border: 1px solid {SURFACE2};
            background-color: {SURFACE0};
        }}
        QTabBar::tab {{
            background-color: {SURFACE1};
            color: {SUBTEXT};
            padding: 6px 14px;
            border: 1px solid {SURFACE2};
            border-bottom: none;
            border-radius: 4px 4px 0 0;
        }}
        QTabBar::tab:selected {{
            background-color: {SURFACE0};
            color: {TEXT};
        }}
        QLabel {{
            color: {TEXT};
            background: transparent;
            border: none;
            padding: 0;
        }}
        QLabel:disabled {{
            color: {SUBTEXT};
        }}
        QStatusBar {{
            background-color: {SURFACE0};
            color: {SUBTEXT};
            border-top: 1px solid {SURFACE2};
        }}
        QComboBox {{
            background-color: {SURFACE1};
            color: {TEXT};
            border: 1px solid {SURFACE2};
            border-radius: 4px;
            padding: 3px 26px 3px 6px;
            min-height: 20px;
        }}
        QComboBox::drop-down {{
            subcontrol-origin: padding;
            subcontrol-position: top right;
            width: 22px;
            border: none;
            background: transparent;
        }}
        QComboBox::down-arrow {{
            image: url("{COMBO_ARROW_URL}");
            width: 10px;
            height: 10px;
        }}
        QComboBox:focus {{
            border-color: {ACCENT};
        }}
        QComboBox QAbstractItemView {{
            background-color: {SURFACE0};
            color: {TEXT};
            border: 1px solid {SURFACE2};
            selection-background-color: {ACCENT};
            selection-color: {BASE};
        }}
        QSpinBox, QDoubleSpinBox {{
            background-color: {SURFACE1};
            color: {TEXT};
            padding: 2px 4px;
            min-height: 20px;
            qproperty-alignment: AlignRight;
        }}
        QMenu {{
            background-color: {SURFACE1};
            color: {TEXT};
            border: 1px solid {SURFACE2};
            border-radius: 4px;
            padding: 4px 0;
        }}
        QMenu::item {{
            padding: 6px 24px 6px 12px;
            border-radius: 3px;
            margin: 1px 4px;
        }}
        QMenu::item:selected {{
            background-color: {ACCENT};
            color: {BASE};
        }}
        QMenu::item:disabled {{
            color: {SUBTEXT};
        }}
        QMenu::separator {{
            height: 1px;
            background: {SURFACE2};
            margin: 4px 8px;
        }}
        QToolTip {{
            background-color: {SURFACE1};
            color: {TEXT};
            border: 1px solid {SURFACE2};
            border-radius: 4px;
            padding: 4px 6px;
        }}
    """)
    # スタイルシート適用後も setFont で app.font() を正しく保つ
    # （ui_font() が app.font().pointSize() を参照するため）
    app.setFont(QFont("Segoe UI", font_pt))


def ui_font(delta: int = 0, bold: bool = False, italic: bool = False) -> QFont:
    """
    アプリのベースフォントサイズを基準とした QFont を返す。

    Args:
        delta:  ベースサイズへの加算値（負も可）
        bold:   太字にするか
        italic: イタリックにするか
    """
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    base_size = app.font().pointSize() if app else 10
    f = QFont("Segoe UI", max(7, base_size + delta))
    if bold:
        f.setWeight(QFont.Weight.Bold)
    if italic:
        f.setItalic(True)
    return f


# ─────────────────────────────────────────
# 絵文字アイコンの統一サイズ
# （アプリのフォント設定に追従させず、常に 12pt 固定で表示する）
# ─────────────────────────────────────────
EMOJI_ICON_PT = 12
EMOJI_ICON_SS = f"font-size: {EMOJI_ICON_PT}pt;"


def emoji_icon_font() -> QFont:
    """絵文字アイコン用の固定 12pt フォント（フォント設定の影響を受けない）。"""
    return QFont("Segoe UI", EMOJI_ICON_PT)


# 後方互換エイリアス
apply_dark_palette = apply_palette


def load_categories_from_db() -> None:
    """
    DB の tag_categories テーブルから CATEGORY_LABELS / CATEGORY_COLORS を更新する。
    アプリ起動時およびカテゴリ変更後に呼ぶ。DB が未初期化なら何もしない。
    """
    global CATEGORY_LABELS, CATEGORY_COLORS, CATEGORY_COLORS_LIGHT, CATEGORY_COLORS_DARK
    try:
        import db.library_db as _lib_db
        rows = _lib_db.fetchall(
            "SELECT key, label, bg_color, fg_color, bg_color_light, fg_color_light, bg_color_dark, fg_color_dark "
            "FROM tag_categories ORDER BY sort_order, key"
        )
    except Exception:
        return
    if not rows:
        return
    new_labels: dict[str, str] = {"all": "すべて"}
    new_colors: dict[str, tuple[str, str]] = {
        "": ("#e8e8e8", "#9ca0b0") if is_light_theme() else ("#2a2a3a", "#a6adc8")
    }
    new_light_colors: dict[str, tuple[str, str]] = {}
    new_dark_colors: dict[str, tuple[str, str]] = {}
    for r in rows:
        key = r["key"]
        new_labels[key] = r["label"]
        default_light = tag_browser_default_base_colors_for_theme(key, "light")
        default_dark = tag_browser_default_base_colors_for_theme(key, "dark")
        legacy = (r["bg_color"], r["fg_color"])
        try:
            light_fallback = default_light if legacy == default_dark else legacy
            light = (r["bg_color_light"] or light_fallback[0], r["fg_color_light"] or light_fallback[1])
            dark = (r["bg_color_dark"] or r["bg_color"] or default_dark[0], r["fg_color_dark"] or r["fg_color"] or default_dark[1])
        except IndexError:
            light = default_light if legacy == default_dark else legacy
            dark = legacy
        new_light_colors[key] = light
        new_dark_colors[key] = dark
        new_colors[key] = light if is_light_theme() else dark
    CATEGORY_LABELS = new_labels
    CATEGORY_COLORS = new_colors
    CATEGORY_COLORS_LIGHT = new_light_colors
    CATEGORY_COLORS_DARK = new_dark_colors


# ─────────────────────────────────────────
# モジュールロード時に dark テーマで初期化する
# （main.py が configure_theme() を呼び直すまでのデフォルト）
# ─────────────────────────────────────────
configure_theme("dark")
