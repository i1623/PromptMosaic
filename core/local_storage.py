"""
ローカル画像ストレージ管理

generation_groups.folder_path を親方向に辿って保存先を解決し、
InvokeAI から取得した画像バイト列をコピーする。
サムネイルは DB の BLOB として管理するため、このモジュールは扱わない。
"""
from __future__ import annotations

from pathlib import Path
import re

import db.env_db as _env_db
import db.history_db as _history_db

# デフォルト保存先（app_settings が空の場合）
_DEFAULT_IMAGES = Path(__file__).parent.parent / "images"
_WINDOWS_FORBIDDEN_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def get_root_dir() -> Path:
    """env_settings の local_images_dir を返す。未設定なら ./images。"""
    row = _env_db.fetchone(
        "SELECT value FROM env_settings WHERE key='local_images_dir'"
    )
    val = row["value"] if row else ""
    return Path(val) if val else _DEFAULT_IMAGES


def default_group_folder(name: str, parent_id: int | None = None) -> Path:
    """新規グループ用の物理保存先フォルダを返す。"""
    safe_name = "".join(c for c in name.strip() if c not in '<>:"/\\|?*').strip()
    if not safe_name:
        safe_name = "group"
    parent_dir = resolve_folder_path(parent_id) if parent_id is not None else get_root_dir()
    return parent_dir / safe_name


def resolve_folder_path(group_id: int | None, db=None) -> Path:
    """
    group_id から保存先フォルダパスを解決する。

    自グループ → 親 → 祖先 の順に folder_path を探し、
    最初に見つかったものを返す。どこにも設定がなければ get_root_dir() を返す。

    db: fetchone を持つ履歴DBハンドル（history_db.for_history() など）。
        省略時はアクティブ履歴。ワーカースレッドからは、実行中にアクティブ履歴が
        切り替わっても参照先がすり替わらないよう固定ハンドルを渡すこと。
    """
    hdb = db if db is not None else _history_db
    current_id = group_id
    while current_id is not None:
        row = hdb.fetchone(
            "SELECT folder_path, parent_id FROM generation_groups WHERE id=?",
            (current_id,),
        )
        if not row:
            break
        if row["folder_path"]:
            return Path(row["folder_path"])
        current_id = row["parent_id"]
    return get_root_dir()


def is_drive_accessible(path: Path) -> bool:
    """パスのルート（ドライブ）がアクセス可能かを確認する。"""
    try:
        root = Path(path.anchor) if path.anchor else path.parent
        return root.exists()
    except Exception:
        return False


def copy_image(image_bytes: bytes, image_name: str, dest_dir: Path) -> Path:
    """
    image_bytes を dest_dir/image_name として書き込み、保存先 Path を返す。

    フォルダが存在しない場合は自動作成する。
    書き込みエラー（満杯・権限なし等）は OSError / IOError として伝播させる。
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / image_name
    dest_path.write_bytes(image_bytes)
    return dest_path


def has_group_folder(group_id: int | None, db=None) -> bool:
    """グループに保存先フォルダが設定されているか（親を含む）を返す。

    db は resolve_folder_path() と同様の履歴DBハンドル（省略時はアクティブ履歴）。
    """
    if group_id is None:
        return False
    hdb = db if db is not None else _history_db
    current_id: int | None = group_id
    while current_id is not None:
        row = hdb.fetchone(
            "SELECT folder_path, parent_id FROM generation_groups WHERE id=?",
            (current_id,),
        )
        if not row:
            break
        if row["folder_path"]:
            return True
        current_id = row["parent_id"]
    return False
