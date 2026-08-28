"""Portable, one-image PromptMosaic prompt packages.

The package intentionally contains editor/generation data only.  It never
contains local paths, Invoke endpoint details, database ids, or image bytes.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import copy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.import_security import (
    DEFAULT_TEXT_IMPORT_MAX_BYTES,
    ImportSecurityError,
    read_text_import_file,
    validate_text_json,
)


PACK_SUFFIX = ".promptmosaic-pack"
PACK_FORMAT = "PromptMosaic image prompt"
PACK_VERSION = 1
MAX_PACK_ITEMS = 20_000
MAX_PACK_DEPTH = 32
MAX_PACK_STRING_CHARS = 100_000


class PromptPackError(ValueError):
    """Raised when a PromptMosaic prompt package is invalid or unsupported."""


def portable_document(document: dict) -> dict:
    """Return a copy without installation-specific LoRA keys."""
    result = copy.deepcopy(document)

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if "lora_source_key" in value:
                value["lora_source_key"] = ""
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(result)
    return result


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _digest(body: dict) -> str:
    return hashlib.sha256(_canonical_bytes(body)).hexdigest()


def build_prompt_pack(payload: dict, *, app_version: str) -> dict:
    if not isinstance(payload, dict):
        raise PromptPackError("payload must be an object")
    validate_text_json(
        payload,
        max_depth=MAX_PACK_DEPTH,
        max_items=MAX_PACK_ITEMS,
        max_string_chars=MAX_PACK_STRING_CHARS,
    )
    body = {
        "format": PACK_FORMAT,
        "format_version": PACK_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "app_version": str(app_version or ""),
        "payload": payload,
    }
    return {**body, "sha256": _digest(body)}


def write_prompt_pack(path: str | Path, payload: dict, *, app_version: str) -> Path:
    target = Path(path)
    if target.suffix.lower() != PACK_SUFFIX:
        target = target.with_name(target.name + PACK_SUFFIX)
    pack = build_prompt_pack(payload, app_version=app_version)
    text = json.dumps(pack, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if len(text.encode("utf-8")) > DEFAULT_TEXT_IMPORT_MAX_BYTES:
        raise PromptPackError("package exceeds the 5 MB size limit")
    target.write_text(text, encoding="utf-8")
    return target


def read_prompt_pack(path: str | Path) -> dict:
    try:
        text = read_text_import_file(
            path,
            allowed_suffixes=(PACK_SUFFIX,),
            max_bytes=DEFAULT_TEXT_IMPORT_MAX_BYTES,
            max_line_chars=MAX_PACK_STRING_CHARS,
            max_lines=50_000,
        )
        data = json.loads(text)
        validate_text_json(
            data,
            max_depth=MAX_PACK_DEPTH,
            max_items=MAX_PACK_ITEMS,
            max_string_chars=MAX_PACK_STRING_CHARS,
        )
    except (OSError, json.JSONDecodeError, ImportSecurityError) as exc:
        raise PromptPackError(str(exc)) from exc

    if not isinstance(data, dict):
        raise PromptPackError("package root must be an object")
    expected_root_keys = {
        "format", "format_version", "created_at", "app_version", "payload", "sha256",
    }
    if set(data) != expected_root_keys:
        raise PromptPackError("package contains unsupported root fields")
    if data.get("format") != PACK_FORMAT:
        raise PromptPackError("not a PromptMosaic image prompt package")
    if data.get("format_version") != PACK_VERSION:
        raise PromptPackError(
            f"unsupported package version: {data.get('format_version')!r}"
        )
    supplied_hash = data.get("sha256")
    if not isinstance(supplied_hash, str) or len(supplied_hash) != 64:
        raise PromptPackError("package hash is missing or invalid")
    body = {key: value for key, value in data.items() if key != "sha256"}
    if not hmac.compare_digest(supplied_hash.lower(), _digest(body)):
        raise PromptPackError("package hash does not match")
    payload = data.get("payload")
    if not isinstance(payload, dict):
        raise PromptPackError("package payload is missing")
    if set(payload) != {"unit", "document", "memo", "generation"}:
        raise PromptPackError("package contains unsupported payload fields")
    if payload.get("unit") != "single_image":
        raise PromptPackError("package is not a single-image unit")
    if not isinstance(payload.get("document"), dict):
        raise PromptPackError("package document is missing")
    if not isinstance(payload.get("memo"), str):
        raise PromptPackError("package memo is invalid")
    if not isinstance(payload.get("generation"), dict):
        raise PromptPackError("package generation settings are invalid")
    return payload
