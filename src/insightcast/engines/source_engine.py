from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from insightcast.core.exceptions import InsightCastError
from insightcast.domain.enums import ErrorCode
from insightcast.domain.models import SourceArtifacts
from insightcast.infrastructure.ytdlp_client import YouTubeMetadata
from insightcast.storage.source_cache import SourceCache
from insightcast.utils.files import (
    build_analysis_job_dir_name,
    build_direct_job_dir_name,
)
from insightcast.utils.youtube import extract_youtube_video_id


class SourceResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    output_dir: Path
    metadata: YouTubeMetadata
    source_artifacts: SourceArtifacts
    cache_decision: Literal["hit", "miss", "repair"] = "miss"


class SourceEngine:
    def __init__(
        self,
        *,
        ytdlp: Any,
        ffmpeg: Any,
        source_cache: SourceCache | None = None,
    ) -> None:
        self.ytdlp = ytdlp
        self.ffmpeg = ffmpeg
        self.source_cache = source_cache

    async def ingest(
        self,
        *,
        youtube_url: str,
        job_id: str,
        created_at: datetime,
        output_root: Path,
        direct: bool,
    ) -> SourceResult:
        resolved_output_root = output_root.expanduser().resolve()
        cache = self.source_cache or SourceCache(resolved_output_root / "source-cache")
        video_id = extract_youtube_video_id(youtube_url)
        cache_entry_existed = cache.entry_dir(video_id).exists()
        cached = cache.load(video_id)
        if cached is not None:
            metadata = cached.metadata
            cache_decision: Literal["hit", "miss", "repair"] = "hit"
        else:
            cache_decision = "repair" if cache_entry_existed else "miss"
            staging = cache.create_staging(video_id)
            try:
                metadata = await self.ytdlp.fetch_metadata(youtube_url)
                if metadata.video_id != video_id:
                    raise InsightCastError(
                        ErrorCode.SOURCE_CACHE_INVALID,
                        "YouTube metadata did not match the requested video.",
                        details={
                            "expected_video_id": video_id,
                            "actual_video_id": metadata.video_id,
                        },
                        stage="ingesting",
                    )
                await self.ytdlp.download_video(youtube_url, staging / "source.mp4")
                await self.ffmpeg.extract_audio(
                    staging / "source.mp4",
                    staging / "audio.mp3",
                )
                cache.write_metadata(staging, metadata)
                cached = cache.promote(video_id, staging)
            except Exception:
                cache.discard_staging(staging)
                raise
        if direct:
            directory_name = build_direct_job_dir_name(metadata.title, job_id, created_at)
        else:
            directory_name = build_analysis_job_dir_name(metadata.title, job_id, created_at)
        output_dir = resolved_output_root / "jobs" / directory_name

        return SourceResult(
            output_dir=output_dir.resolve(),
            metadata=metadata,
            source_artifacts=SourceArtifacts(
                source_video=cached.source_video,
                source_audio=cached.source_audio,
            ),
            cache_decision=cache_decision,
        )
