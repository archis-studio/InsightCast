import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

from insightcast.core.exceptions import InsightCastError
from insightcast.domain.enums import ErrorCode
from insightcast.domain.models import Transcript
from insightcast.infrastructure.transcription.base import (
    build_valid_transcript_segment,
    require_transcript_quality,
)

ModelLoader = Callable[[str, str], Any]


def _load_model(model_size: str, device: str) -> object:
    from faster_whisper import WhisperModel

    return WhisperModel(model_size, device=device)


class LocalWhisperClient:
    def __init__(
        self,
        *,
        model_size: str,
        device: str,
        model_loader: ModelLoader = _load_model,
    ) -> None:
        self.model_size = model_size
        self.device = device
        self.model_loader = model_loader
        self._model: Any | None = None

    @property
    def transcription_provider(self) -> str:
        return "local-whisper"

    @property
    def transcription_model(self) -> str:
        return f"{self.model_size}:{self.device}"

    @property
    def transcription_language(self) -> str:
        return "en"

    @property
    def transcript_schema_version(self) -> int:
        return 1

    async def transcribe(self, audio_path: Path) -> Transcript:
        try:
            if self._model is None:
                self._model = await asyncio.to_thread(
                    self.model_loader,
                    self.model_size,
                    self.device,
                )
            segments_source, info = await asyncio.to_thread(
                self._model.transcribe,
                str(audio_path.expanduser().resolve()),
                language="en",
                vad_filter=True,
            )
            segments_source = list(segments_source)
        except Exception as exc:
            raise InsightCastError(
                ErrorCode.TRANSCRIPTION_FAILED,
                "Local Whisper transcription failed.",
                details={"reason": str(exc)},
                stage="transcribing",
            ) from exc

        language = str(getattr(info, "language", "")).lower()
        if language not in {"en", "english"}:
            raise InsightCastError(
                ErrorCode.UNSUPPORTED_LANGUAGE,
                "Only English source audio is supported.",
                details={"detected_language": language},
                stage="transcribing",
            )
        segments = []
        for index, segment in enumerate(segments_source):
            transcript_segment = build_valid_transcript_segment(
                segment_id=str(getattr(segment, "id", index)),
                start_seconds=float(segment.start),
                end_seconds=float(segment.end),
                text=str(segment.text),
            )
            if transcript_segment is not None:
                segments.append(transcript_segment)
        duration = float(getattr(info, "duration", 0) or 0)
        if segments:
            duration = max(duration, segments[-1].end_seconds)
        return require_transcript_quality(
            Transcript(language="en", duration_seconds=duration, segments=segments)
        )
