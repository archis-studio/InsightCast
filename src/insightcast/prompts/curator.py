import json
from collections.abc import Mapping, Sequence
from typing import Any

PROMPT_VERSION = "curator-v1"
SYSTEM_PROMPT = """You are a knowledge-video curator. Select continuous transcript ranges
that preserve complete idea arcs and useful context. Do not create montages, optimize for
controversy, or require non-overlapping candidates. Return only the requested structured output."""


def build_user_prompt(
    *,
    transcript: Sequence[Mapping[str, Any]],
    candidate_count: int,
    min_duration_minutes: float,
    max_duration_minutes: float,
    validation_feedback: str | None,
) -> str:
    payload = {
        "candidate_count": candidate_count,
        "min_duration_minutes": min_duration_minutes,
        "max_duration_minutes": max_duration_minutes,
        "transcript": list(transcript),
        "validation_feedback": validation_feedback,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)

