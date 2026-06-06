from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from insightcast.domain.models import Transcript


class AudioChunk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: Path
    offset_seconds: float = Field(ge=0)


class TranscriptionClient(Protocol):
    async def transcribe(self, audio_path: Path) -> Transcript: ...

