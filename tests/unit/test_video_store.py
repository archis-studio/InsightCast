import json
from concurrent.futures import ThreadPoolExecutor
from multiprocessing import Event, Queue, get_context
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest

from insightcast.core.exceptions import InsightCastError
from insightcast.domain.enums import ErrorCode
from insightcast.domain.models import Transcript, TranscriptSegment
from insightcast.infrastructure.transcription.base import TranscriptionSpec
from insightcast.infrastructure.ytdlp_client import YouTubeMetadata
from insightcast.storage.file_job_writer import FileJobWriter
from insightcast.storage.manifests import ManifestState, TranscriptManifest, VideoManifest
from insightcast.storage.video_store import VideoStore

VIDEO_ID = "abc123DEF_-"
ORIGINAL_URL = f"https://youtu.be/{VIDEO_ID}"
SHARE_URL = f"https://www.youtube.com/watch?v={VIDEO_ID}&feature=share"


def _ensure_video_process(
    output_root: str,
    title: str,
    start: Event,
    results: Queue,
) -> None:
    start.wait()
    try:
        entry = VideoStore(Path(output_root), FileJobWriter()).ensure_video(
            metadata(title=title),
            ORIGINAL_URL,
        )
    except BaseException as exc:
        results.put(("error", repr(exc)))
    else:
        results.put(("ok", str(entry.root)))


def _write_transcript_process(
    output_root: str,
    text: str,
    start: Event,
    results: Queue,
) -> None:
    start.wait()
    try:
        entry = VideoStore(Path(output_root), FileJobWriter()).write_transcript(
            VIDEO_ID,
            transcription_spec(),
            transcript(text),
        )
    except BaseException as exc:
        results.put(("error", repr(exc)))
    else:
        results.put(("ok", entry.transcript.segments[0].text, entry.manifest.transcript_id))


def metadata(
    *,
    title: str = "Original Title",
    uploader: str | None = "Channel",
    upload_date: str | None = "20260606",
) -> YouTubeMetadata:
    return YouTubeMetadata(
        video_id=VIDEO_ID,
        title=title,
        description="Description",
        duration_seconds=600,
        uploader=uploader,
        upload_date=upload_date,
        webpage_url=f"https://www.youtube.com/watch?v={VIDEO_ID}",
        tags=[],
    )


def transcript(text: str = "Transcript") -> Transcript:
    return Transcript(
        language="en",
        duration_seconds=10,
        segments=[
            TranscriptSegment(
                segment_id="s1",
                start_seconds=0,
                end_seconds=10,
                text=text,
            )
        ],
    )


def transcription_spec(**updates: object) -> TranscriptionSpec:
    values = {
        "source_fingerprint": "a" * 64,
        "provider": "openai",
        "model": "whisper-1",
    }
    values.update(updates)
    return TranscriptionSpec(**values)


def test_video_store_resolves_output_and_videos_roots(tmp_path: Path) -> None:
    store = VideoStore(tmp_path / "parent" / ".." / "outputs", FileJobWriter())

    assert store.output_root == (tmp_path / "outputs").resolve()
    assert store.videos_root == store.output_root / "videos"


def test_video_store_rejects_external_videos_root_symlink_before_writes(
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "outputs"
    external = tmp_path / "external"
    output_root.mkdir()
    external.mkdir()
    (output_root / "videos").symlink_to(external, target_is_directory=True)

    with pytest.raises(InsightCastError) as error:
        VideoStore(output_root, FileJobWriter())

    assert error.value.error_code == ErrorCode.ARTIFACT_PATH_INVALID
    assert list(external.iterdir()) == []


def test_matching_video_roots_validates_exact_prefix_and_returns_sorted_directories(
    tmp_path: Path,
) -> None:
    store = VideoStore(tmp_path / "outputs", FileJobWriter())
    videos = store.videos_root
    (videos / f"{VIDEO_ID}_zeta").mkdir(parents=True)
    (videos / f"{VIDEO_ID}_alpha").mkdir()
    (videos / f"{VIDEO_ID}extra_wrong").mkdir()
    (videos / f"{VIDEO_ID}_file").write_text("not a directory", encoding="utf-8")
    (store.output_root / f"{VIDEO_ID}_legacy-job").mkdir()
    (store.output_root / "jobs" / f"{VIDEO_ID}_job").mkdir(parents=True)

    assert store.matching_video_roots(VIDEO_ID) == [
        (videos / f"{VIDEO_ID}_alpha").resolve(),
        (videos / f"{VIDEO_ID}_zeta").resolve(),
    ]

    with pytest.raises(InsightCastError):
        store.matching_video_roots("../outside")


def test_find_video_returns_none_when_managed_root_is_missing(tmp_path: Path) -> None:
    store = VideoStore(tmp_path / "outputs", FileJobWriter())
    (store.output_root / f"{VIDEO_ID}_legacy-job").mkdir(parents=True)
    (store.output_root / "jobs" / f"{VIDEO_ID}_job").mkdir(parents=True)

    assert store.find_video(VIDEO_ID) is None


def test_matching_video_roots_ignores_external_directory_symlink(tmp_path: Path) -> None:
    store = VideoStore(tmp_path / "outputs", FileJobWriter())
    external = tmp_path / f"{VIDEO_ID}_external"
    external.mkdir()
    store.videos_root.mkdir(parents=True)
    (store.videos_root / f"{VIDEO_ID}_linked").symlink_to(
        external,
        target_is_directory=True,
    )

    assert store.matching_video_roots(VIDEO_ID) == []
    assert store.find_video(VIDEO_ID) is None


def test_video_store_reuses_root_by_video_id_when_title_changes(tmp_path: Path) -> None:
    store = VideoStore(tmp_path / "outputs", FileJobWriter())

    first = store.ensure_video(metadata(), ORIGINAL_URL)
    second = store.ensure_video(
        metadata(title="Renamed Title", uploader="New Channel", upload_date="20260607"),
        SHARE_URL,
    )

    assert first.root.name == f"{VIDEO_ID}_original-title"
    assert second.root == first.root
    assert not (store.videos_root / f"{VIDEO_ID}_renamed-title").exists()
    assert second.manifest.title == "Renamed Title"
    assert second.manifest.uploader == "New Channel"
    assert second.manifest.upload_date == "20260607"
    assert second.manifest.original_youtube_url == SHARE_URL
    assert second.manifest.normalized_youtube_url == (
        f"https://www.youtube.com/watch?v={VIDEO_ID}"
    )
    assert second.manifest.first_seen_at == first.manifest.first_seen_at
    assert second.manifest.last_seen_at >= first.manifest.last_seen_at
    assert second.manifest.source_manifest_path == Path("source/manifest.json")

    persisted = VideoManifest.model_validate_json(
        (first.root / "video.json").read_text(encoding="utf-8")
    )
    assert persisted == second.manifest
    assert not (first.root / "video.json.tmp").exists()


def test_find_video_returns_typed_entry_for_one_root(tmp_path: Path) -> None:
    store = VideoStore(tmp_path / "outputs", FileJobWriter())
    created = store.ensure_video(metadata(), ORIGINAL_URL)

    found = VideoStore(store.output_root, FileJobWriter()).find_video(VIDEO_ID)

    assert found is not None
    assert found.root == created.root
    assert isinstance(found.manifest, VideoManifest)
    assert found.manifest == created.manifest


def test_video_store_writes_and_finds_ready_transcript_by_cache_key(
    tmp_path: Path,
) -> None:
    store = VideoStore(tmp_path / "outputs", FileJobWriter())
    store.ensure_video(metadata(), ORIGINAL_URL)
    spec = transcription_spec()

    entry = store.write_transcript(VIDEO_ID, spec, transcript("Cached"))
    found = store.find_ready_transcript(VIDEO_ID, spec)

    assert found is not None
    assert found.directory == entry.directory
    assert found.manifest.cache_key == entry.manifest.cache_key
    assert found.manifest.transcript_id == f"tx-{entry.manifest.cache_key[:12]}"
    assert found.manifest.state is ManifestState.READY
    assert found.transcript.segments[0].text == "Cached"
    assert entry.transcript_path == entry.directory / "transcript.json"
    assert entry.manifest_path == entry.directory / "manifest.json"


def test_video_store_write_transcript_returns_existing_ready_without_replacing(
    tmp_path: Path,
) -> None:
    store = VideoStore(tmp_path / "outputs", FileJobWriter())
    store.ensure_video(metadata(), ORIGINAL_URL)
    spec = transcription_spec()
    first = store.write_transcript(VIDEO_ID, spec, transcript("Original"))

    second = store.write_transcript(VIDEO_ID, spec, transcript("Replacement"))
    found = store.find_ready_transcript(VIDEO_ID, spec)

    assert found is not None
    assert second == first
    assert found.directory == first.directory
    assert found.manifest.created_at == first.manifest.created_at
    assert found.transcript.segments[0].text == "Original"


def test_video_store_write_transcript_skips_invalid_matching_key_directory(
    tmp_path: Path,
) -> None:
    store = VideoStore(tmp_path / "outputs", FileJobWriter())
    store.ensure_video(metadata(), ORIGINAL_URL)
    spec = transcription_spec()
    first = store.write_transcript(VIDEO_ID, spec, transcript("Original"))
    first.transcript_path.unlink()

    second = store.write_transcript(VIDEO_ID, spec, transcript("Replacement"))

    assert first.directory.is_dir()
    assert not first.transcript_path.exists()
    assert second.directory != first.directory
    assert second.manifest.transcript_id == f"{first.manifest.transcript_id}-1"
    assert second.manifest.cache_key == first.manifest.cache_key
    assert second.transcript.segments[0].text == "Replacement"
    assert store.find_ready_transcript(VIDEO_ID, spec) == second


def test_video_store_write_transcript_skips_corrupt_matching_key_directory(
    tmp_path: Path,
) -> None:
    store = VideoStore(tmp_path / "outputs", FileJobWriter())
    store.ensure_video(metadata(), ORIGINAL_URL)
    spec = transcription_spec()
    first = store.write_transcript(VIDEO_ID, spec, transcript("Original"))
    first.transcript_path.write_text("{bad json", encoding="utf-8")

    second = store.write_transcript(VIDEO_ID, spec, transcript("Replacement"))

    assert first.directory.is_dir()
    assert first.transcript_path.read_text(encoding="utf-8") == "{bad json"
    assert second.directory != first.directory
    assert second.manifest.transcript_id == f"{first.manifest.transcript_id}-1"
    assert store.find_ready_transcript(VIDEO_ID, spec) == second


def test_video_store_persists_and_matches_transcript_schema_version(
    tmp_path: Path,
) -> None:
    store = VideoStore(tmp_path / "outputs", FileJobWriter())
    store.ensure_video(metadata(), ORIGINAL_URL)
    version_one = transcription_spec()
    version_two = transcription_spec(transcript_schema_version=2)

    first = store.write_transcript(VIDEO_ID, version_one, transcript("Version one"))
    second = store.write_transcript(VIDEO_ID, version_two, transcript("Version two"))

    assert first.manifest.cache_key != second.manifest.cache_key
    assert second.manifest.transcript_schema_version == 2
    assert store.find_ready_transcript(VIDEO_ID, version_two) == second
    assert store.find_ready_transcript(VIDEO_ID, version_one) == first


def test_find_ready_transcript_skips_manifest_with_matching_key_but_mismatched_identity(
    tmp_path: Path,
) -> None:
    store = VideoStore(tmp_path / "outputs", FileJobWriter())
    store.ensure_video(metadata(), ORIGINAL_URL)
    spec = transcription_spec()
    entry = store.write_transcript(VIDEO_ID, spec, transcript("Original"))
    payload = json.loads(entry.manifest_path.read_text(encoding="utf-8"))
    payload["provider"] = "other-provider"
    entry.manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    assert store.find_ready_transcript(VIDEO_ID, spec) is None
    assert store.list_transcripts(VIDEO_ID) == []


def test_concurrent_transcript_writers_do_not_replace_existing_ready_entry(
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "outputs"
    VideoStore(output_root, FileJobWriter()).ensure_video(metadata(), ORIGINAL_URL)
    spec = transcription_spec()
    barrier = Barrier(2)

    def write(text: str) -> str:
        store = VideoStore(output_root, FileJobWriter())
        barrier.wait()
        entry = store.write_transcript(VIDEO_ID, spec, transcript(text))
        return entry.transcript.segments[0].text

    with ThreadPoolExecutor(max_workers=2) as executor:
        returned_texts = list(executor.map(write, ["First", "Second"]))

    found = VideoStore(output_root, FileJobWriter()).find_ready_transcript(VIDEO_ID, spec)

    assert found is not None
    assert len(set(returned_texts)) == 1
    assert found.transcript.segments[0].text == returned_texts[0]
    assert len(VideoStore(output_root, FileJobWriter()).list_transcripts(VIDEO_ID)) == 1


def test_cross_process_transcript_writers_share_one_ready_entry(tmp_path: Path) -> None:
    output_root = tmp_path / "outputs"
    VideoStore(output_root, FileJobWriter()).ensure_video(metadata(), ORIGINAL_URL)
    context = get_context("spawn")
    start = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=_write_transcript_process,
            args=(str(output_root), text, start, results),
        )
        for text in ("Process One", "Process Two")
    ]
    for process in processes:
        process.start()

    start.set()
    outcomes: list[tuple[str, str, str]] = [results.get(timeout=10) for _ in processes]
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0

    assert [outcome[0] for outcome in outcomes] == ["ok", "ok"]
    assert len({outcome[1] for outcome in outcomes}) == 1
    assert len({outcome[2] for outcome in outcomes}) == 1
    assert len(VideoStore(output_root, FileJobWriter()).list_transcripts(VIDEO_ID)) == 1


def test_video_store_lists_transcripts_and_skips_corrupt_or_missing_entries(
    tmp_path: Path,
) -> None:
    store = VideoStore(tmp_path / "outputs", FileJobWriter())
    video = store.ensure_video(metadata(), ORIGINAL_URL)
    ready = store.write_transcript(VIDEO_ID, transcription_spec(), transcript("Ready"))
    corrupt_dir = video.root / "transcripts" / "tx-corrupt"
    corrupt_dir.mkdir(parents=True)
    (corrupt_dir / "manifest.json").write_text("{bad json", encoding="utf-8")
    missing_dir = video.root / "transcripts" / "tx-missing"
    missing_dir.mkdir()
    store.writer.write_json(
        missing_dir / "manifest.json",
        TranscriptManifest(
            transcript_id="tx-missing",
            cache_key="b" * 64,
            source_fingerprint="b" * 64,
            provider="openai",
            model="whisper-1",
            language="en",
            transcript_schema_version=1,
            transcript_path=Path("transcripts/tx-missing/transcript.json"),
            created_at=ready.manifest.created_at,
            state=ManifestState.READY,
        ),
    )

    entries = store.list_transcripts(VIDEO_ID)

    assert [entry.manifest.cache_key for entry in entries] == [ready.manifest.cache_key]
    assert store.find_ready_transcript(
        VIDEO_ID,
        transcription_spec(source_fingerprint="b" * 64),
    ) is None


def test_video_store_rejects_external_transcripts_symlink_without_writing_target(
    tmp_path: Path,
) -> None:
    store = VideoStore(tmp_path / "outputs", FileJobWriter())
    video = store.ensure_video(metadata(), ORIGINAL_URL)
    external = tmp_path / "external-transcripts"
    external.mkdir()
    (video.root / "transcripts").symlink_to(external, target_is_directory=True)

    with pytest.raises(InsightCastError) as error:
        store.write_transcript(VIDEO_ID, transcription_spec(), transcript())

    assert error.value.error_code == ErrorCode.ARTIFACT_PATH_INVALID
    assert list(external.iterdir()) == []


def test_video_store_rejects_transcripts_file_with_structured_error(
    tmp_path: Path,
) -> None:
    store = VideoStore(tmp_path / "outputs", FileJobWriter())
    video = store.ensure_video(metadata(), ORIGINAL_URL)
    (video.root / "transcripts").write_text("not a directory", encoding="utf-8")

    with pytest.raises(InsightCastError) as error:
        store.write_transcript(VIDEO_ID, transcription_spec(), transcript())

    assert error.value.error_code == ErrorCode.ARTIFACT_PATH_INVALID
    assert error.value.details["reason"] == "not_directory"


def test_video_store_uses_suffixed_transcript_id_for_cache_key_prefix_collision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = VideoStore(tmp_path / "outputs", FileJobWriter())
    store.ensure_video(metadata(), ORIGINAL_URL)

    def cache_key_for_spec(spec: TranscriptionSpec) -> str:
        if spec.source_fingerprint == "a" * 64:
            return "c" * 64
        return ("c" * 12) + ("d" * 52)

    monkeypatch.setattr(
        "insightcast.storage.video_store.build_transcript_cache_key",
        cache_key_for_spec,
    )

    first = store.write_transcript(VIDEO_ID, transcription_spec(), transcript("First"))
    second = store.write_transcript(
        VIDEO_ID,
        transcription_spec(source_fingerprint="b" * 64),
        transcript("Second"),
    )

    assert first.manifest.transcript_id == "tx-" + ("c" * 12)
    assert second.manifest.transcript_id == "tx-" + ("c" * 12) + "-1"
    assert store.find_ready_transcript(VIDEO_ID, second.manifest.cache_key) == second


def test_find_video_rejects_manifest_identity_mismatch(tmp_path: Path) -> None:
    store = VideoStore(tmp_path / "outputs", FileJobWriter())
    created = store.ensure_video(metadata(), ORIGINAL_URL)
    payload = json.loads((created.root / "video.json").read_text(encoding="utf-8"))
    payload["video_id"] = "different01"
    (created.root / "video.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(InsightCastError) as error:
        store.find_video(VIDEO_ID)

    assert error.value.error_code == ErrorCode.MANIFEST_INVALID
    assert error.value.details == {
        "manifest_path": str((created.root / "video.json").resolve()),
        "reason": "video_id_mismatch",
    }


def test_video_store_rejects_external_manifest_symlink_without_writing_target(
    tmp_path: Path,
) -> None:
    store = VideoStore(tmp_path / "outputs", FileJobWriter())
    created = store.ensure_video(metadata(), ORIGINAL_URL)
    external_manifest = tmp_path / "external-video.json"
    external_contents = b'{"external": true}\n'
    external_manifest.write_bytes(external_contents)
    manifest_path = created.root / "video.json"
    manifest_path.unlink()
    manifest_path.symlink_to(external_manifest)

    for operation in (
        lambda: store.read_manifest(manifest_path, VideoManifest),
        lambda: store.find_video(VIDEO_ID),
        lambda: store.ensure_video(metadata(title="Updated"), SHARE_URL),
    ):
        with pytest.raises(InsightCastError) as error:
            operation()
        assert error.value.error_code == ErrorCode.MANIFEST_INVALID
        assert error.value.details == {
            "manifest_path": str(manifest_path.absolute()),
            "reason": "not_regular_file",
        }

    assert external_manifest.read_bytes() == external_contents
    assert manifest_path.is_symlink()


def test_video_store_rejects_duplicate_video_roots(tmp_path: Path) -> None:
    videos = tmp_path / "outputs" / "videos"
    (videos / f"{VIDEO_ID}_one").mkdir(parents=True)
    (videos / f"{VIDEO_ID}_two").mkdir()

    with pytest.raises(InsightCastError) as error:
        VideoStore(tmp_path / "outputs", FileJobWriter()).find_video(VIDEO_ID)

    assert error.value.error_code == ErrorCode.STORAGE_CONFLICT
    assert error.value.details["video_id"] == VIDEO_ID
    assert len(error.value.details["roots"]) == 2


def test_concurrent_first_creation_uses_one_video_root(tmp_path: Path) -> None:
    output_root = tmp_path / "outputs"
    barrier = Barrier(2)

    def create(title: str) -> Path:
        store = VideoStore(output_root, FileJobWriter())
        barrier.wait()
        return store.ensure_video(metadata(title=title), ORIGINAL_URL).root

    with ThreadPoolExecutor(max_workers=2) as executor:
        roots = list(executor.map(create, ["First Title", "Second Title"]))

    assert roots[0] == roots[1]
    assert len(VideoStore(output_root, FileJobWriter()).matching_video_roots(VIDEO_ID)) == 1


def test_cross_process_first_creation_uses_one_video_root(tmp_path: Path) -> None:
    context = get_context("spawn")
    start = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=_ensure_video_process,
            args=(str(tmp_path / "outputs"), title, start, results),
        )
        for title in ("Process One", "Process Two")
    ]
    for process in processes:
        process.start()

    start.set()
    outcomes: list[tuple[str, Any]] = [results.get(timeout=10) for _ in processes]
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0

    assert [status for status, _ in outcomes] == ["ok", "ok"]
    roots = [root for _, root in outcomes]
    assert roots[0] == roots[1]
    assert len(
        VideoStore(tmp_path / "outputs", FileJobWriter()).matching_video_roots(VIDEO_ID)
    ) == 1


def test_ensure_video_removes_only_exact_stale_staging_directories(
    tmp_path: Path,
) -> None:
    store = VideoStore(tmp_path / "outputs", FileJobWriter())
    store.videos_root.mkdir(parents=True)
    stale = store.videos_root / f".{VIDEO_ID}-{'a' * 32}.tmp"
    stale.mkdir()
    (stale / "partial").write_text("partial", encoding="utf-8")
    short = store.videos_root / f".{VIDEO_ID}-short.tmp"
    short.mkdir()
    other = store.videos_root / f".different01-{'b' * 32}.tmp"
    other.mkdir()

    store.ensure_video(metadata(), ORIGINAL_URL)

    assert not stale.exists()
    assert short.is_dir()
    assert other.is_dir()


def test_failed_initial_manifest_write_leaves_no_root_and_retry_succeeds(
    tmp_path: Path,
) -> None:
    class FailOnceWriter(FileJobWriter):
        def __init__(self) -> None:
            self.failed = False

        def write_json(self, path: Path, payload: object) -> Path:
            if not self.failed:
                self.failed = True
                super().write_json(path, payload)
                raise OSError("injected write failure")
            return super().write_json(path, payload)

    writer = FailOnceWriter()
    store = VideoStore(tmp_path / "outputs", writer)

    with pytest.raises(OSError, match="injected write failure"):
        store.ensure_video(metadata(), ORIGINAL_URL)

    assert store.matching_video_roots(VIDEO_ID) == []
    assert list(store.videos_root.glob(f".{VIDEO_ID}-*.tmp")) == []

    retried = store.ensure_video(metadata(), ORIGINAL_URL)
    assert retried.root.is_dir()
    assert (retried.root / "video.json").is_file()
    assert store.find_video(VIDEO_ID) == retried


@pytest.mark.parametrize(
    "relative",
    [
        Path(""),
        Path("."),
        Path("/tmp/video.mp4"),
        Path("../outside"),
        Path("nested/../../outside"),
        Path(r"C:\video.mp4"),
        Path(r"C:video.mp4"),
        Path(r"nested\..\outside"),
    ],
)
def test_resolve_relative_rejects_invalid_paths(tmp_path: Path, relative: Path) -> None:
    store = VideoStore(tmp_path / "outputs", FileJobWriter())
    owner = store.videos_root / f"{VIDEO_ID}_title"
    owner.mkdir(parents=True)

    with pytest.raises(InsightCastError) as error:
        store.resolve_relative(owner, relative)

    assert error.value.error_code == ErrorCode.ARTIFACT_PATH_INVALID
    assert error.value.details == {"relative_path": str(relative)}


def test_resolve_relative_rejects_external_symlink(tmp_path: Path) -> None:
    store = VideoStore(tmp_path / "outputs", FileJobWriter())
    owner = store.videos_root / f"{VIDEO_ID}_title"
    owner.mkdir(parents=True)
    external = tmp_path / "external"
    external.mkdir()
    (owner / "escape").symlink_to(external, target_is_directory=True)

    with pytest.raises(InsightCastError) as error:
        store.resolve_relative(owner, Path("escape/file.mp4"))

    assert error.value.error_code == ErrorCode.ARTIFACT_PATH_INVALID


def test_resolve_relative_returns_contained_resolved_path(tmp_path: Path) -> None:
    store = VideoStore(tmp_path / "outputs", FileJobWriter())
    owner = store.videos_root / f"{VIDEO_ID}_title"
    owner.mkdir(parents=True)

    assert store.resolve_relative(owner, Path("source/manifest.json")) == (
        owner / "source" / "manifest.json"
    ).resolve()


@pytest.mark.parametrize(
    ("contents", "expected_reason"),
    [
        ("{bad json", "json"),
        (
            json.dumps(
                {
                    "schema_version": 2,
                    "video_id": VIDEO_ID,
                    "original_youtube_url": ORIGINAL_URL,
                    "normalized_youtube_url": (
                        f"https://www.youtube.com/watch?v={VIDEO_ID}"
                    ),
                    "title": "Title",
                    "uploader": None,
                    "upload_date": None,
                    "first_seen_at": "2026-06-07T00:00:00Z",
                    "last_seen_at": "2026-06-07T00:00:00Z",
                    "source_manifest_path": "source/manifest.json",
                }
            ),
            "unsupported_schema",
        ),
        (
            json.dumps(
                {
                    "schema_version": 1,
                    "video_id": VIDEO_ID,
                    "original_youtube_url": ORIGINAL_URL,
                    "normalized_youtube_url": (
                        f"https://www.youtube.com/watch?v={VIDEO_ID}"
                    ),
                    "title": "Title",
                    "uploader": None,
                    "upload_date": None,
                    "first_seen_at": "2026-06-07T00:00:00Z",
                    "last_seen_at": "2026-06-07T00:00:00Z",
                    "source_manifest_path": "source/manifest.json",
                    "unexpected": True,
                }
            ),
            "validation",
        ),
    ],
)
def test_read_manifest_maps_invalid_json_schema_and_extra_fields(
    tmp_path: Path,
    contents: str,
    expected_reason: str,
) -> None:
    path = tmp_path / "video.json"
    path.write_text(contents, encoding="utf-8")
    store = VideoStore(tmp_path / "outputs", FileJobWriter())

    with pytest.raises(InsightCastError) as error:
        store.read_manifest(path, VideoManifest)

    assert error.value.error_code == ErrorCode.MANIFEST_INVALID
    assert error.value.details == {
        "manifest_path": str(path.resolve()),
        "reason": expected_reason,
    }
    assert contents not in str(error.value.details)


def test_read_manifest_maps_io_failure_to_manifest_invalid(tmp_path: Path) -> None:
    path = tmp_path / "missing.json"
    store = VideoStore(tmp_path / "outputs", FileJobWriter())

    with pytest.raises(InsightCastError) as error:
        store.read_manifest(path, VideoManifest)

    assert error.value.error_code == ErrorCode.MANIFEST_INVALID
    assert error.value.details == {
        "manifest_path": str(path.resolve()),
        "reason": "io",
    }


def test_read_manifest_maps_invalid_utf8_to_manifest_invalid(tmp_path: Path) -> None:
    path = tmp_path / "video.json"
    path.write_bytes(b'{"title":"\xff"}')
    store = VideoStore(tmp_path / "outputs", FileJobWriter())

    with pytest.raises(InsightCastError) as error:
        store.read_manifest(path, VideoManifest)

    assert error.value.error_code == ErrorCode.MANIFEST_INVALID
    assert error.value.details == {
        "manifest_path": str(path.resolve()),
        "reason": "encoding",
    }
