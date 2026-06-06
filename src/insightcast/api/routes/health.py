from fastapi import APIRouter, Request

from insightcast.api.schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Check service readiness",
)
async def health(request: Request) -> HealthResponse:
    return HealthResponse(
        status="ok",
        message="Insight Cast is ready.",
        dependencies={
            "ffmpeg": request.app.state.ffmpeg_status,
            "queue_worker": request.app.state.queue_worker_status,
        },
    )

