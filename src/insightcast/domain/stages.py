from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import Field, computed_field, model_validator

from insightcast.domain.models import DomainModel, JobError


class StageStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class PipelineStage(StrEnum):
    SOURCE_INGESTION = "source_ingestion"
    TRANSCRIPTION = "transcription"
    TOPIC_DISCOVERY = "topic_discovery"
    CANDIDATE_BOUNDARY_SELECTION = "candidate_boundary_selection"
    CUT_CLIP = "cut_clip"
    TRANSLATE_SUBTITLES = "translate_subtitles"
    WRITE_SUBTITLES = "write_subtitles"
    BURN_SUBTITLES = "burn_subtitles"
    GENERATE_METADATA = "generate_metadata"
    VALIDATE_RENDER = "validate_render"


class QualityWarning(DomainModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    details: dict[str, object] = Field(default_factory=dict)


class StageRecord(DomainModel):
    stage: PipelineStage
    status: StageStatus
    started_at: datetime | None = None
    completed_at: datetime | None = None
    elapsed_seconds: float | None = Field(default=None, ge=0)
    artifacts: dict[str, Path] = Field(default_factory=dict)
    metadata: dict[str, object] = Field(default_factory=dict)
    resume_strategy: str = Field(min_length=1)
    fresh: bool = False
    reused: bool = False
    warnings: list[QualityWarning] = Field(default_factory=list)
    error: JobError | None = None

    @model_validator(mode="after")
    def validate_stage_state(self) -> "StageRecord":
        if self.status is StageStatus.FAILED and self.error is None:
            raise ValueError("failed stages require error")
        if self.status is not StageStatus.FAILED and self.error is not None:
            raise ValueError("non-failed stages must not carry error")
        if (
            self.completed_at is not None
            and self.started_at is not None
            and self.completed_at < self.started_at
        ):
            raise ValueError("completed_at must not precede started_at")
        return self


class StageManifest(DomainModel):
    schema_version: Literal[1] = 1
    operation_id: str = Field(min_length=1)
    render_id: str = Field(min_length=1)
    candidate_id: str | None = None
    stages: list[StageRecord] = Field(default_factory=list)

    @computed_field
    @property
    def current_stage(self) -> PipelineStage | None:
        if not self.stages:
            return None
        return self.stages[-1].stage

    @computed_field
    @property
    def resume_from(self) -> str | None:
        if (
            self.stages
            and self.stages[-1].status
            in {StageStatus.FAILED, StageStatus.RUNNING, StageStatus.QUEUED}
        ):
            return self.stages[-1].stage.value
        return None
