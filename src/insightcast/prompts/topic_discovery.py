from collections.abc import Mapping, Sequence
from typing import Any

from insightcast.prompts.serialization import compact_json

PROMPT_VERSION = "topic-discovery-v3"
SYSTEM_PROMPT = """Evaluate the provided transcript context and identify distinct important
claims, findings, explanations, consequences, and decisions that can become standalone
InsightCast highlights. When transcript_is_complete is true, evaluate the full transcript.
When deterministic windows are provided, treat times as original source timestamps and
rank only topics supported by the provided source windows. Rank topics by source importance
and viewer value, merge semantic duplicates, and do not rank material merely because it
uses controversy or emotional phrasing. Return only the requested structured output."""


def build_user_prompt(
    *,
    transcript: Sequence[Mapping[str, Any]],
    topic_pool_size: int,
    validation_feedback: str | None,
    transcript_scope: str = "full_transcript",
    transcript_is_complete: bool = True,
    window_plan: Sequence[Mapping[str, Any]] | None = None,
    original_segment_count: int | None = None,
    provided_segment_count: int | None = None,
) -> str:
    payload = {
        "topic_pool_size": topic_pool_size,
        "evaluate_full_transcript": transcript_is_complete,
        "transcript_scope": transcript_scope,
        "transcript_is_complete": transcript_is_complete,
        "window_plan": list(window_plan or []),
        "original_segment_count": original_segment_count,
        "provided_segment_count": provided_segment_count,
        "rank_by_importance": True,
        "require_distinct_topics": True,
        "evaluation_rubric": [
            "importance_to_the_source_argument",
            "standalone_clip_potential",
            "audience_relevance_for_traditional_chinese_viewers",
            "specific_or_counterintuitive_insight",
            "evidence_density",
            "evergreen_value",
            "low_context_dependency",
        ],
        "ranking_instruction": (
            "Rank topics by expected InsightCast highlight value, not by emotional intensity "
            "or how early the idea appears in the source."
        ),
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
    return compact_json(payload)
