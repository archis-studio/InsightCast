import json
from collections.abc import Mapping, Sequence
from typing import Any

PROMPT_VERSION = "curator-v2"
SYSTEM_PROMPT = """You are a knowledge-video curator. Select continuous transcript ranges
that preserve complete idea arcs and useful context. Do not create montages, optimize for
controversy, or require non-overlapping candidates. Return only the requested structured output."""


def build_user_prompt(
    *,
    transcript: Sequence[Mapping[str, Any]],
    candidate_count: int,
    target_min_duration_seconds: float,
    target_max_duration_seconds: float,
    accepted_min_duration_seconds: float,
    accepted_max_duration_seconds: float,
    validation_feedback: str | None,
) -> str:
    payload = {
        "candidate_count": candidate_count,
        "target_min_duration_seconds": target_min_duration_seconds,
        "target_max_duration_seconds": target_max_duration_seconds,
        "accepted_min_duration_seconds": accepted_min_duration_seconds,
        "accepted_max_duration_seconds": accepted_max_duration_seconds,
        "times_are_approximate": True,
        "prefer_complete_idea_arcs": True,
        "duration_instruction": (
            "Treat times as approximate content selections. Aim for the target range, "
            "preserve complete idea arcs, and use the accepted range only as a fallback."
        ),
        "transcript": list(transcript),
        "validation_feedback": validation_feedback,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
