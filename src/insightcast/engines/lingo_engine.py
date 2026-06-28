from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from insightcast.core.exceptions import InsightCastError
from insightcast.domain.enums import ErrorCode
from insightcast.domain.models import TranscriptSegment
from insightcast.prompts import translation as translation_prompt

TRANSLATION_BATCH_SIZE = 40


class LingoModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TranslationItem(LingoModel):
    segment_id: str = Field(min_length=1)
    text: str = Field(min_length=1)


class TranslationResponse(LingoModel):
    items: list[TranslationItem]


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
    def __init__(self, *, client: Any | None = None, model: str | None = None) -> None:
        self.client = client
        self.model = model

    async def translate_clip(
        self,
        *,
        segments: list[TranscriptSegment],
        clip_start_seconds: float,
        clip_end_seconds: float,
    ) -> list[SubtitleItem]:
        selected = [
            segment
            for segment in segments
            if segment.end_seconds > clip_start_seconds
            and segment.start_seconds < clip_end_seconds
        ]
        if self.client is None or self.model is None:
            raise self._generation_error("Translation client is not configured.")
        translations: list[TranslationItem] = []
        for batch_index, offset in enumerate(
            range(0, len(selected), TRANSLATION_BATCH_SIZE)
        ):
            batch = selected[offset : offset + TRANSLATION_BATCH_SIZE]
            translations.extend(
                await self._translate_batch(
                    batch,
                    batch_index=batch_index,
                    batch_path=[],
                )
            )
        return self.prepare_subtitle_items(
            segments=segments,
            translations=translations,
            clip_start_seconds=clip_start_seconds,
            clip_end_seconds=clip_end_seconds,
        )

    async def _translate_batch(
        self,
        batch: list[TranscriptSegment],
        *,
        batch_index: int,
        batch_path: list[int],
        repair_attempted: bool = False,
    ) -> list[TranslationItem]:
        assert self.client is not None
        assert self.model is not None
        response = await self.client.parse(
            model=self.model,
            system_prompt=translation_prompt.SYSTEM_PROMPT,
            user_prompt=translation_prompt.build_user_prompt(
                items=[
                    {"segment_id": segment.segment_id, "text": segment.text}
                    for segment in batch
                ]
            ),
            response_model=TranslationResponse,
            trace_name="translate_subtitles",
        )
        source_ids = [segment.segment_id for segment in batch]
        translation_ids = [translation.segment_id for translation in response.items]
        unreadable = next(
            (
                translation
                for translation in response.items
                if not _is_readable_translation(translation.text)
            ),
            None,
        )
        if translation_ids == source_ids and unreadable is None:
            return response.items
        validation_error = {
            "source_segment_ids": source_ids,
            "translation_segment_ids": translation_ids,
            "unreadable_segment_id": unreadable.segment_id if unreadable else None,
            "batch_index": batch_index,
            "batch_path": batch_path,
        }
        if not repair_attempted:
            repair_response = await self.client.parse(
                model=self.model,
                system_prompt=translation_prompt.SYSTEM_PROMPT,
                user_prompt=translation_prompt.build_repair_user_prompt(
                    items=[
                        {"segment_id": segment.segment_id, "text": segment.text}
                        for segment in batch
                    ],
                    validation_error=validation_error,
                ),
                response_model=TranslationResponse,
                trace_name="translate_subtitles_repair",
            )
            repair_ids = [translation.segment_id for translation in repair_response.items]
            repair_unreadable = next(
                (
                    translation
                    for translation in repair_response.items
                    if not _is_readable_translation(translation.text)
                ),
                None,
            )
            if repair_ids == source_ids and repair_unreadable is None:
                return repair_response.items
            translation_ids = repair_ids
            unreadable = repair_unreadable
        if len(batch) > 1:
            midpoint = len(batch) // 2
            left = await self._translate_batch(
                batch[:midpoint],
                batch_index=batch_index,
                batch_path=[*batch_path, 0],
                repair_attempted=False,
            )
            right = await self._translate_batch(
                batch[midpoint:],
                batch_index=batch_index,
                batch_path=[*batch_path, 1],
                repair_attempted=False,
            )
            return left + right
        if unreadable is not None:
            raise self._generation_error(
                "Translation must contain readable text.",
                batch_index=batch_index,
                batch_path=batch_path,
                segment_id=unreadable.segment_id,
                translation_text=unreadable.text,
            )
        raise self._generation_error(
            "Translation batch must map one-to-one to source subtitle items.",
            batch_index=batch_index,
            batch_path=batch_path,
            source_segment_ids=source_ids,
            translation_segment_ids=translation_ids,
        )

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
            translated_text = translation.text.strip()
            if not _is_readable_translation(translated_text):
                raise self._generation_error(
                    "Translation must contain readable text.",
                    segment_id=segment.segment_id,
                )
            absolute_start = max(segment.start_seconds, clip_start_seconds)
            absolute_end = min(segment.end_seconds, clip_end_seconds)
            items.append(
                SubtitleItem(
                    segment_id=segment.segment_id,
                    start_seconds=absolute_start - clip_start_seconds,
                    end_seconds=absolute_end - clip_start_seconds,
                    english_text=segment.text,
                    traditional_chinese_text=translated_text,
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


def _is_readable_translation(text: str) -> bool:
    stripped = text.strip()
    return bool(stripped) and any(character.isalnum() for character in stripped)
