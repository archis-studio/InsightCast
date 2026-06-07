import asyncio
import json
import shutil
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from threading import Event, Thread
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


async def create_promoted_source(
    store: VideoStore,
    *,
    metadata: YouTubeMetadata | None = None,
    source_bytes: bytes = b"video",
    audio_bytes: bytes = b"audio",
) -> SourceLookup:
    resolved_metadata = metadata or YouTubeMetadata(
        video_id=VIDEO_ID,
        title="Taiwan AI Podcast",
        duration_seconds=1200,
        webpage_url=WATCH_URL,
    )
    async with store.source_transaction(VIDEO_ID) as transaction:
        transaction.ensure_video(resolved_metadata, WATCH_URL)
        staging = transaction.create_staging()
        (staging / "source.mp4").write_bytes(source_bytes)
        (staging / "audio.mp3").write_bytes(audio_bytes)
        transaction.promote(
            staging,
            metadata=resolved_metadata,
            downloaded_at=CREATED_AT,
            audio_extracted_at=CREATED_AT,
        )
        return transaction.load_source()


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
async def test_simultaneous_ingests_share_one_source_transaction(
    tmp_path: Path,
) -> None:
    class PausedYtDlp(FakeYtDlp):
        def __init__(self) -> None:
            super().__init__()
            self.download_started = asyncio.Event()
            self.finish_download = asyncio.Event()

        async def download_video(self, url: str, destination: Path) -> Path:
            self.downloads.append((url, destination))
            self.download_started.set()
            await self.finish_download.wait()
            destination.write_bytes(b"video")
            return destination

    ytdlp = PausedYtDlp()
    ffmpeg = FakeFfmpeg()
    store = VideoStore(tmp_path / "outputs", FileJobWriter())
    engine = SourceEngine(ytdlp=ytdlp, ffmpeg=ffmpeg, video_store=store)

    first_task = asyncio.create_task(
        engine.ingest(**ingest_kwargs(output_root=store.output_root))
    )
    await asyncio.wait_for(ytdlp.download_started.wait(), timeout=5)
    first_staging = ytdlp.downloads[0][1].parent
    second_task = asyncio.create_task(
        engine.ingest(
            **ingest_kwargs(url=SHARE_URL, output_root=store.output_root)
        )
    )
    try:
        await asyncio.sleep(0.1)
        assert first_staging.is_dir()
        assert len(ytdlp.downloads) == 1
    finally:
        ytdlp.finish_download.set()

    first, second = await asyncio.gather(first_task, second_task)

    assert first.source_artifacts == second.source_artifacts
    assert {first.cache_decision, second.cache_decision} == {"miss", "hit"}
    assert len(ytdlp.metadata_requests) == 1
    assert len(ytdlp.downloads) == 1
    assert len(ffmpeg.extractions) == 1
    assert list(first.output_dir.glob(".source-*.tmp")) == []


@pytest.mark.asyncio
async def test_cancelled_source_transaction_waiter_does_not_leak_lock(
    tmp_path: Path,
) -> None:
    store = VideoStore(tmp_path / "outputs", FileJobWriter())
    holder = store.source_transaction(VIDEO_ID)
    await holder.__aenter__()

    async def wait_for_transaction() -> None:
        async with store.source_transaction(VIDEO_ID):
            return None

    waiter = asyncio.create_task(wait_for_transaction())
    await asyncio.sleep(0.05)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    await holder.__aexit__(None, None, None)

    async with asyncio.timeout(1):
        async with store.source_transaction(VIDEO_ID):
            pass


@pytest.mark.asyncio
async def test_source_transaction_enter_failure_releases_same_video_lock(
    tmp_path: Path,
) -> None:
    store = VideoStore(tmp_path / "outputs", FileJobWriter())
    videos_root = store.videos_root
    (videos_root / f"{VIDEO_ID}_one").mkdir(parents=True)
    duplicate = videos_root / f"{VIDEO_ID}_two"
    duplicate.mkdir()

    with pytest.raises(InsightCastError) as exc_info:
        async with store.source_transaction(VIDEO_ID):
            raise AssertionError("duplicate roots should fail before body")

    assert exc_info.value.error_code == ErrorCode.STORAGE_CONFLICT
    duplicate.rmdir()
    (videos_root / f"{VIDEO_ID}_one").rmdir()
    async with asyncio.timeout(1):
        async with store.source_transaction(VIDEO_ID):
            pass


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

        def _load_source_unlocked(self, video_id: str) -> SourceLookup:
            lookup = super()._load_source_unlocked(video_id)
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


def test_create_source_staging_removes_only_crashed_source_staging(
    tmp_path: Path,
) -> None:
    _, _, _, store = make_source_engine(tmp_path)
    metadata = YouTubeMetadata(
        video_id=VIDEO_ID,
        title="Taiwan AI Podcast",
        duration_seconds=1200,
        webpage_url=WATCH_URL,
    )
    video = store.ensure_video(metadata, WATCH_URL)
    stale = video.root / f".source-{'a' * 32}.tmp"
    stale.mkdir()
    (stale / "partial").write_bytes(b"partial")
    short = video.root / ".source-short.tmp"
    short.mkdir()
    symlink_target = tmp_path / "external-staging"
    symlink_target.mkdir()
    symlink = video.root / f".source-{'b' * 32}.tmp"
    symlink.symlink_to(symlink_target, target_is_directory=True)

    async def create_staging() -> Path:
        async with store.source_transaction(VIDEO_ID) as transaction:
            return transaction.create_staging()

    staging = asyncio.run(create_staging())

    assert not stale.exists()
    assert short.is_dir()
    assert symlink.is_symlink()
    assert symlink_target.is_dir()
    assert staging.is_dir()


def test_source_staging_mutation_is_transaction_only(tmp_path: Path) -> None:
    _, _, _, store = make_source_engine(tmp_path)

    assert not hasattr(store, "create_source_staging")
    assert not hasattr(store, "promote_source")
    assert not hasattr(store, "discard_source_staging")


def test_load_source_waits_for_promotion_and_never_observes_partial_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, _, _, store = make_source_engine(tmp_path)

    first = asyncio.run(
        engine.ingest(**ingest_kwargs(output_root=store.output_root))
    )
    metadata = first.metadata.model_copy(update={"title": "Replacement"})
    backup_moved = Event()
    finish_promotion = Event()
    load_finished = Event()
    original_replace = Path.replace
    lookup_results: list[SourceLookup] = []
    thread_errors: list[BaseException] = []

    def pause_after_backup(source: Path, target: Path) -> Path:
        result = original_replace(source, target)
        if source.name == "source" and target.name.endswith(".backup"):
            backup_moved.set()
            if not finish_promotion.wait(timeout=5):
                raise TimeoutError("promotion test did not resume")
        return result

    def promote() -> None:
        try:
            async def run() -> None:
                async with store.source_transaction(VIDEO_ID) as transaction:
                    transaction.ensure_video(metadata, WATCH_URL)
                    staging = transaction.create_staging()
                    (staging / "source.mp4").write_bytes(b"replacement-video")
                    (staging / "audio.mp3").write_bytes(b"replacement-audio")
                    transaction.promote(
                        staging,
                        metadata=metadata,
                        downloaded_at=CREATED_AT,
                        audio_extracted_at=CREATED_AT,
                    )

            asyncio.run(run())
        except BaseException as exc:
            thread_errors.append(exc)

    def load() -> None:
        try:
            lookup_results.append(store.load_source(VIDEO_ID))
        except BaseException as exc:
            thread_errors.append(exc)
        finally:
            load_finished.set()

    monkeypatch.setattr(Path, "replace", pause_after_backup)
    promoter = Thread(target=promote)
    loader = Thread(target=load)
    promoter.start()
    assert backup_moved.wait(timeout=5)
    loader.start()
    try:
        assert not load_finished.wait(timeout=0.1)
    finally:
        finish_promotion.set()
        promoter.join(timeout=5)
        loader.join(timeout=5)

    assert not promoter.is_alive()
    assert not loader.is_alive()
    assert thread_errors == []
    assert len(lookup_results) == 1
    lookup = lookup_results[0]
    assert lookup.status == "hit"
    assert lookup.entry is not None
    assert lookup.entry.source_video.read_bytes() == b"replacement-video"


@pytest.mark.asyncio
async def test_source_transaction_restores_valid_backup_when_source_missing(
    tmp_path: Path,
) -> None:
    _, _, _, store = make_source_engine(tmp_path)
    lookup = await create_promoted_source(store)
    assert lookup.entry is not None
    source_dir = lookup.entry.directory
    backup = source_dir.parent / f".source-{'c' * 32}.backup"
    source_dir.replace(backup)

    recovered = store.load_source(VIDEO_ID)

    assert recovered.status == "hit"
    assert recovered.entry is not None
    assert recovered.entry.source_video.read_bytes() == b"video"
    assert recovered.entry.directory.name == "source"
    assert not backup.exists()


@pytest.mark.asyncio
async def test_source_transaction_removes_backup_when_current_source_is_valid(
    tmp_path: Path,
) -> None:
    _, _, _, store = make_source_engine(tmp_path)
    lookup = await create_promoted_source(store)
    assert lookup.entry is not None
    backup = lookup.entry.root / f".source-{'d' * 32}.backup"
    shutil.copytree(lookup.entry.directory, backup)

    validated = store.load_source(VIDEO_ID)

    assert validated.status == "hit"
    assert validated.entry is not None
    assert validated.entry.directory == lookup.entry.directory
    assert not backup.exists()
