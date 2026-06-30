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
    def __init__(self, description: str = "完整說明") -> None:
        self.description = description
        self.calls: list[dict[str, object]] = []

    async def parse(self, **kwargs: object) -> GeneratedYouTubeMetadata:
        self.calls.append(kwargs)
        return GeneratedYouTubeMetadata(
            title="知識標題",
            title_variants=[
                {
                    "title": "知識標題",
                    "strategy": "conceptual_reframe",
                    "rationale": "顛覆原本看法。",
                },
                {
                    "title": "痛點標題",
                    "strategy": "pain_point",
                    "rationale": "直指觀眾痛點。",
                },
                {
                    "title": "機制標題",
                    "strategy": "mechanism",
                    "rationale": "說明底層機制。",
                },
                {
                    "title": "簡短標題",
                    "strategy": "clean_hook",
                    "rationale": "乾淨保留懸念。",
                },
            ],
            description=self.description,
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
        candidate_suggested_title="Candidate title",
        summary="Candidate summary",
        transcript_excerpt="Transcript excerpt",
        destination=destination,
    )

    payload = json.loads(destination.read_text(encoding="utf-8"))
    assert metadata.privacy_status == "private"
    assert payload["generated"]["title"] == "知識標題"
    assert payload["generated"]["title_variants"][0] == {
        "title": "知識標題",
        "strategy": "conceptual_reframe",
        "rationale": "顛覆原本看法。",
    }
    assert payload["generated"]["privacy_status"] == "private"
    assert payload["source"]["video_id"] == "abc123DEF_-"
    assert payload["trace"]["model"] == "gpt-metadata"
    assert payload["trace"]["prompt_version"] == "metadata-v9"
    call_prompt = json.loads(str(client.calls[0]["user_prompt"]))
    assert call_prompt["candidate_suggested_title"] == "Candidate title"
    assert call_prompt["source_description_excerpt"] == "Source description"
    assert call_prompt["summary"] == "Candidate summary"
    assert call_prompt["transcript_excerpt"] == "Transcript excerpt"
    assert call_prompt["brand_positioning"]["product"] == "InsightCast"


@pytest.mark.asyncio
async def test_publish_engine_normalizes_description_to_single_line_fixed_disclaimer(
    tmp_path: Path,
) -> None:
    client = FakeStructuredClient(
        description=(
            "這段內容先說明觀眾為什麼會被這個問題卡住。\n\n"
            "這支 InsightCast 精選會帶你理解底層機制。\n"
            "看完後會知道該如何重新判斷。"
        )
    )
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

    generated = await engine.generate(
        source_metadata=source,
        candidate_suggested_title="Candidate title",
        summary="Candidate summary",
        transcript_excerpt="Transcript excerpt",
        destination=destination,
    )

    payload = json.loads(destination.read_text(encoding="utf-8"))
    expected_disclaimer = (
        "InsightCast 為繁體中文翻譯精選，非完整原片；完整脈絡請參考原始影片。"
    )
    assert "\n" not in generated.description
    assert generated.description.endswith(expected_disclaimer)
    assert generated.description.count("InsightCast") == 1
    assert "這支 InsightCast 精選" not in generated.description
    assert payload["generated"]["description"] == generated.description
