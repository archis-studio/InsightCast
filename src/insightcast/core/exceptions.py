from collections.abc import Mapping
from typing import Any

from insightcast.domain.enums import ErrorCode


class InsightCastError(Exception):
    def __init__(
        self,
        error_code: ErrorCode,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
        stage: str | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.details = dict(details or {})
        self.stage = stage

