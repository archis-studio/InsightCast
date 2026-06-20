from datetime import UTC, datetime
from pathlib import Path

import pytest

from insightcast.core.exceptions import InsightCastError
from insightcast.domain.enums import ErrorCode
from insightcast.domain.stages import PipelineStage, StageManifest, StageRecord, StageStatus
from insightcast.storage.stage_store import StageStore

NOW = datetime(2026, 6, 19, 12, 0, tzinfo=UTC)


def make_manifest() -> StageManifest:
    return StageManifest(
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


def test_stage_store_round_trips_manifest(tmp_path) -> None:
    store = StageStore()
    path = tmp_path / "stage-manifest.json"
    manifest = make_manifest()

    store.write(path, manifest)
    loaded = store.read(path)

    assert loaded == manifest
    assert path.read_text(encoding="utf-8").endswith("\n")


def test_stage_store_returns_none_for_missing_manifest(tmp_path) -> None:
    assert StageStore().read_optional(tmp_path / "missing.json") is None


def test_stage_store_rejects_symlink_manifest(tmp_path) -> None:
    external_manifest = tmp_path / "external-stage-manifest.json"
    external_contents = b'{"external": true}\n'
    external_manifest.write_bytes(external_contents)
    path = tmp_path / "stage-manifest.json"
    path.symlink_to(external_manifest)

    with pytest.raises(InsightCastError) as error:
        StageStore().write(path, make_manifest())

    assert error.value.error_code == ErrorCode.MANIFEST_INVALID
    assert error.value.stage == "stage_manifest"
    assert error.value.details == {
        "manifest_path": str(path.expanduser().parent.resolve() / path.name),
        "reason": "not_regular_file",
    }
    assert external_manifest.read_bytes() == external_contents
    assert path.is_symlink()


def test_stage_store_read_optional_rejects_symlink_manifest(tmp_path) -> None:
    external_manifest = tmp_path / "external-stage-manifest.json"
    external_manifest.write_text(
        make_manifest().model_dump_json(indent=2, exclude_computed_fields=True) + "\n",
        encoding="utf-8",
    )
    path = tmp_path / "stage-manifest.json"
    path.symlink_to(external_manifest)

    with pytest.raises(InsightCastError) as error:
        StageStore().read_optional(path)

    assert error.value.error_code == ErrorCode.MANIFEST_INVALID
    assert error.value.stage == "stage_manifest"
    assert error.value.details == {
        "manifest_path": str(path.expanduser().parent.resolve() / path.name),
        "reason": "not_regular_file",
    }


def test_stage_store_removes_temporary_file_when_replace_fails(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "stage-manifest.json"

    def fail_replace(self: Path, target: Path) -> Path:
        raise OSError("injected replace failure")

    monkeypatch.setattr(Path, "replace", fail_replace)

    with pytest.raises(OSError, match="injected replace failure"):
        StageStore().write(path, make_manifest())

    assert list(tmp_path.glob(".stage-manifest.json.*.tmp")) == []
