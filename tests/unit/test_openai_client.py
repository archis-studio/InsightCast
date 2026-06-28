import logging
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from insightcast.core.exceptions import InsightCastError
from insightcast.domain.enums import ErrorCode
from insightcast.infrastructure.openai_client import StructuredOpenAIClient


class ResultModel(BaseModel):
    value: str


class FakeResponses:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.calls: list[dict[str, object]] = []

    def parse(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        if hasattr(outcome, "output_parsed"):
            return outcome
        return SimpleNamespace(output_parsed=outcome)


class FakeSleeper:
    def __init__(self) -> None:
        self.sleeps: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


@pytest.mark.asyncio
async def test_structured_response_transports_model_prompts_schema_and_timeout() -> None:
    responses = FakeResponses([ResultModel(value="ok")])
    sdk = SimpleNamespace(responses=responses)
    client = StructuredOpenAIClient(sdk, timeout_seconds=15, max_retries=0)

    result = await client.parse(
        model="gpt-test",
        system_prompt="system contract",
        user_prompt="user data",
        response_model=ResultModel,
    )

    assert result == ResultModel(value="ok")
    assert responses.calls == [
        {
            "model": "gpt-test",
            "instructions": "system contract",
            "input": "user data",
            "text_format": ResultModel,
            "timeout": 15,
        }
    ]


@pytest.mark.asyncio
async def test_structured_response_retries_then_returns_valid_result() -> None:
    responses = FakeResponses([RuntimeError("temporary"), ResultModel(value="recovered")])
    client = StructuredOpenAIClient(
        SimpleNamespace(responses=responses),
        timeout_seconds=30,
        max_retries=1,
    )

    result = await client.parse(
        model="gpt-test",
        system_prompt="system",
        user_prompt="user",
        response_model=ResultModel,
    )

    assert result.value == "recovered"
    assert len(responses.calls) == 2


@pytest.mark.asyncio
async def test_structured_response_sleeps_between_retry_attempts() -> None:
    responses = FakeResponses([RuntimeError("temporary"), ResultModel(value="recovered")])
    sleeper = FakeSleeper()
    client = StructuredOpenAIClient(
        SimpleNamespace(responses=responses),
        timeout_seconds=30,
        max_retries=1,
        retry_sleep_seconds=4.5,
        sleep=sleeper.sleep,
    )

    result = await client.parse(
        model="gpt-test",
        system_prompt="system",
        user_prompt="user",
        response_model=ResultModel,
    )

    assert result.value == "recovered"
    assert sleeper.sleeps == [4.5]
    assert len(responses.calls) == 2


@pytest.mark.asyncio
async def test_structured_response_failure_is_converted_without_prompt_contents() -> None:
    responses = FakeResponses([RuntimeError("secret transport details")])
    client = StructuredOpenAIClient(
        SimpleNamespace(responses=responses),
        timeout_seconds=30,
        max_retries=0,
    )

    with pytest.raises(InsightCastError) as exc_info:
        await client.parse(
            model="gpt-test",
            system_prompt="do not expose",
            user_prompt="private transcript",
            response_model=ResultModel,
        )

    assert exc_info.value.error_code == ErrorCode.LLM_REQUEST_FAILED
    assert exc_info.value.details == {"model": "gpt-test", "reason": "secret transport details"}


@pytest.mark.asyncio
async def test_structured_response_logs_prompt_sizes_and_usage_without_prompt_contents(
    caplog,
) -> None:
    caplog.set_level(logging.INFO)
    usage = SimpleNamespace(input_tokens=12, output_tokens=3, total_tokens=15)
    responses = FakeResponses(
        [SimpleNamespace(output_parsed=ResultModel(value="ok"), usage=usage)]
    )
    client = StructuredOpenAIClient(
        SimpleNamespace(responses=responses),
        timeout_seconds=30,
        max_retries=0,
    )

    result = await client.parse(
        model="gpt-test",
        system_prompt="system secret",
        user_prompt="user private transcript",
        response_model=ResultModel,
        trace_name="topic_discovery",
    )

    assert result.value == "ok"
    log_output = caplog.text
    assert "llm_request_completed" in log_output
    assert "trace_name=topic_discovery" in log_output
    assert "system_chars=13" in log_output
    assert "user_chars=23" in log_output
    assert "input_tokens=12" in log_output
    assert "output_tokens=3" in log_output
    assert "total_tokens=15" in log_output
    assert "system secret" not in log_output
    assert "user private transcript" not in log_output
