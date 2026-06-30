from pathlib import Path
from types import SimpleNamespace

import pytest

from insightcast.core.exceptions import InsightCastError
from insightcast.domain.enums import ErrorCode
from insightcast.infrastructure.transcription.base import (
    AudioChunk,
    TranscriptionSpec,
    build_transcript_cache_key,
)
from insightcast.infrastructure.transcription.local_whisper_client import LocalWhisperClient
from insightcast.infrastructure.transcription.openai_transcription_client import (
    OpenAITranscriptionClient,
    capture_transcription_progress,
)


class FakeTranscriptions:
    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> object:
        file_object = kwargs["file"]
        self.calls.append({**kwargs, "file": Path(file_object.name).name})
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def test_transcript_cache_key_changes_for_identity_inputs() -> None:
    base = TranscriptionSpec(
        source_fingerprint="a" * 64,
        provider="openai",
        model="whisper-1",
    )

    keys = {
        build_transcript_cache_key(base),
        build_transcript_cache_key(base.model_copy(update={"source_fingerprint": "b" * 64})),
        build_transcript_cache_key(base.model_copy(update={"provider": "local"})),
        build_transcript_cache_key(base.model_copy(update={"model": "small:cpu"})),
        build_transcript_cache_key(base.model_copy(update={"language": "ja"})),
        build_transcript_cache_key(base.model_copy(update={"transcript_schema_version": 2})),
    }

    assert len(keys) == 6
    assert all(len(cache_key) == 64 for cache_key in keys)


def test_openai_transcription_identity_uses_provider_model_language_and_schema() -> None:
    client = OpenAITranscriptionClient(FakeTranscriptions([]), model="gpt-4o-mini-transcribe")

    assert client.transcription_provider == "openai"
    assert client.transcription_model == "gpt-4o-mini-transcribe"
    assert client.transcription_language == "en"
    assert client.transcript_schema_version == 1


@pytest.mark.asyncio
async def test_openai_transcription_uses_configured_request_timeout(
    tmp_path: Path,
) -> None:
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"audio")
    transcriptions = FakeTranscriptions(
        [
            SimpleNamespace(
                language="en",
                duration=1,
                segments=[SimpleNamespace(id=0, start=0, end=1, text="Text")],
            )
        ]
    )
    client = OpenAITranscriptionClient(
        transcriptions,
        request_timeout_seconds=240,
        chunker=lambda *_: [AudioChunk(path=audio, offset_seconds=0)],
    )

    await client.transcribe(audio)

    assert transcriptions.calls[0]["timeout"] == 240


def test_local_whisper_identity_includes_model_size_and_device() -> None:
    client = LocalWhisperClient(model_size="small", device="cpu")

    assert client.transcription_provider == "local-whisper"
    assert client.transcription_model == "small:cpu"
    assert client.transcription_language == "en"
    assert client.transcript_schema_version == 1


@pytest.mark.asyncio
async def test_openai_transcription_merges_chunk_segments_with_offsets(tmp_path: Path) -> None:
    first = tmp_path / "part-1.mp3"
    second = tmp_path / "part-2.mp3"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    responses = [
        SimpleNamespace(
            language="english",
            duration=10,
            segments=[SimpleNamespace(id=0, start=1, end=3, text=" First ")],
        ),
        SimpleNamespace(
            language="en",
            duration=8,
            segments=[SimpleNamespace(id=0, start=0.5, end=2, text="Second")],
        ),
    ]
    transcriptions = FakeTranscriptions(responses)
    chunker_calls: list[tuple[Path, int]] = []

    def chunker(path: Path, max_upload_mb: int) -> list[AudioChunk]:
        chunker_calls.append((path, max_upload_mb))
        return [
            AudioChunk(path=first, offset_seconds=0),
            AudioChunk(path=second, offset_seconds=10),
        ]

    client = OpenAITranscriptionClient(
        transcriptions,
        model="whisper-1",
        max_upload_mb=24,
        chunker=chunker,
    )

    transcript = await client.transcribe(tmp_path / "audio.mp3")

    assert chunker_calls == [((tmp_path / "audio.mp3").resolve(), 24)]
    assert transcript.language == "en"
    assert transcript.duration_seconds == 18
    assert [(item.start_seconds, item.end_seconds, item.text) for item in transcript.segments] == [
        (1, 3, "First"),
        (10.5, 12, "Second"),
    ]
    assert all(call["response_format"] == "verbose_json" for call in transcriptions.calls)


@pytest.mark.asyncio
async def test_openai_transcription_skips_empty_segments(tmp_path: Path) -> None:
    chunk = tmp_path / "part.mp3"
    chunk.write_bytes(b"audio")
    transcriptions = FakeTranscriptions(
        [
            SimpleNamespace(
                language="en",
                duration=5,
                segments=[
                    SimpleNamespace(id=0, start=1, end=2, text="Valid"),
                    SimpleNamespace(id=1, start=2, end=3, text="   "),
                    SimpleNamespace(id=2, start=4, end=4, text=""),
                    SimpleNamespace(id=3, start=4, end=4, text="Zero duration"),
                ],
            )
        ]
    )
    client = OpenAITranscriptionClient(
        transcriptions,
        chunker=lambda *_: [AudioChunk(path=chunk, offset_seconds=0)],
    )

    transcript = await client.transcribe(tmp_path / "audio.mp3")

    actual_segments = [
        (item.segment_id, item.start_seconds, item.end_seconds, item.text)
        for item in transcript.segments
    ]

    assert actual_segments == [
        ("0-0", 1, 2, "Valid")
    ]
    assert transcript.duration_seconds == 5


@pytest.mark.asyncio
async def test_openai_transcription_rejects_empty_transcript(tmp_path: Path) -> None:
    chunk = tmp_path / "part.mp3"
    chunk.write_bytes(b"audio")
    transcriptions = FakeTranscriptions(
        [
            SimpleNamespace(
                language="en",
                duration=120,
                segments=[
                    SimpleNamespace(id=0, start=1, end=2, text=" "),
                    SimpleNamespace(id=1, start=3, end=3, text="Zero duration"),
                ],
            )
        ]
    )
    client = OpenAITranscriptionClient(
        transcriptions,
        chunker=lambda *_: [AudioChunk(path=chunk, offset_seconds=0)],
    )

    with pytest.raises(InsightCastError) as exc_info:
        await client.transcribe(tmp_path / "audio.mp3")

    assert exc_info.value.error_code == ErrorCode.TRANSCRIPTION_FAILED
    assert exc_info.value.details["reason"] == "transcript_contains_no_valid_segments"


@pytest.mark.asyncio
async def test_openai_transcription_rejects_sparse_long_transcript(tmp_path: Path) -> None:
    chunk = tmp_path / "part.mp3"
    chunk.write_bytes(b"audio")
    transcriptions = FakeTranscriptions(
        [
            SimpleNamespace(
                language="en",
                duration=200,
                segments=[SimpleNamespace(id=0, start=1, end=2, text="Too little")],
            )
        ]
    )
    client = OpenAITranscriptionClient(
        transcriptions,
        chunker=lambda *_: [AudioChunk(path=chunk, offset_seconds=0)],
    )

    with pytest.raises(InsightCastError) as exc_info:
        await client.transcribe(tmp_path / "audio.mp3")

    assert exc_info.value.error_code == ErrorCode.TRANSCRIPTION_FAILED
    assert exc_info.value.details["reason"] == "transcript_coverage_too_low"


@pytest.mark.asyncio
async def test_openai_transcription_rejects_non_english_audio(tmp_path: Path) -> None:
    chunk = tmp_path / "part.mp3"
    chunk.write_bytes(b"audio")
    transcriptions = FakeTranscriptions(
        [SimpleNamespace(language="japanese", duration=1, segments=[])]
    )
    client = OpenAITranscriptionClient(
        transcriptions,
        chunker=lambda *_: [AudioChunk(path=chunk, offset_seconds=0)],
    )

    with pytest.raises(InsightCastError) as exc_info:
        await client.transcribe(tmp_path / "audio.mp3")

    assert exc_info.value.error_code == ErrorCode.UNSUPPORTED_LANGUAGE


@pytest.mark.asyncio
async def test_openai_transcription_retries_failed_chunk(tmp_path: Path) -> None:
    chunk = tmp_path / "part.mp3"
    chunk.write_bytes(b"audio")
    transcriptions = FakeTranscriptions(
        [
            TimeoutError("Request timed out."),
            SimpleNamespace(
                language="en",
                duration=4,
                segments=[SimpleNamespace(id=0, start=1, end=2, text="Recovered")],
            ),
        ]
    )
    client = OpenAITranscriptionClient(
        transcriptions,
        max_attempts=2,
        chunker=lambda *_: [AudioChunk(path=chunk, offset_seconds=0)],
    )

    transcript = await client.transcribe(tmp_path / "audio.mp3")

    assert [call["file"] for call in transcriptions.calls] == ["part.mp3", "part.mp3"]
    assert transcript.segments[0].text == "Recovered"


@pytest.mark.asyncio
async def test_openai_transcription_resumes_from_completed_chunk_checkpoint(
    tmp_path: Path,
) -> None:
    first = tmp_path / "part-1.mp3"
    second = tmp_path / "part-2.mp3"
    first.write_bytes(b"one")
    second.write_bytes(b"two")

    def chunker(path: Path, max_upload_mb: int) -> list[AudioChunk]:
        return [
            AudioChunk(path=first, offset_seconds=0),
            AudioChunk(path=second, offset_seconds=10),
        ]

    checkpoint_root = tmp_path / "checkpoints"
    first_run = OpenAITranscriptionClient(
        FakeTranscriptions(
            [
                SimpleNamespace(
                    language="en",
                    duration=6,
                    segments=[SimpleNamespace(id=0, start=1, end=2, text="First")],
                ),
                TimeoutError("Request timed out."),
            ]
        ),
        max_attempts=1,
        checkpoint_root=checkpoint_root,
        chunker=chunker,
    )

    with pytest.raises(InsightCastError) as exc_info:
        await first_run.transcribe(tmp_path / "audio.mp3")

    assert exc_info.value.error_code == ErrorCode.TRANSCRIPTION_FAILED
    assert exc_info.value.details["chunk_index"] == 1
    assert Path(str(exc_info.value.details["resume_checkpoint"])).exists() is False

    second_transcriptions = FakeTranscriptions(
        [
            SimpleNamespace(
                language="en",
                duration=5,
                segments=[SimpleNamespace(id=0, start=0.5, end=1.5, text="Second")],
            ),
        ]
    )
    second_run = OpenAITranscriptionClient(
        second_transcriptions,
        max_attempts=1,
        checkpoint_root=checkpoint_root,
        chunker=chunker,
    )

    transcript = await second_run.transcribe(tmp_path / "audio.mp3")

    assert [call["file"] for call in second_transcriptions.calls] == ["part-2.mp3"]
    assert [(item.start_seconds, item.end_seconds, item.text) for item in transcript.segments] == [
        (1, 2, "First"),
        (10.5, 11.5, "Second"),
    ]


@pytest.mark.asyncio
async def test_openai_transcription_emits_machine_readable_progress_events(
    tmp_path: Path,
) -> None:
    first = tmp_path / "part-1.mp3"
    second = tmp_path / "part-2.mp3"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    transcriptions = FakeTranscriptions(
        [
            TimeoutError("Request timed out."),
            SimpleNamespace(
                language="en",
                duration=2,
                segments=[SimpleNamespace(id=0, start=0, end=1, text="First")],
            ),
            SimpleNamespace(
                language="en",
                duration=3,
                segments=[SimpleNamespace(id=0, start=0, end=1, text="Second")],
            ),
        ]
    )
    events: list[dict[str, object]] = []
    client = OpenAITranscriptionClient(
        transcriptions,
        max_attempts=2,
        chunker=lambda *_: [
            AudioChunk(path=first, offset_seconds=0),
            AudioChunk(path=second, offset_seconds=10),
        ],
    )

    with capture_transcription_progress(events.append):
        await client.transcribe(tmp_path / "audio.mp3")

    assert [
        (event["event"], event.get("chunk_index"), event.get("attempt"))
        for event in events
    ] == [
        ("planned", None, None),
        ("started", 0, 1),
        ("failed", 0, 1),
        ("started", 0, 2),
        ("completed", 0, 2),
        ("started", 1, 1),
        ("completed", 1, 1),
        ("completed_all", None, None),
    ]
    assert events[0]["chunk_count"] == 2
    for event in events:
        if event["event"] in {"started", "failed", "completed"}:
            assert event["chunk_count"] == 2
    assert events[-1]["processed_chunks"] == 2


@pytest.mark.asyncio
async def test_local_whisper_loads_model_lazily_and_maps_segments(tmp_path: Path) -> None:
    loads: list[tuple[str, str]] = []

    class FakeModel:
        def transcribe(self, path: str, **kwargs: object) -> tuple[list[object], object]:
            assert path == str((tmp_path / "audio.mp3").resolve())
            assert kwargs["language"] == "en"
            return (
                [SimpleNamespace(id=7, start=2, end=4, text=" Local ")],
                SimpleNamespace(language="en", duration=5),
            )

    def loader(model_size: str, device: str) -> FakeModel:
        loads.append((model_size, device))
        return FakeModel()

    client = LocalWhisperClient(model_size="small", device="cpu", model_loader=loader)
    assert loads == []

    transcript = await client.transcribe(tmp_path / "audio.mp3")

    assert loads == [("small", "cpu")]
    assert transcript.segments[0].text == "Local"


@pytest.mark.asyncio
async def test_local_whisper_skips_invalid_segments(tmp_path: Path) -> None:
    class FakeModel:
        def transcribe(self, path: str, **kwargs: object) -> tuple[list[object], object]:
            return (
                [
                    SimpleNamespace(id=0, start=1, end=2, text="Valid"),
                    SimpleNamespace(id=1, start=2, end=3, text=" "),
                    SimpleNamespace(id=2, start=4, end=4, text="Zero duration"),
                ],
                SimpleNamespace(language="en", duration=5),
            )

    client = LocalWhisperClient(
        model_size="small",
        device="cpu",
        model_loader=lambda *_: FakeModel(),
    )

    transcript = await client.transcribe(tmp_path / "audio.mp3")

    assert [
        (item.segment_id, item.start_seconds, item.end_seconds, item.text)
        for item in transcript.segments
    ] == [("0", 1, 2, "Valid")]
