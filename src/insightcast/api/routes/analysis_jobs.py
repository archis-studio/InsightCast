from pathlib import Path
from typing import Any

from fastapi import APIRouter, status
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from insightcast.api.dependencies import JobServiceDependency, SettingsDependency
from insightcast.api.schemas import (
    AnalysisJobCreateRequest,
    AnalysisJobResponse,
    ErrorResponse,
    QueuedJobResponse,
    RenderBatchListResponse,
    RenderBatchResponse,
    RenderCreateRequest,
    ResolvedCandidateOptions,
)
from insightcast.core.exceptions import InsightCastError
from insightcast.domain.enums import ErrorCode
from insightcast.domain.models import (
    AnalysisJob,
    CandidateSelectionRequest,
    RenderBatch,
)

router = APIRouter(prefix="/api/v1/analysis-jobs", tags=["analysis"])
ERROR_RESPONSES = {
    400: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    500: {"model": ErrorResponse},
}


def _render_artifacts(batch: RenderBatch) -> dict[str, Any]:
    return {
        candidate_id: result.artifacts.model_dump(mode="json")
        for candidate_id, result in batch.candidate_results.items()
        if result.artifacts is not None
    }


def _job_artifacts(job: AnalysisJob) -> dict[str, Any]:
    artifacts: dict[str, Any] = {}
    if job.source_artifacts is not None:
        artifacts["source"] = job.source_artifacts.model_dump(mode="json")
    rendered = {
        batch.render_id: _render_artifacts(batch)
        for batch in job.render_batches
        if _render_artifacts(batch)
    }
    if rendered:
        artifacts["renders"] = rendered
    return artifacts


@router.post(
    "",
    response_model=QueuedJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue a YouTube analysis job",
    responses=ERROR_RESPONSES,
)
async def create_analysis_job(
    request: AnalysisJobCreateRequest,
    service: JobServiceDependency,
    settings: SettingsDependency,
) -> QueuedJobResponse:
    try:
        options = ResolvedCandidateOptions(
            candidate_count=(
                request.candidate_count
                if request.candidate_count is not None
                else settings.default_candidate_count
            ),
            min_duration_minutes=(
                request.min_duration_minutes
                if request.min_duration_minutes is not None
                else settings.default_min_duration_minutes
            ),
            max_duration_minutes=(
                request.max_duration_minutes
                if request.max_duration_minutes is not None
                else settings.default_max_duration_minutes
            ),
        )
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc
    job = await service.create_analysis_job(
        request.youtube_url,
        candidate_count=options.candidate_count,
        min_duration_minutes=options.min_duration_minutes,
        max_duration_minutes=options.max_duration_minutes,
        force_reanalyze=request.force_reanalyze,
    )
    return QueuedJobResponse(
        job_id=job.job_id,
        status=job.status,
        message=job.message,
        artifacts=_job_artifacts(job),
        created_at=job.created_at,
    )


@router.get(
    "/{job_id}",
    response_model=AnalysisJobResponse,
    summary="Get analysis job state",
    responses=ERROR_RESPONSES,
)
async def get_analysis_job(
    job_id: str,
    service: JobServiceDependency,
) -> AnalysisJobResponse:
    job = service.get_analysis_job(job_id)
    return AnalysisJobResponse(
        job_id=job.job_id,
        status=job.status,
        message=job.message,
        candidates=job.candidates,
        render_batches=job.render_batches,
        error=job.error,
        artifacts=_job_artifacts(job),
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


@router.post(
    "/{job_id}/renders",
    response_model=RenderBatchResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue candidate rendering",
    responses=ERROR_RESPONSES,
)
async def create_render(
    job_id: str,
    request: RenderCreateRequest,
    service: JobServiceDependency,
) -> RenderBatchResponse:
    batch = await service.create_render(
        job_id,
        CandidateSelectionRequest(
            candidate_ids=request.candidate_ids,
            force_render=request.force_render,
        ),
    )
    return RenderBatchResponse(
        job_id=job_id,
        render_id=batch.render_id,
        status=batch.status,
        message=batch.message,
        candidate_ids=batch.candidate_ids,
        artifacts=_render_artifacts(batch),
        created_at=batch.created_at,
        updated_at=batch.updated_at,
    )


@router.get(
    "/{job_id}/renders",
    response_model=RenderBatchListResponse,
    summary="List analysis render batches",
    responses=ERROR_RESPONSES,
)
async def list_renders(
    job_id: str,
    service: JobServiceDependency,
) -> RenderBatchListResponse:
    batches = service.list_render_batches(job_id)
    return RenderBatchListResponse(
        job_id=job_id,
        message=f"{len(batches)} render batch(es) found.",
        artifacts={
            batch.render_id: _render_artifacts(batch)
            for batch in batches
            if _render_artifacts(batch)
        },
        render_batches=batches,
    )


@router.post(
    "/{job_id}/youtube-uploads",
    response_model=ErrorResponse,
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
    summary="Validate artifacts for a future YouTube upload",
    responses=ERROR_RESPONSES,
)
async def upload_analysis_stub(
    job_id: str,
    service: JobServiceDependency,
) -> ErrorResponse:
    job = service.get_analysis_job(job_id)
    for batch in reversed(job.render_batches):
        for result in batch.candidate_results.values():
            if result.artifacts is None:
                continue
            video = Path(result.artifacts.burned_video)
            metadata = Path(result.artifacts.youtube_metadata)
            if video.exists() and metadata.exists():
                raise InsightCastError(
                    ErrorCode.UPLOAD_NOT_IMPLEMENTED,
                    "YouTube uploading is not implemented in the MVP.",
                    details={
                        "burned_video": str(video.resolve()),
                        "youtube_metadata": str(metadata.resolve()),
                    },
                )
    raise InsightCastError(
        ErrorCode.VIDEO_RENDER_FAILED,
        "No publishable rendered video and metadata were found.",
        details={"job_id": job_id},
    )
