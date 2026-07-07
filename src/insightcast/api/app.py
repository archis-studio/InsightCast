import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from copy import deepcopy
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from uvicorn.config import LOGGING_CONFIG

from insightcast.api.routes import analysis_jobs, direct_render_jobs, health, videos
from insightcast.api.runtime import build_runtime
from insightcast.core.config import Settings, get_settings
from insightcast.core.exceptions import InsightCastError
from insightcast.domain.enums import ErrorCode
from insightcast.infrastructure.ffmpeg_client import FfmpegClient
from insightcast.services.job_service import JobService
from insightcast.services.queue_worker import QueueWorker


def _server_log_config() -> dict[str, Any]:
    config = deepcopy(LOGGING_CONFIG)
    config["loggers"]["insightcast.task"] = {
        "handlers": ["default"],
        "level": "INFO",
        "propagate": False,
    }
    return config


def create_app(
    *,
    settings: Settings | None = None,
    service: JobService | Any | None = None,
    ffmpeg: FfmpegClient | Any | None = None,
) -> FastAPI:
    resolved_settings = settings or get_settings()
    if service is None or ffmpeg is None:
        runtime = build_runtime(resolved_settings)
        service = service or runtime.service
        ffmpeg = ffmpeg or runtime.ffmpeg

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.ffmpeg_status = "checking"
        app.state.queue_worker_status = "starting"
        await ffmpeg.probe()
        app.state.ffmpeg_status = "ready"
        worker = QueueWorker(queue=service.queue, service=service)
        worker_task = asyncio.create_task(worker.run(), name="insightcast-fifo-worker")
        app.state.worker_task = worker_task
        app.state.queue_worker_status = "ready"
        try:
            yield
        finally:
            app.state.queue_worker_status = "stopped"
            worker_task.cancel()
            with suppress(asyncio.CancelledError):
                await worker_task

    app = FastAPI(
        title="Insight Cast API",
        version="0.1.0",
        description="Local-first YouTube knowledge curation and bilingual rendering.",
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.state.job_service = service
    app.state.ffmpeg_client = ffmpeg
    app.state.ffmpeg_status = "not_started"
    app.state.queue_worker_status = "not_started"

    @app.exception_handler(InsightCastError)
    async def insightcast_error_handler(
        _request: Request,
        exc: InsightCastError,
    ) -> JSONResponse:
        status_code = {
            ErrorCode.JOB_NOT_FOUND: 404,
            ErrorCode.UPLOAD_NOT_IMPLEMENTED: 501,
            ErrorCode.INVALID_YOUTUBE_URL: 400,
            ErrorCode.INVALID_TIME_RANGE: 400,
            ErrorCode.CANDIDATE_NOT_FOUND: 400,
            ErrorCode.INVALID_JOB_STATE: 409,
            ErrorCode.RENDER_NOT_FOUND: 404,
            ErrorCode.RENDER_NOT_PUBLISHABLE: 409,
        }.get(exc.error_code, 500)
        return JSONResponse(
            status_code=status_code,
            content={
                "error_code": exc.error_code.value,
                "message": exc.message,
                "details": exc.details,
            },
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(
        _request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=jsonable_encoder(
                {
                    "error_code": "INVALID_REQUEST",
                    "message": "Request validation failed.",
                    "details": {"errors": exc.errors()},
                },
                custom_encoder={Exception: str},
            ),
        )

    app.include_router(health.router)
    app.include_router(analysis_jobs.router)
    app.include_router(direct_render_jobs.router)
    app.include_router(videos.router)
    return app


def run() -> None:
    settings = get_settings()
    uvicorn.run(
        create_app(settings=settings),
        host=settings.api_host,
        port=settings.api_port,
        log_config=_server_log_config(),
    )
