import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from insightcast.core.exceptions import InsightCastError
from insightcast.domain.enums import ErrorCode
from insightcast.engines.source_engine import SourceEngine
from insightcast.infrastructure.ytdlp_client import YouTubeMetadata
from insightcast.storage.file_job_writer import FileJobWriter
from insightcast.storage.video_store import SourceLookup, VideoStore

VIDEO_ID = "abc123DEF_-"
WATCH_URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"
SHARE_URL = f"https://youtu.be/{VIDEO_ID}"
CREATED_AT = datetime(2026, 6, 6, 14, 30, tzinfo=UTC)


class FakeYtDlp:
    def __init__(self) -> None:
        self.metadata_requests: list[str] = []
        self.downloads: list[tuple[str, Path]] = []
        self.fail_download = False
        self.returned_video_id = VIDEO_ID

    async def fetch_metadata(self, url: str) -> YouTubeMetadata:
        self.metadata_requests.append(url)
        return YouTubeMetadata(
            video_id=self.returned_video_id,
            title="Taiwan AI Podcast",
            description="Source description",
            duration_seconds=1200,
            uploader="Channel",
            upload_date="20260606",
            webpage_url=url,
            tags=["AI"],
        )

    async def download_video(self, url: str, destination: Path) -> Path:
        self.downloads.append((url, destination))
        if self.fail_download:
            raise InsightCastError(
                ErrorCode.YOUTUBE_DOWNLOAD_FAILED,
                "injected download failure",
            )
        destination.write_bytes(b"video")
        return destination


class FakeFfmpeg:
    def __init__(self) -> None:
        self.extractions: list[tuple[Path, Path]] = []

    async def extract_audio(self, source: Path, destination: Path) -> Path:
        self.extractions.append((source, destination))
        destination.write_bytes(b"audio")
        return destination


def make_source_engine(
    tmp_path: Path,
    *,
    store_type: type[VideoStore] = VideoStore,
) -> tuple[SourceEngine, FakeYtDlp, FakeFfmpeg, VideoStore]:
    ytdlp = FakeYtDlp()
    ffmpeg = FakeFfmpeg()
    store = store_type(tmp_path / "outputs", FileJobWriter())
    return (
        SourceEngine(ytdlp=ytdlp, ffmpeg=ffmpeg, video_store=store),
        ytdlp,
        ffmpeg,
        store,
    )


def ingest_kwargs(
    *,
    url: str = WATCH_URL,
    output_root: Path | None = None,
) -> dict[str, Any]:
    return {
        "youtube_url": url,
        "job_id": "a1b2c3d4",
        "created_at": CREATED_AT,
        "output_root": output_root,
        "direct": False,
    }


@pytest.mark.asyncio
async def test_source_engine_stores_source_in_video_root_with_sha256_manifest(
    tmp_path: Path,
) -> None:
    engine, ytdlp, ffmpeg, store = make_source_engine(tmp_path)

    result = await engine.ingest(
        **ingest_kwargs(output_root=store.output_root),
    )

    expected_root = store.videos_root / f"{VIDEO_ID}_taiwan-ai-podcast"
    assert result.output_dir == expected_root.resolve()
    assert result.source_artifacts.source_video == (
        expected_root / "source" / "source.mp4"
    ).resolve()
    assert result.source_artifacts.source_audio == (
        expected_root / "source" / "audio.mp3"
    ).resolve()
    assert result.cache_decision == "miss"
    assert ytdlp.metadata_requests == [WATCH_URL]
    assert len(ytdlp.downloads) == 1
    assert len(ffmpeg.extractions) == 1
    lookup = store.load_source(VIDEO_ID)
    assert lookup.status == "hit"
    assert lookup.entry is not None
    assert lookup.entry.manifest.source_fingerprint == sha256(b"video").hexdigest()
    assert lookup.entry.manifest.source_video_path == Path("source/source.mp4")
    assert lookup.entry.manifest.transcription_audio_path == Path("source/audio.mp3")
    assert lookup.entry.manifest.source_video_size == len(b"video")
    assert lookup.entry.manifest.transcription_audio_size == len(b"audio")
    assert not (store.output_root / "source-cache").exists()


@pytest.mark.asyncio
async def test_url_variants_reuse_same_root_and_skip_all_external_work(
    tmp_path: Path,
) -> None:
    engine, ytdlp, ffmpeg, store = make_source_engine(tmp_path)

    first = await engine.ingest(**ingest_kwargs(output_root=store.output_root))
    second = await engine.ingest(
        **ingest_kwargs(url=SHARE_URL, output_root=store.output_root)
    )

    assert second.output_dir == first.output_dir
    assert second.source_artifacts == first.source_artifacts
    assert second.metadata == first.metadata
    assert second.cache_decision == "hit"
    assert ytdlp.metadata_requests == [WATCH_URL]
    assert len(ytdlp.downloads) == 1
    assert len(ffmpeg.extractions) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ["size", "hash"])
async def test_size_or_hash_mismatch_triggers_repair(
    tmp_path: Path,
    corruption: str,
) -> None:
    engine, ytdlp, ffmpeg, store = make_source_engine(tmp_path)
    first = await engine.ingest(**ingest_kwargs(output_root=store.output_root))
    manifest_path = first.output_dir / "source" / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if corruption == "size":
        payload["source_video_size"] += 1
    else:
        payload["source_fingerprint"] = "0" * 64
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    repaired = await engine.ingest(**ingest_kwargs(output_root=store.output_root))

    assert repaired.cache_decision == "repair"
    assert repaired.source_artifacts.source_video.read_bytes() == b"video"
    assert len(ytdlp.metadata_requests) == 2
    assert len(ytdlp.downloads) == 2
    assert len(ffmpeg.extractions) == 2
    assert store.load_source(VIDEO_ID).status == "hit"


@pytest.mark.asyncio
async def test_incomplete_source_entry_repairs(tmp_path: Path) -> None:
    engine, ytdlp, ffmpeg, store = make_source_engine(tmp_path)
    metadata = await ytdlp.fetch_metadata(WATCH_URL)
    video = store.ensure_video(metadata, WATCH_URL)
    source_dir = video.root / "source"
    source_dir.mkdir()
    (source_dir / "source.mp4").write_bytes(b"partial")
    ytdlp.metadata_requests.clear()

    repaired = await engine.ingest(**ingest_kwargs(output_root=store.output_root))

    assert repaired.cache_decision == "repair"
    assert repaired.source_artifacts.source_video.read_bytes() == b"video"
    assert repaired.source_artifacts.source_audio.read_bytes() == b"audio"
    assert len(ytdlp.metadata_requests) == 1
    assert len(ffmpeg.extractions) == 1


@pytest.mark.asyncio
async def test_failed_repair_preserves_previously_valid_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ForceRepairStore(VideoStore):
        force_repair = False

        def load_source(self, video_id: str) -> SourceLookup:
            lookup = super().load_source(video_id)
            if self.force_repair and lookup.status == "hit":
                self.force_repair = False
                return SourceLookup(status="repair")
            return lookup

    engine, ytdlp, _, store = make_source_engine(
        tmp_path,
        store_type=ForceRepairStore,
    )
    first = await engine.ingest(**ingest_kwargs(output_root=store.output_root))
    source_bytes = first.source_artifacts.source_video.read_bytes()
    manifest_bytes = (first.output_dir / "source" / "manifest.json").read_bytes()
    store.force_repair = True
    original_replace = Path.replace

    def fail_staging_promotion(source: Path, target: Path) -> Path:
        if (
            source.name.startswith(".source-")
            and source.name.endswith(".tmp")
            and target.name == "source"
        ):
            raise OSError("injected promotion failure")
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", fail_staging_promotion)

    with pytest.raises(OSError, match="injected promotion failure"):
        await engine.ingest(**ingest_kwargs(output_root=store.output_root))

    assert first.source_artifacts.source_video.read_bytes() == source_bytes
    assert (first.output_dir / "source" / "manifest.json").read_bytes() == manifest_bytes
    assert store.load_source(VIDEO_ID).status == "hit"
    assert list(first.output_dir.glob(".source-*.tmp")) == []
    assert list(first.output_dir.glob(".source-*.backup")) == []
    assert len(ytdlp.downloads) == 2


@pytest.mark.asyncio
async def test_source_engine_rejects_mismatched_metadata_video_id(
    tmp_path: Path,
) -> None:
    engine, ytdlp, ffmpeg, store = make_source_engine(tmp_path)
    ytdlp.returned_video_id = "different01"

    with pytest.raises(InsightCastError) as exc_info:
        await engine.ingest(**ingest_kwargs(output_root=store.output_root))

    assert exc_info.value.error_code == ErrorCode.SOURCE_CACHE_INVALID
    assert store.find_video(VIDEO_ID) is None
    assert ytdlp.downloads == []
    assert ffmpeg.extractions == []


@pytest.mark.asyncio
async def test_symlinked_source_artifact_is_rejected_without_reading_target(
    tmp_path: Path,
) -> None:
    engine, _, _, store = make_source_engine(tmp_path)
    first = await engine.ingest(**ingest_kwargs(output_root=store.output_root))
    external = tmp_path / "external-audio.mp3"
    external.write_bytes(b"external")
    audio = first.source_artifacts.source_audio
    audio.unlink()
    audio.symlink_to(external)

    lookup = store.load_source(VIDEO_ID)

    assert lookup.status == "repair"
    assert lookup.entry is None
    assert external.read_bytes() == b"external"


@pytest.mark.asyncio
async def test_manifest_traversal_is_rejected_without_accessing_external_file(
    tmp_path: Path,
) -> None:
    engine, _, _, store = make_source_engine(tmp_path)
    first = await engine.ingest(**ingest_kwargs(output_root=store.output_root))
    external = tmp_path / "external-video.mp4"
    external.write_bytes(b"external")
    manifest_path = first.output_dir / "source" / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["source_video_path"] = "../../../../external-video.mp4"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    lookup = store.load_source(VIDEO_ID)

    assert lookup.status == "repair"
    assert lookup.entry is None
    assert external.read_bytes() == b"external"


@pytest.mark.asyncio
async def test_failed_initial_download_discards_source_staging(tmp_path: Path) -> None:
    engine, ytdlp, _, store = make_source_engine(tmp_path)
    ytdlp.fail_download = True

    with pytest.raises(InsightCastError, match="injected download failure"):
        await engine.ingest(**ingest_kwargs(output_root=store.output_root))

    video = store.find_video(VIDEO_ID)
    assert video is not None
    assert not (video.root / "source").exists()
    assert list(video.root.glob(".source-*.tmp")) == []
