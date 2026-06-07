from enum import StrEnum
from pathlib import Path, PureWindowsPath
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    model_validator,
)

from insightcast.domain.models import JobError

SCHEMA_VERSION = 1


class ManifestState(StrEnum):
    READY = "ready"
    FAILED = "failed"


class AnalysisState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_SELECTION = "waiting-selection"
    COMPLETED = "completed"
    FAILED = "failed"


class RenderKind(StrEnum):
    CANDIDATE = "candidate"
    CUSTOM = "custom"


class RenderState(StrEnum):
    QUEUED = "queued"
    RENDERING = "rendering"
    READY = "ready"
    FAILED = "failed"


class PublishState(StrEnum):
    NOT_UPLOADED = "not-uploaded"
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    UPLOAD_FAILED = "upload-failed"


def validate_relative_path(value: Path) -> Path:
    path = Path(value)
    windows_path = PureWindowsPath(str(value))
    if (
        path.is_absolute()
        or windows_path.is_absolute()
        or ".." in path.parts
        or ".." in windows_path.parts
    ):
        raise ValueError("persisted paths must be relative paths without '..' traversal")
    return path


RelativePath = Annotated[Path, AfterValidator(validate_relative_path)]


class ManifestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[SCHEMA_VERSION] = SCHEMA_VERSION


class VideoManifest(ManifestModel):
    video_id: str
    original_youtube_url: str
    normalized_youtube_url: str
    title: str
    uploader: str | None
    upload_date: str | None
    first_seen_at: AwareDatetime
    last_seen_at: AwareDatetime
    source_manifest_path: RelativePath


class SourceManifest(ManifestModel):
    video_id: str
    source_fingerprint: str
    fingerprint_algorithm: Literal["sha256"]
    source_video_path: RelativePath
    source_video_size: int = Field(gt=0)
    transcription_audio_path: RelativePath
    transcription_audio_size: int = Field(gt=0)
    downloaded_at: AwareDatetime
    audio_extracted_at: AwareDatetime
    source_metadata: dict[str, Any]
    state: ManifestState
    error: JobError | None = None


class TranscriptManifest(ManifestModel):
    transcript_id: str
    cache_key: str
    source_fingerprint: str
    provider: str
    model: str
    language: str
    transcript_path: RelativePath
    created_at: AwareDatetime
    state: ManifestState
    error: JobError | None = None


class AnalysisManifest(ManifestModel):
    analysis_id: str
    operation_id: str
    created_at: AwareDatetime
    completed_at: AwareDatetime | None
    normalized_source_url: str
    video_id: str
    transcript_id: str
    curator_model: str
    prompt_version: str
    candidate_count: int = Field(ge=1, le=26)
    min_duration_seconds: float = Field(gt=0)
    max_duration_seconds: float = Field(gt=0)
    state: AnalysisState
    candidates_path: RelativePath
    candidate_paths: dict[str, RelativePath]
    log_path: RelativePath
    error: JobError | None = None

    @model_validator(mode="after")
    def validate_duration_bounds(self) -> "AnalysisManifest":
        if self.max_duration_seconds < self.min_duration_seconds:
            raise ValueError("max_duration_seconds must be at least min_duration_seconds")
        return self


class RenderManifest(ManifestModel):
    render_id: str
    operation_id: str
    kind: RenderKind
    analysis_id: str | None = None
    candidate_id: str | None = None
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    source_fingerprint: str
    transcript_id: str
    render_config: dict[str, Any]
    artifacts: dict[str, RelativePath]
    artifact_sizes: dict[str, PositiveInt]
    artifact_hashes: dict[str, str] = Field(default_factory=dict)
    created_at: AwareDatetime
    completed_at: AwareDatetime | None
    render_state: RenderState
    publish_state: PublishState
    youtube_video_id: str | None
    youtube_url: str | None
    upload_started_at: AwareDatetime | None
    uploaded_at: AwareDatetime | None
    log_path: RelativePath
    render_error: JobError | None = None
    upload_error: JobError | None = None

    @model_validator(mode="after")
    def validate_render_identity_and_timing(self) -> "RenderManifest":
        if self.end_seconds <= self.start_seconds:
            raise ValueError("end_seconds must be later than start_seconds")
        if self.kind is RenderKind.CANDIDATE:
            if self.analysis_id is None or self.candidate_id is None:
                raise ValueError("candidate renders require analysis_id and candidate_id")
        elif self.analysis_id is not None or self.candidate_id is not None:
            raise ValueError("custom renders must omit analysis_id and candidate_id")
        return self
