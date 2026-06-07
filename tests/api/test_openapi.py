import asyncio
from pathlib import Path

from insightcast.api.app import create_app
from insightcast.core.config import Settings


class FakeFfmpeg:
    async def probe(self) -> None:
        return None


class FakeService:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[object] = asyncio.Queue()

    async def process(self, _item: object) -> None:
        return None


def test_openapi_documents_all_operations_descriptions_examples_and_errors(
    tmp_path: Path,
) -> None:
    app = create_app(
        settings=Settings(_env_file=None, openai_api_key="sk-test-value"),
        service=FakeService(),
        ffmpeg=FakeFfmpeg(),
    )

    schema = app.openapi()

    expected_paths = {
        "/health",
        "/api/v1/analysis-jobs",
        "/api/v1/analysis-jobs/{job_id}",
        "/api/v1/analysis-jobs/{job_id}/renders",
        "/api/v1/direct-render-jobs",
        "/api/v1/direct-render-jobs/{job_id}",
        "/api/v1/analysis-jobs/{job_id}/youtube-uploads",
        "/api/v1/direct-render-jobs/{job_id}/youtube-uploads",
    }
    assert expected_paths <= set(schema["paths"])
    analysis_schema = schema["components"]["schemas"]["AnalysisJobCreateRequest"]
    assert analysis_schema["properties"]["youtube_url"]["description"]
    assert analysis_schema["properties"]["candidate_count"]["examples"] == [2]
    for field in (
        "candidate_count",
        "min_duration_minutes",
        "max_duration_minutes",
    ):
        assert field not in analysis_schema["required"]
        assert "default" not in analysis_schema["properties"][field]
        assert "anyOf" not in analysis_schema["properties"][field]
        assert "override" in analysis_schema["properties"][field]["description"].lower()
    assert "ErrorResponse" in schema["components"]["schemas"]
