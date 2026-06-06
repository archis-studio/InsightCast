import pytest

from insightcast.core.exceptions import InsightCastError
from insightcast.domain.enums import ErrorCode
from insightcast.domain.models import TranscriptSegment
from insightcast.engines.lingo_engine import LingoEngine, TranslationItem


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

