"""Resolve generation image files without changing the history DB schema."""
from __future__ import annotations

from pathlib import Path

import db.env_db as _env_db


_INVOKE_ROOT_KEYS = (
    "invoke_images_dir",
    "invoke_outputs_images_dir",
    "invoke_outputs_dir",
)


def resolve_generation_image_path(
    local_path: str | None = None,
    image_name: str | None = None,
    image_subfolder: str | None = None,
) -> Path | None:
    """Resolve a generation image from an existing path or Invoke image name.

    ``local_path`` may point to a PromptMosaic copy or to an external image.
    ``image_name`` is searched under configured Invoke output roots. When
    Invoke's image DTO provides ``image_subfolder``, that structured folder is
    tried first. This resolver intentionally avoids recursive filesystem
    search; history image display must not block the UI by scanning the Invoke
    tree.
    """
    path = _existing_path(local_path)
    if path is not None:
        return path

    image_name = (image_name or "").strip()
    if not image_name:
        return None
    return _find_in_invoke_roots(image_name, image_subfolder=image_subfolder)


def _existing_path(value: str | None) -> Path | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        path = Path(value)
        if path.exists() and path.is_file():
            return path
    except Exception:
        return None
    return None


def invoke_image_roots() -> list[Path]:
    """Return configured Invoke image roots.

    These are optional env_settings keys so public DBs do not need a schema
    migration. ``invoke_outputs_dir`` may point to Invoke's install/root
    directory, its ``outputs`` directory, or ``outputs/images`` directly.
    """
    roots: list[Path] = []
    for key in _INVOKE_ROOT_KEYS:
        try:
            row = _env_db.fetchone("SELECT value FROM env_settings WHERE key=?", (key,))
        except Exception:
            row = None
        raw = str(row["value"] or "").strip() if row else ""
        if not raw:
            continue
        path = Path(raw)
        candidates = [path]
        if key == "invoke_outputs_dir":
            candidates = [path / "images", path / "outputs" / "images", path / "outputs", path]
        for candidate in candidates:
            try:
                resolved = candidate.resolve()
            except Exception:
                resolved = candidate
            if resolved.exists() and resolved.is_dir() and resolved not in roots:
                roots.append(resolved)
    return roots


def _find_in_invoke_roots(image_name: str, image_subfolder: str | None = None) -> Path | None:
    image_subfolder = str(image_subfolder or "").strip().strip("/\\")
    relative_name = Path(image_name)
    relative_with_subfolder = Path(image_subfolder) / image_name if image_subfolder else relative_name

    for root in invoke_image_roots():
        for direct in (root / relative_with_subfolder, root / relative_name):
            if direct.exists() and direct.is_file():
                return direct
    return None
