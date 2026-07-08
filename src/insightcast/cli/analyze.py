import argparse
import json
import sys
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TextIO

from pydantic import ValidationError

from insightcast.cli.api_client import (
    ApiProtocolError,
    CliError,
    Requester,
    default_requester,
    format_details,
    request_json,
    validate_health,
)
from insightcast.core.config import Settings

ACTIVE_STATUSES = {"QUEUED", "INGESTING", "TRANSCRIBING", "CURATING"}
SUCCESS_STATUS = "WAITING_SELECTION"
FAILURE_STATUS = "FAILED"


def _required_string(payload: dict[str, object], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise ApiProtocolError(f"API protocol error: missing required field '{field}'.")
    return value


def _timestamp(now: Callable[[], datetime]) -> str:
    return now().strftime("%H:%M:%S")


def _print_line(stdout: TextIO, now: Callable[[], datetime], message: str) -> None:
    print(f"[{_timestamp(now)}] {message}", file=stdout, flush=True)


def _format_transcription_progress(payload: dict[str, object]) -> str | None:
    progress = payload.get("progress")
    if not isinstance(progress, dict):
        return None
    if progress.get("stage") != "transcription":
        return None
    event = progress.get("event")
    if not isinstance(event, str):
        return None
    parts = [f"event={event}"]
    chunk_index = progress.get("chunk_index")
    chunk_count = progress.get("chunk_count")
    if isinstance(chunk_index, int) and isinstance(chunk_count, int) and chunk_count > 0:
        parts.insert(0, f"chunk {chunk_index + 1}/{chunk_count}")
    attempt = progress.get("attempt")
    max_attempts = progress.get("max_attempts")
    if isinstance(attempt, int) and isinstance(max_attempts, int) and max_attempts > 0:
        insert_at = 1 if parts and parts[0].startswith("chunk ") else 0
        parts.insert(insert_at, f"attempt {attempt}/{max_attempts}")
    return f"Transcription: {', '.join(parts)}"


def _print_verbose(stdout: TextIO, payload: dict[str, object], verbose: bool) -> None:
    if verbose:
        print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False), file=stdout)


def format_elapsed(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:g}s"
    total_seconds = int(seconds)
    minutes, remainder = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m {remainder}s"
    return f"{minutes}m {remainder}s"


def format_timecode(seconds: float) -> str:
    total_seconds = int(seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds_part = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds_part:02d}"


def _number(candidate: dict[str, object], field: str) -> float:
    value = candidate.get(field)
    if not isinstance(value, int | float):
        raise ApiProtocolError(
            f"API protocol error: candidate field '{field}' must be numeric."
        )
    return float(value)


def format_candidate(candidate: dict[str, object]) -> list[str]:
    candidate_id = _required_string(candidate, "candidate_id")
    title = _required_string(candidate, "suggested_title")
    reason = _required_string(candidate, "selection_reason")
    summary = _required_string(candidate, "summary")
    start = _number(candidate, "start_seconds")
    end = _number(candidate, "end_seconds")
    duration = _number(candidate, "duration_seconds")
    return [
        f"Candidate {candidate_id}: {title}",
        f"  Time: {format_timecode(start)} - {format_timecode(end)}",
        f"  Duration: {format_elapsed(duration)}",
        f"  Selection reason: {reason}",
        f"  Summary: {summary}",
    ]


def _source_artifacts(artifacts: dict[str, object]) -> dict[str, object]:
    source = artifacts.get("source")
    if not isinstance(source, dict):
        raise ApiProtocolError("API protocol error: artifacts.source must be an object.")
    return source


def _required_path(payload: dict[str, object], field: str) -> Path:
    return Path(_required_string(payload, field))


def format_analysis_artifacts(
    artifacts: dict[str, object],
    candidates: list[dict[str, object]],
    *,
    job_id: str,
) -> list[str]:
    analysis_id = _required_string(artifacts, "analysis_id")
    transcript_id = _required_string(artifacts, "transcript_id")
    manifest_path = _required_path(artifacts, "manifest_path")
    analysis_dir = manifest_path.parent
    if (
        manifest_path.name != "manifest.json"
        or analysis_dir.name != analysis_id
        or analysis_dir.parent.name != "analyses"
    ):
        raise ApiProtocolError(
            "API protocol error: analysis manifest path does not match analysis_id."
        )
    video_root = analysis_dir.parent.parent
    source = _source_artifacts(artifacts)
    transcript_path = _required_path(source, "transcript")
    if transcript_path.parent.name != transcript_id:
        raise ApiProtocolError(
            "API protocol error: transcript path does not match transcript_id."
        )

    lines = [
        f"Video root: {video_root}",
        f"Analysis: {analysis_id} ({analysis_dir})",
        f"Transcript: {transcript_id} ({transcript_path})",
    ]
    for candidate in candidates:
        candidate_id = _required_string(candidate, "candidate_id").upper()
        candidate_dir = analysis_dir / "candidates" / candidate_id
        lines.extend(
            (
                f"Candidate {candidate_id}: {candidate_dir / 'candidate.json'}",
                (
                    f"Renders for candidate {candidate_id} will appear under "
                    f"{candidate_dir / 'renders'}/"
                ),
            )
        )
    lines.append(f"Log: {video_root / 'logs' / f'{job_id}.log'}")
    return lines


def _pipeline_log_path(payload: dict[str, object]) -> Path | None:
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, dict):
        return None
    manifest_path = artifacts.get("manifest_path")
    job_id = payload.get("job_id")
    if not isinstance(manifest_path, str) or not isinstance(job_id, str):
        return None
    analysis_dir = Path(manifest_path).parent
    if analysis_dir.parent.name != "analyses":
        return None
    return analysis_dir.parent.parent / "logs" / f"{job_id}.log"


def _print_failed_job(payload: dict[str, object], stderr: TextIO) -> None:
    error = payload.get("error")
    if not isinstance(error, dict):
        raise ApiProtocolError("API protocol error: FAILED job is missing 'error'.")
    stage = error.get("stage")
    error_code = error.get("error_code")
    message = error.get("message")
    details = error.get("details", {})
    if stage is not None and not isinstance(stage, str):
        raise ApiProtocolError("API protocol error: error.stage must be a string.")
    if not isinstance(error_code, str) or not isinstance(message, str):
        raise ApiProtocolError(
            "API protocol error: FAILED job error is missing error_code or message."
        )
    print("Analysis failed:", file=stderr)
    print(f"  stage: {stage or 'unknown'}", file=stderr)
    print(f"  error_code: {error_code}", file=stderr)
    print(f"  message: {message}", file=stderr)
    print(f"  details: {format_details(details)}", file=stderr)
    pipeline_log = _pipeline_log_path(payload)
    if pipeline_log is not None:
        print(f"  log: {pipeline_log}", file=stderr)
    else:
        print("  Locate the operation log under OUTPUT_DIR/videos/*/logs/.", file=stderr)


def _print_success(payload: dict[str, object], stdout: TextIO) -> None:
    candidates = payload.get("candidates")
    artifacts = payload.get("artifacts")
    if not isinstance(candidates, list):
        raise ApiProtocolError("API protocol error: candidates must be a list.")
    if not isinstance(artifacts, dict):
        raise ApiProtocolError("API protocol error: artifacts must be an object.")
    job_id = _required_string(payload, "job_id")
    validated_candidates: list[dict[str, object]] = []
    print("Candidates:", file=stdout)
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise ApiProtocolError("API protocol error: each candidate must be an object.")
        validated_candidates.append(candidate)
        for line in format_candidate(candidate):
            print(line, file=stdout)
    for line in format_analysis_artifacts(
        artifacts,
        validated_candidates,
        job_id=job_id,
    ):
        print(line, file=stdout)
    print("Analysis complete; no candidates were rendered.", file=stdout)


def run_analysis(
    youtube_url: str,
    *,
    verbose: bool,
    force_reanalyze: bool = False,
    settings: Settings,
    requester: Requester = default_requester,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    now: Callable[[], datetime] = datetime.now,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    job_id: str | None = None
    try:
        _print_line(stdout, now, f"Checking API: {settings.api_base_url}")
        _print_line(stdout, now, f"Requested YouTube URL: {youtube_url}")
        health = request_json(
            requester,
            "GET",
            f"{settings.api_base_url}/health",
            expected_status=200,
        )
        ffmpeg, queue_worker = validate_health(health)
        _print_line(
            stdout,
            now,
            f"API ready: ffmpeg={ffmpeg}, queue_worker={queue_worker}",
        )
        _print_verbose(stdout, health, verbose)

        created = request_json(
            requester,
            "POST",
            f"{settings.api_base_url}/api/v1/analysis-jobs",
            {
                "youtube_url": youtube_url,
                **({"force_reanalyze": True} if force_reanalyze else {}),
            },
        )
        job_id = _required_string(created, "job_id")
        created_status = _required_string(created, "status")
        created_message = _required_string(created, "message")
        _print_line(
            stdout,
            now,
            "Analysis queued: "
            f"job_id={job_id}, status={created_status}, message={created_message}",
        )
        _print_verbose(stdout, created, verbose)

        started_at = monotonic()
        previous_status: str | None = None
        printed_transcription_progress: set[str] = set()
        first_poll = True
        while True:
            if not first_poll:
                sleep(settings.analyze_poll_interval_seconds)
            first_poll = False
            polled = request_json(
                requester,
                "GET",
                f"{settings.api_base_url}/api/v1/analysis-jobs/{job_id}",
            )
            response_job_id = _required_string(polled, "job_id")
            status = _required_string(polled, "status")
            message = _required_string(polled, "message")
            if response_job_id != job_id:
                raise ApiProtocolError(
                    "API protocol error: polled job ID does not match created job."
                )
            elapsed = monotonic() - started_at
            changed = status != previous_status
            suffix = " [status changed]" if changed else ""
            _print_line(
                stdout,
                now,
                f"{status}: {message} (elapsed {format_elapsed(elapsed)}){suffix}",
            )
            if status == "TRANSCRIBING":
                progress_line = _format_transcription_progress(polled)
                if (
                    progress_line is not None
                    and progress_line not in printed_transcription_progress
                ):
                    printed_transcription_progress.add(progress_line)
                    print(progress_line, file=stdout, flush=True)
            previous_status = status
            if status == SUCCESS_STATUS:
                _print_success(polled, stdout)
                _print_verbose(stdout, polled, verbose)
                return 0
            _print_verbose(stdout, polled, verbose)
            if status == FAILURE_STATUS:
                _print_failed_job(polled, stderr)
                return 1
            if status not in ACTIVE_STATUSES:
                raise ApiProtocolError(
                    f"API protocol error: unknown analysis status '{status}'."
                )
    except KeyboardInterrupt:
        retained = f" Job ID: {job_id}." if job_id else ""
        print(
            "Local monitoring stopped; the API job may continue."
            f"{retained}",
            file=stderr,
        )
        return 130
    except ConnectionError as exc:
        if job_id:
            print(
                f"Connection to {settings.api_base_url} failed after job creation: {exc}. "
                f"The retained job ID is {job_id}; inspect it after the API is available.",
                file=stderr,
            )
        else:
            print(
                f"Could not connect to {settings.api_base_url}: {exc}. "
                "Start the API separately with `uv run cast_api`.",
                file=stderr,
            )
        return 1
    except CliError as exc:
        print(str(exc), file=stderr)
        return 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cast_analyze",
        description="Analyze a YouTube URL through the running Insight Cast API.",
    )
    parser.add_argument("youtube_url", help="YouTube watch, share, embed, or Shorts URL")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="print the complete JSON response after each successful API request",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="create a new analysis job instead of reusing the latest one for the URL",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        settings = Settings()
    except ValidationError as exc:
        print(f"Invalid local configuration:\n{exc}", file=sys.stderr)
        return 2
    return run_analysis(
        args.youtube_url,
        verbose=args.verbose,
        force_reanalyze=args.force,
        settings=settings,
    )
