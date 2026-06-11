import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from insightcast.cli.cache import main
from insightcast.infrastructure.ytdlp_client import YouTubeMetadata
from insightcast.storage.file_job_writer import FileJobWriter
from insightcast.storage.video_store import VideoStore

VIDEO_ID = "abc123DEF_-"


def populate(output_dir: Path) -> None:
    store = VideoStore(output_dir, FileJobWriter())
    metadata = YouTubeMetadata(
        video_id=VIDEO_ID,
        title="Video Title",
        duration_seconds=1200,
        webpage_url=f"https://www.youtube.com/watch?v={VIDEO_ID}",
    )

    async def create_source() -> None:
        async with store.source_transaction(VIDEO_ID) as transaction:
            transaction.ensure_video(metadata, metadata.webpage_url)
            staging = transaction.create_staging()
            (staging / "source.mp4").write_bytes(b"video")
            (staging / "audio.mp3").write_bytes(b"audio")
            transaction.promote(
                staging,
                metadata=metadata,
                downloaded_at=datetime(2026, 6, 7, tzinfo=UTC),
                audio_extracted_at=datetime(2026, 6, 7, tzinfo=UTC),
            )

    asyncio.run(create_source())


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

    video_root = next((output_dir / "videos").glob(f"{VIDEO_ID}_*"))
    assert not (video_root / "source").exists()
    assert (video_root / "video.json").exists()
    assert job_file.exists()


def test_cache_clear_requires_explicit_confirmation(tmp_path: Path) -> None:
    output_dir = tmp_path / "outputs"
    populate(output_dir)

    assert main(["--output-dir", str(output_dir), "clear"]) == 2

    video_root = next((output_dir / "videos").glob(f"{VIDEO_ID}_*"))
    assert (video_root / "source").exists()


def test_cache_clear_with_yes_removes_entries_but_keeps_jobs(tmp_path: Path) -> None:
    output_dir = tmp_path / "outputs"
    populate(output_dir)
    job_file = output_dir / "jobs" / "historical" / "job_state.json"
    job_file.parent.mkdir(parents=True)
    job_file.write_text("{}\n", encoding="utf-8")

    assert main(["--output-dir", str(output_dir), "clear", "--yes"]) == 0

    video_root = next((output_dir / "videos").glob(f"{VIDEO_ID}_*"))
    assert not (video_root / "source").exists()
    assert (video_root / "video.json").exists()
    assert job_file.exists()
