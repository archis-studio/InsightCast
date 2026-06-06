import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from openai import OpenAI

from insightcast.api.routes import analysis_jobs, direct_render_jobs, health
from insightcast.core.config import Settings, get_settings
from insightcast.core.exceptions import InsightCastError
from insightcast.domain.enums import ErrorCode
from insightcast.engines.clip_engine import ClipEngine
from insightcast.engines.curator_engine import CuratorEngine
from insightcast.engines.lingo_engine import LingoEngine
from insightcast.engines.publish_engine import PublishEngine
from insightcast.engines.source_engine import SourceEngine
from insightcast.infrastructure.ffmpeg_client import FfmpegClient
from insightcast.infrastructure.openai_client import StructuredOpenAIClient
from insightcast.infrastructure.transcription.local_whisper_client import LocalWhisperClient
from insightcast.infrastructure.transcription.openai_transcription_client import (
    OpenAITranscriptionClient,
)
from insightcast.infrastructure.ytdlp_client import YtDlpClient
from insightcast.services.job_service import JobService
from insightcast.services.queue_worker import QueueWorker
from insightcast.storage.file_job_writer import FileJobWriter


def _build_runtime(settings: Settings) -> tuple[JobService, FfmpegClient]:
    ffmpeg = FfmpegClient(ffmpeg_bin=settings.ffmpeg_bin, crf=settings.video_crf)
    ytdlp = YtDlpClient(max_height=settings.video_max_height)
    sdk = OpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        timeout=settings.openai_timeout_seconds,
        max_retries=0,
    )
    structured = StructuredOpenAIClient(
        sdk,
        timeout_seconds=settings.openai_timeout_seconds,
        max_retries=settings.openai_max_retries,
    )
    if settings.transcription_provider == "local":
        transcription = LocalWhisperClient(
            model_size=settings.whisper_model_size,
            device=settings.whisper_device,
        )
    else:
        transcription = OpenAITranscriptionClient(
            sdk.audio.transcriptions,
            model=settings.openai_transcription_model,
            max_upload_mb=settings.openai_transcription_max_upload_mb,
        )
    writer = FileJobWriter()
    source = SourceEngine(ytdlp=ytdlp, ffmpeg=ffmpeg)
    lingo = LingoEngine(
        client=structured,
        model=settings.effective_translation_model,
    )
    service = JobService(
        output_root=settings.output_dir,
        work_root=settings.work_dir,
        source_engine=source,
        transcription_client=transcription,
        curator_engine=CuratorEngine(
            client=structured,
            model=settings.effective_curator_model,
        ),
        clip_engine=ClipEngine(ffmpeg=ffmpeg, lingo=lingo),
        publish_engine=PublishEngine(
            client=structured,
            model=settings.effective_metadata_model,
            writer=writer,
        ),
        writer=writer,
    )
    return service, ffmpeg


def create_app(
    *,
    settings: Settings | None = None,
    service: JobService | Any | None = None,
    ffmpeg: FfmpegClient | Any | None = None,
) -> FastAPI:
    resolved_settings = settings or get_settings()
    if service is None or ffmpeg is None:
        built_service, built_ffmpeg = _build_runtime(resolved_settings)
        service = service or built_service
        ffmpeg = ffmpeg or built_ffmpeg

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
            content={
                "error_code": "INVALID_REQUEST",
                "message": "Request validation failed.",
                "details": {"errors": exc.errors()},
            },
        )

    app.include_router(health.router)
    app.include_router(analysis_jobs.router)
    app.include_router(direct_render_jobs.router)
    return app


def run() -> None:
    settings = get_settings()
    uvicorn.run(
        create_app(settings=settings),
        host=settings.api_host,
        port=settings.api_port,
    )

