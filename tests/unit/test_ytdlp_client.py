import json
import subprocess
from pathlib import Path

import pytest

from insightcast.core.exceptions import InsightCastError
from insightcast.domain.enums import ErrorCode
from insightcast.infrastructure.ytdlp_client import YtDlpClient


class RecordingRunner:
    def __init__(self, result: subprocess.CompletedProcess[str]) -> None:
        self.result = result
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        return self.result


@pytest.mark.asyncio
async def test_fetch_metadata_parses_complete_youtube_json() -> None:
    payload = {
        "id": "abc123DEF_-",
        "title": "Video Title",
        "description": "Description",
        "duration": 123.5,
        "uploader": "Channel",
        "upload_date": "20260606",
        "webpage_url": "https://www.youtube.com/watch?v=abc123DEF_-",
        "tags": ["AI", "Knowledge"],
    }
    runner = RecordingRunner(
        subprocess.CompletedProcess(args=[], returncode=0, stdout=json.dumps(payload), stderr="")
    )
    client = YtDlpClient(max_height=1080, runner=runner)

    metadata = await client.fetch_metadata("https://youtu.be/abc123DEF_-")

    assert metadata.title == "Video Title"
    assert metadata.duration_seconds == 123.5
    assert "raw" not in metadata.model_dump()
    assert runner.calls == [
        [
            "yt-dlp",
            "--dump-single-json",
            "--skip-download",
            "--no-playlist",
            "https://youtu.be/abc123DEF_-",
        ]
    ]


@pytest.mark.asyncio
async def test_download_video_caps_height_and_merges_mp4(tmp_path: Path) -> None:
    runner = RecordingRunner(
        subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    )
    client = YtDlpClient(max_height=720, runner=runner)
    destination = tmp_path / "source.mp4"

    result = await client.download_video("https://youtu.be/abc123DEF_-", destination)

    assert result == destination.resolve()
    command = runner.calls[0]
    assert command[:2] == ["yt-dlp", "--no-playlist"]
    assert "bestvideo[height<=720]+bestaudio/best[height<=720]" in command
    assert command[
        command.index("--merge-output-format") : command.index("--merge-output-format") + 2
    ] == ["--merge-output-format", "mp4"]
    assert str(destination.resolve()) in command


@pytest.mark.asyncio
async def test_ytdlp_failure_becomes_stable_application_error() -> None:
    runner = RecordingRunner(
        subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="private failure")
    )

    with pytest.raises(InsightCastError) as exc_info:
        await YtDlpClient(runner=runner).fetch_metadata("https://youtu.be/abc123DEF_-")

    assert exc_info.value.error_code == ErrorCode.YOUTUBE_DOWNLOAD_FAILED
    assert exc_info.value.details["returncode"] == 1
    assert "private failure" in exc_info.value.details["stderr"]
