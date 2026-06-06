import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from insightcast.api.app import create_app
from insightcast.core.config import Settings
from insightcast.core.exceptions import InsightCastError
from insightcast.domain.enums import ErrorCode, JobStatus, JobType
from insightcast.domain.models import DirectRenderJob, RenderArtifacts


class FakeFfmpeg:
    async def probe(self) -> None:
        return None


class FakeService:
    def __init__(self, tmp_path: Path) -> None:
        self.queue: asyncio.Queue[object] = asyncio.Queue()
        self.tmp_path = tmp_path

    async def process(self, _item: object) -> None:
        return None

    async def create_direct_render_job(self, youtube_url: str, **kwargs: object) -> DirectRenderJob:
        return DirectRenderJob(
            job_id="direct-1",
            job_type=JobType.DIRECT_RENDER,
            original_youtube_url=youtube_url,
            normalized_youtube_url="https://www.youtube.com/watch?v=abc123DEF_-",
            status=JobStatus.QUEUED,
            message="Direct render job is queued.",
            output_dir=(self.tmp_path / "direct").resolve(),
            start_seconds=float(kwargs["start_seconds"]),
            end_seconds=float(kwargs["end_seconds"]),
        )

    def get_direct_render_job(self, job_id: str) -> DirectRenderJob:
        render_dir = (self.tmp_path / "direct" / "render").resolve()
        render_dir.mkdir(parents=True, exist_ok=True)
        (render_dir / "clip.mp4").write_bytes(b"video")
        (render_dir / "metadata.json").write_text("{}", encoding="utf-8")
        return DirectRenderJob(
            job_id=job_id,
            job_type=JobType.DIRECT_RENDER,
            original_youtube_url="https://youtu.be/abc123DEF_-",
            normalized_youtube_url="https://www.youtube.com/watch?v=abc123DEF_-",
            status=JobStatus.COMPLETED,
            message="Completed.",
            output_dir=(self.tmp_path / "direct").resolve(),
            start_seconds=10,
            end_seconds=20,
            artifacts=RenderArtifacts(
                traditional_chinese_srt=render_dir / "clip.srt",
                bilingual_ass=render_dir / "clip.ass",
                burned_video=render_dir / "clip.mp4",
                youtube_metadata=render_dir / "metadata.json",
            ),
        )


def make_client(tmp_path: Path) -> TestClient:
    app = create_app(
        settings=Settings(
            _env_file=None,
            openai_api_key="sk-test-value",
            output_dir=tmp_path / "outputs",
            work_dir=tmp_path / ".work",
        ),
        service=FakeService(tmp_path),
        ffmpeg=FakeFfmpeg(),
    )
    return TestClient(app)


def test_direct_render_accepts_timecode_and_numeric_seconds(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    with client:
        response = client.post(
            "/api/v1/direct-render-jobs",
            json={
                "youtube_url": "https://youtu.be/abc123DEF_-",
                "start_time": "00:00:10",
                "end_time": 20,
            },
        )
        fetched = client.get("/api/v1/direct-render-jobs/direct-1")

    assert response.status_code == 202
    assert response.json()["job_id"] == "direct-1"
    assert fetched.status_code == 200
    assert fetched.json()["artifacts"]["burned_video"].endswith("clip.mp4")


def test_upload_stub_returns_paths_with_not_implemented(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    with client:
        response = client.post("/api/v1/direct-render-jobs/direct-1/youtube-uploads")

    assert response.status_code == 501
    assert response.json()["error_code"] == "UPLOAD_NOT_IMPLEMENTED"
    assert response.json()["details"]["burned_video"].endswith("clip.mp4")


def test_invalid_direct_time_range_uses_stable_error(tmp_path: Path) -> None:
    class InvalidService(FakeService):
        async def create_direct_render_job(
            self, youtube_url: str, **kwargs: object
        ) -> DirectRenderJob:
            raise InsightCastError(
                ErrorCode.INVALID_TIME_RANGE,
                "end_time must be later than start_time.",
                details=kwargs,
            )

    app = create_app(
        settings=Settings(_env_file=None, openai_api_key="sk-test-value"),
        service=InvalidService(tmp_path),
        ffmpeg=FakeFfmpeg(),
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/direct-render-jobs",
            json={
                "youtube_url": "https://youtu.be/abc123DEF_-",
                "start_time": 20,
                "end_time": 10,
            },
        )

    assert response.status_code == 400
    assert response.json()["error_code"] == "INVALID_TIME_RANGE"
