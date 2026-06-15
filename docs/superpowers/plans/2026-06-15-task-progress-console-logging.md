# Task Progress Console Logging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add concise standard-library task progress logs to the `cast_api` console for queued jobs, status changes, stage timings, and failures.

**Architecture:** Keep the existing per-job file logger unchanged and add focused helper functions around a separate `insightcast.task` logger. `JobService` will call those helpers only at existing lifecycle boundaries, while `cast_api` will add the logger to a copied Uvicorn logging configuration so request logs and task logs share the console without duplicate handlers.

**Tech Stack:** Python 3.13, standard-library `logging`, FastAPI, Uvicorn, pytest, pytest-asyncio

---

## File Structure

- Modify `src/insightcast/core/logging.py`: define the task logger and stable helper functions for status, stage, and terminal failure events.
- Modify `src/insightcast/services/job_service.py`: call the helpers at existing queue, status, stage, and failure boundaries.
- Modify `src/insightcast/api/app.py`: supply Uvicorn with a copied logging configuration containing `insightcast.task`.
- Modify `tests/unit/test_file_job_writer.py`: verify helper formatting, levels, and unchanged file-logger isolation.
- Modify `tests/service/test_job_service.py`: verify real analysis/render lifecycle events through logging capture.
- Modify `tests/api/test_health.py`: verify the server logging configuration routes only the task logger through Uvicorn's default console handler.

### Task 1: Define Task Event Logging Helpers

**Files:**
- Modify: `src/insightcast/core/logging.py`
- Test: `tests/unit/test_file_job_writer.py`

- [ ] **Step 1: Write failing helper tests**

Add imports and tests that describe the desired event API:

```python
from insightcast.core.logging import (
    get_job_logger,
    log_task_failure,
    log_task_stage,
    log_task_status,
)
from insightcast.domain.enums import ErrorCode, JobStatus, JobType
from insightcast.domain.models import JobError


def test_task_logging_helpers_emit_stable_searchable_events(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    job = make_job(tmp_path)

    with caplog.at_level(logging.INFO, logger="insightcast.task"):
        log_task_status(job)
        log_task_stage(job, "topic_discovery", "started")
        log_task_stage(
            job,
            "topic_discovery",
            "completed",
            elapsed_seconds=1.23456,
        )

    messages = [record.getMessage() for record in caplog.records]
    assert messages == [
        (
            "task job_id=job-1 type=ANALYSIS status=QUEUED "
            "message='Queued.'"
        ),
        (
            "task job_id=job-1 type=ANALYSIS "
            "stage=topic_discovery event=started"
        ),
        (
            "task job_id=job-1 type=ANALYSIS "
            "stage=topic_discovery event=completed elapsed_seconds=1.235"
        ),
    ]


def test_task_failure_helpers_emit_error_without_traceback(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    job = make_job(tmp_path)
    error = JobError(
        stage="topic_discovery",
        error_code=ErrorCode.INVALID_LLM_OUTPUT,
        message="Invalid topics.",
    )

    with caplog.at_level(logging.ERROR, logger="insightcast.task"):
        log_task_stage(
            job,
            "topic_discovery",
            "failed",
            elapsed_seconds=2.5,
        )
        log_task_failure(job, error)

    assert [record.levelno for record in caplog.records] == [
        logging.ERROR,
        logging.ERROR,
    ]
    assert [record.getMessage() for record in caplog.records] == [
        (
            "task job_id=job-1 type=ANALYSIS "
            "stage=topic_discovery event=failed elapsed_seconds=2.500"
        ),
        (
            "task job_id=job-1 type=ANALYSIS event=failed "
            "error_code=INVALID_LLM_OUTPUT stage=topic_discovery"
        ),
    ]
    assert all(record.exc_info is None for record in caplog.records)
```

Extend the existing file-logger test with:

```python
assert logger.propagate is False
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
uv run pytest \
  tests/unit/test_file_job_writer.py::test_task_logging_helpers_emit_stable_searchable_events \
  tests/unit/test_file_job_writer.py::test_task_failure_helpers_emit_error_without_traceback \
  -q
```

Expected: collection fails because `log_task_status`, `log_task_stage`, and
`log_task_failure` do not exist.

- [ ] **Step 3: Implement the minimal helpers**

Add to `src/insightcast/core/logging.py`:

```python
from typing import Any, Literal

from insightcast.domain.models import JobError

_TASK_LOGGER = logging.getLogger("insightcast.task")
TaskStageEvent = Literal["started", "completed", "failed"]


def log_task_status(job: Any) -> None:
    _TASK_LOGGER.info(
        "task job_id=%s type=%s status=%s message=%r",
        job.job_id,
        job.job_type,
        job.status,
        job.message,
    )


def log_task_stage(
    job: Any,
    stage: str,
    event: TaskStageEvent,
    *,
    elapsed_seconds: float | None = None,
) -> None:
    level = logging.ERROR if event == "failed" else logging.INFO
    if elapsed_seconds is None:
        _TASK_LOGGER.log(
            level,
            "task job_id=%s type=%s stage=%s event=%s",
            job.job_id,
            job.job_type,
            stage,
            event,
        )
        return
    _TASK_LOGGER.log(
        level,
        "task job_id=%s type=%s stage=%s event=%s elapsed_seconds=%.3f",
        job.job_id,
        job.job_type,
        stage,
        event,
        elapsed_seconds,
    )


def log_task_failure(job: Any, error: JobError) -> None:
    _TASK_LOGGER.error(
        "task job_id=%s type=%s event=failed error_code=%s stage=%s",
        job.job_id,
        job.job_type,
        error.error_code,
        error.stage or "unknown",
    )
```

Keep `get_job_logger(...).propagate = False` unchanged.

- [ ] **Step 4: Run tests and verify GREEN**

Run:

```bash
uv run pytest tests/unit/test_file_job_writer.py -q
```

Expected: all tests pass, including the new task-event tests.

- [ ] **Step 5: Commit**

```bash
git add src/insightcast/core/logging.py tests/unit/test_file_job_writer.py
git commit -m "feat: add task progress logging helpers"
```

### Task 2: Emit Queued, Status, and Stage Progress

**Files:**
- Modify: `src/insightcast/services/job_service.py`
- Test: `tests/service/test_job_service.py`

- [ ] **Step 1: Write a failing analysis lifecycle test**

Add `import logging` and:

```python
@pytest.mark.asyncio
async def test_analysis_emits_concise_task_progress_events(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    service, _curator, _clip = make_service(tmp_path)

    with caplog.at_level(logging.INFO, logger="insightcast.task"):
        job = await service.create_analysis_job("https://youtu.be/abc123DEF_-")
        await service.process(await service.queue.get())

    messages = [record.getMessage() for record in caplog.records]
    assert (
        f"task job_id={job.job_id} type=ANALYSIS status=QUEUED "
        "message='Analysis job is queued.'"
    ) in messages
    assert (
        f"task job_id={job.job_id} type=ANALYSIS status=TRANSCRIBING "
        "message='Transcribing English audio.'"
    ) in messages
    assert (
        f"task job_id={job.job_id} type=ANALYSIS "
        "stage=topic_discovery event=started"
    ) in messages
    assert any(
        message.startswith(
            f"task job_id={job.job_id} type=ANALYSIS "
            "stage=topic_discovery event=completed elapsed_seconds="
        )
        for message in messages
    )
    assert (
        f"task job_id={job.job_id} type=ANALYSIS status=WAITING_SELECTION "
        "message='2 candidates are ready for selection.'"
    ) in messages
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
uv run pytest \
  tests/service/test_job_service.py::test_analysis_emits_concise_task_progress_events \
  -q
```

Expected: FAIL because no `insightcast.task` records are emitted by `JobService`.

- [ ] **Step 3: Wire status and stage helpers into `JobService`**

Change the logging import:

```python
from insightcast.core.logging import (
    get_job_log_path,
    get_job_logger,
    log_task_stage,
    log_task_status,
)
```

After the existing queued file-log call in both `create_analysis_job()` and
`create_direct_render_job()`, add:

```python
log_task_status(job)
```

In `_touch()`, add the same call before persisting the job:

```python
def _touch(self, job: AnalysisJob | DirectRenderJob) -> None:
    job.updated_at = self.clock()
    get_job_logger(job.job_id, job.output_dir).info("%s: %s", job.status, job.message)
    log_task_status(job)
    self.writer.write_job(job)
```

In `_run_stage()`, measure duration once per exit path and send it to both loggers:

```python
logger = get_job_logger(job.job_id, job.output_dir)
started_at = perf_counter()
logger.info("stage_started stage=%s", stage)
log_task_stage(job, stage, "started")
try:
    result = await operation()
except Exception:
    elapsed_seconds = perf_counter() - started_at
    logger.error(
        "stage_failed stage=%s elapsed_seconds=%.3f",
        stage,
        elapsed_seconds,
    )
    log_task_stage(
        job,
        stage,
        "failed",
        elapsed_seconds=elapsed_seconds,
    )
    raise
elapsed_seconds = perf_counter() - started_at
logger.info(
    "stage_completed stage=%s elapsed_seconds=%.3f",
    stage,
    elapsed_seconds,
)
log_task_stage(
    job,
    stage,
    "completed",
    elapsed_seconds=elapsed_seconds,
)
return result
```

Replace the manual `WAITING_SELECTION` file-log/write block with:

```python
job.status = JobStatus.WAITING_SELECTION
job.message = f"{len(job.candidates)} candidates are ready for selection."
job.updated_at = completed_at
get_job_logger(job.job_id, job.output_dir).info(
    "%s: %s",
    job.status,
    job.message,
)
log_task_status(job)
self.writer.write_job(job)
```

This preserves the manifest completion timestamp while adding the console event.

- [ ] **Step 4: Run focused lifecycle tests**

Run:

```bash
uv run pytest \
  tests/service/test_job_service.py::test_analysis_emits_concise_task_progress_events \
  tests/service/test_job_service.py::test_pipeline_log_records_analysis_and_render_stage_timings \
  -q
```

Expected: both tests pass; detailed operation-log assertions remain unchanged.

- [ ] **Step 5: Commit**

```bash
git add src/insightcast/services/job_service.py tests/service/test_job_service.py
git commit -m "feat: log task status and stage progress"
```

### Task 3: Emit Structured Terminal Failure Events

**Files:**
- Modify: `src/insightcast/services/job_service.py`
- Test: `tests/service/test_job_service.py`

- [ ] **Step 1: Write failing analysis and render failure tests**

Add:

```python
@pytest.mark.asyncio
async def test_failed_analysis_emits_structured_task_failure(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = JobService(
        output_root=tmp_path / "outputs",
        work_root=tmp_path / ".work",
        source_engine=FakeSource(),
        transcription_client=FakeTranscriber(),
        curator_engine=FailingCurator(),
        clip_engine=FakeClip(),
        publish_engine=FakePublish(),
        writer=FileJobWriter(),
        clock=Clock(),
        id_factory=IdFactory(),
    )

    with caplog.at_level(logging.ERROR, logger="insightcast.task"):
        job = await service.create_analysis_job("https://youtu.be/abc123DEF_-")
        await service.process(await service.queue.get())

    messages = [record.getMessage() for record in caplog.records]
    assert (
        f"task job_id={job.job_id} type=ANALYSIS event=failed "
        "error_code=INSUFFICIENT_CANDIDATES stage=topic_discovery"
    ) in messages
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.asyncio
async def test_failed_candidate_render_emits_structured_task_failure(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    service, _curator, clip = make_service(tmp_path)
    job = await service.create_analysis_job("https://youtu.be/abc123DEF_-")
    await service.process(await service.queue.get())
    clip.fail_candidates.add("A")
    await service.create_render(
        job.job_id,
        CandidateSelectionRequest(candidate_ids="A"),
    )

    with caplog.at_level(logging.ERROR, logger="insightcast.task"):
        await service.process(await service.queue.get())

    messages = [record.getMessage() for record in caplog.records]
    assert (
        f"task job_id={job.job_id} type=ANALYSIS event=failed "
        "error_code=VIDEO_RENDER_FAILED stage=rendering"
    ) in messages
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
uv run pytest \
  tests/service/test_job_service.py::test_failed_analysis_emits_structured_task_failure \
  tests/service/test_job_service.py::test_failed_candidate_render_emits_structured_task_failure \
  -q
```

Expected: FAIL because only stage failures and file tracebacks exist.

- [ ] **Step 3: Add terminal failure logging**

Import `log_task_failure` in `job_service.py`.

Centralize analysis and direct-render terminal failure logging in `_fail_job()` after
the existing conversion to `JobError`:

```python
def _fail_job(
    self,
    job: AnalysisJob | DirectRenderJob,
    error: InsightCastError,
) -> None:
    job.status = JobStatus.FAILED
    job.message = error.message
    job.error = self._as_job_error(error, error.stage)
    log_task_failure(job, job.error)
    self._touch(job)
```

This preserves every existing exception branch and its current public fallback error
code/message. It also guarantees one structured terminal failure event for both
analysis and direct-render jobs.

In the candidate render `except` block, after:

```python
error = self._as_job_error(exc, "rendering")
```

add:

```python
log_task_failure(job, error)
```

Do not pass `exc_info` to the task logger. Keep existing
`get_job_logger(...).exception(...)` calls unchanged so tracebacks remain in the
operation log.

- [ ] **Step 4: Run focused failure tests**

Run:

```bash
uv run pytest \
  tests/service/test_job_service.py::test_failed_analysis_emits_structured_task_failure \
  tests/service/test_job_service.py::test_failed_candidate_render_emits_structured_task_failure \
  tests/service/test_job_service.py::test_failed_analysis_after_transcript_retains_failed_manifest \
  -q
```

Expected: all tests pass and persisted failure behavior remains unchanged.

- [ ] **Step 5: Commit**

```bash
git add src/insightcast/services/job_service.py tests/service/test_job_service.py
git commit -m "feat: log structured task failures"
```

### Task 4: Route Task Logs Through Uvicorn's Console Handler

**Files:**
- Modify: `src/insightcast/api/app.py`
- Test: `tests/api/test_health.py`

- [ ] **Step 1: Write a failing logging configuration test**

Add:

```python
from insightcast.api.app import _build_runtime, _server_log_config, create_app


def test_server_log_config_routes_only_task_logger_to_default_console() -> None:
    config = _server_log_config()

    assert config["loggers"]["insightcast.task"] == {
        "handlers": ["default"],
        "level": "INFO",
        "propagate": False,
    }
    assert config["loggers"]["uvicorn.access"]["handlers"] == ["access"]
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
uv run pytest \
  tests/api/test_health.py::test_server_log_config_routes_only_task_logger_to_default_console \
  -q
```

Expected: collection fails because `_server_log_config` does not exist.

- [ ] **Step 3: Implement copied Uvicorn logging configuration**

Add imports:

```python
from copy import deepcopy

from uvicorn.config import LOGGING_CONFIG
```

Add:

```python
def _server_log_config() -> dict[str, Any]:
    config = deepcopy(LOGGING_CONFIG)
    config["loggers"]["insightcast.task"] = {
        "handlers": ["default"],
        "level": "INFO",
        "propagate": False,
    }
    return config
```

Pass it from `run()`:

```python
uvicorn.run(
    create_app(settings=settings),
    host=settings.api_host,
    port=settings.api_port,
    log_config=_server_log_config(),
)
```

Do not call `logging.basicConfig()` and do not alter `uvicorn.access`.

- [ ] **Step 4: Run API logging and health tests**

Run:

```bash
uv run pytest tests/api/test_health.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/insightcast/api/app.py tests/api/test_health.py
git commit -m "feat: show task progress in cast api console"
```

### Task 5: Verify the Complete Change

**Files:**
- Verify only; no planned source changes.

- [ ] **Step 1: Run focused logging and lifecycle suites**

Run:

```bash
uv run pytest \
  tests/unit/test_file_job_writer.py \
  tests/service/test_job_service.py \
  tests/api/test_health.py \
  -q
```

Expected: all tests pass.

- [ ] **Step 2: Run lint**

Run:

```bash
uv run ruff check \
  src/insightcast/core/logging.py \
  src/insightcast/services/job_service.py \
  src/insightcast/api/app.py \
  tests/unit/test_file_job_writer.py \
  tests/service/test_job_service.py \
  tests/api/test_health.py
```

Expected: `All checks passed!`

- [ ] **Step 3: Run the full test suite**

Run:

```bash
uv run pytest -q
```

Expected: all tests pass with no new warnings or failures.

- [ ] **Step 4: Check formatting and worktree scope**

Run:

```bash
git diff --check
git status --short
```

Expected: `git diff --check` exits successfully; status contains only the intended
task logging implementation and plan commit history.

- [ ] **Step 5: Perform a manual console smoke test**

With `cast_api` started from the updated environment, queue one cached analysis and
confirm the terminal shows a small number of lines shaped like:

```text
INFO:     task job_id=<id> type=ANALYSIS status=QUEUED message='Analysis job is queued.'
INFO:     task job_id=<id> type=ANALYSIS stage=source_ingestion event=started
INFO:     task job_id=<id> type=ANALYSIS stage=source_ingestion event=completed elapsed_seconds=0.123
```

Confirm normal Uvicorn access lines remain present and task lines contain no raw
payloads or tracebacks.

- [ ] **Step 6: Commit any verification-only test adjustments**

Only if verification required a test-only correction:

```bash
git add tests
git commit -m "test: verify task progress console logging"
```

If no correction was required, do not create an empty commit.
