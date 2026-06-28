import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict

from insightcast.core.exceptions import InsightCastError
from insightcast.domain.enums import ErrorCode
from insightcast.domain.models import Candidate, Transcript, TranscriptSegment
from insightcast.infrastructure.openai_client import emit_llm_telemetry
from insightcast.prompts import curator, topic_discovery
from insightcast.prompts.serialization import (
    compact_json,
    serialize_transcript_segments_for_prompt,
)

ACCEPTED_DURATION_TOLERANCE_SECONDS = 60
FINAL_DURATION_SEGMENT_TOLERANCE_SECONDS = 30
TOPIC_POOL_MULTIPLIER = 2
TOPIC_PRE_BUFFER_SECONDS = 120
TOPIC_POST_BUFFER_SECONDS = 180
DISCOVERY_PROMPT_CHAR_BUDGET = 24_000
DISCOVERY_WINDOW_SECONDS = 10 * 60
DISCOVERY_WINDOW_SHIFT_SECONDS = 10 * 60
DISCOVERY_MIN_WINDOW_SECONDS = 4 * 60
DISCOVERY_MIN_WINDOW_COUNT = 3
DISCOVERY_WINDOWS_PER_CANDIDATE = 1
SELECTION_PROMPT_CHAR_BUDGET = 36_000
FRAMEWORK_SIGNAL_TERMS = (
    "because",
    "therefore",
    "the reason",
    "what matters",
    "the mistake",
    "the pattern",
    "the framework",
    "the rule",
    "the model",
    "the takeaway",
    "what this means",
    "in other words",
    "the point is",
)
BANTER_SIGNAL_TERMS = (
    "laugh",
    "funny",
    "joke",
    "by the way",
    "welcome",
    "thanks for having me",
    "that reminds me",
    "random",
    "sponsor",
    "subscribe",
)
REPETITION_SIGNAL_TERMS = (
    "as i said",
    "again",
    "like i said",
    "we already",
    "to repeat",
)


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


@dataclass(frozen=True)
class TranscriptPromptPlan:
    segments: list[TranscriptSegment]
    transcript_scope: str
    transcript_is_complete: bool
    windows: list[tuple[float, float]]
    original_segment_count: int

    @property
    def provided_segment_count(self) -> int:
        return len(self.segments)


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
        prompt_plan = _plan_topic_discovery_transcript(
            segments=transcript.segments,
            candidate_count=candidate_count,
            char_budget=DISCOVERY_PROMPT_CHAR_BUDGET,
        )
        serialized_transcript = serialize_transcript_segments_for_prompt(
            prompt_plan.segments
        )
        emit_llm_telemetry(
            {
                "event": "window_plan",
                "trace_name": "topic_discovery",
                "transcript_scope": prompt_plan.transcript_scope,
                "transcript_is_complete": prompt_plan.transcript_is_complete,
                "original_segments": prompt_plan.original_segment_count,
                "provided_segments": prompt_plan.provided_segment_count,
                "window_count": len(prompt_plan.windows),
                "prompt_char_budget": DISCOVERY_PROMPT_CHAR_BUDGET,
                "estimated_transcript_chars": _serialized_transcript_chars(
                    transcript.segments
                ),
                "provided_transcript_chars": _serialized_transcript_chars(
                    prompt_plan.segments
                ),
            }
        )

        for attempt in range(2):
            response = await self.client.parse(
                model=self.model,
                system_prompt=topic_discovery.SYSTEM_PROMPT,
                user_prompt=topic_discovery.build_user_prompt(
                    transcript=serialized_transcript,
                    topic_pool_size=topic_pool_size,
                    validation_feedback=feedback,
                    transcript_scope=prompt_plan.transcript_scope,
                    transcript_is_complete=prompt_plan.transcript_is_complete,
                    window_plan=_window_plan_payload(prompt_plan.windows),
                    original_segment_count=prompt_plan.original_segment_count,
                    provided_segment_count=prompt_plan.provided_segment_count,
                ),
                response_model=TopicDiscoveryResponse,
                trace_name="topic_discovery",
            )
            response = TopicDiscoveryResponse(
                topics=self._normalize_topics(
                    response.topics,
                    minimum_topic_count=minimum_topic_count,
                )
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
    def _normalize_topics(
        topics: list[TopicDiscoveryOutput],
        *,
        minimum_topic_count: int,
    ) -> list[TopicDiscoveryOutput]:
        if len(topics) < minimum_topic_count:
            return topics
        if any(not math.isfinite(topic.importance_score) for topic in topics):
            return topics

        sorted_topics = sorted(
            enumerate(topics),
            key=lambda item: (-item[1].importance_score, item[0]),
        )
        return [
            topic.model_copy(update={"topic_id": f"T{index + 1}"})
            for index, (_, topic) in enumerate(sorted_topics)
        ]

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
            start_is_finite = math.isfinite(topic.start_seconds)
            end_is_finite = math.isfinite(topic.end_seconds)
            score_is_finite = math.isfinite(topic.importance_score)
            if not start_is_finite:
                errors.append(f"topic {topic.topic_id} start_seconds must be finite")
            if not end_is_finite:
                errors.append(f"topic {topic.topic_id} end_seconds must be finite")
            if start_is_finite and end_is_finite:
                if (
                    topic.start_seconds < 0
                    or topic.end_seconds <= topic.start_seconds
                ):
                    errors.append(f"topic {topic.topic_id} has an invalid time range")
                if topic.end_seconds > transcript_duration:
                    errors.append(f"topic {topic.topic_id} exceeds transcript duration")
            if not score_is_finite:
                errors.append(
                    f"topic {topic.topic_id} importance_score must be finite"
                )
            elif not 0 <= topic.importance_score <= 1:
                errors.append(
                    f"topic {topic.topic_id} importance score must be between 0 and 1"
                )
            if (
                index > 0
                and score_is_finite
                and math.isfinite(topics[index - 1].importance_score)
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
        topics = await self.discover_topics(
            transcript=transcript,
            candidate_count=candidate_count,
        )
        return await self.select_candidates(
            transcript=transcript,
            topics=topics,
            candidate_count=candidate_count,
            min_duration_minutes=min_duration_minutes,
            max_duration_minutes=max_duration_minutes,
        )

    async def select_candidates(
        self,
        *,
        transcript: Transcript,
        topics: TopicDiscoveryResponse,
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
        final_min_duration_seconds = max(
            0,
            accepted_min_duration_seconds - FINAL_DURATION_SEGMENT_TOLERANCE_SECONDS,
        )
        final_max_duration_seconds = (
            accepted_max_duration_seconds + FINAL_DURATION_SEGMENT_TOLERANCE_SECONDS
        )
        prompt_plan = _plan_candidate_selection_transcript(
            segments=transcript.segments,
            topics=topics.topics,
            candidate_count=candidate_count,
            target_min_duration_seconds=target_min_duration_seconds,
            final_max_duration_seconds=final_max_duration_seconds,
            char_budget=SELECTION_PROMPT_CHAR_BUDGET,
        )
        serialized_transcript = serialize_transcript_segments_for_prompt(
            prompt_plan.segments
        )
        selection_hints = _build_selection_hints(
            segments=prompt_plan.segments,
            windows=prompt_plan.windows,
        )
        emit_llm_telemetry(
            {
                "event": "window_plan",
                "trace_name": "candidate_selection",
                "transcript_scope": prompt_plan.transcript_scope,
                "transcript_is_complete": prompt_plan.transcript_is_complete,
                "original_segments": prompt_plan.original_segment_count,
                "provided_segments": prompt_plan.provided_segment_count,
                "window_count": len(prompt_plan.windows),
                "prompt_char_budget": SELECTION_PROMPT_CHAR_BUDGET,
                "estimated_transcript_chars": _serialized_transcript_chars(
                    transcript.segments
                ),
                "provided_transcript_chars": _serialized_transcript_chars(
                    prompt_plan.segments
                ),
                "selection_hint_count": len(selection_hints),
                "selection_low_waste_windows": sum(
                    hint["estimated_waste_level"] == "low"
                    for hint in selection_hints
                ),
                "selection_high_waste_windows": sum(
                    hint["estimated_waste_level"] == "high"
                    for hint in selection_hints
                ),
            }
        )
        for attempt in range(2):
            response = await self.client.parse(
                model=self.model,
                system_prompt=curator.SYSTEM_PROMPT,
                user_prompt=curator.build_user_prompt(
                    transcript=serialized_transcript,
                    topics=[topic.model_dump(mode="json") for topic in topics.topics],
                    candidate_count=candidate_count,
                    target_min_duration_seconds=target_min_duration_seconds,
                    target_max_duration_seconds=target_max_duration_seconds,
                    accepted_min_duration_seconds=accepted_min_duration_seconds,
                    accepted_max_duration_seconds=accepted_max_duration_seconds,
                    final_min_duration_seconds=final_min_duration_seconds,
                    final_max_duration_seconds=final_max_duration_seconds,
                    validation_feedback=feedback,
                    transcript_scope=prompt_plan.transcript_scope,
                    transcript_is_complete=prompt_plan.transcript_is_complete,
                    selection_window_plan=_window_plan_payload(prompt_plan.windows),
                    selection_hints=selection_hints,
                    original_segment_count=prompt_plan.original_segment_count,
                    provided_segment_count=prompt_plan.provided_segment_count,
                    source_duration_seconds=transcript.duration_seconds,
                ),
                response_model=CuratorResponse,
                trace_name="candidate_selection",
            )
            normalized_candidates, normalization_errors = self._normalize_candidates(
                response.candidates,
                transcript=transcript,
                target_min_duration_seconds=target_min_duration_seconds,
                target_max_duration_seconds=target_max_duration_seconds,
                accepted_min_duration_seconds=accepted_min_duration_seconds,
                accepted_max_duration_seconds=accepted_max_duration_seconds,
                final_min_duration_seconds=final_min_duration_seconds,
                final_max_duration_seconds=final_max_duration_seconds,
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
                final_min_duration_seconds=final_min_duration_seconds,
                final_max_duration_seconds=final_max_duration_seconds,
            )
            errors = normalization_errors + errors
            if not errors:
                return CurationResult(
                    candidates=[
                        Candidate(**candidate.model_dump())
                        for candidate in normalized_candidates
                    ],
                    model=self.model,
                    prompt_version=(
                        f"{topic_discovery.PROMPT_VERSION}+{curator.PROMPT_VERSION}"
                    ),
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
        final_min_duration_seconds: float,
        final_max_duration_seconds: float,
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
                final_min_duration_seconds=final_min_duration_seconds,
                final_max_duration_seconds=final_max_duration_seconds,
            )
            if result is None:
                duration = candidate.end_seconds - candidate.start_seconds
                errors.append(
                    f"candidate {candidate.candidate_id} could not be normalized: "
                    f"actual duration {duration} seconds; target range "
                    f"{target_min_duration_seconds}-{target_max_duration_seconds} seconds; "
                    f"accepted range {accepted_min_duration_seconds}-"
                    f"{accepted_max_duration_seconds} seconds; final range "
                    f"{final_min_duration_seconds}-{final_max_duration_seconds} seconds"
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
        final_min_duration_seconds: float,
        final_max_duration_seconds: float,
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
            if not final_min_duration_seconds <= duration <= final_max_duration_seconds:
                errors.append(
                    f"candidate {candidate.candidate_id} actual duration {duration} seconds; "
                    f"target range {target_min_duration_seconds}-"
                    f"{target_max_duration_seconds} seconds; accepted range "
                    f"{accepted_min_duration_seconds}-{accepted_max_duration_seconds} seconds; "
                    f"final range {final_min_duration_seconds}-"
                    f"{final_max_duration_seconds} seconds"
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


def _build_topic_windows(
    *,
    segments: Sequence[TranscriptSegment],
    topics: Sequence[TopicDiscoveryOutput],
    target_min_duration_seconds: float,
    final_max_duration_seconds: float,
) -> list[TranscriptSegment]:
    windows = _build_topic_time_windows(
        segments=segments,
        topics=topics,
        target_min_duration_seconds=target_min_duration_seconds,
        final_max_duration_seconds=final_max_duration_seconds,
    )
    return _segments_in_windows(segments, windows)


def _plan_candidate_selection_transcript(
    *,
    segments: Sequence[TranscriptSegment],
    topics: Sequence[TopicDiscoveryOutput],
    candidate_count: int,
    target_min_duration_seconds: float,
    final_max_duration_seconds: float,
    char_budget: int,
) -> TranscriptPromptPlan:
    original = list(segments)
    full_plan = TranscriptPromptPlan(
        segments=original,
        transcript_scope="full_transcript",
        transcript_is_complete=True,
        windows=[],
        original_segment_count=len(original),
    )
    if not original:
        return full_plan

    windows = _build_topic_time_windows(
        segments=original,
        topics=topics,
        target_min_duration_seconds=target_min_duration_seconds,
        final_max_duration_seconds=final_max_duration_seconds,
    )
    if not windows:
        return full_plan

    windows = _select_budgeted_topic_windows(
        segments=original,
        windows=windows,
        char_budget=char_budget,
        minimum_window_count=candidate_count,
    )
    selected_segments = _segments_in_windows(original, windows)
    if not selected_segments or len(selected_segments) >= len(original):
        return full_plan

    return TranscriptPromptPlan(
        segments=selected_segments,
        transcript_scope="budgeted_topic_windows_for_candidate_selection",
        transcript_is_complete=False,
        windows=windows,
        original_segment_count=len(original),
    )


def _build_topic_time_windows(
    *,
    segments: Sequence[TranscriptSegment],
    topics: Sequence[TopicDiscoveryOutput],
    target_min_duration_seconds: float,
    final_max_duration_seconds: float,
) -> list[tuple[float, float]]:
    if not segments:
        return []

    transcript_start = segments[0].start_seconds
    transcript_end = segments[-1].end_seconds
    windows: list[tuple[float, float]] = []
    pre_buffer_seconds = max(
        TOPIC_PRE_BUFFER_SECONDS,
        target_min_duration_seconds / 4,
    )
    post_buffer_seconds = max(
        TOPIC_POST_BUFFER_SECONDS,
        target_min_duration_seconds / 4,
    )

    for topic in topics:
        if not _is_valid_topic_range(topic):
            continue
        if (
            topic.start_seconds >= transcript_end
            or topic.end_seconds <= transcript_start
        ):
            continue
        start = max(transcript_start, topic.start_seconds - pre_buffer_seconds)
        end = min(transcript_end, topic.end_seconds + post_buffer_seconds)
        start, end = _expand_window_to_duration(
            start,
            end,
            minimum_duration_seconds=final_max_duration_seconds,
            transcript_start=transcript_start,
            transcript_end=transcript_end,
        )
        if end > start:
            windows.append((start, end))

    return _merge_time_windows(windows)


def _select_budgeted_topic_windows(
    *,
    segments: Sequence[TranscriptSegment],
    windows: Sequence[tuple[float, float]],
    char_budget: int,
    minimum_window_count: int,
) -> list[tuple[float, float]]:
    selected: list[tuple[float, float]] = []
    for window in windows:
        candidate_windows = [*selected, window]
        candidate_segments = _segments_in_windows(segments, candidate_windows)
        if (
            len(selected) < minimum_window_count
            or _serialized_transcript_chars(candidate_segments) <= char_budget
        ):
            selected.append(window)
            continue
        break
    return selected


def _plan_topic_discovery_transcript(
    *,
    segments: Sequence[TranscriptSegment],
    candidate_count: int,
    char_budget: int,
) -> TranscriptPromptPlan:
    original = list(segments)
    full_chars = _serialized_transcript_chars(original)
    full_plan = TranscriptPromptPlan(
        segments=original,
        transcript_scope="full_transcript",
        transcript_is_complete=True,
        windows=[],
        original_segment_count=len(original),
    )
    if full_chars <= char_budget or not original:
        return full_plan

    windows = _select_budgeted_discovery_windows(
        segments=original,
        max_window_count=max(
            DISCOVERY_MIN_WINDOW_COUNT,
            candidate_count * DISCOVERY_WINDOWS_PER_CANDIDATE,
        ),
        char_budget=char_budget,
    )
    if not windows:
        return full_plan

    selected_segments = _segments_in_windows(original, windows)
    if not selected_segments or len(selected_segments) >= len(original):
        return full_plan

    return TranscriptPromptPlan(
        segments=selected_segments,
        transcript_scope="deterministic_discovery_windows",
        transcript_is_complete=False,
        windows=windows,
        original_segment_count=len(original),
    )


def _select_discovery_windows(
    *,
    segments: Sequence[TranscriptSegment],
    max_window_count: int,
    window_seconds: float = DISCOVERY_WINDOW_SECONDS,
    shift_seconds: float = DISCOVERY_WINDOW_SHIFT_SECONDS,
) -> list[tuple[float, float]]:
    if not segments:
        return []

    transcript_start = segments[0].start_seconds
    transcript_end = segments[-1].end_seconds
    if transcript_end <= transcript_start:
        return []

    raw_windows = _sliding_time_windows(
        transcript_start=transcript_start,
        transcript_end=transcript_end,
        window_seconds=window_seconds,
        shift_seconds=shift_seconds,
    )
    if len(raw_windows) <= max_window_count:
        return _merge_time_windows(raw_windows)

    scored = [
        (_window_text_density(segments, start, end), index, start, end)
        for index, (start, end) in enumerate(raw_windows)
    ]
    selected: dict[int, tuple[float, float]] = {}
    anchor_indexes = {
        0,
        len(raw_windows) - 1,
    }
    for index in sorted(anchor_indexes):
        selected[index] = raw_windows[index]

    for _, index, start, end in sorted(scored, key=lambda item: (-item[0], item[1])):
        if len(selected) >= max_window_count:
            break
        selected[index] = (start, end)

    return [window for _, window in sorted(selected.items(), key=lambda item: item[0])]


def _select_budgeted_discovery_windows(
    *,
    segments: Sequence[TranscriptSegment],
    max_window_count: int,
    char_budget: int,
) -> list[tuple[float, float]]:
    window_seconds = DISCOVERY_WINDOW_SECONDS
    best_windows: list[tuple[float, float]] = []
    while window_seconds >= DISCOVERY_MIN_WINDOW_SECONDS:
        windows = _select_discovery_windows(
            segments=segments,
            max_window_count=max_window_count,
            window_seconds=window_seconds,
            shift_seconds=window_seconds,
        )
        selected_segments = _segments_in_windows(segments, windows)
        if selected_segments and _serialized_transcript_chars(selected_segments) <= char_budget:
            return windows
        if selected_segments:
            best_windows = windows
        window_seconds = math.floor(window_seconds * 0.8)

    return best_windows


def _segments_in_windows(
    segments: Sequence[TranscriptSegment],
    windows: Sequence[tuple[float, float]],
) -> list[TranscriptSegment]:
    return [
        segment
        for segment in segments
        if any(
            segment.end_seconds > start and segment.start_seconds < end
            for start, end in windows
        )
    ]


def _sliding_time_windows(
    *,
    transcript_start: float,
    transcript_end: float,
    window_seconds: float,
    shift_seconds: float,
) -> list[tuple[float, float]]:
    duration = transcript_end - transcript_start
    if duration <= window_seconds:
        return [(transcript_start, transcript_end)]

    windows: list[tuple[float, float]] = []
    start = transcript_start
    while start < transcript_end:
        end = min(transcript_end, start + window_seconds)
        windows.append((start, end))
        if end >= transcript_end:
            break
        start += shift_seconds

    final_start = max(transcript_start, transcript_end - window_seconds)
    if windows[-1][0] < final_start:
        windows.append((final_start, transcript_end))
    return windows


def _window_text_density(
    segments: Sequence[TranscriptSegment],
    start_seconds: float,
    end_seconds: float,
) -> float:
    text_chars = sum(
        len(segment.text.strip())
        for segment in segments
        if segment.end_seconds > start_seconds and segment.start_seconds < end_seconds
    )
    duration = max(1.0, end_seconds - start_seconds)
    return text_chars / duration


def _serialized_transcript_chars(segments: Sequence[TranscriptSegment]) -> int:
    return len(
        compact_json(
            {"transcript": serialize_transcript_segments_for_prompt(segments)}
        )
    )


def _window_plan_payload(
    windows: Sequence[tuple[float, float]],
) -> list[dict[str, float]]:
    return [
        {
            "start": round(start, 3),
            "end": round(end, 3),
        }
        for start, end in windows
    ]


def _build_selection_hints(
    *,
    segments: Sequence[TranscriptSegment],
    windows: Sequence[tuple[float, float]],
) -> list[dict[str, Any]]:
    if not segments:
        return []
    hint_windows = list(windows) or [(segments[0].start_seconds, segments[-1].end_seconds)]
    hints: list[dict[str, Any]] = []
    for start, end in hint_windows:
        window_segments = [
            segment
            for segment in segments
            if segment.end_seconds > start and segment.start_seconds < end
        ]
        if not window_segments:
            continue
        text = " ".join(segment.text for segment in window_segments).lower()
        duration_minutes = max((end - start) / 60, 1 / 60)
        framework_signal_count = _count_terms(text, FRAMEWORK_SIGNAL_TERMS)
        banter_signal_count = _count_terms(text, BANTER_SIGNAL_TERMS)
        repetition_signal_count = _count_terms(text, REPETITION_SIGNAL_TERMS)
        waste_signal_count = banter_signal_count + repetition_signal_count
        hints.append(
            {
                "start": round(start, 3),
                "end": round(end, 3),
                "segment_count": len(window_segments),
                "speech_chars": len(text),
                "speech_chars_per_minute": round(len(text) / duration_minutes, 2),
                "framework_signal_count": framework_signal_count,
                "banter_signal_count": banter_signal_count,
                "repetition_signal_count": repetition_signal_count,
                "estimated_waste_level": _estimated_waste_level(
                    framework_signal_count=framework_signal_count,
                    waste_signal_count=waste_signal_count,
                ),
            }
        )
    return hints


def _count_terms(text: str, terms: Sequence[str]) -> int:
    return sum(text.count(term) for term in terms)


def _estimated_waste_level(
    *,
    framework_signal_count: int,
    waste_signal_count: int,
) -> str:
    if waste_signal_count >= max(4, framework_signal_count):
        return "high"
    if waste_signal_count >= max(2, framework_signal_count / 2):
        return "medium"
    return "low"


def _is_valid_topic_range(topic: TopicDiscoveryOutput) -> bool:
    return (
        math.isfinite(topic.start_seconds)
        and math.isfinite(topic.end_seconds)
        and topic.start_seconds >= 0
        and topic.end_seconds > topic.start_seconds
    )


def _expand_window_to_duration(
    start: float,
    end: float,
    *,
    minimum_duration_seconds: float,
    transcript_start: float,
    transcript_end: float,
) -> tuple[float, float]:
    available_duration = transcript_end - transcript_start
    target_duration = min(minimum_duration_seconds, available_duration)
    current_duration = end - start
    if current_duration >= target_duration:
        return start, end

    missing = target_duration - current_duration
    expanded_start = max(transcript_start, start - missing / 2)
    expanded_end = min(transcript_end, end + missing / 2)

    remaining = target_duration - (expanded_end - expanded_start)
    if remaining > 0 and expanded_start == transcript_start:
        expanded_end = min(transcript_end, expanded_end + remaining)
    elif remaining > 0 and expanded_end == transcript_end:
        expanded_start = max(transcript_start, expanded_start - remaining)

    return expanded_start, expanded_end


def _merge_time_windows(windows: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    ordered = sorted(windows)
    merged: list[tuple[float, float]] = []
    for start, end in ordered:
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
            continue
        prior_start, prior_end = merged[-1]
        merged[-1] = (prior_start, max(prior_end, end))
    return merged


def _normalize_candidate(
    candidate: CuratorCandidateOutput,
    *,
    segments: list[TranscriptSegment],
    target_min_duration_seconds: float,
    target_max_duration_seconds: float,
    accepted_min_duration_seconds: float,
    accepted_max_duration_seconds: float,
    final_min_duration_seconds: float,
    final_max_duration_seconds: float,
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

    overlap_prefix = [0.0]
    for segment in segments:
        overlap_prefix.append(
            overlap_prefix[-1]
            + _segment_overlap(
                segment,
                candidate.start_seconds,
                candidate.end_seconds,
            )
        )

    first_overlap_index = overlapping_indexes[0]
    last_overlap_index = overlapping_indexes[-1]
    options: list[tuple[int, float, float, int, int]] = []
    for start_index in range(last_overlap_index + 1):
        start_segment = segments[start_index]
        first_end_index = max(start_index, first_overlap_index)
        for end_index in range(first_end_index, len(segments)):
            duration = _window_duration(segments, start_index, end_index)
            if duration > final_max_duration_seconds:
                break
            if duration < final_min_duration_seconds:
                continue
            retained_overlap = overlap_prefix[end_index + 1] - overlap_prefix[start_index]
            if retained_overlap <= 0:
                continue
            if target_min_duration_seconds <= duration <= target_max_duration_seconds:
                duration_tier = 0
            elif accepted_min_duration_seconds <= duration <= accepted_max_duration_seconds:
                duration_tier = 1
            else:
                duration_tier = 2
            boundary_distance = (
                abs(start_segment.start_seconds - candidate.start_seconds)
                + abs(segments[end_index].end_seconds - candidate.end_seconds)
            )
            options.append(
                (
                    duration_tier,
                    -retained_overlap,
                    boundary_distance,
                    start_index,
                    end_index,
                )
            )

    if not options:
        return None
    _, _, _, start_index, end_index = min(options)
    return _with_segment_bounds(candidate, segments, start_index, end_index)


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
