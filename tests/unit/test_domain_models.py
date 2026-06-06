from datetime import UTC
from pathlib import Path

from insightcast.domain.enums import ErrorCode, JobStatus, JobType
from insightcast.domain.models import (
    AnalysisJob,
    Candidate,
    CandidateSelectionRequest,
    JobError,
    RenderArtifacts,
    TranscriptSegment,
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

