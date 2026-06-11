import asyncio
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from insightcast.api.app import create_app
from insightcast.core.config import Settings
from insightcast.domain.enums import ErrorCode
from insightcast.domain.models import Candidate, JobError
from insightcast.infrastructure.ytdlp_client import YouTubeMetadata
from insightcast.storage.file_job_writer import FileJobWriter
from insightcast.storage.manifests import (
    AnalysisState,
    PublishState,
    RenderKind,
    RenderState,
)
from insightcast.storage.video_store import VideoStore

VIDEO_ID = "abc123DEF_-"
ORIGINAL_URL = f"https://youtu.be/{VIDEO_ID}"
ANALYSIS_ID = "20260607-120000-abcdef"
RENDER_ID = "20260607-130000-fedcba"
CUSTOM_RENDER_ID = "20260607-150000-custom"


class FakeFfmpeg:
    async def probe(self) -> None:
        return None


class FakeService:
    def __init__(self, video_store: VideoStore) -> None:
        self.queue: asyncio.Queue[object] = asyncio.Queue()
        self.video_store = video_store

    async def process(self, _item: object) -> None:
        return None


def make_client(tmp_path: Path, *, video_store: VideoStore | None = None) -> TestClient:
    store = video_store or VideoStore(tmp_path / "outputs", FileJobWriter())
    app = create_app(
        settings=Settings(
            _env_file=None,
            openai_api_key="sk-test-value",
            output_dir=tmp_path / "outputs",
            work_dir=tmp_path / ".work",
        ),
        service=FakeService(store),
        ffmpeg=FakeFfmpeg(),
    )
    return TestClient(app)


def seed_ready_candidate_render(tmp_path: Path) -> str:
    store = VideoStore(tmp_path / "outputs", FileJobWriter())
    store.ensure_video(
        YouTubeMetadata(
            video_id=VIDEO_ID,
            title="Original Title",
            description="Description",
            duration_seconds=600,
            uploader="Channel",
            upload_date="20260606",
            webpage_url=f"https://www.youtube.com/watch?v={VIDEO_ID}",
            tags=[],
        ),
        ORIGINAL_URL,
    )
    store.write_analysis(
        video_id=VIDEO_ID,
        analysis_id=ANALYSIS_ID,
        operation_id="analysis-job-1",
        created_at=datetime(2026, 6, 7, 12, 0, tzinfo=UTC),
        completed_at=datetime(2026, 6, 7, 12, 1, tzinfo=UTC),
        normalized_source_url=f"https://www.youtube.com/watch?v={VIDEO_ID}",
        transcript_id="tx-abcdef123456",
        curator_model="gpt-curator",
        prompt_version="curator-v1",
        candidate_count=1,
        min_duration_seconds=480,
        max_duration_seconds=720,
        candidates=[
            Candidate(
                candidate_id="A",
                start_seconds=0,
                end_seconds=60,
                suggested_title="Candidate A",
                selection_reason="Strong standalone arc.",
                summary="Summary A",
            )
        ],
        state=AnalysisState.WAITING_SELECTION,
        log_path=Path("logs/analysis-job-1.log"),
    )
    render_dir = store.render_dir(
        VIDEO_ID,
        RENDER_ID,
        analysis_id=ANALYSIS_ID,
        candidate_id="A",
    )
    render_dir.mkdir(parents=True)
    for name in (
        "video.mp4",
        "subtitles.zh-TW.srt",
        "subtitles.bilingual.ass",
        "youtube-metadata.json",
    ):
        (render_dir / name).write_bytes(name.encode())
    store.write_render(
        video_id=VIDEO_ID,
        render_id=RENDER_ID,
        operation_id="render-job-1",
        kind=RenderKind.CANDIDATE,
        analysis_id=ANALYSIS_ID,
        candidate_id="A",
        start_seconds=0,
        end_seconds=60,
        source_fingerprint="a" * 64,
        transcript_id="tx-abcdef123456",
        render_config={"subtitle_language": "zh-TW", "bilingual": True},
        created_at=datetime(2026, 6, 7, 13, 0, tzinfo=UTC),
        completed_at=datetime(2026, 6, 7, 13, 1, tzinfo=UTC),
        render_state=RenderState.READY,
        publish_state=PublishState.NOT_UPLOADED,
        log_path=Path("logs/render-job-1.log"),
    )
    return RENDER_ID


def seed_failed_candidate_render(tmp_path: Path) -> str:
    seed_ready_candidate_render(tmp_path)
    failed_render_id = "20260607-140000-badbad"
    store = VideoStore(tmp_path / "outputs", FileJobWriter())
    store.write_render(
        video_id=VIDEO_ID,
        render_id=failed_render_id,
        operation_id="render-job-2",
        kind=RenderKind.CANDIDATE,
        analysis_id=ANALYSIS_ID,
        candidate_id="A",
        start_seconds=0,
        end_seconds=60,
        source_fingerprint="a" * 64,
        transcript_id="tx-abcdef123456",
        render_config={"subtitle_language": "zh-TW", "bilingual": True},
        created_at=datetime(2026, 6, 7, 14, 0, tzinfo=UTC),
        completed_at=datetime(2026, 6, 7, 14, 1, tzinfo=UTC),
        render_state=RenderState.FAILED,
        publish_state=PublishState.NOT_UPLOADED,
        log_path=Path("logs/render-job-2.log"),
        render_error=JobError(
            stage="rendering",
            error_code=ErrorCode.VIDEO_RENDER_FAILED,
            message="Render failed.",
        ),
    )
    return failed_render_id


def seed_ready_custom_render(tmp_path: Path) -> str:
    seed_ready_candidate_render(tmp_path)
    store = VideoStore(tmp_path / "outputs", FileJobWriter())
    render_dir = store.render_dir(VIDEO_ID, CUSTOM_RENDER_ID)
    render_dir.mkdir(parents=True)
    for name in (
        "video.mp4",
        "subtitles.zh-TW.srt",
        "subtitles.bilingual.ass",
        "youtube-metadata.json",
    ):
        (render_dir / name).write_bytes(name.encode())
    store.write_render(
        video_id=VIDEO_ID,
        render_id=CUSTOM_RENDER_ID,
        operation_id="direct-render-job-1",
        kind=RenderKind.CUSTOM,
        analysis_id=None,
        candidate_id=None,
        start_seconds=120,
        end_seconds=180,
        source_fingerprint="a" * 64,
        transcript_id="tx-abcdef123456",
        render_config={"subtitle_language": "zh-TW", "bilingual": True},
        created_at=datetime(2026, 6, 7, 15, 0, tzinfo=UTC),
        completed_at=datetime(2026, 6, 7, 15, 1, tzinfo=UTC),
        render_state=RenderState.READY,
        publish_state=PublishState.NOT_UPLOADED,
        log_path=Path("logs/direct-render-job-1.log"),
    )
    return CUSTOM_RENDER_ID


def test_video_routes_list_analyses_from_disk(tmp_path: Path) -> None:
    seed_ready_candidate_render(tmp_path)
    client = make_client(tmp_path)

    with client:
        response = client.get(f"/api/v1/videos/{VIDEO_ID}/analyses")

    assert response.status_code == 200
    payload = response.json()
    assert payload["video_id"] == VIDEO_ID
    assert payload["analyses"][0]["analysis_id"] == ANALYSIS_ID
    assert payload["analyses"][0]["candidate_paths"]["A"].endswith("candidate.json")


def test_video_routes_discover_render_from_disk_after_restart(tmp_path: Path) -> None:
    render_id = seed_ready_candidate_render(tmp_path)
    fresh_store = VideoStore(tmp_path / "outputs", FileJobWriter())
    client = make_client(tmp_path, video_store=fresh_store)

    with client:
        response = client.get(f"/api/v1/videos/{VIDEO_ID}/renders")

    assert response.status_code == 200
    payload = response.json()
    assert payload["video_id"] == VIDEO_ID
    assert payload["renders"][0]["render_id"] == render_id
    assert payload["renders"][0]["analysis_id"] == ANALYSIS_ID
    assert payload["renders"][0]["candidate_id"] == "A"
    assert payload["renders"][0]["kind"] == "candidate"
    assert payload["renders"][0]["render_state"] == "ready"
    assert payload["renders"][0]["publish_state"] == "not-uploaded"
    assert payload["renders"][0]["artifacts"]["burned_video"].endswith("video.mp4")


def test_video_routes_discover_custom_render_from_disk(tmp_path: Path) -> None:
    render_id = seed_ready_custom_render(tmp_path)
    client = make_client(tmp_path)

    with client:
        response = client.get(f"/api/v1/videos/{VIDEO_ID}/renders")

    assert response.status_code == 200
    renders = response.json()["renders"]
    custom = next(item for item in renders if item["render_id"] == render_id)
    assert custom["kind"] == "custom"
    assert custom["analysis_id"] is None
    assert custom["candidate_id"] is None
    assert custom["artifacts"]["youtube_metadata"].endswith("youtube-metadata.json")


def test_upload_stub_requires_explicit_publishable_render_id(tmp_path: Path) -> None:
    render_id = seed_ready_candidate_render(tmp_path)
    client = make_client(tmp_path)

    with client:
        response = client.post(
            f"/api/v1/videos/{VIDEO_ID}/renders/{render_id}/youtube-uploads"
        )

    assert response.status_code == 501
    assert response.json()["error_code"] == "UPLOAD_NOT_IMPLEMENTED"
    assert response.json()["details"]["render_id"] == render_id
    assert response.json()["details"]["burned_video"].endswith("video.mp4")


def test_upload_stub_rejects_failed_render_id(tmp_path: Path) -> None:
    render_id = seed_failed_candidate_render(tmp_path)
    client = make_client(tmp_path)

    with client:
        response = client.post(
            f"/api/v1/videos/{VIDEO_ID}/renders/{render_id}/youtube-uploads"
        )

    assert response.status_code == 409
    assert response.json()["error_code"] == "RENDER_NOT_PUBLISHABLE"
    assert response.json()["details"]["render_id"] == render_id
