import asyncio
import json
import subprocess
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from insightcast.core.exceptions import InsightCastError
from insightcast.domain.enums import ErrorCode

ProcessRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]


def _run_process(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=False)


class YouTubeMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    video_id: str
    title: str
    description: str = ""
    duration_seconds: float = Field(gt=0)
    uploader: str | None = None
    upload_date: str | None = None
    webpage_url: str
    tags: list[str] = Field(default_factory=list)


class YtDlpClient:
    def __init__(
        self,
        *,
        executable: str = "yt-dlp",
        max_height: int = 1080,
        runner: ProcessRunner = _run_process,
    ) -> None:
        self.executable = executable
        self.max_height = max_height
        self.runner = runner

    async def fetch_metadata(self, youtube_url: str) -> YouTubeMetadata:
        command = [
            self.executable,
            "--dump-single-json",
            "--skip-download",
            "--no-playlist",
            youtube_url,
        ]
        result = await self._execute(command)
        try:
            payload = json.loads(result.stdout)
            return YouTubeMetadata(
                video_id=payload["id"],
                title=payload["title"],
                description=payload.get("description") or "",
                duration_seconds=payload["duration"],
                uploader=payload.get("uploader"),
                upload_date=payload.get("upload_date"),
                webpage_url=payload.get("webpage_url") or youtube_url,
                tags=payload.get("tags") or [],
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise InsightCastError(
                ErrorCode.YOUTUBE_DOWNLOAD_FAILED,
                "yt-dlp returned invalid video metadata.",
                details={"reason": str(exc)},
                stage="ingesting",
            ) from exc

    async def download_video(self, youtube_url: str, destination: Path) -> Path:
        resolved_destination = destination.expanduser().resolve()
        resolved_destination.parent.mkdir(parents=True, exist_ok=True)
        format_selector = (
            f"bestvideo[height<={self.max_height}]+bestaudio/"
            f"best[height<={self.max_height}]"
        )
        command = [
            self.executable,
            "--no-playlist",
            "--format",
            format_selector,
            "--merge-output-format",
            "mp4",
            "--output",
            str(resolved_destination),
            youtube_url,
        ]
        await self._execute(command)
        return resolved_destination

    async def _execute(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            result = await asyncio.to_thread(self.runner, command)
        except OSError as exc:
            raise InsightCastError(
                ErrorCode.YOUTUBE_DOWNLOAD_FAILED,
                "yt-dlp could not be executed.",
                details={"reason": str(exc)},
                stage="ingesting",
            ) from exc
        if result.returncode != 0:
            raise InsightCastError(
                ErrorCode.YOUTUBE_DOWNLOAD_FAILED,
                "yt-dlp failed to process the YouTube video.",
                details={
                    "returncode": result.returncode,
                    "stderr": result.stderr[-4000:],
                },
                stage="ingesting",
            )
        return result
