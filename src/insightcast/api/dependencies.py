from typing import Annotated

from fastapi import Request
from fastapi.params import Depends

from insightcast.core.config import Settings
from insightcast.services.job_service import JobService
from insightcast.storage.file_job_writer import FileJobWriter
from insightcast.storage.video_store import VideoStore


def get_job_service(request: Request) -> JobService:
    return request.app.state.job_service


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_video_store(request: Request) -> VideoStore:
    video_store = getattr(request.app.state, "video_store", None)
    if video_store is not None:
        return video_store
    service_store = getattr(request.app.state.job_service, "video_store", None)
    if service_store is not None:
        return service_store
    return VideoStore(request.app.state.settings.output_dir, FileJobWriter())


JobServiceDependency = Annotated[JobService, Depends(get_job_service)]
SettingsDependency = Annotated[Settings, Depends(get_app_settings)]
VideoStoreDependency = Annotated[VideoStore, Depends(get_video_store)]
