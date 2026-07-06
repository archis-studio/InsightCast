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
            title="知識標題：來源鉤子",
            title_variants=[
                {
                    "title": "知識標題：來源鉤子",
                    "strategy": "source_equity_hook",
                    "rationale": "用來源中最強的點擊資產切入。",
                },
                {
                    "title": "機制標題：底層原因",
                    "strategy": "mechanism_breakdown",
                    "rationale": "說明底層機制。",
                },
                {
                    "title": "受眾收穫標題：重框痛點",
                    "strategy": "audience_pain_reframe",
                    "rationale": "重框觀眾痛點。",
                },
            ],
            description=self.description,
            tags=["知識", "AI"],
        )


def test_generated_metadata_normalizes_malformed_title_structure() -> None:
    metadata = GeneratedYouTubeMetadata(
        title="不是撐到退休就好：：工作帶來身份、目的感",
        title_variants=[
            {
                "title": "不是撐到退休就好：：工作帶來身份、目的感",
                "strategy": "source_equity_hook",
                "rationale": "Bad double colon.",
            },
            {
                "title": "機制標題:說明底層機制",
                "strategy": "mechanism_breakdown",
                "rationale": "Mechanism.",
            },
            {
                "title": "痛點標題：重框觀眾痛點",
                "strategy": "audience_pain_reframe",
                "rationale": "Pain.",
            },
        ],
        description="說明",
        tags=[],
    )

    assert metadata.title == "不是撐到退休就好：工作帶來身份、目的感"
    assert metadata.title_variants[0].title == metadata.title
    assert metadata.title_variants[1].title == "機制標題：說明底層機制"


def test_generated_metadata_normalizes_speaker_suffix_bar() -> None:
    metadata = GeneratedYouTubeMetadata(
        title="退休規劃的認知誤區：不要把快樂全押在以後｜Morgan Housel",
        title_variants=[
            {
                "title": "退休規劃的認知誤區：不要把快樂全押在以後｜Morgan Housel",
                "strategy": "source_equity_hook",
                "rationale": "Bad speaker suffix.",
            },
            {
                "title": "工作身份的底層機制：退休後少了刺激反而更空",
                "strategy": "mechanism_breakdown",
                "rationale": "Mechanism.",
            },
            {
                "title": "提早退休前該想清楚：你失去的可能不只是工作",
                "strategy": "audience_pain_reframe",
                "rationale": "Pain.",
            },
        ],
        description="說明",
        tags=[],
    )

    assert metadata.title == "退休規劃的認知誤區：不要把快樂全押在以後"
    assert metadata.title_variants[0].title == metadata.title


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
        candidate_core_claim="Candidate core claim",
        candidate_payoff="Candidate payoff",
        candidate_argument_arc=["setup", "evidence", "payoff"],
        candidate_boundary_notes={"start": "starts cleanly", "end": "ends cleanly"},
        destination=destination,
    )

    payload = json.loads(destination.read_text(encoding="utf-8"))
    assert metadata.privacy_status == "private"
    assert payload["generated"]["title"] == "知識標題：來源鉤子"
    assert payload["generated"]["title_variants"][0] == {
        "title": "知識標題：來源鉤子",
        "strategy": "source_equity_hook",
        "rationale": "用來源中最強的點擊資產切入。",
    }
    assert payload["generated"]["privacy_status"] == "private"
    assert payload["source"]["video_id"] == "abc123DEF_-"
    assert payload["trace"]["model"] == "gpt-metadata"
    assert payload["trace"]["prompt_version"] == "metadata-v15"
    call_prompt = json.loads(str(client.calls[0]["user_prompt"]))
    assert call_prompt["candidate_suggested_title"] == "Candidate title"
    assert call_prompt["candidate_editorial_package"] == {
        "core_claim": "Candidate core claim",
        "payoff": "Candidate payoff",
        "argument_arc": ["setup", "evidence", "payoff"],
        "boundary_notes": {"start": "starts cleanly", "end": "ends cleanly"},
    }
    assert call_prompt["source_description_excerpt"] == "Source description"
    assert call_prompt["summary"] == "Candidate summary"
    assert call_prompt["transcript_excerpt"] == "Transcript excerpt"


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
