"""PromptMosaic application icon helpers."""
from __future__ import annotations

from pathlib import Path

import db.app_db as database

_DEFAULT_ICON = Path(__file__).parent.parent / "assets" / "prompt_mosaic.ico"


def resolve_app_icon_path() -> Path | None:
    """Return the configured app icon, or the bundled PromptMosaic icon."""
    row = database.fetchone(
        "SELECT value FROM app_settings WHERE key = 'app_icon_path'"
    )
    configured = (row["value"] if row else "").strip()
    if configured:
        path = Path(configured)
        if path.exists():
            return path
    if _DEFAULT_ICON.exists():
        return _DEFAULT_ICON
    return None


def load_app_icon():
    """Build a QIcon from the configured/bundled icon, or return None."""
    path = resolve_app_icon_path()
    if path is None:
        return None
    from PySide6.QtGui import QIcon

    icon = QIcon(str(path))
    return None if icon.isNull() else icon


def apply_app_icon(widget=None) -> None:
    """Apply the app icon to the QApplication and (optionally) a window.

    The bundled ``prompt_mosaic.ico`` ships proper square frames
    (16/24/32/48/64/128/256). With a valid multi-size .ico, Qt sets the native
    Windows ``WM_SETICON`` itself when the window handle is realized, so the
    taskbar button shows the icon. No manual Win32 / LoadImageW calls are
    needed — those were a workaround for the previously malformed icon file.
    """
    icon = load_app_icon()
    if icon is None:
        return
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is not None:
        app.setWindowIcon(icon)
    if widget is not None:
        widget.setWindowIcon(icon)
