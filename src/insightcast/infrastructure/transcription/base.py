import hashlib
import json
from pathlib import Path
from typing import Annotated, Protocol

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from insightcast.domain.models import Transcript

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
