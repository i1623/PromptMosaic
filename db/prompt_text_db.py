"""
文章プロンプト管理 DB アクセス関数群

テーブル: prompt_texts / prompt_text_embeddings
"""

from __future__ import annotations

from datetime import datetime

import db.library_db as database
from core.text_sanitize import single_line_text
from db.connections import get_active_library_name


def _sync_suggestions() -> None:
    try:
        from db.discovery import update_library_suggestions
        update_library_suggestions(get_active_library_name())
    except Exception:
        pass


# ── 取得 ──────────────────────────────────────────────────────────────────────

def get_all_prompt_texts(show_nsfw: bool = False) -> list[dict]:
    """全件取得。NSFW非表示設定時は is_nsfw=1 の行を除外する。"""
    nsfw_clause = "" if show_nsfw else "WHERE is_nsfw = 0 OR is_nsfw IS NULL"
    rows = database.fetchall(
        f"SELECT * FROM prompt_texts {nsfw_clause} ORDER BY updated_at DESC"
    )
    return [dict(r) for r in rows]


def search_prompt_texts(query: str, mode: str, show_nsfw: bool = False) -> list[dict]:
    """
    文章プロンプトをキーワード検索する。

    mode:
        "partial" — query を単一の部分一致で検索
        "and"     — 半角スペース区切りの全語一致
        "or"      — 半角スペース区切りのいずれか一致
    """
    nsfw_clause = "" if show_nsfw else "AND (is_nsfw = 0 OR is_nsfw IS NULL)"
    words = query.split() if query.strip() else []

    if not words:
        return get_all_prompt_texts(show_nsfw)

    search_cols = (
        "(source_text LIKE ? OR COALESCE(translated_text,'') LIKE ? "
        "OR COALESCE(display_label,'') LIKE ? OR COALESCE(keywords,'') LIKE ? "
        "OR COALESCE(category,'') LIKE ? OR COALESCE(genre,'') LIKE ?)"
    )

    if mode == "and":
        conditions = " AND ".join([search_cols] * len(words))
        params: tuple = ()
        for w in words:
            like = f"%{w}%"
            params += (like, like, like, like, like, like)
    elif mode == "or":
        conditions = " OR ".join([search_cols] * len(words))
        params = ()
        for w in words:
            like = f"%{w}%"
            params += (like, like, like, like, like, like)
    else:  # partial: query.strip() 全体を1語として部分一致
        conditions = search_cols
        like = f"%{query.strip()}%"
        params = (like, like, like, like, like, like)

    sql = (
        f"SELECT * FROM prompt_texts "
        f"WHERE ({conditions}) {nsfw_clause} "
        f"ORDER BY updated_at DESC"
    )
    rows = database.fetchall(sql, params)
    return [dict(r) for r in rows]


# ── 追加 ──────────────────────────────────────────────────────────────────────

def insert_prompt_text(
    source_text: str,
    translated_text: str = "",
    display_label: str = "",
    thumbnail_path: str = "",
    category: str = "",
    genre: str = "",
    parent_id: int | None = None,
    sort_order: int = 0,
    keywords: str = "",
    language: str = "ja",
    status: str = "active",
    rating: int | None = None,
    memo: str = "",
    is_nsfw: bool = False,
) -> int:
    """
    新規文章プロンプトを登録して発行された id を返す。

    source_text は必須（空文字は ValueError）。
    """
    source_text = single_line_text(source_text)
    translated_text = single_line_text(translated_text)
    display_label = single_line_text(display_label)
    thumbnail_path = single_line_text(thumbnail_path)
    category = single_line_text(category)
    genre = single_line_text(genre)
    keywords = single_line_text(keywords)
    language = single_line_text(language or "ja")
    status = single_line_text(status or "active")
    if not source_text.strip():
        raise ValueError("source_text must not be empty")

    cur = database.execute(
        """
        INSERT INTO prompt_texts
            (source_text, translated_text, display_label, thumbnail_path,
             category, genre, parent_id, sort_order, keywords, language, status,
             rating, memo, is_nsfw)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_text,
            translated_text or None,
            display_label or None,
            thumbnail_path or None,
            category or None,
            genre or None,
            parent_id,
            int(sort_order or 0),
            keywords or None,
            language or None,
            status or None,
            rating,
            memo or None,
            1 if is_nsfw else 0,
        ),
    )
    _sync_suggestions()
    return cur.lastrowid


# ── 更新 ──────────────────────────────────────────────────────────────────────

_UPDATABLE_COLS = {
    "source_text", "translated_text", "display_label",
    "category", "genre", "parent_id", "sort_order", "keywords", "language",
    "status", "rating", "memo", "is_nsfw",
}


def update_prompt_text(prompt_text_id: int, **kwargs) -> None:
    """
    指定カラムを更新し updated_at を現在時刻に更新する。

    許可カラム: source_text / translated_text / display_label /
                rating / memo / is_nsfw など
    """
    fields = {k: v for k, v in kwargs.items() if k in _UPDATABLE_COLS}
    if not fields:
        return
    for key in (
        "source_text", "translated_text", "display_label",
        "category", "genre", "keywords", "language", "status",
    ):
        if key in fields:
            fields[key] = single_line_text(fields[key])

    fields["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    params = tuple(fields.values()) + (prompt_text_id,)
    database.execute(
        f"UPDATE prompt_texts SET {set_clause} WHERE id = ?",
        params,
    )
    _sync_suggestions()


# ── 削除 ──────────────────────────────────────────────────────────────────────

def delete_prompt_text(prompt_text_id: int) -> None:
    """指定 id の文章プロンプトを削除する（関連 embedding も CASCADE 削除）。"""
    database.execute("DELETE FROM prompt_texts WHERE id = ?", (prompt_text_id,))
    _sync_suggestions()


# ── 重複チェック ──────────────────────────────────────────────────────────────

def exists_source_text(source_text: str) -> bool:
    """同一 source_text が既に登録されているかを返す。"""
    row = database.fetchone(
        "SELECT 1 FROM prompt_texts WHERE source_text = ? LIMIT 1",
        (source_text,),
    )
    return row is not None
