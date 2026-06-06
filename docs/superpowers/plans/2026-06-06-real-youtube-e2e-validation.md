# Real YouTube End-to-End Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the minimum runtime safeguards and diagnostics needed to run and evaluate two real YouTube end-to-end validations.

**Architecture:** Keep the existing FastAPI, `JobService`, engine, and infrastructure boundaries. Add state validation at the service boundary and a small async timing helper around orchestration stages so diagnostics remain centralized without changing adapter contracts.

**Tech Stack:** Python 3.13, FastAPI, Pydantic v2, pytest, pytest-asyncio, yt-dlp, FFmpeg, OpenAI Python SDK.

---

### Task 1: Guard Candidate Rendering by Analysis State

**Files:**
- Modify: `src/insightcast/domain/enums.py`
- Modify: `src/insightcast/api/app.py`
- Modify: `src/insightcast/services/job_service.py`
- Test: `tests/service/test_job_service.py`
- Test: `tests/api/test_analysis_jobs.py`

- [ ] **Step 1: Write failing service tests**

Add tests that create analysis jobs in `QUEUED`, `INGESTING`, `TRANSCRIBING`,
and `CURATING`, then assert `create_render()` raises `InsightCastError` with a
stable invalid-state error code. Add a test showing `WAITING_SELECTION` remains
accepted.

- [ ] **Step 2: Run the focused tests and verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/service/test_job_service.py -q
```

Expected: the new invalid-state tests fail because `create_render()` currently
queues or validates candidate IDs without checking analysis state.

- [ ] **Step 3: Implement the service guard**

Add a stable `INVALID_JOB_STATE` error code. In `JobService.create_render()`,
allow rendering only for jobs in `WAITING_SELECTION`, `COMPLETED`, or `FAILED`
that have candidates plus retained transcript and source metadata. Raise a
structured `InsightCastError` with current status and job ID otherwise.

- [ ] **Step 4: Add API mapping coverage**

Add an API test asserting `INVALID_JOB_STATE` maps to HTTP 409 and update the
FastAPI exception mapping accordingly.

- [ ] **Step 5: Verify focused tests**

Run:

```bash
.venv/bin/python -m pytest tests/service/test_job_service.py tests/api/test_analysis_jobs.py -q
```

Expected: all focused tests pass.

### Task 2: Add Safe Stage Timing Diagnostics

**Files:**
- Modify: `src/insightcast/services/job_service.py`
- Test: `tests/service/test_job_service.py`

- [ ] **Step 1: Write failing diagnostic tests**

Use `caplog` with existing fake engines to process an analysis job and assert
`pipeline.log` records `stage_started` and `stage_completed` messages for source
ingestion, transcription, and curation, including a non-negative
`elapsed_seconds`. Add render coverage for clip rendering and metadata generation.

- [ ] **Step 2: Run the focused tests and verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/service/test_job_service.py -q
```

Expected: diagnostic assertions fail because stage timing messages do not exist.

- [ ] **Step 3: Implement a scoped async stage helper**

Add a private async helper in `JobService` that:

- logs `stage_started`
- uses `time.perf_counter()` for elapsed time
- awaits the supplied operation
- logs `stage_completed` with elapsed time
- logs `stage_failed` with elapsed time before re-raising

Wrap source ingestion, transcription, curation, clip rendering, and metadata
generation. Keep messages free of API keys, signed URLs, prompts, and transcript
content.

- [ ] **Step 4: Remove duplicate error conversion**

Delete the duplicate `_as_job_error()` assignment in candidate rendering without
changing partial-failure semantics.

- [ ] **Step 5: Verify focused tests**

Run:

```bash
.venv/bin/python -m pytest tests/service/test_job_service.py -q
```

Expected: all service tests pass and logs include stage timing data.

### Task 3: Automated and Runtime Preflight

**Files:**
- Modify only if verification exposes a root-cause defect.

- [ ] **Step 1: Run static and automated verification**

Run:

```bash
.venv/bin/ruff check .
.venv/bin/python -m pytest -q
git diff --check
```

Expected: Ruff passes, all tests pass, and `git diff --check` reports no errors.

- [ ] **Step 2: Start the API in one managed process**

Run:

```bash
.venv/bin/cast_api
```

Keep this process alive for analysis and rendering.

- [ ] **Step 3: Probe operational endpoints**

Request:

```text
GET http://127.0.0.1:8765/health
GET http://127.0.0.1:8765/docs
GET http://127.0.0.1:8765/openapi.json
```

Expected: health JSON reports ready dependencies, Swagger returns HTML, and
OpenAPI includes analysis and render routes.

### Task 4: First-Round Real Analysis

**Files:**
- Generated only: `outputs/**`, `.work/**`

- [ ] **Step 1: Submit the analysis request**

POST `/api/v1/analysis-jobs` with:

```json
{
  "youtube_url": "https://www.youtube.com/watch?v=7zCsfe57tpU",
  "candidate_count": 2,
  "min_duration_minutes": 3,
  "max_duration_minutes": 5,
  "force_reanalyze": true
}
```

- [ ] **Step 2: Poll to a terminal analysis state**

Poll the returned job until `WAITING_SELECTION` or `FAILED`. Do not restart the
API process.

- [ ] **Step 3: Inspect analysis artifacts**

Validate source media duration with ffprobe. Inspect `transcript.json`,
`candidates.json`, `job_state.json`, and `pipeline.log` for ordering, bounds,
candidate quality, stage durations, retries, and safe error details.

### Task 5: First-Round Candidate Render and Product Review

**Files:**
- Generated only: `outputs/**`, `.work/**`

- [ ] **Step 1: Render candidate A**

POST `/api/v1/analysis-jobs/{job_id}/renders` with:

```json
{
  "candidate_ids": ["A"],
  "force_render": false
}
```

- [ ] **Step 2: Poll to a terminal render state**

Poll until the render batch is `COMPLETED` or `FAILED`.

- [ ] **Step 3: Validate artifacts**

Check that SRT, ASS, burned MP4, and metadata JSON exist and are non-empty. Use
ffprobe to verify codecs and duration. Extract representative video frames and
inspect subtitle visibility, clipping, and synchronization.

- [ ] **Step 4: Produce the first-round acceptance report**

Report source facts, request settings, configured model names, stage durations,
candidate quality, artifact paths and sizes, subtitle/video findings, retries,
limitations, and recommended pipeline changes ranked by impact. Request the
second-round long-form URL only after this report is complete.
