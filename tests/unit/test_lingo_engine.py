import json

import pytest

from insightcast.core.exceptions import InsightCastError
from insightcast.domain.enums import ErrorCode
from insightcast.domain.models import TranscriptSegment
from insightcast.engines.lingo_engine import (
    LingoEngine,
    TranslationItem,
    TranslationResponse,
)


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
            translation_response(*[f"s{index}" for index in range(0, 40)]),
            translation_response(*[f"s{index}" for index in range(40, 80)]),
            translation_response(*[f"s{index}" for index in range(80, 85)]),
        ]
    )

    result = await LingoEngine(client=client, model="gpt-translation").translate_clip(
        segments=segments,
        clip_start_seconds=0,
        clip_end_seconds=85,
    )

    assert len(client.calls) == 3
    assert [
        len(json.loads(str(call["user_prompt"]))["items"])
        for call in client.calls
    ] == [40, 40, 5]
    assert [item.segment_id for item in result] == [
        f"s{index}" for index in range(85)
    ]


@pytest.mark.asyncio
async def test_translate_clip_splits_mismatched_batch_and_preserves_order() -> None:
    segments = [
        TranscriptSegment(
            segment_id=f"s{index}",
            start_seconds=index,
            end_seconds=index + 1,
            text=f"Source {index}",
        )
        for index in range(40)
    ]
    client = RecordingTranslationClient(
        [
            translation_response(*[f"s{index}" for index in range(0, 38)]),
            translation_response(*[f"s{index}" for index in range(0, 20)]),
            translation_response(*[f"s{index}" for index in range(20, 40)]),
        ]
    )

    result = await LingoEngine(client=client, model="gpt-translation").translate_clip(
        segments=segments,
        clip_start_seconds=0,
        clip_end_seconds=40,
    )

    assert [
        len(json.loads(str(call["user_prompt"]))["items"])
        for call in client.calls
    ] == [40, 20, 20]
    assert [item.segment_id for item in result] == [
        f"s{index}" for index in range(40)
    ]


@pytest.mark.asyncio
async def test_translate_clip_reports_terminal_single_item_mapping_mismatch() -> None:
    segment = TranscriptSegment(
        segment_id="s0",
        start_seconds=0,
        end_seconds=1,
        text="Source",
    )
    client = RecordingTranslationClient([TranslationResponse(items=[])])

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
    ] == [2, 1, 1]
    assert [item.traditional_chinese_text for item in result] == ["在範圍內", "限制"]


@pytest.mark.asyncio
async def test_translate_clip_reports_terminal_unreadable_translation() -> None:
    segment = TranscriptSegment(
        segment_id="s0",
        start_seconds=0,
        end_seconds=1,
        text="Within",
    )
    client = RecordingTranslationClient(
        [translation_response_with_text(("s0", "..."))]
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

    items = LingoEngine().prepare_subtitle_items(
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
