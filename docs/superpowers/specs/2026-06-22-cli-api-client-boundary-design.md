# CLI API Client Boundary Design

## Goal

Make the CLI layer depend on a shared API-client boundary instead of importing
helpers from another CLI command module. The intended dependency direction is:

```text
cli commands -> cli.api_client -> HTTP API -> service layer
```

This keeps `cast_analyze` and `cast_render` as API wrappers, preserves the API as
the product boundary, and avoids exposing service or storage internals to normal
operator commands.

## Scope

This change includes:

- Create `src/insightcast/cli/api_client.py`.
- Move common HTTP request primitives and API error handling out of
  `src/insightcast/cli/analyze.py`.
- Move shared health-response validation into the same API client module.
- Update `cast_analyze` and `cast_render` to import the shared API client.
- Add focused unit tests for the shared API client behavior.
- Keep the existing console output and exit-code contracts unchanged.

This change does not:

- Split `JobService`.
- Split `VideoStore`.
- Add or change API endpoints.
- Change analysis, render, cache, or persisted artifact behavior.
- Make `cast_cache` an API-backed command.
- Add durable queues or persistent process-local job restoration.

## Current Problem

`src/insightcast/cli/render.py` imports internal request helpers from
`src/insightcast/cli/analyze.py`. That works mechanically, but it makes one CLI
command module act as a shared library for another command. As more commands are
added, that pattern encourages accidental coupling between command-specific
formatting, polling, and shared HTTP behavior.

The shared behavior is not analysis-specific:

- `HttpResponse`
- `Requester`
- production `default_requester`
- JSON decoding
- structured API error formatting
- HTTP status handling
- health dependency validation

Those responsibilities belong in a small API-client module under `cli/`.

## Design

Add `src/insightcast/cli/api_client.py` with one responsibility: provide a small,
testable HTTP/API boundary for CLI commands.

The module exposes:

```python
@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    body: bytes

Requester = Callable[[str, str, dict[str, object] | None], HttpResponse]

class CliError(Exception): ...
class ApiProtocolError(CliError): ...
class ApiRequestError(CliError): ...

def default_requester(
    method: str,
    url: str,
    payload: dict[str, object] | None,
) -> HttpResponse: ...

def request_json(
    requester: Requester,
    method: str,
    url: str,
    payload: dict[str, object] | None = None,
    *,
    expected_status: int | None = None,
) -> dict[str, object]: ...

def validate_health(payload: dict[str, object]) -> tuple[str, str]: ...
```

`analyze.py` keeps analysis-specific behavior:

- CLI argument parsing for `cast_analyze`
- analysis job creation and polling
- candidate/source formatting
- verbose output
- analysis exit-code handling

`render.py` keeps render-specific behavior:

- CLI argument parsing for `cast_render`
- render creation and polling
- persisted render recovery
- render artifact formatting
- render exit-code handling

Both command modules import from `cli.api_client` instead of importing shared
helpers from each other.

## Error Handling

The extracted API client must preserve existing behavior:

- Connection failures still become `ConnectionError`.
- Non-2xx responses still prefer the standard `error_code`, `message`, and
  `details` shape.
- Malformed JSON or non-object JSON still becomes `ApiProtocolError`.
- Health validation still requires `status == "ok"`,
  `dependencies.ffmpeg == "ready"`, and `dependencies.queue_worker == "ready"`.
- Existing command-level `JOB_NOT_FOUND`, `Ctrl-C`, and polling behavior remains
  in the command modules.

## Testing

Use TDD. Start with tests that fail before the extraction:

- `render.py` must no longer import from `insightcast.cli.analyze`.
- `api_client.request_json` decodes successful JSON objects.
- `api_client.request_json` formats structured API errors.
- `api_client.request_json` rejects malformed or non-object JSON.
- `api_client.validate_health` accepts ready dependencies.
- `api_client.validate_health` rejects unavailable dependencies.

Then update the existing analyze/render CLI tests to import shared test helpers
from the new module where needed. The public CLI behavior should remain covered
by the existing `test_analyze_cli.py` and `test_render_cli.py` suites.

Verification commands:

```bash
uv run ruff check src/insightcast/cli tests/unit/test_analyze_cli.py tests/unit/test_render_cli.py tests/unit/test_api_client.py
uv run python -m pytest tests/unit/test_api_client.py tests/unit/test_analyze_cli.py tests/unit/test_render_cli.py -q
uv run python -m pytest -q
uv run cast_analyze --help
uv run cast_render --help
git diff --check
```

## Rollout

Implement this as one small refactor commit. If behavior changes show up in
tests or manual help output, treat that as a bug in the refactor and keep the
existing CLI contract.

Do not begin `JobService` or `VideoStore` decomposition as part of this work.
