from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from insightcast.core.exceptions import InsightCastError
from insightcast.domain.enums import ErrorCode
from insightcast.domain.models import TranscriptSegment
from insightcast.infrastructure.openai_client import emit_llm_telemetry
from insightcast.prompts import translation as translation_prompt

TRANSLATION_BATCH_SIZE = 24


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


class SubtitleTimingPolicy(LingoModel):
    enabled: bool = True
    offset_seconds: float = -0.12
    min_duration_seconds: float = Field(default=0.75, ge=0)
    max_extension_seconds: float = Field(default=0.30, ge=0)
    min_gap_seconds: float = Field(default=0.08, ge=0)

    def normalize(
        self,
        items: list[SubtitleItem],
        *,
        clip_duration_seconds: float,
    ) -> list[SubtitleItem]:
        if not self.enabled or not items:
            return items
        shifted_starts = [
            _clamp(item.start_seconds + self.offset_seconds, 0, clip_duration_seconds)
            for item in items
        ]
        normalized: list[SubtitleItem] = []
        for index, item in enumerate(items):
            start = shifted_starts[index]
            shifted_end = _clamp(
                item.end_seconds + self.offset_seconds,
                0,
                clip_duration_seconds,
            )
            current_duration = max(0.0, shifted_end - start)
            extension = min(
                max(0.0, self.min_duration_seconds - current_duration),
                self.max_extension_seconds,
            )
            end = shifted_end + extension
            if index + 1 < len(items):
                end = min(end, shifted_starts[index + 1] - self.min_gap_seconds)
            end = _clamp(end, start, clip_duration_seconds)
            if end <= start:
                end = min(clip_duration_seconds, start + 0.001)
            normalized.append(
                item.model_copy(
                    update={
                        "start_seconds": round(start, 3),
                        "end_seconds": round(end, 3),
                    }
                )
            )
        return normalized


class LingoEngine:
    def __init__(
        self,
        *,
        client: Any | None = None,
        model: str | None = None,
        batch_size: int = TRANSLATION_BATCH_SIZE,
        timing_policy: SubtitleTimingPolicy | None = None,
    ) -> None:
        self.client = client
        self.model = model
        self.batch_size = max(1, batch_size)
        self.timing_policy = timing_policy or SubtitleTimingPolicy()

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
        for batch_index, offset in enumerate(range(0, len(selected), self.batch_size)):
            batch = selected[offset : offset + self.batch_size]
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
        reason = _translation_validation_reason(
            source_ids=source_ids,
            translation_ids=translation_ids,
            unreadable_segment_id=unreadable.segment_id if unreadable else None,
        )
        emit_llm_telemetry(
            {
                "event": "validation_failed",
                "trace_name": "translate_subtitles",
                "reason": reason,
                "batch_index": batch_index,
                "batch_path": batch_path,
                "source_count": len(source_ids),
                "translation_count": len(translation_ids),
            }
        )
        salvaged = _salvage_translation_items(response.items, source_ids)
        if salvaged is not None:
            return salvaged
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
        return self.timing_policy.normalize(
            items,
            clip_duration_seconds=clip_end_seconds - clip_start_seconds,
        )

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


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(max(value, minimum), maximum)


def _salvage_translation_items(
    translations: list[TranslationItem],
    source_ids: list[str],
) -> list[TranslationItem] | None:
    by_source_id: dict[str, TranslationItem] = {}
    for translation in translations:
        if translation.segment_id not in source_ids:
            continue
        if translation.segment_id in by_source_id:
            continue
        if not _is_readable_translation(translation.text):
            continue
        by_source_id[translation.segment_id] = translation
    if set(by_source_id) != set(source_ids):
        return None
    return [by_source_id[source_id] for source_id in source_ids]


def _translation_validation_reason(
    *,
    source_ids: list[str],
    translation_ids: list[str],
    unreadable_segment_id: str | None,
) -> str:
    if unreadable_segment_id is not None:
        return "unreadable"
    source_set = set(source_ids)
    translation_set = set(translation_ids)
    if len(translation_ids) != len(set(translation_ids)):
        return "duplicate_ids"
    if not source_set.issubset(translation_set):
        return "missing_ids"
    if translation_set - source_set:
        return "extra_ids"
    if translation_ids != source_ids:
        return "reordered_ids"
    return "mapping_mismatch"
