import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, TypeVar

from openai import OpenAI
from pydantic import BaseModel

from insightcast.core.config import Settings
from insightcast.core.exceptions import InsightCastError
from insightcast.domain.enums import ErrorCode

ResponseModel = TypeVar("ResponseModel", bound=BaseModel)
LOGGER = logging.getLogger(__name__)
TelemetrySink = Callable[[dict[str, Any]], None]
_LLM_TELEMETRY_SINK: ContextVar[TelemetrySink | None] = ContextVar(
    "llm_telemetry_sink",
    default=None,
)


@contextmanager
def capture_llm_telemetry(sink: TelemetrySink) -> Any:
    token = _LLM_TELEMETRY_SINK.set(sink)
    try:
        yield
    finally:
        _LLM_TELEMETRY_SINK.reset(token)


def emit_llm_telemetry(fields: dict[str, Any]) -> None:
    sink = _LLM_TELEMETRY_SINK.get()
    if sink is not None:
        sink(fields)


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
        trace_name: str | None = None,
    ) -> ResponseModel:
        last_error: Exception | None = None
        resolved_trace_name = trace_name or response_model.__name__
        system_chars = len(system_prompt)
        user_chars = len(user_prompt)
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
                usage = _usage_fields(response)
                LOGGER.info(
                    "llm_request_completed trace_name=%s model=%s response_model=%s "
                    "attempt=%s system_chars=%s user_chars=%s input_tokens=%s "
                    "output_tokens=%s total_tokens=%s",
                    resolved_trace_name,
                    model,
                    response_model.__name__,
                    attempt + 1,
                    system_chars,
                    user_chars,
                    usage["input_tokens"],
                    usage["output_tokens"],
                    usage["total_tokens"],
                )
                emit_llm_telemetry(
                    {
                        "event": "completed",
                        "trace_name": resolved_trace_name,
                        "model": model,
                        "response_model": response_model.__name__,
                        "attempt": attempt + 1,
                        "system_chars": system_chars,
                        "user_chars": user_chars,
                        **usage,
                    }
                )
                return parsed
            except Exception as exc:
                last_error = exc
                LOGGER.warning(
                    "llm_request_failed trace_name=%s model=%s response_model=%s "
                    "attempt=%s max_attempts=%s system_chars=%s user_chars=%s "
                    "error_type=%s",
                    resolved_trace_name,
                    model,
                    response_model.__name__,
                    attempt + 1,
                    self.max_retries + 1,
                    system_chars,
                    user_chars,
                    type(exc).__name__,
                )
                emit_llm_telemetry(
                    {
                        "event": "failed",
                        "trace_name": resolved_trace_name,
                        "model": model,
                        "response_model": response_model.__name__,
                        "attempt": attempt + 1,
                        "max_attempts": self.max_retries + 1,
                        "system_chars": system_chars,
                        "user_chars": user_chars,
                        "error_type": type(exc).__name__,
                    }
                )
                if attempt < self.max_retries and self.retry_sleep_seconds > 0:
                    await self.sleep(self.retry_sleep_seconds)
        assert last_error is not None
        raise InsightCastError(
            ErrorCode.LLM_REQUEST_FAILED,
            "The language model request failed.",
            details={"model": model, "reason": str(last_error)},
            stage="llm",
        ) from last_error


def _usage_fields(response: object) -> dict[str, int | None]:
    usage = getattr(response, "usage", None)
    return {
        "input_tokens": _usage_value(usage, "input_tokens"),
        "output_tokens": _usage_value(usage, "output_tokens"),
        "total_tokens": _usage_value(usage, "total_tokens"),
    }


def _usage_value(usage: object, name: str) -> int | None:
    if usage is None:
        return None
    value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
    return value if isinstance(value, int) else None
