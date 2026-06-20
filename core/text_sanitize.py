from __future__ import annotations

import unicodedata


def single_line_text(value: str) -> str:
    """Remove control characters from text that is rendered in single-line UI."""
    text = str(value or "")
    chars: list[str] = []
    last_space = False
    for ch in text:
        cat = unicodedata.category(ch)
        if cat.startswith("C"):
            if not last_space:
                chars.append(" ")
                last_space = True
            continue
        chars.append(ch)
        last_space = ch.isspace()
    return " ".join("".join(chars).split())
