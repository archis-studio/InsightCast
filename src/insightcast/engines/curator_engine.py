from typing import Any

from pydantic import BaseModel, ConfigDict

from insightcast.core.exceptions import InsightCastError
from insightcast.domain.enums import ErrorCode
from insightcast.domain.models import Candidate, Transcript
from insightcast.prompts import curator


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


class CurationResult(CuratorModel):
    candidates: list[Candidate]
    model: str
    prompt_version: str


class CuratorEngine:
    def __init__(self, *, client: Any, model: str) -> None:
        self.client = client
        self.model = model

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
        for attempt in range(2):
            response = await self.client.parse(
                model=self.model,
                system_prompt=curator.SYSTEM_PROMPT,
                user_prompt=curator.build_user_prompt(
                    transcript=[
                        segment.model_dump(mode="json") for segment in transcript.segments
                    ],
                    candidate_count=candidate_count,
                    min_duration_minutes=min_duration_minutes,
                    max_duration_minutes=max_duration_minutes,
                    validation_feedback=feedback,
                ),
                response_model=CuratorResponse,
            )
            last_response = response
            errors = self._validate_candidates(
                response.candidates,
                transcript_duration=transcript.duration_seconds,
                candidate_count=candidate_count,
                min_duration_seconds=min_duration_minutes * 60,
                max_duration_seconds=max_duration_minutes * 60,
            )
            if not errors:
                return CurationResult(
                    candidates=[
                        Candidate(**candidate.model_dump()) for candidate in response.candidates
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
    def _validate_candidates(
        candidates: list[CuratorCandidateOutput],
        *,
        transcript_duration: float,
        candidate_count: int,
        min_duration_seconds: float,
        max_duration_seconds: float,
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
            if not min_duration_seconds <= duration <= max_duration_seconds:
                errors.append(
                    f"candidate {candidate.candidate_id} duration must be between "
                    f"{min_duration_seconds} and {max_duration_seconds} seconds"
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


def _sequential_id(index: int) -> str:
    value = index + 1
    result = ""
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result
