from typing import Annotated

from fastapi import Request
from fastapi.params import Depends

from insightcast.core.config import Settings
from insightcast.services.job_service import JobService


def get_job_service(request: Request) -> JobService:
    return request.app.state.job_service


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


JobServiceDependency = Annotated[JobService, Depends(get_job_service)]
SettingsDependency = Annotated[Settings, Depends(get_app_settings)]
