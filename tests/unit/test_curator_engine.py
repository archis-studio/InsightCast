import json

import pytest
from pydantic import BaseModel

from insightcast.core.exceptions import InsightCastError
from insightcast.domain.enums import ErrorCode
from insightcast.domain.models import Transcript, TranscriptSegment
from insightcast.engines import curator_engine
from insightcast.engines.curator_engine import (
    CuratorCandidateOutput,
    CuratorEngine,
    CuratorResponse,
    SelectionReviewCandidateOutput,
    SelectionReviewResponse,
    TopicDiscoveryOutput,
    TopicDiscoveryResponse,
)
from insightcast.infrastructure.openai_client import capture_llm_telemetry


class FakeStructuredClient:
    def __init__(self, responses: list[BaseModel]) -> None:
        self.responses = responses
        self.calls: list[dict[str, object]] = []

    async def parse(self, **kwargs: object) -> BaseModel:
        self.calls.append(kwargs)
        return self.responses.pop(0)


def segmented_transcript(*bounds: tuple[float, float]) -> Transcript:
    return Transcript(
        language="en",
        duration_seconds=bounds[-1][1],
        segments=[
            TranscriptSegment(
                segment_id=f"s{index}",
                start_seconds=start,
                end_seconds=end,
                text=f"Segment {index}",
            )
            for index, (start, end) in enumerate(bounds, start=1)
        ],
    )


def transcript(duration: float = 1800) -> Transcript:
    return segmented_transcript(
        *[
            (start, min(start + 300, duration))
            for start in range(0, int(duration), 300)
        ]
    )


def output(
    candidate_id: str,
    start: float,
    end: float,
    *,
    title: str = "Title",
    score: float | None = 0.9,
    boundary_ending_type: str = "conclusion",
) -> CuratorCandidateOutput:
    return CuratorCandidateOutput(
        candidate_id=candidate_id,
        start_seconds=start,
        end_seconds=end,
        suggested_title=title,
        selection_reason="Complete idea arc",
        summary="Useful summary",
        core_claim="Core claim",
        payoff="Viewer payoff",
        argument_arc=["setup", "claim", "evidence", "conclusion"],
        boundary_start_reason="Starts at useful setup",
        boundary_end_reason="Ends at natural conclusion",
        boundary_ending_type=boundary_ending_type,
        score=score,
    )


def topic(
    topic_id: str,
    start: float,
    end: float,
    score: float,
) -> TopicDiscoveryOutput:
    return TopicDiscoveryOutput(
        topic_id=topic_id,
        label=f"Topic {topic_id}",
        summary=f"Summary for {topic_id}",
        central_claim=f"Central claim for {topic_id}",
        importance_reason=f"Importance reason for {topic_id}",
        start_seconds=start,
        end_seconds=end,
        importance_score=score,
    )


def valid_topics(candidate_count: int = 1) -> TopicDiscoveryResponse:
    topic_count = candidate_count * 2
    return TopicDiscoveryResponse(
        topics=[
            topic(
                f"T{index + 1}",
                index * 300,
                (index + 1) * 300,
                1 - (index / topic_count),
            )
            for index in range(topic_count)
        ]
    )


@pytest.mark.asyncio
async def test_discover_topics_requests_larger_ranked_pool() -> None:
    client = FakeStructuredClient(
        [
            TopicDiscoveryResponse(
                topics=[
                    topic("T1", 0, 300, 0.9),
                    topic("T2", 300, 600, 0.8),
                    topic("T3", 600, 900, 0.7),
                    topic("T4", 900, 1200, 0.6),
                ]
            )
        ]
    )

    result = await CuratorEngine(client=client, model="gpt-curator").discover_topics(
        transcript=transcript(),
        candidate_count=2,
    )

    assert [item.topic_id for item in result.topics] == ["T1", "T2", "T3", "T4"]
    user_prompt = str(client.calls[0]["user_prompt"])
    assert '"topic_pool_size":4' in user_prompt
    assert '"id":"s1"' in user_prompt
    assert "segment_id" not in user_prompt
    assert "start_seconds" not in user_prompt
    assert client.calls[0]["response_model"] is TopicDiscoveryResponse


@pytest.mark.asyncio
async def test_discover_topics_sorts_valid_topics_by_importance_score() -> None:
    client = FakeStructuredClient(
        [
            TopicDiscoveryResponse(
                topics=[
                    topic("T1", 0, 300, 0.93),
                    topic("T2", 300, 600, 0.91),
                    topic("T3", 600, 900, 0.96),
                    topic("T4", 900, 1200, 0.80),
                ]
            )
        ]
    )

    result = await CuratorEngine(client=client, model="gpt-curator").discover_topics(
        transcript=transcript(),
        candidate_count=2,
    )

    assert len(client.calls) == 1
    assert [item.topic_id for item in result.topics] == ["T1", "T2", "T3", "T4"]
    assert [item.importance_score for item in result.topics] == [0.96, 0.93, 0.91, 0.80]
    assert result.topics[0].label == "Topic T3"


@pytest.mark.asyncio
async def test_discover_topics_retries_with_specific_validation_feedback() -> None:
    client = FakeStructuredClient(
        [
            TopicDiscoveryResponse(
                topics=[
                    topic("T2", 0, 300, 0.2),
                    topic("T1", 300, 600, 0.8),
                ]
            ),
            TopicDiscoveryResponse(
                topics=[
                    topic("T1", 0, 300, 0.9),
                    topic("T2", 300, 600, 0.8),
                    topic("T3", 600, 900, 0.7),
                    topic("T4", 900, 1200, 0.6),
                ]
            ),
        ]
    )

    result = await CuratorEngine(client=client, model="gpt-curator").discover_topics(
        transcript=transcript(),
        candidate_count=2,
    )

    assert len(client.calls) == 2
    assert len(result.topics) == 4
    retry_prompt = str(client.calls[1]["user_prompt"])
    assert "topic pool must contain at least 3 topics" in retry_prompt
    assert "topic 1 ID must be T1" in retry_prompt
    assert "descending importance order" in retry_prompt


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("start_seconds", float("nan")),
        ("start_seconds", float("inf")),
        ("end_seconds", float("nan")),
        ("end_seconds", float("inf")),
        ("importance_score", float("nan")),
        ("importance_score", float("inf")),
    ],
)
async def test_discover_topics_retries_when_numeric_fields_are_not_finite(
    field_name: str,
    invalid_value: float,
) -> None:
    invalid_topics = [
        topic("T1", 0, 300, 0.9),
        topic("T2", 300, 600, 0.8),
        topic("T3", 600, 900, 0.7),
        topic("T4", 900, 1200, 0.6),
    ]
    invalid_topics[0] = invalid_topics[0].model_copy(
        update={field_name: invalid_value}
    )
    valid_topics = [
        topic("T1", 0, 300, 0.9),
        topic("T2", 300, 600, 0.8),
        topic("T3", 600, 900, 0.7),
        topic("T4", 900, 1200, 0.6),
    ]
    client = FakeStructuredClient(
        [
            TopicDiscoveryResponse(topics=invalid_topics),
            TopicDiscoveryResponse(topics=valid_topics),
        ]
    )

    result = await CuratorEngine(client=client, model="gpt-curator").discover_topics(
        transcript=transcript(),
        candidate_count=2,
    )

    assert len(client.calls) == 2
    assert len(result.topics) == 4
    assert f"{field_name} must be finite" in str(client.calls[1]["user_prompt"])


@pytest.mark.asyncio
async def test_discover_topics_raises_insufficient_candidates_after_retry() -> None:
    undersized = TopicDiscoveryResponse(
        topics=[
            topic("T1", 0, 300, 0.9),
            topic("T2", 300, 600, 0.8),
        ]
    )
    client = FakeStructuredClient([undersized, undersized])

    with pytest.raises(InsightCastError) as exc_info:
        await CuratorEngine(client=client, model="gpt-curator").discover_topics(
            transcript=transcript(),
            candidate_count=2,
        )

    error = exc_info.value
    assert error.error_code == ErrorCode.INSUFFICIENT_CANDIDATES
    assert error.stage == "topic_discovery"
    assert error.message == "The curator could not discover enough valid topics."
    assert error.details == {
        "minimum_topics": 3,
        "requested_topic_pool": 4,
        "received_topics": 2,
        "validation_feedback": "topic pool must contain at least 3 topics, received 2",
    }


@pytest.mark.asyncio
async def test_discover_topics_raises_invalid_llm_output_after_retry() -> None:
    invalid = TopicDiscoveryResponse(
        topics=[
            topic("T1", 0, 300, 0.9).model_copy(update={"label": " "}),
            topic("T2", 300, 600, 0.8),
            topic("T3", 600, 900, 0.7),
            topic("T4", 900, 1200, 0.6),
        ]
    )
    client = FakeStructuredClient([invalid, invalid])

    with pytest.raises(InsightCastError) as exc_info:
        await CuratorEngine(client=client, model="gpt-curator").discover_topics(
            transcript=transcript(),
            candidate_count=2,
        )

    error = exc_info.value
    assert error.error_code == ErrorCode.INVALID_LLM_OUTPUT
    assert error.stage == "topic_discovery"
    assert (
        error.message
        == "The curator returned invalid topic discovery data after one retry."
    )
    assert error.details == {
        "validation_feedback": "topic T1 label must not be empty"
    }


@pytest.mark.asyncio
async def test_curate_discovers_topics_then_selects_candidates() -> None:
    client = FakeStructuredClient(
        [
            valid_topics(),
            CuratorResponse(candidates=[output("A", 0, 600)]),
        ]
    )

    result = await CuratorEngine(client=client, model="gpt-curator").curate(
        transcript=transcript(),
        candidate_count=1,
        min_duration_minutes=8,
        max_duration_minutes=12,
    )

    assert len(client.calls) == 2
    assert client.calls[0]["response_model"] is TopicDiscoveryResponse
    assert client.calls[1]["response_model"] is CuratorResponse
    candidate_payload = json.loads(str(client.calls[1]["user_prompt"]))
    assert candidate_payload["topics"][0]["topic_id"] == "T1"
    assert candidate_payload["topics"][1]["topic_id"] == "T2"
    assert result.prompt_version == "topic-discovery-v3+curator-v6"


@pytest.mark.asyncio
async def test_curator_accepts_exact_ordered_candidates_and_overlap() -> None:
    client = FakeStructuredClient(
        [
            CuratorResponse(
                candidates=[
                    output("A", 0, 600),
                    output("B", 500, 1100),
                ]
            )
        ]
    )
    engine = CuratorEngine(client=client, model="gpt-curator")

    result = await engine.select_candidates(
        transcript=transcript(),
        topics=valid_topics(candidate_count=2),
        candidate_count=2,
        min_duration_minutes=8,
        max_duration_minutes=12,
    )

    assert [candidate.candidate_id for candidate in result.candidates] == ["A", "B"]
    assert result.model == "gpt-curator"
    assert result.prompt_version


@pytest.mark.asyncio
async def test_select_candidates_sends_windowed_transcript_to_boundary_prompt() -> None:
    source = segmented_transcript(
        *[(second, second + 60) for second in range(0, 2400, 60)]
    )
    client = FakeStructuredClient(
        [CuratorResponse(candidates=[output("A", 300, 900)])]
    )

    await CuratorEngine(client=client, model="gpt-curator").select_candidates(
        transcript=source,
        topics=TopicDiscoveryResponse(
            topics=[topic("T1", 600, 660, 0.9)]
        ),
        candidate_count=1,
        min_duration_minutes=8,
        max_duration_minutes=12,
    )

    payload = json.loads(str(client.calls[0]["user_prompt"]))
    segment_ids = [segment["id"] for segment in payload["transcript"]]
    assert (
        payload["transcript_scope"]
        == "budgeted_topic_windows_for_candidate_selection"
    )
    assert payload["transcript_is_complete"] is False
    assert payload["selection_window_plan"]
    assert payload["selection_hints"]
    assert payload["original_segment_count"] == 40
    assert payload["provided_segment_count"] == len(payload["transcript"])
    assert payload["source_duration_seconds"] == source.duration_seconds
    assert "s1" not in segment_ids
    assert "s6" in segment_ids
    assert "s20" not in segment_ids


@pytest.mark.asyncio
async def test_select_candidates_falls_back_to_full_transcript_when_windows_are_empty() -> None:
    source = segmented_transcript((0, 300), (300, 600), (600, 900))
    client = FakeStructuredClient(
        [CuratorResponse(candidates=[output("A", 0, 600)])]
    )

    await CuratorEngine(client=client, model="gpt-curator").select_candidates(
        transcript=source,
        topics=TopicDiscoveryResponse(
            topics=[topic("T1", float("nan"), 600, 0.9)]
        ),
        candidate_count=1,
        min_duration_minutes=8,
        max_duration_minutes=12,
    )

    payload = json.loads(str(client.calls[0]["user_prompt"]))
    segment_ids = [segment["id"] for segment in payload["transcript"]]
    assert payload["transcript_scope"] == "full_transcript"
    assert payload["transcript_is_complete"] is True
    assert payload["selection_window_plan"] == []
    assert payload["selection_hints"]
    assert segment_ids == ["s1", "s2", "s3"]


@pytest.mark.asyncio
async def test_select_candidates_normalizes_against_full_transcript_not_window() -> None:
    source = segmented_transcript(
        *[(second, second + 60) for second in range(0, 1800, 60)]
    )
    client = FakeStructuredClient(
        [CuratorResponse(candidates=[output("A", 1200, 1680)])]
    )

    result = await CuratorEngine(client=client, model="gpt-curator").select_candidates(
        transcript=source,
        topics=TopicDiscoveryResponse(
            topics=[topic("T1", 300, 360, 0.9)]
        ),
        candidate_count=1,
        min_duration_minutes=8,
        max_duration_minutes=12,
    )

    candidate = result.candidates[0]
    assert (candidate.start_seconds, candidate.end_seconds) == (1200, 1680)


@pytest.mark.asyncio
async def test_curator_retries_once_with_validation_feedback() -> None:
    client = FakeStructuredClient(
        [
            CuratorResponse(candidates=[output("A", 1900, 2000)]),
            CuratorResponse(candidates=[output("A", 0, 600)]),
        ]
    )
    engine = CuratorEngine(client=client, model="gpt-curator")

    result = await engine.select_candidates(
        transcript=transcript(),
        topics=TopicDiscoveryResponse(
            topics=[topic("T1", float("nan"), 300, 0.9)]
        ),
        candidate_count=1,
        min_duration_minutes=8,
        max_duration_minutes=12,
    )

    assert len(result.candidates) == 1
    assert len(client.calls) == 2
    assert json.loads(str(client.calls[0]["user_prompt"]))["validation_feedback"] is None
    retry_payload = json.loads(str(client.calls[1]["user_prompt"]))
    retry_feedback = retry_payload["validation_feedback"]
    assert "candidate A" in retry_feedback
    assert "actual duration 100" in retry_feedback
    assert "target range 480" in retry_feedback
    assert "accepted range 420" in retry_feedback
    assert "final range 390" in retry_feedback
    assert retry_payload["transcript_scope"] == "candidate_selection_retry_context"
    assert retry_payload["transcript_is_complete"] is False
    assert len(retry_payload["transcript"]) < len(
        json.loads(str(client.calls[0]["user_prompt"]))["transcript"]
    )
    assert retry_payload["previous_candidates"][0]["candidate_id"] == "A"
    assert retry_payload["retry_instruction"] == (
        "Repair the candidate package using the validation feedback. Reuse any "
        "valid reasoning from previous_candidates, but choose corrected source "
        "boundaries from the compact transcript context."
    )


def test_build_topic_windows_adds_context_and_clamps_to_transcript() -> None:
    source = segmented_transcript(
        (0, 60),
        (60, 120),
        (120, 180),
        (180, 240),
        (240, 300),
        (300, 360),
        (360, 420),
        (420, 480),
        (480, 540),
        (540, 600),
        (600, 660),
        (660, 720),
        (720, 780),
        (780, 840),
        (840, 900),
    )

    windowed = curator_engine._build_topic_windows(
        segments=source.segments,
        topics=[topic("T1", 300, 360, 0.9)],
        target_min_duration_seconds=480,
        final_max_duration_seconds=810,
    )

    assert [segment.segment_id for segment in windowed] == [
        "s1",
        "s2",
        "s3",
        "s4",
        "s5",
        "s6",
        "s7",
        "s8",
        "s9",
        "s10",
        "s11",
        "s12",
        "s13",
        "s14",
    ]


def test_build_topic_windows_merges_overlaps_and_preserves_order() -> None:
    source = segmented_transcript(
        *[(second, second + 60) for second in range(0, 1800, 60)]
    )

    windowed = curator_engine._build_topic_windows(
        segments=source.segments,
        topics=[
            topic("T1", 300, 420, 0.9),
            topic("T2", 600, 720, 0.8),
        ],
        target_min_duration_seconds=480,
        final_max_duration_seconds=810,
    )

    ids = [segment.segment_id for segment in windowed]
    assert ids == [f"s{index}" for index in range(1, 20)]
    assert len(ids) == len(set(ids))


def test_build_topic_windows_skips_invalid_topic_ranges() -> None:
    source = segmented_transcript(
        (0, 100),
        (100, 200),
        (200, 300),
        (300, 400),
        (400, 500),
        (500, 600),
    )

    windowed = curator_engine._build_topic_windows(
        segments=source.segments,
        topics=[
            topic("T1", float("nan"), 100, 0.9),
            topic("T2", 400, 300, 0.8),
            topic("T3", 200, 300, 0.7),
        ],
        target_min_duration_seconds=480,
        final_max_duration_seconds=810,
    )

    assert [segment.segment_id for segment in windowed] == [
        "s1",
        "s2",
        "s3",
        "s4",
        "s5",
        "s6",
    ]


def test_build_topic_windows_returns_empty_for_no_valid_ranges() -> None:
    source = segmented_transcript((0, 100), (100, 200))

    windowed = curator_engine._build_topic_windows(
        segments=source.segments,
        topics=[
            topic("T1", float("inf"), 100, 0.9),
            topic("T2", 150, 150, 0.8),
        ],
        target_min_duration_seconds=480,
        final_max_duration_seconds=810,
    )

    assert windowed == []


def test_candidate_selection_plan_limits_topic_windows_by_budget() -> None:
    source = Transcript(
        language="en",
        duration_seconds=3600,
        segments=[
            TranscriptSegment(
                segment_id=f"s{index}",
                start_seconds=float(start),
                end_seconds=float(start + 60),
                text="selection detail " * 120,
            )
            for index, start in enumerate(range(0, 3600, 60), start=1)
        ],
    )
    topics = [
        topic("T1", 300, 360, 0.9),
        topic("T2", 1500, 1560, 0.8),
        topic("T3", 2700, 2760, 0.7),
    ]

    plan = curator_engine._plan_candidate_selection_transcript(
        segments=source.segments,
        topics=topics,
        candidate_count=2,
        target_min_duration_seconds=480,
        final_max_duration_seconds=810,
        char_budget=30_000,
    )

    assert plan.transcript_scope == "budgeted_topic_windows_for_candidate_selection"
    assert plan.transcript_is_complete is False
    assert len(plan.windows) == 2
    assert plan.provided_segment_count < plan.original_segment_count
    assert plan.segments[0].start_seconds == 0


def test_candidate_selection_plan_uses_full_transcript_when_no_valid_windows() -> None:
    source = segmented_transcript((0, 300), (300, 600))

    plan = curator_engine._plan_candidate_selection_transcript(
        segments=source.segments,
        topics=[topic("T1", float("nan"), 100, 0.9)],
        candidate_count=2,
        target_min_duration_seconds=480,
        final_max_duration_seconds=810,
        char_budget=20_000,
    )

    assert plan.transcript_scope == "full_transcript"
    assert plan.transcript_is_complete is True
    assert plan.windows == []
    assert [segment.segment_id for segment in plan.segments] == ["s1", "s2"]


def test_build_selection_hints_estimates_window_waste_and_framework_signals() -> None:
    source = Transcript(
        language="en",
        duration_seconds=600,
        segments=[
            TranscriptSegment(
                segment_id="s1",
                start_seconds=0,
                end_seconds=60,
                text="The reason this matters is because the framework changes the model.",
            ),
            TranscriptSegment(
                segment_id="s2",
                start_seconds=60,
                end_seconds=120,
                text="Funny story by the way laugh joke subscribe again again.",
            ),
        ],
    )

    hints = curator_engine._build_selection_hints(
        segments=source.segments,
        windows=[(0, 60), (60, 120)],
    )

    assert hints[0]["estimated_waste_level"] == "low"
    assert hints[0]["framework_signal_count"] >= 3
    assert hints[1]["estimated_waste_level"] == "high"
    assert hints[1]["banter_signal_count"] >= 4
    assert hints[1]["repetition_signal_count"] >= 2


def test_topic_discovery_plan_keeps_short_transcripts_complete() -> None:
    source = segmented_transcript(
        (0, 60),
        (60, 120),
        (120, 180),
    )

    plan = curator_engine._plan_topic_discovery_transcript(
        segments=source.segments,
        candidate_count=2,
        char_budget=10_000,
    )

    assert plan.transcript_scope == "full_transcript"
    assert plan.transcript_is_complete is True
    assert plan.windows == []
    assert [segment.segment_id for segment in plan.segments] == ["s1", "s2", "s3"]


def test_topic_discovery_plan_prefilters_long_transcripts_without_rebasing_time() -> None:
    source = Transcript(
        language="en",
        duration_seconds=3600,
        segments=[
            TranscriptSegment(
                segment_id=f"s{index}",
                start_seconds=float(start),
                end_seconds=float(start + 60),
                text=("dense insight " * 300 if 1200 <= start < 1800 else "short"),
            )
            for index, start in enumerate(range(0, 3600, 60), start=1)
        ],
    )

    plan = curator_engine._plan_topic_discovery_transcript(
        segments=source.segments,
        candidate_count=2,
        char_budget=1_000,
    )

    assert plan.transcript_scope == "deterministic_discovery_windows"
    assert plan.transcript_is_complete is False
    assert plan.original_segment_count == 60
    assert 0 < len(plan.segments) < len(source.segments)
    assert plan.windows
    assert any(start < 1800 and end > 1200 for start, end in plan.windows)
    assert any(segment.start_seconds >= 1200 for segment in plan.segments)
    assert all(segment.start_seconds < segment.end_seconds for segment in plan.segments)


def test_topic_discovery_plan_does_not_cover_entire_fifty_minute_transcript() -> None:
    source = Transcript(
        language="en",
        duration_seconds=3000,
        segments=[
            TranscriptSegment(
                segment_id=f"s{index}",
                start_seconds=float(start),
                end_seconds=float(start + 60),
                text=("dense insight " * 300 if 1200 <= start < 1800 else "short"),
            )
            for index, start in enumerate(range(0, 3000, 60), start=1)
        ],
    )

    plan = curator_engine._plan_topic_discovery_transcript(
        segments=source.segments,
        candidate_count=2,
        char_budget=1_000,
    )

    assert plan.transcript_scope == "deterministic_discovery_windows"
    assert plan.transcript_is_complete is False
    assert len(plan.windows) == 3
    assert len(plan.segments) <= 35
    assert plan.provided_segment_count < plan.original_segment_count


@pytest.mark.asyncio
async def test_discover_topics_sends_window_plan_for_large_transcript() -> None:
    source = Transcript(
        language="en",
        duration_seconds=3600,
        segments=[
            TranscriptSegment(
                segment_id=f"s{index}",
                start_seconds=float(start),
                end_seconds=float(start + 60),
                text=("dense insight " * 300 if 1200 <= start < 1800 else "short"),
            )
            for index, start in enumerate(range(0, 3600, 60), start=1)
        ],
    )
    client = FakeStructuredClient(
        [
            TopicDiscoveryResponse(
                topics=[
                    topic("T1", 1200, 1500, 0.9),
                    topic("T2", 1500, 1800, 0.8),
                    topic("T3", 0, 300, 0.7),
                    topic("T4", 3000, 3300, 0.6),
                ]
            )
        ]
    )

    await CuratorEngine(client=client, model="gpt-curator").discover_topics(
        transcript=source,
        candidate_count=2,
    )

    payload = json.loads(str(client.calls[0]["user_prompt"]))
    assert payload["transcript_scope"] == "deterministic_discovery_windows"
    assert payload["transcript_is_complete"] is False
    assert payload["evaluate_full_transcript"] is False
    assert payload["original_segment_count"] == 60
    assert payload["provided_segment_count"] < 60
    assert payload["window_plan"]
    assert any(segment["start"] >= 1200 for segment in payload["transcript"])


def test_build_topic_windows_skips_topics_outside_transcript_bounds() -> None:
    source = segmented_transcript((0, 100), (100, 200), (200, 300))

    windowed = curator_engine._build_topic_windows(
        segments=source.segments,
        topics=[
            topic("T1", 500, 600, 0.9),
            topic("T2", -200, -100, 0.8),
        ],
        target_min_duration_seconds=480,
        final_max_duration_seconds=810,
    )

    assert windowed == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("segments", "proposed", "expected"),
    [
        (
            [(0, 240), (240, 480), (480, 720)],
            (0, 480),
            (0, 480),
        ),
        (
            [(0, 240), (240, 480), (480, 720)],
            (30, 450),
            (0, 480),
        ),
        (
            [(0, 180), (180, 360), (360, 540)],
            (180, 360),
            (0, 540),
        ),
        (
            [(0, 390), (390, 750)],
            (390, 750),
            (0, 750),
        ),
        (
            [(0, 300), (300, 600), (600, 900)],
            (0, 900),
            (0, 600),
        ),
        (
            [(0, 450), (450, 900)],
            (0, 900),
            (0, 450),
        ),
        (
            [(0, 180), (180, 360), (360, 540), (540, 720)],
            (0, 180),
            (0, 540),
        ),
        (
            [(0, 180), (180, 360), (360, 540), (540, 720)],
            (540, 720),
            (180, 720),
        ),
    ],
)
async def test_curator_normalizes_candidates_to_complete_segments(
    segments: list[tuple[float, float]],
    proposed: tuple[float, float],
    expected: tuple[float, float],
) -> None:
    candidate = output("A", *proposed, title="Preserved title")
    client = FakeStructuredClient([CuratorResponse(candidates=[candidate])])

    result = await CuratorEngine(client=client, model="gpt-curator").select_candidates(
        transcript=segmented_transcript(*segments),
        topics=valid_topics(),
        candidate_count=1,
        min_duration_minutes=8,
        max_duration_minutes=12,
    )

    normalized = result.candidates[0]
    assert (normalized.start_seconds, normalized.end_seconds) == expected
    assert normalized.suggested_title == candidate.suggested_title
    assert normalized.selection_reason == candidate.selection_reason
    assert normalized.summary == candidate.summary
    assert normalized.score == candidate.score


@pytest.mark.asyncio
async def test_selection_review_can_rank_lower_scored_candidate_first() -> None:
    client = FakeStructuredClient(
        [
            CuratorResponse(
                candidates=[
                    output("A", 0, 480, title="Early headline"),
                    output("B", 900, 1500, title="Better complete arc"),
                ]
            ),
            SelectionReviewResponse(
                candidates=[
                    SelectionReviewCandidateOutput(
                        candidate_id="B",
                        rank=1,
                        adjusted_start_seconds=900,
                        adjusted_end_seconds=1500,
                        selection_reason="B has the stronger standalone payoff.",
                        boundary_adjustment_reason="No adjustment needed.",
                        risk_notes="Low risk.",
                    ),
                    SelectionReviewCandidateOutput(
                        candidate_id="A",
                        rank=2,
                        adjusted_start_seconds=0,
                        adjusted_end_seconds=480,
                        selection_reason="A is useful but mostly setup.",
                        boundary_adjustment_reason="No adjustment needed.",
                        risk_notes="Weaker conclusion.",
                    ),
                ]
            ),
        ]
    )

    result = await CuratorEngine(
        client=client,
        model="gpt-curator",
        enable_selection_review=True,
    ).select_candidates(
        transcript=segmented_transcript((0, 480), (480, 900), (900, 1500)),
        topics=valid_topics(candidate_count=2),
        candidate_count=2,
        min_duration_minutes=8,
        max_duration_minutes=12,
    )

    by_id = {candidate.candidate_id: candidate for candidate in result.candidates}
    assert by_id["B"].score == 1.0
    assert by_id["A"].score < by_id["B"].score
    assert "Selection review rank #1" in by_id["B"].selection_reason
    assert client.calls[-1]["trace_name"] == "selection_review"


@pytest.mark.asyncio
async def test_selection_review_skips_clear_low_risk_candidates() -> None:
    client = FakeStructuredClient(
        [
            CuratorResponse(
                candidates=[
                    output("A", 0, 600, title="Clear best", score=0.94),
                    output("B", 900, 1500, title="Distant second", score=0.72),
                ]
            ),
        ]
    )

    result = await CuratorEngine(
        client=client,
        model="gpt-curator",
        enable_selection_review=True,
    ).select_candidates(
        transcript=segmented_transcript((0, 600), (900, 1500)),
        topics=valid_topics(candidate_count=2),
        candidate_count=2,
        min_duration_minutes=8,
        max_duration_minutes=12,
    )

    assert [candidate.candidate_id for candidate in result.candidates] == ["A", "B"]
    assert [call["trace_name"] for call in client.calls] == ["candidate_selection"]


@pytest.mark.asyncio
async def test_selection_review_runs_when_top_scores_are_close() -> None:
    client = FakeStructuredClient(
        [
            CuratorResponse(
                candidates=[
                    output("A", 0, 600, title="Slight lead", score=0.86),
                    output("B", 900, 1500, title="Close second", score=0.82),
                ]
            ),
            SelectionReviewResponse(
                candidates=[
                    SelectionReviewCandidateOutput(
                        candidate_id="B",
                        rank=1,
                        adjusted_start_seconds=900,
                        adjusted_end_seconds=1500,
                        selection_reason="B has a cleaner standalone payoff.",
                        boundary_adjustment_reason="No adjustment needed.",
                        risk_notes="Low risk.",
                    ),
                    SelectionReviewCandidateOutput(
                        candidate_id="A",
                        rank=2,
                        adjusted_start_seconds=0,
                        adjusted_end_seconds=600,
                        selection_reason="A is close but weaker.",
                        boundary_adjustment_reason="No adjustment needed.",
                        risk_notes="Close score.",
                    ),
                ]
            ),
        ]
    )

    result = await CuratorEngine(
        client=client,
        model="gpt-curator",
        enable_selection_review=True,
    ).select_candidates(
        transcript=segmented_transcript((0, 600), (900, 1500)),
        topics=valid_topics(candidate_count=2),
        candidate_count=2,
        min_duration_minutes=8,
        max_duration_minutes=12,
    )

    by_id = {candidate.candidate_id: candidate for candidate in result.candidates}
    assert by_id["B"].score == 1.0
    assert [call["trace_name"] for call in client.calls] == [
        "candidate_selection",
        "selection_review",
    ]


@pytest.mark.asyncio
async def test_selection_review_window_plan_records_trigger_reason() -> None:
    client = FakeStructuredClient(
        [
            CuratorResponse(
                candidates=[
                    output("A", 0, 600, title="Slight lead", score=0.86),
                    output("B", 900, 1500, title="Close second", score=0.82),
                ]
            ),
            SelectionReviewResponse(
                candidates=[
                    SelectionReviewCandidateOutput(
                        candidate_id="A",
                        rank=1,
                        adjusted_start_seconds=0,
                        adjusted_end_seconds=600,
                        selection_reason="A remains stronger.",
                        boundary_adjustment_reason="No adjustment needed.",
                        risk_notes="Close score checked.",
                    ),
                    SelectionReviewCandidateOutput(
                        candidate_id="B",
                        rank=2,
                        adjusted_start_seconds=900,
                        adjusted_end_seconds=1500,
                        selection_reason="B is close but weaker.",
                        boundary_adjustment_reason="No adjustment needed.",
                        risk_notes="Close score checked.",
                    ),
                ]
            ),
        ]
    )
    telemetry: list[dict[str, object]] = []

    with capture_llm_telemetry(telemetry.append):
        await CuratorEngine(
            client=client,
            model="gpt-curator",
            enable_selection_review=True,
        ).select_candidates(
            transcript=segmented_transcript((0, 600), (900, 1500)),
            topics=valid_topics(candidate_count=2),
            candidate_count=2,
            min_duration_minutes=8,
            max_duration_minutes=12,
        )

    selection_review_windows = [
        event
        for event in telemetry
        if event.get("event") == "window_plan"
        and event.get("trace_name") == "selection_review"
    ]
    assert selection_review_windows[0]["reason"] == "close_candidate_scores"


@pytest.mark.asyncio
async def test_selection_review_can_extend_boundary_to_natural_ending() -> None:
    client = FakeStructuredClient(
        [
            CuratorResponse(
                candidates=[output("A", 0, 480, boundary_ending_type="open_loop")]
            ),
            SelectionReviewResponse(
                candidates=[
                    SelectionReviewCandidateOutput(
                        candidate_id="A",
                        rank=1,
                        adjusted_start_seconds=0,
                        adjusted_end_seconds=540,
                        selection_reason="The extra minute completes the argument.",
                        boundary_adjustment_reason=(
                            "The original endpoint stops before the conclusion."
                        ),
                        risk_notes="Extension stays within target duration.",
                    )
                ]
            ),
        ]
    )

    result = await CuratorEngine(
        client=client,
        model="gpt-curator",
        enable_selection_review=True,
    ).select_candidates(
        transcript=segmented_transcript((0, 480), (480, 540), (540, 900)),
        topics=valid_topics(),
        candidate_count=1,
        min_duration_minutes=8,
        max_duration_minutes=12,
    )

    candidate = result.candidates[0]
    assert (candidate.start_seconds, candidate.end_seconds) == (0, 540)
    assert "Boundary review:" in candidate.selection_reason
    assert candidate.score == 1.0


@pytest.mark.asyncio
async def test_selection_review_uses_compact_candidate_context() -> None:
    client = FakeStructuredClient(
        [
            CuratorResponse(
                candidates=[output("A", 100, 700, boundary_ending_type="open_loop")]
            ),
            SelectionReviewResponse(
                candidates=[
                    SelectionReviewCandidateOutput(
                        candidate_id="A",
                        rank=1,
                        adjusted_start_seconds=100,
                        adjusted_end_seconds=700,
                        selection_reason="Best compact candidate.",
                        boundary_adjustment_reason="No adjustment needed.",
                        risk_notes="Low risk.",
                    )
                ]
            ),
        ]
    )

    await CuratorEngine(
        client=client,
        model="gpt-curator",
        enable_selection_review=True,
    ).select_candidates(
        transcript=segmented_transcript((0, 60), (100, 700), (1300, 1900)),
        topics=TopicDiscoveryResponse(
            topics=[
                topic("T1", 100, 700, 0.9),
                topic("T2", 1300, 1900, 0.8),
                topic("T3", 0, 60, 0.7),
            ]
        ),
        candidate_count=2,
        min_duration_minutes=8,
        max_duration_minutes=12,
    )

    review_payload = json.loads(client.calls[-1]["user_prompt"])
    review_segment_ids = {segment["id"] for segment in review_payload["transcript"]}
    assert review_segment_ids == {"s2"}


@pytest.mark.asyncio
async def test_selection_review_uses_boundary_excerpts_not_full_candidate_transcript() -> None:
    client = FakeStructuredClient(
        [
            CuratorResponse(
                candidates=[output("A", 120, 720, boundary_ending_type="open_loop")]
            ),
            SelectionReviewResponse(
                candidates=[
                    SelectionReviewCandidateOutput(
                        candidate_id="A",
                        rank=1,
                        adjusted_start_seconds=120,
                        adjusted_end_seconds=720,
                        selection_reason="Best package.",
                        boundary_adjustment_reason="No adjustment needed.",
                        risk_notes="Low risk.",
                    )
                ]
            ),
        ]
    )

    await CuratorEngine(
        client=client,
        model="gpt-curator",
        enable_selection_review=True,
    ).select_candidates(
        transcript=segmented_transcript(
            (0, 120),
            (120, 240),
            (240, 600),
            (600, 720),
            (720, 840),
        ),
        topics=valid_topics(),
        candidate_count=1,
        min_duration_minutes=8,
        max_duration_minutes=12,
    )

    review_payload = json.loads(client.calls[-1]["user_prompt"])
    review_segment_ids = {segment["id"] for segment in review_payload["transcript"]}
    assert review_segment_ids == {"s2", "s4"}
    assert review_payload["candidates"][0]["core_claim"] == "Core claim"
    assert review_payload["candidates"][0]["argument_arc"] == [
        "setup",
        "claim",
        "evidence",
        "conclusion",
    ]
    assert review_payload["candidates"][0]["boundary_ending_type"] == "open_loop"


@pytest.mark.asyncio
@pytest.mark.parametrize("duration", [390, 810])
async def test_curator_accepts_exact_final_duration_boundaries(duration: float) -> None:
    client = FakeStructuredClient(
        [CuratorResponse(candidates=[output("A", 0, duration)])]
    )

    result = await CuratorEngine(client=client, model="gpt-curator").select_candidates(
        transcript=segmented_transcript((0, duration)),
        topics=valid_topics(),
        candidate_count=1,
        min_duration_minutes=8,
        max_duration_minutes=12,
    )

    candidate = result.candidates[0]
    assert (candidate.start_seconds, candidate.end_seconds) == (0, duration)
    assert len(client.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("duration", [389, 811])
async def test_curator_rejects_durations_outside_final_range(duration: float) -> None:
    invalid = CuratorResponse(candidates=[output("A", 0, duration)])
    client = FakeStructuredClient([invalid, invalid])

    with pytest.raises(InsightCastError) as exc_info:
        await CuratorEngine(client=client, model="gpt-curator").select_candidates(
            transcript=segmented_transcript((0, duration)),
            topics=valid_topics(),
            candidate_count=1,
            min_duration_minutes=8,
            max_duration_minutes=12,
        )

    error = exc_info.value
    assert error.error_code == ErrorCode.INVALID_LLM_OUTPUT
    assert len(client.calls) == 2
    feedback = str(error.details["validation_feedback"])
    assert "target range 480" in feedback
    assert "accepted range 420" in feedback
    assert "final range 390-810" in feedback.replace(".0", "")


@pytest.mark.asyncio
async def test_curator_prefers_target_window_on_alternative_contraction_path() -> None:
    client = FakeStructuredClient(
        [CuratorResponse(candidates=[output("A", 0, 375)])]
    )

    result = await CuratorEngine(client=client, model="gpt-curator").select_candidates(
        transcript=segmented_transcript(
            (0, 50),
            (50, 350),
            (350, 750),
        ),
        topics=valid_topics(),
        candidate_count=1,
        min_duration_minutes=8,
        max_duration_minutes=12,
    )

    candidate = result.candidates[0]
    assert (candidate.start_seconds, candidate.end_seconds) == (50, 750)


@pytest.mark.asyncio
async def test_curator_prefers_accepted_window_over_final_window() -> None:
    client = FakeStructuredClient(
        [CuratorResponse(candidates=[output("A", 0, 400)])]
    )

    result = await CuratorEngine(client=client, model="gpt-curator").select_candidates(
        transcript=segmented_transcript((0, 400), (400, 750)),
        topics=valid_topics(),
        candidate_count=1,
        min_duration_minutes=8,
        max_duration_minutes=12,
    )

    candidate = result.candidates[0]
    assert (candidate.start_seconds, candidate.end_seconds) == (0, 750)


@pytest.mark.asyncio
async def test_curator_normalizes_large_transcript_without_rescanning_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_window_rescan(*args: object, **kwargs: object) -> float:
        raise AssertionError("normalization must use constant-time window overlap")

    monkeypatch.setattr(curator_engine, "_window_overlap", reject_window_rescan)
    segments = [(second, second + 1) for second in range(1000)]
    client = FakeStructuredClient(
        [CuratorResponse(candidates=[output("A", 500, 520)])]
    )

    result = await CuratorEngine(client=client, model="gpt-curator").select_candidates(
        transcript=segmented_transcript(*segments),
        topics=valid_topics(),
        candidate_count=1,
        min_duration_minutes=8,
        max_duration_minutes=12,
    )

    candidate = result.candidates[0]
    assert (candidate.start_seconds, candidate.end_seconds) == (40, 520)


@pytest.mark.asyncio
async def test_curator_retries_when_candidate_does_not_overlap_transcript() -> None:
    client = FakeStructuredClient(
        [
            CuratorResponse(candidates=[output("A", 1000, 1100)]),
            CuratorResponse(candidates=[output("A", 1000, 1100)]),
        ]
    )

    with pytest.raises(InsightCastError) as exc_info:
        await CuratorEngine(client=client, model="gpt-curator").select_candidates(
            transcript=segmented_transcript((0, 300), (300, 600)),
            topics=valid_topics(),
            candidate_count=1,
            min_duration_minutes=8,
            max_duration_minutes=12,
        )

    assert exc_info.value.error_code == ErrorCode.INVALID_LLM_OUTPUT
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_curator_retries_when_transcript_has_no_segments() -> None:
    client = FakeStructuredClient(
        [
            CuratorResponse(candidates=[output("A", 0, 600)]),
            CuratorResponse(candidates=[output("A", 0, 600)]),
        ]
    )

    with pytest.raises(InsightCastError) as exc_info:
        await CuratorEngine(client=client, model="gpt-curator").select_candidates(
            transcript=Transcript(language="en", duration_seconds=600, segments=[]),
            topics=valid_topics(),
            candidate_count=1,
            min_duration_minutes=8,
            max_duration_minutes=12,
        )

    assert exc_info.value.error_code == ErrorCode.INVALID_LLM_OUTPUT
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_curator_rejects_when_no_segment_window_fits_accepted_duration() -> None:
    client = FakeStructuredClient(
        [
            CuratorResponse(candidates=[output("A", 0, 900)]),
            CuratorResponse(candidates=[output("A", 0, 900)]),
        ]
    )

    with pytest.raises(InsightCastError) as exc_info:
        await CuratorEngine(client=client, model="gpt-curator").select_candidates(
            transcript=segmented_transcript((0, 900)),
            topics=valid_topics(),
            candidate_count=1,
            min_duration_minutes=8,
            max_duration_minutes=12,
        )

    assert exc_info.value.error_code == ErrorCode.INVALID_LLM_OUTPUT


@pytest.mark.asyncio
async def test_second_undersized_result_returns_available_valid_candidates() -> None:
    client = FakeStructuredClient(
        [
            CuratorResponse(candidates=[output("A", 0, 600)]),
        ]
    )

    result = await CuratorEngine(client=client, model="gpt-curator").select_candidates(
        transcript=transcript(),
        topics=valid_topics(candidate_count=2),
        candidate_count=2,
        min_duration_minutes=8,
        max_duration_minutes=12,
    )

    assert [candidate.candidate_id for candidate in result.candidates] == ["A"]
    assert len(client.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_candidates",
    [
        [output("B", 0, 600)],
        [output("A", 700, 600)],
        [output("A", 1900, 2000)],
        [output("A", 0, 600, title=" ")],
    ],
)
async def test_second_invalid_result_raises_invalid_llm_output(
    invalid_candidates: list[CuratorCandidateOutput],
) -> None:
    client = FakeStructuredClient(
        [
            CuratorResponse(candidates=invalid_candidates),
            CuratorResponse(candidates=invalid_candidates),
        ]
    )

    with pytest.raises(InsightCastError) as exc_info:
        await CuratorEngine(client=client, model="gpt-curator").select_candidates(
            transcript=transcript(),
            topics=valid_topics(),
            candidate_count=1,
            min_duration_minutes=8,
            max_duration_minutes=12,
        )

    assert exc_info.value.error_code == ErrorCode.INVALID_LLM_OUTPUT
