import json
from pathlib import Path

import pytest

from insightcast.engines.publish_engine import (
    GeneratedYouTubeMetadata,
    PublishEngine,
)
from insightcast.infrastructure.ytdlp_client import YouTubeMetadata
from insightcast.storage.file_job_writer import FileJobWriter


class FakeStructuredClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def parse(self, **kwargs: object) -> GeneratedYouTubeMetadata:
        self.calls.append(kwargs)
        return GeneratedYouTubeMetadata(
            title="知識標題",
            description="完整說明",
            tags=["知識", "AI"],
        )


@pytest.mark.asyncio
async def test_publish_engine_generates_private_metadata_and_writes_traceable_json(
    tmp_path: Path,
) -> None:
    client = FakeStructuredClient()
    engine = PublishEngine(
        client=client,
        model="gpt-metadata",
        writer=FileJobWriter(),
    )
    source = YouTubeMetadata(
        video_id="abc123DEF_-",
        title="Source title",
        description="Source description",
        duration_seconds=1200,
        webpage_url="https://www.youtube.com/watch?v=abc123DEF_-",
    )
    destination = tmp_path / "video.youtube-metadata.json"

    metadata = await engine.generate(
        source_metadata=source,
        summary="Candidate summary",
        transcript_excerpt="Transcript excerpt",
        destination=destination,
    )

    payload = json.loads(destination.read_text(encoding="utf-8"))
    assert metadata.privacy_status == "private"
    assert payload["generated"]["title"] == "知識標題"
    assert payload["generated"]["privacy_status"] == "private"
    assert payload["source"]["video_id"] == "abc123DEF_-"
    assert payload["trace"]["model"] == "gpt-metadata"
    assert payload["trace"]["prompt_version"] == "metadata-v5"
    call_prompt = json.loads(str(client.calls[0]["user_prompt"]))
    assert call_prompt["summary"] == "Candidate summary"
    assert call_prompt["transcript_excerpt"] == "Transcript excerpt"
    assert call_prompt["brand_positioning"]["product"] == "InsightCast"
