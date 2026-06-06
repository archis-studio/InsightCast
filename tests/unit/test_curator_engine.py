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


def transcript(duration: float = 1800) -> Transcript:
    return Transcript(
        language="en",
        duration_seconds=duration,
        segments=[
            TranscriptSegment(
                segment_id="s1",
                start_seconds=0,
                end_seconds=duration,
                text="Complete transcript",
            )
        ],
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
            CuratorResponse(candidates=[output("A", 0, 100)]),
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
    assert "duration" in str(client.calls[1]["user_prompt"]).lower()


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
        [output("A", 0, 100)],
        [output("A", 0, 1900)],
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

