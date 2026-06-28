import json
from collections.abc import Mapping, Sequence
from typing import Any

from insightcast.domain.models import TranscriptSegment


def compact_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def serialize_transcript_segments_for_prompt(
    segments: Sequence[TranscriptSegment],
) -> list[dict[str, Any]]:
    return [
        {
            "id": segment.segment_id,
            "start": round(segment.start_seconds, 3),
            "end": round(segment.end_seconds, 3),
            "text": segment.text.strip(),
        }
        for segment in segments
    ]
