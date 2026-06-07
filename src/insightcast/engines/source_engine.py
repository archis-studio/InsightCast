from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from insightcast.core.exceptions import InsightCastError
from insightcast.domain.enums import ErrorCode
from insightcast.domain.models import SourceArtifacts
from insightcast.infrastructure.ytdlp_client import YouTubeMetadata
from insightcast.storage.video_store import SourceEntry, VideoStore
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
        video_store: VideoStore,
    ) -> None:
        self.ytdlp = ytdlp
        self.ffmpeg = ffmpeg
        self.video_store = video_store

    async def ingest(
        self,
        *,
        youtube_url: str,
        job_id: str,
        created_at: datetime,
        output_root: Path,
        direct: bool,
    ) -> SourceResult:
        del job_id, created_at, output_root, direct
        video_id = extract_youtube_video_id(youtube_url)
        async with self.video_store.source_transaction(video_id) as transaction:
            lookup = transaction.load_source()
            if lookup.entry is not None:
                return self._result(
                    source=lookup.entry,
                    metadata=lookup.entry.metadata,
                    cache_decision="hit",
                )
            cache_decision = lookup.status
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
            transaction.ensure_video(metadata, youtube_url)
            staging = transaction.create_staging()
            try:
                await self.ytdlp.download_video(youtube_url, staging / "source.mp4")
                downloaded_at = datetime.now(UTC)
                await self.ffmpeg.extract_audio(
                    staging / "source.mp4",
                    staging / "audio.mp3",
                )
                source = transaction.promote(
                    staging,
                    metadata=metadata,
                    downloaded_at=downloaded_at,
                    audio_extracted_at=datetime.now(UTC),
                )
            except BaseException:
                with suppress(InsightCastError):
                    transaction.discard_staging(staging)
                raise

        return self._result(
            source=source,
            metadata=metadata,
            cache_decision=cache_decision,
        )

    @staticmethod
    def _result(
        *,
        source: SourceEntry,
        metadata: YouTubeMetadata,
        cache_decision: Literal["hit", "miss", "repair"],
    ) -> SourceResult:
        return SourceResult(
            output_dir=source.root,
            metadata=metadata,
            source_artifacts=SourceArtifacts(
                source_video=source.source_video,
                source_audio=source.source_audio,
            ),
            cache_decision=cache_decision,
        )
