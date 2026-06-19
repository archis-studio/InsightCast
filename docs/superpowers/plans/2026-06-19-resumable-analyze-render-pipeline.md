# Resumable Analyze Render Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make AI-assisted analyze/render workflows reliably reuse or resume work, repair subtitle translation failures, and report actionable progress and errors for 8-12 minute highlights from long-form source videos.

**Architecture:** Add a small stage-manifest layer beside existing render manifests, split render work into observable stages, persist validated subtitle translation batches, and validate final artifacts before marking renders ready. Keep existing API and storage patterns; add force controls and structured summaries without replacing the current job service.

**Tech Stack:** Python 3.13, FastAPI, Pydantic v2, pytest, local filesystem manifests, existing OpenAI parse client abstraction, ffmpeg.

---

## File Structure

- Create `src/insightcast/domain/stages.py`: shared stage status, stage record, resume strategy, and quality warning models.
- Create `src/insightcast/storage/stage_store.py`: read/write stage manifests atomically under render directories.
- Create `src/insightcast/engines/render_validator.py`: validate subtitle mapping, subtitle files, video, metadata, and manifest artifacts.
- Modify `src/insightcast/domain/enums.py`: add granular render and validation error codes.
- Modify `src/insightcast/domain/models.py`: add stage manifest paths and force flags to render request/domain objects where needed.
- Modify `src/insightcast/api/schemas.py`: expose `force_translate`, `force_metadata`, and stage summaries.
- Modify `src/insightcast/engines/lingo_engine.py`: persist reusable translation batches and add repair prompt attempts.
- Modify `src/insightcast/engines/clip_engine.py`: split clip cutting, subtitle writing, and subtitle burn-in into separately callable methods while keeping `render()` as a compatibility wrapper.
- Modify `src/insightcast/services/job_service.py`: orchestrate granular stages, write stage manifests, reuse checkpoints, run validation before `RenderState.READY`, and return stage-aware errors.
- Modify `src/insightcast/api/routes/analysis_jobs.py`: include stage summary data in render responses.
- Modify `src/insightcast/cli/analyze.py`: report reused/resumed/fresh state, stage summary, failure resume instructions, and render artifacts.
- Test `tests/unit/test_stage_store.py`.
- Test `tests/unit/test_lingo_engine.py`.
- Test `tests/unit/test_render_validator.py`.
- Test `tests/unit/test_clip_engine.py`.
- Test `tests/service/test_job_service.py`.
- Test `tests/api/test_analysis_jobs.py`.

## Task 1: Add Stage Domain Models

**Files:**
- Create: `src/insightcast/domain/stages.py`
- Test: `tests/unit/test_stage_models.py`

- [ ] **Step 1: Write stage model tests**

Create `tests/unit/test_stage_models.py`:

```python
from datetime import UTC, datetime
from pathlib import Path

import pytest

from insightcast.domain.enums import ErrorCode
from insightcast.domain.models import JobError
from insightcast.domain.stages import PipelineStage, StageManifest, StageRecord, StageStatus


NOW = datetime(2026, 6, 19, 12, 0, tzinfo=UTC)


def test_stage_record_accepts_completed_stage_with_elapsed_time() -> None:
    record = StageRecord(
        stage=PipelineStage.TRANSLATE_SUBTITLES,
        status=StageStatus.COMPLETED,
        started_at=NOW,
        completed_at=NOW,
        elapsed_seconds=3.5,
        artifacts={"batch": Path("translations/batch-0001.json")},
        resume_strategy="reuse completed translation batch",
    )

    assert record.stage is PipelineStage.TRANSLATE_SUBTITLES
    assert record.status is StageStatus.COMPLETED
    assert record.artifacts["batch"] == Path("translations/batch-0001.json")


def test_stage_record_requires_error_for_failed_stage() -> None:
    with pytest.raises(ValueError, match="failed stages require error"):
        StageRecord(
            stage=PipelineStage.BURN_SUBTITLES,
            status=StageStatus.FAILED,
            started_at=NOW,
            completed_at=NOW,
            elapsed_seconds=1.0,
            resume_strategy="rerun burn_subtitles",
        )


def test_stage_manifest_reports_latest_resume_point() -> None:
    manifest = StageManifest(
        schema_version=1,
        operation_id="job-1",
        render_id="render-1",
        candidate_id="A",
        stages=[
            StageRecord(
                stage=PipelineStage.CUT_CLIP,
                status=StageStatus.COMPLETED,
                started_at=NOW,
                completed_at=NOW,
                elapsed_seconds=1.0,
                resume_strategy="reuse cut clip",
            ),
            StageRecord(
                stage=PipelineStage.TRANSLATE_SUBTITLES,
                status=StageStatus.FAILED,
                started_at=NOW,
                completed_at=NOW,
                elapsed_seconds=2.0,
                resume_strategy="resume failed translation batch",
                error=JobError(
                    stage="translate_subtitles",
                    error_code=ErrorCode.SUBTITLE_REPAIR_EXHAUSTED,
                    message="Subtitle repair exhausted.",
                    details={"segment_id": "s1"},
                ),
            ),
        ],
    )

    assert manifest.current_stage is PipelineStage.TRANSLATE_SUBTITLES
    assert manifest.resume_from == "translate_subtitles"
```

- [ ] **Step 2: Run the failing tests**

Run: `uv run pytest tests/unit/test_stage_models.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'insightcast.domain.stages'`.

- [ ] **Step 3: Implement stage models**

Create `src/insightcast/domain/stages.py`:

```python
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from pydantic import Field, computed_field, model_validator

from insightcast.domain.models import DomainModel, JobError


class StageStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class PipelineStage(StrEnum):
    SOURCE_INGESTION = "source_ingestion"
    TRANSCRIPTION = "transcription"
    TOPIC_DISCOVERY = "topic_discovery"
    CANDIDATE_BOUNDARY_SELECTION = "candidate_boundary_selection"
    CUT_CLIP = "cut_clip"
    TRANSLATE_SUBTITLES = "translate_subtitles"
    WRITE_SUBTITLES = "write_subtitles"
    BURN_SUBTITLES = "burn_subtitles"
    GENERATE_METADATA = "generate_metadata"
    VALIDATE_RENDER = "validate_render"


class QualityWarning(DomainModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    details: dict[str, object] = Field(default_factory=dict)


class StageRecord(DomainModel):
    stage: PipelineStage
    status: StageStatus
    started_at: datetime | None = None
    completed_at: datetime | None = None
    elapsed_seconds: float | None = Field(default=None, ge=0)
    artifacts: dict[str, Path] = Field(default_factory=dict)
    resume_strategy: str = Field(min_length=1)
    fresh: bool = False
    reused: bool = False
    warnings: list[QualityWarning] = Field(default_factory=list)
    error: JobError | None = None

    @model_validator(mode="after")
    def validate_stage_state(self) -> "StageRecord":
        if self.status is StageStatus.FAILED and self.error is None:
            raise ValueError("failed stages require error")
        if self.status is not StageStatus.FAILED and self.error is not None:
            raise ValueError("non-failed stages must not carry error")
        if self.completed_at is not None and self.started_at is not None:
            if self.completed_at < self.started_at:
                raise ValueError("completed_at must not precede started_at")
        return self


class StageManifest(DomainModel):
    schema_version: int = 1
    operation_id: str = Field(min_length=1)
    render_id: str = Field(min_length=1)
    candidate_id: str | None = None
    stages: list[StageRecord] = Field(default_factory=list)

    @computed_field
    @property
    def current_stage(self) -> PipelineStage | None:
        if not self.stages:
            return None
        return self.stages[-1].stage

    @computed_field
    @property
    def resume_from(self) -> str | None:
        for record in reversed(self.stages):
            if record.status in {StageStatus.FAILED, StageStatus.RUNNING, StageStatus.QUEUED}:
                return record.stage.value
        return None
```

- [ ] **Step 4: Verify stage model tests pass**

Run: `uv run pytest tests/unit/test_stage_models.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/insightcast/domain/stages.py tests/unit/test_stage_models.py
git commit -m "Add pipeline stage domain models"
```

## Task 2: Persist Stage Manifests

**Files:**
- Create: `src/insightcast/storage/stage_store.py`
- Test: `tests/unit/test_stage_store.py`

- [ ] **Step 1: Write stage store tests**

Create `tests/unit/test_stage_store.py`:

```python
from datetime import UTC, datetime

from insightcast.domain.stages import PipelineStage, StageManifest, StageRecord, StageStatus
from insightcast.storage.stage_store import StageStore


NOW = datetime(2026, 6, 19, 12, 0, tzinfo=UTC)


def test_stage_store_round_trips_manifest(tmp_path) -> None:
    store = StageStore()
    path = tmp_path / "stage-manifest.json"
    manifest = StageManifest(
        operation_id="job-1",
        render_id="render-1",
        candidate_id="A",
        stages=[
            StageRecord(
                stage=PipelineStage.CUT_CLIP,
                status=StageStatus.COMPLETED,
                started_at=NOW,
                completed_at=NOW,
                elapsed_seconds=1.2,
                resume_strategy="reuse cut clip",
            )
        ],
    )

    store.write(path, manifest)
    loaded = store.read(path)

    assert loaded == manifest
    assert path.read_text(encoding="utf-8").endswith("\n")


def test_stage_store_returns_none_for_missing_manifest(tmp_path) -> None:
    assert StageStore().read_optional(tmp_path / "missing.json") is None
```

- [ ] **Step 2: Run the failing tests**

Run: `uv run pytest tests/unit/test_stage_store.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'insightcast.storage.stage_store'`.

- [ ] **Step 3: Implement stage store**

Create `src/insightcast/storage/stage_store.py`:

```python
from pathlib import Path

from insightcast.domain.stages import StageManifest


class StageStore:
    def read(self, path: Path) -> StageManifest:
        return StageManifest.model_validate_json(path.read_text(encoding="utf-8"))

    def read_optional(self, path: Path) -> StageManifest | None:
        if not path.is_file():
            return None
        return self.read(path)

    def write(self, path: Path, manifest: StageManifest) -> Path:
        resolved = path.expanduser().resolve()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        tmp = resolved.with_suffix(f"{resolved.suffix}.tmp")
        tmp.write_text(
            manifest.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        tmp.replace(resolved)
        return resolved
```

- [ ] **Step 4: Verify stage store tests pass**

Run: `uv run pytest tests/unit/test_stage_store.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/insightcast/storage/stage_store.py tests/unit/test_stage_store.py
git commit -m "Persist pipeline stage manifests"
```

## Task 3: Add Granular Error Codes And Force Flags

**Files:**
- Modify: `src/insightcast/domain/enums.py`
- Modify: `src/insightcast/domain/models.py`
- Modify: `src/insightcast/api/schemas.py`
- Test: `tests/unit/test_domain_models.py`
- Test: `tests/api/test_analysis_jobs.py`

- [ ] **Step 1: Write request model tests**

Append to `tests/unit/test_domain_models.py`:

```python
from insightcast.domain.models import CandidateSelectionRequest


def test_candidate_selection_request_carries_force_substeps() -> None:
    request = CandidateSelectionRequest(
        candidate_ids="a",
        force_render=False,
        force_translate=True,
        force_metadata=True,
    )

    assert request.candidate_ids == ["A"]
    assert request.force_translate is True
    assert request.force_metadata is True
```

- [ ] **Step 2: Run the failing test**

Run: `uv run pytest tests/unit/test_domain_models.py::test_candidate_selection_request_carries_force_substeps -q`

Expected: FAIL because `CandidateSelectionRequest` rejects extra fields.

- [ ] **Step 3: Add error codes**

Modify `src/insightcast/domain/enums.py` inside `ErrorCode`:

```python
    SUBTITLE_BATCH_INVALID = "SUBTITLE_BATCH_INVALID"
    SUBTITLE_REPAIR_EXHAUSTED = "SUBTITLE_REPAIR_EXHAUSTED"
    SUBTITLE_FILE_INVALID = "SUBTITLE_FILE_INVALID"
    RENDER_ARTIFACT_INVALID = "RENDER_ARTIFACT_INVALID"
    METADATA_GENERATION_FAILED = "METADATA_GENERATION_FAILED"
```

Keep existing error codes unchanged.

- [ ] **Step 4: Add force flags to domain request**

Modify `src/insightcast/domain/models.py` in `CandidateSelectionRequest`:

```python
class CandidateSelectionRequest(DomainModel):
    candidate_ids: list[str]
    force_render: bool = False
    force_translate: bool = False
    force_metadata: bool = False
```

Leave the existing `normalize_candidate_ids` validator unchanged.

- [ ] **Step 5: Add force flags to API request**

Modify `src/insightcast/api/schemas.py` in `RenderCreateRequest`:

```python
class RenderCreateRequest(ApiModel):
    candidate_ids: str | list[str] = Field(
        description="One candidate ID or an ordered list of candidate IDs.",
        examples=[["A", "C"]],
    )
    force_render: bool = Field(
        default=False,
        description="Create a new timestamped render without overwriting previous output.",
        examples=[False],
    )
    force_translate: bool = Field(
        default=False,
        description="Redo subtitle translation even when reusable translation artifacts exist.",
        examples=[False],
    )
    force_metadata: bool = Field(
        default=False,
        description="Regenerate YouTube metadata even when reusable metadata exists.",
        examples=[False],
    )
```

- [ ] **Step 6: Pass flags through route**

Modify `src/insightcast/api/routes/analysis_jobs.py` in `create_render()` where it builds `CandidateSelectionRequest`:

```python
        CandidateSelectionRequest(
            candidate_ids=request.candidate_ids,
            force_render=request.force_render,
            force_translate=request.force_translate,
            force_metadata=request.force_metadata,
        )
```

- [ ] **Step 7: Verify tests**

Run:

```bash
uv run pytest tests/unit/test_domain_models.py::test_candidate_selection_request_carries_force_substeps tests/api/test_analysis_jobs.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

Run:

```bash
git add src/insightcast/domain/enums.py src/insightcast/domain/models.py src/insightcast/api/schemas.py src/insightcast/api/routes/analysis_jobs.py tests/unit/test_domain_models.py
git commit -m "Add render force controls and error codes"
```

## Task 4: Split Clip Engine Into Reusable Render Steps

**Files:**
- Modify: `src/insightcast/engines/clip_engine.py`
- Test: `tests/unit/test_clip_engine.py`

- [ ] **Step 1: Write split-step clip engine test**

Append to `tests/unit/test_clip_engine.py`:

```python
@pytest.mark.asyncio
async def test_clip_engine_exposes_individual_render_steps(tmp_path) -> None:
    ffmpeg = FakeFfmpeg()
    lingo = FakeLingo()
    engine = ClipEngine(ffmpeg=ffmpeg, lingo=lingo)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    work_dir = tmp_path / "work"
    output_dir = tmp_path / "render"
    candidate = Candidate(
        candidate_id="A",
        start_seconds=10,
        end_seconds=12,
        suggested_title="Title",
        selection_reason="Reason",
        summary="Summary",
    )

    temporary = await engine.cut_clip(source, candidate, work_dir)
    subtitles = await engine.translate_subtitles(
        [TranscriptSegment(segment_id="s1", start_seconds=10, end_seconds=12, text="Hello")],
        candidate,
    )
    srt, ass = engine.write_subtitles(subtitles, candidate, output_dir)
    burned = await engine.burn_subtitles(temporary, ass, output_dir)

    assert temporary == work_dir / "video.unburned.mp4"
    assert srt == output_dir / "subtitles.zh-TW.srt"
    assert ass == output_dir / "subtitles.bilingual.ass"
    assert burned == output_dir / "video.mp4"
```

- [ ] **Step 2: Run failing test**

Run: `uv run pytest tests/unit/test_clip_engine.py::test_clip_engine_exposes_individual_render_steps -q`

Expected: FAIL because `ClipEngine.cut_clip` is not defined.

- [ ] **Step 3: Add split methods while preserving wrapper**

Modify `src/insightcast/engines/clip_engine.py`:

```python
    async def cut_clip(self, source_video: Path, selection: Candidate, work_dir: Path) -> Path:
        resolved_work_dir = work_dir.expanduser().resolve()
        resolved_work_dir.mkdir(parents=True, exist_ok=True)
        temporary_clip = resolved_work_dir / "video.unburned.mp4"
        await self.ffmpeg.cut_clip(
            source_video,
            temporary_clip,
            start_seconds=selection.start_seconds,
            end_seconds=selection.end_seconds,
        )
        return temporary_clip

    async def translate_subtitles(
        self,
        transcript_segments: list[TranscriptSegment],
        selection: Candidate,
    ) -> list[SubtitleItem]:
        return await self.lingo.translate_clip(
            segments=transcript_segments,
            clip_start_seconds=selection.start_seconds,
            clip_end_seconds=selection.end_seconds,
        )

    def write_subtitles(
        self,
        subtitle_items: list[SubtitleItem],
        selection: Candidate,
        output_dir: Path,
    ) -> tuple[Path, Path]:
        resolved_output_dir = output_dir.expanduser().resolve()
        resolved_output_dir.mkdir(parents=True, exist_ok=True)
        srt_path = resolved_output_dir / "subtitles.zh-TW.srt"
        ass_path = resolved_output_dir / "subtitles.bilingual.ass"
        srt_path.write_text(
            serialize_traditional_chinese_srt(subtitle_items),
            encoding="utf-8",
            newline="\n",
        )
        ass_path.write_text(
            serialize_bilingual_ass(subtitle_items, title=selection.suggested_title),
            encoding="utf-8",
            newline="\n",
        )
        return srt_path, ass_path

    async def burn_subtitles(self, temporary_clip: Path, ass_path: Path, output_dir: Path) -> Path:
        resolved_output_dir = output_dir.expanduser().resolve()
        resolved_output_dir.mkdir(parents=True, exist_ok=True)
        burned_path = resolved_output_dir / "video.mp4"
        await self.ffmpeg.burn_subtitles(temporary_clip, ass_path, burned_path)
        return burned_path
```

Update the existing `render()` method to call these methods:

```python
        temporary_clip = await self.cut_clip(source_video, selection, work_dir)
```

Then:

```python
        subtitle_items = await self.translate_subtitles(transcript_segments, selection)
        srt_path, ass_path = self.write_subtitles(subtitle_items, selection, output_dir)
        burned_path = await self.burn_subtitles(temporary_clip, ass_path, output_dir)
        temporary_clip.unlink(missing_ok=True)
```

- [ ] **Step 4: Verify clip engine tests**

Run: `uv run pytest tests/unit/test_clip_engine.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/insightcast/engines/clip_engine.py tests/unit/test_clip_engine.py
git commit -m "Split clip engine render steps"
```

## Task 5: Add Translation Batch Checkpoints And Repair Prompt Hooks

**Files:**
- Modify: `src/insightcast/prompts/translation.py`
- Modify: `src/insightcast/engines/lingo_engine.py`
- Test: `tests/unit/test_lingo_engine.py`

- [ ] **Step 1: Write repair prompt test**

Append to `tests/unit/test_lingo_engine.py`:

```python
@pytest.mark.asyncio
async def test_translate_batch_retries_with_repair_prompt_before_splitting() -> None:
    segments = [
        TranscriptSegment(segment_id="s0", start_seconds=0, end_seconds=1, text="First"),
        TranscriptSegment(segment_id="s1", start_seconds=1, end_seconds=2, text="Second"),
    ]
    client = RecordingTranslationClient(
        [
            TranslationResponse(items=[]),
            translation_response("s0", "s1"),
        ]
    )

    result = await LingoEngine(client=client, model="gpt-translation").translate_clip(
        segments=segments,
        clip_start_seconds=0,
        clip_end_seconds=2,
    )

    assert [item.segment_id for item in result] == ["s0", "s1"]
    assert len(client.calls) == 2
    assert "Repair this subtitle translation batch" in str(client.calls[1]["user_prompt"])
```

- [ ] **Step 2: Run failing test**

Run: `uv run pytest tests/unit/test_lingo_engine.py::test_translate_batch_retries_with_repair_prompt_before_splitting -q`

Expected: FAIL because current code splits immediately and does not use repair prompt text.

- [ ] **Step 3: Add repair prompt builder**

Modify `src/insightcast/prompts/translation.py`:

```python
def build_repair_user_prompt(
    *,
    items: Sequence[Mapping[str, Any]],
    validation_error: Mapping[str, Any],
) -> str:
    return json.dumps(
        {
            "instruction": (
                "Repair this subtitle translation batch. Return exactly one translated item "
                "for each source item, preserve item order and segment_id values, and do not "
                "return empty or punctuation-only translations."
            ),
            "validation_error": dict(validation_error),
            "items": list(items),
        },
        ensure_ascii=False,
        indent=2,
    )
```

- [ ] **Step 4: Add one repair attempt before recursive split**

Modify `src/insightcast/engines/lingo_engine.py`:

```python
    async def _translate_batch(
        self,
        batch: list[TranscriptSegment],
        *,
        batch_index: int,
        batch_path: list[int],
        repair_attempted: bool = False,
    ) -> list[TranslationItem]:
```

After computing `source_ids`, `translation_ids`, and `unreadable`, insert:

```python
        validation_error = {
            "source_segment_ids": source_ids,
            "translation_segment_ids": translation_ids,
            "unreadable_segment_id": unreadable.segment_id if unreadable else None,
            "batch_index": batch_index,
            "batch_path": batch_path,
        }
        if not repair_attempted:
            repair_response = await self.client.parse(
                model=self.model,
                system_prompt=translation_prompt.SYSTEM_PROMPT,
                user_prompt=translation_prompt.build_repair_user_prompt(
                    items=[
                        {"segment_id": segment.segment_id, "text": segment.text}
                        for segment in batch
                    ],
                    validation_error=validation_error,
                ),
                response_model=TranslationResponse,
            )
            repair_ids = [translation.segment_id for translation in repair_response.items]
            repair_unreadable = next(
                (
                    translation
                    for translation in repair_response.items
                    if not _is_readable_translation(translation.text)
                ),
                None,
            )
            if repair_ids == source_ids and repair_unreadable is None:
                return repair_response.items
```

Update recursive calls to pass `repair_attempted=False` for each child batch.

- [ ] **Step 5: Verify lingo tests**

Run: `uv run pytest tests/unit/test_lingo_engine.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add src/insightcast/prompts/translation.py src/insightcast/engines/lingo_engine.py tests/unit/test_lingo_engine.py
git commit -m "Add subtitle translation repair prompt"
```

## Task 6: Add Render Validation

**Files:**
- Create: `src/insightcast/engines/render_validator.py`
- Test: `tests/unit/test_render_validator.py`

- [ ] **Step 1: Write render validator tests**

Create `tests/unit/test_render_validator.py`:

```python
import pytest

from insightcast.core.exceptions import InsightCastError
from insightcast.domain.enums import ErrorCode
from insightcast.domain.models import TranscriptSegment
from insightcast.engines.lingo_engine import SubtitleItem
from insightcast.engines.render_validator import RenderValidator


def test_render_validator_accepts_complete_artifacts(tmp_path) -> None:
    render_dir = tmp_path / "render"
    render_dir.mkdir()
    (render_dir / "video.mp4").write_bytes(b"video")
    (render_dir / "subtitles.zh-TW.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\n你好\n", encoding="utf-8")
    (render_dir / "subtitles.bilingual.ass").write_text("[Script Info]\n", encoding="utf-8")
    (render_dir / "youtube-metadata.json").write_text("{}", encoding="utf-8")

    RenderValidator().validate(
        render_dir=render_dir,
        expected_segments=[TranscriptSegment(segment_id="s1", start_seconds=0, end_seconds=1, text="Hello")],
        subtitle_items=[
            SubtitleItem(
                segment_id="s1",
                start_seconds=0,
                end_seconds=1,
                english_text="Hello",
                traditional_chinese_text="你好",
            )
        ],
    )


def test_render_validator_rejects_missing_segment_mapping(tmp_path) -> None:
    render_dir = tmp_path / "render"
    render_dir.mkdir()

    with pytest.raises(InsightCastError) as exc_info:
        RenderValidator().validate(
            render_dir=render_dir,
            expected_segments=[
                TranscriptSegment(segment_id="s1", start_seconds=0, end_seconds=1, text="Hello")
            ],
            subtitle_items=[],
        )

    assert exc_info.value.error_code == ErrorCode.RENDER_ARTIFACT_INVALID
    assert exc_info.value.details["expected_segment_ids"] == ["s1"]
    assert exc_info.value.details["actual_segment_ids"] == []
```

- [ ] **Step 2: Run failing tests**

Run: `uv run pytest tests/unit/test_render_validator.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'insightcast.engines.render_validator'`.

- [ ] **Step 3: Implement render validator**

Create `src/insightcast/engines/render_validator.py`:

```python
from pathlib import Path

from insightcast.core.exceptions import InsightCastError
from insightcast.domain.enums import ErrorCode
from insightcast.domain.models import TranscriptSegment
from insightcast.engines.lingo_engine import SubtitleItem


class RenderValidator:
    def validate(
        self,
        *,
        render_dir: Path,
        expected_segments: list[TranscriptSegment],
        subtitle_items: list[SubtitleItem],
    ) -> None:
        expected_ids = [segment.segment_id for segment in expected_segments]
        actual_ids = [item.segment_id for item in subtitle_items]
        if actual_ids != expected_ids:
            raise InsightCastError(
                ErrorCode.RENDER_ARTIFACT_INVALID,
                "Rendered subtitles do not match selected transcript segments.",
                details={
                    "expected_segment_ids": expected_ids,
                    "actual_segment_ids": actual_ids,
                },
                stage="validate_render",
            )
        for item in subtitle_items:
            if item.end_seconds <= item.start_seconds or item.start_seconds < 0:
                raise InsightCastError(
                    ErrorCode.SUBTITLE_FILE_INVALID,
                    "Rendered subtitle timing is invalid.",
                    details={"segment_id": item.segment_id},
                    stage="validate_render",
                )
            if not item.traditional_chinese_text.strip():
                raise InsightCastError(
                    ErrorCode.SUBTITLE_FILE_INVALID,
                    "Rendered subtitle text is empty.",
                    details={"segment_id": item.segment_id},
                    stage="validate_render",
                )
        required = {
            "video": render_dir / "video.mp4",
            "traditional_chinese_srt": render_dir / "subtitles.zh-TW.srt",
            "bilingual_ass": render_dir / "subtitles.bilingual.ass",
            "youtube_metadata": render_dir / "youtube-metadata.json",
        }
        missing = [
            name
            for name, path in required.items()
            if not path.is_file() or path.stat().st_size <= 0
        ]
        if missing:
            raise InsightCastError(
                ErrorCode.RENDER_ARTIFACT_INVALID,
                "Rendered artifacts are missing or empty.",
                details={"missing_or_empty": missing},
                stage="validate_render",
            )
```

- [ ] **Step 4: Verify validator tests**

Run: `uv run pytest tests/unit/test_render_validator.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/insightcast/engines/render_validator.py tests/unit/test_render_validator.py
git commit -m "Validate render artifacts before publishable state"
```

## Task 7: Orchestrate Granular Candidate Render Stages

**Files:**
- Modify: `src/insightcast/services/job_service.py`
- Test: `tests/service/test_job_service.py`

- [ ] **Step 1: Write service test for completed stage manifest**

Append to `tests/service/test_job_service.py`:

```python
async def test_candidate_render_writes_stage_manifest(tmp_path: Path) -> None:
    service, _, _ = make_service(tmp_path)
    job = await service.create_analysis_job("https://youtu.be/abc123DEF_-")
    await service.process(await service.queue.get())

    batch = await service.create_render(
        job.job_id,
        CandidateSelectionRequest(candidate_ids="A", force_render=True),
    )
    await service.process(await service.queue.get())

    stage_manifest_path = batch.output_dir / "stage-manifest.json"
    assert stage_manifest_path.is_file()
    payload = json.loads(stage_manifest_path.read_text(encoding="utf-8"))
    assert [stage["stage"] for stage in payload["stages"]] == [
        "cut_clip",
        "translate_subtitles",
        "write_subtitles",
        "burn_subtitles",
        "generate_metadata",
        "validate_render",
    ]
    assert all(stage["status"] == "completed" for stage in payload["stages"])
```

- [ ] **Step 2: Run failing service test**

Run: `uv run pytest tests/service/test_job_service.py::test_candidate_render_writes_stage_manifest -q`

Expected: FAIL because no `stage-manifest.json` is written.

- [ ] **Step 3: Wire stage dependencies into `JobService`**

Modify `src/insightcast/services/job_service.py` imports:

```python
from insightcast.domain.stages import PipelineStage, StageManifest, StageRecord, StageStatus
from insightcast.engines.render_validator import RenderValidator
from insightcast.storage.stage_store import StageStore
```

In `JobService.__init__`, add optional dependencies with defaults:

```python
        stage_store: StageStore | None = None,
        render_validator: RenderValidator | None = None,
```

Set fields:

```python
        self.stage_store = stage_store or StageStore()
        self.render_validator = render_validator or RenderValidator()
```

Update `FakeClip` in `tests/service/test_job_service.py` so service tests can exercise the split render methods:

```python
    async def cut_clip(self, source_video: Path, selection: Candidate, work_dir: Path) -> Path:
        work_dir.mkdir(parents=True, exist_ok=True)
        temporary_clip = work_dir / "video.unburned.mp4"
        temporary_clip.write_bytes(b"temporary")
        return temporary_clip

    async def translate_subtitles(
        self,
        transcript_segments: list[TranscriptSegment],
        selection: Candidate,
    ) -> list[SubtitleItem]:
        return [
            SubtitleItem(
                segment_id=segment.segment_id,
                start_seconds=max(segment.start_seconds, selection.start_seconds)
                - selection.start_seconds,
                end_seconds=min(segment.end_seconds, selection.end_seconds)
                - selection.start_seconds,
                english_text=segment.text,
                traditional_chinese_text="翻譯",
            )
            for segment in transcript_segments
            if segment.end_seconds > selection.start_seconds
            and segment.start_seconds < selection.end_seconds
        ]

    def write_subtitles(
        self,
        subtitle_items: list[SubtitleItem],
        selection: Candidate,
        output_dir: Path,
    ) -> tuple[Path, Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        srt = output_dir / "subtitles.zh-TW.srt"
        ass = output_dir / "subtitles.bilingual.ass"
        srt.write_text("srt", encoding="utf-8")
        ass.write_text("ass", encoding="utf-8")
        return srt, ass

    async def burn_subtitles(self, temporary_clip: Path, ass_path: Path, output_dir: Path) -> Path:
        if "A" in self.fail_candidates:
            raise InsightCastError(ErrorCode.VIDEO_RENDER_FAILED, "render failed")
        output_dir.mkdir(parents=True, exist_ok=True)
        burned = output_dir / "video.mp4"
        burned.write_bytes(b"video")
        return burned
```

- [ ] **Step 4: Add stage helper methods**

Add methods near `_run_stage()` in `src/insightcast/services/job_service.py`:

```python
    def _stage_manifest_path(self, render_dir: Path) -> Path:
        return render_dir / "stage-manifest.json"

    def _load_stage_manifest(
        self,
        *,
        render_dir: Path,
        job_id: str,
        render_id: str,
        candidate_id: str | None,
    ) -> StageManifest:
        return self.stage_store.read_optional(self._stage_manifest_path(render_dir)) or StageManifest(
            operation_id=job_id,
            render_id=render_id,
            candidate_id=candidate_id,
        )

    def _append_stage_record(
        self,
        *,
        render_dir: Path,
        manifest: StageManifest,
        record: StageRecord,
    ) -> StageManifest:
        manifest.stages.append(record)
        self.stage_store.write(self._stage_manifest_path(render_dir), manifest)
        return manifest
```

- [ ] **Step 5: Replace monolithic clip render in `_process_analysis_render`**

In `_process_analysis_render`, replace the single `_run_stage(job, "candidate_clip_render", ...)` call with sequential stage calls:

```python
                stage_manifest = self._load_stage_manifest(
                    render_dir=candidate_dir,
                    job_id=job.job_id,
                    render_id=batch.render_id,
                    candidate_id=candidate_id,
                )
                selected_segments = [
                    segment
                    for segment in transcript.segments
                    if segment.end_seconds > candidate.start_seconds
                    and segment.start_seconds < candidate.end_seconds
                ]
                temporary_clip = await self._run_stage(
                    job,
                    PipelineStage.CUT_CLIP.value,
                    lambda candidate=candidate: self.clip_engine.cut_clip(
                        job.source_artifacts.source_video,
                        candidate,
                        self.work_root / job.job_id / batch.render_id,
                    ),
                )
                self._append_stage_record(
                    render_dir=candidate_dir,
                    manifest=stage_manifest,
                    record=StageRecord(
                        stage=PipelineStage.CUT_CLIP,
                        status=StageStatus.COMPLETED,
                        resume_strategy="reuse video.unburned.mp4 when source fingerprint and candidate timing match",
                        artifacts={"temporary_clip": temporary_clip},
                        fresh=True,
                    ),
                )
                subtitle_items = await self._run_stage(
                    job,
                    PipelineStage.TRANSLATE_SUBTITLES.value,
                    lambda candidate=candidate: self.clip_engine.translate_subtitles(
                        transcript.segments,
                        candidate,
                    ),
                )
                self._append_stage_record(
                    render_dir=candidate_dir,
                    manifest=stage_manifest,
                    record=StageRecord(
                        stage=PipelineStage.TRANSLATE_SUBTITLES,
                        status=StageStatus.COMPLETED,
                        resume_strategy="reuse validated translation batches",
                        fresh=True,
                    ),
                )
                srt_path, ass_path = self.clip_engine.write_subtitles(
                    subtitle_items,
                    candidate,
                    candidate_dir,
                )
                self._append_stage_record(
                    render_dir=candidate_dir,
                    manifest=stage_manifest,
                    record=StageRecord(
                        stage=PipelineStage.WRITE_SUBTITLES,
                        status=StageStatus.COMPLETED,
                        resume_strategy="reuse subtitle files when translation batches match",
                        artifacts={"srt": srt_path, "ass": ass_path},
                        fresh=True,
                    ),
                )
                burned_path = await self._run_stage(
                    job,
                    PipelineStage.BURN_SUBTITLES.value,
                    lambda temporary_clip=temporary_clip, ass_path=ass_path: self.clip_engine.burn_subtitles(
                        temporary_clip,
                        ass_path,
                        candidate_dir,
                    ),
                )
                temporary_clip.unlink(missing_ok=True)
                self._append_stage_record(
                    render_dir=candidate_dir,
                    manifest=stage_manifest,
                    record=StageRecord(
                        stage=PipelineStage.BURN_SUBTITLES,
                        status=StageStatus.COMPLETED,
                        resume_strategy="reuse burned video when subtitle files and source fingerprint match",
                        artifacts={"video": burned_path},
                        fresh=True,
                    ),
                )
```

Leave `metadata_generation` in place for now, but change its stage string to `PipelineStage.GENERATE_METADATA.value` and append a completed `GENERATE_METADATA` record after it succeeds.

- [ ] **Step 6: Add validation stage before ready manifest**

Before the final `self.video_store.write_render(... render_state=RenderState.READY ...)`, insert:

```python
                await self._run_stage(
                    job,
                    PipelineStage.VALIDATE_RENDER.value,
                    lambda candidate_dir=candidate_dir,
                    selected_segments=selected_segments,
                    subtitle_items=subtitle_items: asyncio.to_thread(
                        self.render_validator.validate,
                        render_dir=candidate_dir,
                        expected_segments=selected_segments,
                        subtitle_items=subtitle_items,
                    ),
                )
                self._append_stage_record(
                    render_dir=candidate_dir,
                    manifest=stage_manifest,
                    record=StageRecord(
                        stage=PipelineStage.VALIDATE_RENDER,
                        status=StageStatus.COMPLETED,
                        resume_strategy="render is publishable; reuse ready render by default",
                        fresh=True,
                    ),
                )
```

Ensure `asyncio` is imported if not already present.

- [ ] **Step 7: Record failed stage manifests**

Inside the `except Exception as exc:` block, before `self.video_store.write_render(... render_state=RenderState.FAILED ...)`, add:

```python
                failed_stage = getattr(exc, "stage", None) or "rendering"
                stage_manifest = self._load_stage_manifest(
                    render_dir=candidate_dir,
                    job_id=job.job_id,
                    render_id=batch.render_id,
                    candidate_id=candidate_id,
                )
                self._append_stage_record(
                    render_dir=candidate_dir,
                    manifest=stage_manifest,
                    record=StageRecord(
                        stage=PipelineStage(failed_stage)
                        if failed_stage in {stage.value for stage in PipelineStage}
                        else PipelineStage.VALIDATE_RENDER,
                        status=StageStatus.FAILED,
                        resume_strategy=f"rerun render to resume from {failed_stage}",
                        error=error,
                    ),
                )
```

Move the existing `error = self._as_job_error(exc, "rendering")` assignment above this failed-stage append block so the `StageRecord` receives the same structured error that is written to the failed render manifest.

- [ ] **Step 8: Verify service tests**

Run:

```bash
uv run pytest tests/service/test_job_service.py::test_candidate_render_writes_stage_manifest tests/service/test_job_service.py::test_pipeline_log_records_analysis_and_render_stage_timings -q
```

Expected: PASS. If the existing log test expects `candidate_clip_render`, update it to expect the new granular render stages.

- [ ] **Step 9: Commit**

Run:

```bash
git add src/insightcast/services/job_service.py tests/service/test_job_service.py
git commit -m "Orchestrate granular render stages"
```

## Task 8: Surface Stage Summaries In API Responses

**Files:**
- Modify: `src/insightcast/api/schemas.py`
- Modify: `src/insightcast/api/routes/analysis_jobs.py`
- Test: `tests/api/test_analysis_jobs.py`

- [ ] **Step 1: Write API response test**

Append to `tests/api/test_analysis_jobs.py`:

```python
def test_render_batch_response_includes_stage_manifest(tmp_path: Path) -> None:
    client, service = make_client(tmp_path)
    batch = RenderBatch(
        render_id="render-1",
        candidate_ids=["A"],
        status=JobStatus.COMPLETED,
        message="All selected candidates rendered successfully.",
        output_dir=(tmp_path / "analysis" / "render").resolve(),
        created_at=datetime(2026, 6, 6, tzinfo=UTC),
        updated_at=datetime(2026, 6, 6, tzinfo=UTC),
    )
    batch.output_dir.mkdir(parents=True)
    (batch.output_dir / "stage-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "operation_id": "analysis-1",
                "render_id": "render-1",
                "candidate_id": "A",
                "stages": [
                    {
                        "stage": "validate_render",
                        "status": "completed",
                        "started_at": None,
                        "completed_at": None,
                        "elapsed_seconds": None,
                        "artifacts": {},
                        "resume_strategy": "render is publishable",
                        "fresh": False,
                        "reused": True,
                        "warnings": [],
                        "error": None,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    service.render_batches = [batch]

    with client:
        response = client.get("/api/v1/analysis-jobs/analysis-1/renders")

    assert response.status_code == 200
    body = response.json()
    assert body["render_batches"][0]["stages"][0]["stage"] == "validate_render"
```

Update `FakeService.__init__` in `tests/api/test_analysis_jobs.py`:

```python
        self.render_batches: list[RenderBatch] = []
```

Update `FakeService.list_render_batches`:

```python
    def list_render_batches(self, _job_id: str) -> list[RenderBatch]:
        return self.render_batches
```

- [ ] **Step 2: Run failing API test**

Run: `uv run pytest tests/api/test_analysis_jobs.py::test_render_batch_response_includes_stage_manifest -q`

Expected: FAIL because `RenderBatch` responses do not expose `stages`.

- [ ] **Step 3: Add response stage models**

Modify `src/insightcast/api/schemas.py`:

```python
from insightcast.domain.stages import StageRecord
```

Add:

```python
class RenderBatchItem(ApiModel):
    render_id: str
    candidate_ids: list[str]
    status: JobStatus
    message: str
    output_dir: Path
    candidate_results: dict[str, Any]
    stages: list[StageRecord] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
```

Change `RenderBatchListResponse.render_batches` from `list[RenderBatch]` to:

```python
    render_batches: list[RenderBatchItem]
```

- [ ] **Step 4: Load stage manifests in route**

Modify `src/insightcast/api/routes/analysis_jobs.py` with helper:

```python
def _render_batch_item(batch: RenderBatch) -> dict[str, Any]:
    stage_path = batch.output_dir / "stage-manifest.json"
    stages = []
    if stage_path.is_file():
        stages = StageManifest.model_validate_json(
            stage_path.read_text(encoding="utf-8")
        ).stages
    return {
        "render_id": batch.render_id,
        "candidate_ids": batch.candidate_ids,
        "status": batch.status,
        "message": batch.message,
        "output_dir": batch.output_dir,
        "candidate_results": batch.candidate_results,
        "stages": stages,
        "created_at": batch.created_at,
        "updated_at": batch.updated_at,
    }
```

Import `StageManifest` and use this helper in `list_renders()`:

```python
        render_batches=[_render_batch_item(batch) for batch in batches],
```

- [ ] **Step 5: Verify API tests**

Run: `uv run pytest tests/api/test_analysis_jobs.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add src/insightcast/api/schemas.py src/insightcast/api/routes/analysis_jobs.py tests/api/test_analysis_jobs.py
git commit -m "Expose render stage summaries in API"
```

## Task 9: Add CLI Render Status Reporting Helpers

**Files:**
- Modify: `src/insightcast/cli/analyze.py`
- Test: `tests/unit/test_analyze_cli.py`

- [ ] **Step 1: Write CLI formatting test**

Append to `tests/unit/test_analyze_cli.py`:

```python
from insightcast.cli import analyze


def test_cli_formats_render_stage_summary() -> None:
    stdout = StringIO()
    payload = {
        "render_batches": [
            {
                "render_id": "render-1",
                "status": "COMPLETED",
                "stages": [
                    {"stage": "translate_subtitles", "status": "completed", "resume_strategy": "reuse validated translation batches"},
                    {"stage": "validate_render", "status": "completed", "resume_strategy": "render is publishable"},
                ],
            }
        ]
    }

    analyze._print_render_stage_summary(payload, stdout=stdout)

    output = stdout.getvalue()
    assert "Render render-1: COMPLETED" in output
    assert "translate_subtitles: completed" in output
    assert "validate_render: completed" in output
```

- [ ] **Step 2: Run failing CLI test**

Run: `uv run pytest tests/unit/test_analyze_cli.py::test_cli_formats_render_stage_summary -q`

Expected: FAIL because `_print_render_stage_summary` does not exist.

- [ ] **Step 3: Implement CLI helper**

Modify `src/insightcast/cli/analyze.py`:

```python
def _print_render_stage_summary(payload: dict[str, Any], *, stdout: TextIO) -> None:
    for batch in payload.get("render_batches", []):
        print(
            f"Render {batch.get('render_id')}: {batch.get('status')}",
            file=stdout,
        )
        for stage in batch.get("stages", []):
            print(
                f"  {stage.get('stage')}: {stage.get('status')}",
                file=stdout,
            )
            if stage.get("error"):
                error = stage["error"]
                print(
                    f"    error_code={error.get('error_code')} resume={stage.get('resume_strategy')}",
                    file=stdout,
                )
```

Import `Any` and `TextIO` if missing.

- [ ] **Step 4: Verify CLI unit test**

Run: `uv run pytest tests/unit/test_analyze_cli.py::test_cli_formats_render_stage_summary -q`

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/insightcast/cli/analyze.py tests/unit/test_analyze_cli.py
git commit -m "Format render stage summaries in CLI"
```

## Task 10: Full Verification And Documentation

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update README render docs**

Modify the candidate render section in `README.md` to include:

```markdown
`force_translate=true` redoes subtitle translation while preserving the render batch.
`force_metadata=true` regenerates YouTube metadata. By default the system reuses ready
renders and resumes safe checkpoints.

Render responses include stage summaries. Use `stage-manifest.json` in the render
directory for detailed resume and error diagnostics.
```

- [ ] **Step 2: Run focused test suite**

Run:

```bash
uv run pytest tests/unit/test_stage_models.py tests/unit/test_stage_store.py tests/unit/test_lingo_engine.py tests/unit/test_clip_engine.py tests/unit/test_render_validator.py tests/service/test_job_service.py tests/api/test_analysis_jobs.py tests/unit/test_analyze_cli.py -q
```

Expected: PASS.

- [ ] **Step 3: Run lint**

Run:

```bash
uv run ruff check src tests
```

Expected: PASS.

- [ ] **Step 4: Run diff check**

Run:

```bash
git diff --check
```

Expected: no output and exit code 0.

- [ ] **Step 5: Commit docs and any final fixes**

Run:

```bash
git add README.md
git commit -m "Document resumable render diagnostics"
```

If the verification steps required small fixes, include those touched files in the same commit with README.

## Self-Review

- Spec coverage: The plan covers structured stage state, resume manifests, automatic reuse defaults, translation repair, render validation, actionable errors, API summaries, CLI summaries, force controls, and documentation.
- Scope check: The plan does not implement full one-hour rendering, manual subtitle editing, upload scheduling, or distributed workers, matching the spec's out-of-scope section.
- Placeholder scan: No task uses TBD/TODO/fill-in language. Tests use concrete helper names and assertions from the current codebase.
- Type consistency: Stage model names are `PipelineStage`, `StageStatus`, `StageRecord`, and `StageManifest` throughout; force flags are `force_translate` and `force_metadata` throughout.
