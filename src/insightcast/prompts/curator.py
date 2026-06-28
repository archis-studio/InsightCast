from collections.abc import Mapping, Sequence
from typing import Any

from insightcast.prompts.serialization import compact_json

PROMPT_VERSION = "curator-v4"
SYSTEM_PROMPT = """You are the candidate-boundary stage of a knowledge-video curator.
Select the most important distinct knowledge units from the provided transcript context.
Choose continuous source ranges that preserve necessary background, the central claim or
finding, key evidence or reasoning, and a meaningful conclusion. Optimize for standalone
InsightCast highlights with clear viewer payoff, not merely long excerpts around a topic.
Remove greetings, sponsorships, repetition, and tangents when they are not needed for the
argument. Return only the requested structured output."""


def build_user_prompt(
    *,
    transcript: Sequence[Mapping[str, Any]],
    topics: Sequence[Mapping[str, Any]],
    candidate_count: int,
    target_min_duration_seconds: float,
    target_max_duration_seconds: float,
    accepted_min_duration_seconds: float,
    accepted_max_duration_seconds: float,
    final_min_duration_seconds: float,
    final_max_duration_seconds: float,
    validation_feedback: str | None,
    transcript_scope: str = "selected_source_windows_around_ranked_topics",
    transcript_is_complete: bool = False,
) -> str:
    payload = {
        "candidate_count": candidate_count,
        "topics": list(topics),
        "selection_priority": [
            "importance",
            "complete_argument",
            "standalone_viewer_value",
            "information_density",
            "duration_fit",
        ],
        "require_distinct_topics": True,
        "required_arc": [
            "necessary_background",
            "central_claim_or_finding",
            "key_evidence_or_reasoning",
            "meaningful_conclusion",
        ],
        "target_min_duration_seconds": target_min_duration_seconds,
        "target_max_duration_seconds": target_max_duration_seconds,
        "accepted_min_duration_seconds": accepted_min_duration_seconds,
        "accepted_max_duration_seconds": accepted_max_duration_seconds,
        "final_min_duration_seconds": final_min_duration_seconds,
        "final_max_duration_seconds": final_max_duration_seconds,
        "times_are_approximate": True,
        "duration_instruction": (
            "Aim for the target range. Use the accepted range only to preserve a complete "
            "argument. Use the final range only for segment alignment. Do not include "
            "low-value material to reach a duration."
        ),
        "candidate_quality_bar": [
            "clear_standalone_viewer_payoff",
            "specific_insight_or_tension",
            "enough_context_without_long_setup",
            "evidence_or_reasoning_inside_the_clip",
            "minimal_overlap_with_other_candidates",
            "defensible_title_and_summary",
        ],
        "overlap_policy": (
            "Prefer non-overlapping candidates. Only reuse source time when the second "
            "candidate explains a materially different idea and the overlap is necessary."
        ),
        "transcript_scope": transcript_scope,
        "transcript_is_complete": transcript_is_complete,
        "transcript": list(transcript),
        "validation_feedback": validation_feedback,
    }
    return compact_json(payload)
