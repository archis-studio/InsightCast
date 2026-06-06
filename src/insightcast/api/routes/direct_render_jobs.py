from pathlib import Path
from typing import Any

from fastapi import APIRouter, status

from insightcast.api.dependencies import JobServiceDependency
from insightcast.api.schemas import (
    DirectRenderCreateRequest,
    DirectRenderJobResponse,
    ErrorResponse,
    QueuedJobResponse,
)
from insightcast.core.exceptions import InsightCastError
from insightcast.domain.enums import ErrorCode
from insightcast.domain.models import DirectRenderJob

router = APIRouter(prefix="/api/v1/direct-render-jobs", tags=["direct render"])
ERROR_RESPONSES = {
    400: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    500: {"model": ErrorResponse},
}


def _artifacts(job: DirectRenderJob) -> dict[str, Any]:
    if job.artifacts is None:
        return {}
    return job.artifacts.model_dump(mode="json")


@router.post(
    "",
    response_model=QueuedJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue a direct time-range render",
    responses=ERROR_RESPONSES,
)
async def create_direct_render_job(
    request: DirectRenderCreateRequest,
    service: JobServiceDependency,
) -> QueuedJobResponse:
    try:
        start_seconds, end_seconds = request.parsed_times()
    except ValueError as exc:
        raise InsightCastError(
            ErrorCode.INVALID_TIME_RANGE,
            "start_time and end_time must be valid timecodes or numeric seconds.",
            details={"start_time": request.start_time, "end_time": request.end_time},
        ) from exc
    job = await service.create_direct_render_job(
        request.youtube_url,
        start_seconds=start_seconds,
        end_seconds=end_seconds,
    )
    return QueuedJobResponse(
        job_id=job.job_id,
        status=job.status,
        message=job.message,
        artifacts=_artifacts(job),
        created_at=job.created_at,
    )


@router.get(
    "/{job_id}",
    response_model=DirectRenderJobResponse,
    summary="Get direct render job state",
    responses=ERROR_RESPONSES,
)
async def get_direct_render_job(
    job_id: str,
    service: JobServiceDependency,
) -> DirectRenderJobResponse:
    job = service.get_direct_render_job(job_id)
    return DirectRenderJobResponse(
        job_id=job.job_id,
        status=job.status,
        message=job.message,
        error=job.error,
        artifacts=_artifacts(job),
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


@router.post(
    "/{job_id}/youtube-uploads",
    response_model=ErrorResponse,
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
    summary="Validate artifacts for a future YouTube upload",
    responses=ERROR_RESPONSES,
)
async def upload_direct_stub(
    job_id: str,
    service: JobServiceDependency,
) -> ErrorResponse:
    job = service.get_direct_render_job(job_id)
    if job.artifacts is None:
        raise InsightCastError(
            ErrorCode.VIDEO_RENDER_FAILED,
            "No publishable rendered video and metadata were found.",
            details={"job_id": job_id},
        )
    video = Path(job.artifacts.burned_video)
    metadata = Path(job.artifacts.youtube_metadata)
    if not video.exists() or not metadata.exists():
        raise InsightCastError(
            ErrorCode.VIDEO_RENDER_FAILED,
            "Rendered video or metadata file is missing.",
            details={"burned_video": str(video), "youtube_metadata": str(metadata)},
        )
    raise InsightCastError(
        ErrorCode.UPLOAD_NOT_IMPLEMENTED,
        "YouTube uploading is not implemented in the MVP.",
        details={
            "burned_video": str(video.resolve()),
            "youtube_metadata": str(metadata.resolve()),
        },
    )
