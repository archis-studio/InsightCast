import json

import pytest

from insightcast.core.exceptions import InsightCastError
from insightcast.domain.enums import ErrorCode
from insightcast.domain.models import TranscriptSegment
from insightcast.engines.lingo_engine import (
    LingoEngine,
    SubtitleTimingPolicy,
    TranslationItem,
    TranslationResponse,
)
from insightcast.infrastructure.openai_client import capture_llm_telemetry


class RecordingTranslationClient:
    def __init__(self, responses: list[TranslationResponse]) -> None:
        self.responses = responses
        self.calls: list[dict[str, object]] = []

    async def parse(self, **kwargs: object) -> TranslationResponse:
        self.calls.append(kwargs)
        return self.responses.pop(0)


def translation_response(*segment_ids: str) -> TranslationResponse:
    return TranslationResponse(
        items=[
            TranslationItem(segment_id=segment_id, text=f"翻譯 {segment_id}")
            for segment_id in segment_ids
        ]
    )


def translation_response_with_text(
    *items: tuple[str, str],
) -> TranslationResponse:
    return TranslationResponse(
        items=[
            TranslationItem(segment_id=segment_id, text=text)
            for segment_id, text in items
        ]
    )


def subtitle_segment(
    segment_id: str,
    start_seconds: float,
    end_seconds: float,
) -> TranscriptSegment:
    return TranscriptSegment(
        segment_id=segment_id,
        start_seconds=start_seconds,
        end_seconds=end_seconds,
        text=f"Source {segment_id}",
    )


@pytest.mark.asyncio
async def test_translate_clip_batches_long_requests_in_source_order() -> None:
    segments = [
        TranscriptSegment(
            segment_id=f"s{index}",
            start_seconds=index,
            end_seconds=index + 1,
            text=f"Source {index}",
        )
        for index in range(85)
    ]
    client = RecordingTranslationClient(
        [
            translation_response(*[f"s{index}" for index in range(0, 24)]),
            translation_response(*[f"s{index}" for index in range(24, 48)]),
            translation_response(*[f"s{index}" for index in range(48, 72)]),
            translation_response(*[f"s{index}" for index in range(72, 85)]),
        ]
    )

    result = await LingoEngine(client=client, model="gpt-translation").translate_clip(
        segments=segments,
        clip_start_seconds=0,
        clip_end_seconds=85,
    )

    assert len(client.calls) == 4
    assert [
        len(json.loads(str(call["user_prompt"]))["items"])
        for call in client.calls
    ] == [24, 24, 24, 13]
    assert [item.segment_id for item in result] == [
        f"s{index}" for index in range(85)
    ]


def test_prepare_subtitle_items_applies_conservative_timing_policy() -> None:
    segments = [
        subtitle_segment("s1", 10.20, 10.80),
        subtitle_segment("s2", 10.95, 13.00),
    ]

    items = LingoEngine(
        timing_policy=SubtitleTimingPolicy(
            enabled=True,
            offset_seconds=-0.12,
            min_duration_seconds=0.75,
            max_extension_seconds=0.30,
            min_gap_seconds=0.08,
        )
    ).prepare_subtitle_items(
        segments=segments,
        translations=[
            TranslationItem(segment_id="s1", text="第一句"),
            TranslationItem(segment_id="s2", text="第二句"),
        ],
        clip_start_seconds=10.0,
        clip_end_seconds=14.0,
    )

    assert [(item.start_seconds, item.end_seconds) for item in items] == [
        (0.08, 0.75),
        (0.83, 2.88),
    ]


def test_prepare_subtitle_items_does_not_hold_fast_speech_too_long() -> None:
    segments = [
        subtitle_segment("s1", 1.00, 1.30),
        subtitle_segment("s2", 1.36, 2.00),
    ]

    items = LingoEngine(
        timing_policy=SubtitleTimingPolicy(
            enabled=True,
            offset_seconds=-0.12,
            min_duration_seconds=0.75,
            max_extension_seconds=0.30,
            min_gap_seconds=0.08,
        )
    ).prepare_subtitle_items(
        segments=segments,
        translations=[
            TranslationItem(segment_id="s1", text="短句"),
            TranslationItem(segment_id="s2", text="下一句"),
        ],
        clip_start_seconds=0,
        clip_end_seconds=3,
    )

    assert [(item.start_seconds, item.end_seconds) for item in items] == [
        (0.88, 1.16),
        (1.24, 1.99),
    ]


def test_prepare_subtitle_items_can_disable_timing_policy() -> None:
    segments = [subtitle_segment("s1", 1.00, 1.30)]

    items = LingoEngine(
        timing_policy=SubtitleTimingPolicy(
            enabled=False,
            offset_seconds=-0.12,
            min_duration_seconds=0.75,
            max_extension_seconds=0.30,
            min_gap_seconds=0.08,
        )
    ).prepare_subtitle_items(
        segments=segments,
        translations=[TranslationItem(segment_id="s1", text="短句")],
        clip_start_seconds=0,
        clip_end_seconds=2,
    )

    assert [(item.start_seconds, item.end_seconds) for item in items] == [(1.0, 1.3)]


@pytest.mark.asyncio
async def test_translate_clip_uses_configured_batch_size() -> None:
    segments = [
        TranscriptSegment(
            segment_id=f"s{index}",
            start_seconds=index,
            end_seconds=index + 1,
            text=f"Source {index}",
        )
        for index in range(30)
    ]
    client = RecordingTranslationClient(
        [
            translation_response(*[f"s{index}" for index in range(0, 12)]),
            translation_response(*[f"s{index}" for index in range(12, 24)]),
            translation_response(*[f"s{index}" for index in range(24, 30)]),
        ]
    )

    await LingoEngine(
        client=client,
        model="gpt-translation",
        batch_size=12,
    ).translate_clip(
        segments=segments,
        clip_start_seconds=0,
        clip_end_seconds=30,
    )

    assert [
        len(json.loads(str(call["user_prompt"]))["items"])
        for call in client.calls
    ] == [12, 12, 6]


@pytest.mark.asyncio
async def test_translate_clip_salvages_reordered_items_without_repair() -> None:
    segments = [
        TranscriptSegment(
            segment_id=f"s{index}",
            start_seconds=index,
            end_seconds=index + 1,
            text=f"Source {index}",
        )
        for index in range(3)
    ]
    client = RecordingTranslationClient(
        [
            translation_response_with_text(
                ("s2", "第三"),
                ("s0", "第一"),
                ("s1", "第二"),
            )
        ]
    )
    telemetry: list[dict[str, object]] = []

    with capture_llm_telemetry(telemetry.append):
        result = await LingoEngine(
            client=client,
            model="gpt-translation",
        ).translate_clip(
            segments=segments,
            clip_start_seconds=0,
            clip_end_seconds=3,
        )

    assert len(client.calls) == 1
    assert [item.traditional_chinese_text for item in result] == ["第一", "第二", "第三"]
    assert any(
        event.get("event") == "validation_failed"
        and event.get("reason") == "reordered_ids"
        for event in telemetry
    )


@pytest.mark.asyncio
async def test_translate_clip_salvages_extra_and_duplicate_items_without_repair() -> None:
    segments = [
        TranscriptSegment(
            segment_id=f"s{index}",
            start_seconds=index,
            end_seconds=index + 1,
            text=f"Source {index}",
        )
        for index in range(2)
    ]
    client = RecordingTranslationClient(
        [
            translation_response_with_text(
                ("extra", "多餘"),
                ("s1", "第二"),
                ("s0", "..."),
                ("s0", "第一"),
            )
        ]
    )

    result = await LingoEngine(client=client, model="gpt-translation").translate_clip(
        segments=segments,
        clip_start_seconds=0,
        clip_end_seconds=2,
    )

    assert len(client.calls) == 1
    assert [item.traditional_chinese_text for item in result] == ["第一", "第二"]


@pytest.mark.asyncio
async def test_translate_clip_splits_mismatched_batch_and_preserves_order() -> None:
    segments = [
        TranscriptSegment(
            segment_id=f"s{index}",
            start_seconds=index,
            end_seconds=index + 1,
            text=f"Source {index}",
        )
        for index in range(24)
    ]
    client = RecordingTranslationClient(
        [
            translation_response(*[f"s{index}" for index in range(0, 22)]),
            translation_response(*[f"s{index}" for index in range(0, 22)]),
            translation_response(*[f"s{index}" for index in range(0, 12)]),
            translation_response(*[f"s{index}" for index in range(12, 24)]),
        ]
    )

    result = await LingoEngine(client=client, model="gpt-translation").translate_clip(
        segments=segments,
        clip_start_seconds=0,
        clip_end_seconds=24,
    )

    assert [
        len(json.loads(str(call["user_prompt"]))["items"])
        for call in client.calls
    ] == [24, 24, 12, 12]
    assert [item.segment_id for item in result] == [
        f"s{index}" for index in range(24)
    ]


@pytest.mark.asyncio
async def test_translate_clip_reports_terminal_single_item_mapping_mismatch() -> None:
    segment = TranscriptSegment(
        segment_id="s0",
        start_seconds=0,
        end_seconds=1,
        text="Source",
    )
    client = RecordingTranslationClient(
        [TranslationResponse(items=[]), TranslationResponse(items=[])]
    )

    with pytest.raises(InsightCastError) as exc_info:
        await LingoEngine(client=client, model="gpt-translation").translate_clip(
            segments=[segment],
            clip_start_seconds=0,
            clip_end_seconds=1,
        )

    assert exc_info.value.error_code == ErrorCode.SUBTITLE_GENERATION_FAILED
    assert exc_info.value.details["batch_index"] == 0
    assert exc_info.value.details["batch_path"] == []
    assert exc_info.value.details["source_segment_ids"] == ["s0"]
    assert exc_info.value.details["translation_segment_ids"] == []


@pytest.mark.asyncio
async def test_translate_clip_splits_batch_with_unreadable_translation() -> None:
    segments = [
        TranscriptSegment(
            segment_id="s0",
            start_seconds=0,
            end_seconds=1,
            text="Within",
        ),
        TranscriptSegment(
            segment_id="s1",
            start_seconds=1,
            end_seconds=2,
            text="constraints",
        ),
    ]
    client = RecordingTranslationClient(
        [
            translation_response_with_text(("s0", "..."), ("s1", "限制")),
            translation_response_with_text(("s0", "..."), ("s1", "限制")),
            translation_response_with_text(("s0", "在範圍內")),
            translation_response_with_text(("s1", "限制")),
        ]
    )

    result = await LingoEngine(client=client, model="gpt-translation").translate_clip(
        segments=segments,
        clip_start_seconds=0,
        clip_end_seconds=2,
    )

    assert [
        len(json.loads(str(call["user_prompt"]))["items"])
        for call in client.calls
    ] == [2, 2, 1, 1]
    assert [item.traditional_chinese_text for item in result] == ["在範圍內", "限制"]


@pytest.mark.asyncio
async def test_translate_batch_retries_with_repair_prompt_before_splitting() -> None:
    segments = [
        TranscriptSegment(segment_id="s0", start_seconds=0, end_seconds=1, text="First"),
        TranscriptSegment(segment_id="s1", start_seconds=1, end_seconds=2, text="Second"),
    ]
    client = RecordingTranslationClient(
        [
            TranslationResponse(items=[]),
            translation_response("s0", "s1"),
        ]
    )

    result = await LingoEngine(client=client, model="gpt-translation").translate_clip(
        segments=segments,
        clip_start_seconds=0,
        clip_end_seconds=2,
    )

    assert [item.segment_id for item in result] == ["s0", "s1"]
    assert len(client.calls) == 2
    assert "Repair this subtitle translation batch" in str(client.calls[1]["user_prompt"])


@pytest.mark.asyncio
async def test_translate_clip_reports_terminal_unreadable_translation() -> None:
    segment = TranscriptSegment(
        segment_id="s0",
        start_seconds=0,
        end_seconds=1,
        text="Within",
    )
    client = RecordingTranslationClient(
        [
            translation_response_with_text(("s0", "...")),
            translation_response_with_text(("s0", "...")),
        ]
    )

    with pytest.raises(InsightCastError) as exc_info:
        await LingoEngine(client=client, model="gpt-translation").translate_clip(
            segments=[segment],
            clip_start_seconds=0,
            clip_end_seconds=1,
        )

    assert exc_info.value.error_code == ErrorCode.SUBTITLE_GENERATION_FAILED
    assert exc_info.value.details["batch_index"] == 0
    assert exc_info.value.details["batch_path"] == []
    assert exc_info.value.details["segment_id"] == "s0"
    assert exc_info.value.details["translation_text"] == "..."


@pytest.mark.asyncio
async def test_translate_clip_reports_repair_unreadable_after_original_mismatch() -> None:
    segment = TranscriptSegment(
        segment_id="s0",
        start_seconds=0,
        end_seconds=1,
        text="Within",
    )
    client = RecordingTranslationClient(
        [
            TranslationResponse(items=[]),
            translation_response_with_text(("s0", "...")),
        ]
    )

    with pytest.raises(InsightCastError) as exc_info:
        await LingoEngine(client=client, model="gpt-translation").translate_clip(
            segments=[segment],
            clip_start_seconds=0,
            clip_end_seconds=1,
        )

    assert exc_info.value.error_code == ErrorCode.SUBTITLE_GENERATION_FAILED
    assert exc_info.value.message == "Translation must contain readable text."
    assert exc_info.value.details["segment_id"] == "s0"
    assert exc_info.value.details["translation_text"] == "..."


@pytest.mark.asyncio
async def test_translate_clip_reports_repair_mapping_after_original_unreadable() -> None:
    segment = TranscriptSegment(
        segment_id="s0",
        start_seconds=0,
        end_seconds=1,
        text="Within",
    )
    client = RecordingTranslationClient(
        [
            translation_response_with_text(("s0", "...")),
            TranslationResponse(items=[]),
        ]
    )

    with pytest.raises(InsightCastError) as exc_info:
        await LingoEngine(client=client, model="gpt-translation").translate_clip(
            segments=[segment],
            clip_start_seconds=0,
            clip_end_seconds=1,
        )

    assert exc_info.value.error_code == ErrorCode.SUBTITLE_GENERATION_FAILED
    assert (
        exc_info.value.message
        == "Translation batch must map one-to-one to source subtitle items."
    )
    assert exc_info.value.details["source_segment_ids"] == ["s0"]
    assert exc_info.value.details["translation_segment_ids"] == []


def test_prepare_subtitle_items_filters_clamps_and_relativizes_segments() -> None:
    segments = [
        TranscriptSegment(segment_id="before", start_seconds=1, end_seconds=4, text="Before"),
        TranscriptSegment(segment_id="left", start_seconds=4, end_seconds=7, text="Left edge"),
        TranscriptSegment(segment_id="inside", start_seconds=8, end_seconds=10, text="Inside"),
        TranscriptSegment(segment_id="right", start_seconds=11, end_seconds=14, text="Right edge"),
        TranscriptSegment(segment_id="after", start_seconds=14, end_seconds=16, text="After"),
    ]
    translations = [
        TranslationItem(segment_id="left", text="左側"),
        TranslationItem(segment_id="inside", text="中間"),
        TranslationItem(segment_id="right", text="右側"),
    ]

    items = LingoEngine(
        timing_policy=SubtitleTimingPolicy(enabled=False)
    ).prepare_subtitle_items(
        segments=segments,
        translations=translations,
        clip_start_seconds=5,
        clip_end_seconds=12,
    )

    assert [(item.segment_id, item.start_seconds, item.end_seconds) for item in items] == [
        ("left", 0, 2),
        ("inside", 3, 5),
        ("right", 6, 7),
    ]
    assert [item.traditional_chinese_text for item in items] == ["左側", "中間", "右側"]


@pytest.mark.parametrize(
    "translations",
    [
        [TranslationItem(segment_id="left", text="左側")],
        [
            TranslationItem(segment_id="inside", text="中間"),
            TranslationItem(segment_id="left", text="左側"),
        ],
        [
            TranslationItem(segment_id="left", text="左側"),
            TranslationItem(segment_id="wrong", text="錯誤"),
        ],
    ],
)
def test_prepare_subtitle_items_rejects_translation_mapping_mismatches(
    translations: list[TranslationItem],
) -> None:
    segments = [
        TranscriptSegment(segment_id="left", start_seconds=4, end_seconds=7, text="Left"),
        TranscriptSegment(segment_id="inside", start_seconds=8, end_seconds=10, text="Inside"),
    ]

    with pytest.raises(InsightCastError) as exc_info:
        LingoEngine().prepare_subtitle_items(
            segments=segments,
            translations=translations,
            clip_start_seconds=5,
            clip_end_seconds=12,
        )

    assert exc_info.value.error_code == ErrorCode.SUBTITLE_GENERATION_FAILED


@pytest.mark.parametrize("text", ["", "   ", ".", "？！", "...？！"])
def test_prepare_subtitle_items_rejects_empty_or_punctuation_only_translation(
    text: str,
) -> None:
    segments = [
        TranscriptSegment(segment_id="s1", start_seconds=0, end_seconds=2, text="Hello")
    ]
    translation = TranslationItem.model_construct(segment_id="s1", text=text)

    with pytest.raises(InsightCastError) as exc_info:
        LingoEngine().prepare_subtitle_items(
            segments=segments,
            translations=[translation],
            clip_start_seconds=0,
            clip_end_seconds=2,
        )

    assert exc_info.value.error_code == ErrorCode.SUBTITLE_GENERATION_FAILED
    assert exc_info.value.details["segment_id"] == "s1"
