from datetime import UTC, datetime

from insightcast.domain.stages import PipelineStage, StageManifest, StageRecord, StageStatus
from insightcast.storage.stage_store import StageStore


NOW = datetime(2026, 6, 19, 12, 0, tzinfo=UTC)


def test_stage_store_round_trips_manifest(tmp_path) -> None:
    store = StageStore()
    path = tmp_path / "stage-manifest.json"
    manifest = StageManifest(
        operation_id="job-1",
        render_id="render-1",
        candidate_id="A",
        stages=[
            StageRecord(
                stage=PipelineStage.CUT_CLIP,
                status=StageStatus.COMPLETED,
                started_at=NOW,
                completed_at=NOW,
                elapsed_seconds=1.2,
                resume_strategy="reuse cut clip",
            )
        ],
    )

    store.write(path, manifest)
    loaded = store.read(path)

    assert loaded == manifest
    assert path.read_text(encoding="utf-8").endswith("\n")


def test_stage_store_returns_none_for_missing_manifest(tmp_path) -> None:
    assert StageStore().read_optional(tmp_path / "missing.json") is None
