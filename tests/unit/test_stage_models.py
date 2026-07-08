from datetime import UTC, datetime
from pathlib import Path

import pytest

from insightcast.domain.enums import ErrorCode
from insightcast.domain.models import JobError
from insightcast.domain.stages import PipelineStage, StageManifest, StageRecord, StageStatus

NOW = datetime(2026, 6, 19, 12, 0, tzinfo=UTC)


def test_stage_record_accepts_completed_stage_with_elapsed_time() -> None:
    record = StageRecord(
        stage=PipelineStage.TRANSLATE_SUBTITLES,
        status=StageStatus.COMPLETED,
        started_at=NOW,
        completed_at=NOW,
        elapsed_seconds=3.5,
        artifacts={"batch": Path("translations/batch-0001.json")},
        metadata={"source_media": {"codec": "h264"}},
        resume_strategy="reuse completed translation batch",
    )

    assert record.stage is PipelineStage.TRANSLATE_SUBTITLES
    assert record.status is StageStatus.COMPLETED
    assert record.artifacts["batch"] == Path("translations/batch-0001.json")
    assert record.metadata["source_media"] == {"codec": "h264"}


def test_stage_record_requires_error_for_failed_stage() -> None:
    with pytest.raises(ValueError, match="failed stages require error"):
        StageRecord(
            stage=PipelineStage.BURN_SUBTITLES,
            status=StageStatus.FAILED,
            started_at=NOW,
            completed_at=NOW,
            elapsed_seconds=1.0,
            resume_strategy="rerun burn_subtitles",
        )


def test_stage_record_rejects_error_for_non_failed_stage() -> None:
    with pytest.raises(ValueError, match="non-failed stages must not carry error"):
        StageRecord(
            stage=PipelineStage.CUT_CLIP,
            status=StageStatus.COMPLETED,
            started_at=NOW,
            completed_at=NOW,
            elapsed_seconds=1.0,
            resume_strategy="reuse cut clip",
            error=JobError(
                stage="cut_clip",
                error_code=ErrorCode.VIDEO_RENDER_FAILED,
                message="Unexpected error.",
            ),
        )


def test_stage_record_rejects_completion_before_start() -> None:
    with pytest.raises(ValueError, match="completed_at must not precede started_at"):
        StageRecord(
            stage=PipelineStage.CUT_CLIP,
            status=StageStatus.COMPLETED,
            started_at=datetime(2026, 6, 19, 12, 1, tzinfo=UTC),
            completed_at=NOW,
            elapsed_seconds=1.0,
            resume_strategy="reuse cut clip",
        )


def test_stage_manifest_rejects_unknown_schema_version() -> None:
    with pytest.raises(ValueError):
        StageManifest(schema_version=2, operation_id="job-1", render_id="render-1")


def test_stage_manifest_reports_latest_resume_point() -> None:
    manifest = StageManifest(
        schema_version=1,
        operation_id="job-1",
        render_id="render-1",
        candidate_id="A",
        stages=[
            StageRecord(
                stage=PipelineStage.CUT_CLIP,
                status=StageStatus.COMPLETED,
                started_at=NOW,
                completed_at=NOW,
                elapsed_seconds=1.0,
                resume_strategy="reuse cut clip",
            ),
            StageRecord(
                stage=PipelineStage.TRANSLATE_SUBTITLES,
                status=StageStatus.FAILED,
                started_at=NOW,
                completed_at=NOW,
                elapsed_seconds=2.0,
                resume_strategy="resume failed translation batch",
                error=JobError(
                    stage="translate_subtitles",
                    error_code=ErrorCode.SUBTITLE_REPAIR_EXHAUSTED,
                    message="Subtitle repair exhausted.",
                    details={"segment_id": "s1"},
                ),
            ),
        ],
    )

    assert manifest.current_stage is PipelineStage.TRANSLATE_SUBTITLES
    assert manifest.resume_from == "translate_subtitles"
