from pathlib import Path

import pytest

from insightcast.cli.cache import main
from insightcast.infrastructure.ytdlp_client import YouTubeMetadata
from insightcast.storage.source_cache import SourceCache

VIDEO_ID = "abc123DEF_-"


def populate(output_dir: Path) -> None:
    cache = SourceCache(output_dir / "source-cache")
    staging = cache.create_staging(VIDEO_ID)
    (staging / "source.mp4").write_bytes(b"video")
    (staging / "audio.mp3").write_bytes(b"audio")
    cache.write_metadata(
        staging,
        YouTubeMetadata(
            video_id=VIDEO_ID,
            title="Video Title",
            duration_seconds=1200,
            webpage_url=f"https://www.youtube.com/watch?v={VIDEO_ID}",
        ),
    )
    cache.promote(VIDEO_ID, staging)


def test_cache_list_reports_validated_entry(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output_dir = tmp_path / "outputs"
    populate(output_dir)

    assert main(["--output-dir", str(output_dir), "list"]) == 0

    output = capsys.readouterr().out
    assert VIDEO_ID in output
    assert "Video Title" in output
    assert "5 B" in output


def test_cache_remove_deletes_only_requested_entry(tmp_path: Path) -> None:
    output_dir = tmp_path / "outputs"
    populate(output_dir)
    job_file = output_dir / "jobs" / "historical" / "job_state.json"
    job_file.parent.mkdir(parents=True)
    job_file.write_text("{}\n", encoding="utf-8")

    assert main(["--output-dir", str(output_dir), "remove", VIDEO_ID]) == 0

    assert not (output_dir / "source-cache" / VIDEO_ID).exists()
    assert job_file.exists()


def test_cache_clear_requires_explicit_confirmation(tmp_path: Path) -> None:
    output_dir = tmp_path / "outputs"
    populate(output_dir)

    assert main(["--output-dir", str(output_dir), "clear"]) == 2

    assert (output_dir / "source-cache" / VIDEO_ID).exists()


def test_cache_clear_with_yes_removes_entries_but_keeps_jobs(tmp_path: Path) -> None:
    output_dir = tmp_path / "outputs"
    populate(output_dir)
    job_file = output_dir / "jobs" / "historical" / "job_state.json"
    job_file.parent.mkdir(parents=True)
    job_file.write_text("{}\n", encoding="utf-8")

    assert main(["--output-dir", str(output_dir), "clear", "--yes"]) == 0

    assert not (output_dir / "source-cache" / VIDEO_ID).exists()
    assert job_file.exists()
