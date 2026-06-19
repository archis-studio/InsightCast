import pytest

from insightcast.core.exceptions import InsightCastError
from insightcast.domain.enums import ErrorCode
from insightcast.domain.models import TranscriptSegment
from insightcast.engines.lingo_engine import SubtitleItem
from insightcast.engines.render_validator import RenderValidator


def test_render_validator_accepts_complete_artifacts(tmp_path) -> None:
    render_dir = tmp_path / "render"
    render_dir.mkdir()
    (render_dir / "video.mp4").write_bytes(b"video")
    (render_dir / "subtitles.zh-TW.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n你好\n",
        encoding="utf-8",
    )
    (render_dir / "subtitles.bilingual.ass").write_text(
        "[Script Info]\n",
        encoding="utf-8",
    )
    (render_dir / "youtube-metadata.json").write_text("{}", encoding="utf-8")

    RenderValidator().validate(
        render_dir=render_dir,
        expected_segments=[
            TranscriptSegment(segment_id="s1", start_seconds=0, end_seconds=1, text="Hello")
        ],
        subtitle_items=[
            SubtitleItem(
                segment_id="s1",
                start_seconds=0,
                end_seconds=1,
                english_text="Hello",
                traditional_chinese_text="你好",
            )
        ],
    )


def test_render_validator_rejects_missing_segment_mapping(tmp_path) -> None:
    render_dir = tmp_path / "render"
    render_dir.mkdir()

    with pytest.raises(InsightCastError) as exc_info:
        RenderValidator().validate(
            render_dir=render_dir,
            expected_segments=[
                TranscriptSegment(
                    segment_id="s1",
                    start_seconds=0,
                    end_seconds=1,
                    text="Hello",
                )
            ],
            subtitle_items=[],
        )

    assert exc_info.value.error_code == ErrorCode.RENDER_ARTIFACT_INVALID
    assert exc_info.value.details["expected_segment_ids"] == ["s1"]
    assert exc_info.value.details["actual_segment_ids"] == []
