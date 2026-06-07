from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from insightcast.domain.enums import ErrorCode, JobStatus, JobType
from insightcast.domain.models import (
    AnalysisJob,
    Candidate,
    CandidateSelectionRequest,
    JobError,
    RenderArtifacts,
    TranscriptSegment,
)
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


def test_candidate_selection_normalizes_string_and_duplicate_list() -> None:
    assert CandidateSelectionRequest(candidate_ids="A").candidate_ids == ["A"]
    assert CandidateSelectionRequest(candidate_ids=["A", "C", "A"]).candidate_ids == ["A", "C"]


def test_candidate_exposes_duration_seconds() -> None:
    candidate = Candidate(
        candidate_id="A",
        start_seconds=10,
        end_seconds=75.5,
        suggested_title="A useful idea",
        selection_reason="Complete explanation",
        summary="A concise summary.",
    )

    assert candidate.duration_seconds == 65.5


def test_job_models_use_utc_timestamps_and_structured_values(tmp_path: Path) -> None:
    error = JobError(
        stage="curating",
        error_code=ErrorCode.INSUFFICIENT_CANDIDATES,
        message="Not enough valid candidates.",
        details={"requested": 2, "received": 1},
    )
    job = AnalysisJob(
        job_id="job-1",
        job_type=JobType.ANALYSIS,
        original_youtube_url="https://youtu.be/abc123def45",
        normalized_youtube_url="https://www.youtube.com/watch?v=abc123def45",
        status=JobStatus.FAILED,
        message="Curation failed.",
        output_dir=(tmp_path / "job").resolve(),
        error=error,
    )

    assert job.created_at.tzinfo is UTC
    assert job.updated_at.tzinfo is UTC
    assert job.error is not None
    assert job.error.error_code == ErrorCode.INSUFFICIENT_CANDIDATES


def test_transcript_segment_and_render_artifacts_are_typed(tmp_path: Path) -> None:
    segment = TranscriptSegment(
        segment_id="s1",
        start_seconds=1.25,
        end_seconds=3.5,
        text="Hello world.",
    )
    artifacts = RenderArtifacts(
        traditional_chinese_srt=(tmp_path / "clip.zh-TW.srt").resolve(),
        bilingual_ass=(tmp_path / "clip.bilingual.ass").resolve(),
        burned_video=(tmp_path / "clip.burned.mp4").resolve(),
        youtube_metadata=(tmp_path / "clip.youtube-metadata.json").resolve(),
    )

    assert segment.end_seconds > segment.start_seconds
    assert artifacts.burned_video.is_absolute()


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
    now = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)
    manifest = VideoManifest(
        video_id="abc123DEF_-",
        original_youtube_url="https://youtu.be/abc123DEF_-",
        normalized_youtube_url="https://www.youtube.com/watch?v=abc123DEF_-",
        title="A Useful Talk",
        uploader=None,
        upload_date=None,
        first_seen_at=now,
        last_seen_at=now,
        source_manifest_path=Path("source/manifest.json"),
    )

    assert manifest.schema_version == 1
    with pytest.raises(ValidationError):
        VideoManifest.model_validate({**manifest.model_dump(), "unexpected": True})
    with pytest.raises(ValidationError):
        VideoManifest.model_validate({**manifest.model_dump(), "schema_version": 2})


def test_manifest_models_reject_numeric_strings_in_numeric_fields() -> None:
    now = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)

    with pytest.raises(ValidationError):
        SourceManifest(
            video_id="abc123DEF_-",
            source_fingerprint="a" * 64,
            fingerprint_algorithm="sha256",
            source_video_path=Path("source/source.mp4"),
            source_video_size="100",
            transcription_audio_path=Path("source/audio.mp3"),
            transcription_audio_size=50,
            downloaded_at=now,
            audio_extracted_at=now,
            source_metadata={},
            state=ManifestState.READY,
        )


def test_all_manifest_path_fields_reject_traversal() -> None:
    now = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)

    with pytest.raises(ValidationError, match="relative path"):
        SourceManifest(
            video_id="abc123DEF_-",
            source_fingerprint="a" * 64,
            fingerprint_algorithm="sha256",
            source_video_path=Path("../source.mp4"),
            source_video_size=100,
            transcription_audio_path=Path("source/audio.mp3"),
            transcription_audio_size=50,
            downloaded_at=now,
            audio_extracted_at=now,
            source_metadata={},
            state=ManifestState.READY,
        )


@pytest.mark.parametrize("artifact_path", [Path(r"C:\video.mp4"), Path(r"clips\..\video.mp4")])
def test_manifest_paths_reject_windows_absolute_and_traversing_paths(
    artifact_path: Path,
) -> None:
    with pytest.raises(ValidationError, match="relative path"):
        VideoManifest(
            video_id="abc123DEF_-",
            original_youtube_url="https://youtu.be/abc123DEF_-",
            normalized_youtube_url="https://www.youtube.com/watch?v=abc123DEF_-",
            title="A Useful Talk",
            uploader=None,
            upload_date=None,
            first_seen_at=datetime(2026, 6, 7, 12, 0, tzinfo=UTC),
            last_seen_at=datetime(2026, 6, 7, 12, 0, tzinfo=UTC),
            source_manifest_path=artifact_path,
        )


def test_transcript_and_analysis_manifests_expose_approved_contracts() -> None:
    now = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)
    transcript = TranscriptManifest(
        transcript_id="transcript-1",
        cache_key="f" * 64,
        source_fingerprint="a" * 64,
        provider="openai",
        model="whisper-1",
        language="en",
        transcript_path=Path("transcripts/transcript-1/transcript.json"),
        created_at=now,
        state=ManifestState.READY,
    )
    analysis = AnalysisManifest(
        analysis_id="20260607-120000-abcdef",
        operation_id="operation-1",
        created_at=now,
        completed_at=None,
        normalized_source_url="https://www.youtube.com/watch?v=abc123DEF_-",
        video_id="abc123DEF_-",
        transcript_id=transcript.transcript_id,
        curator_model="gpt-4.1",
        prompt_version="v1",
        candidate_count=2,
        min_duration_seconds=480,
        max_duration_seconds=720,
        state=AnalysisState.WAITING_SELECTION,
        candidates_path=Path("analyses/20260607-120000-abcdef/candidates.json"),
        candidate_paths={
            "A": Path("analyses/20260607-120000-abcdef/candidates/A"),
            "B": Path("analyses/20260607-120000-abcdef/candidates/B"),
        },
        log_path=Path("logs/operation-1.log"),
    )

    assert transcript.state is ManifestState.READY
    assert analysis.state is AnalysisState.WAITING_SELECTION


@pytest.mark.parametrize("candidate_id", ["../A", "lower", "AA"])
def test_analysis_manifest_rejects_invalid_candidate_path_keys(candidate_id: str) -> None:
    now = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)

    with pytest.raises(ValidationError, match="candidate ID"):
        AnalysisManifest(
            analysis_id="20260607-120000-abcdef",
            operation_id="operation-1",
            created_at=now,
            completed_at=None,
            normalized_source_url="https://www.youtube.com/watch?v=abc123DEF_-",
            video_id="abc123DEF_-",
            transcript_id="transcript-1",
            curator_model="gpt-4.1",
            prompt_version="v1",
            candidate_count=1,
            min_duration_seconds=480,
            max_duration_seconds=720,
            state=AnalysisState.WAITING_SELECTION,
            candidates_path=Path("analyses/20260607-120000-abcdef/candidates.json"),
            candidate_paths={
                candidate_id: Path("analyses/20260607-120000-abcdef/candidates/A")
            },
            log_path=Path("logs/operation-1.log"),
        )


@pytest.mark.parametrize("candidate_id", ["A", "Z"])
def test_analysis_manifest_accepts_boundary_candidate_path_keys(candidate_id: str) -> None:
    now = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)

    manifest = AnalysisManifest(
        analysis_id="20260607-120000-abcdef",
        operation_id="operation-1",
        created_at=now,
        completed_at=None,
        normalized_source_url="https://www.youtube.com/watch?v=abc123DEF_-",
        video_id="abc123DEF_-",
        transcript_id="transcript-1",
        curator_model="gpt-4.1",
        prompt_version="v1",
        candidate_count=1,
        min_duration_seconds=480,
        max_duration_seconds=720,
        state=AnalysisState.WAITING_SELECTION,
        candidates_path=Path("analyses/20260607-120000-abcdef/candidates.json"),
        candidate_paths={
            candidate_id: Path(f"analyses/20260607-120000-abcdef/candidates/{candidate_id}")
        },
        log_path=Path("logs/operation-1.log"),
    )

    assert list(manifest.candidate_paths) == [candidate_id]


@pytest.mark.parametrize("candidate_id", ["../A", "lower", "AA"])
def test_render_manifest_rejects_invalid_candidate_ids(candidate_id: str) -> None:
    now = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)

    with pytest.raises(ValidationError, match="candidate ID"):
        RenderManifest(
            render_id="20260607-120000-abcdef",
            operation_id="operation-1",
            kind=RenderKind.CANDIDATE,
            analysis_id="20260607-115900-fedcba",
            candidate_id=candidate_id,
            start_seconds=10,
            end_seconds=70,
            source_fingerprint="a" * 64,
            transcript_id="transcript-1",
            render_config={},
            artifacts={"video": Path("video.mp4")},
            artifact_sizes={"video": 100},
            created_at=now,
            completed_at=None,
            render_state=RenderState.READY,
            publish_state=PublishState.NOT_UPLOADED,
            youtube_video_id=None,
            youtube_url=None,
            upload_started_at=None,
            uploaded_at=None,
            log_path=Path("logs/operation-1.log"),
        )


@pytest.mark.parametrize("candidate_id", ["A", "Z"])
def test_render_manifest_accepts_boundary_candidate_ids(candidate_id: str) -> None:
    now = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)

    manifest = RenderManifest(
        render_id="20260607-120000-abcdef",
        operation_id="operation-1",
        kind=RenderKind.CANDIDATE,
        analysis_id="20260607-115900-fedcba",
        candidate_id=candidate_id,
        start_seconds=10,
        end_seconds=70,
        source_fingerprint="a" * 64,
        transcript_id="transcript-1",
        render_config={},
        artifacts={"video": Path("video.mp4")},
        artifact_sizes={"video": 100},
        created_at=now,
        completed_at=None,
        render_state=RenderState.READY,
        publish_state=PublishState.NOT_UPLOADED,
        youtube_video_id=None,
        youtube_url=None,
        upload_started_at=None,
        uploaded_at=None,
        log_path=Path("logs/operation-1.log"),
    )

    assert manifest.candidate_id == candidate_id


def test_custom_render_manifest_omits_candidate_identity() -> None:
    now = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)

    manifest = RenderManifest(
        render_id="20260607-120000-abcdef",
        operation_id="operation-1",
        kind=RenderKind.CUSTOM,
        start_seconds=10,
        end_seconds=70,
        source_fingerprint="a" * 64,
        transcript_id="transcript-1",
        render_config={},
        artifacts={"video": Path("video.mp4")},
        artifact_sizes={"video": 100},
        created_at=now,
        completed_at=None,
        render_state=RenderState.READY,
        publish_state=PublishState.NOT_UPLOADED,
        youtube_video_id=None,
        youtube_url=None,
        upload_started_at=None,
        uploaded_at=None,
        log_path=Path("logs/operation-1.log"),
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
    now = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)

    with pytest.raises(ValidationError):
        RenderManifest(
            render_id="20260607-120000-abcdef",
            operation_id="operation-1",
            kind=kind,
            analysis_id=analysis_id,
            candidate_id=candidate_id,
            start_seconds=10,
            end_seconds=70,
            source_fingerprint="a" * 64,
            transcript_id="transcript-1",
            render_config={},
            artifacts={"video": Path("video.mp4")},
            artifact_sizes={"video": 100},
            created_at=now,
            completed_at=None,
            render_state=RenderState.READY,
            publish_state=PublishState.NOT_UPLOADED,
            youtube_video_id=None,
            youtube_url=None,
            upload_started_at=None,
            uploaded_at=None,
            log_path=Path("logs/operation-1.log"),
        )


def test_render_manifest_rejects_absolute_artifact_paths() -> None:
    now = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)

    with pytest.raises(ValidationError, match="relative path"):
        RenderManifest(
            render_id="20260607-120000-abcdef",
            operation_id="operation-1",
            kind=RenderKind.CANDIDATE,
            analysis_id="20260607-115900-fedcba",
            candidate_id="A",
            start_seconds=10,
            end_seconds=70,
            source_fingerprint="a" * 64,
            transcript_id="transcript-1",
            render_config={"subtitle_language": "zh-TW"},
            artifacts={
                "video": Path("/tmp/video.mp4"),
                "youtube_metadata": Path("youtube-metadata.json"),
            },
            artifact_sizes={"video": 100, "youtube_metadata": 50},
            artifact_hashes={},
            created_at=now,
            completed_at=None,
            render_state=RenderState.QUEUED,
            publish_state=PublishState.NOT_UPLOADED,
            youtube_video_id=None,
            youtube_url=None,
            upload_started_at=None,
            uploaded_at=None,
            log_path=Path("logs/operation-1.log"),
        )
