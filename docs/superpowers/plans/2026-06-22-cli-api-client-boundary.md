# CLI API Client Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract shared CLI HTTP/API behavior into `src/insightcast/cli/api_client.py` so command modules depend on a common API boundary instead of each other.

**Architecture:** Keep `cast_analyze` and `cast_render` as thin API wrappers. Move request/response primitives, structured API error handling, and health validation into a shared CLI API client module. Preserve command-specific parsing, polling, formatting, recovery, and exit-code behavior in the command modules.

**Tech Stack:** Python 3.13, standard-library `urllib.request`, `json`, `argparse`, pytest, Ruff.

---

### Task 1: Add Shared API Client Tests

**Files:**
- Create: `tests/unit/test_api_client.py`
- Modify: `tests/unit/test_render_cli.py`

- [ ] **Step 1: Write failing tests for API client behavior**

Create `tests/unit/test_api_client.py`:

```python
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
    def requester(method: str, url: str, payload: dict[str, object] | None) -> HttpResponse:
        assert method == "GET"
        assert url == "http://api.test/health"
        assert payload is None
        return response(200, {"status": "ok"})

    assert request_json(requester, "GET", "http://api.test/health") == {"status": "ok"}


def test_request_json_formats_structured_api_error() -> None:
    def requester(method: str, url: str, payload: dict[str, object] | None) -> HttpResponse:
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
    def requester(method: str, url: str, payload: dict[str, object] | None) -> HttpResponse:
        return HttpResponse(status_code=200, body=b"not json")

    with pytest.raises(ApiProtocolError, match="not valid JSON"):
        request_json(requester, "GET", "http://api.test/broken")


def test_request_json_rejects_non_object_json() -> None:
    def requester(method: str, url: str, payload: dict[str, object] | None) -> HttpResponse:
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
```

- [ ] **Step 2: Write failing dependency-boundary test**

Add to `tests/unit/test_render_cli.py`:

```python
def test_render_cli_does_not_import_analyze_cli_internals() -> None:
    import ast
    from pathlib import Path

    forbidden = {
        "ApiProtocolError",
        "ApiRequestError",
        "CliError",
        "HttpResponse",
        "Requester",
        "_request_json",
        "_validate_health",
        "default_requester",
    }
    source = Path("src/insightcast/cli/render.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "insightcast.cli.analyze"
        for alias in node.names
    }

    assert imported_names.isdisjoint(forbidden)
```

- [ ] **Step 3: Run tests and verify RED**

Run:

```bash
uv run python -m pytest tests/unit/test_api_client.py tests/unit/test_render_cli.py::test_render_cli_does_not_import_analyze_cli_internals -q
```

Expected: `test_api_client.py` import fails because `insightcast.cli.api_client` does not exist, and the boundary test fails while `render.py` still imports API helpers from `insightcast.cli.analyze`.

### Task 2: Extract `cli.api_client`

**Files:**
- Create: `src/insightcast/cli/api_client.py`
- Modify: `src/insightcast/cli/analyze.py`
- Modify: `src/insightcast/cli/render.py`

- [ ] **Step 1: Create the shared API client module**

Create `src/insightcast/cli/api_client.py` by moving the existing shared code from `analyze.py`:

```python
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


def _format_details(details: object) -> str:
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
            detail_text = _format_details(details if details is not None else {})
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
        raise ApiProtocolError(
            "API protocol error: health dependencies must include ffmpeg and queue_worker."
        )
    ffmpeg = dependencies.get("ffmpeg")
    queue_worker = dependencies.get("queue_worker")
    if not isinstance(ffmpeg, str) or not isinstance(queue_worker, str):
        raise ApiProtocolError(
            "API protocol error: health dependencies must include ffmpeg and queue_worker."
        )
    if status != "ok" or ffmpeg != "ready" or queue_worker != "ready":
        raise ApiRequestError(
            "API is not ready: "
            f"status={status}, ffmpeg={ffmpeg}, queue_worker={queue_worker}."
        )
    return ffmpeg, queue_worker
```

- [ ] **Step 2: Update `analyze.py` imports and call sites**

In `src/insightcast/cli/analyze.py`, remove local definitions of:

```python
HttpResponse
Requester
CliError
ApiProtocolError
ApiRequestError
default_requester
_decode_json
_format_details
_request_json
_validate_health
```

Add imports:

```python
from insightcast.cli.api_client import (
    ApiProtocolError,
    ApiRequestError,
    CliError,
    Requester,
    default_requester,
    request_json,
    validate_health,
)
```

Replace `_request_json(...)` with `request_json(...)` and `_validate_health(...)` with `validate_health(...)`.

- [ ] **Step 3: Update `render.py` imports and call sites**

In `src/insightcast/cli/render.py`, replace the import from `insightcast.cli.analyze` with:

```python
from insightcast.cli.api_client import (
    ApiProtocolError,
    CliError,
    Requester,
    default_requester,
    request_json,
    validate_health,
)
```

Keep analysis-specific imports from `analyze.py` only if they are truly formatting/status constants:

```python
from insightcast.cli.analyze import (
    ACTIVE_STATUSES,
    FAILURE_STATUS,
    _print_line,
    _required_string,
    format_elapsed,
)
```

Replace `_request_json(...)` with `request_json(...)` and `_validate_health(...)` with `validate_health(...)`.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```bash
uv run python -m pytest tests/unit/test_api_client.py tests/unit/test_analyze_cli.py tests/unit/test_render_cli.py -q
```

Expected: all focused CLI tests pass.

### Task 3: Tighten Boundary And Verification

**Files:**
- Modify: `tests/unit/test_render_cli.py`
- Modify: `tests/unit/test_analyze_cli.py` only if imports require it

- [ ] **Step 1: Confirm boundary test passes**

Run:

```bash
uv run python -m pytest tests/unit/test_render_cli.py::test_render_cli_does_not_import_analyze_cli_internals -q
```

Expected: PASS. If it fails, remove remaining `insightcast.cli.analyze` imports for HTTP/API helpers. Keep only formatting/status imports if no better local owner exists.

- [ ] **Step 2: Run lint for CLI files**

Run:

```bash
uv run ruff check src/insightcast/cli tests/unit/test_api_client.py tests/unit/test_analyze_cli.py tests/unit/test_render_cli.py
```

Expected: `All checks passed!`

- [ ] **Step 3: Verify CLI help remains stable**

Run:

```bash
uv run --reinstall-package insight-cast cast_analyze --help
uv run --reinstall-package insight-cast cast_render --help
```

Expected: both commands print help successfully. `cast_render` still includes `--wait`, `--video-id`, `--analysis-id`, and `--force-render`.

- [ ] **Step 4: Run full test suite**

Run:

```bash
uv run python -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 5: Check whitespace and final diff**

Run:

```bash
git diff --check
git status --short
```

Expected: no whitespace errors. Modified files should be limited to the CLI modules and their tests.

- [ ] **Step 6: Commit**

Run:

```bash
git add src/insightcast/cli/api_client.py src/insightcast/cli/analyze.py src/insightcast/cli/render.py tests/unit/test_api_client.py tests/unit/test_analyze_cli.py tests/unit/test_render_cli.py
git commit -m "refactor: extract shared CLI API client"
```

Expected: one focused refactor commit.
