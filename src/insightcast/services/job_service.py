import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from time import perf_counter
from typing import Any, TypeVar
from uuid import uuid4

from insightcast.core.exceptions import InsightCastError
from insightcast.core.logging import (
    format_log_fields,
    format_task_summary,
    get_job_log_path,
    get_job_logger,
    log_task_failure,
    log_task_llm_telemetry,
    log_task_stage,
    log_task_status,
    log_task_summary,
    log_task_transcription_progress,
)
from insightcast.domain.enums import ErrorCode, JobStatus, JobType
from insightcast.domain.models import (
    AnalysisJob,
    Candidate,
    CandidateRenderResult,
    CandidateSelectionRequest,
    DirectRenderJob,
    JobError,
    RenderArtifacts,
    RenderBatch,
    Transcript,
)
from insightcast.domain.stages import PipelineStage, StageManifest, StageRecord, StageStatus
from insightcast.engines.render_validator import RenderValidator
from insightcast.infrastructure.openai_client import capture_llm_telemetry
from insightcast.infrastructure.transcription.base import (
    TranscriptionSpec,
    build_transcript_cache_key,
)
from insightcast.infrastructure.transcription.openai_transcription_client import (
    capture_transcription_progress,
)
from insightcast.storage.file_job_writer import FileJobWriter
from insightcast.storage.manifests import (
    AnalysisState,
    PublishState,
    RenderKind,
    RenderState,
)
from insightcast.storage.stage_store import StageStore
from insightcast.storage.video_store import VideoStore
from insightcast.utils.files import build_run_id
from insightcast.utils.youtube import extract_youtube_video_id, normalize_youtube_url

StageResult = TypeVar("StageResult")


class WorkKind(StrEnum):
    ANALYSIS = "ANALYSIS"
    ANALYSIS_RENDER = "ANALYSIS_RENDER"
    DIRECT_RENDER = "DIRECT_RENDER"


@dataclass(frozen=True)
class WorkItem:
    kind: WorkKind
    job_id: str
    render_id: str | None = None


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return uuid4().hex


class JobService:
    def __init__(
        self,
        *,
        output_root: Path,
        work_root: Path,
        source_engine: Any,
        transcription_client: Any,
        curator_engine: Any,
        clip_engine: Any,
        publish_engine: Any,
        writer: Any,
        queue: asyncio.Queue[WorkItem] | None = None,
        clock: Callable[[], datetime] = _utc_now,
        id_factory: Callable[[], str] = _new_id,
        stage_store: StageStore | None = None,
        render_validator: RenderValidator | None = None,
    ) -> None:
        self.output_root = output_root.expanduser().resolve()
        self.work_root = work_root.expanduser().resolve()
        self.source_engine = source_engine
        self.transcription_client = transcription_client
        self.curator_engine = curator_engine
        self.clip_engine = clip_engine
        self.publish_engine = publish_engine
        self.writer = writer
        self.queue: asyncio.Queue[WorkItem] = queue or asyncio.Queue()
        self.clock = clock
        self.id_factory = id_factory
        self.stage_store = stage_store or StageStore()
        self.render_validator = render_validator or RenderValidator()

        self.analysis_jobs: dict[str, AnalysisJob] = {}
        self.direct_jobs: dict[str, DirectRenderJob] = {}
        self.latest_analysis_by_url: dict[str, str] = {}
        self._analysis_options: dict[str, tuple[int, float, float]] = {}
        self._transcripts: dict[str, Transcript] = {}
        self._transcript_locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._source_metadata: dict[str, Any] = {}
        self._source_fingerprints: dict[str, str] = {}
        self._operation_started_at: dict[str, float] = {}
        self._operation_stage_metrics: dict[str, dict[str, float]] = {}
        self._operation_llm_metrics: dict[str, dict[str, dict[str, int]]] = {}
        self._operation_llm_skipped: dict[str, dict[str, int]] = {}
        self._operation_window_plan: dict[str, dict[str, dict[str, Any]]] = {}
        self.processed_work: list[WorkItem] = []
        self.video_store = VideoStore(self.output_root, FileJobWriter())

    async def create_analysis_job(
        self,
        youtube_url: str,
        *,
        candidate_count: int = 2,
        min_duration_minutes: float = 8,
        max_duration_minutes: float = 12,
        force_reanalyze: bool = False,
    ) -> AnalysisJob:
        normalized_url = normalize_youtube_url(youtube_url)
        if not force_reanalyze and normalized_url in self.latest_analysis_by_url:
            latest_job = self.analysis_jobs[self.latest_analysis_by_url[normalized_url]]
            if latest_job.status is not JobStatus.FAILED:
                return latest_job
        job_id = self.id_factory()
        created_at = self.clock()
        analysis_id = build_run_id(created_at, job_id)
        output_dir = self.output_root / "jobs" / (
            f"{created_at:%Y%m%d-%H%M%S}_pending_{job_id[:6]}"
        )
        job = AnalysisJob(
            job_id=job_id,
            job_type=JobType.ANALYSIS,
            original_youtube_url=youtube_url,
            normalized_youtube_url=normalized_url,
            status=JobStatus.QUEUED,
            message="Analysis job is queued.",
            output_dir=output_dir,
            video_id=extract_youtube_video_id(normalized_url),
            analysis_id=analysis_id,
            candidate_count=candidate_count,
            min_duration_minutes=min_duration_minutes,
            max_duration_minutes=max_duration_minutes,
            created_at=created_at,
            updated_at=created_at,
        )
        self.analysis_jobs[job_id] = job
        self.latest_analysis_by_url[normalized_url] = job_id
        self._analysis_options[job_id] = (
            candidate_count,
            min_duration_minutes,
            max_duration_minutes,
        )
        get_job_logger(job.job_id, job.output_dir).info("%s: %s", job.status, job.message)
        log_task_status(job)
        self.writer.write_job(job)
        await self.queue.put(WorkItem(kind=WorkKind.ANALYSIS, job_id=job_id))
        return job

    def get_analysis_job(self, job_id: str) -> AnalysisJob:
        try:
            return self.analysis_jobs[job_id]
        except KeyError as exc:
            raise self._job_not_found(job_id) from exc

    def list_render_batches(self, job_id: str) -> list[RenderBatch]:
        return self.get_analysis_job(job_id).render_batches

    def _resumable_failed_render_batch(
        self,
        job: AnalysisJob,
        candidate_ids: list[str],
    ) -> RenderBatch | None:
        for batch in reversed(job.render_batches):
            if batch.status is not JobStatus.FAILED:
                continue
            if not set(candidate_ids).issubset(set(batch.candidate_ids)):
                continue
            if all(
                (
                    result := batch.candidate_results.get(candidate_id)
                ) is not None
                and result.error is not None
                for candidate_id in candidate_ids
            ):
                return batch
        return None

    async def create_render(
        self,
        job_id: str,
        request: CandidateSelectionRequest,
    ) -> RenderBatch:
        job = self.get_analysis_job(job_id)
        renderable_statuses = {
            JobStatus.WAITING_SELECTION,
            JobStatus.COMPLETED,
            JobStatus.FAILED,
        }
        has_retained_analysis = (
            bool(job.candidates)
            and job_id in self._transcripts
            and job_id in self._source_metadata
            and job.source_artifacts is not None
        )
        if job.status not in renderable_statuses or not has_retained_analysis:
            raise InsightCastError(
                ErrorCode.INVALID_JOB_STATE,
                "Analysis job is not ready for candidate rendering.",
                details={"job_id": job.job_id, "status": job.status},
                stage="rendering",
            )
        candidates = {candidate.candidate_id: candidate for candidate in job.candidates}
        missing = [
            candidate_id
            for candidate_id in request.candidate_ids
            if candidate_id not in candidates
        ]
        if missing:
            raise InsightCastError(
                ErrorCode.CANDIDATE_NOT_FOUND,
                "One or more candidate IDs do not exist for this analysis job.",
                details={"candidate_ids": missing},
                stage="rendering",
            )

        assert job.video_id is not None
        if not request.force_render:
            resumable = self._resumable_failed_render_batch(job, request.candidate_ids)
            if resumable is not None:
                for candidate_id in request.candidate_ids:
                    resumable.candidate_results.pop(candidate_id, None)
                resumable.status = JobStatus.QUEUED
                resumable.message = "Render batch is queued for resume."
                resumable.updated_at = self.clock()
                await self.queue.put(
                    WorkItem(
                        kind=WorkKind.ANALYSIS_RENDER,
                        job_id=job_id,
                        render_id=resumable.render_id,
                    )
                )
                self._touch(job)
                return resumable

        created_at = self.clock()
        render_id = build_run_id(created_at, self.id_factory())
        output_dir = self.video_store.render_dir(
            job.video_id,
            render_id,
            analysis_id=job.analysis_id,
            candidate_id=request.candidate_ids[0],
        )
        batch = RenderBatch(
            render_id=render_id,
            candidate_ids=request.candidate_ids,
            status=JobStatus.QUEUED,
            message="Render batch is queued.",
            output_dir=output_dir,
            created_at=created_at,
            updated_at=created_at,
        )
        if not request.force_render:
            for candidate_id in request.candidate_ids:
                existing = self.video_store.find_ready_candidate_render(
                    job.video_id,
                    job.analysis_id,
                    candidate_id,
                )
                if existing is not None:
                    batch.candidate_results[candidate_id] = CandidateRenderResult(
                        candidate_id=candidate_id,
                        output_dir=existing.directory,
                        manifest_path=existing.manifest_path,
                        artifacts=existing.artifacts,
                    )
        source_fingerprint = self._source_fingerprint_for_job(job)
        assert job.transcript_id is not None
        for candidate_id in request.candidate_ids:
            if candidate_id in batch.candidate_results:
                continue
            candidate = candidates[candidate_id]
            self.video_store.write_render(
                video_id=job.video_id,
                render_id=render_id,
                operation_id=job.job_id,
                kind=RenderKind.CANDIDATE,
                analysis_id=job.analysis_id,
                candidate_id=candidate_id,
                start_seconds=candidate.start_seconds,
                end_seconds=candidate.end_seconds,
                source_fingerprint=source_fingerprint,
                transcript_id=job.transcript_id,
                render_config={"subtitle_language": "zh-TW", "bilingual": True},
                created_at=created_at,
                completed_at=None,
                render_state=RenderState.QUEUED,
                publish_state=PublishState.NOT_UPLOADED,
                log_path=Path("logs") / f"{job.job_id}.log",
            )
        job.render_batches.append(batch)
        if len(batch.candidate_results) == len(request.candidate_ids):
            batch.status = JobStatus.COMPLETED
            batch.message = "All selected candidates already have completed artifacts."
            batch.updated_at = self.clock()
        else:
            await self.queue.put(
                WorkItem(
                    kind=WorkKind.ANALYSIS_RENDER,
                    job_id=job_id,
                    render_id=render_id,
                )
            )
        self._touch(job)
        return batch

    async def create_direct_render_job(
        self,
        youtube_url: str,
        *,
        start_seconds: float,
        end_seconds: float,
    ) -> DirectRenderJob:
        if end_seconds <= start_seconds:
            raise InsightCastError(
                ErrorCode.INVALID_TIME_RANGE,
                "end_time must be later than start_time.",
                details={"start_time": start_seconds, "end_time": end_seconds},
            )
        normalized_url = normalize_youtube_url(youtube_url)
        job_id = self.id_factory()
        created_at = self.clock()
        render_id = build_run_id(created_at, job_id)
        output_dir = self.output_root / "jobs" / (
            f"{created_at:%Y%m%d-%H%M%S}_pending_direct_{job_id[:6]}"
        )
        job = DirectRenderJob(
            job_id=job_id,
            job_type=JobType.DIRECT_RENDER,
            original_youtube_url=youtube_url,
            normalized_youtube_url=normalized_url,
            status=JobStatus.QUEUED,
            message="Direct render job is queued.",
            output_dir=output_dir,
            video_id=extract_youtube_video_id(normalized_url),
            render_id=render_id,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            created_at=created_at,
            updated_at=created_at,
        )
        self.direct_jobs[job_id] = job
        get_job_logger(job.job_id, job.output_dir).info("%s: %s", job.status, job.message)
        log_task_status(job)
        self.writer.write_job(job)
        await self.queue.put(WorkItem(kind=WorkKind.DIRECT_RENDER, job_id=job_id))
        return job

    def get_direct_render_job(self, job_id: str) -> DirectRenderJob:
        try:
            return self.direct_jobs[job_id]
        except KeyError as exc:
            raise self._job_not_found(job_id) from exc

    async def process(self, item: WorkItem) -> None:
        self.processed_work.append(item)
        job = self._job_for_work_item(item)
        self._reset_operation_metrics(job.job_id)
        with capture_llm_telemetry(
            lambda fields, job=job: self._log_llm_telemetry(job, fields)
        ):
            if item.kind == WorkKind.ANALYSIS:
                await self._process_analysis(item.job_id)
            elif item.kind == WorkKind.ANALYSIS_RENDER:
                assert item.render_id is not None
                await self._process_analysis_render(item.job_id, item.render_id)
            else:
                await self._process_direct_render(item.job_id)

    def _job_for_work_item(self, item: WorkItem) -> AnalysisJob | DirectRenderJob:
        if item.kind is WorkKind.DIRECT_RENDER:
            return self.get_direct_render_job(item.job_id)
        return self.get_analysis_job(item.job_id)

    def _log_llm_telemetry(
        self,
        job: AnalysisJob | DirectRenderJob,
        fields: dict[str, Any],
    ) -> None:
        self._record_llm_telemetry(job.job_id, fields)
        get_job_logger(job.job_id, job.output_dir).info(
            "llm_telemetry %s",
            format_log_fields(fields),
        )
        log_task_llm_telemetry(job, fields)

    async def _process_analysis(self, job_id: str) -> None:
        job = self.get_analysis_job(job_id)
        try:
            self._set_status(job, JobStatus.INGESTING, "Downloading the source video.")
            source = await self._run_stage(
                job,
                "source_ingestion",
                lambda: self.source_engine.ingest(
                    youtube_url=job.normalized_youtube_url,
                    job_id=job.job_id,
                    created_at=job.created_at,
                    output_root=self.output_root,
                    direct=False,
                ),
            )
            provisional_output_dir = job.output_dir
            job.video_id = source.metadata.video_id
            job.output_dir = self.video_store.analysis_dir(
                source.metadata.video_id,
                job.analysis_id,
            )
            job.manifest_path = job.output_dir / "manifest.json"
            self._finalize_provisional_output(
                job.job_id,
                provisional_output_dir,
                job.output_dir,
            )
            job.source_artifacts = source.source_artifacts
            self._log_source_cache(job, source)
            self._source_metadata[job_id] = source.metadata
            self._source_fingerprints[job.job_id] = (
                await asyncio.to_thread(
                    self._load_source_fingerprint,
                    source.metadata.video_id,
                )
            )
            candidate_count, minimum, maximum = self._analysis_options[job_id]
            self._set_status(job, JobStatus.TRANSCRIBING, "Transcribing English audio.")
            transcript = await self._load_or_create_transcript(
                job,
                source,
            )
            self._transcripts[job_id] = transcript
            assert job.transcript_id is not None
            self.video_store.write_analysis(
                video_id=source.metadata.video_id,
                analysis_id=job.analysis_id,
                operation_id=job.job_id,
                created_at=job.created_at,
                completed_at=None,
                normalized_source_url=job.normalized_youtube_url,
                transcript_id=job.transcript_id,
                curator_model="",
                prompt_version="",
                candidate_count=candidate_count,
                min_duration_seconds=minimum * 60,
                max_duration_seconds=maximum * 60,
                candidates=[],
                state=AnalysisState.RUNNING,
                log_path=Path("logs") / f"{job.job_id}.log",
            )

            self._set_status(job, JobStatus.CURATING, "Ranking important video topics.")
            topics = await self._run_stage(
                job,
                "topic_discovery",
                lambda: self.curator_engine.discover_topics(
                    transcript=transcript,
                    candidate_count=candidate_count,
                ),
            )
            self._set_status(job, JobStatus.CURATING, "Selecting complete candidate ranges.")
            result = await self._run_stage(
                job,
                "candidate_boundary_selection",
                lambda: self.curator_engine.select_candidates(
                    transcript=transcript,
                    topics=topics,
                    candidate_count=candidate_count,
                    min_duration_minutes=minimum,
                    max_duration_minutes=maximum,
                ),
            )
            job.candidates = result.candidates
            completed_at = self.clock()
            analysis = self.video_store.write_analysis(
                video_id=source.metadata.video_id,
                analysis_id=job.analysis_id,
                operation_id=job.job_id,
                created_at=job.created_at,
                completed_at=completed_at,
                normalized_source_url=job.normalized_youtube_url,
                transcript_id=job.transcript_id,
                curator_model=result.model,
                prompt_version=result.prompt_version,
                candidate_count=candidate_count,
                min_duration_seconds=minimum * 60,
                max_duration_seconds=maximum * 60,
                candidates=result.candidates,
                state=AnalysisState.WAITING_SELECTION,
                log_path=Path("logs") / f"{job.job_id}.log",
            )
            self.writer.write_json(analysis.candidates_path, result)
            job.source_artifacts.candidates = analysis.candidates_path
            job.manifest_path = analysis.manifest_path
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
            self._log_operation_summary(
                job,
                {
                    "event": "analysis_completed",
                    "analysis_id": job.analysis_id,
                    "candidate_count": len(job.candidates),
                },
            )
        except InsightCastError as exc:
            get_job_logger(job.job_id, job.output_dir).exception(
                "Analysis pipeline failed with %s",
                exc.error_code,
            )
            self._fail_job(job, exc)
            self._write_failed_analysis_manifest(job)
        except Exception as exc:
            get_job_logger(job.job_id, job.output_dir).exception(
                "Unexpected analysis pipeline failure"
            )
            self._fail_job(
                job,
                InsightCastError(
                    ErrorCode.TRANSCRIPTION_FAILED,
                    "Analysis pipeline failed.",
                    details={"reason": str(exc)},
                    stage="analysis",
                ),
            )
            self._write_failed_analysis_manifest(job)

    async def _process_analysis_render(self, job_id: str, render_id: str) -> None:
        job = self.get_analysis_job(job_id)
        batch = next(
            batch for batch in job.render_batches if batch.render_id == render_id
        )
        batch.status = JobStatus.RENDERING
        batch.message = "Rendering selected candidates."
        batch.updated_at = self.clock()
        self._set_status(job, JobStatus.RENDERING, batch.message)
        candidates = {candidate.candidate_id: candidate for candidate in job.candidates}
        transcript = self._transcripts[job_id]
        source_metadata = self._source_metadata[job_id]
        assert job.source_artifacts is not None
        for candidate_id in batch.candidate_ids:
            if candidate_id in batch.candidate_results:
                continue
            candidate = candidates[candidate_id]
            assert job.video_id is not None
            assert job.transcript_id is not None
            candidate_dir = self.video_store.render_dir(
                job.video_id,
                batch.render_id,
                analysis_id=job.analysis_id,
                candidate_id=candidate_id,
            )
            try:
                self.video_store.write_render(
                    video_id=job.video_id,
                    render_id=batch.render_id,
                    operation_id=job.job_id,
                    kind=RenderKind.CANDIDATE,
                    analysis_id=job.analysis_id,
                    candidate_id=candidate_id,
                    start_seconds=candidate.start_seconds,
                    end_seconds=candidate.end_seconds,
                    source_fingerprint=self._source_fingerprint_for_job(job),
                    transcript_id=job.transcript_id,
                    render_config={"subtitle_language": "zh-TW", "bilingual": True},
                    created_at=batch.created_at,
                    completed_at=None,
                    render_state=RenderState.RENDERING,
                    publish_state=PublishState.NOT_UPLOADED,
                    log_path=Path("logs") / f"{job.job_id}.log",
                )
                if not job.source_artifacts.source_video.is_file():
                    raise InsightCastError(
                        ErrorCode.SOURCE_CACHE_MISSING,
                        "The cached source video required for rendering is missing.",
                        details={
                            "job_id": job.job_id,
                            "source_video": str(job.source_artifacts.source_video),
                        },
                        stage=PipelineStage.SOURCE_INGESTION.value,
                    )
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
                completed_cut = self._latest_completed_stage(
                    stage_manifest,
                    PipelineStage.CUT_CLIP,
                )
                reusable_clip = self._stage_artifact_path(
                    completed_cut,
                    "temporary_clip",
                )
                if reusable_clip is not None and reusable_clip.is_file():
                    temporary_clip = reusable_clip
                    stage_manifest = self._append_stage_record(
                        render_dir=candidate_dir,
                        manifest=stage_manifest,
                        record=StageRecord(
                            stage=PipelineStage.CUT_CLIP,
                            status=StageStatus.SKIPPED,
                            completed_at=self.clock(),
                            artifacts={"temporary_clip": temporary_clip},
                            resume_strategy="reuse completed cut clip",
                            reused=True,
                        ),
                    )
                else:
                    stage_manifest = self._start_stage_record(
                        render_dir=candidate_dir,
                        manifest=stage_manifest,
                        stage=PipelineStage.CUT_CLIP,
                        resume_strategy=(
                            "rerun cut_clip unless a completed cut clip can be reused"
                        ),
                        fresh=True,
                    )
                    temporary_clip = await self._run_stage(
                        job,
                        PipelineStage.CUT_CLIP.value,
                        lambda candidate=candidate: self.clip_engine.cut_clip(
                            job.source_artifacts.source_video,
                            candidate,
                            self.work_root / job.job_id / batch.render_id,
                        ),
                    )
                    stage_manifest = self._finish_stage_record(
                        render_dir=candidate_dir,
                        manifest=stage_manifest,
                        stage=PipelineStage.CUT_CLIP,
                        status=StageStatus.COMPLETED,
                        artifacts={"temporary_clip": temporary_clip},
                    )
                stage_manifest = self._start_stage_record(
                    render_dir=candidate_dir,
                    manifest=stage_manifest,
                    stage=PipelineStage.TRANSLATE_SUBTITLES,
                    resume_strategy="reuse validated translation batches",
                    fresh=True,
                )
                subtitle_items = await self._run_stage(
                    job,
                    PipelineStage.TRANSLATE_SUBTITLES.value,
                    lambda candidate=candidate: self.clip_engine.translate_subtitles(
                        transcript.segments,
                        candidate,
                    ),
                )
                stage_manifest = self._finish_stage_record(
                    render_dir=candidate_dir,
                    manifest=stage_manifest,
                    stage=PipelineStage.TRANSLATE_SUBTITLES,
                    status=StageStatus.COMPLETED,
                )
                stage_manifest = self._start_stage_record(
                    render_dir=candidate_dir,
                    manifest=stage_manifest,
                    stage=PipelineStage.WRITE_SUBTITLES,
                    resume_strategy="reuse subtitle files when translation batches match",
                    fresh=True,
                )
                srt_path, ass_path = await self._run_stage(
                    job,
                    PipelineStage.WRITE_SUBTITLES.value,
                    lambda subtitle_items=subtitle_items,
                    candidate=candidate,
                    candidate_dir=candidate_dir: asyncio.to_thread(
                        self.clip_engine.write_subtitles,
                        subtitle_items,
                        candidate,
                        candidate_dir,
                    ),
                )
                stage_manifest = self._finish_stage_record(
                    render_dir=candidate_dir,
                    manifest=stage_manifest,
                    stage=PipelineStage.WRITE_SUBTITLES,
                    status=StageStatus.COMPLETED,
                    artifacts={"srt": srt_path, "ass": ass_path},
                )
                stage_manifest = self._start_stage_record(
                    render_dir=candidate_dir,
                    manifest=stage_manifest,
                    stage=PipelineStage.BURN_SUBTITLES,
                    resume_strategy=(
                        "reuse burned video when subtitle files and source "
                        "fingerprint match"
                    ),
                    fresh=True,
                )
                burned_path = await self._run_stage(
                    job,
                    PipelineStage.BURN_SUBTITLES.value,
                    lambda temporary_clip=temporary_clip,
                    ass_path=ass_path,
                    candidate_dir=candidate_dir: self.clip_engine.burn_subtitles(
                        temporary_clip,
                        ass_path,
                        candidate_dir,
                    ),
                )
                temporary_clip.unlink(missing_ok=True)
                stage_manifest = self._finish_stage_record(
                    render_dir=candidate_dir,
                    manifest=stage_manifest,
                    stage=PipelineStage.BURN_SUBTITLES,
                    status=StageStatus.COMPLETED,
                    artifacts={"video": burned_path},
                )
                metadata_path = candidate_dir / "youtube-metadata.json"
                excerpt = self._transcript_excerpt(transcript, candidate)
                stage_manifest = self._start_stage_record(
                    render_dir=candidate_dir,
                    manifest=stage_manifest,
                    stage=PipelineStage.GENERATE_METADATA,
                    resume_strategy="reuse generated metadata",
                    fresh=True,
                )
                await self._run_stage(
                    job,
                    PipelineStage.GENERATE_METADATA.value,
                    lambda candidate=candidate,
                    excerpt=excerpt,
                    metadata_path=metadata_path: self.publish_engine.generate(
                        source_metadata=source_metadata,
                        candidate_suggested_title=candidate.suggested_title,
                        summary=candidate.summary,
                        transcript_excerpt=excerpt,
                        candidate_core_claim=candidate.core_claim,
                        candidate_payoff=candidate.payoff,
                        candidate_argument_arc=candidate.argument_arc,
                        candidate_boundary_notes=candidate.boundary_notes,
                        destination=metadata_path,
                    ),
                )
                stage_manifest = self._finish_stage_record(
                    render_dir=candidate_dir,
                    manifest=stage_manifest,
                    stage=PipelineStage.GENERATE_METADATA,
                    status=StageStatus.COMPLETED,
                    artifacts={"youtube_metadata": metadata_path},
                )
                stage_manifest = self._start_stage_record(
                    render_dir=candidate_dir,
                    manifest=stage_manifest,
                    stage=PipelineStage.VALIDATE_RENDER,
                    resume_strategy="render is publishable; reuse ready render by default",
                    fresh=True,
                )
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
                self._finish_stage_record(
                    render_dir=candidate_dir,
                    manifest=stage_manifest,
                    stage=PipelineStage.VALIDATE_RENDER,
                    status=StageStatus.COMPLETED,
                )
                render = self.video_store.write_render(
                    video_id=job.video_id,
                    render_id=batch.render_id,
                    operation_id=job.job_id,
                    kind=RenderKind.CANDIDATE,
                    analysis_id=job.analysis_id,
                    candidate_id=candidate_id,
                    start_seconds=candidate.start_seconds,
                    end_seconds=candidate.end_seconds,
                    source_fingerprint=self._source_fingerprint_for_job(job),
                    transcript_id=job.transcript_id,
                    render_config={"subtitle_language": "zh-TW", "bilingual": True},
                    created_at=batch.created_at,
                    completed_at=self.clock(),
                    render_state=RenderState.READY,
                    publish_state=PublishState.NOT_UPLOADED,
                    log_path=Path("logs") / f"{job.job_id}.log",
                )
                batch.candidate_results[candidate_id] = CandidateRenderResult(
                    candidate_id=candidate_id,
                    output_dir=render.directory,
                    manifest_path=render.manifest_path,
                    artifacts=render.artifacts,
                )
            except Exception as exc:
                get_job_logger(job.job_id, job.output_dir).exception(
                    "Candidate %s render failed",
                    candidate_id,
                )
                error = self._as_job_error(exc, "rendering")
                failed_stage = getattr(exc, "stage", None) or "rendering"
                stage_manifest = self._load_stage_manifest_or_new(
                    render_dir=candidate_dir,
                    job_id=job.job_id,
                    render_id=batch.render_id,
                    candidate_id=candidate_id,
                )
                failed_pipeline_stage = (
                    PipelineStage(failed_stage)
                    if failed_stage in {stage.value for stage in PipelineStage}
                    else PipelineStage.SOURCE_INGESTION
                )
                self._finish_stage_record(
                    render_dir=candidate_dir,
                    manifest=stage_manifest,
                    stage=failed_pipeline_stage,
                    status=StageStatus.FAILED,
                    error=error,
                    resume_strategy=f"rerun render to resume from {failed_stage}",
                )
                log_task_failure(job, error)
                self.video_store.write_render(
                    video_id=job.video_id,
                    render_id=batch.render_id,
                    operation_id=job.job_id,
                    kind=RenderKind.CANDIDATE,
                    analysis_id=job.analysis_id,
                    candidate_id=candidate_id,
                    start_seconds=candidate.start_seconds,
                    end_seconds=candidate.end_seconds,
                    source_fingerprint=self._source_fingerprint_for_job(job),
                    transcript_id=job.transcript_id,
                    render_config={"subtitle_language": "zh-TW", "bilingual": True},
                    created_at=batch.created_at,
                    completed_at=self.clock(),
                    render_state=RenderState.FAILED,
                    publish_state=PublishState.NOT_UPLOADED,
                    log_path=Path("logs") / f"{job.job_id}.log",
                    render_error=error,
                )
                batch.candidate_results[candidate_id] = CandidateRenderResult(
                    candidate_id=candidate_id,
                    output_dir=candidate_dir,
                    manifest_path=candidate_dir / "manifest.json",
                    error=error,
                )
        failures = [
            result for result in batch.candidate_results.values() if result.error is not None
        ]
        batch.updated_at = self.clock()
        if failures:
            batch.status = JobStatus.FAILED
            batch.message = f"{len(failures)} selected candidate render(s) failed."
            job.status = JobStatus.FAILED
            job.message = batch.message
        else:
            batch.status = JobStatus.COMPLETED
            batch.message = "All selected candidates rendered successfully."
            job.status = JobStatus.COMPLETED
            job.message = batch.message
        self._touch(job)
        if not failures:
            self._log_operation_summary(
                job,
                {
                    "event": "render_completed",
                    "render_id": batch.render_id,
                    "candidate_ids": ",".join(batch.candidate_ids),
                    "candidate_count": len(batch.candidate_ids),
                },
            )

    async def _process_direct_render(self, job_id: str) -> None:
        job = self.get_direct_render_job(job_id)
        try:
            self._set_status(job, JobStatus.INGESTING, "Downloading the source video.")
            source = await self._run_stage(
                job,
                "source_ingestion",
                lambda: self.source_engine.ingest(
                    youtube_url=job.normalized_youtube_url,
                    job_id=job.job_id,
                    created_at=job.created_at,
                    output_root=self.output_root,
                    direct=True,
                ),
            )
            provisional_output_dir = job.output_dir
            job.video_id = source.metadata.video_id
            job.source_artifacts = source.source_artifacts
            self._log_source_cache(job, source)
            self._source_fingerprints[job.job_id] = (
                await asyncio.to_thread(
                    self._load_source_fingerprint,
                    source.metadata.video_id,
                )
            )
            self._set_status(job, JobStatus.TRANSCRIBING, "Transcribing English audio.")
            transcript = await self._load_or_create_transcript(job, source)
            assert job.transcript_id is not None
            assert job.render_id is not None
            render_dir = self.video_store.render_dir(
                source.metadata.video_id,
                job.render_id,
            )
            job.output_dir = render_dir
            job.manifest_path = render_dir / "manifest.json"
            self._finalize_provisional_output(
                job.job_id,
                provisional_output_dir,
                job.output_dir,
            )
            created_at = job.created_at
            self.video_store.write_render(
                video_id=source.metadata.video_id,
                render_id=job.render_id,
                operation_id=job.job_id,
                kind=RenderKind.CUSTOM,
                analysis_id=None,
                candidate_id=None,
                start_seconds=job.start_seconds,
                end_seconds=job.end_seconds,
                source_fingerprint=self._source_fingerprint_for_job(job),
                transcript_id=job.transcript_id,
                render_config={"subtitle_language": "zh-TW", "bilingual": True},
                created_at=created_at,
                completed_at=None,
                render_state=RenderState.QUEUED,
                publish_state=PublishState.NOT_UPLOADED,
                log_path=Path("logs") / f"{job.job_id}.log",
            )
            self.video_store.write_render(
                video_id=source.metadata.video_id,
                render_id=job.render_id,
                operation_id=job.job_id,
                kind=RenderKind.CUSTOM,
                analysis_id=None,
                candidate_id=None,
                start_seconds=job.start_seconds,
                end_seconds=job.end_seconds,
                source_fingerprint=self._source_fingerprint_for_job(job),
                transcript_id=job.transcript_id,
                render_config={"subtitle_language": "zh-TW", "bilingual": True},
                created_at=created_at,
                completed_at=None,
                render_state=RenderState.RENDERING,
                publish_state=PublishState.NOT_UPLOADED,
                log_path=Path("logs") / f"{job.job_id}.log",
            )
            self._set_status(job, JobStatus.RENDERING, "Rendering the requested time range.")
            selection = Candidate(
                candidate_id="custom",
                start_seconds=job.start_seconds,
                end_seconds=job.end_seconds,
                suggested_title=f"{source.metadata.title} clip",
                selection_reason="User-selected direct render range.",
                summary="Direct render selected by the user.",
            )
            stage_manifest = self._load_stage_manifest(
                render_dir=render_dir,
                job_id=job.job_id,
                render_id=job.render_id,
                candidate_id=None,
            )
            selected_segments = [
                segment
                for segment in transcript.segments
                if segment.end_seconds > selection.start_seconds
                and segment.start_seconds < selection.end_seconds
            ]
            stage_manifest = self._start_stage_record(
                render_dir=render_dir,
                manifest=stage_manifest,
                stage=PipelineStage.CUT_CLIP,
                resume_strategy=(
                    "rerun cut_clip unless a completed cut clip can be reused"
                ),
                fresh=True,
            )
            temporary_clip = await self._run_stage(
                job,
                PipelineStage.CUT_CLIP.value,
                lambda: self.clip_engine.cut_clip(
                    source.source_artifacts.source_video,
                    selection,
                    self.work_root / job.job_id / job.render_id,
                ),
            )
            stage_manifest = self._finish_stage_record(
                render_dir=render_dir,
                manifest=stage_manifest,
                stage=PipelineStage.CUT_CLIP,
                status=StageStatus.COMPLETED,
                artifacts={"temporary_clip": temporary_clip},
            )
            stage_manifest = self._start_stage_record(
                render_dir=render_dir,
                manifest=stage_manifest,
                stage=PipelineStage.TRANSLATE_SUBTITLES,
                resume_strategy="reuse validated translation batches",
                fresh=True,
            )
            subtitle_items = await self._run_stage(
                job,
                PipelineStage.TRANSLATE_SUBTITLES.value,
                lambda: self.clip_engine.translate_subtitles(
                    transcript.segments,
                    selection,
                ),
            )
            stage_manifest = self._finish_stage_record(
                render_dir=render_dir,
                manifest=stage_manifest,
                stage=PipelineStage.TRANSLATE_SUBTITLES,
                status=StageStatus.COMPLETED,
            )
            stage_manifest = self._start_stage_record(
                render_dir=render_dir,
                manifest=stage_manifest,
                stage=PipelineStage.WRITE_SUBTITLES,
                resume_strategy="reuse subtitle files when translation batches match",
                fresh=True,
            )
            srt_path, ass_path = await self._run_stage(
                job,
                PipelineStage.WRITE_SUBTITLES.value,
                lambda: asyncio.to_thread(
                    self.clip_engine.write_subtitles,
                    subtitle_items,
                    selection,
                    render_dir,
                ),
            )
            stage_manifest = self._finish_stage_record(
                render_dir=render_dir,
                manifest=stage_manifest,
                stage=PipelineStage.WRITE_SUBTITLES,
                status=StageStatus.COMPLETED,
                artifacts={"srt": srt_path, "ass": ass_path},
            )
            stage_manifest = self._start_stage_record(
                render_dir=render_dir,
                manifest=stage_manifest,
                stage=PipelineStage.BURN_SUBTITLES,
                resume_strategy=(
                    "reuse burned video when subtitle files and source fingerprint match"
                ),
                fresh=True,
            )
            burned_path = await self._run_stage(
                job,
                PipelineStage.BURN_SUBTITLES.value,
                lambda: self.clip_engine.burn_subtitles(
                    temporary_clip,
                    ass_path,
                    render_dir,
                ),
            )
            temporary_clip.unlink(missing_ok=True)
            stage_manifest = self._finish_stage_record(
                render_dir=render_dir,
                manifest=stage_manifest,
                stage=PipelineStage.BURN_SUBTITLES,
                status=StageStatus.COMPLETED,
                artifacts={"video": burned_path},
            )
            metadata_path = render_dir / "youtube-metadata.json"
            excerpt = self._transcript_excerpt(transcript, selection)
            stage_manifest = self._start_stage_record(
                render_dir=render_dir,
                manifest=stage_manifest,
                stage=PipelineStage.GENERATE_METADATA,
                resume_strategy="reuse generated metadata",
                fresh=True,
            )
            await self._run_stage(
                job,
                PipelineStage.GENERATE_METADATA.value,
                lambda: self.publish_engine.generate(
                    source_metadata=source.metadata,
                    candidate_suggested_title=selection.suggested_title,
                    summary=selection.summary,
                    transcript_excerpt=excerpt,
                    candidate_core_claim=selection.core_claim,
                    candidate_payoff=selection.payoff,
                    candidate_argument_arc=selection.argument_arc,
                    candidate_boundary_notes=selection.boundary_notes,
                    destination=metadata_path,
                ),
            )
            stage_manifest = self._finish_stage_record(
                render_dir=render_dir,
                manifest=stage_manifest,
                stage=PipelineStage.GENERATE_METADATA,
                status=StageStatus.COMPLETED,
                artifacts={"youtube_metadata": metadata_path},
            )
            stage_manifest = self._start_stage_record(
                render_dir=render_dir,
                manifest=stage_manifest,
                stage=PipelineStage.VALIDATE_RENDER,
                resume_strategy="render is publishable; reuse ready render by default",
                fresh=True,
            )
            await self._run_stage(
                job,
                PipelineStage.VALIDATE_RENDER.value,
                lambda: asyncio.to_thread(
                    self.render_validator.validate,
                    render_dir=render_dir,
                    expected_segments=selected_segments,
                    subtitle_items=subtitle_items,
                ),
            )
            self._finish_stage_record(
                render_dir=render_dir,
                manifest=stage_manifest,
                stage=PipelineStage.VALIDATE_RENDER,
                status=StageStatus.COMPLETED,
            )
            render = self.video_store.write_render(
                video_id=source.metadata.video_id,
                render_id=job.render_id,
                operation_id=job.job_id,
                kind=RenderKind.CUSTOM,
                analysis_id=None,
                candidate_id=None,
                start_seconds=job.start_seconds,
                end_seconds=job.end_seconds,
                source_fingerprint=self._source_fingerprint_for_job(job),
                transcript_id=job.transcript_id,
                render_config={"subtitle_language": "zh-TW", "bilingual": True},
                created_at=created_at,
                completed_at=self.clock(),
                render_state=RenderState.READY,
                publish_state=PublishState.NOT_UPLOADED,
                log_path=Path("logs") / f"{job.job_id}.log",
            )
            job.artifacts = render.artifacts
            self._set_status(
                job,
                JobStatus.COMPLETED,
                "Direct render completed successfully.",
            )
            self._log_operation_summary(
                job,
                {
                    "event": "direct_render_completed",
                    "render_id": job.render_id,
                },
            )
        except InsightCastError as exc:
            get_job_logger(job.job_id, job.output_dir).exception(
                "Direct render pipeline failed with %s",
                exc.error_code,
            )
            self._fail_job(job, exc)
            self._mark_failed_direct_render_stage(job)
            self._write_failed_direct_render_manifest(job)
        except Exception as exc:
            get_job_logger(job.job_id, job.output_dir).exception(
                "Unexpected direct render pipeline failure"
            )
            error = InsightCastError(
                ErrorCode.VIDEO_RENDER_FAILED,
                "Direct render pipeline failed.",
                details={"reason": str(exc)},
                stage=getattr(exc, "stage", None) or "rendering",
            )
            self._fail_job(job, error)
            self._mark_failed_direct_render_stage(job)
            self._write_failed_direct_render_manifest(job)

    def _set_status(
        self,
        job: AnalysisJob | DirectRenderJob,
        status: JobStatus,
        message: str,
    ) -> None:
        job.status = status
        job.message = message
        self._touch(job)

    def _touch(self, job: AnalysisJob | DirectRenderJob) -> None:
        job.updated_at = self.clock()
        get_job_logger(job.job_id, job.output_dir).info("%s: %s", job.status, job.message)
        log_task_status(job)
        self.writer.write_job(job)

    @staticmethod
    def _log_source_cache(
        job: AnalysisJob | DirectRenderJob,
        source: Any,
    ) -> None:
        get_job_logger(job.job_id, job.output_dir).info(
            "source_cache_%s video_id=%s source=%s audio=%s",
            source.cache_decision,
            source.metadata.video_id,
            source.source_artifacts.source_video,
            source.source_artifacts.source_audio,
        )

    async def _load_or_create_transcript(
        self,
        job: AnalysisJob | DirectRenderJob,
        source: Any,
    ) -> Transcript:
        store = VideoStore(self.output_root, FileJobWriter())
        lookup = await asyncio.to_thread(store.load_source, source.metadata.video_id)
        if lookup.entry is None:
            raise InsightCastError(
                ErrorCode.SOURCE_CACHE_INVALID,
                "Managed source is required before transcript caching.",
                details={"video_id": source.metadata.video_id},
                stage="transcribing",
            )
        spec = TranscriptionSpec(
            source_fingerprint=lookup.entry.manifest.source_fingerprint,
            provider=self.transcription_client.transcription_provider,
            model=self.transcription_client.transcription_model,
            language=self.transcription_client.transcription_language,
            transcript_schema_version=self.transcription_client.transcript_schema_version,
        )
        cached = await asyncio.to_thread(
            store.find_ready_transcript,
            source.metadata.video_id,
            spec,
        )
        if cached is not None:
            return self._use_cached_transcript(job, source, cached)
        cache_key = build_transcript_cache_key(spec)
        lock = self._transcript_locks.setdefault(
            (source.metadata.video_id, cache_key),
            asyncio.Lock(),
        )
        async with lock:
            cached = await asyncio.to_thread(
                store.find_ready_transcript,
                source.metadata.video_id,
                spec,
            )
            if cached is not None:
                return self._use_cached_transcript(job, source, cached)
            transcript = await self._run_stage(
                job,
                "transcription",
                lambda: self._transcribe_with_progress_logging(job, source),
            )
            entry = await asyncio.to_thread(
                store.write_transcript,
                source.metadata.video_id,
                spec,
                transcript,
            )
        assert job.source_artifacts is not None
        job.source_artifacts.transcript = entry.transcript_path
        job.transcript_id = entry.manifest.transcript_id
        get_job_logger(job.job_id, job.output_dir).info(
            "transcript_cache_miss video_id=%s transcript_id=%s cache_key=%s",
            source.metadata.video_id,
            entry.manifest.transcript_id,
            entry.manifest.cache_key,
        )
        return entry.transcript

    async def _transcribe_with_progress_logging(
        self,
        job: AnalysisJob | DirectRenderJob,
        source: Any,
    ) -> Transcript:
        logger = get_job_logger(job.job_id, job.output_dir)

        def emit_progress(fields: dict[str, Any]) -> None:
            enriched = {
                "stage": "transcription",
                "video_id": source.metadata.video_id,
                **fields,
            }
            job.progress = enriched.copy()
            job.updated_at = self.clock()
            log_fields = enriched.copy()
            log_fields.pop("stage", None)
            logger.info("transcription_progress %s", format_log_fields(log_fields))
            log_task_transcription_progress(job, log_fields)

        with capture_transcription_progress(emit_progress):
            return await self.transcription_client.transcribe(
                source.source_artifacts.source_audio
            )

    @staticmethod
    def _use_cached_transcript(
        job: AnalysisJob | DirectRenderJob,
        source: Any,
        cached: Any,
    ) -> Transcript:
        assert job.source_artifacts is not None
        job.source_artifacts.transcript = cached.transcript_path
        job.transcript_id = cached.manifest.transcript_id
        get_job_logger(job.job_id, job.output_dir).info(
            "transcript_cache_hit video_id=%s transcript_id=%s cache_key=%s",
            source.metadata.video_id,
            cached.manifest.transcript_id,
            cached.manifest.cache_key,
        )
        return cached.transcript

    def _source_fingerprint_for_job(
        self,
        job: AnalysisJob | DirectRenderJob,
    ) -> str:
        cached = self._source_fingerprints.get(job.job_id)
        if cached is not None:
            return cached
        if job.video_id is None:
            raise InsightCastError(
                ErrorCode.SOURCE_CACHE_INVALID,
                "Managed source identity is missing for rendering.",
                details={"job_id": job.job_id},
                stage="rendering",
            )
        fingerprint = self._load_source_fingerprint(job.video_id)
        self._source_fingerprints[job.job_id] = fingerprint
        return fingerprint

    def _load_source_fingerprint(self, video_id: str) -> str:
        lookup = self.video_store.load_source(video_id)
        if lookup.entry is None:
            raise InsightCastError(
                ErrorCode.SOURCE_CACHE_INVALID,
                "Managed source is required for rendering.",
                details={"video_id": video_id},
                stage="rendering",
            )
        return lookup.entry.manifest.source_fingerprint

    async def _run_stage(
        self,
        job: AnalysisJob | DirectRenderJob,
        stage: str,
        operation: Callable[[], Awaitable[StageResult]],
    ) -> StageResult:
        logger = get_job_logger(job.job_id, job.output_dir)
        started_at = perf_counter()
        logger.info("stage_started stage=%s", stage)
        log_task_stage(job, stage, "started")
        try:
            result = await operation()
        except InsightCastError as exc:
            elapsed_seconds = perf_counter() - started_at
            if exc.stage is not None and exc.stage != stage:
                exc.details.setdefault("inner_stage", exc.stage)
            exc.stage = stage
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
        except Exception as exc:
            elapsed_seconds = perf_counter() - started_at
            exc.stage = stage  # type: ignore[attr-defined]
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
        self._record_stage_metric(job.job_id, stage, elapsed_seconds)
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

    def _reset_operation_metrics(self, job_id: str) -> None:
        self._operation_started_at[job_id] = perf_counter()
        self._operation_stage_metrics[job_id] = {}
        self._operation_llm_metrics[job_id] = {}
        self._operation_llm_skipped[job_id] = {}
        self._operation_window_plan[job_id] = {}

    def _record_stage_metric(
        self,
        job_id: str,
        stage: str,
        elapsed_seconds: float,
    ) -> None:
        self._operation_stage_metrics.setdefault(job_id, {})[stage] = elapsed_seconds

    def _record_llm_telemetry(self, job_id: str, fields: dict[str, Any]) -> None:
        event = fields.get("event")
        if event == "window_plan":
            trace_name = str(fields.get("trace_name") or "unknown")
            self._operation_window_plan.setdefault(job_id, {})[trace_name] = fields.copy()
            return
        if event == "skipped":
            trace_name = str(fields.get("trace_name") or "unknown")
            skipped = self._operation_llm_skipped.setdefault(job_id, {})
            skipped[trace_name] = skipped.get(trace_name, 0) + 1
            return
        if event != "completed":
            return
        trace_name = str(fields.get("trace_name") or "unknown")
        metrics = self._operation_llm_metrics.setdefault(job_id, {}).setdefault(
            trace_name,
            {
                "calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "user_chars": 0,
            },
        )
        metrics["calls"] += 1
        for key in ("input_tokens", "output_tokens", "total_tokens", "user_chars"):
            value = fields.get(key)
            if isinstance(value, int):
                metrics[key] += value

    def _log_operation_summary(
        self,
        job: AnalysisJob | DirectRenderJob,
        fields: dict[str, Any],
    ) -> None:
        summary = {
            **fields,
            "operation_elapsed_seconds": round(
                perf_counter() - self._operation_started_at.get(job.job_id, perf_counter()),
                3,
            ),
            **self._stage_summary_fields(job.job_id),
            **self._llm_summary_fields(job.job_id),
            **self._window_plan_summary_fields(job.job_id),
        }
        get_job_logger(job.job_id, job.output_dir).info(
            "\n%s",
            format_task_summary(job, summary),
        )
        log_task_summary(job, summary)

    def _stage_summary_fields(self, job_id: str) -> dict[str, float]:
        return {
            f"stage_{_metric_key(stage)}_seconds": round(elapsed_seconds, 3)
            for stage, elapsed_seconds in self._operation_stage_metrics.get(
                job_id,
                {},
            ).items()
        }

    def _llm_summary_fields(self, job_id: str) -> dict[str, int | float]:
        traces = self._operation_llm_metrics.get(job_id, {})
        fields: dict[str, int | float] = {
            "llm_calls": 0,
            "llm_input_tokens": 0,
            "llm_output_tokens": 0,
            "llm_total_tokens": 0,
        }
        for trace_name, metrics in traces.items():
            trace_key = _metric_key(trace_name)
            fields[f"llm_{trace_key}_calls"] = metrics["calls"]
            fields[f"llm_{trace_key}_input_tokens"] = metrics["input_tokens"]
            fields[f"llm_{trace_key}_output_tokens"] = metrics["output_tokens"]
            fields[f"llm_{trace_key}_total_tokens"] = metrics["total_tokens"]
            fields["llm_calls"] += metrics["calls"]
            fields["llm_input_tokens"] += metrics["input_tokens"]
            fields["llm_output_tokens"] += metrics["output_tokens"]
            fields["llm_total_tokens"] += metrics["total_tokens"]
        translation_calls = traces.get("translate_subtitles", {}).get("calls", 0)
        repair_calls = traces.get("translate_subtitles_repair", {}).get("calls", 0)
        if translation_calls:
            fields["llm_translate_subtitles_repair_ratio"] = round(
                repair_calls / translation_calls,
                3,
            )
        for trace_name, skipped_count in self._operation_llm_skipped.get(
            job_id,
            {},
        ).items():
            fields[f"llm_{_metric_key(trace_name)}_skipped"] = skipped_count
        return fields

    def _window_plan_summary_fields(self, job_id: str) -> dict[str, Any]:
        traces = self._operation_window_plan.get(job_id, {})
        if not traces:
            return {}
        summary: dict[str, Any] = {}
        for trace_name, fields in traces.items():
            trace_key = _metric_key(trace_name)
            summary.update(
                {
                    f"window_{trace_key}_transcript_scope": fields.get(
                        "transcript_scope"
                    ),
                    f"window_{trace_key}_transcript_is_complete": fields.get(
                        "transcript_is_complete"
                    ),
                    f"window_{trace_key}_original_segments": fields.get(
                        "original_segments"
                    ),
                    f"window_{trace_key}_provided_segments": fields.get(
                        "provided_segments"
                    ),
                    f"window_{trace_key}_count": fields.get("window_count"),
                    f"window_{trace_key}_prompt_char_budget": fields.get(
                        "prompt_char_budget"
                    ),
                    f"window_{trace_key}_estimated_transcript_chars": fields.get(
                        "estimated_transcript_chars"
                    ),
                    f"window_{trace_key}_provided_transcript_chars": fields.get(
                        "provided_transcript_chars"
                    ),
                    f"window_{trace_key}_selection_hint_count": fields.get(
                        "selection_hint_count"
                    ),
                    f"window_{trace_key}_selection_low_waste_windows": fields.get(
                        "selection_low_waste_windows"
                    ),
                    f"window_{trace_key}_selection_high_waste_windows": fields.get(
                        "selection_high_waste_windows"
                    ),
                }
            )
        return summary

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
        return self.stage_store.read_optional(
            self._stage_manifest_path(render_dir)
        ) or StageManifest(
            operation_id=job_id,
            render_id=render_id,
            candidate_id=candidate_id,
        )

    def _load_stage_manifest_or_new(
        self,
        *,
        render_dir: Path,
        job_id: str,
        render_id: str,
        candidate_id: str | None,
    ) -> StageManifest:
        try:
            return self._load_stage_manifest(
                render_dir=render_dir,
                job_id=job_id,
                render_id=render_id,
                candidate_id=candidate_id,
            )
        except InsightCastError:
            return StageManifest(
                operation_id=job_id,
                render_id=render_id,
                candidate_id=candidate_id,
            )

    @staticmethod
    def _latest_completed_stage(
        manifest: StageManifest,
        stage: PipelineStage,
    ) -> StageRecord | None:
        for record in reversed(manifest.stages):
            if record.stage == stage and record.status is StageStatus.COMPLETED:
                return record
        return None

    @staticmethod
    def _stage_artifact_path(record: StageRecord | None, key: str) -> Path | None:
        if record is None:
            return None
        artifact = record.artifacts.get(key)
        if artifact is None:
            return None
        return artifact

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

    def _start_stage_record(
        self,
        *,
        render_dir: Path,
        manifest: StageManifest,
        stage: PipelineStage,
        resume_strategy: str,
        fresh: bool,
        reused: bool = False,
    ) -> StageManifest:
        return self._append_stage_record(
            render_dir=render_dir,
            manifest=manifest,
            record=StageRecord(
                stage=stage,
                status=StageStatus.RUNNING,
                started_at=self.clock(),
                resume_strategy=resume_strategy,
                fresh=fresh,
                reused=reused,
            ),
        )

    def _finish_stage_record(
        self,
        *,
        render_dir: Path,
        manifest: StageManifest,
        stage: PipelineStage,
        status: StageStatus,
        artifacts: dict[str, Path] | None = None,
        error: JobError | None = None,
        resume_strategy: str | None = None,
    ) -> StageManifest:
        completed_at = self.clock()
        elapsed_seconds = self._stage_elapsed_seconds(
            manifest.operation_id,
            stage,
        )
        for record in reversed(manifest.stages):
            if record.stage == stage and record.status is StageStatus.RUNNING:
                record.status = status
                record.completed_at = completed_at
                if elapsed_seconds is not None:
                    record.elapsed_seconds = elapsed_seconds
                elif record.started_at is not None:
                    record.elapsed_seconds = (
                        completed_at - record.started_at
                    ).total_seconds()
                if artifacts is not None:
                    record.artifacts = artifacts
                record.error = error
                if resume_strategy is not None:
                    record.resume_strategy = resume_strategy
                self.stage_store.write(self._stage_manifest_path(render_dir), manifest)
                return manifest
        return self._append_stage_record(
            render_dir=render_dir,
            manifest=manifest,
            record=StageRecord(
                stage=stage,
                status=status,
                completed_at=completed_at,
                artifacts=artifacts or {},
                resume_strategy=(
                    resume_strategy or f"rerun render to resume from {stage.value}"
                ),
                error=error,
            ),
        )

    def _stage_elapsed_seconds(
        self,
        job_id: str,
        stage: PipelineStage,
    ) -> float | None:
        elapsed_seconds = self._operation_stage_metrics.get(job_id, {}).get(stage.value)
        if elapsed_seconds is None:
            return None
        return round(elapsed_seconds, 3)

    def _finalize_provisional_output(
        self,
        job_id: str,
        provisional_dir: Path,
        final_dir: Path,
    ) -> None:
        resolved = provisional_dir.resolve()
        jobs_root = (self.output_root / "jobs").resolve()
        if resolved.parent != jobs_root or "_pending_" not in resolved.name:
            return
        final = final_dir.resolve()
        final.mkdir(parents=True, exist_ok=True)
        provisional_log = resolved / "pipeline.log"
        final_log = get_job_log_path(job_id, final)
        final_log.parent.mkdir(parents=True, exist_ok=True)
        if provisional_log.exists():
            if final_log.exists():
                with final_log.open("a", encoding="utf-8") as destination:
                    destination.write(provisional_log.read_text(encoding="utf-8"))
                provisional_log.unlink()
            else:
                provisional_log.replace(final_log)
        job_state = resolved / "job_state.json"
        if job_state.exists():
            job_state.unlink()
        if resolved.exists():
            resolved.rmdir()

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

    def _write_failed_analysis_manifest(self, job: AnalysisJob) -> None:
        if job.video_id is None or job.transcript_id is None or job.error is None:
            return
        candidate_count, minimum, maximum = self._analysis_options[job.job_id]
        self.video_store.write_analysis(
            video_id=job.video_id,
            analysis_id=job.analysis_id,
            operation_id=job.job_id,
            created_at=job.created_at,
            completed_at=job.updated_at,
            normalized_source_url=job.normalized_youtube_url,
            transcript_id=job.transcript_id,
            curator_model="",
            prompt_version="",
            candidate_count=candidate_count,
            min_duration_seconds=minimum * 60,
            max_duration_seconds=maximum * 60,
            candidates=job.candidates,
            state=AnalysisState.FAILED,
            log_path=Path("logs") / f"{job.job_id}.log",
            error=job.error,
        )

    def _write_failed_direct_render_manifest(self, job: DirectRenderJob) -> None:
        if (
            job.video_id is None
            or job.render_id is None
            or job.transcript_id is None
            or job.error is None
        ):
            return
        render = self.video_store.write_render(
            video_id=job.video_id,
            render_id=job.render_id,
            operation_id=job.job_id,
            kind=RenderKind.CUSTOM,
            analysis_id=None,
            candidate_id=None,
            start_seconds=job.start_seconds,
            end_seconds=job.end_seconds,
            source_fingerprint=self._source_fingerprint_for_job(job),
            transcript_id=job.transcript_id,
            render_config={"subtitle_language": "zh-TW", "bilingual": True},
            created_at=job.created_at,
            completed_at=job.updated_at,
            render_state=RenderState.FAILED,
            publish_state=PublishState.NOT_UPLOADED,
            log_path=Path("logs") / f"{job.job_id}.log",
            render_error=job.error,
        )
        job.output_dir = render.directory
        job.manifest_path = render.manifest_path

    def _mark_failed_direct_render_stage(self, job: DirectRenderJob) -> None:
        if job.render_id is None or job.error is None:
            return
        failed_stage = job.error.stage or "rendering"
        known_stages = {stage.value for stage in PipelineStage}
        if failed_stage not in known_stages:
            return
        stage_path = self._stage_manifest_path(job.output_dir)
        if not stage_path.exists():
            return
        manifest = self._load_stage_manifest_or_new(
            render_dir=job.output_dir,
            job_id=job.job_id,
            render_id=job.render_id,
            candidate_id=None,
        )
        self._finish_stage_record(
            render_dir=job.output_dir,
            manifest=manifest,
            stage=PipelineStage(failed_stage),
            status=StageStatus.FAILED,
            error=job.error,
            resume_strategy=f"rerun render to resume from {failed_stage}",
        )

    @staticmethod
    def _as_job_error(exc: Exception, stage: str | None) -> JobError:
        resolved_stage = getattr(exc, "stage", None) or stage
        if isinstance(exc, InsightCastError):
            return JobError(
                stage=resolved_stage,
                error_code=exc.error_code,
                message=exc.message,
                details=exc.details,
            )
        return JobError(
            stage=resolved_stage,
            error_code=ErrorCode.VIDEO_RENDER_FAILED,
            message="Candidate rendering failed.",
            details={"reason": str(exc)},
        )

    @staticmethod
    def _transcript_excerpt(transcript: Transcript, candidate: Candidate) -> str:
        return " ".join(
            segment.text
            for segment in transcript.segments
            if segment.end_seconds > candidate.start_seconds
            and segment.start_seconds < candidate.end_seconds
        )

    @staticmethod
    def _completed_artifacts(
        job: AnalysisJob,
        candidate_id: str,
    ) -> RenderArtifacts | None:
        for batch in reversed(job.render_batches):
            result = batch.candidate_results.get(candidate_id)
            if result is not None and result.artifacts is not None:
                return result.artifacts
        return None

    @staticmethod
    def _job_not_found(job_id: str) -> InsightCastError:
        return InsightCastError(
            ErrorCode.JOB_NOT_FOUND,
            "The requested job does not exist in this server process.",
            details={"job_id": job_id},
        )


def _metric_key(value: str) -> str:
    return "".join(character if character.isalnum() else "_" for character in value)
