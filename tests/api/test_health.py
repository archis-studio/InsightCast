import asyncio
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from insightcast.api.app import _server_log_config, create_app
from insightcast.api.runtime import build_runtime
from insightcast.core.config import Settings


class FakeFfmpeg:
    def __init__(self) -> None:
        self.probes = 0

    async def probe(self) -> None:
        self.probes += 1


class FakeService:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[object] = asyncio.Queue()

    async def process(self, _item: object) -> None:
        return None


def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        openai_api_key="sk-test-value",
        output_dir=tmp_path / "outputs",
        work_dir=tmp_path / ".work",
    )


def test_health_reports_dependency_readiness_and_lifespan_probes_once(
    tmp_path: Path,
) -> None:
    ffmpeg = FakeFfmpeg()
    app = create_app(
        settings=settings(tmp_path),
        service=FakeService(),
        ffmpeg=ffmpeg,
    )

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "message": "Insight Cast is ready.",
        "dependencies": {"ffmpeg": "ready", "queue_worker": "ready"},
    }
    assert ffmpeg.probes == 1


def test_runtime_uses_ytdlp_from_current_python_environment(tmp_path: Path) -> None:
    runtime = build_runtime(settings(tmp_path))

    assert Path(runtime.service.source_engine.ytdlp.executable) == Path(
        sys.executable
    ).with_name("yt-dlp")


def test_server_log_config_routes_only_task_logger_to_default_console() -> None:
    config = _server_log_config()

    assert config["loggers"]["insightcast.task"] == {
        "handlers": ["default"],
        "level": "INFO",
        "propagate": False,
    }
    assert config["loggers"]["uvicorn.access"]["handlers"] == ["access"]
