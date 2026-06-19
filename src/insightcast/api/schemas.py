from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from insightcast.domain.enums import ErrorCode, JobStatus
from insightcast.domain.models import Candidate, JobError, RenderBatch
from insightcast.storage.manifests import AnalysisState, PublishState, RenderKind, RenderState
from insightcast.utils.timecode import parse_timecode


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _omitted_override() -> Any:
    return None


class AnalysisJobCreateRequest(ApiModel):
    youtube_url: str = Field(
        description="YouTube watch, share, embed, or Shorts URL.",
        examples=["https://www.youtube.com/watch?v=abc123DEF_-"],
    )
    candidate_count: int = Field(
        default_factory=_omitted_override,
        ge=1,
        le=26,
        description="Optional override for the configured candidate count.",
        examples=[2],
    )
    min_duration_minutes: float = Field(
        default_factory=_omitted_override,
        gt=0,
        description="Optional override for the configured minimum candidate duration.",
        examples=[8],
    )
    max_duration_minutes: float = Field(
        default_factory=_omitted_override,
        gt=0,
        description="Optional override for the configured maximum candidate duration.",
        examples=[12],
    )
    force_reanalyze: bool = Field(
        default=False,
        description="Create a new analysis job even when the URL was analyzed in this process.",
        examples=[False],
    )

class ResolvedCandidateOptions(ApiModel):
    candidate_count: int = Field(ge=1, le=26)
    min_duration_minutes: float = Field(gt=0)
    max_duration_minutes: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_duration_range(self) -> "ResolvedCandidateOptions":
        if self.max_duration_minutes < self.min_duration_minutes:
            raise ValueError("max_duration_minutes must be at least min_duration_minutes")
        return self


class RenderCreateRequest(ApiModel):
    candidate_ids: str | list[str] = Field(
        description="One candidate ID or an ordered list of candidate IDs.",
        examples=[["A", "C"]],
    )
    force_render: bool = Field(
        default=False,
        description="Create a new timestamped render without overwriting previous output.",
        examples=[False],
    )
    force_translate: bool = Field(
        default=False,
        description="Redo subtitle translation even when reusable translation artifacts exist.",
        examples=[False],
    )
    force_metadata: bool = Field(
        default=False,
        description="Regenerate YouTube metadata even when reusable metadata exists.",
        examples=[False],
    )


class DirectRenderCreateRequest(ApiModel):
    youtube_url: str = Field(
        description="YouTube URL to download and render.",
        examples=["https://www.youtube.com/watch?v=abc123DEF_-"],
    )
    start_time: str | float = Field(
        description="Clip start as HH:MM:SS(.mmm) or numeric seconds.",
        examples=["00:12:30"],
    )
    end_time: str | float = Field(
        description="Clip end as HH:MM:SS(.mmm) or numeric seconds.",
        examples=["00:22:00"],
    )

    def parsed_times(self) -> tuple[float, float]:
        return parse_timecode(self.start_time), parse_timecode(self.end_time)


class ErrorResponse(ApiModel):
    error_code: ErrorCode | str = Field(description="Stable machine-readable error code.")
    message: str = Field(description="Human-readable error explanation.")
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Safe structured context for diagnosing the failure.",
    )


class HealthResponse(ApiModel):
    status: str
    message: str
    dependencies: dict[str, str]


class QueuedJobResponse(ApiModel):
    job_id: str
    status: JobStatus
    message: str
    artifacts: dict[str, Any]
    created_at: datetime


class AnalysisJobResponse(ApiModel):
    job_id: str
    status: JobStatus
    message: str
    candidates: list[Candidate]
    render_batches: list[RenderBatch]
    error: JobError | None
    artifacts: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class RenderBatchResponse(ApiModel):
    job_id: str
    render_id: str
    status: JobStatus
    message: str
    candidate_ids: list[str]
    artifacts: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class RenderBatchListResponse(ApiModel):
    job_id: str
    status: str = "ok"
    message: str
    artifacts: dict[str, Any]
    render_batches: list[RenderBatch]


class DirectRenderJobResponse(ApiModel):
    job_id: str
    status: JobStatus
    message: str
    error: JobError | None
    artifacts: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class VideoResponse(ApiModel):
    video_id: str
    title: str
    uploader: str | None
    upload_date: str | None
    original_youtube_url: str
    normalized_youtube_url: str
    first_seen_at: datetime
    last_seen_at: datetime
    root: Path
    manifest_path: Path


class VideoAnalysisItem(ApiModel):
    analysis_id: str
    operation_id: str
    state: AnalysisState
    created_at: datetime
    completed_at: datetime | None
    transcript_id: str
    candidate_count: int
    candidates_path: Path
    candidate_paths: dict[str, Path]
    manifest_path: Path


class VideoAnalysisListResponse(ApiModel):
    video_id: str
    analyses: list[VideoAnalysisItem]


class VideoRenderItem(ApiModel):
    render_id: str
    operation_id: str
    kind: RenderKind
    analysis_id: str | None
    candidate_id: str | None
    start_seconds: float
    end_seconds: float
    render_state: RenderState
    publish_state: PublishState
    created_at: datetime
    completed_at: datetime | None
    manifest_path: Path
    artifacts: dict[str, Path]


class VideoRenderListResponse(ApiModel):
    video_id: str
    renders: list[VideoRenderItem]
