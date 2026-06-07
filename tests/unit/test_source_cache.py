import json
from pathlib import Path

import pytest

from insightcast.core.exceptions import InsightCastError
from insightcast.infrastructure.ytdlp_client import YouTubeMetadata
from insightcast.storage.source_cache import SourceCache

VIDEO_ID = "abc123DEF_-"


def metadata() -> YouTubeMetadata:
    return YouTubeMetadata(
        video_id=VIDEO_ID,
        title="Video Title",
        description="Description",
        duration_seconds=1200,
        uploader="Channel",
        upload_date="20260606",
        webpage_url=f"https://www.youtube.com/watch?v={VIDEO_ID}",
        tags=["AI"],
    )


def build_complete_staging(cache: SourceCache) -> Path:
    staging = cache.create_staging(VIDEO_ID)
    (staging / "source.mp4").write_bytes(b"video")
    (staging / "audio.mp3").write_bytes(b"audio")
    cache.write_metadata(staging, metadata())
    return staging


def test_source_cache_promotes_and_loads_complete_entry(tmp_path: Path) -> None:
    cache = SourceCache(tmp_path / "source-cache")

    entry = cache.promote(VIDEO_ID, build_complete_staging(cache))
    loaded = cache.load(VIDEO_ID)

    assert loaded == entry
    assert entry.directory == (tmp_path / "source-cache" / VIDEO_ID).resolve()
    assert entry.source_video.name == "source.mp4"
    assert entry.source_audio.name == "audio.mp3"
    assert entry.metadata.title == "Video Title"


@pytest.mark.parametrize("missing", ["source.mp4", "audio.mp3", "metadata.json"])
def test_source_cache_rejects_incomplete_entry(tmp_path: Path, missing: str) -> None:
    cache = SourceCache(tmp_path / "source-cache")
    entry_dir = cache.entry_dir(VIDEO_ID)
    entry_dir.mkdir(parents=True)
    (entry_dir / "source.mp4").write_bytes(b"video")
    (entry_dir / "audio.mp3").write_bytes(b"audio")
    cache.write_metadata(entry_dir, metadata())
    (entry_dir / missing).unlink()

    assert cache.load(VIDEO_ID) is None


def test_source_cache_metadata_excludes_raw_download_fields(tmp_path: Path) -> None:
    cache = SourceCache(tmp_path / "source-cache")
    entry = cache.promote(VIDEO_ID, build_complete_staging(cache))

    payload = json.loads(entry.metadata_path.read_text(encoding="utf-8"))

    assert set(payload) == {
        "video_id",
        "title",
        "description",
        "duration_seconds",
        "uploader",
        "upload_date",
        "webpage_url",
        "tags",
    }
    assert "formats" not in payload
    assert "url" not in payload
    assert "http_headers" not in payload


def test_failed_staging_does_not_replace_existing_entry(tmp_path: Path) -> None:
    cache = SourceCache(tmp_path / "source-cache")
    original = cache.promote(VIDEO_ID, build_complete_staging(cache))
    staging = cache.create_staging(VIDEO_ID)
    (staging / "source.mp4").write_bytes(b"replacement")

    with pytest.raises(InsightCastError):
        cache.promote(VIDEO_ID, staging)

    loaded = cache.load(VIDEO_ID)
    assert loaded == original
    assert loaded.source_video.read_bytes() == b"video"


def test_remove_ignores_incomplete_entry_but_clear_removes_it(tmp_path: Path) -> None:
    cache = SourceCache(tmp_path / "source-cache")
    incomplete = cache.entry_dir(VIDEO_ID)
    incomplete.mkdir(parents=True)
    (incomplete / "source.mp4").write_bytes(b"partial")

    assert cache.remove(VIDEO_ID) is False
    assert incomplete.exists()
    assert cache.clear() == 1
    assert not incomplete.exists()


@pytest.mark.parametrize("video_id", ["../outside", "bad", "abc123DEF_-/child"])
def test_source_cache_rejects_invalid_video_ids(tmp_path: Path, video_id: str) -> None:
    cache = SourceCache(tmp_path / "source-cache")

    with pytest.raises(InsightCastError):
        cache.entry_dir(video_id)
