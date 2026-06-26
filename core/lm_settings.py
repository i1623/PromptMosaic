from __future__ import annotations

import db.app_db as _app_db


DEFAULT_LM_TEMPERATURE = 0.3
DEFAULT_LM_SEED = 1

DEFAULT_CLASSIFY_PROMPT = (
    "You classify local PromptMosaic prompt/tile items into exactly one category. "
    "Return strict JSON only. Use one category key from the provided list. "
    "Also decide whether the prompt/tile item is NSFW. "
    "Output schema: {\"category\":\"<key>\",\"is_nsfw\":false}."
)


def _get_setting(key: str, default: str = "") -> str:
    row = _app_db.fetchone("SELECT value FROM app_settings WHERE key=?", (key,))
    return row["value"] if row else default


def lm_temperature() -> float:
    try:
        value = float(_get_setting("lm_temperature", str(DEFAULT_LM_TEMPERATURE)))
    except ValueError:
        value = DEFAULT_LM_TEMPERATURE
    return value if value > 0 else DEFAULT_LM_TEMPERATURE


def lm_seed() -> int:
    try:
        value = int(_get_setting("lm_seed", str(DEFAULT_LM_SEED)))
    except ValueError:
        value = DEFAULT_LM_SEED
    return value if value > 0 else DEFAULT_LM_SEED
