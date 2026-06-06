# Insight Cast FastAPI MVP Design

Date: 2026-06-06
Status: Approved design, pending user review of written specification

## 1. Product Definition

Insight Cast is an open-source, local-first AI knowledge curation tool. It turns
long-form English YouTube podcasts and conversations into thoughtful,
medium-length videos with English and Traditional Chinese subtitles.

FastAPI is the operational interface. The MVP has no frontend, authentication,
database, Celery worker, or public-service security model. Users clone the
repository, configure environment variables, start the API, and operate it
through Swagger UI.

The project is initialized and maintained as a Git repository. Generated
outputs, secrets, caches, local virtual environments, downloaded models, and
temporary media are excluded through `.gitignore`.

The default API address is:

```text
http://127.0.0.1:8765
http://127.0.0.1:8765/docs
```

The MVP supports YouTube URLs only. Local video input is explicitly deferred.

## 2. User Workflows

### 2.1 Automatic Analysis

The user submits a YouTube URL and optional curation parameters. Insight Cast
downloads the video, transcribes the complete English audio, and uses an LLM to
produce ordered candidates identified as `A`, `B`, `C`, and so on.

Defaults:

- Candidate count: 2
- Minimum candidate duration: 8 minutes
- Maximum candidate duration: 12 minutes
- Candidate overlap: allowed

The requested number of valid candidates is mandatory. If the system cannot
produce that exact number within the requested duration limits, the job fails
with `INSUFFICIENT_CANDIDATES`.

Candidate output includes:

- Stable candidate ID for later selection
- Start and end timestamps
- Suggested title
- Selection reason
- Summary
- Optional score

The analysis job stops at `WAITING_SELECTION`. It does not render video until
the user explicitly selects candidates.

### 2.2 Candidate Rendering

The user submits one or more candidate IDs from an existing analysis job.
Candidate selection accepts either a string or a list and normalizes both to an
ordered, duplicate-free list.

Examples:

```json
{"candidate_ids": "A"}
```

```json
{"candidate_ids": ["A", "C"]}
```

Each selected candidate produces:

- Traditional Chinese SRT
- Bilingual English and Traditional Chinese ASS
- Bilingual subtitle-burned MP4
- Complete YouTube metadata JSON

The temporary unburned clip is deleted after successful rendering. The source
video remains available so users can render additional candidates later.

Previously completed candidates are skipped by default and their existing
artifacts are returned. `force_render=true` creates a new timestamped render
batch and never overwrites previous output.

### 2.3 Direct Rendering

The user submits a YouTube URL and one explicit start/end time range. Every
direct render creates a new job and output directory, even when the URL was
processed before.

Direct rendering skips candidate curation and produces:

- Traditional Chinese SRT
- Bilingual English and Traditional Chinese ASS
- Bilingual subtitle-burned MP4
- Complete YouTube metadata JSON

Only one time range is accepted per direct-render request.

### 2.4 YouTube Upload Stub

Upload endpoints verify that a rendered video and metadata exist, then return a
clear `NOT_IMPLEMENTED` response. The MVP does not implement YouTube OAuth or
actual uploading.

## 3. API Contract

### 3.1 Health

```http
GET /health
```

Returns service health and basic dependency readiness.

### 3.2 Create Analysis Job

```http
POST /api/v1/analysis-jobs
```

Request:

```json
{
  "youtube_url": "https://www.youtube.com/watch?v=...",
  "candidate_count": 2,
  "min_duration_minutes": 8,
  "max_duration_minutes": 12,
  "force_reanalyze": false
}
```

The endpoint immediately returns a job ID and queue status. During one server
process, a normalized YouTube URL maps to its latest analysis job. Repeating the
request returns that job unless `force_reanalyze=true`.

### 3.3 Get Analysis Job

```http
GET /api/v1/analysis-jobs/{job_id}
```

Returns job status, message, candidates when available, render batches, errors,
and structured absolute artifact paths.

### 3.4 Render Analysis Candidates

```http
POST /api/v1/analysis-jobs/{job_id}/renders
```

Request:

```json
{
  "candidate_ids": ["A", "B"],
  "force_render": false
}
```

The endpoint immediately returns the queued render batch ID and status.

### 3.5 List Render Batches

```http
GET /api/v1/analysis-jobs/{job_id}/renders
```

Returns all render batches and artifacts for the analysis job.

### 3.6 Create Direct Render Job

```http
POST /api/v1/direct-render-jobs
```

Request:

```json
{
  "youtube_url": "https://www.youtube.com/watch?v=...",
  "start_time": "00:12:30",
  "end_time": "00:22:00"
}
```

`start_time` and `end_time` accept timecode strings or numeric seconds. The end
time must be greater than the start time.

### 3.7 Get Direct Render Job

```http
GET /api/v1/direct-render-jobs/{job_id}
```

Returns status, human-readable progress, errors, metadata, and artifact paths.

### 3.8 Upload Stubs

```http
POST /api/v1/analysis-jobs/{job_id}/youtube-uploads
POST /api/v1/direct-render-jobs/{job_id}/youtube-uploads
```

These endpoints return `NOT_IMPLEMENTED` with the publishable video and metadata
paths.

### 3.9 Response Design

Every successful operational response includes:

- `status`: stable machine-readable status
- `message`: human-readable explanation assembled from current values
- `artifacts`: structured absolute local paths
- Identifiers such as `job_id` and `render_id`
- Timestamps where relevant

All request and response fields have Pydantic descriptions and examples so
Swagger UI is a complete operational interface.

Runtime errors use a stable shape:

```json
{
  "error_code": "INVALID_TIME_RANGE",
  "message": "end_time must be later than start_time.",
  "details": {
    "start_time": "00:22:00",
    "end_time": "00:12:30"
  }
}
```

## 4. Runtime Architecture

```text
FastAPI and Swagger UI
        |
JobService and in-memory registries
        |
Single-process asyncio FIFO queue
        |
Source / Lingo / Curator / Clip / Publish Engines
        |
OpenAI / yt-dlp / FFmpeg infrastructure clients
        |
Local output files
```

FastAPI creates one `asyncio.Queue` and one worker during application startup.
Analysis and rendering work enters the same queue so only one CPU-intensive
pipeline operation runs at a time. Blocking SDK, yt-dlp, FFmpeg, and local
Whisper operations run outside the event loop.

The queue and job registry exist only for the current server process. Server
restart does not reload, resume, or expose historical jobs. Output files,
`job_state.json`, and logs remain available for manual inspection.

## 5. Module Boundaries

Suggested source layout:

```text
src/insightcast/
  api/
    app.py
    dependencies.py
    routes/
      analysis_jobs.py
      direct_render_jobs.py
      health.py
  core/
    config.py
    exceptions.py
    logging.py
  domain/
    enums.py
    models.py
  engines/
    source_engine.py
    lingo_engine.py
    curator_engine.py
    clip_engine.py
    publish_engine.py
  infrastructure/
    ffmpeg_client.py
    ytdlp_client.py
    openai_client.py
    transcription/
      base.py
      openai_transcription_client.py
      local_whisper_client.py
  prompts/
    curator.py
    translation.py
    metadata.py
  services/
    job_service.py
    queue_worker.py
  storage/
    file_job_writer.py
  utils/
    files.py
    srt.py
    ass.py
    timecode.py
    youtube.py
```

Responsibilities:

- API validates inputs and documents contracts.
- JobService coordinates pipelines, state, URL cache, and render batches.
- Engines implement application behavior without HTTP concerns.
- Infrastructure clients isolate external SDK and subprocess behavior.
- Prompt modules contain independently versioned system prompts.
- Storage writes state snapshots and artifacts but does not restore jobs.
- Domain models define all persisted and API-visible structures.

## 6. Job and Render State

Job statuses:

```text
QUEUED
INGESTING
TRANSCRIBING
CURATING
WAITING_SELECTION
RENDERING
COMPLETED
FAILED
```

Each job records:

- Job ID and job type
- Normalized and original YouTube URL
- Current status and progress message
- Output directory
- Creation and update timestamps
- Candidates where applicable
- Render batches
- Artifact paths
- Structured error

Each render batch records:

- Render ID
- Ordered candidate IDs
- Status and timestamps
- Artifact paths by candidate
- Candidate-specific errors

If one candidate in a multi-select render succeeds and another fails, successful
artifacts remain. The batch is marked failed with candidate-specific details,
and the failed candidate can be submitted again.

## 7. Output Layout and File Lifecycle

Analysis job:

```text
outputs/
  20260606-143000_video-title_a1b2c3/
    job_state.json
    pipeline.log
    source/
      video-title.source.mp4
      video-title.audio.mp3
    analysis/
      transcript.json
      candidates.json
    renders/
      20260606-151000/
        candidate-a/
          video-title.a.zh-TW.srt
          video-title.a.bilingual.ass
          video-title.a.bilingual.burned.mp4
          video-title.a.youtube-metadata.json
```

Direct render:

```text
outputs/
  20260606-160000_video-title_direct_d4e5f6/
    job_state.json
    pipeline.log
    source/
      video-title.source.mp4
      video-title.audio.mp3
    render/
      video-title.custom.zh-TW.srt
      video-title.custom.bilingual.ass
      video-title.custom.bilingual.burned.mp4
      video-title.custom.youtube-metadata.json
```

Temporary files are placed under `.work/`. Successful rendering deletes the
temporary unburned clip. Failed work retains temporary files for diagnosis.

File and directory names use sanitized YouTube titles, timestamps, and short job
IDs. API responses expose absolute paths.

## 8. Source and Video Processing

yt-dlp downloads the best available video and audio combination, capped at
1080p by default, and merges it into MP4.

FFmpeg performs:

- Audio extraction and compression
- Precise clip cutting by re-encoding
- Bilingual ASS subtitle burning
- H.264 output with configurable CRF, default 18
- AAC audio for broad compatibility

The system checks that FFmpeg is executable during startup. yt-dlp and FFmpeg
failures are converted into clear application errors and logged with subprocess
details.

## 9. Transcription

The default provider is OpenAI transcription. Local `faster-whisper` remains a
configurable offline fallback.

```env
TRANSCRIPTION_PROVIDER=openai
OPENAI_TRANSCRIPTION_MODEL=whisper-1
WHISPER_MODEL_SIZE=large-v3
WHISPER_DEVICE=auto
```

OpenAI transcription uses `whisper-1` because the subtitle workflow requires
timestamped segments. Long audio is compressed and split into chunks below the
provider upload limit. Chunk timestamps are offset back to the complete video
timeline.

The MVP supports English source audio only. Detected unsupported languages fail
with `UNSUPPORTED_LANGUAGE`. The provider interface keeps room for explicitly
supported languages such as Japanese later.

## 10. AI Models and Prompts

All AI text judgment defaults to:

```env
LLM_MODEL=gpt-5.4-mini
CURATOR_MODEL=gpt-5.4-mini
TRANSLATION_MODEL=gpt-5.4-mini
METADATA_MODEL=gpt-5.4-mini
```

Specialized model settings fall back to `LLM_MODEL` when omitted.

System prompts are not embedded in the OpenAI client. They live in dedicated
modules:

```text
prompts/curator.py
prompts/translation.py
prompts/metadata.py
```

Each module defines:

- System prompt
- User prompt builder
- Prompt version
- Input and output contract

The OpenAI client only handles SDK calls, system/user message transport,
structured responses, timeout, retry, and error conversion. Engines select the
prompt, model, and Pydantic response schema.

Generated JSON records model and prompt version for traceability. The API does
not allow arbitrary system prompt injection.

## 11. Curation

The Curator receives timestamped English transcript segments and the requested
candidate count and duration range. It selects continuous segments with complete
idea arcs and useful context. It does not create montages, optimize for
controversy, or require candidates to be non-overlapping.

The result uses Structured Outputs where supported and is always validated with
Pydantic. Application validation checks:

- Exact candidate count
- Sequential IDs beginning with `A`
- Start before end
- Duration inside requested limits
- Times inside transcript duration
- Required title, reason, and summary

Invalid output is retried once with validation feedback. A second invalid result
fails the job.

## 12. Translation and Subtitle Generation

Translation targets natural Traditional Chinese for a Taiwanese audience. It
preserves meaning, proper nouns, and technical terminology without overly
literal phrasing.

Translation operates in contextual batches while maintaining a one-to-one
mapping with source subtitle items. The system validates item count and timing
before writing files.

Outputs:

- `zh-TW.srt`: Traditional Chinese subtitle track
- `bilingual.ass`: English on top, Traditional Chinese below
- Burned MP4 using the bilingual ASS file

The MVP does not perform speaker diarization. Subtitle timing is converted from
the original video timeline to the selected clip's relative timeline and
clamped to clip boundaries.

## 13. Metadata Generation

PublishEngine generates:

- Title
- Description
- Tags
- Privacy status, default `private`

The prompt targets thoughtful Traditional Chinese knowledge content and avoids
clickbait. Metadata is returned through the API and written to a JSON artifact.

## 14. Configuration and Startup

Configuration uses `pydantic-settings` and `.env`.

`src/insightcast/core/config.py` defines one typed `Settings` class as the
single configuration entry point. Other modules receive settings through
dependency injection and do not read `os.environ` directly. A cached
`get_settings()` factory initializes configuration once per process.

The Settings class is responsible for:

- Reading `.env` and process environment variables
- Applying documented defaults
- Resolving output paths
- Validating required secrets and model names
- Rejecting empty values and obvious placeholder API keys
- Validating numeric ranges such as API port, CRF, and maximum video height
- Exposing specialized model settings with fallback to `LLM_MODEL`

Core settings include:

```text
API_HOST=127.0.0.1
API_PORT=8765
OUTPUT_DIR=outputs
OPENAI_API_KEY=
OPENAI_BASE_URL=
LLM_MODEL=gpt-5.4-mini
CURATOR_MODEL=gpt-5.4-mini
TRANSLATION_MODEL=gpt-5.4-mini
METADATA_MODEL=gpt-5.4-mini
TRANSCRIPTION_PROVIDER=openai
OPENAI_TRANSCRIPTION_MODEL=whisper-1
WHISPER_MODEL_SIZE=large-v3
WHISPER_DEVICE=auto
FFMPEG_BIN=ffmpeg
VIDEO_MAX_HEIGHT=1080
VIDEO_CRF=18
```

The repository includes `.env.example` with every supported setting, safe
non-secret defaults, Chinese comments, and placeholder values where user input
is required. `.env` is ignored by Git and must never be committed.

Startup fails fast when:

- `OPENAI_API_KEY` is absent, empty, or an obvious placeholder
- Required model settings are empty
- FFmpeg cannot be executed

Local Whisper model loading and downloading are deferred until first use.

The console command is:

```toml
[project.scripts]
cast_api = "insightcast.api.app:run"
```

## 15. Documentation and Repository Setup

The implementation initializes Git before feature work and adds a focused
`.gitignore`.

The root `README.md` is written primarily in Traditional Chinese and includes:

- What Insight Cast does and what the MVP does not do
- Architecture and engine responsibilities
- Supported workflows with diagrams or concise step lists
- Prerequisites: Python 3.12+, uv, FFmpeg, network access, and an OpenAI API key
- macOS and common Linux FFmpeg installation examples
- How to verify `ffmpeg` and `uv` are available
- How to copy `.env.example` to `.env`
- A table explaining every environment variable, whether it is required, its
  default, and example values
- OpenAI API key preparation and security cautions
- Local Whisper fallback configuration and expected model download cost
- `uv sync`, test, and API startup commands
- Swagger UI usage at `http://127.0.0.1:8765/docs`
- Complete curl examples for analysis, status, rendering, direct rendering,
  and upload stubs
- Output directory and filename explanation
- Troubleshooting for startup validation, FFmpeg, yt-dlp, model downloads, and
  API errors
- CPU and processing-time expectations
- Docker instructions added only after local acceptance passes
- Copyright and lawful-use notice

The initial repository files include at least:

```text
.gitignore
.env.example
README.md
pyproject.toml
uv.lock
src/
tests/
outputs/.gitkeep
```

The generated `uv.lock` is committed so clones resolve the tested dependency
set reproducibly.

## 16. Error Handling

Stable error codes include:

```text
INVALID_YOUTUBE_URL
YOUTUBE_DOWNLOAD_FAILED
FFMPEG_NOT_AVAILABLE
AUDIO_EXTRACTION_FAILED
UNSUPPORTED_LANGUAGE
TRANSCRIPTION_FAILED
LLM_REQUEST_FAILED
INVALID_LLM_OUTPUT
INSUFFICIENT_CANDIDATES
INVALID_TIME_RANGE
CANDIDATE_NOT_FOUND
SUBTITLE_GENERATION_FAILED
VIDEO_RENDER_FAILED
JOB_NOT_FOUND
UPLOAD_NOT_IMPLEMENTED
```

Job failures record the stage, error code, user-facing message, and safe details.
Complete tracebacks are written only to `pipeline.log`.

## 17. Testing

Unit tests cover:

- Timecode conversion
- SRT and bilingual ASS generation
- Candidate count, duration, and boundary validation
- Transcript filtering and relative timing
- Job state file writing
- YouTube URL normalization and cache behavior
- String/list candidate selection normalization
- Output naming and sanitization
- Structured AI output validation and one retry

Service and API tests mock OpenAI, yt-dlp, and FFmpeg and cover:

- Analysis job queueing
- Same-URL job reuse and forced reanalysis
- Transition to `WAITING_SELECTION`
- Multi-select and incremental rendering
- Existing-render skipping
- Forced render batch versioning
- Direct render without Curator
- Partial render failure
- Unified API errors
- Swagger/OpenAPI descriptions, examples, and response schemas

## 18. Acceptance Order

Local development is completed and verified before Docker work begins.

Required local acceptance:

```bash
git status
uv sync
uv run pytest
uv run cast_api
```

While the API is running:

```text
GET http://127.0.0.1:8765/health
GET http://127.0.0.1:8765/docs
```

Acceptance requires:

- The workspace is a valid Git repository
- `.env`, generated outputs, caches, and temporary media are ignored
- Dependency synchronization succeeds
- All tests pass
- API starts on port 8765
- Health endpoint returns success
- Swagger UI loads
- OpenAPI documents all request fields, response fields, examples, and errors

Docker packaging is implemented only after these local checks pass. Final Docker
acceptance includes an FFmpeg-enabled CPU image, port 8765, `.env` support, and
a documented output volume:

```bash
docker build -t insightcast .
docker run --env-file .env -p 8765:8765 \
  -v "$(pwd)/outputs:/app/outputs" insightcast
```

## 19. Explicitly Deferred

- Frontend
- Local file input
- Authentication and authorization
- Public-service filesystem protections
- Database-backed job history
- Queue recovery after restart
- Celery or external workers
- Speaker diarization
- YouTube OAuth and upload
- Languages other than English
- Thumbnail generation
- 4K-first processing

## 20. Legal Note

Insight Cast does not grant rights to download, edit, republish, or otherwise
reuse third-party content. Users are responsible for obtaining permission and
ensuring lawful use.
