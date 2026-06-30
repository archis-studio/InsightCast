import asyncio
import json
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
        self.render_requests: list[object] = []
        self.render_batches: list[RenderBatch] = []

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
            video_id="abc123DEF_-",
            analysis_id="20260606-000000-analys",
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
            video_id="abc123DEF_-",
            analysis_id="20260606-000000-analys",
            progress={
                "stage": "transcription",
                "event": "started",
                "chunk_index": 0,
                "chunk_count": 3,
                "attempt": 1,
                "max_attempts": 3,
            },
        )

    async def create_render(self, job_id: str, request: object) -> RenderBatch:
        self.render_requests.append(request)
        if job_id == "not-ready":
            raise InsightCastError(
                ErrorCode.INVALID_JOB_STATE,
                "Analysis job is not ready for candidate rendering.",
                details={"job_id": job_id, "status": JobStatus.TRANSCRIBING},
            )
        return RenderBatch(
            render_id="render-1",
            candidate_ids=request.candidate_ids,
            status=JobStatus.QUEUED,
            message="Render batch is queued.",
            output_dir=(self.tmp_path / job_id / "render").resolve(),
        )

    def list_render_batches(self, _job_id: str) -> list[RenderBatch]:
        return self.render_batches


def make_client(
    tmp_path: Path,
    *,
    default_candidate_count: int = 2,
    default_min_duration_minutes: float = 8,
    default_max_duration_minutes: float = 12,
) -> tuple[TestClient, FakeService]:
    service = FakeService(tmp_path)
    app = create_app(
        settings=Settings(
            _env_file=None,
            openai_api_key="sk-test-value",
            output_dir=tmp_path / "outputs",
            work_dir=tmp_path / ".work",
            default_candidate_count=default_candidate_count,
            default_min_duration_minutes=default_min_duration_minutes,
            default_max_duration_minutes=default_max_duration_minutes,
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
    assert created.json()["artifacts"] == {
        "video_id": "abc123DEF_-",
        "analysis_id": "20260606-000000-analys",
    }
    assert service.created[0]["candidate_count"] == 2
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "WAITING_SELECTION"
    assert fetched.json()["progress"] == {
        "stage": "transcription",
        "event": "started",
        "chunk_index": 0,
        "chunk_count": 3,
        "attempt": 1,
        "max_attempts": 3,
    }
    assert "artifacts" in fetched.json()
    assert render.status_code == 202
    assert render.json()["render_id"] == "render-1"
    assert batches.json()["render_batches"] == []


def test_render_batch_response_includes_stage_manifest(tmp_path: Path) -> None:
    client, service = make_client(tmp_path)
    batch = RenderBatch(
        render_id="render-1",
        candidate_ids=["A"],
        status=JobStatus.COMPLETED,
        message="All selected candidates rendered successfully.",
        output_dir=(tmp_path / "analysis" / "render").resolve(),
        created_at=datetime(2026, 6, 6, tzinfo=UTC),
        updated_at=datetime(2026, 6, 6, tzinfo=UTC),
    )
    batch.output_dir.mkdir(parents=True)
    (batch.output_dir / "stage-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "operation_id": "analysis-1",
                "render_id": "render-1",
                "candidate_id": "A",
                "stages": [
                    {
                        "stage": "validate_render",
                        "status": "completed",
                        "started_at": None,
                        "completed_at": None,
                        "elapsed_seconds": None,
                        "artifacts": {},
                        "resume_strategy": "render is publishable",
                        "fresh": False,
                        "reused": True,
                        "warnings": [],
                        "error": None,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    service.render_batches = [batch]

    with client:
        response = client.get("/api/v1/analysis-jobs/analysis-1/renders")

    assert response.status_code == 200
    body = response.json()
    assert body["render_batches"][0]["stages"][0]["stage"] == "validate_render"


def test_analysis_route_passes_force_render_flag_to_service(tmp_path: Path) -> None:
    client, service = make_client(tmp_path)
    with client:
        response = client.post(
            "/api/v1/analysis-jobs/analysis-1/renders",
            json={
                "candidate_ids": ["A", "C"],
                "force_render": True,
            },
        )

    assert response.status_code == 202
    request = service.render_requests[0]
    assert request.candidate_ids == ["A", "C"]
    assert request.force_render is True


def test_analysis_route_uses_configured_defaults_for_omitted_fields(tmp_path: Path) -> None:
    client, service = make_client(
        tmp_path,
        default_candidate_count=4,
        default_min_duration_minutes=6,
        default_max_duration_minutes=9,
    )
    with client:
        response = client.post(
            "/api/v1/analysis-jobs",
            json={"youtube_url": "https://youtu.be/abc123DEF_-"},
        )

    assert response.status_code == 202
    assert service.created[0]["candidate_count"] == 4
    assert service.created[0]["min_duration_minutes"] == 6
    assert service.created[0]["max_duration_minutes"] == 9


def test_analysis_route_merges_partial_overrides_field_by_field(tmp_path: Path) -> None:
    client, service = make_client(
        tmp_path,
        default_candidate_count=4,
        default_min_duration_minutes=6,
        default_max_duration_minutes=9,
    )
    with client:
        response = client.post(
            "/api/v1/analysis-jobs",
            json={
                "youtube_url": "https://youtu.be/abc123DEF_-",
                "candidate_count": 3,
                "max_duration_minutes": 10,
            },
        )

    assert response.status_code == 202
    assert service.created[0]["candidate_count"] == 3
    assert service.created[0]["min_duration_minutes"] == 6
    assert service.created[0]["max_duration_minutes"] == 10


def test_analysis_route_rejects_explicit_null_override(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)
    with client:
        response = client.post(
            "/api/v1/analysis-jobs",
            json={
                "youtube_url": "https://youtu.be/abc123DEF_-",
                "candidate_count": None,
            },
        )

    assert response.status_code == 422
    assert response.json()["error_code"] == "INVALID_REQUEST"


def test_analysis_route_rejects_invalid_merged_duration_range(tmp_path: Path) -> None:
    client, _ = make_client(
        tmp_path,
        default_min_duration_minutes=8,
        default_max_duration_minutes=12,
    )
    with client:
        response = client.post(
            "/api/v1/analysis-jobs",
            json={
                "youtube_url": "https://youtu.be/abc123DEF_-",
                "min_duration_minutes": 13,
            },
        )

    assert response.status_code == 422
    assert response.json()["error_code"] == "INVALID_REQUEST"


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


def test_request_validation_error_is_json_serializable(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)
    with client:
        response = client.post(
            "/api/v1/analysis-jobs",
            json={
                "youtube_url": "https://youtu.be/abc123DEF_-",
                "min_duration_minutes": 12,
                "max_duration_minutes": 8,
            },
        )

    assert response.status_code == 422
    assert response.json()["error_code"] == "INVALID_REQUEST"
    assert response.json()["details"]["errors"]


def test_invalid_job_state_returns_conflict(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)
    with client:
        response = client.post(
            "/api/v1/analysis-jobs/not-ready/renders",
            json={"candidate_ids": "A"},
        )

    assert response.status_code == 409
    assert response.json() == {
        "error_code": "INVALID_JOB_STATE",
        "message": "Analysis job is not ready for candidate rendering.",
        "details": {
            "job_id": "not-ready",
            "status": "TRANSCRIBING",
        },
    }
