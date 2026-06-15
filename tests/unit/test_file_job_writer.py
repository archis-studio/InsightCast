import json
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from insightcast.core.logging import (
    get_job_logger,
    log_task_failure,
    log_task_stage,
    log_task_status,
)
from insightcast.domain.enums import ErrorCode, JobStatus, JobType
from insightcast.domain.models import AnalysisJob, JobError
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
        video_id="abc123DEF_-",
        analysis_id="20260606-143000-job-1a",
    )


class _CaptureHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def task_log_records() -> list[logging.LogRecord]:
    logger = logging.getLogger("insightcast.task")
    handler = _CaptureHandler()
    previous_handlers = list(logger.handlers)
    previous_level = logger.level
    previous_propagate = logger.propagate
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        yield handler.records
    finally:
        logger.handlers = previous_handlers
        logger.setLevel(previous_level)
        logger.propagate = previous_propagate


def test_log_task_status_emits_expected_info_message(
    tmp_path: Path,
    task_log_records: list[logging.LogRecord],
) -> None:
    job = make_job(tmp_path)

    log_task_status(job)

    [record] = task_log_records
    assert record.name == "insightcast.task"
    assert record.levelno == logging.INFO
    assert record.msg == "task job_id=%s type=%s status=%s message=%r"
    assert record.args == (job.job_id, job.job_type, job.status, job.message)
    assert record.getMessage() == (
        "task job_id=job-1 type=ANALYSIS status=QUEUED message='Queued.'"
    )


@pytest.mark.parametrize("event", ["started", "completed"])
def test_log_task_stage_emits_expected_info_message_without_elapsed(
    tmp_path: Path,
    event: str,
    task_log_records: list[logging.LogRecord],
) -> None:
    job = make_job(tmp_path)

    log_task_stage(job, "transcription", event)

    [record] = task_log_records
    assert record.name == "insightcast.task"
    assert record.levelno == logging.INFO
    assert record.msg == "task job_id=%s type=%s stage=%s event=%s"
    assert record.args == (job.job_id, job.job_type, "transcription", event)
    assert record.getMessage() == (
        f"task job_id=job-1 type=ANALYSIS stage=transcription event={event}"
    )


def test_log_task_stage_emits_expected_error_message_with_elapsed(
    tmp_path: Path,
    task_log_records: list[logging.LogRecord],
) -> None:
    job = make_job(tmp_path)

    log_task_stage(job, "render", "failed", elapsed_seconds=12.3456)

    [record] = task_log_records
    assert record.name == "insightcast.task"
    assert record.levelno == logging.ERROR
    assert record.msg == "task job_id=%s type=%s stage=%s event=%s elapsed_seconds=%.3f"
    assert record.args == (job.job_id, job.job_type, "render", "failed", 12.3456)
    assert record.getMessage() == (
        "task job_id=job-1 type=ANALYSIS stage=render event=failed "
        "elapsed_seconds=12.346"
    )


def test_log_task_failure_emits_expected_error_message_without_traceback(
    tmp_path: Path,
    task_log_records: list[logging.LogRecord],
) -> None:
    job = make_job(tmp_path)
    error = JobError(
        error_code=ErrorCode.TRANSCRIPTION_FAILED,
        message="Transcript failed.",
    )

    log_task_failure(job, error)

    [record] = task_log_records
    assert record.name == "insightcast.task"
    assert record.levelno == logging.ERROR
    assert record.msg == "task job_id=%s type=%s event=failed error_code=%s stage=%s"
    assert record.args == (job.job_id, job.job_type, error.error_code, "unknown")
    assert record.exc_info is None
    assert record.exc_text is None
    assert record.getMessage() == (
        "task job_id=job-1 type=ANALYSIS event=failed "
        "error_code=TRANSCRIPTION_FAILED stage=unknown"
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


def test_concurrent_write_json_produces_one_complete_payload(tmp_path: Path) -> None:
    path = tmp_path / "artifacts" / "metadata.json"
    payloads = [
        {"writer": index, "content": str(index) * 100_000}
        for index in range(8)
    ]
    barrier = Barrier(len(payloads))

    def write(payload: dict[str, object]) -> None:
        barrier.wait()
        FileJobWriter().write_json(path, payload)

    with ThreadPoolExecutor(max_workers=len(payloads)) as executor:
        list(executor.map(write, payloads))

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted in payloads
    assert list(path.parent.glob(f".{path.name}.*.tmp")) == []


def test_write_json_cleans_own_temporary_file_when_replace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "artifacts" / "metadata.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"existing": true}\n', encoding="utf-8")

    def fail_replace(self: Path, target: Path) -> Path:
        raise OSError("injected replace failure")

    monkeypatch.setattr(Path, "replace", fail_replace)

    with pytest.raises(OSError, match="injected replace failure"):
        FileJobWriter().write_json(path, {"replacement": True})

    assert json.loads(path.read_text(encoding="utf-8")) == {"existing": True}
    assert list(path.parent.glob(f".{path.name}.*.tmp")) == []


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
    assert logger.propagate is False
    assert "處理開始" in (output_dir / "pipeline.log").read_text(encoding="utf-8")
