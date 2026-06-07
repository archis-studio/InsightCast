from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from insightcast.domain.enums import ErrorCode
from insightcast.domain.models import JobError
from insightcast.storage.manifests import (
    SCHEMA_VERSION,
    AnalysisManifest,
    AnalysisState,
    ManifestState,
    PublishState,
    RenderKind,
    RenderManifest,
    RenderState,
    SourceManifest,
    TranscriptManifest,
    VideoManifest,
)

NOW = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)
LATER = NOW + timedelta(minutes=1)
LATEST = LATER + timedelta(minutes=1)
DIGEST = "a" * 64
UPPER_DIGEST = "A" * 64


def manifest_error() -> JobError:
    return JobError(
        stage="storage",
        error_code=ErrorCode.MANIFEST_INVALID,
        message="Manifest operation failed.",
    )


def source_values(**updates: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "video_id": "abc123DEF_-",
        "source_fingerprint": DIGEST,
        "fingerprint_algorithm": "sha256",
        "source_video_path": Path("source/source.mp4"),
        "source_video_size": 100,
        "transcription_audio_path": Path("source/audio.mp3"),
        "transcription_audio_size": 50,
        "downloaded_at": NOW,
        "audio_extracted_at": NOW,
        "source_metadata": {},
        "state": ManifestState.READY,
    }
    values.update(updates)
    return values


def transcript_values(**updates: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "transcript_id": "transcript-1",
        "cache_key": DIGEST,
        "source_fingerprint": DIGEST,
        "provider": "openai",
        "model": "whisper-1",
        "language": "en",
        "transcript_path": Path("transcripts/transcript-1/transcript.json"),
        "created_at": NOW,
        "state": ManifestState.READY,
    }
    values.update(updates)
    return values


def analysis_values(**updates: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "analysis_id": "20260607-120000-abcdef",
        "operation_id": "operation-1",
        "created_at": NOW,
        "completed_at": NOW,
        "normalized_source_url": "https://www.youtube.com/watch?v=abc123DEF_-",
        "video_id": "abc123DEF_-",
        "transcript_id": "transcript-1",
        "curator_model": "gpt-4.1",
        "prompt_version": "v1",
        "candidate_count": 1,
        "min_duration_seconds": 480,
        "max_duration_seconds": 720,
        "state": AnalysisState.WAITING_SELECTION,
        "candidates_path": Path("analyses/20260607-120000-abcdef/candidates.json"),
        "candidate_paths": {
            "A": Path("analyses/20260607-120000-abcdef/candidates/A")
        },
        "log_path": Path("logs/operation-1.log"),
    }
    values.update(updates)
    return values


def render_values(**updates: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "render_id": "20260607-120000-abcdef",
        "operation_id": "operation-1",
        "kind": RenderKind.CANDIDATE,
        "analysis_id": "20260607-115900-fedcba",
        "candidate_id": "A",
        "start_seconds": 10,
        "end_seconds": 70,
        "source_fingerprint": DIGEST,
        "transcript_id": "transcript-1",
        "render_config": {},
        "artifacts": {
            "video": Path("video.mp4"),
            "youtube_metadata": Path("youtube-metadata.json"),
        },
        "artifact_sizes": {"video": 100, "youtube_metadata": 50},
        "artifact_hashes": {},
        "created_at": NOW,
        "completed_at": NOW,
        "render_state": RenderState.READY,
        "publish_state": PublishState.NOT_UPLOADED,
        "youtube_video_id": None,
        "youtube_url": None,
        "upload_started_at": None,
        "uploaded_at": None,
        "log_path": Path("logs/operation-1.log"),
    }
    values.update(updates)
    return values


def test_manifest_contract_enums_and_error_codes() -> None:
    assert SCHEMA_VERSION == 1
    assert [state.value for state in ManifestState] == ["ready", "failed"]
    assert [kind.value for kind in RenderKind] == ["candidate", "custom"]
    assert [state.value for state in RenderState] == ["queued", "rendering", "ready", "failed"]
    assert [state.value for state in PublishState] == [
        "not-uploaded",
        "uploading",
        "uploaded",
        "upload-failed",
    ]
    assert ErrorCode.STORAGE_CONFLICT == "STORAGE_CONFLICT"
    assert ErrorCode.MANIFEST_INVALID == "MANIFEST_INVALID"
    assert ErrorCode.SOURCE_FINGERPRINT_MISMATCH == "SOURCE_FINGERPRINT_MISMATCH"
    assert ErrorCode.TRANSCRIPT_CACHE_INVALID == "TRANSCRIPT_CACHE_INVALID"
    assert ErrorCode.RENDER_NOT_FOUND == "RENDER_NOT_FOUND"
    assert ErrorCode.RENDER_NOT_PUBLISHABLE == "RENDER_NOT_PUBLISHABLE"
    assert ErrorCode.ARTIFACT_PATH_INVALID == "ARTIFACT_PATH_INVALID"
    assert ErrorCode.INVALID_PUBLISH_STATE == "INVALID_PUBLISH_STATE"


def test_manifest_models_are_strict_and_require_schema_version_one() -> None:
    manifest = VideoManifest(
        video_id="abc123DEF_-",
        original_youtube_url="https://youtu.be/abc123DEF_-",
        normalized_youtube_url="https://www.youtube.com/watch?v=abc123DEF_-",
        title="A Useful Talk",
        uploader=None,
        upload_date=None,
        first_seen_at=NOW,
        last_seen_at=NOW,
        source_manifest_path=Path("source/manifest.json"),
    )

    assert manifest.schema_version == 1
    with pytest.raises(ValidationError):
        VideoManifest.model_validate({**manifest.model_dump(), "unexpected": True})
    with pytest.raises(ValidationError):
        VideoManifest.model_validate({**manifest.model_dump(), "schema_version": 2})
    with pytest.raises(ValidationError):
        SourceManifest(**source_values(source_video_size="100"))


@pytest.mark.parametrize(
    "path",
    [
        Path(""),
        Path("."),
        Path("/tmp/video.mp4"),
        Path("../video.mp4"),
        Path(r"C:\video.mp4"),
        Path(r"C:video.mp4"),
        Path(r"clips\..\video.mp4"),
    ],
)
def test_manifest_paths_reject_empty_current_absolute_drive_and_traversal(path: Path) -> None:
    with pytest.raises(ValidationError, match="relative path"):
        VideoManifest(
            video_id="abc123DEF_-",
            original_youtube_url="https://youtu.be/abc123DEF_-",
            normalized_youtube_url="https://www.youtube.com/watch?v=abc123DEF_-",
            title="A Useful Talk",
            uploader=None,
            upload_date=None,
            first_seen_at=NOW,
            last_seen_at=NOW,
            source_manifest_path=path,
        )


@pytest.mark.parametrize("candidate_id", ["../A", "lower", "AA"])
def test_analysis_manifest_rejects_invalid_candidate_path_keys(candidate_id: str) -> None:
    with pytest.raises(ValidationError, match="candidate ID"):
        AnalysisManifest(
            **analysis_values(
                candidate_paths={
                    candidate_id: Path("analyses/20260607-120000-abcdef/candidates/A")
                }
            )
        )


@pytest.mark.parametrize("candidate_id", ["A", "Z"])
def test_analysis_and_render_manifests_accept_boundary_candidate_ids(
    candidate_id: str,
) -> None:
    analysis = AnalysisManifest(
        **analysis_values(
            candidate_paths={
                candidate_id: Path(
                    f"analyses/20260607-120000-abcdef/candidates/{candidate_id}"
                )
            }
        )
    )
    render = RenderManifest(**render_values(candidate_id=candidate_id))

    assert list(analysis.candidate_paths) == [candidate_id]
    assert render.candidate_id == candidate_id


@pytest.mark.parametrize("candidate_id", ["../A", "lower", "AA"])
def test_render_manifest_rejects_invalid_candidate_ids(candidate_id: str) -> None:
    with pytest.raises(ValidationError, match="candidate ID"):
        RenderManifest(**render_values(candidate_id=candidate_id))


def test_custom_render_manifest_omits_candidate_identity() -> None:
    manifest = RenderManifest(
        **render_values(
            kind=RenderKind.CUSTOM,
            analysis_id=None,
            candidate_id=None,
        )
    )

    assert manifest.analysis_id is None
    assert manifest.candidate_id is None


@pytest.mark.parametrize(
    ("kind", "analysis_id", "candidate_id"),
    [
        (RenderKind.CANDIDATE, "20260607-115900-fedcba", None),
        (RenderKind.CUSTOM, "20260607-115900-fedcba", "A"),
    ],
)
def test_render_manifest_enforces_candidate_identity_by_kind(
    kind: RenderKind,
    analysis_id: str | None,
    candidate_id: str | None,
) -> None:
    with pytest.raises(ValidationError):
        RenderManifest(
            **render_values(
                kind=kind,
                analysis_id=analysis_id,
                candidate_id=candidate_id,
            )
        )


def test_render_manifest_rejects_absolute_artifact_paths() -> None:
    with pytest.raises(ValidationError, match="relative path"):
        RenderManifest(
            **render_values(
                artifacts={
                    "video": Path("/tmp/video.mp4"),
                    "youtube_metadata": Path("youtube-metadata.json"),
                }
            )
        )


@pytest.mark.parametrize(
    ("artifact_sizes", "artifact_hashes"),
    [
        ({"video": 100}, {}),
        ({"video": 100, "youtube_metadata": 50}, {"subtitles": DIGEST}),
    ],
)
def test_render_manifest_rejects_inconsistent_artifact_maps(
    artifact_sizes: dict[str, int],
    artifact_hashes: dict[str, str],
) -> None:
    with pytest.raises(ValidationError, match="artifact"):
        RenderManifest(
            **render_values(
                artifact_sizes=artifact_sizes,
                artifact_hashes=artifact_hashes,
            )
        )


@pytest.mark.parametrize("digest", ["a" * 63, "g" * 64, "a" * 65])
def test_manifests_reject_invalid_sha256_digests(digest: str) -> None:
    with pytest.raises(ValidationError):
        SourceManifest(**source_values(source_fingerprint=digest))
    with pytest.raises(ValidationError):
        TranscriptManifest(**transcript_values(cache_key=digest))
    with pytest.raises(ValidationError):
        RenderManifest(**render_values(artifact_hashes={"video": digest}))


def test_manifests_accept_uppercase_sha256_digests() -> None:
    source = SourceManifest(**source_values(source_fingerprint=UPPER_DIGEST))
    transcript = TranscriptManifest(
        **transcript_values(cache_key=UPPER_DIGEST, source_fingerprint=UPPER_DIGEST)
    )
    render = RenderManifest(
        **render_values(
            source_fingerprint=UPPER_DIGEST,
            artifact_hashes={"video": UPPER_DIGEST},
        )
    )

    assert source.source_fingerprint == UPPER_DIGEST
    assert transcript.cache_key == UPPER_DIGEST
    assert render.artifact_hashes["video"] == UPPER_DIGEST


@pytest.mark.parametrize(
    ("model", "values"),
    [
        (SourceManifest, source_values(state=ManifestState.FAILED)),
        (TranscriptManifest, transcript_values(state=ManifestState.FAILED)),
        (AnalysisManifest, analysis_values(state=AnalysisState.FAILED, completed_at=None)),
        (
            RenderManifest,
            render_values(render_state=RenderState.FAILED, completed_at=None),
        ),
        (
            RenderManifest,
            render_values(publish_state=PublishState.UPLOAD_FAILED),
        ),
    ],
)
def test_failed_manifest_states_require_structured_errors(
    model: type[Any],
    values: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError, match="error"):
        model(**values)


def test_failed_manifest_states_accept_structured_errors() -> None:
    SourceManifest(**source_values(state=ManifestState.FAILED, error=manifest_error()))
    TranscriptManifest(
        **transcript_values(state=ManifestState.FAILED, error=manifest_error())
    )
    AnalysisManifest(
        **analysis_values(
            state=AnalysisState.FAILED,
            completed_at=None,
            error=manifest_error(),
        )
    )
    RenderManifest(
        **render_values(
            render_state=RenderState.FAILED,
            completed_at=None,
            render_error=manifest_error(),
        )
    )
    RenderManifest(
        **render_values(
            publish_state=PublishState.UPLOAD_FAILED,
            upload_started_at=LATER,
            upload_error=manifest_error(),
        )
    )


@pytest.mark.parametrize(
    ("model", "values"),
    [
        (SourceManifest, source_values(error=manifest_error())),
        (TranscriptManifest, transcript_values(error=manifest_error())),
        (AnalysisManifest, analysis_values(error=manifest_error())),
        (RenderManifest, render_values(render_error=manifest_error())),
        (RenderManifest, render_values(upload_error=manifest_error())),
    ],
)
def test_successful_manifest_states_reject_stale_errors(
    model: type[Any],
    values: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError, match="error"):
        model(**values)


@pytest.mark.parametrize("state", [AnalysisState.WAITING_SELECTION, AnalysisState.COMPLETED])
def test_ready_analysis_states_require_completion_time(state: AnalysisState) -> None:
    with pytest.raises(ValidationError, match="completed_at"):
        AnalysisManifest(**analysis_values(state=state, completed_at=None))


@pytest.mark.parametrize("state", [AnalysisState.WAITING_SELECTION, AnalysisState.COMPLETED])
def test_ready_analysis_states_require_candidate_paths(state: AnalysisState) -> None:
    with pytest.raises(ValidationError, match="candidate_paths"):
        AnalysisManifest(**analysis_values(state=state, candidate_paths={}))


@pytest.mark.parametrize("state", [AnalysisState.WAITING_SELECTION, AnalysisState.COMPLETED])
def test_ready_analysis_candidate_count_matches_paths(state: AnalysisState) -> None:
    with pytest.raises(ValidationError, match="candidate_count"):
        AnalysisManifest(**analysis_values(state=state, candidate_count=2))


def test_ready_render_requires_completion_time() -> None:
    with pytest.raises(ValidationError, match="completed_at"):
        RenderManifest(**render_values(completed_at=None))


def test_ready_render_requires_artifacts() -> None:
    with pytest.raises(ValidationError, match="artifacts"):
        RenderManifest(
            **render_values(
                artifacts={},
                artifact_sizes={},
            )
        )


def test_active_analysis_and_render_states_remain_constructible() -> None:
    AnalysisManifest(
        **analysis_values(
            state=AnalysisState.QUEUED,
            completed_at=None,
            candidate_paths={},
        )
    )
    AnalysisManifest(
        **analysis_values(
            state=AnalysisState.RUNNING,
            completed_at=None,
            candidate_paths={},
        )
    )
    RenderManifest(
        **render_values(
            render_state=RenderState.QUEUED,
            completed_at=None,
            artifacts={},
            artifact_sizes={},
        )
    )
    RenderManifest(
        **render_values(
            render_state=RenderState.RENDERING,
            completed_at=None,
            artifacts={},
            artifact_sizes={},
        )
    )


def test_uploaded_publish_state_requires_remote_identity_and_timestamp() -> None:
    with pytest.raises(ValidationError, match="uploaded"):
        RenderManifest(
            **render_values(
                publish_state=PublishState.UPLOADED,
                upload_started_at=LATER,
            )
        )

    RenderManifest(
        **render_values(
            publish_state=PublishState.UPLOADED,
            youtube_video_id="remote123",
            youtube_url="https://www.youtube.com/watch?v=remote123",
            upload_started_at=LATER,
            uploaded_at=LATEST,
        )
    )


@pytest.mark.parametrize(
    "publish_state",
    [
        PublishState.NOT_UPLOADED,
        PublishState.UPLOADING,
        PublishState.UPLOAD_FAILED,
    ],
)
def test_non_uploaded_publish_states_reject_uploaded_identity(
    publish_state: PublishState,
) -> None:
    updates: dict[str, Any] = {
        "publish_state": publish_state,
        "youtube_video_id": "remote123",
        "youtube_url": "https://www.youtube.com/watch?v=remote123",
        "uploaded_at": NOW,
    }
    if publish_state is not PublishState.NOT_UPLOADED:
        updates["upload_started_at"] = NOW
    if publish_state is PublishState.UPLOAD_FAILED:
        updates["upload_error"] = manifest_error()

    with pytest.raises(ValidationError, match="uploaded"):
        RenderManifest(**render_values(**updates))


@pytest.mark.parametrize(
    "publish_state",
    [
        PublishState.UPLOADING,
        PublishState.UPLOADED,
        PublishState.UPLOAD_FAILED,
    ],
)
def test_active_publish_states_require_ready_render(
    publish_state: PublishState,
) -> None:
    updates: dict[str, Any] = {
        "render_state": RenderState.RENDERING,
        "completed_at": None,
        "publish_state": publish_state,
        "upload_started_at": LATER,
    }
    if publish_state is PublishState.UPLOADED:
        updates.update(
            youtube_video_id="remote123",
            youtube_url="https://www.youtube.com/watch?v=remote123",
            uploaded_at=LATEST,
        )
    if publish_state is PublishState.UPLOAD_FAILED:
        updates["upload_error"] = manifest_error()

    with pytest.raises(ValidationError, match="ready"):
        RenderManifest(**render_values(**updates))


@pytest.mark.parametrize(
    "render_state",
    [RenderState.QUEUED, RenderState.RENDERING, RenderState.FAILED],
)
def test_non_ready_renders_remain_not_uploaded(render_state: RenderState) -> None:
    updates: dict[str, Any] = {
        "render_state": render_state,
        "completed_at": None,
        "publish_state": PublishState.UPLOADING,
        "upload_started_at": LATER,
    }
    if render_state is RenderState.FAILED:
        updates["render_error"] = manifest_error()

    with pytest.raises(ValidationError, match="ready"):
        RenderManifest(**render_values(**updates))


@pytest.mark.parametrize(
    "publish_state",
    [
        PublishState.UPLOADING,
        PublishState.UPLOADED,
        PublishState.UPLOAD_FAILED,
    ],
)
def test_active_publish_states_require_upload_started_at(
    publish_state: PublishState,
) -> None:
    updates: dict[str, Any] = {"publish_state": publish_state}
    if publish_state is PublishState.UPLOADED:
        updates.update(
            youtube_video_id="remote123",
            youtube_url="https://www.youtube.com/watch?v=remote123",
            uploaded_at=LATEST,
        )
    if publish_state is PublishState.UPLOAD_FAILED:
        updates["upload_error"] = manifest_error()

    with pytest.raises(ValidationError, match="upload_started_at"):
        RenderManifest(**render_values(**updates))


def test_not_uploaded_publish_state_rejects_upload_started_at() -> None:
    with pytest.raises(ValidationError, match="upload_started_at"):
        RenderManifest(**render_values(upload_started_at=LATER))


def test_valid_render_publish_lifecycle_states() -> None:
    RenderManifest(**render_values())
    RenderManifest(
        **render_values(
            publish_state=PublishState.UPLOADING,
            upload_started_at=LATER,
        )
    )
    RenderManifest(
        **render_values(
            publish_state=PublishState.UPLOADED,
            upload_started_at=LATER,
            uploaded_at=LATEST,
            youtube_video_id="remote123",
            youtube_url="https://www.youtube.com/watch?v=remote123",
        )
    )
    RenderManifest(
        **render_values(
            publish_state=PublishState.UPLOAD_FAILED,
            upload_started_at=LATER,
            upload_error=manifest_error(),
        )
    )
    RenderManifest(
        **render_values(
            render_state=RenderState.FAILED,
            publish_state=PublishState.NOT_UPLOADED,
            completed_at=None,
            render_error=manifest_error(),
        )
    )


def test_analysis_completion_timestamp_cannot_precede_creation() -> None:
    with pytest.raises(ValidationError, match="completed_at"):
        AnalysisManifest(
            **analysis_values(completed_at=NOW - timedelta(seconds=1))
        )


def test_render_completion_timestamp_cannot_precede_creation() -> None:
    with pytest.raises(ValidationError, match="completed_at"):
        RenderManifest(
            **render_values(completed_at=NOW - timedelta(seconds=1))
        )


def test_upload_start_cannot_precede_render_completion() -> None:
    with pytest.raises(ValidationError, match="upload_started_at"):
        RenderManifest(
            **render_values(
                publish_state=PublishState.UPLOADING,
                upload_started_at=NOW - timedelta(seconds=1),
            )
        )


def test_uploaded_timestamp_cannot_precede_upload_start() -> None:
    with pytest.raises(ValidationError, match="uploaded_at"):
        RenderManifest(
            **render_values(
                publish_state=PublishState.UPLOADED,
                upload_started_at=LATEST,
                uploaded_at=LATER,
                youtube_video_id="remote123",
                youtube_url="https://www.youtube.com/watch?v=remote123",
            )
        )
