# Agent Analysis CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a documented `uv run cast_analyze "<youtube-url>"` command that validates API readiness, queues one analysis job, polls it to `WAITING_SELECTION`, and reports candidates, artifacts, failures, and interruptions.

**Architecture:** Extend the existing `Settings` model with client URL and poll interval configuration. Implement a standard-library HTTP client in `src/insightcast/cli/analyze.py`, keeping request decoding, protocol validation, polling, and formatting in focused functions that tests can exercise through an injected request and sleep boundary. The API remains the sole owner of job execution and server lifecycle.

**Tech Stack:** Python 3.13, `argparse`, `urllib.request`, `json`, Pydantic Settings, pytest, Ruff, Hatch package scripts.

---

### Task 1: CLI Settings

**Files:**
- Modify: `src/insightcast/core/config.py`
- Modify: `tests/unit/test_config.py`
- Modify: `.env.example`

- [ ] **Step 1: Write failing default and override tests**

Add tests that instantiate `Settings(_env_file=None, openai_api_key="sk-test-value")`
and assert:

```python
assert settings.api_base_url == "http://127.0.0.1:8765"
assert settings.analyze_poll_interval_seconds == 30
```

Add an environment override test using `monkeypatch`:

```python
monkeypatch.setenv("API_BASE_URL", "https://api.example.test/base/")
monkeypatch.setenv("ANALYZE_POLL_INTERVAL_SECONDS", "2.5")
settings = Settings(_env_file=None, openai_api_key="sk-test-value")
assert settings.api_base_url == "https://api.example.test/base"
assert settings.analyze_poll_interval_seconds == 2.5
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
uv run pytest tests/unit/test_config.py -q
```

Expected: failures because the two settings do not exist.

- [ ] **Step 3: Implement and validate settings**

Add:

```python
api_base_url: str = "http://127.0.0.1:8765"
analyze_poll_interval_seconds: float = Field(default=30, gt=0)
```

Validate `api_base_url` with `urllib.parse.urlsplit`: trim whitespace and trailing
slashes, require `http` or `https`, require a hostname, and reject query or fragment
components.

- [ ] **Step 4: Add invalid-value tests**

Parameterize empty, non-HTTP, hostless, query-bearing, and fragment-bearing URLs, plus
zero and negative poll intervals. Each must raise `ValidationError`.

- [ ] **Step 5: Run tests and verify GREEN**

Run:

```bash
uv run pytest tests/unit/test_config.py -q
```

Expected: all config tests pass.

- [ ] **Step 6: Document environment defaults**

Add these entries to `.env.example`:

```env
API_BASE_URL=http://127.0.0.1:8765
ANALYZE_POLL_INTERVAL_SECONDS=30
```

### Task 2: HTTP Request Boundary And Health Check

**Files:**
- Create: `src/insightcast/cli/analyze.py`
- Create: `tests/unit/test_analyze_cli.py`

- [ ] **Step 1: Write failing health success test**

Create a scripted requester that records method, URL, and JSON body, then returns:

```python
{
    "status": "ok",
    "message": "Insight Cast is ready.",
    "dependencies": {"ffmpeg": "ready", "queue_worker": "ready"},
}
```

Call the CLI workflow and assert the first request is
`GET http://127.0.0.1:8765/health` and output includes both dependency values.

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
uv run pytest tests/unit/test_analyze_cli.py::test_checks_healthy_api_before_creating_job -q
```

Expected: import failure because `insightcast.cli.analyze` does not exist.

- [ ] **Step 3: Implement request and response primitives**

Implement:

```python
@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    body: bytes

Requester = Callable[[str, str, dict[str, object] | None], HttpResponse]
```

The production requester uses `urllib.request.Request` and `urlopen`, sets
`Content-Type: application/json` for POST, decodes JSON as UTF-8, and converts
`HTTPError`, `URLError`, timeouts, malformed JSON, non-object JSON, and non-2xx
responses into typed CLI exceptions.

- [ ] **Step 4: Implement health validation**

Require object fields `status`, `dependencies.ffmpeg`, and
`dependencies.queue_worker`. Return an API failure before POST unless status is
`ok` and both dependencies are `ready`.

- [ ] **Step 5: Add unavailable and dependency-not-ready tests**

Assert connection failure output names the API URL and `uv run cast_api`. Assert a
dependency failure prints the actual dependency values and that the requester never
receives a POST.

- [ ] **Step 6: Run health tests and verify GREEN**

Run:

```bash
uv run pytest tests/unit/test_analyze_cli.py -k "health or unavailable or dependency" -q
```

Expected: selected tests pass.

### Task 3: Job Creation And Polling

**Files:**
- Modify: `src/insightcast/cli/analyze.py`
- Modify: `tests/unit/test_analyze_cli.py`

- [ ] **Step 1: Write failing POST contract test**

Script a healthy response, a `202` queued response, and a terminal
`WAITING_SELECTION` response. Assert the POST body is exactly:

```python
{"youtube_url": "https://www.youtube.com/watch?v=abc123DEF_-"}
```

and contains no candidate or duration overrides.

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
uv run pytest tests/unit/test_analyze_cli.py::test_posts_only_youtube_url -q
```

Expected: failure because job creation is not implemented.

- [ ] **Step 3: Implement job creation validation**

POST `/api/v1/analysis-jobs`, require `job_id`, `status`, and `message`, print the
queued job ID, and retain the ID for all later diagnostics.

- [ ] **Step 4: Write failing progression and timing test**

Provide `QUEUED`, repeated `INGESTING`, then `WAITING_SELECTION` poll responses.
Inject a fake monotonic clock and sleep function. Assert:

```python
assert sleeps == [2.5, 2.5, 2.5]
```

Assert the first GET occurs immediately after POST, every poll prints a timestamped
heartbeat, unchanged status remains visible, and changed statuses are clearly marked.

- [ ] **Step 5: Implement polling**

Recognize only:

```python
ACTIVE_STATUSES = {"QUEUED", "INGESTING", "TRANSCRIBING", "CURATING"}
SUCCESS_STATUS = "WAITING_SELECTION"
FAILURE_STATUS = "FAILED"
```

Poll immediately, sleep only before later polls, compute total elapsed time from the
monotonic clock, and fail on any unknown status.

- [ ] **Step 6: Run polling tests and verify GREEN**

Run:

```bash
uv run pytest tests/unit/test_analyze_cli.py -k "posts_only or progression or heartbeat or unknown" -q
```

Expected: selected tests pass.

### Task 4: Success And Verbose Formatting

**Files:**
- Modify: `src/insightcast/cli/analyze.py`
- Modify: `tests/unit/test_analyze_cli.py`

- [ ] **Step 1: Write failing success formatting test**

Use a terminal response containing candidate `A` and source artifacts. Assert output
contains candidate ID, title, `00:01:30` to `00:03:00`, `1m 30s`, selection reason,
summary, every source artifact path, and a notice that rendering was not performed.

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
uv run pytest tests/unit/test_analyze_cli.py::test_formats_candidates_and_source_artifacts -q
```

Expected: missing success formatting.

- [ ] **Step 3: Implement formatting helpers**

Implement stable helpers for:

```python
format_elapsed(seconds: float) -> str
format_timecode(seconds: float) -> str
format_candidate(candidate: dict[str, object]) -> list[str]
format_source_artifacts(artifacts: dict[str, object]) -> list[str]
```

Validate required candidate fields before formatting and treat malformed candidates
or artifacts as protocol errors.

- [ ] **Step 4: Write and implement verbose JSON test**

Run the same scripted workflow with `--verbose`. Assert every successful response
object is emitted directly after its formatted response using:

```python
json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
```

- [ ] **Step 5: Run formatting tests and verify GREEN**

Run:

```bash
uv run pytest tests/unit/test_analyze_cli.py -k "formats_candidates or verbose" -q
```

Expected: selected tests pass.

### Task 5: API, Protocol, Failed Job, And Interrupt Errors

**Files:**
- Modify: `src/insightcast/cli/analyze.py`
- Modify: `tests/unit/test_analyze_cli.py`

- [ ] **Step 1: Write failing standard API error test**

Return a non-2xx JSON body:

```python
{
    "error_code": "INVALID_YOUTUBE_URL",
    "message": "Unsupported URL.",
    "details": {"youtube_url": "bad"},
}
```

Assert output includes all three fields and exit code `1`.

- [ ] **Step 2: Implement API error formatting**

Decode the standard error shape when present. Otherwise print HTTP status and decoded
response body without exposing headers or environment values.

- [ ] **Step 3: Write malformed response tests**

Cover malformed JSON, JSON arrays, and missing required health/job fields. Assert each
is reported as an API protocol error with exit code `1`.

- [ ] **Step 4: Write and implement failed-job formatting**

For a `FAILED` response, print `stage`, `error_code`, `message`, and indented details.
When a source path exists, derive `<job-output-dir>/pipeline.log` from the parent of a
source artifact. Otherwise print guidance to locate the job under `OUTPUT_DIR/jobs/`.

- [ ] **Step 5: Write and implement post-creation connection failure**

Make the poll GET raise a connection error. Assert the retained job ID is printed and
the CLI does not issue a second POST.

- [ ] **Step 6: Write and implement Ctrl-C behavior**

Raise `KeyboardInterrupt` during sleep. Assert exit code `130`, output states local
monitoring stopped while the API job may continue, and includes the retained job ID.

- [ ] **Step 7: Run all CLI tests and verify GREEN**

Run:

```bash
uv run pytest tests/unit/test_analyze_cli.py -q
```

Expected: all CLI tests pass.

### Task 6: Entry Point And Documentation

**Files:**
- Modify: `pyproject.toml`
- Create: `AGENTS.md`
- Modify: `README.md`
- Modify: `.env.example`
- Modify locally: `.env` when present
- Modify: `tests/test_repository_contract.py`

- [ ] **Step 1: Write failing repository contract assertions**

Assert `pyproject.toml` contains:

```toml
cast_analyze = "insightcast.cli.analyze:main"
```

Assert root `AGENTS.md` exists and documents `uv run cast_analyze`,
`WAITING_SELECTION`, `--verbose`, separate server lifecycle, failure log inspection,
and no render without explicit user instruction.

- [ ] **Step 2: Run contract tests and verify RED**

Run:

```bash
uv run pytest tests/test_repository_contract.py -q
```

Expected: failure because the script and agent instructions are absent.

- [ ] **Step 3: Register the command**

Add the package script under `[project.scripts]`.

- [ ] **Step 4: Add agent workflow**

Create `AGENTS.md` with the canonical eight-step analysis workflow from the approved
spec. Keep server startup, shutdown, and rendering outside the command.

- [ ] **Step 5: Add human documentation**

Extend the README environment table with `API_BASE_URL` and
`ANALYZE_POLL_INTERVAL_SECONDS`. Add a CLI section showing separate
`uv run cast_api` and `uv run cast_analyze` terminals, `--verbose`, terminal status,
exit semantics, and analysis-only behavior.

- [ ] **Step 6: Update environment files**

Ensure `.env.example` and the ignored root `.env`, when present, contain:

```env
API_BASE_URL=http://127.0.0.1:8765
ANALYZE_POLL_INTERVAL_SECONDS=30
```

- [ ] **Step 7: Run contract and help checks**

Run:

```bash
uv run pytest tests/test_repository_contract.py -q
uv run cast_analyze --help
```

Expected: tests pass and help displays the URL positional argument and `--verbose`.

### Task 7: Full Verification And Review

**Files:**
- Review all changed files

- [ ] **Step 1: Run Ruff**

Run:

```bash
uv run ruff check .
```

Expected: no lint errors.

- [ ] **Step 2: Run all tests**

Run:

```bash
uv run pytest
```

Expected: all tests pass.

- [ ] **Step 3: Run CLI help**

Run:

```bash
uv run cast_analyze --help
```

Expected: exit code `0` and documented usage.

- [ ] **Step 4: Review the diff**

Run:

```bash
git diff --check
git status --short
git diff --stat
```

Confirm there are no generated files, unrelated edits, trailing whitespace, secret
values, or accidental server lifecycle/render behavior.
