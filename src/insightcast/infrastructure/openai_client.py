import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from openai import OpenAI
from pydantic import BaseModel

from insightcast.core.config import Settings
from insightcast.core.exceptions import InsightCastError
from insightcast.domain.enums import ErrorCode

ResponseModel = TypeVar("ResponseModel", bound=BaseModel)


class StructuredOpenAIClient:
    def __init__(
        self,
        client: Any,
        *,
        timeout_seconds: float,
        max_retries: int,
        retry_sleep_seconds: float = 0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.client = client
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.retry_sleep_seconds = max(0, retry_sleep_seconds)
        self.sleep = sleep

    @classmethod
    def from_settings(cls, settings: Settings) -> "StructuredOpenAIClient":
        sdk = OpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            timeout=settings.openai_timeout_seconds,
            max_retries=0,
        )
        return cls(
            sdk,
            timeout_seconds=settings.openai_timeout_seconds,
            max_retries=settings.openai_max_retries,
            retry_sleep_seconds=settings.openai_retry_sleep_seconds,
        )

    async def parse(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        response_model: type[ResponseModel],
    ) -> ResponseModel:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = await asyncio.to_thread(
                    self.client.responses.parse,
                    model=model,
                    instructions=system_prompt,
                    input=user_prompt,
                    text_format=response_model,
                    timeout=self.timeout_seconds,
                )
                parsed = response.output_parsed
                if parsed is None:
                    raise ValueError("OpenAI response did not contain parsed output")
                return parsed
            except Exception as exc:
                last_error = exc
                if attempt < self.max_retries and self.retry_sleep_seconds > 0:
                    await self.sleep(self.retry_sleep_seconds)
        assert last_error is not None
        raise InsightCastError(
            ErrorCode.LLM_REQUEST_FAILED,
            "The language model request failed.",
            details={"model": model, "reason": str(last_error)},
            stage="llm",
        ) from last_error
