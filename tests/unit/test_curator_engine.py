import pytest

from insightcast.core.exceptions import InsightCastError
from insightcast.domain.enums import ErrorCode
from insightcast.domain.models import Transcript, TranscriptSegment
from insightcast.engines.curator_engine import (
    CuratorCandidateOutput,
    CuratorEngine,
    CuratorResponse,
)


class FakeStructuredClient:
    def __init__(self, responses: list[CuratorResponse]) -> None:
        self.responses = responses
        self.calls: list[dict[str, object]] = []

    async def parse(self, **kwargs: object) -> CuratorResponse:
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
) -> CuratorCandidateOutput:
    return CuratorCandidateOutput(
        candidate_id=candidate_id,
        start_seconds=start,
        end_seconds=end,
        suggested_title=title,
        selection_reason="Complete idea arc",
        summary="Useful summary",
        score=0.9,
    )


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

    result = await engine.curate(
        transcript=transcript(),
        candidate_count=2,
        min_duration_minutes=8,
        max_duration_minutes=12,
    )

    assert [candidate.candidate_id for candidate in result.candidates] == ["A", "B"]
    assert result.model == "gpt-curator"
    assert result.prompt_version


@pytest.mark.asyncio
async def test_curator_retries_once_with_validation_feedback() -> None:
    client = FakeStructuredClient(
        [
            CuratorResponse(candidates=[output("A", 1900, 2000)]),
            CuratorResponse(candidates=[output("A", 0, 600)]),
        ]
    )
    engine = CuratorEngine(client=client, model="gpt-curator")

    result = await engine.curate(
        transcript=transcript(),
        candidate_count=1,
        min_duration_minutes=8,
        max_duration_minutes=12,
    )

    assert len(result.candidates) == 1
    assert len(client.calls) == 2
    assert '"validation_feedback": null' in str(client.calls[0]["user_prompt"])
    retry_prompt = str(client.calls[1]["user_prompt"])
    assert "candidate A" in retry_prompt
    assert "actual duration 100" in retry_prompt
    assert "target range 480" in retry_prompt
    assert "accepted range 420" in retry_prompt


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

    result = await CuratorEngine(client=client, model="gpt-curator").curate(
        transcript=segmented_transcript(*segments),
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
async def test_curator_retries_when_candidate_does_not_overlap_transcript() -> None:
    client = FakeStructuredClient(
        [
            CuratorResponse(candidates=[output("A", 1000, 1100)]),
            CuratorResponse(candidates=[output("A", 1000, 1100)]),
        ]
    )

    with pytest.raises(InsightCastError) as exc_info:
        await CuratorEngine(client=client, model="gpt-curator").curate(
            transcript=segmented_transcript((0, 300), (300, 600)),
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
        await CuratorEngine(client=client, model="gpt-curator").curate(
            transcript=Transcript(language="en", duration_seconds=600, segments=[]),
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
        await CuratorEngine(client=client, model="gpt-curator").curate(
            transcript=segmented_transcript((0, 900)),
            candidate_count=1,
            min_duration_minutes=8,
            max_duration_minutes=12,
        )

    assert exc_info.value.error_code == ErrorCode.INVALID_LLM_OUTPUT


@pytest.mark.asyncio
async def test_second_undersized_result_raises_insufficient_candidates() -> None:
    client = FakeStructuredClient(
        [
            CuratorResponse(candidates=[output("A", 0, 600)]),
            CuratorResponse(candidates=[output("A", 0, 600)]),
        ]
    )

    with pytest.raises(InsightCastError) as exc_info:
        await CuratorEngine(client=client, model="gpt-curator").curate(
            transcript=transcript(),
            candidate_count=2,
            min_duration_minutes=8,
            max_duration_minutes=12,
        )

    assert exc_info.value.error_code == ErrorCode.INSUFFICIENT_CANDIDATES


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
        await CuratorEngine(client=client, model="gpt-curator").curate(
            transcript=transcript(),
            candidate_count=1,
            min_duration_minutes=8,
            max_duration_minutes=12,
        )

    assert exc_info.value.error_code == ErrorCode.INVALID_LLM_OUTPUT
