import json
from collections.abc import Callable
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    body: bytes


Requester = Callable[[str, str, dict[str, object] | None], HttpResponse]


class CliError(Exception):
    pass


class ApiProtocolError(CliError):
    pass


class ApiRequestError(CliError):
    pass


def default_requester(
    method: str,
    url: str,
    payload: dict[str, object] | None,
) -> HttpResponse:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"} if data is not None else {}
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request) as response:
            return HttpResponse(status_code=response.status, body=response.read())
    except HTTPError as exc:
        return HttpResponse(status_code=exc.code, body=exc.read())
    except (URLError, TimeoutError, OSError) as exc:
        raise ConnectionError(str(exc)) from exc


def _decode_json(response: HttpResponse) -> dict[str, object]:
    try:
        payload = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApiProtocolError("API protocol error: response was not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise ApiProtocolError("API protocol error: response JSON must be an object.")
    return payload


def format_details(details: object) -> str:
    return json.dumps(details, indent=2, sort_keys=True, ensure_ascii=False)


def request_json(
    requester: Requester,
    method: str,
    url: str,
    payload: dict[str, object] | None = None,
    *,
    expected_status: int | None = None,
) -> dict[str, object]:
    response = requester(method, url, payload)
    if expected_status is not None and response.status_code != expected_status:
        raise ApiRequestError(
            f"API request expected HTTP {expected_status}, got {response.status_code}."
        )
    if not 200 <= response.status_code < 300:
        try:
            error = _decode_json(response)
        except ApiProtocolError:
            body = response.body.decode("utf-8", errors="replace")
            raise ApiRequestError(f"HTTP {response.status_code}: {body}") from None
        error_code = error.get("error_code")
        message = error.get("message")
        details = error.get("details")
        if isinstance(error_code, str) and isinstance(message, str):
            detail_text = format_details(details if details is not None else {})
            raise ApiRequestError(
                f"API error {error_code}: {message}\nDetails:\n{detail_text}"
            )
        raise ApiRequestError(
            f"HTTP {response.status_code}: "
            f"{response.body.decode('utf-8', errors='replace')}"
        )
    return _decode_json(response)


def validate_health(payload: dict[str, object]) -> tuple[str, str]:
    status = payload.get("status")
    dependencies = payload.get("dependencies")
    if not isinstance(dependencies, dict):
        raise ApiProtocolError("API protocol error: missing required field 'dependencies'.")
    ffmpeg = dependencies.get("ffmpeg")
    queue_worker = dependencies.get("queue_worker")
    if not isinstance(ffmpeg, str) or not isinstance(queue_worker, str):
        raise ApiProtocolError(
            "API protocol error: health dependencies must include ffmpeg and queue_worker."
        )
    if status != "ok" or ffmpeg != "ready" or queue_worker != "ready":
        raise ApiRequestError(
            "API is not ready: "
            f"status={status}, ffmpeg={ffmpeg}, queue_worker={queue_worker}. "
            "No analysis job was created."
        )
    return ffmpeg, queue_worker
