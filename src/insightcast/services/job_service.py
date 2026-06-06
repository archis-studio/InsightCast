import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

from insightcast.core.exceptions import InsightCastError
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
from insightcast.utils.files import sanitize_filename
from insightcast.utils.youtube import normalize_youtube_url


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

        self.analysis_jobs: dict[str, AnalysisJob] = {}
        self.direct_jobs: dict[str, DirectRenderJob] = {}
        self.latest_analysis_by_url: dict[str, str] = {}
        self._analysis_options: dict[str, tuple[int, float, float]] = {}
        self._transcripts: dict[str, Transcript] = {}
        self._source_metadata: dict[str, Any] = {}
        self.processed_work: list[WorkItem] = []

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
            return self.analysis_jobs[self.latest_analysis_by_url[normalized_url]]
        job_id = self.id_factory()
        created_at = self.clock()
        output_dir = self.output_root / (
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

    async def create_render(
        self,
        job_id: str,
        request: CandidateSelectionRequest,
    ) -> RenderBatch:
        job = self.get_analysis_job(job_id)
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

        render_id = self.id_factory()
        created_at = self.clock()
        output_dir = job.output_dir / "renders" / (
            f"{created_at:%Y%m%d-%H%M%S}-{created_at.microsecond:06d}"
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
                existing = self._completed_artifacts(job, candidate_id)
                if existing is not None:
                    batch.candidate_results[candidate_id] = CandidateRenderResult(
                        candidate_id=candidate_id,
                        artifacts=existing,
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
        output_dir = self.output_root / (
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
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            created_at=created_at,
            updated_at=created_at,
        )
        self.direct_jobs[job_id] = job
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
        if item.kind == WorkKind.ANALYSIS:
            await self._process_analysis(item.job_id)
        elif item.kind == WorkKind.ANALYSIS_RENDER:
            assert item.render_id is not None
            await self._process_analysis_render(item.job_id, item.render_id)
        else:
            await self._process_direct_render(item.job_id)

    async def _process_analysis(self, job_id: str) -> None:
        job = self.get_analysis_job(job_id)
        try:
            self._set_status(job, JobStatus.INGESTING, "Downloading the source video.")
            source = await self.source_engine.ingest(
                youtube_url=job.normalized_youtube_url,
                job_id=job.job_id,
                created_at=job.created_at,
                output_root=self.output_root,
                direct=False,
            )
            job.output_dir = source.output_dir
            job.source_artifacts = source.source_artifacts
            self._source_metadata[job_id] = source.metadata
            self._set_status(job, JobStatus.TRANSCRIBING, "Transcribing English audio.")
            transcript = await self.transcription_client.transcribe(
                source.source_artifacts.source_audio
            )
            self._transcripts[job_id] = transcript
            analysis_dir = job.output_dir / "analysis"
            transcript_path = analysis_dir / "transcript.json"
            self.writer.write_json(transcript_path, transcript)
            job.source_artifacts.transcript = transcript_path.resolve()

            self._set_status(job, JobStatus.CURATING, "Selecting candidate idea arcs.")
            candidate_count, minimum, maximum = self._analysis_options[job_id]
            result = await self.curator_engine.curate(
                transcript=transcript,
                candidate_count=candidate_count,
                min_duration_minutes=minimum,
                max_duration_minutes=maximum,
            )
            job.candidates = result.candidates
            candidates_path = analysis_dir / "candidates.json"
            self.writer.write_json(candidates_path, result)
            job.source_artifacts.candidates = candidates_path.resolve()
            self._set_status(
                job,
                JobStatus.WAITING_SELECTION,
                f"{len(job.candidates)} candidates are ready for selection.",
            )
        except InsightCastError as exc:
            self._fail_job(job, exc)
        except Exception as exc:
            self._fail_job(
                job,
                InsightCastError(
                    ErrorCode.TRANSCRIPTION_FAILED,
                    "Analysis pipeline failed.",
                    details={"reason": str(exc)},
                    stage="analysis",
                ),
            )

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
            candidate_dir = batch.output_dir / f"candidate-{candidate_id.lower()}"
            source_base = job.source_artifacts.source_video.stem.removesuffix(".source")
            base_name = f"{source_base}.{candidate_id.lower()}"
            try:
                clip = await self.clip_engine.render(
                    source_video=job.source_artifacts.source_video,
                    transcript_segments=transcript.segments,
                    selection=candidate,
                    output_dir=candidate_dir,
                    work_dir=self.work_root / job.job_id / batch.render_id,
                    base_name=base_name,
                )
                metadata_path = candidate_dir / f"{base_name}.youtube-metadata.json"
                excerpt = self._transcript_excerpt(transcript, candidate)
                await self.publish_engine.generate(
                    source_metadata=source_metadata,
                    summary=candidate.summary,
                    transcript_excerpt=excerpt,
                    destination=metadata_path,
                )
                batch.candidate_results[candidate_id] = CandidateRenderResult(
                    candidate_id=candidate_id,
                    artifacts=RenderArtifacts(
                        traditional_chinese_srt=clip.traditional_chinese_srt.resolve(),
                        bilingual_ass=clip.bilingual_ass.resolve(),
                        burned_video=clip.burned_video.resolve(),
                        youtube_metadata=metadata_path.resolve(),
                    ),
                )
            except Exception as exc:
                error = self._as_job_error(exc, "rendering")
                batch.candidate_results[candidate_id] = CandidateRenderResult(
                    candidate_id=candidate_id,
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

    async def _process_direct_render(self, job_id: str) -> None:
        job = self.get_direct_render_job(job_id)
        try:
            self._set_status(job, JobStatus.INGESTING, "Downloading the source video.")
            source = await self.source_engine.ingest(
                youtube_url=job.normalized_youtube_url,
                job_id=job.job_id,
                created_at=job.created_at,
                output_root=self.output_root,
                direct=True,
            )
            job.output_dir = source.output_dir
            job.source_artifacts = source.source_artifacts
            self._set_status(job, JobStatus.TRANSCRIBING, "Transcribing English audio.")
            transcript = await self.transcription_client.transcribe(
                source.source_artifacts.source_audio
            )
            self.writer.write_json(job.output_dir / "analysis" / "transcript.json", transcript)
            self._set_status(job, JobStatus.RENDERING, "Rendering the requested time range.")
            selection = Candidate(
                candidate_id="custom",
                start_seconds=job.start_seconds,
                end_seconds=job.end_seconds,
                suggested_title=f"{source.metadata.title} clip",
                selection_reason="User-selected direct render range.",
                summary="Direct render selected by the user.",
            )
            base_name = (
                f"{sanitize_filename(source.metadata.title)}.custom"
            )
            render_dir = job.output_dir / "render"
            clip = await self.clip_engine.render(
                source_video=source.source_artifacts.source_video,
                transcript_segments=transcript.segments,
                selection=selection,
                output_dir=render_dir,
                work_dir=self.work_root / job.job_id,
                base_name=base_name,
            )
            metadata_path = render_dir / f"{base_name}.youtube-metadata.json"
            await self.publish_engine.generate(
                source_metadata=source.metadata,
                summary=selection.summary,
                transcript_excerpt=self._transcript_excerpt(transcript, selection),
                destination=metadata_path,
            )
            job.artifacts = RenderArtifacts(
                traditional_chinese_srt=clip.traditional_chinese_srt.resolve(),
                bilingual_ass=clip.bilingual_ass.resolve(),
                burned_video=clip.burned_video.resolve(),
                youtube_metadata=metadata_path.resolve(),
            )
            self._set_status(
                job,
                JobStatus.COMPLETED,
                "Direct render completed successfully.",
            )
        except InsightCastError as exc:
            self._fail_job(job, exc)
        except Exception as exc:
            self._fail_job(
                job,
                InsightCastError(
                    ErrorCode.VIDEO_RENDER_FAILED,
                    "Direct render pipeline failed.",
                    details={"reason": str(exc)},
                    stage="rendering",
                ),
            )

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
        self.writer.write_job(job)

    def _fail_job(
        self,
        job: AnalysisJob | DirectRenderJob,
        error: InsightCastError,
    ) -> None:
        job.status = JobStatus.FAILED
        job.message = error.message
        job.error = self._as_job_error(error, error.stage)
        self._touch(job)

    @staticmethod
    def _as_job_error(exc: Exception, stage: str | None) -> JobError:
        if isinstance(exc, InsightCastError):
            return JobError(
                stage=exc.stage or stage,
                error_code=exc.error_code,
                message=exc.message,
                details=exc.details,
            )
        return JobError(
            stage=stage,
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
