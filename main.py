"""
PromptMosaic エントリーポイント

起動コマンド:
    python main.py

起動順序:
  1. DB 初期化
  2. 設定読み込み（言語 / テーマ / フォントサイズ）
  3. i18n 言語設定
  4. スタイル / テーマ設定（UI モジュールより前に実施）
  5. PySide6 / UI モジュールのインポート
  6. QApplication 作成 → パレット適用 → フォント設定
"""
import sys


def _set_windows_app_id() -> None:
    """Windows taskbar icon grouping hint for python-launched apps."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "PromptMosaic.PromptMosaic"
        )
    except Exception:
        pass


def main() -> None:
    _set_windows_app_id()

    # ── 1. DB 初期化 ────────────────────────────────────
    from db.init import initialize_all
    initialize_all()

    # ── 2. 設定読み込み ──────────────────────────────────
    import db.app_db as _app_db

    lang      = _app_db.get_setting("language",  "ja")
    theme     = _app_db.get_setting("theme",     "dark")
    font_size = _app_db.get_setting("font_size", "10")

    # ── 3. i18n ──────────────────────────────────────────
    from core.i18n import set_language
    set_language(lang)

    # ── 4. テーマ設定（UI モジュール import より先に実行） ──
    import ui.styles as styles
    styles.configure_theme(theme)
    styles.load_categories_from_db()

    # ── 5. PySide6 / UI モジュール ─────────────────────────
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QTimer
    from core.app_icon import apply_app_icon
    from ui.main_window import MainWindow

    # ── 6. アプリ起動 ────────────────────────────────────
    app = QApplication(sys.argv)

    try:
        pt = int(font_size)
    except (ValueError, TypeError):
        pt = 10
    styles.apply_palette(app, font_pt=pt)
    # 有効な多サイズ .ico をアプリ全体へ一度設定すれば、Qt が各ウィンドウの
    # ネイティブアイコン(WM_SETICON)を実体化時に設定し、タスクバーにも反映される。
    # （MainWindow.__init__ 内でもウィンドウへ適用される）
    apply_app_icon(None)

    window = MainWindow()
    # 起動時に白いウィンドウがちらつくのを避けるため、透明(opacity 0)で表示し、
    # レイアウト/テーマ確定後にフェードインする。
    window.setWindowOpacity(0.0)
    window.winId()
    apply_app_icon(window)
    window.show()

    def _reveal() -> None:
        # 不透明(opacity 1.0)に戻すと Qt が層化(WS_EX_LAYERED)を解除する。
        # 透明な層化ウィンドウのまま show() するとタスクバーボタンがアイコンを
        # 取りこぼし汎用アイコンになるため、不透明化＝層化解除の直後にアイコンを
        # 1度だけ再確定する（リトライループではなく、可視化のこの1点だけ）。
        window.setWindowOpacity(1.0)
        apply_app_icon(window)

    QTimer.singleShot(120, _reveal)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
