"""
External inbox import helpers.

External tools write prompt candidates into external_inbox. PromptMosaic only
checks pending rows and imports them into the normal history tree when the user
confirms. Cancelled rows remain pending so they can be offered again later.
"""
from __future__ import annotations

import datetime
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import db.env_db as _env_db
import db.history_db as _history_db
from core.prompt_builder import NaturalTextTile, PromptDocument, TagTile
from core.version import APP_VERSION


@dataclass(frozen=True)
class ImportResult:
    imported: int = 0
    failed: int = 0


def pending_rows() -> list:
    return _history_db.fetchall(
        """
        SELECT *
        FROM external_inbox
        WHERE status = 'pending'
        ORDER BY created_at ASC, id ASC
        """
    )


def pending_count() -> int:
    row = _history_db.fetchone(
        "SELECT COUNT(*) AS cnt FROM external_inbox WHERE status = 'pending'"
    )
    return int(row["cnt"] or 0) if row else 0


def preview_text(rows: list, limit: int = 8) -> str:
    lines: list[str] = []
    for row in rows[:limit]:
        payload = _payload(row)
        group_path = _group_path(row, payload)
        path = " > ".join(group_path)
        title = _review_title(row, payload, group_path)
        lines.append(f"- {path} / {title}")
    if len(rows) > limit:
        lines.append(f"- ... and {len(rows) - limit} more")
    return "\n".join(lines)


def import_pending() -> ImportResult:
    rows = pending_rows()
    imported = 0
    failed = 0
    for row in rows:
        try:
            gen_id = import_row(row)
            _history_db.execute(
                """
                UPDATE external_inbox
                SET status='imported',
                    imported_generation_id=?,
                    imported_at=CURRENT_TIMESTAMP,
                    error_message=NULL
                WHERE id=?
                """,
                (gen_id, row["id"]),
            )
            imported += 1
        except Exception as exc:
            _history_db.execute(
                """
                UPDATE external_inbox
                SET status='error', error_message=?
                WHERE id=?
                """,
                (str(exc), row["id"]),
            )
            failed += 1
    return ImportResult(imported=imported, failed=failed)


def import_row(row) -> int:
    payload = _payload(row)
    history_name = (row["history_name"] or payload.get("history_name") or "").strip()
    if not history_name:
        raise ValueError("history_name is required")

    group_path = _group_path(row, payload)
    if not group_path:
        raise ValueError("at least one history group is required")

    model_key = payload.get("model_key") or payload.get("invoke_key") or payload.get("model_id") or None
    model_name = payload.get("model_name") or None
    model_base = payload.get("model_base") or payload.get("base") or None
    model_hash = payload.get("model_hash") or None
    if model_key and not model_name:
        mrow = _env_db.fetchone(
            "SELECT name, base, invoke_hash FROM models WHERE invoke_key=?",
            (model_key,),
        )
        if mrow:
            model_name = mrow["name"]
            model_base = model_base or mrow["base"]
            model_hash = model_hash or mrow["invoke_hash"]

    with _history_db.transaction() as conn:
        group_id = _ensure_group_path(conn, group_path, row["save_folder_path"] or payload.get("save_folder_path"))
        work_id = _ensure_work(conn, _work_name(row, payload))
        page_id = _ensure_page(conn, work_id, _page_number(row, payload)) if work_id else None

        doc = _document_from_payload(payload)
        positive_prompt = payload.get("positive_prompt") or payload.get("prompt") or doc.compile_positive()
        negative_prompt = payload.get("negative_prompt") or doc.compile_negative()

        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur = conn.execute(
            """
            INSERT INTO generations
                (sent_positive_prompt, sent_negative_prompt, structured_prompt,
                 structured_negative, created_at, group_id, page_id, cut_number,
                 scene_description, dialogue, seed, steps, cfg_scale, scheduler,
                 width, height, loras_json, invoke_key, model_name, model_base,
                 model_hash, generation_mode, app_version, image_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                positive_prompt,
                negative_prompt,
                doc.to_json(),
                None,
                now,
                group_id,
                page_id,
                _cut_number(row, payload),
                payload.get("scene_description") or payload.get("scene") or "",
                payload.get("dialogue") or "",
                _int_or_none(payload.get("seed")),
                _int_or_none(payload.get("steps")),
                _float_or_none(payload.get("cfg_scale") or payload.get("cfg")),
                payload.get("scheduler") or None,
                _int_or_none(payload.get("width")),
                _int_or_none(payload.get("height")),
                _json_or_none(payload.get("loras") or payload.get("lora_list") or payload.get("loras_json")),
                model_key,
                model_name,
                model_base,
                model_hash,
                payload.get("generation_mode") or "externaltool_import",
                APP_VERSION,
                _int_or_none(payload.get("image_count")) or 1,
            ),
        )
        gen_id = cur.lastrowid
        doc.save_to_db(gen_id)

        title = _review_title(row, payload, group_path)
        memo = _review_memo(row, payload, group_path)
        conn.execute("INSERT OR IGNORE INTO image_reviews (generation_id) VALUES (?)", (gen_id,))
        conn.execute(
            """
            UPDATE image_reviews
            SET title=?, review_text=?, updated_at=CURRENT_TIMESTAMP
            WHERE generation_id=?
            """,
            (title, memo, gen_id),
        )

    return int(gen_id)


def _payload(row) -> dict[str, Any]:
    try:
        data = json.loads(row["payload_json"] or "{}")
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _group_path(row, payload: dict[str, Any]) -> list[str]:
    raw = row["group_path_json"] or payload.get("group_path_json") or payload.get("group_path")
    if raw:
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                raw = [p.strip() for p in raw.split(">")]
        if isinstance(raw, list):
            parts = [str(p).strip() for p in raw if str(p).strip()]
            if parts:
                return parts

    parts = [row["history_name"] or payload.get("history_name")]
    page = row["page_name"] or payload.get("page_name")
    if not page and row["page_number"] is not None:
        page = f"Page {row['page_number']}"
    if not page and payload.get("page_number") is not None:
        page = f"Page {payload.get('page_number')}"
    cut = row["cut_name"] or payload.get("cut_name")
    if not cut and row["cut_number"] is not None:
        cut = f"Cut {row['cut_number']}"
    if not cut and payload.get("cut_number") is not None:
        cut = f"Cut {payload.get('cut_number')}"
    parts.extend([page, cut])
    return [str(p).strip() for p in parts if str(p or "").strip()]


def _ensure_group_path(conn, parts: list[str], save_folder_path: str | None) -> int:
    parent_id: int | None = None
    group_id: int | None = None
    for name in parts:
        row = conn.execute(
            """
            SELECT id
            FROM generation_groups
            WHERE name=? AND parent_id IS ?
            """,
            (name, parent_id),
        ).fetchone()
        if row:
            group_id = int(row["id"])
        else:
            cur = conn.execute(
                "INSERT INTO generation_groups (name, parent_id, folder_path) VALUES (?, ?, ?)",
                (
                    name,
                    parent_id,
                    str(_default_group_folder(name, parent_id)),
                ),
            )
            group_id = int(cur.lastrowid)
        parent_id = group_id

    if group_id is None:
        raise ValueError("empty group path")
    if save_folder_path:
        path = str(Path(save_folder_path))
        conn.execute(
            "UPDATE generation_groups SET folder_path=? WHERE id=?",
            (path, group_id),
        )
    return group_id


def _default_group_folder(name: str, parent_id: int | None) -> Path:
    from core import local_storage

    return local_storage.default_group_folder(name, parent_id)


def _ensure_work(conn, name: str | None) -> int | None:
    if not name:
        return None
    row = conn.execute("SELECT id FROM works WHERE name=?", (name,)).fetchone()
    if row:
        return int(row["id"])
    cur = conn.execute("INSERT INTO works (name) VALUES (?)", (name,))
    return int(cur.lastrowid)


def _ensure_page(conn, work_id: int | None, page_number: int | None) -> int | None:
    if work_id is None or page_number is None:
        return None
    row = conn.execute(
        "SELECT id FROM pages WHERE work_id=? AND page_number=?",
        (work_id, page_number),
    ).fetchone()
    if row:
        return int(row["id"])
    cur = conn.execute(
        "INSERT INTO pages (work_id, page_number) VALUES (?, ?)",
        (work_id, page_number),
    )
    return int(cur.lastrowid)


def _document_from_payload(payload: dict[str, Any]) -> PromptDocument:
    raw_doc = payload.get("structured_prompt") or payload.get("prompt_document")
    if raw_doc:
        try:
            if isinstance(raw_doc, dict):
                return PromptDocument.from_dict(raw_doc)
            return PromptDocument.from_json(str(raw_doc))
        except Exception:
            pass

    doc = PromptDocument()
    positive = payload.get("positive_prompt") or payload.get("prompt") or ""
    negative = payload.get("negative_prompt") or ""
    doc.positive.middle.tiles = _tiles_from_prompt(str(positive))
    doc.negative.middle.tiles = _tiles_from_prompt(str(negative))
    return doc


def _tiles_from_prompt(text: str) -> list:
    tiles: list = []
    for raw in text.split(","):
        tag = raw.strip()
        if not tag:
            continue
        emphasis = 1.0
        if ":" in tag:
            tag_part, _, num_part = tag.rpartition(":")
            try:
                emphasis = float(num_part)
                tag = tag_part.strip()
            except ValueError:
                pass
        if "\n" in tag or len(tag) > 80:
            tiles.append(NaturalTextTile(text=tag))
        else:
            tiles.append(TagTile(tag_name=tag, emphasis=emphasis))
    return tiles


def _work_name(row, payload: dict[str, Any]) -> str | None:
    return (
        payload.get("work_name")
        or payload.get("story_name")
        or payload.get("project_name")
        or row["history_name"]
    )


def _page_number(row, payload: dict[str, Any]) -> int | None:
    return _int_or_none(row["page_number"]) or _int_or_none(payload.get("page_number"))


def _cut_number(row, payload: dict[str, Any]) -> int | None:
    return _int_or_none(row["cut_number"]) or _int_or_none(payload.get("cut_number"))


def _review_memo(row, payload: dict[str, Any], group_path: list[str]) -> str:
    notes = payload.get("notes") or payload.get("memo") or ""
    source = row["source_item_id"] or payload.get("external_tool_id") or ""
    details = [
        "Imported from external inbox.",
        f"Path: {' > '.join(group_path)}",
    ]
    if source:
        details.append(f"Source ID: {source}")
    if notes:
        details.append("")
        details.append(str(notes))
    return "\n".join(details)


def _review_title(row, payload: dict[str, Any], group_path: list[str]) -> str:
    """
    Review title shown in the PromptMosaic history list.

    An external tool can set it explicitly with external_inbox.title or
    payload.review_title. If the history hierarchy changes, the tool can set
    payload.title_path_index to pick a group_path element; negative indexes are
    supported, so -1 means the last level.
    """
    for key in (
        "review_title",
        "history_item_title",
        "panel_title",
        "generation_title",
        "title",
    ):
        value = payload.get(key)
        if str(value or "").strip():
            return str(value).strip()
    if str(row["title"] or "").strip():
        return str(row["title"]).strip()

    idx = _int_or_none(payload.get("title_path_index"))
    if idx is not None and group_path:
        try:
            return group_path[idx]
        except IndexError:
            pass

    for value in (
        row["cut_name"],
        payload.get("cut_name"),
        payload.get("cut_label"),
        group_path[-1] if group_path else None,
    ):
        if str(value or "").strip():
            return str(value).strip()

    cut_no = _cut_number(row, payload)
    if cut_no is not None:
        return f"Cut {cut_no}"
    return f"ExternalTool #{row['id']}"


def _json_or_none(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
