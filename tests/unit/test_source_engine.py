from datetime import UTC, datetime
from pathlib import Path

import pytest

from insightcast.engines.source_engine import SourceEngine
from insightcast.infrastructure.ytdlp_client import YouTubeMetadata


class FakeYtDlp:
    def __init__(self) -> None:
        self.downloads: list[tuple[str, Path]] = []

    async def fetch_metadata(self, url: str) -> YouTubeMetadata:
        return YouTubeMetadata(
            video_id="abc123DEF_-",
            title="台灣 AI / Podcast",
            description="Source description",
            duration_seconds=1200,
            uploader="Channel",
            upload_date="20260606",
            webpage_url=url,
            tags=["AI"],
        )

    async def download_video(self, url: str, destination: Path) -> Path:
        self.downloads.append((url, destination))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"video")
        return destination


class FakeFfmpeg:
    def __init__(self) -> None:
        self.extractions: list[tuple[Path, Path]] = []

    async def extract_audio(self, source: Path, destination: Path) -> Path:
        self.extractions.append((source, destination))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"audio")
        return destination


@pytest.mark.asyncio
async def test_source_engine_builds_layout_downloads_and_extracts_audio(tmp_path: Path) -> None:
    ytdlp = FakeYtDlp()
    ffmpeg = FakeFfmpeg()
    engine = SourceEngine(ytdlp=ytdlp, ffmpeg=ffmpeg)

    result = await engine.ingest(
        youtube_url="https://www.youtube.com/watch?v=abc123DEF_-",
        job_id="a1b2c3d4",
        created_at=datetime(2026, 6, 6, 14, 30, tzinfo=UTC),
        output_root=tmp_path,
        direct=False,
    )

    assert result.output_dir.name == "20260606-143000_台灣-ai-podcast_a1b2c3"
    assert result.source_artifacts.source_video.name == "台灣-ai-podcast.source.mp4"
    assert result.source_artifacts.source_audio.name == "台灣-ai-podcast.audio.mp3"
    assert result.metadata.title == "台灣 AI / Podcast"
    assert len(ytdlp.downloads) == 1
    assert len(ffmpeg.extractions) == 1


@pytest.mark.asyncio
async def test_source_engine_reuses_existing_source_files(tmp_path: Path) -> None:
    ytdlp = FakeYtDlp()
    ffmpeg = FakeFfmpeg()
    engine = SourceEngine(ytdlp=ytdlp, ffmpeg=ffmpeg)
    kwargs = {
        "youtube_url": "https://www.youtube.com/watch?v=abc123DEF_-",
        "job_id": "a1b2c3d4",
        "created_at": datetime(2026, 6, 6, 14, 30, tzinfo=UTC),
        "output_root": tmp_path,
        "direct": True,
    }

    first = await engine.ingest(**kwargs)
    second = await engine.ingest(**kwargs)

    assert first.output_dir.name.endswith("_direct_a1b2c3")
    assert second.source_artifacts == first.source_artifacts
    assert len(ytdlp.downloads) == 1
    assert len(ffmpeg.extractions) == 1

