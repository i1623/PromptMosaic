"""
send_queue.db wrapper — 生成ユニット送信の一時バッファ。

1レコード = 1ユニット = generate_batch() 呼び出し1回分（1モデル×枚数）。
送信して item_ids を履歴行へ書き込んだらレコードを削除する。
不変条件: 正常起動時・正常終了時にレコードは存在しない
（残っていれば前回セッションの異常終了 → 起動時に送信済み item を
キャンセルして全クリアする。自動再開はしない）。
"""
from __future__ import annotations

import json
import sqlite3

from db.connections import get_send_queue_conn


def fetchone(sql: str, params: tuple = ()) -> sqlite3.Row | None:
    return get_send_queue_conn().execute(sql, params).fetchone()


def fetchall(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    return get_send_queue_conn().execute(sql, params).fetchall()


def execute(sql: str, params: tuple = ()) -> sqlite3.Cursor:
    conn = get_send_queue_conn()
    cur = conn.execute(sql, params)
    conn.commit()
    return cur


# ── ユニット操作 ─────────────────────────────────────────────


def enqueue_unit(
    seq: int,
    history_name: str | None,
    generation_ids: list[int] | None,
    payload: dict,
) -> int:
    """ユニットを追加する。記録なし生成は history_name/generation_ids を None で。"""
    cur = execute(
        "INSERT INTO send_queue (seq, history_name, generation_ids, payload) "
        "VALUES (?, ?, ?, ?)",
        (
            seq,
            history_name,
            json.dumps(generation_ids) if generation_ids else None,
            json.dumps(payload, ensure_ascii=False),
        ),
    )
    return int(cur.lastrowid)


def pending_units() -> list[sqlite3.Row]:
    """未処理ユニットを送信順に返す。"""
    return fetchall("SELECT * FROM send_queue ORDER BY seq ASC, id ASC")


def mark_sent(unit_id: int, item_ids: list[int]) -> None:
    """送信済み item_ids を控える（履歴行への書き込み前にクラッシュした場合の再送防止）。"""
    execute(
        "UPDATE send_queue SET sent_item_ids=? WHERE id=?",
        (json.dumps(item_ids), unit_id),
    )


def delete_unit(unit_id: int) -> None:
    execute("DELETE FROM send_queue WHERE id=?", (unit_id,))


def clear_all() -> None:
    execute("DELETE FROM send_queue")


def unit_generation_ids(row: sqlite3.Row) -> list[int]:
    """レコードの generation_ids（JSON）を安全に list[int] で返す。"""
    try:
        return [int(g) for g in json.loads(row["generation_ids"] or "[]")]
    except Exception:
        return []


def unit_sent_item_ids(row: sqlite3.Row) -> list[int] | None:
    """送信済みなら item_ids、未送信なら None。"""
    raw = row["sent_item_ids"]
    if not raw:
        return None
    try:
        return [int(i) for i in json.loads(raw)]
    except Exception:
        return None
