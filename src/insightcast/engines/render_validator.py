from pathlib import Path

from insightcast.core.exceptions import InsightCastError
from insightcast.domain.enums import ErrorCode
from insightcast.domain.models import TranscriptSegment
from insightcast.engines.lingo_engine import SubtitleItem


class RenderValidator:
    def validate(
        self,
        *,
        render_dir: Path,
        expected_segments: list[TranscriptSegment],
        subtitle_items: list[SubtitleItem],
    ) -> None:
        expected_ids = [segment.segment_id for segment in expected_segments]
        actual_ids = [item.segment_id for item in subtitle_items]
        if actual_ids != expected_ids:
            raise InsightCastError(
                ErrorCode.RENDER_ARTIFACT_INVALID,
                "Rendered subtitles do not match selected transcript segments.",
                details={
                    "expected_segment_ids": expected_ids,
                    "actual_segment_ids": actual_ids,
                },
                stage="validate_render",
            )
        for item in subtitle_items:
            if item.end_seconds <= item.start_seconds or item.start_seconds < 0:
                raise InsightCastError(
                    ErrorCode.SUBTITLE_FILE_INVALID,
                    "Rendered subtitle timing is invalid.",
                    details={"segment_id": item.segment_id},
                    stage="validate_render",
                )
            if not item.traditional_chinese_text.strip():
                raise InsightCastError(
                    ErrorCode.SUBTITLE_FILE_INVALID,
                    "Rendered subtitle text is empty.",
                    details={"segment_id": item.segment_id},
                    stage="validate_render",
                )
        required = {
            "video": render_dir / "video.mp4",
            "traditional_chinese_srt": render_dir / "subtitles.zh-TW.srt",
            "bilingual_ass": render_dir / "subtitles.bilingual.ass",
            "youtube_metadata": render_dir / "youtube-metadata.json",
        }
        missing = [
            name
            for name, path in required.items()
            if not path.is_file() or path.stat().st_size <= 0
        ]
        if missing:
            raise InsightCastError(
                ErrorCode.RENDER_ARTIFACT_INVALID,
                "Rendered artifacts are missing or empty.",
                details={"missing_or_empty": missing},
                stage="validate_render",
            )
