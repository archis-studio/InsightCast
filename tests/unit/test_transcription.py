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
)


class FakeTranscriptions:
    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> object:
        file_object = kwargs["file"]
        self.calls.append({**kwargs, "file": Path(file_object.name).name})
        return self.responses.pop(0)


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
