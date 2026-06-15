import json
from collections.abc import Mapping, Sequence
from typing import Any

PROMPT_VERSION = "topic-discovery-v1"
SYSTEM_PROMPT = """Evaluate the full transcript and identify distinct important claims,
findings, explanations, consequences, and decisions. Rank topics by importance, merge semantic
duplicates, and do not rank material merely because it uses controversy or emotional phrasing.
Return only the requested structured output."""


def build_user_prompt(
    *,
    transcript: Sequence[Mapping[str, Any]],
    topic_pool_size: int,
    validation_feedback: str | None,
) -> str:
    payload = {
        "topic_pool_size": topic_pool_size,
        "evaluate_full_transcript": True,
        "rank_by_importance": True,
        "require_distinct_topics": True,
        "exclude_low_value_material": [
            "greetings",
            "sponsorships",
            "repetition",
            "anecdotes_without_a_broader_point",
            "setup_without_a_conclusion",
        ],
        "topic_requirements": (
            "Return sequential topic IDs in descending importance. Each topic must include "
            "a label, summary, central claim, importance reason, approximate continuous range, "
            "and importance score."
        ),
        "transcript": list(transcript),
        "validation_feedback": validation_feedback,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
