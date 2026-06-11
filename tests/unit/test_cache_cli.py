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
    assert "ready" in output
    assert "5 B" in output
    assert "0cab1c96" in output


def test_cache_list_reports_video_when_source_is_missing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output_dir = tmp_path / "outputs"
    populate(output_dir)
    assert main(["--output-dir", str(output_dir), "remove", VIDEO_ID]) == 0

    assert main(["--output-dir", str(output_dir), "list"]) == 0

    output = capsys.readouterr().out
    assert VIDEO_ID in output
    assert "Video Title" in output
    assert "missing" in output
    assert "0 B" in output


def test_cache_remove_deletes_only_source_and_preserves_results(tmp_path: Path) -> None:
    output_dir = tmp_path / "outputs"
    populate(output_dir)
    video_root = next((output_dir / "videos").glob(f"{VIDEO_ID}_*"))
    analysis_file = video_root / "analyses" / "analysis-1" / "manifest.json"
    render_file = (
        video_root
        / "analyses"
        / "analysis-1"
        / "candidates"
        / "A"
        / "renders"
        / "render-1"
        / "manifest.json"
    )
    analysis_file.parent.mkdir(parents=True)
    analysis_file.write_text("{}\n", encoding="utf-8")
    render_file.parent.mkdir(parents=True)
    render_file.write_text("{}\n", encoding="utf-8")

    assert main(["--output-dir", str(output_dir), "remove", VIDEO_ID]) == 0

    assert not (video_root / "source").exists()
    assert (video_root / "video.json").exists()
    assert analysis_file.exists()
    assert render_file.exists()


def test_cache_clear_requires_explicit_confirmation(tmp_path: Path) -> None:
    output_dir = tmp_path / "outputs"
    populate(output_dir)

    assert main(["--output-dir", str(output_dir), "clear"]) == 2

    video_root = next((output_dir / "videos").glob(f"{VIDEO_ID}_*"))
    assert (video_root / "source").exists()


def test_cache_clear_with_yes_removes_sources_but_keeps_results(tmp_path: Path) -> None:
    output_dir = tmp_path / "outputs"
    populate(output_dir)
    video_root = next((output_dir / "videos").glob(f"{VIDEO_ID}_*"))
    transcript_file = video_root / "transcripts" / "tx-1" / "transcript.json"
    analysis_file = video_root / "analyses" / "analysis-1" / "manifest.json"
    log_file = video_root / "logs" / "analysis-1.log"
    for path in (transcript_file, analysis_file, log_file):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")

    assert main(["--output-dir", str(output_dir), "clear", "--yes"]) == 0

    assert not (video_root / "source").exists()
    assert (video_root / "video.json").exists()
    assert transcript_file.exists()
    assert analysis_file.exists()
    assert log_file.exists()


def test_cache_clear_rejects_invalid_root_before_deleting_sources(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output_dir = tmp_path / "outputs"
    populate(output_dir)
    valid_root = next((output_dir / "videos").glob(f"{VIDEO_ID}_*"))
    invalid_root = output_dir / "videos" / "invalid-root"
    invalid_root.mkdir()
    (invalid_root / "video.json").write_text("{", encoding="utf-8")

    assert main(["--output-dir", str(output_dir), "clear", "--yes"]) == 2

    assert (valid_root / "source").is_dir()
    assert "MANIFEST_INVALID" in capsys.readouterr().err


def test_cache_remove_rejects_duplicate_video_roots_without_deleting_sources(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output_dir = tmp_path / "outputs"
    populate(output_dir)
    first_root = next((output_dir / "videos").glob(f"{VIDEO_ID}_*"))
    duplicate_root = output_dir / "videos" / f"{VIDEO_ID}_duplicate"
    duplicate_root.mkdir()
    (duplicate_root / "video.json").write_text(
        (first_root / "video.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    assert main(["--output-dir", str(output_dir), "remove", VIDEO_ID]) == 2

    assert (first_root / "source").is_dir()
    assert "STORAGE_CONFLICT" in capsys.readouterr().err


def test_cache_reports_invalid_videos_root_as_structured_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    (output_dir / "videos").write_text("not a directory\n", encoding="utf-8")

    assert main(["--output-dir", str(output_dir), "list"]) == 2

    assert "ARTIFACT_PATH_INVALID" in capsys.readouterr().err
