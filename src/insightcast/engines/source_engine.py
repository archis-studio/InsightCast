from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from insightcast.domain.models import SourceArtifacts
from insightcast.infrastructure.ytdlp_client import YouTubeMetadata
from insightcast.utils.files import (
    build_analysis_job_dir_name,
    build_direct_job_dir_name,
    sanitize_filename,
)


class SourceResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    output_dir: Path
    metadata: YouTubeMetadata
    source_artifacts: SourceArtifacts


class SourceEngine:
    def __init__(self, *, ytdlp: Any, ffmpeg: Any) -> None:
        self.ytdlp = ytdlp
        self.ffmpeg = ffmpeg

    async def ingest(
        self,
        *,
        youtube_url: str,
        job_id: str,
        created_at: datetime,
        output_root: Path,
        direct: bool,
    ) -> SourceResult:
        metadata = await self.ytdlp.fetch_metadata(youtube_url)
        if direct:
            directory_name = build_direct_job_dir_name(metadata.title, job_id, created_at)
        else:
            directory_name = build_analysis_job_dir_name(metadata.title, job_id, created_at)
        output_dir = output_root.expanduser().resolve() / directory_name
        source_dir = output_dir / "source"
        source_dir.mkdir(parents=True, exist_ok=True)
        base_name = sanitize_filename(metadata.title)
        source_video = source_dir / f"{base_name}.source.mp4"
        source_audio = source_dir / f"{base_name}.audio.mp3"

        if not source_video.exists():
            await self.ytdlp.download_video(youtube_url, source_video)
        if not source_audio.exists():
            await self.ffmpeg.extract_audio(source_video, source_audio)

        return SourceResult(
            output_dir=output_dir,
            metadata=metadata,
            source_artifacts=SourceArtifacts(
                source_video=source_video.resolve(),
                source_audio=source_audio.resolve(),
            ),
        )

