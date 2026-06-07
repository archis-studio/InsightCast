# Agent Analysis CLI Design

## Goal

Provide one documented command that a user or coding agent can run from the
repository root to analyze a YouTube URL through the existing Insight Cast API:

```bash
uv run cast_analyze "https://www.youtube.com/watch?v=..."
```

The command must make the asynchronous workflow visible in its console output,
finish when candidate analysis is ready, and leave API server lifecycle
management as a separate explicit operation.

## Scope

This change adds:

- a Python package CLI named `cast_analyze`
- agent-facing repository instructions in `AGENTS.md`
- human-facing CLI documentation in `README.md`
- CLI settings in `Settings`, `.env`, and `.env.example`
- focused configuration and CLI tests

This change does not:

- start, stop, or restart the API server
- cancel a server job when the CLI exits
- render candidates
- add or change API endpoints
- restore jobs after an API server restart
- stream the server-side `pipeline.log`

Candidate rendering will be designed as a later CLI extension after the
analysis-only command and its parameter semantics are established.

## Command And Configuration

Register this package script:

```toml
cast_analyze = "insightcast.cli.analyze:main"
```

The initial command accepts one positional YouTube URL:

```bash
uv run cast_analyze "<youtube-url>"
uv run cast_analyze --verbose "<youtube-url>"
```

`--verbose` prints the complete JSON response after each successful API
request, in addition to the normal formatted output.

The CLI reads these settings through the existing `Settings` class:

| Environment variable | Default | Constraint |
| --- | --- | --- |
| `API_BASE_URL` | `http://127.0.0.1:8765` | Non-empty HTTP or HTTPS URL |
| `ANALYZE_POLL_INTERVAL_SECONDS` | `30` | Greater than zero |

Both keys are added to `.env.example` and the current ignored `.env`. The main
README environment table documents them. `API_HOST` and `API_PORT` remain server
binding settings; `API_BASE_URL` is the client-facing URL and may differ for
containers or remote API access.

There is no default overall timeout. A long video may remain in ingestion or
transcription for hours.

## Architecture

The CLI is a thin HTTP client for the existing API contract. It does not import
or invoke `JobService`, engines, or queue internals.

Use the Python standard library for HTTP and JSON so the command does not add a
runtime dependency or require external `curl` or `jq` executables. Keep API
request handling, polling decisions, and console formatting in small,
independently testable functions within `src/insightcast/cli/analyze.py`.

The API continues to own the full pipeline:

```text
POST analysis job
  -> QUEUED
  -> INGESTING
  -> TRANSCRIBING
  -> CURATING
  -> WAITING_SELECTION
```

`WAITING_SELECTION` is the successful terminal state for this analysis-only
CLI. `FAILED` is the failure terminal state. The CLI must use the response's
machine-readable `status`, not infer stage completion from messages, elapsed
time, or artifact files.

## Execution Flow

1. Parse the YouTube URL and `--verbose`.
2. Load and validate `Settings`.
3. Print the API base URL and the requested YouTube URL.
4. Send `GET {API_BASE_URL}/health`.
5. Require HTTP 200, `status == "ok"`, `dependencies.ffmpeg == "ready"`, and
   `dependencies.queue_worker == "ready"`.
6. If health checking fails, stop before creating a job and tell the user to
   start the server separately with `uv run cast_api`.
7. Send `POST {API_BASE_URL}/api/v1/analysis-jobs` with only:

   ```json
   {"youtube_url": "<input-url>"}
   ```

   Candidate count and duration fields are intentionally omitted so server
   defaults remain authoritative.
8. Print the returned job ID, status, and message.
9. Poll `GET {API_BASE_URL}/api/v1/analysis-jobs/{job_id}` every
   `ANALYZE_POLL_INTERVAL_SECONDS`.
10. Print a timestamped line for every poll. Include status, API message, and
    total elapsed time. Clearly mark status changes; unchanged polls act as a
    heartbeat so the command never appears stalled.
11. On `WAITING_SELECTION`, print the candidate list and source artifact paths,
    then exit successfully.
12. On `FAILED`, print structured error information and exit unsuccessfully.
13. On an unknown status, stop with a protocol error rather than polling
    forever.

The first status GET occurs immediately after job creation. Sleep occurs between
subsequent polls.

## Console Output

Default output is concise but complete enough to understand progress:

```text
[12:00:00] Checking API: http://127.0.0.1:8765
[12:00:00] API ready: ffmpeg=ready, queue_worker=ready
[12:00:01] Analysis queued: job_id=abc123
[12:00:01] QUEUED: Analysis job is queued. (elapsed 0s)
[12:00:31] INGESTING: Downloading the source video. (elapsed 30s)
[12:01:01] INGESTING: Downloading the source video. (elapsed 1m 0s)
[12:01:31] TRANSCRIBING: Transcribing English audio. (elapsed 1m 30s)
```

On success, display each candidate with:

- candidate ID
- suggested title
- start and end timecodes
- duration
- selection reason
- summary

Also print available source artifact paths and remind the user that this command
analyzes only; it does not render.

With `--verbose`, print each complete decoded JSON object with stable
indentation directly after the corresponding formatted response. Do not redact
artifact paths or job metadata. Request headers and environment secrets are
never printed.

## Error Handling And Exit Codes

Use these exit semantics:

- `0`: analysis reached `WAITING_SELECTION`
- `1`: API or analysis failure
- `2`: invalid CLI arguments or invalid local configuration
- `130`: interrupted with `Ctrl-C`

Handle these classes distinctly:

- Connection failure: identify the URL and tell the user to run
  `uv run cast_api` in a separate terminal.
- Health dependency not ready: print dependency values and do not create a job.
- Non-2xx API response: decode the standard `error_code`, `message`, and
  `details` shape when possible; otherwise print the HTTP status and response
  body.
- Malformed JSON or missing required response fields: report an API protocol
  error.
- Poll connection interruption after job creation: report the retained job ID
  so the user can inspect it later. Do not create a replacement job
  automatically.
- `FAILED` job: print `stage`, `error_code`, `message`, and formatted `details`.
  If source artifact paths reveal the job output directory, print the expected
  sibling `pipeline.log` path. Otherwise tell the user to locate the job under
  `OUTPUT_DIR/jobs/`.
- `Ctrl-C`: print that local monitoring stopped while the API job may continue,
  include the job ID when available, and exit `130`.

The CLI does not retry POST requests because a lost response could result in an
ambiguous job creation outcome. Polling GET requests happen only on the normal
configured interval; automatic rapid retries are not introduced.

## Agent Instructions

Create a root `AGENTS.md` with a dedicated YouTube analysis workflow. It tells
agents:

1. Do not start or stop the API server as part of analysis.
2. Check that the user has separately run `uv run cast_api`.
3. Execute `uv run cast_analyze "<youtube-url>"`.
4. Use `--verbose` when raw API payloads are needed for diagnosis.
5. Treat `WAITING_SELECTION` as successful analysis completion.
6. Report candidate IDs, titles, time ranges, summaries, and artifact paths.
7. On failure, report the structured console error and inspect the referenced
   `pipeline.log` when available.
8. Do not queue renders unless the user explicitly requests rendering.

These instructions make the repository command the canonical workflow and keep
agents from rebuilding API orchestration with ad hoc curl commands.

## Testing

Add unit tests using a local fake HTTP server or injected request boundary. Tests
must not contact YouTube, OpenAI, or a real Insight Cast server.

Cover:

- default and environment-overridden CLI settings
- invalid API base URL and non-positive poll interval
- healthy server checks
- unavailable server guidance
- dependency-not-ready failure
- analysis POST body omits server-default candidate options
- status progression and heartbeat output
- immediate first poll and configured sleep interval
- success formatting for candidates and artifacts
- verbose full-JSON output
- standard API error formatting
- malformed response handling
- failed-job formatting and nonzero exit
- unknown status handling
- `Ctrl-C` message and exit behavior

Repository verification includes:

```bash
uv run ruff check .
uv run pytest
uv run cast_analyze --help
```

## Future Render Extension

The initial command intentionally leaves render semantics open. A later design
can add either a dedicated `cast_render` command or analysis options such as
`--render A,B`. That design must decide candidate selection defaults, whether
analysis and rendering share one invocation, render polling, force-render
behavior, and final artifact presentation without changing the analysis-only
success contract defined here.
