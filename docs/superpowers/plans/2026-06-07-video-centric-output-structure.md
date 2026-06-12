# Video-Centric Output Structure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Store all new Insight Cast artifacts beneath one YouTube-video directory, reuse validated source and transcript data, preserve immutable analyses and renders, and discover publishable renders from disk after restart.

**Architecture:** Introduce typed manifest models and a `VideoStore` that owns path construction, atomic manifest I/O, containment checks, cache lookup, and disk discovery. Keep media operations in the existing engines and workflow transitions in `JobService`; both receive paths and persisted records from `VideoStore`. Add video-centric read/upload-stub routes that require an explicit render ID, while legacy output directories remain unread and untouched.

**Tech Stack:** Python 3.13, Pydantic v2, FastAPI, pathlib, hashlib SHA-256, pytest, Ruff, uv

**Implementation status:** Completed. The checklist below is retained as the
historical implementation sequence; current behavior is documented in `README.md`
and enforced by the repository, API, CLI, storage, and acceptance tests named in
this plan.

---

## File Structure

### New files

- `src/insightcast/storage/manifests.py`: schema-versioned video, source, transcript, analysis, and render manifest models.
- `src/insightcast/storage/video_store.py`: canonical paths, atomic JSON, containment, source/transcript lookup, analysis/render creation, and restart-safe discovery.
- `src/insightcast/api/routes/videos.py`: list analyses/renders and validate an explicit render for future upload.
- `tests/unit/test_video_store.py`: storage contract, cache, containment, immutable IDs, and legacy exclusion.
- `tests/api/test_videos.py`: restart-safe render discovery and explicit upload target API.
- `tests/acceptance/test_video_output_lifecycle.py`: focused filesystem lifecycle using fresh store/service instances.

### Modified files

- `src/insightcast/domain/enums.py`: storage and manifest error codes.
- `src/insightcast/domain/models.py`: persisted identity fields and render-manifest path references.
- `src/insightcast/utils/files.py`: video, analysis, transcript, render, and operation identifiers.
- `src/insightcast/storage/file_job_writer.py`: reusable atomic JSON helper remains the low-level writer.
- `src/insightcast/engines/source_engine.py`: source reuse and repair through `VideoStore`.
- `src/insightcast/services/job_service.py`: transcript reuse, manifest transitions, candidate/direct paths, and stable artifact names.
- `src/insightcast/engines/clip_engine.py`: accept stable destination names.
- `src/insightcast/api/app.py`: construct/inject `VideoStore` and register video routes.
- `src/insightcast/api/dependencies.py`: expose `VideoStore`.
- `src/insightcast/api/schemas.py`: video, analysis, render-list, and explicit publish responses.
- `src/insightcast/api/routes/analysis_jobs.py`: return new artifact locations and remove implicit upload selection.
- `src/insightcast/api/routes/direct_render_jobs.py`: return custom-render locations and remove implicit upload selection.
- `src/insightcast/cli/cache.py`: manage source directories inside video roots without deleting analyses/renders.
- `src/insightcast/cli/analyze.py`: print video, analysis, transcript, candidate, and log paths.
- `README.md`: canonical tree, lookup table, reuse rules, discovery examples, and legacy notice.
- `AGENTS.md`: report the new video/analysis locations.
- Existing unit, service, API, OpenAPI, CLI, and repository-contract tests: update expected paths and contracts.

### Removed file

- `src/insightcast/storage/source_cache.py`: replaced by the video-root source operations in `VideoStore`.

---

### Task 1: Define Manifest And Identifier Contracts

**Files:**
- Create: `src/insightcast/storage/manifests.py`
- Modify: `src/insightcast/domain/enums.py`
- Modify: `src/insightcast/utils/files.py`
- Test: `tests/unit/test_domain_models.py`
- Test: `tests/unit/test_files.py`

- [ ] **Step 1: Write failing manifest and identifier tests**

```python
def test_manifest_models_reject_absolute_artifact_paths() -> None:
    with pytest.raises(ValidationError):
        RenderManifest(
            schema_version=1,
            render_id="20260607-120000-a1b2c3",
            operation_id="op-a1b2c3",
            kind=RenderKind.CANDIDATE,
            analysis_id="20260607-110000-d4e5f6",
            candidate_id="A",
            start_seconds=10,
            end_seconds=20,
            source_fingerprint="a" * 64,
            transcript_id="tx-a1b2c3",
            artifacts={"video": Path("/tmp/video.mp4")},
        )


def test_video_and_run_identifiers_are_readable_and_stable() -> None:
    now = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)
    assert build_video_dir_name("abc123DEF_-", "A Useful / Talk") == (
        "abc123DEF_-_a-useful-talk"
    )
    assert build_run_id(now, "abcdef1234") == "20260607-120000-abcdef"
```

- [ ] **Step 2: Run the focused tests and confirm failure**

Run: `uv run pytest tests/unit/test_domain_models.py tests/unit/test_files.py -q`

Expected: FAIL because manifest models and new identifier helpers do not exist.

- [ ] **Step 3: Add manifest enums, models, and path validation**

Implement:

```python
SCHEMA_VERSION = 1


class ManifestState(StrEnum):
    READY = "ready"
    FAILED = "failed"


class RenderKind(StrEnum):
    CANDIDATE = "candidate"
    CUSTOM = "custom"


class RenderState(StrEnum):
    QUEUED = "queued"
    RENDERING = "rendering"
    READY = "ready"
    FAILED = "failed"


class PublishState(StrEnum):
    NOT_UPLOADED = "not-uploaded"
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    UPLOAD_FAILED = "upload-failed"


def validate_relative_path(value: Path) -> Path:
    if value.is_absolute() or ".." in value.parts:
        raise ValueError("manifest paths must be relative and contained")
    return value
```

Define `VideoManifest`, `SourceManifest`, `TranscriptManifest`,
`AnalysisManifest`, and `RenderManifest` with `extra="forbid"`, schema version
`1`, explicit timestamps, state fields, relative paths, and structured error
fields. Add `STORAGE_CONFLICT`, `MANIFEST_INVALID`, `SOURCE_FINGERPRINT_MISMATCH`,
`TRANSCRIPT_CACHE_INVALID`, `RENDER_NOT_FOUND`, `RENDER_NOT_PUBLISHABLE`,
`ARTIFACT_PATH_INVALID`, and `INVALID_PUBLISH_STATE` to `ErrorCode`.

Add:

```python
def build_video_dir_name(video_id: str, title: str) -> str:
    return f"{validate_youtube_video_id(video_id)}_{sanitize_filename(title)}"


def build_run_id(created_at: datetime, value: str) -> str:
    return f"{created_at:%Y%m%d-%H%M%S}-{value[:6]}"
```

- [ ] **Step 4: Run tests and Ruff**

Run: `uv run pytest tests/unit/test_domain_models.py tests/unit/test_files.py -q`

Expected: PASS.

Run: `uv run ruff check src/insightcast/storage/manifests.py src/insightcast/domain/enums.py src/insightcast/utils/files.py tests/unit/test_domain_models.py tests/unit/test_files.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/insightcast/storage/manifests.py src/insightcast/domain/enums.py src/insightcast/utils/files.py tests/unit/test_domain_models.py tests/unit/test_files.py
git commit -m "feat: define video output manifests"
```

### Task 2: Implement The VideoStore Foundation

**Files:**
- Create: `src/insightcast/storage/video_store.py`
- Modify: `src/insightcast/storage/file_job_writer.py`
- Test: `tests/unit/test_video_store.py`

- [ ] **Step 1: Write failing video-root and containment tests**

```python
def test_video_store_reuses_root_by_video_id_when_title_changes(tmp_path: Path) -> None:
    store = VideoStore(tmp_path / "outputs", FileJobWriter())
    first = store.ensure_video(metadata(title="Original Title"), ORIGINAL_URL)
    second = store.ensure_video(metadata(title="Renamed Title"), SHARE_URL)
    assert second.root == first.root
    assert second.manifest.title == "Renamed Title"
    assert second.manifest.first_seen_at == first.manifest.first_seen_at


def test_video_store_rejects_duplicate_video_roots(tmp_path: Path) -> None:
    videos = tmp_path / "outputs" / "videos"
    (videos / f"{VIDEO_ID}_one").mkdir(parents=True)
    (videos / f"{VIDEO_ID}_two").mkdir()
    with pytest.raises(InsightCastError) as error:
        VideoStore(tmp_path / "outputs", FileJobWriter()).find_video(VIDEO_ID)
    assert error.value.error_code == ErrorCode.STORAGE_CONFLICT


def test_resolve_relative_rejects_escape_and_external_symlink(tmp_path: Path) -> None:
    store = VideoStore(tmp_path / "outputs", FileJobWriter())
    root = tmp_path / "outputs" / "videos" / f"{VIDEO_ID}_title"
    root.mkdir(parents=True)
    with pytest.raises(InsightCastError):
        store.resolve_relative(root, Path("../outside"))
```

- [ ] **Step 2: Run the focused tests and confirm failure**

Run: `uv run pytest tests/unit/test_video_store.py -q`

Expected: FAIL because `VideoStore` is missing.

- [ ] **Step 3: Implement canonical root discovery and atomic manifest I/O**

Implement `VideoStore` with:

```python
class VideoStore:
    def __init__(self, output_root: Path, writer: FileJobWriter) -> None:
        self.output_root = output_root.expanduser().resolve()
        self.videos_root = self.output_root / "videos"
        self.writer = writer

    def matching_video_roots(self, video_id: str) -> list[Path]:
        prefix = f"{validate_youtube_video_id(video_id)}_"
        if not self.videos_root.exists():
            return []
        return sorted(
            path.resolve()
            for path in self.videos_root.iterdir()
            if path.is_dir() and path.name.startswith(prefix)
        )

    def resolve_relative(self, owner: Path, relative: Path) -> Path:
        if relative.is_absolute() or ".." in relative.parts:
            raise self._invalid_artifact(relative)
        resolved = (owner / relative).resolve()
        owner = owner.resolve()
        if resolved != owner and owner not in resolved.parents:
            raise self._invalid_artifact(relative)
        return resolved
```

`ensure_video()` creates `<video-id>_<first-title-slug>/video.json`, never
renames the directory, updates last-seen metadata atomically, and fails on
duplicate roots. `read_manifest()` validates Pydantic type and schema version,
mapping JSON, validation, and I/O failures to `MANIFEST_INVALID`.

- [ ] **Step 4: Run tests and Ruff**

Run: `uv run pytest tests/unit/test_video_store.py -q`

Expected: PASS.

Run: `uv run ruff check src/insightcast/storage/video_store.py src/insightcast/storage/file_job_writer.py tests/unit/test_video_store.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/insightcast/storage/video_store.py src/insightcast/storage/file_job_writer.py tests/unit/test_video_store.py
git commit -m "feat: add video-centric storage root"
```

### Task 3: Move Source Reuse And Repair Into VideoStore

**Files:**
- Modify: `src/insightcast/storage/video_store.py`
- Modify: `src/insightcast/engines/source_engine.py`
- Delete: `src/insightcast/storage/source_cache.py`
- Modify: `tests/unit/test_source_engine.py`
- Delete: `tests/unit/test_source_cache.py`
- Modify: `tests/conftest.py`

- [ ] **Step 1: Write failing source fingerprint and repair tests**

```python
@pytest.mark.asyncio
async def test_source_engine_reuses_valid_video_root_source(tmp_path: Path) -> None:
    engine, ytdlp, ffmpeg, store = make_source_engine(tmp_path)
    first = await engine.ingest(**ingest_kwargs())
    second = await engine.ingest(**ingest_kwargs(url=SHARE_URL))
    assert second.cache_decision == "hit"
    assert second.source_artifacts == first.source_artifacts
    assert len(ytdlp.downloads) == 1
    assert len(ffmpeg.extractions) == 1
    manifest = store.load_source(VIDEO_ID)
    assert manifest.fingerprint == sha256(b"video").hexdigest()


@pytest.mark.asyncio
async def test_failed_source_repair_preserves_previous_valid_source(
    tmp_path: Path,
) -> None:
    engine, _, _, store = make_source_engine(tmp_path)
    first = await engine.ingest(**ingest_kwargs())
    store.source_manifest_path(VIDEO_ID).write_text("{bad json", encoding="utf-8")
    engine.ytdlp.fail_download = True
    with pytest.raises(InsightCastError):
        await engine.ingest(**ingest_kwargs())
    assert first.source_artifacts.source_video.read_bytes() == b"video"
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `uv run pytest tests/unit/test_source_engine.py -q`

Expected: FAIL because source ingestion still uses `outputs/source-cache/`.

- [ ] **Step 3: Implement source staging, hashing, validation, and promotion**

Add `VideoStore.load_source()`, `validate_source()`, `create_source_staging()`,
and `promote_source()`. Validation checks nonzero declared sizes and streams
`source.mp4` through SHA-256:

```python
def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
```

Change `SourceEngine` to receive `video_store`, fetch metadata only when creating
or repairing a video root, download into a temporary sibling source directory,
and return the video root plus source artifact paths. Preserve an existing valid
source until replacement promotion succeeds. Remove `SourceCache`.

- [ ] **Step 4: Run source and service regression tests**

Run: `uv run pytest tests/unit/test_source_engine.py tests/service/test_job_service.py -q`

Expected: PASS after updating service fixtures to inject `VideoStore`.

Run: `uv run ruff check src/insightcast/storage/video_store.py src/insightcast/engines/source_engine.py tests/unit/test_source_engine.py tests/conftest.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A src/insightcast/storage tests/conftest.py tests/unit/test_source_engine.py tests/unit/test_source_cache.py tests/service/test_job_service.py
git commit -m "feat: reuse sources within video roots"
```

### Task 4: Add Transcript Cache Keys And Reuse

**Files:**
- Modify: `src/insightcast/storage/video_store.py`
- Modify: `src/insightcast/infrastructure/transcription/base.py`
- Modify: `src/insightcast/infrastructure/transcription/openai_transcription_client.py`
- Modify: `src/insightcast/infrastructure/transcription/local_whisper_client.py`
- Modify: `src/insightcast/services/job_service.py`
- Test: `tests/unit/test_video_store.py`
- Test: `tests/unit/test_transcription.py`
- Test: `tests/service/test_job_service.py`

- [ ] **Step 1: Write failing transcript identity and reuse tests**

```python
def test_transcript_cache_key_changes_with_every_content_input() -> None:
    base = TranscriptSpec("f" * 64, "openai", "whisper-1", "en", 1)
    assert len({
        build_transcript_cache_key(base),
        build_transcript_cache_key(base.model_copy(update={"model": "whisper-2"})),
        build_transcript_cache_key(base.model_copy(update={"language": "zh"})),
        build_transcript_cache_key(base.model_copy(update={"source_fingerprint": "e" * 64})),
    }) == 4


@pytest.mark.asyncio
async def test_second_analysis_reuses_matching_transcript(tmp_path: Path) -> None:
    service, transcriber = make_service(tmp_path)
    await process_analysis(service, force_reanalyze=False)
    await process_analysis(service, force_reanalyze=True)
    assert transcriber.calls == 1
    assert len(service.video_store.list_transcripts(VIDEO_ID)) == 1
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `uv run pytest tests/unit/test_video_store.py tests/unit/test_transcription.py tests/service/test_job_service.py -q`

Expected: FAIL because transcription clients do not expose cache identity and
`JobService` always transcribes.

- [ ] **Step 3: Add transcription specs and persisted cache lookup**

Add a `TranscriptionSpec` protocol/property returning provider, model, language
`"en"`, and transcript schema version. Implement it in both clients. Build the
complete cache key from canonical JSON:

```python
payload = json.dumps(spec.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
cache_key = hashlib.sha256(payload.encode("utf-8")).hexdigest()
transcript_id = f"tx-{cache_key[:12]}"
```

Add `VideoStore.find_ready_transcript()`, `create_transcript_manifest()`, and
`write_transcript()`. In `JobService`, load a matching transcript before calling
the transcriber; otherwise transcribe and atomically persist it. Invalid entries
are skipped and a new collision-safe transcript ID is created.

- [ ] **Step 4: Run focused and regression tests**

Run: `uv run pytest tests/unit/test_video_store.py tests/unit/test_transcription.py tests/service/test_job_service.py -q`

Expected: PASS.

Run: `uv run ruff check src/insightcast/infrastructure/transcription src/insightcast/storage/video_store.py src/insightcast/services/job_service.py tests/unit/test_video_store.py tests/unit/test_transcription.py tests/service/test_job_service.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/insightcast/infrastructure/transcription src/insightcast/storage/video_store.py src/insightcast/services/job_service.py tests/unit/test_video_store.py tests/unit/test_transcription.py tests/service/test_job_service.py
git commit -m "feat: cache transcripts by source and model"
```

### Task 5: Persist Immutable Analyses And Candidate Directories

**Files:**
- Modify: `src/insightcast/domain/models.py`
- Modify: `src/insightcast/storage/video_store.py`
- Modify: `src/insightcast/services/job_service.py`
- Modify: `src/insightcast/core/logging.py`
- Test: `tests/unit/test_video_store.py`
- Test: `tests/service/test_job_service.py`

- [ ] **Step 1: Write failing analysis layout tests**

```python
@pytest.mark.asyncio
async def test_forced_analyses_are_immutable_and_write_candidate_directories(
    tmp_path: Path,
) -> None:
    service, _ = make_service(tmp_path)
    first = await process_analysis(service)
    second = await process_analysis(service, force_reanalyze=True)
    assert first.analysis_id != second.analysis_id
    for job in (first, second):
        root = Path(job.output_dir)
        assert root == service.video_store.analysis_dir(VIDEO_ID, job.analysis_id)
        assert (root / "candidates.json").is_file()
        assert (root / "candidates" / "A" / "candidate.json").is_file()
        assert (root / "manifest.json").is_file()
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `uv run pytest tests/service/test_job_service.py::test_forced_analyses_are_immutable_and_write_candidate_directories -q`

Expected: FAIL because analyses still use job directories and aggregate-only
candidate output.

- [ ] **Step 3: Create analysis manifests before curation and persist candidates**

Add `video_id`, `analysis_id`, `transcript_id`, and `manifest_path` to
`AnalysisJob`. Replace provisional job directories with an analysis operation
log staged under `WORK_DIR` until source metadata resolves the video root.

Use:

```python
analysis_id = build_run_id(job.created_at, job.job_id)
analysis_dir = video_root / "analyses" / analysis_id
candidate_dir = analysis_dir / "candidates" / candidate.candidate_id.upper()
```

Write `manifest.json` as `running`, then `candidates.json` and each
`candidate.json`, then transition atomically to `waiting-selection`. Logs move
to `video_root/logs/<job-id>.log`; the analysis manifest stores that relative
path. Failed analyses retain a `failed` manifest and structured error.

- [ ] **Step 4: Run service tests**

Run: `uv run pytest tests/service/test_job_service.py tests/unit/test_file_job_writer.py -q`

Expected: PASS.

Run: `uv run ruff check src/insightcast/domain/models.py src/insightcast/storage/video_store.py src/insightcast/services/job_service.py src/insightcast/core/logging.py tests/service/test_job_service.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/insightcast/domain/models.py src/insightcast/storage/video_store.py src/insightcast/services/job_service.py src/insightcast/core/logging.py tests/service/test_job_service.py tests/unit/test_file_job_writer.py
git commit -m "feat: persist immutable video analyses"
```

### Task 6: Write Candidate And Custom Renders With Stable Names

**Files:**
- Modify: `src/insightcast/domain/models.py`
- Modify: `src/insightcast/storage/video_store.py`
- Modify: `src/insightcast/services/job_service.py`
- Modify: `src/insightcast/engines/clip_engine.py`
- Test: `tests/unit/test_clip_engine.py`
- Test: `tests/unit/test_video_store.py`
- Test: `tests/service/test_job_service.py`

- [ ] **Step 1: Write failing candidate/custom render tests**

```python
@pytest.mark.asyncio
async def test_candidate_render_is_nested_under_original_candidate_letter(
    tmp_path: Path,
) -> None:
    service, _ = make_service(tmp_path)
    job = await process_analysis(service)
    first = await process_render(service, job, "A", force=True)
    second = await process_render(service, job, "A", force=True)
    assert first.output_dir.parent.parent.name == "A"
    assert first.output_dir != second.output_dir
    assert set(path.name for path in first.output_dir.iterdir()) == {
        "manifest.json",
        "video.mp4",
        "subtitles.zh-TW.srt",
        "subtitles.bilingual.ass",
        "youtube-metadata.json",
    }


@pytest.mark.asyncio
async def test_direct_render_uses_video_level_custom_directory(tmp_path: Path) -> None:
    service, _ = make_service(tmp_path)
    job = await process_direct_render(service)
    assert Path(job.output_dir).parent.name == "custom"
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `uv run pytest tests/unit/test_clip_engine.py tests/service/test_job_service.py -q`

Expected: FAIL because render batches and title-derived filenames still use the
job-centric layout.

- [ ] **Step 3: Implement immutable render manifests and stable artifacts**

Change `ClipEngine.render()` to receive explicit output paths or always use:

```python
srt_path = output_dir / "subtitles.zh-TW.srt"
ass_path = output_dir / "subtitles.bilingual.ass"
burned_path = output_dir / "video.mp4"
temporary_clip = work_dir / "video.unburned.mp4"
```

Candidate render path:

```python
analysis_dir / "candidates" / candidate_id / "renders" / render_id
```

Direct render path:

```python
video_root / "renders" / "custom" / render_id
```

Create render manifest state `queued`, transition to `rendering`, write metadata
as `youtube-metadata.json`, validate all required artifacts, and transition to
`ready` with publish state `not-uploaded`. On failure retain the directory,
error, and state `failed`. A non-forced reuse returns the newest existing ready
manifest without creating a new directory.

- [ ] **Step 4: Run render and service tests**

Run: `uv run pytest tests/unit/test_clip_engine.py tests/unit/test_video_store.py tests/service/test_job_service.py -q`

Expected: PASS.

Run: `uv run ruff check src/insightcast/domain/models.py src/insightcast/storage/video_store.py src/insightcast/services/job_service.py src/insightcast/engines/clip_engine.py tests/unit/test_clip_engine.py tests/unit/test_video_store.py tests/service/test_job_service.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/insightcast/domain/models.py src/insightcast/storage/video_store.py src/insightcast/services/job_service.py src/insightcast/engines/clip_engine.py tests/unit/test_clip_engine.py tests/unit/test_video_store.py tests/service/test_job_service.py
git commit -m "feat: organize candidate and custom renders"
```

### Task 7: Add Restart-Safe Video And Render Discovery APIs

**Files:**
- Create: `src/insightcast/api/routes/videos.py`
- Create: `tests/api/test_videos.py`
- Modify: `src/insightcast/api/dependencies.py`
- Modify: `src/insightcast/api/schemas.py`
- Modify: `src/insightcast/api/app.py`
- Modify: `src/insightcast/api/routes/analysis_jobs.py`
- Modify: `src/insightcast/api/routes/direct_render_jobs.py`
- Modify: `tests/api/test_analysis_jobs.py`
- Modify: `tests/api/test_direct_render_jobs.py`
- Modify: `tests/api/test_openapi.py`

- [ ] **Step 1: Write failing disk-discovery and explicit upload tests**

```python
def test_video_routes_discover_render_from_disk_after_restart(tmp_path: Path) -> None:
    seed_ready_candidate_render(tmp_path)
    fresh_store = VideoStore(tmp_path / "outputs", FileJobWriter())
    client = make_client(tmp_path, video_store=fresh_store)
    response = client.get(f"/api/v1/videos/{VIDEO_ID}/renders")
    assert response.status_code == 200
    assert response.json()["renders"][0]["candidate_id"] == "A"


def test_upload_stub_requires_explicit_publishable_render_id(tmp_path: Path) -> None:
    render_id = seed_ready_candidate_render(tmp_path)
    client = make_client(tmp_path)
    response = client.post(
        f"/api/v1/videos/{VIDEO_ID}/renders/{render_id}/youtube-uploads"
    )
    assert response.status_code == 501
    assert response.json()["error_code"] == "UPLOAD_NOT_IMPLEMENTED"
    assert response.json()["details"]["render_id"] == render_id
```

- [ ] **Step 2: Run API tests and confirm failure**

Run: `uv run pytest tests/api/test_videos.py tests/api/test_openapi.py -q`

Expected: FAIL because video-centric routes are missing.

- [ ] **Step 3: Add video-centric read and upload-stub routes**

Add:

```text
GET  /api/v1/videos/{video_id}
GET  /api/v1/videos/{video_id}/analyses
GET  /api/v1/videos/{video_id}/renders
POST /api/v1/videos/{video_id}/renders/{render_id}/youtube-uploads
```

The list response includes analysis ID, candidate ID or `custom`, render ID,
state, publish state, timestamps, manifest path, and absolute resolved artifact
paths. The upload stub resolves exactly one render from `VideoStore`, rejects
failed/missing artifacts with `RENDER_NOT_PUBLISHABLE`, and otherwise returns
`UPLOAD_NOT_IMPLEMENTED` plus explicit paths.

Remove the two job routes that implicitly choose the newest available render.
Keep analysis/direct job create/get routes and update their artifact payloads to
expose video ID, analysis/transcript/render IDs, and new absolute paths.

- [ ] **Step 4: Run all API tests**

Run: `uv run pytest tests/api -q`

Expected: PASS.

Run: `uv run ruff check src/insightcast/api tests/api`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/insightcast/api tests/api
git commit -m "feat: discover video renders from disk"
```

### Task 8: Adapt CLI Output And Source Cleanup

**Files:**
- Modify: `src/insightcast/cli/analyze.py`
- Modify: `src/insightcast/cli/cache.py`
- Modify: `tests/unit/test_analyze_cli.py`
- Modify: `tests/unit/test_cache_cli.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Write failing CLI expectations**

```python
def test_analysis_cli_prints_video_analysis_and_candidate_paths() -> None:
    output = run_successful_cli(video_centric_response())
    assert "Video root:" in output
    assert "Analysis:" in output
    assert "Transcript:" in output
    assert "Candidate A:" in output
    assert "/analyses/" in output


def test_cache_remove_deletes_only_source_and_preserves_results(tmp_path: Path) -> None:
    seed_video_with_source_analysis_and_render(tmp_path)
    assert cache_main(["--output-dir", str(tmp_path / "outputs"), "remove", VIDEO_ID]) == 0
    video_root = find_video_root(tmp_path)
    assert not (video_root / "source").exists()
    assert (video_root / "analyses").exists()
```

- [ ] **Step 2: Run CLI tests and confirm failure**

Run: `uv run pytest tests/unit/test_analyze_cli.py tests/unit/test_cache_cli.py -q`

Expected: FAIL because CLI formatting and cache cleanup still use job/source-cache
paths.

- [ ] **Step 3: Update CLI formatting and cache semantics**

`cast_analyze` prints the video root, analysis ID/directory, transcript ID/path,
candidate letter and `candidate.json`, log path, and a reminder that renders
will appear below that candidate's `renders/`.

`cast_cache list` scans managed video manifests and reports video ID, title,
source readiness, source/audio sizes, and fingerprint. `remove <video-id>`
atomically removes only `source/`; `clear --yes` removes every managed
`source/` but preserves `video.json`, transcripts, analyses, renders, and logs.
Invalid or duplicate roots produce structured errors rather than deleting data.

- [ ] **Step 4: Run CLI tests and help commands**

Run: `uv run pytest tests/unit/test_analyze_cli.py tests/unit/test_cache_cli.py -q`

Expected: PASS.

Run: `uv run cast_analyze --help`

Expected: exit 0.

Run: `uv run cast_cache --help`

Expected: exit 0.

- [ ] **Step 5: Commit**

```bash
git add src/insightcast/cli/analyze.py src/insightcast/cli/cache.py tests/unit/test_analyze_cli.py tests/unit/test_cache_cli.py pyproject.toml
git commit -m "feat: expose video output paths in cli"
```

### Task 9: Update README, Agent Guidance, And Repository Contracts

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `tests/test_repository_contract.py`

- [ ] **Step 1: Write failing documentation contract assertions**

```python
def test_readme_documents_video_centric_output_lookup() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for text in [
        "outputs/videos/",
        "<video-id>_<title-slug>",
        "analyses/<analysis-id>/candidates/A/renders/<render-id>/",
        "renders/custom/<render-id>/",
        "source fingerprint",
        "transcription provider",
        "not-uploaded",
        "舊版",
        "不會自動遷移",
        "find outputs/videos",
    ]:
        assert text in readme
```

- [ ] **Step 2: Run repository-contract tests and confirm failure**

Run: `uv run pytest tests/test_repository_contract.py -q`

Expected: FAIL because README still documents `outputs/jobs/` and
`outputs/source-cache/`.

- [ ] **Step 3: Rewrite output lifecycle documentation**

Document:

- the exact canonical tree and stable filenames
- video ID lookup independent of URL form and title changes
- source validation, SHA-256 reuse, repair, and cache cleanup
- transcript cache key inputs
- how to choose an analysis and follow `A/B/C` to render versions
- direct render lookup under `renders/custom/`
- render and publish states
- explicit render-ID upload endpoint
- restart-safe manifest discovery
- legacy directories being ignored and manually removable

Include executable examples:

```bash
find outputs/videos -name video.json -print
find "outputs/videos/${VIDEO_ID}_"* -path "*/candidates/A/renders/*/video.mp4" -print
find "outputs/videos/${VIDEO_ID}_"* -path "*/renders/custom/*/manifest.json" -print
jq '{render_id, state, publish_state, artifacts}' path/to/manifest.json
```

Update `AGENTS.md` so analysis reports include video root, analysis ID,
transcript reuse, candidate directories, and operation log.

- [ ] **Step 4: Run documentation contracts**

Run: `uv run pytest tests/test_repository_contract.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add README.md AGENTS.md tests/test_repository_contract.py
git commit -m "docs: explain video output discovery"
```

### Task 10: Add End-To-End Filesystem Lifecycle Acceptance

**Files:**
- Create: `tests/acceptance/test_video_output_lifecycle.py`
- Modify: `tests/service/test_job_service.py`

- [ ] **Step 1: Write the focused acceptance test**

```python
@pytest.mark.asyncio
async def test_video_output_lifecycle_survives_fresh_store_instance(
    tmp_path: Path,
) -> None:
    service, fakes = make_acceptance_service(tmp_path)
    first = await analyze(service, WATCH_URL)
    second = await analyze(service, SHARE_URL, force_reanalyze=True)
    render_one = await render(service, first, "A", force=True)
    render_two = await render(service, first, "A", force=True)

    assert fakes.ytdlp.download_count == 1
    assert fakes.transcriber.call_count == 1
    assert first.analysis_id != second.analysis_id
    assert render_one.render_id != render_two.render_id

    fresh = VideoStore(tmp_path / "outputs", FileJobWriter())
    renders = fresh.list_publishable_renders(VIDEO_ID)
    selected = fresh.resolve_publishable_render(VIDEO_ID, render_two.render_id)
    assert {item.render_id for item in renders} >= {
        render_one.render_id,
        render_two.render_id,
    }
    assert selected.candidate_id == "A"
    assert selected.video_path.name == "video.mp4"
```

- [ ] **Step 2: Run the acceptance test and fix only contract integration gaps**

Run: `uv run pytest tests/acceptance/test_video_output_lifecycle.py -q`

Expected: PASS. If it fails, update only the owning storage/service boundary;
do not add test-only path logic.

- [ ] **Step 3: Verify legacy directories are ignored**

Extend the acceptance fixture with:

```python
legacy = tmp_path / "outputs" / "20260606-legacy-job"
legacy.mkdir(parents=True)
(legacy / "job_state.json").write_text("{}", encoding="utf-8")
assert fresh.list_analyses(VIDEO_ID)
assert legacy.exists()
```

Run: `uv run pytest tests/acceptance/test_video_output_lifecycle.py -q`

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/acceptance/test_video_output_lifecycle.py tests/service/test_job_service.py
git commit -m "test: cover video output lifecycle"
```

### Task 11: Remove Legacy Assumptions And Run Full Verification

**Files:**
- Verify: entire repository

- [ ] **Step 1: Search for stale managed-layout references**

Run:

```bash
rg -n 'outputs/jobs|outputs/source-cache|/source-cache/|candidate-a|bilingual\.burned|analysis-jobs/.*/youtube-uploads|direct-render-jobs/.*/youtube-uploads' src tests README.md AGENTS.md
```

Expected: no runtime or current documentation references to the old managed
layout. Historical design/plan documents are intentionally unchanged.

- [ ] **Step 2: Run the full test suite**

Run: `uv run pytest -q`

Expected: PASS.

- [ ] **Step 3: Run Ruff**

Run: `uv run ruff check .`

Expected: PASS.

- [ ] **Step 4: Verify CLI and OpenAPI surfaces**

Run: `uv run cast_analyze --help`

Expected: exit 0.

Run: `uv run cast_cache list`

Expected: exit 0 and only managed `outputs/videos/` entries are listed.

Run:

```bash
OPENAI_API_KEY=sk-plan-verification uv run python -c "from insightcast.api.app import create_app; print(sorted(create_app().openapi()['paths']))"
```

Expected: output includes video analysis/render discovery and explicit
render-ID upload paths.

- [ ] **Step 5: Inspect the final diff**

Run: `git status --short`

Expected: only intended source, tests, README, and AGENTS changes.

Run: `git diff --check`

Expected: no whitespace errors.

- [ ] **Step 6: Confirm every implementation task was committed**

Run: `git status --short`

Expected: empty output. If verification required a scoped fix, rerun the owning
task's focused test, commit that exact fix with its affected files, then repeat
the full verification commands.
