import json
from collections.abc import Mapping, Sequence
from typing import Any

PROMPT_VERSION = "translation-v1"
SYSTEM_PROMPT = """Translate English subtitle items into natural Traditional Chinese for a
Taiwanese audience. Preserve meaning, proper nouns, technical terminology, item IDs, and the
one-to-one item mapping. Avoid overly literal phrasing."""


def build_user_prompt(*, items: Sequence[Mapping[str, Any]]) -> str:
    return json.dumps({"items": list(items)}, ensure_ascii=False, indent=2)

