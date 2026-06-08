from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from insightcast.domain.enums import ErrorCode, JobStatus, JobType


def utc_now() -> datetime:
    return datetime.now(UTC)


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TranscriptSegment(DomainModel):
    segment_id: str
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    text: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_timing(self) -> "TranscriptSegment":
        if self.end_seconds <= self.start_seconds:
            raise ValueError("end_seconds must be later than start_seconds")
        return self


class Transcript(DomainModel):
    language: str
    duration_seconds: float = Field(gt=0)
    segments: list[TranscriptSegment]


class Candidate(DomainModel):
    candidate_id: str = Field(min_length=1)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    suggested_title: str = Field(min_length=1)
    selection_reason: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    score: float | None = None

    @model_validator(mode="after")
    def validate_timing(self) -> "Candidate":
        if self.end_seconds <= self.start_seconds:
            raise ValueError("end_seconds must be later than start_seconds")
        return self

    @computed_field
    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds


class JobError(DomainModel):
    stage: str | None = None
    error_code: ErrorCode
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class SourceArtifacts(DomainModel):
    source_video: Path
    source_audio: Path
    transcript: Path | None = None
    candidates: Path | None = None


class RenderArtifacts(DomainModel):
    traditional_chinese_srt: Path
    bilingual_ass: Path
    burned_video: Path
    youtube_metadata: Path


class CandidateRenderResult(DomainModel):
    candidate_id: str
    artifacts: RenderArtifacts | None = None
    error: JobError | None = None


class RenderBatch(DomainModel):
    render_id: str
    candidate_ids: list[str]
    status: JobStatus = JobStatus.QUEUED
    message: str
    output_dir: Path
    candidate_results: dict[str, CandidateRenderResult] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class BaseJob(DomainModel):
    job_id: str
    job_type: JobType
    original_youtube_url: str
    normalized_youtube_url: str
    status: JobStatus = JobStatus.QUEUED
    message: str
    output_dir: Path
    source_artifacts: SourceArtifacts | None = None
    error: JobError | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class AnalysisJob(BaseJob):
    video_id: str | None = None
    analysis_id: str
    transcript_id: str | None = None
    manifest_path: Path | None = None
    candidate_count: int = Field(default=2, ge=1, le=26)
    min_duration_minutes: float = Field(default=8, gt=0)
    max_duration_minutes: float = Field(default=12, gt=0)
    candidates: list[Candidate] = Field(default_factory=list)
    render_batches: list[RenderBatch] = Field(default_factory=list)


class DirectRenderJob(BaseJob):
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    artifacts: RenderArtifacts | None = None

    @model_validator(mode="after")
    def validate_timing(self) -> "DirectRenderJob":
        if self.end_seconds <= self.start_seconds:
            raise ValueError("end_seconds must be later than start_seconds")
        return self


class CandidateSelectionRequest(DomainModel):
    candidate_ids: list[str]
    force_render: bool = False

    @field_validator("candidate_ids", mode="before")
    @classmethod
    def normalize_candidate_ids(cls, value: str | list[str]) -> list[str]:
        items = [value] if isinstance(value, str) else value
        if not items:
            raise ValueError("at least one candidate ID is required")
        normalized: list[str] = []
        for item in items:
            candidate_id = item.strip().upper()
            if not candidate_id:
                raise ValueError("candidate IDs must not be empty")
            if candidate_id not in normalized:
                normalized.append(candidate_id)
        return normalized
