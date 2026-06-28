from collections.abc import Mapping, Sequence
from typing import Any

from insightcast.prompts.serialization import compact_json

PROMPT_VERSION = "translation-v1"
SYSTEM_PROMPT = """Translate English subtitle items into natural Traditional Chinese for a
Taiwanese audience. Preserve meaning, proper nouns, technical terminology, item IDs, and the
one-to-one item mapping. Avoid overly literal phrasing."""


def build_user_prompt(*, items: Sequence[Mapping[str, Any]]) -> str:
    return compact_json({"items": list(items)})


def build_repair_user_prompt(
    *,
    items: Sequence[Mapping[str, Any]],
    validation_error: Mapping[str, Any],
) -> str:
    return compact_json(
        {
            "instruction": (
                "Repair this subtitle translation batch. Return exactly one translated item "
                "for each source item, preserve item order and segment_id values, and do not "
                "return empty or punctuation-only translations."
            ),
            "validation_error": dict(validation_error),
            "items": list(items),
        }
    )
