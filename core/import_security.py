from __future__ import annotations

import json
import math
import re
import unicodedata
from pathlib import Path
from typing import Any

from core.i18n import tr


DEFAULT_TEXT_IMPORT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_BACKUP_JSON_MAX_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_DEPTH = 24
DEFAULT_MAX_ITEMS = 100_000
DEFAULT_MAX_STRING_CHARS = 100_000
MAX_IMPORT_RECORDS = 5_000
MAX_IMPORT_LINE_CHARS = 8_192
MAX_IMPORT_LINES = 10_000

TAG_NAME_MAX_CHARS = 200
ONE_LINE_MEMO_MAX_CHARS = 300
MEMO_MAX_CHARS = 3_000
MANUFACTURER_MAX_CHARS = 100
PROMPT_TEXT_MAX_CHARS = 8_192
SIGNATURE_MAX_CHARS = 300

_ALLOWED_TEXT_CONTROLS = {"\t", "\n", "\r"}
_BASE64ISH_RE = re.compile(r"^[A-Za-z0-9+/=\s]+$")
_TAG_NAME_FORBIDDEN_CHARS = set('<>"`')
_FORBIDDEN_CODEPOINTS = {
    0x202A, 0x202B, 0x202C, 0x202D, 0x202E,
    0x2066, 0x2067, 0x2068, 0x2069,
    0x200B, 0x200C, 0x200D,
    0xFEFF,
}


class ImportSecurityError(ValueError):
    """Raised when an import file fails pre-parse safety checks."""


def read_text_import_file(
    path: str | Path,
    *,
    allowed_suffixes: tuple[str, ...],
    max_bytes: int = DEFAULT_TEXT_IMPORT_MAX_BYTES,
    max_line_chars: int = MAX_IMPORT_LINE_CHARS,
    max_lines: int = MAX_IMPORT_LINES,
) -> str:
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix not in allowed_suffixes:
        allowed = ", ".join(allowed_suffixes)
        raise ImportSecurityError(tr("import_security.unsupported_file_type", allowed=allowed))

    size = p.stat().st_size
    if size > max_bytes:
        mb = max_bytes / (1024 * 1024)
        raise ImportSecurityError(tr("import_security.file_too_large", mb=f"{mb:.0f}"))

    raw = p.read_bytes()
    if b"\x00" in raw:
        raise ImportSecurityError(tr("import_security.binary_detected"))

    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ImportSecurityError(tr("import_security.invalid_utf8")) from exc

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    _validate_plain_text(text, allow_tab=True)
    lines = text.split("\n")
    if len(lines) > max_lines:
        raise ImportSecurityError(tr("import_security.too_many_lines", count=max_lines))
    for line in lines:
        if len(line) > max_line_chars:
            raise ImportSecurityError(tr("import_security.line_too_long", chars=max_line_chars))
    return text


def load_json_import_file(
    path: str | Path,
    *,
    max_bytes: int = DEFAULT_TEXT_IMPORT_MAX_BYTES,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_items: int = DEFAULT_MAX_ITEMS,
    max_string_chars: int = DEFAULT_MAX_STRING_CHARS,
) -> Any:
    text = read_text_import_file(path, allowed_suffixes=(".json",), max_bytes=max_bytes)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ImportSecurityError(tr("import_security.invalid_json")) from exc
    validate_text_json(
        data,
        max_depth=max_depth,
        max_items=max_items,
        max_string_chars=max_string_chars,
    )
    return data


def validate_text_json(
    data: Any,
    *,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_items: int = DEFAULT_MAX_ITEMS,
    max_string_chars: int = DEFAULT_MAX_STRING_CHARS,
) -> None:
    seen = 0

    def walk(value: Any, depth: int) -> None:
        nonlocal seen
        seen += 1
        if seen > max_items:
            raise ImportSecurityError(tr("import_security.too_many_items", count=max_items))
        if depth > max_depth:
            raise ImportSecurityError(tr("import_security.too_deep", depth=max_depth))

        if isinstance(value, dict):
            for key, child in value.items():
                if not isinstance(key, str):
                    raise ImportSecurityError(tr("import_security.invalid_json_key"))
                _validate_string(key, max_string_chars)
                walk(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                walk(child, depth + 1)
        elif isinstance(value, str):
            _validate_string(value, max_string_chars)
        elif value is None or isinstance(value, bool):
            return
        elif isinstance(value, int):
            return
        elif isinstance(value, float):
            if not math.isfinite(value):
                raise ImportSecurityError(tr("import_security.invalid_json_value"))
            return
        else:
            raise ImportSecurityError(tr("import_security.invalid_json_value"))

    walk(data, 0)


def sanitize_text(
    value: Any,
    *,
    max_len: int,
    allow_newline: bool = False,
    normalization: str = "NFC",
    field_name: str | None = None,
    strict_tag_chars: bool = False,
) -> str:
    text = "" if value is None else str(value)
    text = unicodedata.normalize(normalization, text)

    result: list[str] = []
    for ch in text:
        cp = ord(ch)
        cat = unicodedata.category(ch)

        if cp in _FORBIDDEN_CODEPOINTS:
            raise ImportSecurityError(
                tr("import_security.forbidden_unicode", code=f"U+{cp:04X}")
            )
        if cat in {"Cs", "Co", "Cn"}:
            raise ImportSecurityError(
                tr("import_security.unsupported_unicode", code=f"U+{cp:04X}")
            )
        if cat in {"Cf"}:
            raise ImportSecurityError(
                tr("import_security.forbidden_unicode", code=f"U+{cp:04X}")
            )
        if cat == "Cc":
            if ch == "\n" and allow_newline:
                result.append(ch)
                continue
            if ch == "\t":
                result.append(" ")
                continue
            raise ImportSecurityError(
                tr("import_security.control_character_code", code=f"U+{cp:04X}")
            )
        if strict_tag_chars and ch in _TAG_NAME_FORBIDDEN_CHARS:
            raise ImportSecurityError(
                tr("import_security.forbidden_field_character", field=field_name or "")
            )
        result.append(ch)

    cleaned = "".join(result).strip()
    if len(cleaned) > max_len:
        raise ImportSecurityError(tr("import_security.string_too_long", chars=max_len))
    return cleaned


def sanitize_tag_name(value: Any, *, field_name: str | None = None) -> str:
    return sanitize_text(
        value,
        max_len=TAG_NAME_MAX_CHARS,
        allow_newline=False,
        normalization="NFKC",
        field_name=field_name,
        strict_tag_chars=True,
    )


def sanitize_text_json(
    data: Any,
    *,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_items: int = DEFAULT_MAX_ITEMS,
    max_string_chars: int = PROMPT_TEXT_MAX_CHARS,
) -> Any:
    seen = 0

    def walk(value: Any, depth: int) -> Any:
        nonlocal seen
        seen += 1
        if seen > max_items:
            raise ImportSecurityError(tr("import_security.too_many_items", count=max_items))
        if depth > max_depth:
            raise ImportSecurityError(tr("import_security.too_deep", depth=max_depth))

        if isinstance(value, dict):
            cleaned: dict[str, Any] = {}
            for key, child in value.items():
                if not isinstance(key, str):
                    raise ImportSecurityError(tr("import_security.invalid_json_key"))
                clean_key = sanitize_text(key, max_len=MAX_IMPORT_LINE_CHARS, allow_newline=False)
                cleaned[clean_key] = walk(child, depth + 1)
            return cleaned
        if isinstance(value, list):
            return [walk(child, depth + 1) for child in value]
        if isinstance(value, str):
            return sanitize_text(value, max_len=max_string_chars, allow_newline=True)
        if value is None or isinstance(value, bool):
            return value
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            if not math.isfinite(value):
                raise ImportSecurityError(tr("import_security.invalid_json_value"))
            return value
        raise ImportSecurityError(tr("import_security.invalid_json_value"))

    return walk(data, 0)


def _validate_string(value: str, max_string_chars: int) -> None:
    if len(value) > max_string_chars:
        raise ImportSecurityError(tr("import_security.string_too_long", chars=max_string_chars))
    _validate_plain_text(value, allow_tab=True)
    compact = "".join(value.split())
    if (
        len(compact) > 8192
        and len(compact) == len(value)
        and len(compact) % 4 == 0
        and _BASE64ISH_RE.fullmatch(compact)
    ):
        raise ImportSecurityError(tr("import_security.binary_like_text"))


def _validate_plain_text(value: str, *, allow_tab: bool) -> None:
    for ch in value:
        cp = ord(ch)
        cat = unicodedata.category(ch)
        if cp in _FORBIDDEN_CODEPOINTS or cat in {"Cf", "Cs", "Co", "Cn"}:
            raise ImportSecurityError(
                tr("import_security.forbidden_unicode", code=f"U+{cp:04X}")
            )
        if cat == "Cc":
            if ch in {"\n", "\r"} or (allow_tab and ch == "\t"):
                continue
            raise ImportSecurityError(
                tr("import_security.control_character_code", code=f"U+{cp:04X}")
            )
