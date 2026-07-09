from collections.abc import Mapping, Sequence
from typing import Any

from insightcast.prompts.serialization import compact_json

PROMPT_VERSION = "translation-v3"
SYSTEM_PROMPT = """Translate English subtitle items into natural Traditional Chinese for a
Taiwanese audience. Preserve meaning, proper nouns, technical terminology, item IDs, order,
and exact one-to-one item mapping. Return the same number of items as the source. Never merge,
split, omit, add, or reorder items. Every translated text must be readable Traditional Chinese,
not empty, and not punctuation-only. Translate every source item, including short filler,
backchannels, restarts, fragments, and repeated words. If a source item has little semantic
content, still return a brief natural subtitle such as "嗯", "對", "好", or "所以" when
appropriate. Never summarize across items, never combine neighboring items, and never leave a
segment untranslated because it seems minor. Avoid overly literal phrasing."""


def _mapping_contract(items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    segment_ids = [str(item.get("segment_id", "")) for item in items]
    return {
        "item_count": len(items),
        "required_segment_ids": segment_ids,
        "mapping_contract": {
            "preserve_segment_ids_exactly": True,
            "preserve_item_order": True,
            "return_exactly_one_translation_per_source_item": True,
            "do_not_merge_split_omit_add_or_reorder_items": True,
            "translated_text_must_be_readable_traditional_chinese": True,
        },
    }


def build_user_prompt(*, items: Sequence[Mapping[str, Any]]) -> str:
    source_items = list(items)
    return compact_json({**_mapping_contract(source_items), "items": source_items})


def build_repair_user_prompt(
    *,
    items: Sequence[Mapping[str, Any]],
    validation_error: Mapping[str, Any],
) -> str:
    source_items = list(items)
    return compact_json(
        {
            "instruction": (
                "Repair this subtitle translation batch. Return exactly one translated item "
                "for each source item, preserve item order and segment_id values, and do not "
                "return empty or punctuation-only translations."
            ),
            **_mapping_contract(source_items),
            "validation_error": dict(validation_error),
            "items": source_items,
        }
    )
