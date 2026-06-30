import json
from collections.abc import Callable
from datetime import datetime
from io import StringIO
from typing import Any

import pytest

from insightcast.cli import analyze
from insightcast.cli.analyze import run_analysis
from insightcast.cli.api_client import HttpResponse
from insightcast.core.config import Settings

YOUTUBE_URL = "https://www.youtube.com/watch?v=abc123DEF_-"
API_BASE_URL = "http://127.0.0.1:8765"


def response(status_code: int, payload: object) -> HttpResponse:
    return HttpResponse(status_code=status_code, body=json.dumps(payload).encode())


def healthy_response() -> HttpResponse:
    return response(
        200,
        {
            "status": "ok",
            "message": "Insight Cast is ready.",
            "dependencies": {"ffmpeg": "ready", "queue_worker": "ready"},
        },
    )


def queued_response() -> HttpResponse:
    return response(
        202,
        {
            "job_id": "job-123",
            "status": "QUEUED",
            "message": "Analysis job is queued.",
            "artifacts": {},
            "created_at": "2026-06-07T00:00:00Z",
        },
    )


def job_response(
    status: str,
    message: str,
    *,
    candidates: list[dict[str, object]] | None = None,
    error: dict[str, object] | None = None,
    artifacts: dict[str, object] | None = None,
    progress: dict[str, object] | None = None,
) -> HttpResponse:
    resolved_artifacts = artifacts
    if resolved_artifacts is None:
        resolved_artifacts = (
            video_centric_artifacts() if status == "WAITING_SELECTION" else {}
        )
    return response(
        200,
        {
            "job_id": "job-123",
            "status": status,
            "message": message,
            "candidates": candidates or [],
            "render_batches": [],
            "error": error,
            "progress": progress,
            "artifacts": resolved_artifacts,
            "created_at": "2026-06-07T00:00:00Z",
            "updated_at": "2026-06-07T00:00:01Z",
        },
    )


def video_centric_artifacts() -> dict[str, object]:
    video_root = "/tmp/outputs/videos/abc123DEF_-_video-title"
    analysis_id = "20260607-120000-analys"
    transcript_id = "tx-abcdef123456"
    return {
        "video_id": "abc123DEF_-",
        "analysis_id": analysis_id,
        "transcript_id": transcript_id,
        "manifest_path": f"{video_root}/analyses/{analysis_id}/manifest.json",
        "source": {
            "source_video": f"{video_root}/source/source.mp4",
            "source_audio": f"{video_root}/source/audio.mp3",
            "transcript": (
                f"{video_root}/transcripts/{transcript_id}/transcript.json"
            ),
            "candidates": f"{video_root}/analyses/{analysis_id}/candidates.json",
        },
    }


class ScriptedRequester:
    def __init__(self, outcomes: list[HttpResponse | BaseException]) -> None:
        self.outcomes = iter(outcomes)
        self.requests: list[tuple[str, str, dict[str, object] | None]] = []

    def __call__(
        self,
        method: str,
        url: str,
        payload: dict[str, object] | None,
    ) -> HttpResponse:
        self.requests.append((method, url, payload))
        outcome = next(self.outcomes)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += seconds


class FlushTrackingStdout(StringIO):
    def __init__(self) -> None:
        super().__init__()
        self.flush_count = 0

    def flush(self) -> None:
        self.flush_count += 1
        super().flush()


def settings(**overrides: Any) -> Settings:
    return Settings(
        _env_file=None,
        openai_api_key="sk-test-value",
        **overrides,
    )


def execute(
    requester: ScriptedRequester,
    *,
    verbose: bool = False,
    cli_settings: Settings | None = None,
    sleep: Callable[[float], None] | None = None,
    monotonic: Callable[[], float] | None = None,
) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    code = run_analysis(
        YOUTUBE_URL,
        verbose=verbose,
        settings=cli_settings or settings(),
        requester=requester,
        sleep=sleep or (lambda _: None),
        monotonic=monotonic or (lambda: 0.0),
        now=lambda: datetime(2026, 6, 7, 12, 0, 0),
        stdout=stdout,
        stderr=stderr,
    )
    return code, stdout.getvalue(), stderr.getvalue()


def test_print_line_flushes_status_updates_immediately() -> None:
    stdout = FlushTrackingStdout()

    analyze._print_line(
        stdout,
        lambda: datetime(2026, 6, 7, 12, 0, 0),
        "INGESTING: Downloading the source video.",
    )

    assert stdout.getvalue() == "[12:00:00] INGESTING: Downloading the source video.\n"
    assert stdout.flush_count == 1


def test_checks_healthy_api_before_creating_job() -> None:
    requester = ScriptedRequester(
        [
            healthy_response(),
            queued_response(),
            job_response("WAITING_SELECTION", "Candidates are ready."),
        ]
    )

    code, output, errors = execute(requester)

    assert code == 0
    assert requester.requests[0] == ("GET", f"{API_BASE_URL}/health", None)
    assert f"Requested YouTube URL: {YOUTUBE_URL}" in output
    assert "API ready: ffmpeg=ready, queue_worker=ready" in output
    assert errors == ""


def test_unavailable_api_prints_server_guidance_without_posting() -> None:
    requester = ScriptedRequester([ConnectionError("connection refused")])

    code, output, errors = execute(requester)

    assert code == 1
    assert output.startswith("[12:00:00] Checking API:")
    assert API_BASE_URL in errors
    assert "uv run cast_api" in errors
    assert len(requester.requests) == 1


def test_dependency_not_ready_stops_before_post() -> None:
    requester = ScriptedRequester(
        [
            response(
                200,
                {
                    "status": "ok",
                    "message": "Starting.",
                    "dependencies": {
                        "ffmpeg": "missing",
                        "queue_worker": "starting",
                    },
                },
            )
        ]
    )

    code, _, errors = execute(requester)

    assert code == 1
    assert "ffmpeg=missing" in errors
    assert "queue_worker=starting" in errors
    assert len(requester.requests) == 1


def test_health_requires_http_200() -> None:
    requester = ScriptedRequester(
        [
            response(
                201,
                {
                    "status": "ok",
                    "message": "Insight Cast is ready.",
                    "dependencies": {
                        "ffmpeg": "ready",
                        "queue_worker": "ready",
                    },
                },
            )
        ]
    )

    code, _, errors = execute(requester)

    assert code == 1
    assert "expected HTTP 200" in errors
    assert len(requester.requests) == 1


def test_posts_only_youtube_url() -> None:
    requester = ScriptedRequester(
        [
            healthy_response(),
            queued_response(),
            job_response("WAITING_SELECTION", "Candidates are ready."),
        ]
    )

    code, output, _ = execute(requester)

    assert code == 0
    assert requester.requests[1] == (
        "POST",
        f"{API_BASE_URL}/api/v1/analysis-jobs",
        {"youtube_url": YOUTUBE_URL},
    )
    assert (
        "Analysis queued: job_id=job-123, status=QUEUED, "
        "message=Analysis job is queued."
    ) in output


def test_force_posts_reanalysis_request() -> None:
    requester = ScriptedRequester(
        [
            healthy_response(),
            queued_response(),
            job_response("WAITING_SELECTION", "Candidates are ready."),
        ]
    )
    stdout = StringIO()
    stderr = StringIO()

    code = run_analysis(
        YOUTUBE_URL,
        verbose=False,
        force_reanalyze=True,
        settings=settings(),
        requester=requester,
        sleep=lambda _: None,
        monotonic=lambda: 0.0,
        now=lambda: datetime(2026, 6, 7, 12, 0, 0),
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 0
    assert requester.requests[1] == (
        "POST",
        f"{API_BASE_URL}/api/v1/analysis-jobs",
        {"youtube_url": YOUTUBE_URL, "force_reanalyze": True},
    )
    assert stderr.getvalue() == ""


def test_prints_transcription_chunk_progress_when_available() -> None:
    requester = ScriptedRequester(
        [
            healthy_response(),
            queued_response(),
            job_response(
                "TRANSCRIBING",
                "Transcribing English audio.",
                progress={
                    "stage": "transcription",
                    "event": "started",
                    "chunk_index": 1,
                    "chunk_count": 5,
                    "attempt": 2,
                    "max_attempts": 3,
                },
            ),
            job_response("WAITING_SELECTION", "Candidates are ready."),
        ]
    )

    code, output, errors = execute(requester)

    assert code == 0
    assert errors == ""
    assert "Transcription: chunk 2/5, attempt 2/3, event=started" in output


def test_prints_transcription_progress_only_while_transcribing() -> None:
    requester = ScriptedRequester(
        [
            healthy_response(),
            queued_response(),
            job_response(
                "CURATING",
                "Selecting complete candidate ranges.",
                progress={
                    "stage": "transcription",
                    "event": "completed_all",
                    "chunk_count": 2,
                    "processed_chunks": 2,
                },
            ),
            job_response("WAITING_SELECTION", "Candidates are ready."),
        ]
    )

    code, output, errors = execute(requester)

    assert code == 0
    assert errors == ""
    assert "Transcription:" not in output


def test_polls_immediately_then_uses_configured_interval_and_prints_heartbeats() -> None:
    clock = FakeClock()
    requester = ScriptedRequester(
        [
            healthy_response(),
            queued_response(),
            job_response("QUEUED", "Analysis job is queued."),
            job_response("INGESTING", "Downloading the source video."),
            job_response("INGESTING", "Downloading the source video."),
            job_response("WAITING_SELECTION", "Candidates are ready."),
        ]
    )

    code, output, _ = execute(
        requester,
        cli_settings=settings(analyze_poll_interval_seconds=2.5),
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )

    assert code == 0
    assert clock.sleeps == [2.5, 2.5, 2.5]
    assert requester.requests[2][0] == "GET"
    assert output.count("INGESTING: Downloading the source video.") == 2
    assert "QUEUED: Analysis job is queued. (elapsed 0s) [status changed]" in output
    assert "INGESTING: Downloading the source video. (elapsed 2.5s) [status changed]" in output
    assert "INGESTING: Downloading the source video. (elapsed 5s)" in output


def test_formats_candidates_and_video_centric_artifact_paths() -> None:
    requester = ScriptedRequester(
        [
            healthy_response(),
            queued_response(),
            job_response(
                "WAITING_SELECTION",
                "Candidates are ready.",
                candidates=[
                    {
                        "candidate_id": "A",
                        "start_seconds": 90,
                        "end_seconds": 180,
                        "suggested_title": "A useful segment",
                        "selection_reason": "Focused explanation",
                        "summary": "The speaker explains the core idea.",
                        "score": 0.9,
                        "duration_seconds": 90,
                    }
                ],
                artifacts=video_centric_artifacts(),
            ),
        ]
    )

    code, output, _ = execute(requester)

    assert code == 0
    for expected in (
        "Candidate A",
        "A useful segment",
        "00:01:30 - 00:03:00",
        "Duration: 1m 30s",
        "Focused explanation",
        "The speaker explains the core idea.",
        "Video root: /tmp/outputs/videos/abc123DEF_-_video-title",
        (
            "Analysis: 20260607-120000-analys "
            "(/tmp/outputs/videos/abc123DEF_-_video-title/"
            "analyses/20260607-120000-analys)"
        ),
        (
            "Transcript: tx-abcdef123456 "
            "(/tmp/outputs/videos/abc123DEF_-_video-title/"
            "transcripts/tx-abcdef123456/transcript.json)"
        ),
        (
            "Candidate A: /tmp/outputs/videos/abc123DEF_-_video-title/"
            "analyses/20260607-120000-analys/candidates/A/candidate.json"
        ),
        (
            "Log: /tmp/outputs/videos/abc123DEF_-_video-title/"
            "logs/job-123.log"
        ),
        (
            "Renders for candidate A will appear under "
            "/tmp/outputs/videos/abc123DEF_-_video-title/"
            "analyses/20260607-120000-analys/candidates/A/renders/"
        ),
        "Analysis complete; no candidates were rendered.",
    ):
        assert expected in output


def test_verbose_prints_every_successful_json_response() -> None:
    requester = ScriptedRequester(
        [
            healthy_response(),
            queued_response(),
            job_response("WAITING_SELECTION", "Candidates are ready."),
        ]
    )

    code, output, _ = execute(requester, verbose=True)

    assert code == 0
    assert output.count('"status": "ok"') == 1
    assert output.count('"job_id": "job-123"') == 2
    assert output.index('"status": "ok"') > output.index("API ready:")


def test_standard_api_error_is_formatted() -> None:
    requester = ScriptedRequester(
        [
            healthy_response(),
            response(
                400,
                {
                    "error_code": "INVALID_YOUTUBE_URL",
                    "message": "Unsupported URL.",
                    "details": {"youtube_url": "bad"},
                },
            ),
        ]
    )

    code, _, errors = execute(requester)

    assert code == 1
    assert "INVALID_YOUTUBE_URL" in errors
    assert "Unsupported URL." in errors
    assert '"youtube_url": "bad"' in errors


@pytest.mark.parametrize(
    "malformed",
    [
        HttpResponse(status_code=200, body=b"{"),
        response(200, []),
        response(200, {"status": "ok"}),
    ],
)
def test_malformed_responses_report_protocol_error(malformed: HttpResponse) -> None:
    requester = ScriptedRequester([malformed])

    code, _, errors = execute(requester)

    assert code == 1
    assert "API protocol error" in errors


def test_failed_job_prints_structured_error_and_pipeline_log() -> None:
    requester = ScriptedRequester(
        [
            healthy_response(),
            queued_response(),
            job_response(
                "FAILED",
                "Analysis failed.",
                error={
                    "stage": "transcribing",
                    "error_code": "TRANSCRIPTION_FAILED",
                    "message": "Audio could not be transcribed.",
                    "details": {"chunk": 3},
                },
                artifacts=video_centric_artifacts(),
            ),
        ]
    )

    code, _, errors = execute(requester)

    assert code == 1
    assert "stage: transcribing" in errors
    assert "error_code: TRANSCRIPTION_FAILED" in errors
    assert "Audio could not be transcribed." in errors
    assert '"chunk": 3' in errors
    assert (
        "/tmp/outputs/videos/abc123DEF_-_video-title/logs/job-123.log" in errors
    )


def test_poll_connection_failure_retains_job_id_without_reposting() -> None:
    requester = ScriptedRequester(
        [
            healthy_response(),
            queued_response(),
            ConnectionError("connection reset"),
        ]
    )

    code, _, errors = execute(requester)

    assert code == 1
    assert "job-123" in errors
    assert sum(method == "POST" for method, _, _ in requester.requests) == 1


def test_unknown_status_is_a_protocol_error() -> None:
    requester = ScriptedRequester(
        [
            healthy_response(),
            queued_response(),
            job_response("MYSTERY", "Unknown state."),
        ]
    )

    code, _, errors = execute(requester)

    assert code == 1
    assert "API protocol error" in errors
    assert "MYSTERY" in errors


def test_ctrl_c_stops_monitoring_and_retains_job_id() -> None:
    requester = ScriptedRequester(
        [
            healthy_response(),
            queued_response(),
            job_response("QUEUED", "Analysis job is queued."),
        ]
    )

    def interrupt(_: float) -> None:
        raise KeyboardInterrupt

    code, _, errors = execute(requester, sleep=interrupt)

    assert code == 130
    assert "Local monitoring stopped" in errors
    assert "API job may continue" in errors
    assert "job-123" in errors
