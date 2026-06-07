import json
from pathlib import Path

import pytest

from insightcast.core.exceptions import InsightCastError
from insightcast.domain.enums import ErrorCode
from insightcast.infrastructure.ytdlp_client import YouTubeMetadata
from insightcast.storage.file_job_writer import FileJobWriter
from insightcast.storage.manifests import VideoManifest
from insightcast.storage.video_store import VideoStore

VIDEO_ID = "abc123DEF_-"
ORIGINAL_URL = f"https://youtu.be/{VIDEO_ID}"
SHARE_URL = f"https://www.youtube.com/watch?v={VIDEO_ID}&feature=share"


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


def test_video_store_resolves_output_and_videos_roots(tmp_path: Path) -> None:
    store = VideoStore(tmp_path / "parent" / ".." / "outputs", FileJobWriter())

    assert store.output_root == (tmp_path / "outputs").resolve()
    assert store.videos_root == store.output_root / "videos"


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


def test_video_store_rejects_duplicate_video_roots(tmp_path: Path) -> None:
    videos = tmp_path / "outputs" / "videos"
    (videos / f"{VIDEO_ID}_one").mkdir(parents=True)
    (videos / f"{VIDEO_ID}_two").mkdir()

    with pytest.raises(InsightCastError) as error:
        VideoStore(tmp_path / "outputs", FileJobWriter()).find_video(VIDEO_ID)

    assert error.value.error_code == ErrorCode.STORAGE_CONFLICT
    assert error.value.details["video_id"] == VIDEO_ID
    assert len(error.value.details["roots"]) == 2


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
