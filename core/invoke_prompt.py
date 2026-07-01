"""Invoke / Compel-style prompt parsing helpers."""
from __future__ import annotations

from dataclasses import dataclass
import re

from core.prompt_builder import GroupTile, NaturalTextTile, TagTile
from core.text_sanitize import single_line_text


_DYNAMIC_WILDCARD_RE = re.compile(r"(?<!_)__[A-Za-z0-9][A-Za-z0-9_\-/ .]*__(?!_)")


@dataclass(frozen=True)
class _Weight:
    strength_level: int = 0
    emphasis: float = 1.0


def looks_like_dynamic_prompt(prompt: str) -> bool:
    """Return True when a prompt appears to contain InvokeAI dynamic prompt syntax."""
    text = prompt or ""
    if _DYNAMIC_WILDCARD_RE.search(text):
        return True
    return _has_dynamic_variant_group(text)


def parse_invoke_prompt(prompt: str) -> list:
    """Parse an Invoke prompt into PromptMosaic tiles.

    The parser keeps Invoke emphasis semantics as tile state instead of literal
    prompt text: trailing + / - becomes strength_level, and numeric
    parenthesized weight becomes emphasis where PromptMosaic can represent it.
    """
    tiles: list = []
    for part in _split_top_level_commas(prompt or ""):
        tiles.extend(_parse_part(part))
    return tiles


def _has_dynamic_variant_group(text: str) -> bool:
    depth = 0
    quote = ""
    escape = False
    group_has_pipe: list[bool] = []

    for ch in text:
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if quote:
            if ch == quote:
                quote = ""
            continue
        if ch in ("'", '"'):
            quote = ch
            continue
        if ch == "{":
            depth += 1
            group_has_pipe.append(False)
            continue
        if ch == "}" and depth > 0:
            has_pipe = group_has_pipe.pop()
            depth -= 1
            if has_pipe:
                return True
            continue
        if ch == "|" and depth > 0:
            group_has_pipe[-1] = True

    return False


def _parse_part(part: str) -> list:
    text = single_line_text(part).strip()
    if not text:
        return []

    text, weight = _consume_suffix_weight(text)
    inner = _unwrap_parenthesized(text)
    if inner is not None:
        children = parse_invoke_prompt(inner)
        if len(children) == 1 and isinstance(children[0], (TagTile, NaturalTextTile)):
            _apply_weight(children[0], weight)
            return children
        if children:
            group = GroupTile(name=_group_name_from_children(children))
            group.tiles = children
            group.strength_level = weight.strength_level
            if abs(weight.emphasis - 1.0) > 1e-4:
                group.name = f"{group.name} [{weight.emphasis:.4g}]"
            return [group]
        text = inner.strip()

    tile = TagTile(tag_name=_unquote(text))
    _apply_weight(tile, weight)
    return [tile] if tile.tag_name else []


def _split_top_level_commas(text: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    quote = ""
    escape = False

    for ch in text:
        if escape:
            buf.append(ch)
            escape = False
            continue
        if ch == "\\" and quote:
            buf.append(ch)
            escape = True
            continue
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            continue
        if ch == "(":
            depth += 1
            buf.append(ch)
            continue
        if ch == ")" and depth > 0:
            depth -= 1
            buf.append(ch)
            continue
        if ch == "," and depth == 0:
            parts.append("".join(buf).strip())
            buf.clear()
            continue
        buf.append(ch)

    parts.append("".join(buf).strip())
    return [p for p in parts if p]


def _consume_suffix_weight(text: str) -> tuple[str, _Weight]:
    text = text.strip()
    if not text:
        return text, _Weight()

    i = len(text)
    while i > 0 and text[i - 1] in "+-":
        i -= 1
    if i < len(text):
        suffix = text[i:]
        if suffix and all(ch == suffix[0] for ch in suffix):
            level = len(suffix) if suffix[0] == "+" else -len(suffix)
            body = text[:i].rstrip()
            if body:
                return body, _Weight(strength_level=level)

    if text.endswith(")"):
        return text, _Weight()

    close = text.rfind(")")
    if close >= 0 and close < len(text) - 1:
        suffix = text[close + 1 :].strip()
        try:
            emphasis = float(suffix)
        except ValueError:
            return text, _Weight()
        return text[: close + 1].strip(), _Weight(emphasis=emphasis)

    return text, _Weight()


def _unwrap_parenthesized(text: str) -> str | None:
    text = text.strip()
    if len(text) < 2 or text[0] != "(" or text[-1] != ")":
        return None
    depth = 0
    quote = ""
    escape = False
    for idx, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if ch == "\\" and quote:
            escape = True
            continue
        if quote:
            if ch == quote:
                quote = ""
            continue
        if ch in ("'", '"'):
            quote = ch
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0 and idx != len(text) - 1:
                return None
    return text[1:-1].strip() if depth == 0 else None


def _apply_weight(tile, weight: _Weight) -> None:
    if hasattr(tile, "strength_level"):
        tile.strength_level += weight.strength_level
    if abs(weight.emphasis - 1.0) > 1e-4 and hasattr(tile, "emphasis"):
        tile.emphasis = weight.emphasis


def _group_name_from_children(children: list) -> str:
    names: list[str] = []
    for child in children[:2]:
        names.append(
            single_line_text(
                getattr(child, "tag_name", "")
                or getattr(child, "display_label", "")
                or getattr(child, "text", "")
                or getattr(child, "name", "")
            )
        )
    name = " / ".join(n for n in names if n).strip()
    return name[:24] if name else "Invoke"


def _unquote(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1].strip()
    return text


