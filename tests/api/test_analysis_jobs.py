import asyncio
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from insightcast.api.app import create_app
from insightcast.core.config import Settings
from insightcast.core.exceptions import InsightCastError
from insightcast.domain.enums import ErrorCode, JobStatus, JobType
from insightcast.domain.models import AnalysisJob, RenderBatch


class FakeFfmpeg:
    async def probe(self) -> None:
        return None


class FakeService:
    def __init__(self, tmp_path: Path) -> None:
        self.queue: asyncio.Queue[object] = asyncio.Queue()
        self.tmp_path = tmp_path
        self.created: list[dict[str, object]] = []

    async def process(self, _item: object) -> None:
        return None

    async def create_analysis_job(self, youtube_url: str, **kwargs: object) -> AnalysisJob:
        self.created.append({"youtube_url": youtube_url, **kwargs})
        return AnalysisJob(
            job_id="analysis-1",
            job_type=JobType.ANALYSIS,
            original_youtube_url=youtube_url,
            normalized_youtube_url="https://www.youtube.com/watch?v=abc123DEF_-",
            status=JobStatus.QUEUED,
            message="Analysis job is queued.",
            output_dir=(self.tmp_path / "analysis").resolve(),
            created_at=datetime(2026, 6, 6, tzinfo=UTC),
            updated_at=datetime(2026, 6, 6, tzinfo=UTC),
        )

    def get_analysis_job(self, job_id: str) -> AnalysisJob:
        if job_id == "missing":
            raise InsightCastError(ErrorCode.JOB_NOT_FOUND, "Job not found.")
        return AnalysisJob(
            job_id=job_id,
            job_type=JobType.ANALYSIS,
            original_youtube_url="https://youtu.be/abc123DEF_-",
            normalized_youtube_url="https://www.youtube.com/watch?v=abc123DEF_-",
            status=JobStatus.WAITING_SELECTION,
            message="2 candidates are ready.",
            output_dir=(self.tmp_path / "analysis").resolve(),
        )

    async def create_render(self, job_id: str, request: object) -> RenderBatch:
        return RenderBatch(
            render_id="render-1",
            candidate_ids=request.candidate_ids,
            status=JobStatus.QUEUED,
            message="Render batch is queued.",
            output_dir=(self.tmp_path / job_id / "render").resolve(),
        )

    def list_render_batches(self, _job_id: str) -> list[RenderBatch]:
        return []


def make_client(tmp_path: Path) -> tuple[TestClient, FakeService]:
    service = FakeService(tmp_path)
    app = create_app(
        settings=Settings(
            _env_file=None,
            openai_api_key="sk-test-value",
            output_dir=tmp_path / "outputs",
            work_dir=tmp_path / ".work",
        ),
        service=service,
        ffmpeg=FakeFfmpeg(),
    )
    return TestClient(app), service


def test_analysis_routes_queue_get_render_and_list(tmp_path: Path) -> None:
    client, service = make_client(tmp_path)
    with client:
        created = client.post(
            "/api/v1/analysis-jobs",
            json={
                "youtube_url": "https://youtu.be/abc123DEF_-",
                "candidate_count": 2,
                "min_duration_minutes": 8,
                "max_duration_minutes": 12,
                "force_reanalyze": False,
            },
        )
        fetched = client.get("/api/v1/analysis-jobs/analysis-1")
        render = client.post(
            "/api/v1/analysis-jobs/analysis-1/renders",
            json={"candidate_ids": "A"},
        )
        batches = client.get("/api/v1/analysis-jobs/analysis-1/renders")

    assert created.status_code == 202
    assert created.json()["job_id"] == "analysis-1"
    assert created.json()["artifacts"] == {}
    assert service.created[0]["candidate_count"] == 2
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "WAITING_SELECTION"
    assert "artifacts" in fetched.json()
    assert render.status_code == 202
    assert render.json()["render_id"] == "render-1"
    assert batches.json()["render_batches"] == []


def test_job_not_found_uses_stable_flat_error_shape(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)
    with client:
        response = client.get("/api/v1/analysis-jobs/missing")

    assert response.status_code == 404
    assert response.json() == {
        "error_code": "JOB_NOT_FOUND",
        "message": "Job not found.",
        "details": {},
    }

