from collections.abc import Mapping, Sequence
from typing import Any

from insightcast.prompts.serialization import compact_json

PROMPT_VERSION = "selection-review-v1"

SYSTEM_PROMPT = """You are the final selection reviewer for long-form knowledge-video clips.
Compare the candidate clips and decide which should be rendered first. Do not rely on
numeric scores alone. Prefer complete, independently understandable knowledge units with
natural starts, natural endings, strong viewer payoff, and low avoidable waste. Adjust
boundaries only when a small shift improves completeness or removes filler. Return only
the requested structured output."""


def build_user_prompt(
    *,
    transcript: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
    target_min_duration_seconds: float,
    target_max_duration_seconds: float,
    final_min_duration_seconds: float,
    final_max_duration_seconds: float,
    source_duration_seconds: float | None = None,
) -> str:
    payload = {
        "source_duration_seconds": source_duration_seconds,
        "goal": (
            "Rank candidates for rendering. Keep all candidate IDs, but assign rank 1 to "
            "the best render choice. Adjust start/end only when needed for natural "
            "boundaries or argument completeness."
        ),
        "transcript_scope": (
            "boundary excerpts only: start and end excerpts for each candidate, not the "
            "full candidate transcript. Use candidate packages for the full argument arc."
        ),
        "target_min_duration_seconds": target_min_duration_seconds,
        "target_max_duration_seconds": target_max_duration_seconds,
        "final_min_duration_seconds": final_min_duration_seconds,
        "final_max_duration_seconds": final_max_duration_seconds,
        "decision_rules": [
            "Numeric candidate scores are secondary; use comparative judgment.",
            "A clip must complete one clear knowledge unit.",
            "The ending should stop after a conclusion, transition, or natural pause.",
            "Do not stop at 8 minutes just because it satisfies the minimum.",
            "Prefer 9-11 minutes when it improves completeness without adding waste.",
            "If the ending cuts off a developing point, extend to the nearest natural ending.",
            "If the opening contains filler, move the start forward to the useful setup.",
            "Do not add low-value material just to increase duration.",
        ],
        "review_each_candidate_for": [
            "standalone_viewer_payoff",
            "argument_completeness",
            "ending_completeness",
            "avoidable_waste",
            "whether_boundary_adjustment_is_needed",
        ],
        "return_requirements": (
            "Return every input candidate exactly once. Ranks must be unique integers from "
            "1 to candidate_count. adjusted_start_seconds and adjusted_end_seconds must be "
            "original source timestamps within the final duration range. In "
            "boundary_adjustment_reason, explain the reason in words and do not write "
            "timestamp ranges such as 'from X to Y'."
        ),
        "candidates": list(candidates),
        "transcript": list(transcript),
    }
    return compact_json(payload)
