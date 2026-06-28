import asyncio
import json
import logging
import subprocess
import time
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from insightcast.core.exceptions import InsightCastError
from insightcast.domain.enums import ErrorCode
from insightcast.domain.models import Transcript, TranscriptSegment
from insightcast.infrastructure.transcription.base import (
    AudioChunk,
    build_valid_transcript_segment,
    require_transcript_quality,
)

Chunker = Callable[[Path, int], list[AudioChunk]]
ProgressSink = Callable[[dict[str, Any]], None]
CHECKPOINT_SCHEMA_VERSION = 1
LOGGER = logging.getLogger(__name__)
_PROGRESS_SINK: ContextVar[ProgressSink | None] = ContextVar(
    "transcription_progress_sink",
    default=None,
)


@contextmanager
def capture_transcription_progress(sink: ProgressSink) -> Any:
    token = _PROGRESS_SINK.set(sink)
    try:
        yield
    finally:
        _PROGRESS_SINK.reset(token)


def emit_transcription_progress(event: str, **fields: Any) -> None:
    sink = _PROGRESS_SINK.get()
    if sink is not None:
        sink({"event": event, **fields})


def _value(item: object, name: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def split_audio_for_upload(audio_path: Path, max_upload_mb: int) -> list[AudioChunk]:
    path = audio_path.expanduser().resolve()
    max_bytes = max_upload_mb * 1024 * 1024
    if path.stat().st_size <= max_bytes:
        return [AudioChunk(path=path, offset_seconds=0)]

    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {probe.stderr[-1000:]}")
    duration = float(probe.stdout.strip())
    segment_seconds = max(30, int(duration * max_bytes / path.stat().st_size * 0.9))
    chunk_dir = path.parent / f"{path.stem}-chunks"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    for stale_chunk in chunk_dir.glob("chunk-*.mp3"):
        stale_chunk.unlink()
    pattern = chunk_dir / "chunk-%04d.mp3"
    split = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(path),
            "-f",
            "segment",
            "-segment_time",
            str(segment_seconds),
            "-c",
            "copy",
            str(pattern),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if split.returncode != 0:
        raise RuntimeError(f"ffmpeg chunking failed: {split.stderr[-1000:]}")
    paths = sorted(chunk_dir.glob("chunk-*.mp3"))
    if not paths or any(chunk.stat().st_size > max_bytes for chunk in paths):
        raise RuntimeError("audio chunks could not be kept below the upload limit")
    return [
        AudioChunk(path=chunk, offset_seconds=index * segment_seconds)
        for index, chunk in enumerate(paths)
    ]


class OpenAITranscriptionClient:
    def __init__(
        self,
        transcriptions: Any,
        *,
        model: str = "whisper-1",
        max_upload_mb: int = 24,
        max_attempts: int = 3,
        retry_sleep_seconds: float = 0,
        checkpoint_root: Path | None = None,
        chunker: Chunker = split_audio_for_upload,
    ) -> None:
        self.transcriptions = transcriptions
        self.model = model
        self.max_upload_mb = max_upload_mb
        self.max_attempts = max(1, max_attempts)
        self.retry_sleep_seconds = max(0, retry_sleep_seconds)
        self.checkpoint_root = checkpoint_root
        self.chunker = chunker

    @property
    def transcription_provider(self) -> str:
        return "openai"

    @property
    def transcription_model(self) -> str:
        return self.model

    @property
    def transcription_language(self) -> str:
        return "en"

    @property
    def transcript_schema_version(self) -> int:
        return 1

    async def transcribe(self, audio_path: Path) -> Transcript:
        resolved_path = audio_path.expanduser().resolve()
        try:
            chunks = await asyncio.to_thread(self.chunker, resolved_path, self.max_upload_mb)
            self._emit_progress(
                "planned",
                audio_path=str(resolved_path),
                chunk_count=len(chunks),
                max_upload_mb=self.max_upload_mb,
                total_chunk_bytes=sum(chunk.path.stat().st_size for chunk in chunks),
            )
            checkpoint_dir = self._checkpoint_dir(resolved_path)
            responses = [
                await asyncio.to_thread(
                    self._load_or_transcribe_chunk,
                    chunk,
                    chunk_index,
                    checkpoint_dir / f"chunk-{chunk_index:04d}.json",
                )
                for chunk_index, chunk in enumerate(chunks)
            ]
        except InsightCastError:
            raise
        except Exception as exc:
            raise InsightCastError(
                ErrorCode.TRANSCRIPTION_FAILED,
                "OpenAI transcription failed.",
                details={"reason": str(exc)},
                stage="transcribing",
            ) from exc

        segments: list[TranscriptSegment] = []
        duration_seconds = 0.0
        processed_chunks = 0
        for chunk_index, (chunk, response) in enumerate(zip(chunks, responses, strict=True)):
            language = str(_value(response, "language", "")).lower()
            if language not in {"en", "english"}:
                raise InsightCastError(
                    ErrorCode.UNSUPPORTED_LANGUAGE,
                    "Only English source audio is supported.",
                    details={"detected_language": language},
                    stage="transcribing",
                )
            response_duration = float(_value(response, "duration", 0) or 0)
            duration_seconds = max(duration_seconds, chunk.offset_seconds + response_duration)
            for segment in _value(response, "segments", []) or []:
                start = chunk.offset_seconds + float(_value(segment, "start"))
                end = chunk.offset_seconds + float(_value(segment, "end"))
                transcript_segment = build_valid_transcript_segment(
                    segment_id=f"{chunk_index}-{_value(segment, 'id', len(segments))}",
                    start_seconds=start,
                    end_seconds=end,
                    text=_value(segment, "text", ""),
                )
                if transcript_segment is None:
                    continue
                duration_seconds = max(duration_seconds, end)
                segments.append(transcript_segment)
            processed_chunks += 1
        self._emit_progress(
            "completed_all",
            audio_path=str(resolved_path),
            chunk_count=len(chunks),
            processed_chunks=processed_chunks,
            segment_count=len(segments),
            duration_seconds=duration_seconds,
        )
        return require_transcript_quality(
            Transcript(
                language="en",
                duration_seconds=duration_seconds,
                segments=segments,
            )
        )

    def _checkpoint_dir(self, audio_path: Path) -> Path:
        if self.checkpoint_root is not None:
            return self.checkpoint_root.expanduser().resolve()
        model_slug = "".join(
            character if character.isalnum() or character in {"-", "_"} else "-"
            for character in self.model
        ).strip("-")
        return audio_path.parent / f"{audio_path.stem}-transcription-checkpoints" / model_slug

    def _load_or_transcribe_chunk(
        self,
        chunk: AudioChunk,
        chunk_index: int,
        checkpoint_path: Path,
    ) -> dict[str, Any]:
        checkpoint = self._load_chunk_checkpoint(chunk, checkpoint_path)
        if checkpoint is not None:
            self._emit_progress(
                "reused",
                chunk_index=chunk_index,
                chunk_path=str(chunk.path),
                chunk_bytes=chunk.path.stat().st_size,
                offset_seconds=chunk.offset_seconds,
                checkpoint=str(checkpoint_path),
            )
            LOGGER.info(
                "transcription_chunk_reused chunk_index=%s chunk_path=%s checkpoint=%s",
                chunk_index,
                chunk.path,
                checkpoint_path,
            )
            return checkpoint

        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                self._emit_progress(
                    "started",
                    chunk_index=chunk_index,
                    chunk_path=str(chunk.path),
                    chunk_bytes=chunk.path.stat().st_size,
                    offset_seconds=chunk.offset_seconds,
                    attempt=attempt,
                    max_attempts=self.max_attempts,
                    checkpoint=str(checkpoint_path),
                )
                response = self._transcribe_chunk(chunk.path)
                checkpoint = self._chunk_checkpoint(chunk, response)
                self._write_chunk_checkpoint(checkpoint_path, checkpoint)
                self._emit_progress(
                    "completed",
                    chunk_index=chunk_index,
                    chunk_path=str(chunk.path),
                    chunk_bytes=chunk.path.stat().st_size,
                    offset_seconds=chunk.offset_seconds,
                    attempt=attempt,
                    checkpoint=str(checkpoint_path),
                    segment_count=len(checkpoint["segments"]),
                    duration_seconds=checkpoint["duration"],
                )
                LOGGER.info(
                    "transcription_chunk_completed chunk_index=%s chunk_path=%s "
                    "attempt=%s checkpoint=%s",
                    chunk_index,
                    chunk.path,
                    attempt,
                    checkpoint_path,
                )
                return checkpoint
            except InsightCastError:
                raise
            except Exception as exc:
                last_error = exc
                self._emit_progress(
                    "failed",
                    chunk_index=chunk_index,
                    chunk_path=str(chunk.path),
                    chunk_bytes=chunk.path.stat().st_size,
                    offset_seconds=chunk.offset_seconds,
                    attempt=attempt,
                    max_attempts=self.max_attempts,
                    checkpoint=str(checkpoint_path),
                    error=repr(exc),
                )
                LOGGER.warning(
                    "transcription_chunk_failed chunk_index=%s chunk_path=%s "
                    "attempt=%s max_attempts=%s error=%r",
                    chunk_index,
                    chunk.path,
                    attempt,
                    self.max_attempts,
                    exc,
                )
                if attempt < self.max_attempts and self.retry_sleep_seconds > 0:
                    time.sleep(self.retry_sleep_seconds)

        assert last_error is not None
        raise InsightCastError(
            ErrorCode.TRANSCRIPTION_FAILED,
            "OpenAI transcription failed.",
            details={
                "reason": str(last_error),
                "chunk_index": chunk_index,
                "chunk_path": str(chunk.path),
                "attempts": self.max_attempts,
                "resume_checkpoint": str(checkpoint_path),
            },
            stage="transcribing",
        ) from last_error

    @staticmethod
    def _emit_progress(event: str, **fields: Any) -> None:
        emit_transcription_progress(event, **fields)

    def _chunk_checkpoint(self, chunk: AudioChunk, response: object) -> dict[str, Any]:
        return {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "model": self.model,
            "chunk_path_name": chunk.path.name,
            "chunk_size": chunk.path.stat().st_size,
            "offset_seconds": chunk.offset_seconds,
            "language": str(_value(response, "language", "")),
            "duration": float(_value(response, "duration", 0) or 0),
            "segments": [
                {
                    "id": _value(segment, "id", index),
                    "start": float(_value(segment, "start")),
                    "end": float(_value(segment, "end")),
                    "text": str(_value(segment, "text", "")).strip(),
                }
                for index, segment in enumerate(_value(response, "segments", []) or [])
            ],
        }

    def _load_chunk_checkpoint(
        self,
        chunk: AudioChunk,
        checkpoint_path: Path,
    ) -> dict[str, Any] | None:
        if not checkpoint_path.exists():
            return None
        try:
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(checkpoint, dict):
            return None
        expected = {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "model": self.model,
            "chunk_path_name": chunk.path.name,
            "chunk_size": chunk.path.stat().st_size,
            "offset_seconds": chunk.offset_seconds,
        }
        for key, value in expected.items():
            if checkpoint.get(key) != value:
                return None
        return checkpoint

    @staticmethod
    def _write_chunk_checkpoint(checkpoint_path: Path, checkpoint: dict[str, Any]) -> None:
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = checkpoint_path.with_name(f"{checkpoint_path.name}.tmp")
        temporary_path.write_text(
            json.dumps(checkpoint, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary_path.replace(checkpoint_path)

    def _transcribe_chunk(self, chunk_path: Path) -> object:
        with chunk_path.open("rb") as audio:
            return self.transcriptions.create(
                file=audio,
                model=self.model,
                language="en",
                response_format="verbose_json",
                timestamp_granularities=["segment"],
            )
