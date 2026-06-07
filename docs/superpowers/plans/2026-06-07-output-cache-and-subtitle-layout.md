# Output Cache and Subtitle Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add configurable candidate defaults, shared YouTube source caching, timestamped job output organization, cache cleanup commands, and Chinese-first bilingual subtitles with strict translation validation.

**Architecture:** Keep FastAPI request validation at the API boundary and persist resolved analysis options on each job. Add a focused `SourceCache` storage component that owns cache validation, atomic replacement, metadata serialization, listing, and deletion; `SourceEngine` coordinates ingestion and job directory naming around it. Keep subtitle validation in `LingoEngine` and presentation rules in the ASS serializer.

**Tech Stack:** Python 3.13, FastAPI, Pydantic v2, pytest, pytest-asyncio, yt-dlp, FFmpeg, Click-compatible standard argparse CLI entry points.

---

### Task 1: Configurable Candidate Defaults and API Overrides

**Files:**
- Modify: `src/insightcast/core/config.py`
- Modify: `src/insightcast/api/schemas.py`
- Modify: `src/insightcast/api/routes/analysis_jobs.py`
- Modify: `src/insightcast/domain/models.py`
- Modify: `src/insightcast/services/job_service.py`
- Modify: `src/insightcast/api/app.py`
- Modify: `.env.example`
- Modify: `README.md`
- Test: `tests/unit/test_config.py`
- Test: `tests/api/test_analysis_jobs.py`
- Test: `tests/api/test_openapi.py`
- Test: `tests/service/test_job_service.py`

- [ ] **Step 1: Write failing settings and API tests**

Add settings tests for `DEFAULT_CANDIDATE_COUNT`, `DEFAULT_MIN_DURATION_MINUTES`,
and `DEFAULT_MAX_DURATION_MINUTES`, including candidate bounds, positive durations,
and the maximum/minimum relationship. Add API tests for all-omitted defaults,
field-by-field overrides, explicit `null`, and an invalid merged range.

- [ ] **Step 2: Run focused tests and verify failure**

Run:

```bash
uv run pytest tests/unit/test_config.py tests/api/test_analysis_jobs.py tests/api/test_openapi.py -q
```

Expected: failures because settings fields and request merge behavior do not exist.

- [ ] **Step 3: Implement settings and request resolution**

Add the three settings fields with Pydantic constraints and an after-model validator.
Make request override fields optional but non-null when present by rejecting explicit
`null` in a before-model validator. Add:

```python
def resolve_candidate_options(
    self,
    settings: Settings,
) -> tuple[int, float, float]:
    candidate_count = self.candidate_count or settings.default_candidate_count
    minimum = self.min_duration_minutes or settings.default_min_duration_minutes
    maximum = self.max_duration_minutes or settings.default_max_duration_minutes
    if maximum < minimum:
        raise ValueError("max_duration_minutes must be at least min_duration_minutes")
    return candidate_count, minimum, maximum
```

Use explicit `is None` checks in the implementation so valid numeric values are
never selected by truthiness. Inject `SettingsDependency` into the route, pass the
resolved values to `JobService`, and store them on `AnalysisJob` as resolved fields.

- [ ] **Step 4: Remove fixed OpenAPI defaults and update docs**

Descriptions must call the three fields optional server-default overrides, and the
schema must not contain fixed `default` values. Add the environment variables to
`.env.example` and the README configuration table.

- [ ] **Step 5: Verify focused tests**

Run:

```bash
uv run pytest tests/unit/test_config.py tests/api/test_analysis_jobs.py tests/api/test_openapi.py tests/service/test_job_service.py -q
```

Expected: all focused tests pass.

### Task 2: Shared Source Cache and Sanitized Metadata

**Files:**
- Create: `src/insightcast/storage/source_cache.py`
- Modify: `src/insightcast/infrastructure/ytdlp_client.py`
- Modify: `src/insightcast/engines/source_engine.py`
- Modify: `src/insightcast/api/app.py`
- Modify: `src/insightcast/domain/enums.py`
- Modify: `src/insightcast/utils/youtube.py`
- Test: `tests/unit/test_source_cache.py`
- Test: `tests/unit/test_source_engine.py`
- Test: `tests/unit/test_ytdlp_client.py`
- Test: `tests/unit/test_youtube.py`

- [ ] **Step 1: Write failing cache tests**

Cover normalized video ID extraction, cache paths, valid hit behavior, incomplete
entry repair, repair failure preservation, sanitized metadata, and invalid cache
metadata. Fakes must assert a cache hit invokes neither yt-dlp nor FFmpeg.

- [ ] **Step 2: Run focused tests and verify failure**

Run:

```bash
uv run pytest tests/unit/test_source_cache.py tests/unit/test_source_engine.py tests/unit/test_ytdlp_client.py tests/unit/test_youtube.py -q
```

Expected: import and behavior failures because the cache does not exist.

- [ ] **Step 3: Implement cache models and validation**

Define a stable metadata model containing only:

```python
video_id: str
title: str
description: str
duration_seconds: float
uploader: str | None
upload_date: str | None
webpage_url: str
tags: list[str]
```

`SourceCache.load(video_id)` returns a cache entry only when `metadata.json` parses,
IDs match, and both media files are non-empty. Every derived path must resolve below
`outputs/source-cache`.

- [ ] **Step 4: Implement atomic cache creation and repair**

Build into a sibling temporary directory, write sanitized metadata atomically, then
promote the completed directory. When replacing an existing invalid entry, rename it
to a backup, promote the new entry, and remove the backup only after success; restore
the backup if promotion fails.

- [ ] **Step 5: Integrate SourceEngine**

Resolve video ID before metadata retrieval. On hit, return cached artifacts and
metadata immediately. On miss, fetch metadata, download `source.mp4`, extract
`audio.mp3`, write `metadata.json`, then return the promoted entry. Log cache
hit/miss/repair decisions without raw metadata or signed URLs.

- [ ] **Step 6: Verify focused tests**

Run:

```bash
uv run pytest tests/unit/test_source_cache.py tests/unit/test_source_engine.py tests/unit/test_ytdlp_client.py tests/unit/test_youtube.py -q
```

Expected: all focused tests pass.

### Task 3: Job and Render Output Layout

**Files:**
- Modify: `src/insightcast/utils/files.py`
- Modify: `src/insightcast/engines/source_engine.py`
- Modify: `src/insightcast/services/job_service.py`
- Modify: `src/insightcast/core/logging.py`
- Modify: `README.md`
- Test: `tests/unit/test_files.py`
- Test: `tests/unit/test_source_engine.py`
- Test: `tests/service/test_job_service.py`

- [ ] **Step 1: Write failing path tests**

Assert new jobs live below `outputs/jobs`, source artifacts point into
`outputs/source-cache/<video-id>`, direct jobs retain `render/`, and render batches
use `<timestamp>-<render-id-prefix>`.

- [ ] **Step 2: Run focused tests and verify failure**

Run:

```bash
uv run pytest tests/unit/test_files.py tests/unit/test_source_engine.py tests/service/test_job_service.py -q
```

Expected: old root-level job and microsecond render names fail.

- [ ] **Step 3: Implement output naming**

Create provisional and final job directories below `output_root / "jobs"`. Change
the render helper to:

```python
def build_render_dir_name(created_at: datetime, render_id: str) -> str:
    return f"{_timestamp(created_at)}-{render_id[:6]}"
```

Keep direct render outputs in `job.output_dir / "render"` and analysis outputs in
`analysis/` and `renders/`.

- [ ] **Step 4: Preserve logs and state during finalization**

Update provisional-directory containment checks for the `jobs` root and preserve
the existing atomic `job_state.json` behavior.

- [ ] **Step 5: Verify focused tests**

Run:

```bash
uv run pytest tests/unit/test_files.py tests/unit/test_source_engine.py tests/service/test_job_service.py -q
```

Expected: all focused tests pass.

### Task 4: Cache Cleanup CLI

**Files:**
- Create: `src/insightcast/cli/cache.py`
- Create: `src/insightcast/cli/__init__.py`
- Modify: `pyproject.toml`
- Modify: `README.md`
- Test: `tests/unit/test_cache_cli.py`
- Test: `tests/unit/test_source_cache.py`

- [ ] **Step 1: Write failing cleanup and CLI tests**

Cover list output fields, one-entry removal, invalid IDs, path containment,
`clear` refusal without `--yes`, and successful confirmed clear.

- [ ] **Step 2: Run focused tests and verify failure**

Run:

```bash
uv run pytest tests/unit/test_source_cache.py tests/unit/test_cache_cli.py -q
```

Expected: failures because cleanup methods and `cast_cache` do not exist.

- [ ] **Step 3: Implement cleanup operations**

`SourceCache.list_entries()` returns video ID, title, source size, audio size, and
last modification time for validated entries only. `remove(video_id)` validates the
ID and containment before deleting. `clear()` removes only children of the cache root.

- [ ] **Step 4: Implement CLI entry point**

Expose:

```toml
cast_cache = "insightcast.cli.cache:main"
```

Use argparse subcommands `list`, `remove <youtube-video-id>`, and
`clear --yes`. Missing `--yes` exits non-zero with a clear message.

- [ ] **Step 5: Verify focused tests**

Run:

```bash
uv run pytest tests/unit/test_source_cache.py tests/unit/test_cache_cli.py -q
```

Expected: all focused tests pass.

### Task 5: Chinese-First Subtitle Layout and Quality Guard

**Files:**
- Modify: `src/insightcast/engines/lingo_engine.py`
- Modify: `src/insightcast/utils/ass.py`
- Modify: `src/insightcast/domain/enums.py`
- Test: `tests/unit/test_lingo_engine.py`
- Test: `tests/unit/test_subtitles.py`

- [ ] **Step 1: Write failing validation and serialization tests**

Add cases for blank translations, whitespace, punctuation-only strings, missing or
reordered IDs, Chinese style/event ordering, `&H0082E0FF`, Chinese upper margin, and
English lower title-safe margin.

- [ ] **Step 2: Run focused tests and verify failure**

Run:

```bash
uv run pytest tests/unit/test_lingo_engine.py tests/unit/test_subtitles.py -q
```

Expected: punctuation-only translations and old style ordering fail.

- [ ] **Step 3: Implement translation guard**

Normalize with `.strip()` and reject text for which no Unicode character is
alphanumeric. Raise the stable subtitle-generation error with the offending
segment ID, without rewriting valid translations.

- [ ] **Step 4: Implement ASS layout**

Serialize the `TraditionalChinese` style before `English`, set Chinese primary color
to `&H0082E0FF`, use PingFang TC 46 and Arial 44, and give Chinese a larger bottom
margin than English so it renders above. Emit the Chinese event before the English
event for each interval.

- [ ] **Step 5: Verify focused tests**

Run:

```bash
uv run pytest tests/unit/test_lingo_engine.py tests/unit/test_subtitles.py -q
```

Expected: all focused tests pass.

### Task 6: Integration, Documentation, and Full Verification

**Files:**
- Modify: `README.md`
- Modify: `.env.example`
- Modify only other files when verification exposes a root-cause defect.

- [ ] **Step 1: Update operational documentation**

Document the new output tree, cache lifetime, cleanup commands, configured defaults,
and the behavior when historical job state references a removed cache entry.

- [ ] **Step 2: Run static checks**

Run:

```bash
uv run ruff check .
git diff --check
```

Expected: both commands pass.

- [ ] **Step 3: Run full automated tests**

Run:

```bash
uv run pytest
```

Expected: all tests pass.

- [ ] **Step 4: Run focused media acceptance when FFmpeg is available**

Generate a representative ASS file, burn it onto a short synthetic 1920x1080 clip,
and inspect output dimensions, duration, and extracted frame line ordering.

- [ ] **Step 5: Review the final diff**

Confirm no raw yt-dlp payload, signed URLs, cookies, or request headers are persisted;
all cache deletion is contained below `outputs/source-cache`; and historical output
directories are untouched.
