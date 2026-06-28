import hashlib
import json
from pathlib import Path
from typing import Annotated, Protocol

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from insightcast.core.exceptions import InsightCastError
from insightcast.domain.enums import ErrorCode
from insightcast.domain.models import Transcript, TranscriptSegment

Sha256Digest = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9A-Fa-f]{64}$"),
]


class AudioChunk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: Path
    offset_seconds: float = Field(ge=0)


class TranscriptionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_fingerprint: Sha256Digest
    provider: str
    model: str
    language: str = "en"
    transcript_schema_version: int = 1


def build_transcript_cache_key(spec: TranscriptionSpec) -> str:
    payload = spec.model_dump(mode="json")
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_valid_transcript_segment(
    *,
    segment_id: object,
    start_seconds: object,
    end_seconds: object,
    text: object,
) -> TranscriptSegment | None:
    normalized_text = str(text or "").strip()
    if not normalized_text:
        return None
    try:
        start = float(start_seconds)
        end = float(end_seconds)
    except (TypeError, ValueError):
        return None
    if end <= start:
        return None
    return TranscriptSegment(
        segment_id=str(segment_id),
        start_seconds=start,
        end_seconds=end,
        text=normalized_text,
    )


def require_transcript_quality(transcript: Transcript) -> Transcript:
    if not transcript.segments:
        raise InsightCastError(
            ErrorCode.TRANSCRIPTION_FAILED,
            "Transcript contains no valid segments.",
            details={
                "reason": "transcript_contains_no_valid_segments",
                "duration_seconds": transcript.duration_seconds,
            },
            stage="transcribing",
        )

    covered_seconds = sum(
        segment.end_seconds - segment.start_seconds for segment in transcript.segments
    )
    if transcript.duration_seconds >= 60 and covered_seconds / transcript.duration_seconds < 0.01:
        raise InsightCastError(
            ErrorCode.TRANSCRIPTION_FAILED,
            "Transcript covers too little of the source audio.",
            details={
                "reason": "transcript_coverage_too_low",
                "covered_seconds": covered_seconds,
                "duration_seconds": transcript.duration_seconds,
            },
            stage="transcribing",
        )
    return transcript


class TranscriptionClient(Protocol):
    @property
    def transcription_provider(self) -> str: ...

    @property
    def transcription_model(self) -> str: ...

    @property
    def transcription_language(self) -> str: ...

    @property
    def transcript_schema_version(self) -> int: ...

    async def transcribe(self, audio_path: Path) -> Transcript: ...
