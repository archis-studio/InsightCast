import json

import pytest

from insightcast.cli.api_client import (
    ApiProtocolError,
    ApiRequestError,
    HttpResponse,
    request_json,
    validate_health,
)


def response(status_code: int, payload: object) -> HttpResponse:
    return HttpResponse(status_code=status_code, body=json.dumps(payload).encode())


def test_request_json_decodes_successful_json_object() -> None:
    def requester(
        method: str,
        url: str,
        payload: dict[str, object] | None,
    ) -> HttpResponse:
        assert method == "GET"
        assert url == "http://api.test/health"
        assert payload is None
        return response(200, {"status": "ok"})

    assert request_json(requester, "GET", "http://api.test/health") == {"status": "ok"}


def test_request_json_formats_structured_api_error() -> None:
    def requester(
        method: str,
        url: str,
        payload: dict[str, object] | None,
    ) -> HttpResponse:
        return response(
            404,
            {
                "error_code": "JOB_NOT_FOUND",
                "message": "The requested job does not exist.",
                "details": {"job_id": "job-123"},
            },
        )

    with pytest.raises(ApiRequestError) as exc_info:
        request_json(requester, "GET", "http://api.test/jobs/job-123")

    message = str(exc_info.value)
    assert "API error JOB_NOT_FOUND" in message
    assert "The requested job does not exist." in message
    assert '"job_id": "job-123"' in message


def test_request_json_rejects_malformed_json() -> None:
    def requester(
        method: str,
        url: str,
        payload: dict[str, object] | None,
    ) -> HttpResponse:
        return HttpResponse(status_code=200, body=b"not json")

    with pytest.raises(ApiProtocolError, match="not valid JSON"):
        request_json(requester, "GET", "http://api.test/broken")


def test_request_json_rejects_non_object_json() -> None:
    def requester(
        method: str,
        url: str,
        payload: dict[str, object] | None,
    ) -> HttpResponse:
        return HttpResponse(status_code=200, body=b"[]")

    with pytest.raises(ApiProtocolError, match="must be an object"):
        request_json(requester, "GET", "http://api.test/list")


def test_validate_health_accepts_ready_dependencies() -> None:
    ffmpeg, queue_worker = validate_health(
        {
            "status": "ok",
            "dependencies": {"ffmpeg": "ready", "queue_worker": "ready"},
        }
    )

    assert ffmpeg == "ready"
    assert queue_worker == "ready"


def test_validate_health_rejects_unavailable_dependencies() -> None:
    with pytest.raises(ApiRequestError) as exc_info:
        validate_health(
            {
                "status": "ok",
                "dependencies": {"ffmpeg": "missing", "queue_worker": "ready"},
            }
        )

    assert "API is not ready" in str(exc_info.value)
    assert "ffmpeg=missing" in str(exc_info.value)
