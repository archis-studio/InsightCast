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
    StringConstraints,
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
        str(path) in {"", "."}
        or path.is_absolute()
        or windows_path.is_absolute()
        or bool(windows_path.drive)
        or ".." in path.parts
        or ".." in windows_path.parts
    ):
        raise ValueError(
            "persisted paths must be non-empty relative paths without drives or '..' traversal"
        )
    return path


RelativePath = Annotated[Path, AfterValidator(validate_relative_path)]


def validate_candidate_id(value: str) -> str:
    if len(value) != 1 or not "A" <= value <= "Z":
        raise ValueError("candidate ID must be a single uppercase letter from A to Z")
    return value


CandidateId = Annotated[str, AfterValidator(validate_candidate_id)]
Sha256Digest = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9A-Fa-f]{64}$"),
]


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
    source_fingerprint: Sha256Digest
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

    @model_validator(mode="after")
    def validate_state(self) -> "SourceManifest":
        if self.state is ManifestState.FAILED and self.error is None:
            raise ValueError("failed source manifests require an error")
        if self.state is not ManifestState.FAILED and self.error is not None:
            raise ValueError("non-failed source manifests must not carry an error")
        return self


class TranscriptManifest(ManifestModel):
    transcript_id: str
    cache_key: Sha256Digest
    source_fingerprint: Sha256Digest
    provider: str
    model: str
    language: str
    transcript_path: RelativePath
    created_at: AwareDatetime
    state: ManifestState
    error: JobError | None = None

    @model_validator(mode="after")
    def validate_state(self) -> "TranscriptManifest":
        if self.state is ManifestState.FAILED and self.error is None:
            raise ValueError("failed transcript manifests require an error")
        if self.state is not ManifestState.FAILED and self.error is not None:
            raise ValueError("non-failed transcript manifests must not carry an error")
        return self


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
    candidate_paths: dict[CandidateId, RelativePath]
    log_path: RelativePath
    error: JobError | None = None

    @model_validator(mode="after")
    def validate_invariants(self) -> "AnalysisManifest":
        if self.max_duration_seconds < self.min_duration_seconds:
            raise ValueError("max_duration_seconds must be at least min_duration_seconds")
        if self.state is AnalysisState.FAILED and self.error is None:
            raise ValueError("failed analysis manifests require an error")
        if self.state is not AnalysisState.FAILED and self.error is not None:
            raise ValueError("non-failed analysis manifests must not carry an error")
        if self.completed_at is not None and self.completed_at < self.created_at:
            raise ValueError("completed_at must not precede created_at")
        if (
            self.state in {AnalysisState.WAITING_SELECTION, AnalysisState.COMPLETED}
            and self.completed_at is None
        ):
            raise ValueError(f"{self.state} analysis manifests require completed_at")
        if (
            self.state in {AnalysisState.WAITING_SELECTION, AnalysisState.COMPLETED}
            and not self.candidate_paths
        ):
            raise ValueError(f"{self.state} analysis manifests require candidate_paths")
        if (
            self.state in {AnalysisState.WAITING_SELECTION, AnalysisState.COMPLETED}
            and len(self.candidate_paths) != self.candidate_count
        ):
            raise ValueError(
                "candidate_count must equal candidate_paths count for durable analysis states"
            )
        return self


class RenderManifest(ManifestModel):
    render_id: str
    operation_id: str
    kind: RenderKind
    analysis_id: str | None = None
    candidate_id: CandidateId | None = None
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    source_fingerprint: Sha256Digest
    transcript_id: str
    render_config: dict[str, Any]
    artifacts: dict[str, RelativePath]
    artifact_sizes: dict[str, PositiveInt]
    artifact_hashes: dict[str, Sha256Digest] = Field(default_factory=dict)
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
    def validate_invariants(self) -> "RenderManifest":
        if self.end_seconds <= self.start_seconds:
            raise ValueError("end_seconds must be later than start_seconds")
        if self.kind is RenderKind.CANDIDATE:
            if self.analysis_id is None or self.candidate_id is None:
                raise ValueError("candidate renders require analysis_id and candidate_id")
        elif self.analysis_id is not None or self.candidate_id is not None:
            raise ValueError("custom renders must omit analysis_id and candidate_id")
        artifact_keys = set(self.artifacts)
        if set(self.artifact_sizes) != artifact_keys:
            raise ValueError("artifact_sizes keys must exactly match artifacts keys")
        if not set(self.artifact_hashes) <= artifact_keys:
            raise ValueError("artifact_hashes keys must be a subset of artifacts keys")
        if self.render_state is RenderState.FAILED and self.render_error is None:
            raise ValueError("failed render manifests require render_error")
        if self.render_state is not RenderState.FAILED and self.render_error is not None:
            raise ValueError("non-failed render manifests must not carry render_error")
        if self.completed_at is not None and self.completed_at < self.created_at:
            raise ValueError("completed_at must not precede created_at")
        if self.render_state is RenderState.READY and self.completed_at is None:
            raise ValueError("ready render manifests require completed_at")
        if self.render_state is RenderState.READY and not self.artifacts:
            raise ValueError("ready render manifests require artifacts")
        if (
            self.publish_state is not PublishState.NOT_UPLOADED
            and self.render_state is not RenderState.READY
        ):
            raise ValueError("active publish states require render_state ready")
        if self.publish_state is PublishState.UPLOAD_FAILED and self.upload_error is None:
            raise ValueError("upload-failed render manifests require upload_error")
        if (
            self.publish_state is not PublishState.UPLOAD_FAILED
            and self.upload_error is not None
        ):
            raise ValueError("non-failed publish states must not carry upload_error")
        if self.publish_state is PublishState.NOT_UPLOADED:
            if self.upload_started_at is not None:
                raise ValueError("not-uploaded publish state must not carry upload_started_at")
        elif self.upload_started_at is None:
            raise ValueError(f"{self.publish_state} publish state requires upload_started_at")
        if (
            self.upload_started_at is not None
            and self.completed_at is not None
            and self.upload_started_at < self.completed_at
        ):
            raise ValueError("upload_started_at must not precede completed_at")
        uploaded_identity = (
            self.uploaded_at,
            self.youtube_video_id,
            self.youtube_url,
        )
        if self.publish_state is PublishState.UPLOADED:
            if any(value is None for value in uploaded_identity):
                raise ValueError(
                    "uploaded publish state requires uploaded_at, youtube_video_id, and youtube_url"
                )
        elif any(value is not None for value in uploaded_identity):
            raise ValueError("non-uploaded publish states must not carry uploaded identity")
        if (
            self.uploaded_at is not None
            and self.upload_started_at is not None
            and self.uploaded_at < self.upload_started_at
        ):
            raise ValueError("uploaded_at must not precede upload_started_at")
        return self
