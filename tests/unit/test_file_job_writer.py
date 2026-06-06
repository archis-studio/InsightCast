import json
import logging
from pathlib import Path

from insightcast.core.logging import get_job_logger
from insightcast.domain.enums import JobStatus, JobType
from insightcast.domain.models import AnalysisJob
from insightcast.storage.file_job_writer import FileJobWriter


def make_job(tmp_path: Path) -> AnalysisJob:
    return AnalysisJob(
        job_id="job-1",
        job_type=JobType.ANALYSIS,
        original_youtube_url="https://youtu.be/abc123DEF_-",
        normalized_youtube_url="https://www.youtube.com/watch?v=abc123DEF_-",
        status=JobStatus.QUEUED,
        message="Queued.",
        output_dir=(tmp_path / "nested" / "job").resolve(),
    )


def test_write_job_creates_pretty_utf8_atomic_snapshot(tmp_path: Path) -> None:
    job = make_job(tmp_path)
    writer = FileJobWriter()

    snapshot_path = writer.write_job(job)

    assert snapshot_path == job.output_dir / "job_state.json"
    assert snapshot_path.is_absolute()
    assert not snapshot_path.with_suffix(".json.tmp").exists()
    raw = snapshot_path.read_text(encoding="utf-8")
    payload = json.loads(raw)
    assert "\n  " in raw
    assert payload["job_id"] == "job-1"
    assert payload["output_dir"] == str(job.output_dir)


def test_write_json_replaces_existing_content_and_preserves_unicode(tmp_path: Path) -> None:
    path = tmp_path / "artifacts" / "metadata.json"
    writer = FileJobWriter()
    writer.write_json(path, {"title": "舊標題"})

    writer.write_json(path, {"title": "新標題"})

    assert json.loads(path.read_text(encoding="utf-8")) == {"title": "新標題"}


def test_get_job_logger_writes_one_pipeline_log_without_duplicate_handlers(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "job"

    logger = get_job_logger("job-1", output_dir)
    same_logger = get_job_logger("job-1", output_dir)
    logger.info("處理開始")

    assert logger is same_logger
    file_handlers = [
        handler for handler in logger.handlers if isinstance(handler, logging.FileHandler)
    ]
    assert len(file_handlers) == 1
    assert "處理開始" in (output_dir / "pipeline.log").read_text(encoding="utf-8")
