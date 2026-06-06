import asyncio
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from insightcast.core.exceptions import InsightCastError
from insightcast.domain.enums import ErrorCode
from insightcast.domain.models import Transcript, TranscriptSegment
from insightcast.infrastructure.transcription.base import AudioChunk

Chunker = Callable[[Path, int], list[AudioChunk]]


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
        chunker: Chunker = split_audio_for_upload,
    ) -> None:
        self.transcriptions = transcriptions
        self.model = model
        self.max_upload_mb = max_upload_mb
        self.chunker = chunker

    async def transcribe(self, audio_path: Path) -> Transcript:
        resolved_path = audio_path.expanduser().resolve()
        try:
            chunks = await asyncio.to_thread(self.chunker, resolved_path, self.max_upload_mb)
            responses = [
                await asyncio.to_thread(self._transcribe_chunk, chunk.path) for chunk in chunks
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
        for chunk_index, (chunk, response) in enumerate(
            zip(chunks, responses, strict=True)
        ):
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
                duration_seconds = max(duration_seconds, end)
                segments.append(
                    TranscriptSegment(
                        segment_id=f"{chunk_index}-{_value(segment, 'id', len(segments))}",
                        start_seconds=start,
                        end_seconds=end,
                        text=str(_value(segment, "text", "")).strip(),
                    )
                )
        return Transcript(
            language="en",
            duration_seconds=duration_seconds,
            segments=segments,
        )

    def _transcribe_chunk(self, chunk_path: Path) -> object:
        with chunk_path.open("rb") as audio:
            return self.transcriptions.create(
                file=audio,
                model=self.model,
                language="en",
                response_format="verbose_json",
                timestamp_granularities=["segment"],
            )

