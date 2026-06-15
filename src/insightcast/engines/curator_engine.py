from typing import Any

from pydantic import BaseModel, ConfigDict

from insightcast.core.exceptions import InsightCastError
from insightcast.domain.enums import ErrorCode
from insightcast.domain.models import Candidate, Transcript, TranscriptSegment
from insightcast.prompts import curator, topic_discovery

ACCEPTED_DURATION_TOLERANCE_SECONDS = 60
TOPIC_POOL_MULTIPLIER = 2


class CuratorModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CuratorCandidateOutput(CuratorModel):
    candidate_id: str
    start_seconds: float
    end_seconds: float
    suggested_title: str
    selection_reason: str
    summary: str
    score: float | None = None


class CuratorResponse(CuratorModel):
    candidates: list[CuratorCandidateOutput]


class TopicDiscoveryOutput(CuratorModel):
    topic_id: str
    label: str
    summary: str
    central_claim: str
    importance_reason: str
    start_seconds: float
    end_seconds: float
    importance_score: float


class TopicDiscoveryResponse(CuratorModel):
    topics: list[TopicDiscoveryOutput]


class CurationResult(CuratorModel):
    candidates: list[Candidate]
    model: str
    prompt_version: str


class CuratorEngine:
    def __init__(self, *, client: Any, model: str) -> None:
        self.client = client
        self.model = model

    async def discover_topics(
        self,
        *,
        transcript: Transcript,
        candidate_count: int,
    ) -> TopicDiscoveryResponse:
        topic_pool_size = candidate_count * TOPIC_POOL_MULTIPLIER
        minimum_topic_count = candidate_count + 1
        feedback: str | None = None
        last_response: TopicDiscoveryResponse | None = None

        for attempt in range(2):
            response = await self.client.parse(
                model=self.model,
                system_prompt=topic_discovery.SYSTEM_PROMPT,
                user_prompt=topic_discovery.build_user_prompt(
                    transcript=[
                        segment.model_dump(mode="json") for segment in transcript.segments
                    ],
                    topic_pool_size=topic_pool_size,
                    validation_feedback=feedback,
                ),
                response_model=TopicDiscoveryResponse,
            )
            last_response = response
            errors = self._validate_topics(
                response.topics,
                transcript_duration=transcript.duration_seconds,
                minimum_topic_count=minimum_topic_count,
            )
            if not errors:
                return response
            feedback = "; ".join(errors)
            if attempt == 1:
                break

        assert last_response is not None
        details = {"validation_feedback": feedback}
        if len(last_response.topics) < minimum_topic_count:
            details.update(
                {
                    "minimum_topics": minimum_topic_count,
                    "requested_topic_pool": topic_pool_size,
                    "received_topics": len(last_response.topics),
                }
            )
            raise InsightCastError(
                ErrorCode.INSUFFICIENT_CANDIDATES,
                "The curator could not discover enough valid topics.",
                details=details,
                stage="topic_discovery",
            )
        raise InsightCastError(
            ErrorCode.INVALID_LLM_OUTPUT,
            "The curator returned invalid topic discovery data after one retry.",
            details=details,
            stage="topic_discovery",
        )

    @staticmethod
    def _validate_topics(
        topics: list[TopicDiscoveryOutput],
        *,
        transcript_duration: float,
        minimum_topic_count: int,
    ) -> list[str]:
        errors: list[str] = []
        if len(topics) < minimum_topic_count:
            errors.append(
                f"topic pool must contain at least {minimum_topic_count} topics, "
                f"received {len(topics)}"
            )
        for index, topic in enumerate(topics):
            expected_id = f"T{index + 1}"
            if topic.topic_id != expected_id:
                errors.append(
                    f"topic {index + 1} ID must be {expected_id}, received {topic.topic_id}"
                )
            text_fields = {
                "label": topic.label,
                "summary": topic.summary,
                "central_claim": topic.central_claim,
                "importance_reason": topic.importance_reason,
            }
            for field_name, value in text_fields.items():
                if not value.strip():
                    errors.append(
                        f"topic {topic.topic_id} {field_name} must not be empty"
                    )
            if topic.start_seconds < 0 or topic.end_seconds <= topic.start_seconds:
                errors.append(f"topic {topic.topic_id} has an invalid time range")
            if topic.end_seconds > transcript_duration:
                errors.append(f"topic {topic.topic_id} exceeds transcript duration")
            if not 0 <= topic.importance_score <= 1:
                errors.append(
                    f"topic {topic.topic_id} importance score must be between 0 and 1"
                )
            if (
                index > 0
                and topic.importance_score > topics[index - 1].importance_score
            ):
                errors.append(
                    "topics must be in descending importance order; "
                    f"topic {topic.topic_id} score {topic.importance_score} exceeds "
                    f"the prior score {topics[index - 1].importance_score}"
                )
        return errors

    async def curate(
        self,
        *,
        transcript: Transcript,
        candidate_count: int,
        min_duration_minutes: float,
        max_duration_minutes: float,
    ) -> CurationResult:
        feedback: str | None = None
        last_response: CuratorResponse | None = None
        target_min_duration_seconds = min_duration_minutes * 60
        target_max_duration_seconds = max_duration_minutes * 60
        accepted_min_duration_seconds = max(
            0,
            target_min_duration_seconds - ACCEPTED_DURATION_TOLERANCE_SECONDS,
        )
        accepted_max_duration_seconds = (
            target_max_duration_seconds + ACCEPTED_DURATION_TOLERANCE_SECONDS
        )
        for attempt in range(2):
            response = await self.client.parse(
                model=self.model,
                system_prompt=curator.SYSTEM_PROMPT,
                user_prompt=curator.build_user_prompt(
                    transcript=[
                        segment.model_dump(mode="json") for segment in transcript.segments
                    ],
                    candidate_count=candidate_count,
                    target_min_duration_seconds=target_min_duration_seconds,
                    target_max_duration_seconds=target_max_duration_seconds,
                    accepted_min_duration_seconds=accepted_min_duration_seconds,
                    accepted_max_duration_seconds=accepted_max_duration_seconds,
                    validation_feedback=feedback,
                ),
                response_model=CuratorResponse,
            )
            normalized_candidates, normalization_errors = self._normalize_candidates(
                response.candidates,
                transcript=transcript,
                target_min_duration_seconds=target_min_duration_seconds,
                target_max_duration_seconds=target_max_duration_seconds,
                accepted_min_duration_seconds=accepted_min_duration_seconds,
                accepted_max_duration_seconds=accepted_max_duration_seconds,
            )
            last_response = CuratorResponse(candidates=normalized_candidates)
            errors = self._validate_candidates(
                normalized_candidates,
                transcript_duration=transcript.duration_seconds,
                candidate_count=candidate_count,
                target_min_duration_seconds=target_min_duration_seconds,
                target_max_duration_seconds=target_max_duration_seconds,
                accepted_min_duration_seconds=accepted_min_duration_seconds,
                accepted_max_duration_seconds=accepted_max_duration_seconds,
            )
            errors = normalization_errors + errors
            if not errors:
                return CurationResult(
                    candidates=[
                        Candidate(**candidate.model_dump()) for candidate in normalized_candidates
                    ],
                    model=self.model,
                    prompt_version=curator.PROMPT_VERSION,
                )
            feedback = "; ".join(errors)
            if attempt == 1:
                break

        assert last_response is not None
        if len(last_response.candidates) < candidate_count:
            raise InsightCastError(
                ErrorCode.INSUFFICIENT_CANDIDATES,
                "The curator could not produce the requested number of valid candidates.",
                details={
                    "requested": candidate_count,
                    "received": len(last_response.candidates),
                    "validation_feedback": feedback,
                },
                stage="curating",
            )
        raise InsightCastError(
            ErrorCode.INVALID_LLM_OUTPUT,
            "The curator returned invalid candidate data after one retry.",
            details={"validation_feedback": feedback},
            stage="curating",
        )

    @staticmethod
    def _normalize_candidates(
        candidates: list[CuratorCandidateOutput],
        *,
        transcript: Transcript,
        target_min_duration_seconds: float,
        target_max_duration_seconds: float,
        accepted_min_duration_seconds: float,
        accepted_max_duration_seconds: float,
    ) -> tuple[list[CuratorCandidateOutput], list[str]]:
        normalized: list[CuratorCandidateOutput] = []
        errors: list[str] = []
        for candidate in candidates:
            result = _normalize_candidate(
                candidate,
                segments=transcript.segments,
                target_min_duration_seconds=target_min_duration_seconds,
                target_max_duration_seconds=target_max_duration_seconds,
                accepted_min_duration_seconds=accepted_min_duration_seconds,
                accepted_max_duration_seconds=accepted_max_duration_seconds,
            )
            if result is None:
                duration = candidate.end_seconds - candidate.start_seconds
                errors.append(
                    f"candidate {candidate.candidate_id} could not be normalized: "
                    f"actual duration {duration} seconds; target range "
                    f"{target_min_duration_seconds}-{target_max_duration_seconds} seconds; "
                    f"accepted range {accepted_min_duration_seconds}-"
                    f"{accepted_max_duration_seconds} seconds"
                )
                normalized.append(candidate)
            else:
                normalized.append(result)
        return normalized, errors

    @staticmethod
    def _validate_candidates(
        candidates: list[CuratorCandidateOutput],
        *,
        transcript_duration: float,
        candidate_count: int,
        target_min_duration_seconds: float,
        target_max_duration_seconds: float,
        accepted_min_duration_seconds: float,
        accepted_max_duration_seconds: float,
    ) -> list[str]:
        errors: list[str] = []
        if len(candidates) != candidate_count:
            errors.append(
                f"candidate count must be {candidate_count}, received {len(candidates)}"
            )
        for index, candidate in enumerate(candidates):
            expected_id = _sequential_id(index)
            if candidate.candidate_id != expected_id:
                errors.append(
                    f"candidate {index + 1} ID must be {expected_id}, "
                    f"received {candidate.candidate_id}"
                )
            if candidate.start_seconds < 0 or candidate.end_seconds <= candidate.start_seconds:
                errors.append(f"candidate {candidate.candidate_id} has an invalid time range")
            duration = candidate.end_seconds - candidate.start_seconds
            if not accepted_min_duration_seconds <= duration <= accepted_max_duration_seconds:
                errors.append(
                    f"candidate {candidate.candidate_id} actual duration {duration} seconds; "
                    f"target range {target_min_duration_seconds}-"
                    f"{target_max_duration_seconds} seconds; accepted range "
                    f"{accepted_min_duration_seconds}-{accepted_max_duration_seconds} seconds"
                )
            if candidate.end_seconds > transcript_duration:
                errors.append(
                    f"candidate {candidate.candidate_id} exceeds transcript duration"
                )
            text_fields = {
                "suggested_title": candidate.suggested_title,
                "selection_reason": candidate.selection_reason,
                "summary": candidate.summary,
            }
            for field_name, value in text_fields.items():
                if not value.strip():
                    errors.append(
                        f"candidate {candidate.candidate_id} {field_name} must not be empty"
                    )
        return errors


def _normalize_candidate(
    candidate: CuratorCandidateOutput,
    *,
    segments: list[TranscriptSegment],
    target_min_duration_seconds: float,
    target_max_duration_seconds: float,
    accepted_min_duration_seconds: float,
    accepted_max_duration_seconds: float,
) -> CuratorCandidateOutput | None:
    if candidate.end_seconds <= candidate.start_seconds or not segments:
        return None

    overlapping_indexes = [
        index
        for index, segment in enumerate(segments)
        if segment.end_seconds > candidate.start_seconds
        and segment.start_seconds < candidate.end_seconds
    ]
    if not overlapping_indexes:
        return None

    start_index = overlapping_indexes[0]
    end_index = overlapping_indexes[-1]
    fallback: tuple[int, int] | None = None

    while True:
        duration = _window_duration(segments, start_index, end_index)
        if target_min_duration_seconds <= duration <= target_max_duration_seconds:
            return _with_segment_bounds(candidate, segments, start_index, end_index)
        if accepted_min_duration_seconds <= duration <= accepted_max_duration_seconds:
            fallback = (start_index, end_index)

        if duration < target_min_duration_seconds:
            options: list[tuple[float, int, int]] = []
            if start_index > 0:
                expanded_duration = _window_duration(
                    segments,
                    start_index - 1,
                    end_index,
                )
                if expanded_duration <= accepted_max_duration_seconds:
                    options.append(
                        (
                            abs(
                                segments[start_index - 1].start_seconds
                                - candidate.start_seconds
                            ),
                            start_index - 1,
                            end_index,
                        )
                    )
            if end_index + 1 < len(segments):
                expanded_duration = _window_duration(
                    segments,
                    start_index,
                    end_index + 1,
                )
                if expanded_duration <= accepted_max_duration_seconds:
                    options.append(
                        (
                            abs(
                                segments[end_index + 1].end_seconds
                                - candidate.end_seconds
                            ),
                            start_index,
                            end_index + 1,
                        )
                    )
            if not options:
                break
            _, start_index, end_index = min(options, key=lambda option: option[0])
            duration = _window_duration(segments, start_index, end_index)
            if target_max_duration_seconds < duration <= accepted_max_duration_seconds:
                return _with_segment_bounds(candidate, segments, start_index, end_index)
            continue

        options = []
        if start_index < end_index:
            retained_overlap = _window_overlap(
                segments,
                start_index + 1,
                end_index,
                candidate.start_seconds,
                candidate.end_seconds,
            )
            if retained_overlap > 0:
                options.append(
                    (
                        _segment_overlap(
                            segments[start_index],
                            candidate.start_seconds,
                            candidate.end_seconds,
                        ),
                        1,
                        start_index + 1,
                        end_index,
                    )
                )
            retained_overlap = _window_overlap(
                segments,
                start_index,
                end_index - 1,
                candidate.start_seconds,
                candidate.end_seconds,
            )
            if retained_overlap > 0:
                options.append(
                    (
                        _segment_overlap(
                            segments[end_index],
                            candidate.start_seconds,
                            candidate.end_seconds,
                        ),
                        0,
                        start_index,
                        end_index - 1,
                    )
                )
        if not options:
            break
        _, _, start_index, end_index = min(options, key=lambda option: (option[0], option[1]))
        duration = _window_duration(segments, start_index, end_index)
        if accepted_min_duration_seconds <= duration < target_min_duration_seconds:
            return _with_segment_bounds(candidate, segments, start_index, end_index)

    if fallback is None:
        return None
    return _with_segment_bounds(candidate, segments, *fallback)


def _window_duration(
    segments: list[TranscriptSegment],
    start_index: int,
    end_index: int,
) -> float:
    return segments[end_index].end_seconds - segments[start_index].start_seconds


def _segment_overlap(
    segment: TranscriptSegment,
    start_seconds: float,
    end_seconds: float,
) -> float:
    return max(
        0,
        min(segment.end_seconds, end_seconds)
        - max(segment.start_seconds, start_seconds),
    )


def _window_overlap(
    segments: list[TranscriptSegment],
    start_index: int,
    end_index: int,
    proposed_start_seconds: float,
    proposed_end_seconds: float,
) -> float:
    return sum(
        _segment_overlap(segment, proposed_start_seconds, proposed_end_seconds)
        for segment in segments[start_index : end_index + 1]
    )


def _with_segment_bounds(
    candidate: CuratorCandidateOutput,
    segments: list[TranscriptSegment],
    start_index: int,
    end_index: int,
) -> CuratorCandidateOutput:
    return candidate.model_copy(
        update={
            "start_seconds": segments[start_index].start_seconds,
            "end_seconds": segments[end_index].end_seconds,
        }
    )


def _sequential_id(index: int) -> str:
    value = index + 1
    result = ""
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result
