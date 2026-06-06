# Insight Cast FastAPI MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the approved local-first Insight Cast FastAPI MVP that analyzes English YouTube videos, queues candidate/direct renders, produces bilingual artifacts, and exposes all operations through Swagger UI.

**Architecture:** A FastAPI process owns in-memory job registries and one FIFO `asyncio.Queue`. `JobService` coordinates domain-focused engines; infrastructure clients isolate OpenAI, yt-dlp, FFmpeg, and transcription, while storage persists inspectable JSON snapshots without restoring jobs after restart.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, pydantic-settings, OpenAI Python SDK, yt-dlp, ffmpeg, pysubs2, uv, pytest, pytest-asyncio, httpx, Ruff.

---

## File Map

- `pyproject.toml`, `uv.lock`: package metadata, runtime/dev dependencies, console script, tool configuration.
- `.gitignore`, `.env.example`, `README.md`: repository hygiene, complete configuration template, Traditional Chinese operations guide.
- `src/insightcast/core/*`: typed settings, stable exceptions, logging setup.
- `src/insightcast/domain/*`: statuses and all persisted/API-visible Pydantic models.
- `src/insightcast/utils/*`: timecodes, YouTube URL normalization, filenames, SRT, and ASS generation.
- `src/insightcast/storage/file_job_writer.py`: atomic job snapshots and JSON artifacts.
- `src/insightcast/infrastructure/*`: subprocess and OpenAI SDK adapters.
- `src/insightcast/prompts/*`: versioned curation, translation, and metadata contracts.
- `src/insightcast/engines/*`: source, transcription, curation, clipping, and publishing behavior.
- `src/insightcast/services/*`: in-memory registries, orchestration, and FIFO worker.
- `src/insightcast/api/*`: application lifespan, dependencies, routes, error mapping, and CLI entry point.
- `tests/unit/*`: deterministic domain, utility, storage, prompt, and engine tests.
- `tests/service/*`: orchestration and queue tests with fake engine ports.
- `tests/api/*`: HTTP/OpenAPI tests with dependency overrides.
- `Dockerfile`, `.dockerignore`: added only after local acceptance succeeds.

### Task 1: Initialize Repository and Python Package

**Files:**
- Create: `.gitignore`
- Create: `.env.example`
- Create: `pyproject.toml`
- Create: `src/insightcast/__init__.py`
- Create: `tests/conftest.py`
- Create: `outputs/.gitkeep`

- [ ] **Step 1: Initialize Git and add repository exclusions**

Run: `git init && git branch -M main`

Write `.gitignore` covering `.env`, `.venv/`, `.worktrees/`, `.work/`, `outputs/*` except `outputs/.gitkeep`, Python caches, coverage, IDE files, downloaded models, and generated media.

- [ ] **Step 2: Add the package manifest**

Define Python `>=3.12`, runtime dependencies (`fastapi`, `uvicorn[standard]`, `pydantic`, `pydantic-settings`, `openai`, `yt-dlp`, `pysubs2`, `python-multipart`) and dev dependencies (`pytest`, `pytest-asyncio`, `httpx`, `ruff`). Configure `cast_api = "insightcast.api.app:run"`, src layout, pytest asyncio mode, and Ruff.

- [ ] **Step 3: Add the complete environment template**

Include every setting from the design with Chinese comments, safe defaults, `OPENAI_API_KEY=replace-me`, and optional blank specialized model overrides that fall back to `LLM_MODEL`.

- [ ] **Step 4: Resolve dependencies and verify package import**

Run: `uv sync`

Expected: Python 3.12 environment created and `uv.lock` generated.

Run: `uv run python -c "import insightcast"`

Expected: exit 0.

- [ ] **Step 5: Commit the baseline**

Run: `git add .gitignore .env.example pyproject.toml uv.lock src tests outputs docs && git commit -m "chore: initialize insight cast project"`

### Task 2: Configuration, Errors, and Domain Models

**Files:**
- Create: `src/insightcast/core/config.py`
- Create: `src/insightcast/core/exceptions.py`
- Create: `src/insightcast/domain/enums.py`
- Create: `src/insightcast/domain/models.py`
- Test: `tests/unit/test_config.py`
- Test: `tests/unit/test_domain_models.py`

- [ ] **Step 1: Write failing settings tests**

Cover invalid/placeholder API keys, empty model names, port/CRF/height ranges, absolute output/work paths, specialized model fallback, and valid local-transcription configuration.

Run: `uv run pytest tests/unit/test_config.py -q`

Expected: FAIL because `Settings` does not exist.

- [ ] **Step 2: Implement typed settings**

Use `BaseSettings`, validators, computed specialized-model properties, `Path.resolve()`, and cached `get_settings()`. Only require an OpenAI key when a configured provider or engine needs OpenAI.

- [ ] **Step 3: Write failing domain model tests**

Cover stable statuses/error codes, candidate duration, render artifact grouping, structured job errors, UTC timestamps, and candidate selection accepting a string/list while preserving order and removing duplicates.

Run: `uv run pytest tests/unit/test_domain_models.py -q`

Expected: FAIL because domain types do not exist.

- [ ] **Step 4: Implement enums, Pydantic models, and application exception**

Define `JobStatus`, `JobType`, `ErrorCode`, transcript/candidate/artifact/render/job models, request/response models, and `InsightCastError(error_code, message, details, stage)`.

- [ ] **Step 5: Verify and commit**

Run: `uv run pytest tests/unit/test_config.py tests/unit/test_domain_models.py -q`

Expected: PASS.

Run: `git add src/insightcast/core src/insightcast/domain tests/unit && git commit -m "feat: add configuration and domain contracts"`

### Task 3: Timecode, URL, and Filename Utilities

**Files:**
- Create: `src/insightcast/utils/timecode.py`
- Create: `src/insightcast/utils/youtube.py`
- Create: `src/insightcast/utils/files.py`
- Test: `tests/unit/test_timecode.py`
- Test: `tests/unit/test_youtube.py`
- Test: `tests/unit/test_files.py`

- [ ] **Step 1: Write failing utility tests**

Test numeric and `HH:MM:SS(.mmm)` parsing, invalid/negative inputs, SRT/ASS formatting, YouTube watch/share/embed/shorts normalization, rejection of non-YouTube/missing IDs, Unicode title sanitization, reserved characters, empty titles, and timestamped job/render directory names.

Run: `uv run pytest tests/unit/test_timecode.py tests/unit/test_youtube.py tests/unit/test_files.py -q`

Expected: FAIL because utility modules do not exist.

- [ ] **Step 2: Implement minimal pure utilities**

Use `urllib.parse` for URLs, `Decimal`-safe numeric conversion, deterministic formatting, bounded slugs, and injected timestamps/job IDs for testability.

- [ ] **Step 3: Verify and commit**

Run: `uv run pytest tests/unit/test_timecode.py tests/unit/test_youtube.py tests/unit/test_files.py -q`

Expected: PASS.

Run: `git add src/insightcast/utils tests/unit && git commit -m "feat: add input and output utilities"`

### Task 4: Transcript Slicing and Subtitle Writers

**Files:**
- Create: `src/insightcast/utils/srt.py`
- Create: `src/insightcast/utils/ass.py`
- Create: `src/insightcast/engines/lingo_engine.py`
- Test: `tests/unit/test_subtitles.py`
- Test: `tests/unit/test_lingo_engine.py`

- [ ] **Step 1: Write failing transcript slicing tests**

Given absolute transcript segments and clip boundaries, require intersecting segments only, relative timestamps, boundary clamping, positive duration, preserved source order, and one-to-one translation mapping.

- [ ] **Step 2: Implement `LingoEngine.prepare_subtitle_items`**

Return typed bilingual subtitle items and raise `SUBTITLE_GENERATION_FAILED` for count, ID, order, or timing mismatches.

- [ ] **Step 3: Write failing SRT/ASS golden tests**

Assert UTF-8 Traditional Chinese SRT, escaped ASS text, English top/Chinese bottom styles, PlayRes/style headers, and stable event timing.

- [ ] **Step 4: Implement subtitle serializers**

Use `pysubs2` for ASS timing/escaping and a small deterministic SRT serializer.

- [ ] **Step 5: Verify and commit**

Run: `uv run pytest tests/unit/test_subtitles.py tests/unit/test_lingo_engine.py -q`

Expected: PASS.

Run: `git add src/insightcast/utils src/insightcast/engines/lingo_engine.py tests/unit && git commit -m "feat: generate bilingual subtitle assets"`

### Task 5: Atomic Storage and Logging

**Files:**
- Create: `src/insightcast/core/logging.py`
- Create: `src/insightcast/storage/file_job_writer.py`
- Test: `tests/unit/test_file_job_writer.py`

- [ ] **Step 1: Write failing storage tests**

Verify parent creation, pretty UTF-8 JSON, absolute serialized artifact paths, atomic replace, job snapshot updates, and separate per-job `pipeline.log`.

- [ ] **Step 2: Implement writer and logger factory**

Write to a sibling temporary file then `Path.replace`; serialize Pydantic models in JSON mode; avoid duplicate handlers.

- [ ] **Step 3: Verify and commit**

Run: `uv run pytest tests/unit/test_file_job_writer.py -q`

Expected: PASS.

Run: `git add src/insightcast/core/logging.py src/insightcast/storage tests/unit && git commit -m "feat: persist inspectable job state"`

### Task 6: External Process Clients

**Files:**
- Create: `src/insightcast/infrastructure/ytdlp_client.py`
- Create: `src/insightcast/infrastructure/ffmpeg_client.py`
- Test: `tests/unit/test_ytdlp_client.py`
- Test: `tests/unit/test_ffmpeg_client.py`

- [ ] **Step 1: Write failing command-construction and error tests**

Assert yt-dlp format capped at configured height, MP4 merge, metadata extraction, FFmpeg executable probing, MP3 extraction, precise re-encoded clipping, ASS burning with H.264/CRF/AAC, subprocess stderr capture, and stable error conversion.

- [ ] **Step 2: Implement async-safe subprocess adapters**

Build argument arrays without shell interpolation and run blocking work through `asyncio.to_thread(subprocess.run, ...)`.

- [ ] **Step 3: Verify and commit**

Run: `uv run pytest tests/unit/test_ytdlp_client.py tests/unit/test_ffmpeg_client.py -q`

Expected: PASS without network or real media.

Run: `git add src/insightcast/infrastructure tests/unit && git commit -m "feat: add media infrastructure clients"`

### Task 7: OpenAI, Transcription, and Versioned Prompts

**Files:**
- Create: `src/insightcast/infrastructure/openai_client.py`
- Create: `src/insightcast/infrastructure/transcription/base.py`
- Create: `src/insightcast/infrastructure/transcription/openai_transcription_client.py`
- Create: `src/insightcast/infrastructure/transcription/local_whisper_client.py`
- Create: `src/insightcast/prompts/curator.py`
- Create: `src/insightcast/prompts/translation.py`
- Create: `src/insightcast/prompts/metadata.py`
- Test: `tests/unit/test_openai_client.py`
- Test: `tests/unit/test_transcription.py`
- Test: `tests/unit/test_prompts.py`

- [ ] **Step 1: Write failing prompt and structured-call tests**

Require version constants, explicit input/output contracts, no arbitrary system prompt from API input, selected model transport, Pydantic structured parsing, timeout/retry behavior, and safe `LLM_REQUEST_FAILED` details.

- [ ] **Step 2: Implement OpenAI structured response adapter and prompts**

Keep prompt text outside the client. Make retry count injectable and convert SDK failures to application errors.

- [ ] **Step 3: Write failing transcription tests**

Cover chunk planning below upload limit, timestamp offset merge, English language validation, `whisper-1` segment parsing, lazy local model import/loading, and unsupported language errors.

- [ ] **Step 4: Implement transcription protocol and providers**

Expose one async `transcribe(audio_path) -> Transcript`; defer `faster-whisper` import and model construction until first local call.

- [ ] **Step 5: Verify and commit**

Run: `uv run pytest tests/unit/test_openai_client.py tests/unit/test_transcription.py tests/unit/test_prompts.py -q`

Expected: PASS.

Run: `git add src/insightcast/infrastructure src/insightcast/prompts tests/unit && git commit -m "feat: add ai and transcription adapters"`

### Task 8: Curation Validation and Retry

**Files:**
- Create: `src/insightcast/engines/curator_engine.py`
- Test: `tests/unit/test_curator_engine.py`

- [ ] **Step 1: Write failing candidate validation tests**

Require exact count, IDs `A..`, start before end, requested duration bounds, transcript boundaries, non-empty title/reason/summary, optional overlap, and `INSUFFICIENT_CANDIDATES` for a valid but undersized set.

- [ ] **Step 2: Write failing retry tests**

First invalid structured result must trigger one correction request containing validation feedback; second invalid result must raise `INVALID_LLM_OUTPUT`.

- [ ] **Step 3: Implement `CuratorEngine`**

Pass transcript/request parameters to the versioned prompt, validate independently of SDK schema validation, and record model/prompt version in `candidates.json`.

- [ ] **Step 4: Verify and commit**

Run: `uv run pytest tests/unit/test_curator_engine.py -q`

Expected: PASS.

Run: `git add src/insightcast/engines/curator_engine.py tests/unit && git commit -m "feat: curate validated video candidates"`

### Task 9: Source, Clip, and Publish Engines

**Files:**
- Create: `src/insightcast/engines/source_engine.py`
- Create: `src/insightcast/engines/clip_engine.py`
- Create: `src/insightcast/engines/publish_engine.py`
- Test: `tests/unit/test_source_engine.py`
- Test: `tests/unit/test_clip_engine.py`
- Test: `tests/unit/test_publish_engine.py`

- [ ] **Step 1: Write failing source workflow tests**

Verify sanitized output layout, source download, audio extraction, metadata return, existing source reuse within a job, and preserved original/normalized URLs.

- [ ] **Step 2: Implement `SourceEngine`**

Coordinate yt-dlp/FFmpeg clients and return typed source artifacts.

- [ ] **Step 3: Write failing clip lifecycle tests**

Require temporary clip under `.work/`, subtitle files in final candidate/direct directory, burn operation, temporary deletion only after complete success, and retained work files on failure.

- [ ] **Step 4: Implement `ClipEngine`**

Coordinate clip, translation, subtitle serialization, and burn steps with candidate/direct naming.

- [ ] **Step 5: Write failing metadata tests and implement `PublishEngine`**

Validate title/description/tags/privacy (`private` default), write complete source plus generated YouTube metadata JSON, and record model/prompt version.

- [ ] **Step 6: Verify and commit**

Run: `uv run pytest tests/unit/test_source_engine.py tests/unit/test_clip_engine.py tests/unit/test_publish_engine.py -q`

Expected: PASS.

Run: `git add src/insightcast/engines tests/unit && git commit -m "feat: compose media processing engines"`

### Task 10: Job Service and FIFO Worker

**Files:**
- Create: `src/insightcast/services/job_service.py`
- Create: `src/insightcast/services/queue_worker.py`
- Test: `tests/service/test_job_service.py`
- Test: `tests/service/test_queue_worker.py`

- [ ] **Step 1: Write failing analysis lifecycle tests**

Cover immediate `QUEUED`, status transitions through `WAITING_SELECTION`, same-normalized-URL reuse, forced reanalysis, exact candidate failures, state snapshots, and safe structured errors.

- [ ] **Step 2: Implement analysis registry and pipeline**

Keep latest analysis job by normalized URL for the process lifetime and enqueue typed work items.

- [ ] **Step 3: Write failing render lifecycle tests**

Cover string/list normalization, missing IDs, multi-select order, completed candidate skipping, incremental rendering, timestamped force batches, partial success retention, candidate-specific errors, retrying failed candidates, and source retention.

- [ ] **Step 4: Implement analysis render batches**

Return existing artifacts for skipped candidates and never overwrite on `force_render=true`.

- [ ] **Step 5: Write failing direct-render tests**

Validate one range, end after start, unique job/output each request, no curator calls, status transitions, and complete artifacts.

- [ ] **Step 6: Implement direct pipeline and single FIFO worker**

One worker consumes analysis and render work serially; cancellation during shutdown is clean.

- [ ] **Step 7: Verify and commit**

Run: `uv run pytest tests/service -q`

Expected: PASS.

Run: `git add src/insightcast/services tests/service && git commit -m "feat: orchestrate queued insight cast jobs"`

### Task 11: FastAPI Routes, Lifespan, and OpenAPI

**Files:**
- Create: `src/insightcast/api/app.py`
- Create: `src/insightcast/api/dependencies.py`
- Create: `src/insightcast/api/routes/health.py`
- Create: `src/insightcast/api/routes/analysis_jobs.py`
- Create: `src/insightcast/api/routes/direct_render_jobs.py`
- Test: `tests/api/test_health.py`
- Test: `tests/api/test_analysis_jobs.py`
- Test: `tests/api/test_direct_render_jobs.py`
- Test: `tests/api/test_openapi.py`

- [ ] **Step 1: Write failing health/startup tests**

Require dependency readiness, FFmpeg probe, fail-fast invalid settings, one queue/worker per lifespan, and clean worker cancellation.

- [ ] **Step 2: Implement app factory, dependencies, lifespan, and CLI**

Expose `create_app(settings=None, service=None)` for tests and `run()` using configured host/port.

- [ ] **Step 3: Write failing endpoint tests**

Cover every specified route, 202 queue responses, 404 `JOB_NOT_FOUND`, validation errors in stable runtime shape, upload stubs that verify artifacts then return `UPLOAD_NOT_IMPLEMENTED`, and structured absolute paths.

- [ ] **Step 4: Implement routes and exception handlers**

Use explicit response models, Pydantic descriptions/examples, and a unified `InsightCastError` handler.

- [ ] **Step 5: Write and satisfy OpenAPI contract tests**

Assert all eight operations, request examples, field descriptions, response schemas, and documented error response.

- [ ] **Step 6: Verify and commit**

Run: `uv run pytest tests/api -q`

Expected: PASS.

Run: `git add src/insightcast/api tests/api && git commit -m "feat: expose fastapi operational interface"`

### Task 12: Repository Documentation and Full Local Verification

**Files:**
- Create: `README.md`
- Modify: `.env.example`
- Modify: `pyproject.toml`
- Test: `tests/test_repository_contract.py`

- [ ] **Step 1: Write repository contract tests**

Assert required files, script entry point, every documented environment key, ignored secrets/generated paths, README commands/routes/legal notice, and no tracked `.env` or generated output.

- [ ] **Step 2: Write the Traditional Chinese README**

Include product boundaries, architecture, workflows, prerequisites, macOS/Linux FFmpeg install and checks, complete env table, API key safety, local Whisper cost, setup/test/start commands, Swagger and curl walkthroughs, output lifecycle, troubleshooting, processing expectations, and legal notice. Do not add Docker instructions yet.

- [ ] **Step 3: Run static and test verification**

Run: `uv run ruff check .`

Expected: PASS.

Run: `uv run pytest -q`

Expected: PASS.

Run: `git status --short`

Expected: only intended README/config/test changes before commit.

- [ ] **Step 4: Commit documentation**

Run: `git add README.md .env.example pyproject.toml tests && git commit -m "docs: add local operation guide"`

### Task 13: Local Runtime Acceptance

**Files:**
- Modify only files required by acceptance failures.

- [ ] **Step 1: Create a temporary acceptance environment**

Use a non-placeholder test key and test doubles/configuration that permit startup without making paid/network calls. Do not commit `.env`.

- [ ] **Step 2: Run required local commands**

Run: `git status --short`

Expected: clean.

Run: `uv sync && uv run pytest && uv run ruff check .`

Expected: all exit 0.

- [ ] **Step 3: Start and probe the API**

Run `uv run cast_api` in a managed background session, then request:

```text
GET http://127.0.0.1:8765/health
GET http://127.0.0.1:8765/docs
GET http://127.0.0.1:8765/openapi.json
```

Expected: health JSON success, Swagger HTML 200, and OpenAPI JSON containing all routes.

- [ ] **Step 4: Stop the server and commit any acceptance fixes**

Ensure no server process remains. Re-run the complete suite before committing.

### Task 14: Docker Packaging After Local Acceptance

**Files:**
- Create: `Dockerfile`
- Create: `.dockerignore`
- Modify: `README.md`
- Test: `tests/test_repository_contract.py`

- [ ] **Step 1: Write failing Docker repository tests**

Require a CPU Python 3.12 image, FFmpeg installation, non-root runtime user, port 8765, `cast_api` command, `.env` runtime support, and documented `outputs` volume.

- [ ] **Step 2: Implement Docker packaging and documentation**

Use a slim Python base, install FFmpeg, copy lock/manifest before source for cache efficiency, install locked dependencies with uv, and run on `0.0.0.0:8765`.

- [ ] **Step 3: Verify image and container**

Run: `docker build -t insightcast .`

Expected: build succeeds.

Run: `docker run --rm --env-file .env -p 8765:8765 -v "$(pwd)/outputs:/app/outputs" insightcast`

Probe `/health` and `/docs`; then stop the container.

- [ ] **Step 4: Run full regression and commit**

Run: `uv run ruff check . && uv run pytest -q`

Expected: PASS.

Run: `git add Dockerfile .dockerignore README.md tests && git commit -m "build: add ffmpeg docker image"`

### Task 15: Final Specification and Quality Review

**Files:**
- Modify only files required by review findings.

- [ ] **Step 1: Audit every design requirement**

Map sections 1-20 of `docs/superpowers/specs/2026-06-06-insight-cast-mvp-design.md` to implementation/tests. Record and fix any uncovered non-deferred requirement.

- [ ] **Step 2: Run final verification**

Run: `uv sync`

Run: `uv run ruff check .`

Run: `uv run pytest`

Run: `git status --short --branch`

Expected: dependency sync succeeds, lint/tests pass, and worktree is clean on the feature branch.

- [ ] **Step 3: Review commit history and diff**

Run: `git log --oneline --decorate main..HEAD`

Run: `git diff --stat main...HEAD`

Expected: scoped commits covering the MVP with no secrets, generated media, caches, or unrelated files.

## Self-Review

- Spec coverage: Tasks 1-15 cover repository setup, configuration, all API workflows, queue/state behavior, output lifecycle, media/transcription/AI boundaries, prompts, validation/retry, documentation, local acceptance, Docker-after-local-acceptance, stable errors, and all explicitly listed tests.
- Deferred items remain excluded: frontend, auth, database recovery, Celery, diarization, actual YouTube upload, thumbnails, extra languages, local file input, and 4K-first processing.
- Placeholder scan: implementation tasks name exact behavior, files, commands, and expected results; no production behavior is deferred within the MVP.
- Type consistency: `JobStatus`, `JobType`, `ErrorCode`, `InsightCastError`, transcript/candidate/render/job models, `JobService`, and engine/client boundaries are introduced before downstream use.
