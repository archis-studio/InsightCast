from pydantic import BaseModel, ConfigDict, Field, model_validator

from insightcast.core.exceptions import InsightCastError
from insightcast.domain.enums import ErrorCode
from insightcast.domain.models import TranscriptSegment


class LingoModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TranslationItem(LingoModel):
    segment_id: str = Field(min_length=1)
    text: str = Field(min_length=1)


class SubtitleItem(LingoModel):
    segment_id: str
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    english_text: str = Field(min_length=1)
    traditional_chinese_text: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_timing(self) -> "SubtitleItem":
        if self.end_seconds <= self.start_seconds:
            raise ValueError("end_seconds must be later than start_seconds")
        return self


class LingoEngine:
    def prepare_subtitle_items(
        self,
        *,
        segments: list[TranscriptSegment],
        translations: list[TranslationItem],
        clip_start_seconds: float,
        clip_end_seconds: float,
    ) -> list[SubtitleItem]:
        if clip_end_seconds <= clip_start_seconds:
            raise self._generation_error("Clip end must be later than clip start.")

        selected = [
            segment
            for segment in segments
            if segment.end_seconds > clip_start_seconds
            and segment.start_seconds < clip_end_seconds
        ]
        source_ids = [segment.segment_id for segment in selected]
        translation_ids = [translation.segment_id for translation in translations]
        if translation_ids != source_ids:
            raise self._generation_error(
                "Translation items must map one-to-one to source subtitle items.",
                source_segment_ids=source_ids,
                translation_segment_ids=translation_ids,
            )

        items: list[SubtitleItem] = []
        for segment, translation in zip(selected, translations, strict=True):
            absolute_start = max(segment.start_seconds, clip_start_seconds)
            absolute_end = min(segment.end_seconds, clip_end_seconds)
            items.append(
                SubtitleItem(
                    segment_id=segment.segment_id,
                    start_seconds=absolute_start - clip_start_seconds,
                    end_seconds=absolute_end - clip_start_seconds,
                    english_text=segment.text,
                    traditional_chinese_text=translation.text,
                )
            )
        return items

    @staticmethod
    def _generation_error(message: str, **details: object) -> InsightCastError:
        return InsightCastError(
            ErrorCode.SUBTITLE_GENERATION_FAILED,
            message,
            details=details,
            stage="subtitle_generation",
        )

