import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from insightcast.api.app import create_app
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

