"""Named multi-model generation plans stored in environment.db."""
from __future__ import annotations

from typing import Any

import db.env_db as _env_db


def ensure_default_plan() -> int:
    row = _env_db.fetchone("SELECT id FROM generation_plans ORDER BY id LIMIT 1")
    if row:
        return int(row["id"])
    cur = _env_db.execute("INSERT INTO generation_plans(name) VALUES (?)", ("Default",))
    return int(cur.lastrowid)


def list_plans() -> list[dict[str, Any]]:
    ensure_default_plan()
    rows = _env_db.fetchall(
        """SELECT p.id, p.name, p.updated_at,
                  COUNT(r.id) AS row_count,
                  COALESCE(SUM(CASE WHEN r.enabled THEN r.image_count ELSE 0 END), 0) AS image_count
           FROM generation_plans p
           LEFT JOIN generation_plan_rows r ON r.plan_id = p.id
           GROUP BY p.id
           ORDER BY p.name COLLATE NOCASE"""
    )
    return [dict(r) for r in rows]


def get_plan(plan_id: int, *, validate: bool = True) -> dict[str, Any] | None:
    plan = _env_db.fetchone(
        "SELECT id, name, created_at, updated_at FROM generation_plans WHERE id=?",
        (plan_id,),
    )
    if not plan:
        return None
    rows = []
    changed = False
    for row in _env_db.fetchall(
        """SELECT id, plan_id, sort_order, enabled, model_key, model_name, model_base,
                  image_count, steps, cfg_scale, scheduler, extra_positive, extra_negative
           FROM generation_plan_rows
           WHERE plan_id=?
           ORDER BY sort_order, id""",
        (plan_id,),
    ):
        info = dict(row)
        model = _env_db.fetchone(
            "SELECT invoke_key, name, base, available FROM models WHERE invoke_key=? AND type='main'",
            (info["model_key"],),
        )
        info["model_missing"] = not model or not int(model["available"] or 0)
        if model:
            info["model_name"] = model["name"] or info["model_name"] or info["model_key"]
            info["model_base"] = model["base"] or info["model_base"] or ""
        if validate and info["model_missing"] and info["enabled"]:
            _env_db.execute("UPDATE generation_plan_rows SET enabled=0 WHERE id=?", (info["id"],))
            info["enabled"] = 0
            changed = True

        loras = []
        for lora_row in _env_db.fetchall(
            """SELECT id, row_id, sort_order, enabled, lora_key, name, base, weight
               FROM generation_plan_loras
               WHERE row_id=?
               ORDER BY sort_order, id""",
            (info["id"],),
        ):
            lora = dict(lora_row)
            found = _env_db.fetchone(
                "SELECT invoke_key, name, base, invoke_hash, available FROM models WHERE invoke_key=? AND type='lora'",
                (lora["lora_key"],),
            )
            lora["missing"] = not found or not int(found["available"] or 0)
            if found:
                lora["name"] = found["name"] or lora["name"] or lora["lora_key"]
                lora["base"] = found["base"] or lora["base"] or ""
                lora["hash"] = found["invoke_hash"] or ""
            if validate and lora["missing"] and lora["enabled"]:
                _env_db.execute("UPDATE generation_plan_loras SET enabled=0 WHERE id=?", (lora["id"],))
                lora["enabled"] = 0
                changed = True
            loras.append(lora)
        info["loras"] = loras
        rows.append(info)
    if changed:
        _env_db.execute(
            "UPDATE generation_plans SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (plan_id,)
        )
    result = dict(plan)
    result["rows"] = rows
    return result


def create_plan(name: str) -> int:
    cur = _env_db.execute("INSERT INTO generation_plans(name) VALUES (?)", (name.strip(),))
    return int(cur.lastrowid)


def rename_plan(plan_id: int, name: str) -> None:
    _env_db.execute(
        "UPDATE generation_plans SET name=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (name.strip(), plan_id),
    )


def delete_plan(plan_id: int) -> None:
    _env_db.execute("DELETE FROM generation_plans WHERE id=?", (plan_id,))


def duplicate_name_exists(name: str, exclude_id: int | None = None) -> bool:
    if exclude_id is None:
        row = _env_db.fetchone("SELECT 1 FROM generation_plans WHERE name=?", (name.strip(),))
    else:
        row = _env_db.fetchone(
            "SELECT 1 FROM generation_plans WHERE name=? AND id!=?",
            (name.strip(), exclude_id),
        )
    return row is not None


def save_rows(plan_id: int, rows: list[dict[str, Any]]) -> None:
    with _env_db.transaction() as conn:
        conn.execute("DELETE FROM generation_plan_rows WHERE plan_id=?", (plan_id,))
        for idx, row in enumerate(rows):
            cur = conn.execute(
                """INSERT INTO generation_plan_rows
                   (plan_id, sort_order, enabled, model_key, model_name, model_base,
                    image_count, steps, cfg_scale, scheduler, extra_positive, extra_negative)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    plan_id, idx, 1 if row.get("enabled", True) else 0,
                    row.get("model_key", ""), row.get("model_name", ""), row.get("model_base", ""),
                    int(row.get("image_count") or 1),
                    int(row.get("steps") or 30),
                    float(row.get("cfg_scale") or 7.0),
                    row.get("scheduler") or "euler",
                    row.get("extra_positive") or "",
                    row.get("extra_negative") or "",
                ),
            )
            row_id = int(cur.lastrowid)
            for lidx, lora in enumerate(row.get("loras") or []):
                conn.execute(
                    """INSERT INTO generation_plan_loras
                       (row_id, sort_order, enabled, lora_key, name, base, weight)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        row_id, lidx, 1 if lora.get("enabled", True) else 0,
                        lora.get("lora_key") or lora.get("invoke_key") or "",
                        lora.get("name", ""), lora.get("base", ""),
                        float(lora.get("weight") or 0.75),
                    ),
                )
        conn.execute(
            "UPDATE generation_plans SET updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (plan_id,),
        )
