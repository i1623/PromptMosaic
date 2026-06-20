"""
プロンプトビルダー コアロジック

データ構造:
    PromptDocument
    └── PositivePrompt / NegativePrompt
        └── Block (top / middle / bottom)
            └── TagTile / NaturalTextTile

コンパイラ:
    PromptDocument → Invoke送信文字列

DB I/O:
    PromptDocument ↔ tiles / prompt_blocks テーブル

JSON シリアライズ:
    PromptDocument ↔ structured_prompt カラム（JSON文字列）
"""

from __future__ import annotations

import json
import random
import re
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from typing import ClassVar, Union
from core.text_sanitize import single_line_text

import db.history_db as _history_db


# ============================================================
# 定数
# ============================================================

class BlockPosition(str, Enum):
    TOP    = "top"
    MIDDLE = "middle"
    BOTTOM = "bottom"


class BlockType(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"


class TileType(str, Enum):
    TAG          = "tag"
    NATURAL_TEXT = "natural_text"


class TileCategory(str, Enum):
    """タグの8カテゴリ"""
    OBJECT      = "object"
    STATE       = "state"
    QUALITY     = "quality"
    STYLE       = "style"
    COMPOSITION = "composition"
    LIGHTING    = "lighting"
    ACTION      = "action"
    SCENE       = "scene"


# ============================================================
# タイル
# ============================================================

@dataclass
class TagTile:
    """
    単語タグを表すタイル。

    Attributes:
        tag_name:        英語タグ名（Invokeに送る値）
        tag_local:       ローカライズ表示ラベル（日本語など）
        category:        TileCategory
        dictionary_key:  由来タグ辞書の安定キー
        emphasis:        強調度。1.0 = 素のタグ、1.3 = (tag)1.3（compel書式）
        strength_level:  compel +/- 強度。0=なし、1=+、2=++、-1=-、など
        is_locked:       Trueのときシャッフル対象外
        is_trigger_word: LoRAトリガーワードとして追加されたとき True（表示用フラグ）
        enabled:         False のときプロンプトから除外（ON/OFF トグル）
        lora_source_key: このタイルを追加した LoRA の invoke_key（トグル連動用）
        source_text:     翻訳前の原文タグ。空の場合は tag_local/tag_name を使う
        translated_text: 翻訳結果タグ。空の場合は tag_name を使う
    """
    tag_name:        str
    tag_local:       str   = ""
    category:        str   = ""
    dictionary_key:  str   = ""
    emphasis:        float = 1.0
    strength_level:  int   = 0
    is_locked:       bool  = False
    is_trigger_word: bool  = False
    enabled:         bool  = True
    lora_source_key: str   = ""
    source_text:     str   = ""
    translated_text: str   = ""

    tile_type: str = field(default=TileType.TAG.value, init=False, repr=False)

    def __post_init__(self) -> None:
        self.tag_name = single_line_text(self.tag_name)
        self.tag_local = single_line_text(self.tag_local)
        self.dictionary_key = single_line_text(self.dictionary_key)
        self.source_text = single_line_text(self.source_text)
        self.translated_text = single_line_text(self.translated_text)

    def compile(self) -> str:
        """タグをcompel書式文字列に変換する。無効タイルは空文字を返す。"""
        if not self.enabled:
            return ""
        name = self.tag_name.strip()
        if not name:
            return ""
        # compel +/- style suffix
        if self.strength_level > 0:
            name = name + "+" * self.strength_level
        elif self.strength_level < 0:
            name = name + "-" * abs(self.strength_level)
        # compel numerical style: (word)value
        if abs(self.emphasis - 1.0) < 1e-6:
            return name
        return f"({name}){self.emphasis:.4g}"

    def local_text(self) -> str:
        """UI language/source side text for copy-only workflows."""
        return (self.source_text or self.tag_local or self.tag_name).strip()

    def to_dict(self) -> dict:
        return {
            "tile_type":       TileType.TAG.value,
            "tag_name":        single_line_text(self.tag_name),
            "tag_local":       single_line_text(self.tag_local),
            "category":        self.category,
            "dictionary_key":  self.dictionary_key,
            "emphasis":        self.emphasis,
            "strength_level":  self.strength_level,
            "is_locked":       self.is_locked,
            "is_trigger_word": self.is_trigger_word,
            "enabled":         self.enabled,
            "lora_source_key": self.lora_source_key,
            "source_text":     single_line_text(self.source_text),
            "translated_text": single_line_text(self.translated_text),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TagTile":
        return cls(
            tag_name        = d["tag_name"],
            tag_local       = d.get("tag_local", d.get("tag_ja", "")),  # 旧形式 tag_ja も受け付ける
            category        = d.get("category", ""),
            dictionary_key  = d.get("dictionary_key", ""),
            emphasis        = float(d.get("emphasis", 1.0)),
            strength_level  = int(d.get("strength_level", 0)),
            is_locked       = bool(d.get("is_locked", False)),
            is_trigger_word = bool(d.get("is_trigger_word", False)),
            enabled         = bool(d.get("enabled", True)),
            lora_source_key = d.get("lora_source_key", ""),
            source_text     = d.get("source_text", ""),
            translated_text = d.get("translated_text", ""),
        )


@dataclass
class NaturalTextTile:
    """
    自然文を表すタイル。内部の単語は分解されず1塊として扱われる。

    Attributes:
        text:            自然文（後方互換のため維持。compile時は translated_text を優先）
        source_text:     翻訳前の原文。空の場合は text と同値とみなす
        translated_text: 翻訳結果。空の場合は text をそのまま使用
        display_label:   タイル表示用の短い名前。空の場合は source_text/text を表示
        language:        "en" / "ja"
        enabled:         False のときプロンプトから除外（ON/OFF トグル）
    """
    text:            str
    source_text:     str  = ""
    translated_text: str  = ""
    display_label:   str  = ""
    language:        str  = "en"
    enabled:         bool = True
    strength_level:  int  = 0
    emphasis:        float = 1.0

    tile_type: str = field(default=TileType.NATURAL_TEXT.value, init=False, repr=False)

    def __post_init__(self) -> None:
        self.text = single_line_text(self.text)
        self.source_text = single_line_text(self.source_text)
        self.translated_text = single_line_text(self.translated_text)
        self.display_label = single_line_text(self.display_label)

    def compile(self) -> str:
        """無効タイルは空文字を返す。translated_text があれば優先。strength/emphasis を適用。"""
        if not self.enabled:
            return ""
        text = (self.translated_text or self.text).strip()
        if not text:
            return ""
        lv  = self.strength_level
        emp = abs(self.emphasis - 1.0) > 1e-4
        if lv != 0 or emp:
            s = "+" * lv if lv > 0 else "-" * abs(lv)
            if emp:
                return f"({text}){self.emphasis:.4g}"
            return f"({text}){s}"
        return text

    def local_text(self) -> str:
        """UI language/source side text for copy-only workflows."""
        return (self.source_text or self.display_label or self.text or self.translated_text).strip()

    def to_dict(self) -> dict:
        return {
            "tile_type":       TileType.NATURAL_TEXT.value,
            "text":            single_line_text(self.text),
            "source_text":     single_line_text(self.source_text),
            "translated_text": single_line_text(self.translated_text),
            "display_label":   single_line_text(self.display_label),
            "language":        self.language,
            "enabled":         self.enabled,
            "strength_level":  self.strength_level,
            "emphasis":        self.emphasis,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "NaturalTextTile":
        return cls(
            text            = d["text"],
            source_text     = d.get("source_text", ""),
            translated_text = d.get("translated_text", ""),
            display_label   = d.get("display_label", ""),
            language        = d.get("language", "en"),
            enabled         = bool(d.get("enabled", True)),
            strength_level  = int(d.get("strength_level", 0)),
            emphasis        = float(d.get("emphasis", 1.0)),
        )


@dataclass
class GroupTile:
    """
    複数のタイルをグループ化するコンテナタイル（2階層ネスト可）。

    Attributes:
        name:    グループ名（表示用）
        tiles:   格納するタイルのリスト（AnyTile を含む GroupTile も可）
        mode:    コンパイル時の選択モード
                 "none"       — 全タイルを出力
                 "random"     — count 個をランダム選択
                 "sequential" — count 個を順番に選択（生成毎に進む）
        count:   random/sequential モードでの選択個数
        enabled: False のとき全体を除外
        strength_level: compel +/- 強度。0=なし、1=+、2=++、-1=-、など
        edit_locked: True のときUI上でグループ内容の編集をロック
    """
    _grp_counter: ClassVar[int] = 0

    name:    str  = ""
    tiles:   list = field(default_factory=list)  # list[AnyTile]
    mode:    str  = "none"
    count:   int  = 1
    enabled: bool = True
    strength_level: int = 0
    edit_locked: bool = False
    # LoRA トリガーワードグループの連動キー（TagTile.lora_source_key と同様）
    lora_source_key: str = field(default="", init=False, repr=False)
    # UI の展開/折りたたみ状態（_refresh_tiles 再構築後も復元するために保持）
    ui_expanded: bool = field(default=False, init=False, repr=False)
    # シーケンシャルインデックス（実行時のみ、シリアライズ対象外）
    _seq_idx: int = field(default=0, init=False, repr=False)
    # 直前の compile() で選択したタイルの self.tiles 内インデックス
    # （実行時のみ。子の履歴スナップショットが「選択タイルのみON」を再現するために使う）
    _last_selected: list | None = field(default=None, init=False, repr=False)

    tile_type: str = field(default="group", init=False, repr=False)

    def __post_init__(self) -> None:
        self.name = single_line_text(self.name)
        if not self.name:
            GroupTile._grp_counter += 1
            self.name = f"Grp{GroupTile._grp_counter}"

    def compile(self) -> str:
        if not self.enabled:
            return ""
        active = [t for t in self.tiles if getattr(t, "enabled", True)]
        if not active:
            return ""
        if self.mode == "random":
            n = min(self.count, len(active))
            selected = random.sample(active, n)
        elif self.mode == "sequential":
            n = min(self.count, len(active))
            idx = self._seq_idx % len(active)
            selected = []
            for _ in range(n):
                selected.append(active[idx % len(active)])
                idx += 1
            self._seq_idx = idx % len(active)
        else:
            selected = active
        # 選択モードでは「どのタイルを選んだか」を記録する
        # （子の履歴スナップショット = 選択タイルのみON の再現用。compile_local は対象外）
        if self.mode in ("random", "sequential"):
            sel_ids = {id(t) for t in selected}
            self._last_selected = [
                i for i, t in enumerate(self.tiles) if id(t) in sel_ids
            ]
        parts = [t.compile() for t in selected]
        text = ", ".join(p for p in parts if p)
        if text and self.strength_level != 0:
            suffix = "+" * self.strength_level if self.strength_level > 0 else "-" * abs(self.strength_level)
            return f"({text}){suffix}"
        return text

    def compile_local(self, *, include_disabled: bool = False, separator: str = "\n") -> str:
        """Return source/local-language text for this group.

        include_disabled=True is for block-level copy: disabled tiles/groups are
        preserved as //...// so the user can see what is currently OFF.
        """
        if not self.enabled and not include_disabled:
            return ""

        if include_disabled:
            selected = list(self.tiles)
        else:
            active = [t for t in self.tiles if getattr(t, "enabled", True)]
            if not active:
                return ""
            if self.mode == "random":
                n = min(self.count, len(active))
                selected = random.sample(active, n)
            elif self.mode == "sequential":
                n = min(self.count, len(active))
                idx = self._seq_idx % len(active)
                selected = []
                for _ in range(n):
                    selected.append(active[idx % len(active)])
                    idx += 1
                self._seq_idx = idx % len(active)
            else:
                selected = active

        parts = [
            _tile_local_text(t, include_disabled=include_disabled, separator=separator)
            for t in selected
        ]
        text = separator.join(p for p in parts if p)
        if include_disabled and not self.enabled and text:
            return f"//{text}//"
        return text

    def reset_seq(self) -> None:
        """シーケンシャルインデックスを先頭に戻す"""
        self._seq_idx = 0
        for t in self.tiles:
            if isinstance(t, GroupTile):
                t.reset_seq()

    def to_dict(self, *, include_ui_state: bool = True) -> dict:
        data = {
            "tile_type":       "group",
            "name":            single_line_text(self.name),
            "tiles":           [
                t.to_dict(include_ui_state=include_ui_state) if isinstance(t, GroupTile) else t.to_dict()
                for t in self.tiles
            ],
            "mode":            self.mode,
            "count":           self.count,
            "enabled":         self.enabled,
            "strength_level":  self.strength_level,
            "edit_locked":     self.edit_locked,
            "lora_source_key": self.lora_source_key,
        }
        if include_ui_state:
            data["ui_expanded"] = self.ui_expanded
        return data

    @classmethod
    def from_dict(
        cls,
        d: dict,
        *,
        name_override: str | None = None,
        restore_ui_state: bool = True,
    ) -> "GroupTile":
        gt = cls(
            name    = name_override if name_override is not None else d.get("name", ""),
            mode    = d.get("mode", "none"),
            count   = int(d.get("count", 1)),
            enabled = bool(d.get("enabled", True)),
            strength_level = int(d.get("strength_level", 0)),
            edit_locked = bool(d.get("edit_locked", False)),
        )
        gt.lora_source_key = d.get("lora_source_key", "")
        gt.ui_expanded     = bool(d.get("ui_expanded", False)) if restore_ui_state else False
        for td in d.get("tiles", []):
            tt = td.get("tile_type", TileType.TAG.value)
            if tt == "group":
                gt.tiles.append(GroupTile.from_dict(td, restore_ui_state=restore_ui_state))
            elif tt == TileType.TAG.value:
                gt.tiles.append(TagTile.from_dict(td))
            else:
                gt.tiles.append(NaturalTextTile.from_dict(td))
        return gt


AnyTile = Union[TagTile, NaturalTextTile, "GroupTile"]


def _tile_local_text(tile: AnyTile, *, include_disabled: bool = False, separator: str = "\n") -> str:
    """Return local/source text for a tile, optionally marking OFF items."""
    if isinstance(tile, GroupTile):
        return tile.compile_local(include_disabled=include_disabled, separator=separator)

    enabled = bool(getattr(tile, "enabled", True))
    if not enabled and not include_disabled:
        return ""

    if isinstance(tile, (TagTile, NaturalTextTile)):
        text = tile.local_text()
    else:
        text = str(
            getattr(tile, "tag_local", "")
            or getattr(tile, "source_text", "")
            or getattr(tile, "text", "")
        ).strip()

    if include_disabled and not enabled and text:
        return f"//{text}//"
    return text


# ============================================================
# ブロック
# ============================================================

@dataclass
class Block:
    """
    プロンプト内の1ブロック（先頭 / 中間 / 末尾）。

    Attributes:
        position:  BlockPosition
        block_type: BlockType
        tiles:     このブロックに含まれるタイルのリスト
        randomize: Trueのとき compile() 時にブロック内タイルをシャッフル
        label:     UI表示用のラベル（任意）
    """
    position:   str = BlockPosition.MIDDLE.value
    block_type: str = BlockType.POSITIVE.value
    tiles:      list[AnyTile] = field(default_factory=list)
    randomize:  bool = False
    label:      str  = ""

    def add_tile(self, tile: AnyTile, index: int | None = None) -> None:
        if index is None:
            self.tiles.append(tile)
        else:
            self.tiles.insert(index, tile)

    def remove_tile(self, index: int) -> AnyTile:
        return self.tiles.pop(index)

    def move_tile(self, from_index: int, to_index: int) -> None:
        tile = self.tiles.pop(from_index)
        self.tiles.insert(to_index, tile)

    def compile(self) -> str:
        """
        ブロックをInvoke用文字列に変換する。

        - randomize=True のとき、ロックされていないタイルをシャッフルする。
          is_locked=True の TagTile は元の位置に固定する。
        - タグとタグの間はカンマ区切り。
        - 自然文はそのままスペースで連結（前後にカンマを入れない）。
        """
        tiles = self._shuffled_tiles() if self.randomize else list(self.tiles)

        parts: list[str] = []
        tag_buffer: list[str] = []

        def flush_tags():
            if tag_buffer:
                parts.append(", ".join(tag_buffer))
                tag_buffer.clear()

        for tile in tiles:
            compiled = tile.compile()
            if not compiled:
                continue
            if isinstance(tile, TagTile):
                tag_buffer.append(compiled)
            else:  # NaturalTextTile / GroupTile
                flush_tags()
                parts.append(compiled)

        flush_tags()
        return ", ".join(parts) if parts else ""

    def compile_local(self, *, include_disabled: bool = False, separator: str = "\n") -> str:
        """ブロックの現地語/原文だけを取り出す。"""
        tiles = self._shuffled_tiles() if (self.randomize and not include_disabled) else list(self.tiles)
        parts = [
            _tile_local_text(tile, include_disabled=include_disabled, separator=separator)
            for tile in tiles
        ]
        return separator.join(p for p in parts if p)

    def _shuffled_tiles(self) -> list[AnyTile]:
        """
        ロックされていないタイルをシャッフルする。

        アルゴリズム:
          1. is_locked=True の TagTile の位置を固定する
          2. それ以外の TagTile / NaturalTextTile / GroupTile を抽出してシャッフル
          3. 元のリストをなぞり、固定位置以外へシャッフル済みタイルを差し込む
        """
        locked_positions: dict[int, TagTile] = {}
        unlocked_tiles: list[AnyTile] = []

        for i, tile in enumerate(self.tiles):
            if isinstance(tile, TagTile) and tile.is_locked:
                locked_positions[i] = tile
            else:
                unlocked_tiles.append(tile)

        random.shuffle(unlocked_tiles)
        tile_iter = iter(unlocked_tiles)

        result: list[AnyTile] = []
        for i, tile in enumerate(self.tiles):
            if i in locked_positions:
                result.append(locked_positions[i])
            else:
                result.append(next(tile_iter))

        return result

    def to_dict(self) -> dict:
        return {
            "position":   self.position,
            "block_type": self.block_type,
            "randomize":  self.randomize,
            "label":      self.label,
            "tiles":      [t.to_dict() for t in self.tiles],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Block":
        block = cls(
            position   = d.get("position",   BlockPosition.MIDDLE.value),
            block_type = d.get("block_type", BlockType.POSITIVE.value),
            randomize  = bool(d.get("randomize", False)),
            label      = d.get("label", ""),
        )
        for td in d.get("tiles", []):
            tt = td.get("tile_type", TileType.TAG.value)
            if tt == "group":
                block.tiles.append(GroupTile.from_dict(td))
            elif tt == TileType.TAG.value:
                block.tiles.append(TagTile.from_dict(td))
            else:
                block.tiles.append(NaturalTextTile.from_dict(td))
        return block


# ============================================================
# PromptSide（ポジ or ネガ、3ブロック固定）
# ============================================================

@dataclass
class PromptSide:
    """
    ポジティブまたはネガティブプロンプトの全ブロックを管理する。

    top / middle / bottom の3ブロックを常に保持する。
    """
    block_type: str = BlockType.POSITIVE.value
    top:    Block = field(default_factory=lambda: Block(position=BlockPosition.TOP.value,    block_type=BlockType.POSITIVE.value))
    middle: Block = field(default_factory=lambda: Block(position=BlockPosition.MIDDLE.value, block_type=BlockType.POSITIVE.value))
    bottom: Block = field(default_factory=lambda: Block(position=BlockPosition.BOTTOM.value, block_type=BlockType.POSITIVE.value))

    def __post_init__(self):
        # block_type を各ブロックに反映
        for blk in (self.top, self.middle, self.bottom):
            blk.block_type = self.block_type

    def block(self, position: str) -> Block:
        return {"top": self.top, "middle": self.middle, "bottom": self.bottom}[position]

    def compile(self) -> str:
        """3ブロックを top→middle→bottom の順に結合する。"""
        parts = []
        for b in (self.top, self.middle, self.bottom):
            c = b.compile()
            if c:
                parts.append(c)
        return ", ".join(parts)

    def compile_local(self, *, include_disabled: bool = False, separator: str = "\n") -> str:
        """3ブロックの現地語/原文を top→middle→bottom の順に結合する。"""
        parts = []
        for b in (self.top, self.middle, self.bottom):
            c = b.compile_local(include_disabled=include_disabled, separator=separator)
            if c:
                parts.append(c)
        return separator.join(parts)

    def to_dict(self) -> dict:
        return {
            "block_type": self.block_type,
            "top":    self.top.to_dict(),
            "middle": self.middle.to_dict(),
            "bottom": self.bottom.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PromptSide":
        bt = d.get("block_type", BlockType.POSITIVE.value)
        side = cls(block_type=bt)
        side.top    = Block.from_dict(d["top"])
        side.middle = Block.from_dict(d["middle"])
        side.bottom = Block.from_dict(d["bottom"])
        return side


# ============================================================
# PromptDocument（全体コンテナ）
# ============================================================

@dataclass
class PromptDocument:
    """
    プロンプト全体を表すコンテナ。

    positive / negative の2つの PromptSide を持つ。
    compile_positive() / compile_negative() でInvoke送信用文字列を生成する。
    """
    positive: PromptSide = field(default_factory=lambda: PromptSide(block_type=BlockType.POSITIVE.value))
    negative: PromptSide = field(default_factory=lambda: PromptSide(block_type=BlockType.NEGATIVE.value))

    def compile_positive(self) -> str:
        return self.positive.compile()

    def compile_negative(self) -> str:
        return self.negative.compile()

    def compile_local_positive(self, *, include_disabled: bool = False, separator: str = "\n") -> str:
        return self.positive.compile_local(include_disabled=include_disabled, separator=separator)

    def compile_local_negative(self, *, include_disabled: bool = False, separator: str = "\n") -> str:
        return self.negative.compile_local(include_disabled=include_disabled, separator=separator)

    def _all_group_tiles(self) -> list["GroupTile"]:
        """ドキュメント内の全 GroupTile を再帰収集する。"""
        result: list[GroupTile] = []

        def _collect(tiles: list) -> None:
            for t in tiles:
                if isinstance(t, GroupTile):
                    result.append(t)
                    _collect(t.tiles)

        for side in (self.positive, self.negative):
            for blk in (side.top, side.middle, side.bottom):
                _collect(blk.tiles)
        return result

    def has_variation_sources(self, *, include_negative: bool = True) -> bool:
        """
        プロンプトに「生成ごとに送信文字列が変わる要素」があるかを返す。

        変動要素とみなすもの:
          ① randomize（ブロックシャッフル）が有効で、動かせるタイル
             （有効かつ非ロック）が2つ以上あるブロック
          ② 有効な random/sequential グループで有効タイルが2つ以上のもの
             （ネストは有効なグループの中だけ辿る）

        シード固定×複数枚生成の事前チェック用（同一画像の量産防止）。
        """
        def _groups_vary(tiles: list) -> bool:
            for t in tiles:
                if isinstance(t, GroupTile) and t.enabled:
                    active = [x for x in t.tiles if getattr(x, "enabled", True)]
                    if t.mode in ("random", "sequential") and len(active) >= 2:
                        return True
                    if _groups_vary(t.tiles):
                        return True
            return False

        sides = [self.positive] + ([self.negative] if include_negative else [])
        for side in sides:
            for blk in (side.top, side.middle, side.bottom):
                if blk.randomize:
                    movable = [
                        t for t in blk.tiles
                        if getattr(t, "enabled", True)
                        and not getattr(t, "is_locked", False)
                    ]
                    if len(movable) >= 2:
                        return True
                if _groups_vary(blk.tiles):
                    return True
        return False

    def reset_selection_log(self) -> None:
        """全グループの「直前 compile の選択記録」をクリアする（生成バッチ開始時に呼ぶ）。"""
        for g in self._all_group_tiles():
            g._last_selected = None

    def snapshot_with_last_selection(self) -> "PromptDocument":
        """
        子の履歴用スナップショットを返す。

        直前の compile() で random/sequential グループが選択したタイルのみを ON、
        同グループ内の他タイルを OFF にしたクローン。グループの mode/count/enabled は
        そのまま維持する（親＝現在のドキュメントは変更しない）。
        選択記録がないグループ（mode "none"・無効・未コンパイル）は不変。
        """
        snap = self.clone()

        def _apply(orig_tiles: list, snap_tiles: list) -> None:
            for o, s in zip(orig_tiles, snap_tiles):
                if isinstance(o, GroupTile) and isinstance(s, GroupTile):
                    if o.mode in ("random", "sequential") and o._last_selected is not None:
                        keep = set(o._last_selected)
                        for i, st in enumerate(s.tiles):
                            st.enabled = i in keep
                    _apply(o.tiles, s.tiles)

        for side_o, side_s in ((self.positive, snap.positive), (self.negative, snap.negative)):
            for pos_name in ("top", "middle", "bottom"):
                _apply(side_o.block(pos_name).tiles, side_s.block(pos_name).tiles)
        return snap

    def compile_for_preview(self) -> tuple[str, str]:
        """プレビュー用コンパイル。sequential の _seq_idx・選択記録を進めない。"""
        groups = self._all_group_tiles()
        snapshot = {id(g): (g._seq_idx, g._last_selected) for g in groups}
        try:
            pos = self.compile_positive()
            neg = self.compile_negative()
        finally:
            for g in groups:
                if id(g) in snapshot:
                    g._seq_idx, g._last_selected = snapshot[id(g)]
        return pos, neg

    def compile_local_for_preview(self) -> tuple[str, str]:
        """現地語コピー用コンパイル。sequential の _seq_idx を進めない。"""
        groups = self._all_group_tiles()
        snapshot = {id(g): g._seq_idx for g in groups}
        try:
            pos = self.compile_local_positive()
            neg = self.compile_local_negative()
        finally:
            for g in groups:
                if id(g) in snapshot:
                    g._seq_idx = snapshot[id(g)]
        return pos, neg

    def to_dict(self) -> dict:
        return {
            "positive": self.positive.to_dict(),
            "negative": self.negative.to_dict(),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, d: dict) -> "PromptDocument":
        doc = cls()
        doc.positive = PromptSide.from_dict(d["positive"])
        doc.negative = PromptSide.from_dict(d["negative"])
        return doc

    @classmethod
    def from_json(cls, s: str) -> "PromptDocument":
        return cls.from_dict(json.loads(s))

    def clone(self) -> "PromptDocument":
        return PromptDocument.from_dict(self.to_dict())

    # ------------------------------------------------------------------
    # DB保存 / 読み込み
    # ------------------------------------------------------------------

    def save_to_db(self, generation_id: int) -> None:
        """
        このドキュメントの tiles / prompt_blocks を DB に保存する。
        既存データがあれば削除して上書きする。
        """
        with _history_db.transaction() as conn:
            conn.execute("DELETE FROM tiles        WHERE generation_id = ?", (generation_id,))
            conn.execute("DELETE FROM prompt_blocks WHERE generation_id = ?", (generation_id,))

            for side in (self.positive, self.negative):
                for blk in (side.top, side.middle, side.bottom):
                    # prompt_blocks
                    conn.execute(
                        """
                        INSERT INTO prompt_blocks (generation_id, block_type, block_position, randomize, label)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (generation_id, blk.block_type, blk.position, int(blk.randomize), blk.label),
                    )
                    # tiles
                    for order_index, tile in enumerate(blk.tiles):
                        if isinstance(tile, TagTile):
                            conn.execute(
                                """
                                INSERT INTO tiles
                                    (generation_id, block_type, block_position, order_index,
                                     tile_type, tag_name, tag_local, tag_category, emphasis, is_locked,
                                     natural_text)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    generation_id, blk.block_type, blk.position, order_index,
                                    TileType.TAG.value,
                                    single_line_text(tile.tag_name),
                                    single_line_text(tile.tag_local),
                                    tile.category,
                                    tile.emphasis, int(tile.is_locked),
                                    json.dumps(tile.to_dict(), ensure_ascii=False),
                                ),
                            )
                        elif isinstance(tile, GroupTile):
                            import json as _json
                            conn.execute(
                                """
                                INSERT INTO tiles
                                    (generation_id, block_type, block_position, order_index,
                                     tile_type, natural_text)
                                VALUES (?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    generation_id, blk.block_type, blk.position, order_index,
                                    "group",
                                    _json.dumps(tile.to_dict(), ensure_ascii=False),
                                ),
                            )
                        else:  # NaturalTextTile
                            conn.execute(
                                """
                                INSERT INTO tiles
                                    (generation_id, block_type, block_position, order_index,
                                     tile_type, natural_text, natural_language)
                                VALUES (?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    generation_id, blk.block_type, blk.position, order_index,
                                    TileType.NATURAL_TEXT.value,
                                    json.dumps(tile.to_dict(), ensure_ascii=False), tile.language,
                                ),
                            )

    @classmethod
    def load_from_db(cls, generation_id: int) -> "PromptDocument | None":
        """
        DB から generation_id に対応する PromptDocument を復元する。
        データが存在しない場合は None を返す。
        """
        blocks_rows = _history_db.fetchall(
            "SELECT * FROM prompt_blocks WHERE generation_id = ? ORDER BY block_type, block_position",
            (generation_id,),
        )
        if not blocks_rows:
            return None

        tiles_rows = _history_db.fetchall(
            "SELECT * FROM tiles WHERE generation_id = ? ORDER BY block_type, block_position, order_index",
            (generation_id,),
        )
        # {(block_type, block_position): [tile, ...]}
        tile_map: dict[tuple, list[AnyTile]] = {}
        for row in tiles_rows:
            key = (row["block_type"], row["block_position"])
            tile_map.setdefault(key, [])
            if row["tile_type"] == TileType.TAG.value:
                natural_text = row["natural_text"] or ""
                try:
                    d = json.loads(natural_text)
                    if isinstance(d, dict) and d.get("tile_type") == TileType.TAG.value:
                        tile_map[key].append(TagTile.from_dict(d))
                    else:
                        raise ValueError
                except Exception:
                    # 旧形式（個別カラム）からのフォールバック
                    tile_map[key].append(TagTile(
                        tag_name  = row["tag_name"] or "",
                        tag_local = row["tag_local"] or "",
                        category  = row["tag_category"] or "",
                        emphasis  = float(row["emphasis"] or 1.0),
                        is_locked = bool(row["is_locked"]),
                    ))
            elif row["tile_type"] == "group":
                import json as _json
                try:
                    d = _json.loads(row["natural_text"] or "{}")
                    tile_map[key].append(GroupTile.from_dict(d))
                except Exception:
                    pass  # 壊れたグループデータは無視
            else:
                natural_text = row["natural_text"] or ""
                try:
                    d = json.loads(natural_text)
                    if isinstance(d, dict) and d.get("tile_type") == TileType.NATURAL_TEXT.value:
                        tile_map[key].append(NaturalTextTile.from_dict(d))
                    else:
                        raise ValueError
                except Exception:
                    tile_map[key].append(NaturalTextTile(
                        text     = natural_text,
                        language = row["natural_language"] or "en",
                    ))

        doc = cls()
        for row in blocks_rows:
            bt  = row["block_type"]
            pos = row["block_position"]
            side = doc.positive if bt == BlockType.POSITIVE.value else doc.negative
            blk  = side.block(pos)
            blk.randomize = bool(row["randomize"])
            blk.label     = row["label"] or ""
            blk.tiles     = tile_map.get((bt, pos), [])

        return doc
