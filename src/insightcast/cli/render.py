import argparse
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

from pydantic import ValidationError

from insightcast.cli.analyze import (
    ACTIVE_STATUSES,
    FAILURE_STATUS,
    ApiProtocolError,
    CliError,
    Requester,
    _print_line,
    _request_json,
    _required_string,
    _validate_health,
    default_requester,
    format_elapsed,
)
from insightcast.core.config import Settings

RENDER_ACTIVE_STATUSES = {"QUEUED", "RENDERING"}
RENDER_SUCCESS_STATUS = "COMPLETED"
JOB_NOT_FOUND_GUIDANCE = (
    "This analysis job is not retained by the running API process. "
    "If the API was restarted, run `uv run cast_analyze` again for the same URL, "
    "or inspect persisted renders under outputs/videos."
)


ProbeVideo = Callable[[Path], str | None]


def default_probe_video(video_path: Path) -> str | None:
    if shutil.which("ffprobe") is None or not video_path.is_file():
        return None
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,size",
            "-of",
            "default=noprint_wrappers=1:nokey=0",
            str(video_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _find_render_batch(payload: dict[str, object], render_id: str) -> dict[str, Any]:
    batches = payload.get("render_batches")
    if not isinstance(batches, list):
        raise ApiProtocolError("API protocol error: render_batches must be a list.")
    for batch in batches:
        if isinstance(batch, dict) and batch.get("render_id") == render_id:
            return batch
    raise ApiProtocolError(f"API protocol error: render batch '{render_id}' was not found.")


def _current_stage(batch: dict[str, Any]) -> dict[str, Any] | None:
    stages = batch.get("stages")
    if not isinstance(stages, list) or not stages:
        return None
    for stage in reversed(stages):
        if isinstance(stage, dict) and stage.get("status") == "running":
            return stage
    for stage in reversed(stages):
        if isinstance(stage, dict):
            return stage
    return None


def _candidate_result(batch: dict[str, Any], candidate_id: str) -> dict[str, Any]:
    results = batch.get("candidate_results")
    if not isinstance(results, dict):
        raise ApiProtocolError("API protocol error: candidate_results must be an object.")
    result = results.get(candidate_id)
    if not isinstance(result, dict):
        raise ApiProtocolError(
            f"API protocol error: candidate result '{candidate_id}' was not found."
        )
    return result


def _required_artifact(artifacts: dict[str, object], field: str) -> Path:
    return Path(_required_string(artifacts, field))


def _print_completed_render(
    batch: dict[str, Any],
    candidate_ids: list[str],
    *,
    stdout: TextIO,
    probe_video: ProbeVideo,
) -> None:
    print(f"Render ID: {batch.get('render_id')}", file=stdout)
    print(f"Output directory: {batch.get('output_dir')}", file=stdout)
    stage_manifest = Path(str(batch.get("output_dir"))) / "stage-manifest.json"
    for candidate_id in candidate_ids:
        result = _candidate_result(batch, candidate_id)
        if result.get("error") is not None:
            print(f"Candidate {candidate_id}: failed", file=stdout)
            print(f"  error: {result['error']}", file=stdout)
            continue
        artifacts = result.get("artifacts")
        if not isinstance(artifacts, dict):
            raise ApiProtocolError("API protocol error: completed result missing artifacts.")
        video = _required_artifact(artifacts, "burned_video")
        traditional_chinese_srt = _required_artifact(
            artifacts,
            "traditional_chinese_srt",
        )
        bilingual_ass = _required_artifact(artifacts, "bilingual_ass")
        youtube_metadata = _required_artifact(artifacts, "youtube_metadata")
        print(f"Candidate {candidate_id}:", file=stdout)
        print(f"  Video MP4: {video}", file=stdout)
        print(f"  Traditional Chinese SRT: {traditional_chinese_srt}", file=stdout)
        print(f"  Bilingual ASS: {bilingual_ass}", file=stdout)
        print(f"  YouTube metadata: {youtube_metadata}", file=stdout)
        print(f"  Render manifest: {_required_string(result, 'manifest_path')}", file=stdout)
        print(f"  Stage manifest: {stage_manifest}", file=stdout)
        probe_output = probe_video(video)
        if probe_output:
            print("  ffprobe:", file=stdout)
            for line in probe_output.splitlines():
                print(f"    {line}", file=stdout)


def run_render(
    job_id: str,
    candidate_ids: list[str],
    *,
    wait: bool,
    force_render: bool,
    settings: Settings,
    requester: Requester = default_requester,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    now: Callable[[], datetime] = datetime.now,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
    probe_video: ProbeVideo = default_probe_video,
) -> int:
    try:
        _print_line(stdout, now, f"Checking API: {settings.api_base_url}")
        health = _request_json(
            requester,
            "GET",
            f"{settings.api_base_url}/health",
            expected_status=200,
        )
        ffmpeg, queue_worker = _validate_health(health)
        _print_line(
            stdout,
            now,
            f"API ready: ffmpeg={ffmpeg}, queue_worker={queue_worker}",
        )
        created = _request_json(
            requester,
            "POST",
            f"{settings.api_base_url}/api/v1/analysis-jobs/{job_id}/renders",
            {"candidate_ids": candidate_ids, "force_render": force_render},
        )
        render_id = _required_string(created, "render_id")
        status = _required_string(created, "status")
        message = _required_string(created, "message")
        _print_line(
            stdout,
            now,
            f"Render queued: render_id={render_id}, status={status}, message={message}",
        )
        if not wait:
            print("Use --wait to monitor render completion.", file=stdout, flush=True)
            return 0

        started_at = monotonic()
        previous_status: str | None = None
        first_poll = True
        while True:
            if not first_poll:
                sleep(settings.analyze_poll_interval_seconds)
            first_poll = False
            polled = _request_json(
                requester,
                "GET",
                f"{settings.api_base_url}/api/v1/analysis-jobs/{job_id}/renders",
            )
            batch = _find_render_batch(polled, render_id)
            batch_status = _required_string(batch, "status")
            batch_message = _required_string(batch, "message")
            elapsed = monotonic() - started_at
            changed = batch_status != previous_status
            suffix = " [status changed]" if changed else ""
            _print_line(
                stdout,
                now,
                f"{batch_status}: {batch_message} (elapsed {format_elapsed(elapsed)}){suffix}",
            )
            current_stage = _current_stage(batch)
            if current_stage is not None:
                print(
                    "Current stage: "
                    f"{current_stage.get('stage')} ({current_stage.get('status')})",
                    file=stdout,
                    flush=True,
                )
            previous_status = batch_status
            if batch_status == RENDER_SUCCESS_STATUS:
                _print_completed_render(
                    batch,
                    candidate_ids,
                    stdout=stdout,
                    probe_video=probe_video,
                )
                return 0
            if batch_status == FAILURE_STATUS:
                _print_failed_render(batch, stderr)
                return 1
            if batch_status not in RENDER_ACTIVE_STATUSES and batch_status not in ACTIVE_STATUSES:
                raise ApiProtocolError(
                    f"API protocol error: unknown render status '{batch_status}'."
                )
    except ConnectionError as exc:
        print(
            f"Could not connect to {settings.api_base_url}: {exc}. "
            "Start the API separately with `uv run cast_api`.",
            file=stderr,
        )
        return 1
    except CliError as exc:
        print(str(exc), file=stderr)
        if "API error JOB_NOT_FOUND" in str(exc):
            print(JOB_NOT_FOUND_GUIDANCE, file=stderr)
        return 1


def _print_failed_render(batch: dict[str, Any], stderr: TextIO) -> None:
    print("Render failed:", file=stderr)
    print(f"  render_id: {batch.get('render_id')}", file=stderr)
    print(f"  output_dir: {batch.get('output_dir')}", file=stderr)
    for stage in batch.get("stages", []):
        if isinstance(stage, dict) and stage.get("error"):
            print(f"  stage: {stage.get('stage')}", file=stderr)
            print(f"  error: {stage.get('error')}", file=stderr)
            print(f"  resume: {stage.get('resume_strategy')}", file=stderr)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cast_render",
        description="Render selected candidates through the running Insight Cast API.",
    )
    parser.add_argument("job_id", help="analysis job ID from cast_analyze")
    parser.add_argument("candidate_ids", nargs="+", help="candidate IDs to render")
    parser.add_argument("--wait", action="store_true", help="poll until render completion")
    parser.add_argument(
        "--force-render",
        action="store_true",
        help="render even when reusable artifacts already exist",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        settings = Settings()
    except ValidationError as exc:
        print(f"Invalid local configuration:\n{exc}", file=sys.stderr)
        return 2
    return run_render(
        args.job_id,
        [candidate_id.upper() for candidate_id in args.candidate_ids],
        wait=args.wait,
        force_render=args.force_render,
        settings=settings,
    )
