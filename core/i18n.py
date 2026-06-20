"""
国際化（i18n）モジュール

使い方:
    from core.i18n import tr, set_language, available_languages

    set_language("ja")          # 起動時に1度呼ぶ
    label = tr("block.clear")   # → "クリア"
    msg   = tr("main.status_queue", pending=3, in_prog=1, completed=42)

対応言語:
    lang/ ディレクトリ内の *.json ファイルを自動検出
    各 JSON の "__lang" キーが言語コード（なければファイル名）

ルール:
    ・キーが見つからない場合はキー文字列をそのまま返す
    ・{placeholder} 形式の文字列は **kwargs で展開する
    ・言語履歴を最大3件保持（スイッチャーUIで使用）
"""
from __future__ import annotations

import json
import pathlib

_lang: str = "ja"
_strings: dict[str, str] = {}

_LANG_DIR = pathlib.Path(__file__).parent.parent / "lang"

# 言語切り替え履歴（最新が末尾、最大3件）
_lang_history: list[str] = []


def _load_file(path: pathlib.Path) -> tuple[str, dict]:
    """JSON ファイルを読み込み (lang_code, strings) を返す。"""
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    lang_code = data.get("__lang") or path.stem
    return lang_code, {k: v for k, v in data.items() if not k.startswith("__")}


def available_languages() -> list[tuple[str, str]]:
    """
    lang/ 内の言語ファイルを検出して (lang_code, display_name) リストを返す。

    並び順: 現在の言語 → 前の言語 → その前 → アルファベット順
    """
    if not _LANG_DIR.exists():
        return [(_lang, _lang)]

    found: dict[str, str] = {}  # lang_code → display_name
    for p in sorted(_LANG_DIR.glob("*.json")):
        try:
            with p.open(encoding="utf-8") as f:
                data = json.load(f)
            code = data.get("__lang") or p.stem
            # display name: 自分自身の language_ja/language_en キー、なければコード
            name = data.get(f"settings.language_{code}", code)
            found[code] = name
        except Exception:
            pass

    # 履歴順で並べ替え（最新が先頭）、残りはアルファベット順
    history_codes = list(reversed(_lang_history))
    ordered: list[tuple[str, str]] = []
    for c in history_codes:
        if c in found:
            ordered.append((c, found.pop(c)))
    for c in sorted(found):
        ordered.append((c, found[c]))
    return ordered


def set_language(lang: str) -> None:
    """言語をセットしてJSONを読み込む。不明な言語は "ja" にフォールバック。"""
    global _lang, _strings

    # lang/ 内から lang_code が一致するファイルを探す
    target_path: pathlib.Path | None = None
    if _LANG_DIR.exists():
        for p in _LANG_DIR.glob("*.json"):
            try:
                with p.open(encoding="utf-8") as f:
                    data = json.load(f)
                code = data.get("__lang") or p.stem
                if code == lang:
                    target_path = p
                    break
            except Exception:
                pass

    # 見つからなければ ja.json をフォールバック
    if target_path is None:
        target_path = _LANG_DIR / "ja.json"
        lang = "ja"

    if not target_path.exists():
        return

    _, strings = _load_file(target_path)
    _strings = strings
    _lang = lang

    # 言語履歴を更新（重複除去、最大3件）
    global _lang_history
    if lang in _lang_history:
        _lang_history.remove(lang)
    _lang_history.append(lang)
    if len(_lang_history) > 3:
        _lang_history.pop(0)


def current_language() -> str:
    """現在の言語コードを返す。"""
    return _lang


def tr(key: str, **kwargs) -> str:
    """
    翻訳文字列を返す。

    Args:
        key:    ドット区切りのキー（例: "block.clear"）
        **kwargs: str.format_map() に渡すパラメータ

    Returns:
        翻訳された文字列。キーが見つからない場合はキーをそのまま返す。
    """
    text = _strings.get(key, key)
    if kwargs:
        try:
            text = text.format_map(kwargs)
        except (KeyError, ValueError):
            pass
    return text


# ── 起動時のデフォルトロード ───────────────────────────
# インポートしただけで "ja" が使えるようにしておく
_default_path = _LANG_DIR / "ja.json"
if _default_path.exists():
    try:
        _, _strings = _load_file(_default_path)
        _lang_history = ["ja"]
    except Exception:
        pass
