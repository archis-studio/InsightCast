import ast
import json
from collections.abc import Callable
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Any

from insightcast.cli.api_client import HttpResponse
from insightcast.cli.render import run_render
from insightcast.core.config import Settings

API_BASE_URL = "http://127.0.0.1:8765"
JOB_ID = "job-123"
RENDER_ID = "render-abc"
VIDEO_ID = "abc123DEF_-"
ANALYSIS_ID = "analysis-123"


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


def render_queued_response() -> HttpResponse:
    return response(
        202,
        {
            "job_id": JOB_ID,
            "render_id": RENDER_ID,
            "status": "QUEUED",
            "message": "Render batch is queued.",
            "candidate_ids": ["B"],
            "artifacts": {},
            "created_at": "2026-06-21T00:00:00Z",
            "updated_at": "2026-06-21T00:00:00Z",
        },
    )


def job_not_found_response() -> HttpResponse:
    return response(
        404,
        {
            "error_code": "JOB_NOT_FOUND",
            "message": "The requested job does not exist in this server process.",
            "details": {"job_id": JOB_ID},
        },
    )


def persisted_render_list_response() -> HttpResponse:
    output_dir = "/tmp/outputs/videos/example/analyses/analysis-123/candidates/B/renders/render-old"
    return response(
        200,
        {
            "video_id": VIDEO_ID,
            "renders": [
                {
                    "render_id": "render-old",
                    "operation_id": "op-render-old",
                    "kind": "candidate",
                    "analysis_id": ANALYSIS_ID,
                    "candidate_id": "B",
                    "start_seconds": 10.0,
                    "end_seconds": 70.0,
                    "render_state": "ready",
                    "publish_state": "not-uploaded",
                    "created_at": "2026-06-21T00:00:00Z",
                    "completed_at": "2026-06-21T00:01:00Z",
                    "manifest_path": f"{output_dir}/manifest.json",
                    "artifacts": {
                        "traditional_chinese_srt": f"{output_dir}/subtitles.zh-TW.srt",
                        "bilingual_ass": f"{output_dir}/subtitles.bilingual.ass",
                        "burned_video": f"{output_dir}/video.mp4",
                        "youtube_metadata": f"{output_dir}/youtube-metadata.json",
                    },
                }
            ],
        },
    )


def render_list_response(status: str, *, stage_status: str = "running") -> HttpResponse:
    output_dir = "/tmp/outputs/videos/example/analyses/analysis/candidates/B/renders/render-abc"
    return response(
        200,
        {
            "job_id": JOB_ID,
            "status": "ok",
            "message": "1 render batch(es) found.",
            "artifacts": (
                {
                    RENDER_ID: {
                        "B": {
                            "traditional_chinese_srt": f"{output_dir}/subtitles.zh-TW.srt",
                            "bilingual_ass": f"{output_dir}/subtitles.bilingual.ass",
                            "burned_video": f"{output_dir}/video.mp4",
                            "youtube_metadata": f"{output_dir}/youtube-metadata.json",
                            "render_id": RENDER_ID,
                            "manifest_path": f"{output_dir}/manifest.json",
                        }
                    }
                }
                if status == "COMPLETED"
                else {}
            ),
            "render_batches": [
                {
                    "render_id": RENDER_ID,
                    "candidate_ids": ["B"],
                    "status": status,
                    "message": (
                        "All selected candidates rendered successfully."
                        if status == "COMPLETED"
                        else "Rendering selected candidates."
                    ),
                    "output_dir": output_dir,
                    "candidate_results": (
                        {
                            "B": {
                                "candidate_id": "B",
                                "output_dir": output_dir,
                                "manifest_path": f"{output_dir}/manifest.json",
                                "artifacts": {
                                    "traditional_chinese_srt": (
                                        f"{output_dir}/subtitles.zh-TW.srt"
                                    ),
                                    "bilingual_ass": f"{output_dir}/subtitles.bilingual.ass",
                                    "burned_video": f"{output_dir}/video.mp4",
                                    "youtube_metadata": f"{output_dir}/youtube-metadata.json",
                                },
                                "error": None,
                            }
                        }
                        if status == "COMPLETED"
                        else {}
                    ),
                    "stages": [
                        {
                            "stage": "cut_clip",
                            "status": "completed",
                            "resume_strategy": "rerun cut_clip",
                            "artifacts": {},
                            "error": None,
                        },
                        {
                            "stage": "burn_subtitles",
                            "status": stage_status,
                            "resume_strategy": "reuse burned video",
                            "artifacts": {},
                            "error": None,
                        },
                    ],
                    "created_at": "2026-06-21T00:00:00Z",
                    "updated_at": "2026-06-21T00:00:01Z",
                }
            ],
        },
    )


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


def settings(**overrides: Any) -> Settings:
    return Settings(
        _env_file=None,
        openai_api_key="sk-test-value",
        **overrides,
    )


def execute(
    requester: ScriptedRequester,
    *,
    wait: bool = False,
    force_render: bool = False,
    video_id: str | None = None,
    analysis_id: str | None = None,
    sleep: Callable[[float], None] | None = None,
    monotonic: Callable[[], float] | None = None,
) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    code = run_render(
        JOB_ID,
        ["B"],
        wait=wait,
        force_render=force_render,
        video_id=video_id,
        analysis_id=analysis_id,
        settings=settings(analyze_poll_interval_seconds=2.5),
        requester=requester,
        sleep=sleep or (lambda _: None),
        monotonic=monotonic or (lambda: 0.0),
        now=lambda: datetime(2026, 6, 21, 12, 0, 0),
        stdout=stdout,
        stderr=stderr,
        probe_video=lambda _path: None,
    )
    return code, stdout.getvalue(), stderr.getvalue()


def test_queues_requested_candidate_through_api_without_waiting() -> None:
    requester = ScriptedRequester([healthy_response(), render_queued_response()])

    code, output, errors = execute(requester)

    assert code == 0
    assert requester.requests == [
        ("GET", f"{API_BASE_URL}/health", None),
        (
            "POST",
            f"{API_BASE_URL}/api/v1/analysis-jobs/{JOB_ID}/renders",
            {"candidate_ids": ["B"], "force_render": False},
        ),
    ]
    assert "Render queued: render_id=render-abc, status=QUEUED" in output
    assert "Use --wait to monitor render completion." in output
    assert errors == ""


def test_wait_polls_renders_and_prints_completed_artifacts() -> None:
    clock = FakeClock()
    requester = ScriptedRequester(
        [
            healthy_response(),
            render_queued_response(),
            render_list_response("RENDERING"),
            render_list_response("COMPLETED", stage_status="completed"),
        ]
    )

    code, output, errors = execute(
        requester,
        wait=True,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )

    assert code == 0
    assert clock.sleeps == [2.5]
    assert requester.requests[2:] == [
        ("GET", f"{API_BASE_URL}/api/v1/analysis-jobs/{JOB_ID}/renders", None),
        ("GET", f"{API_BASE_URL}/api/v1/analysis-jobs/{JOB_ID}/renders", None),
    ]
    assert "RENDERING: Rendering selected candidates. (elapsed 0s) [status changed]" in output
    assert "Current stage: burn_subtitles (running)" in output
    assert "COMPLETED: All selected candidates rendered successfully." in output
    assert "Traditional Chinese SRT:" in output
    assert "Bilingual ASS:" in output
    assert "Video MP4:" in output
    assert "Stage manifest:" in output
    assert errors == ""


def test_force_render_is_sent_to_api() -> None:
    requester = ScriptedRequester([healthy_response(), render_queued_response()])

    code, _, _ = execute(requester, force_render=True)

    assert code == 0
    assert requester.requests[1][2] == {"candidate_ids": ["B"], "force_render": True}


def test_job_not_found_explains_process_local_job_ids() -> None:
    requester = ScriptedRequester([healthy_response(), job_not_found_response()])

    code, output, errors = execute(requester)

    assert code == 1
    assert "API ready" in output
    assert "API error JOB_NOT_FOUND" in errors
    assert "not retained by the running API process" in errors
    assert "If the API was restarted" in errors
    assert "cast_analyze" in errors
    assert "outputs/videos" in errors


def test_job_not_found_can_report_matching_persisted_render() -> None:
    requester = ScriptedRequester(
        [healthy_response(), job_not_found_response(), persisted_render_list_response()]
    )

    code, output, errors = execute(
        requester,
        video_id=VIDEO_ID,
        analysis_id=ANALYSIS_ID,
    )

    assert code == 0
    assert requester.requests[-1] == (
        "GET",
        f"{API_BASE_URL}/api/v1/videos/{VIDEO_ID}/renders",
        None,
    )
    assert "API ready" in output
    assert "Found persisted render artifacts after JOB_NOT_FOUND." in output
    assert "Render ID: render-old" in output
    assert "Candidate B:" in output
    assert "Video MP4:" in output
    assert "Traditional Chinese SRT:" in output
    assert "Bilingual ASS:" in output
    assert "YouTube metadata:" in output
    assert "API error JOB_NOT_FOUND" in errors


def test_render_cli_does_not_import_analyze_cli_api_helpers() -> None:
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
