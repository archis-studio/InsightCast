import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, TextIO

from openai import OpenAI
from pydantic import ValidationError

from insightcast.core.config import Settings, get_settings
from insightcast.core.exceptions import InsightCastError
from insightcast.domain.models import Candidate, Transcript
from insightcast.engines.publish_engine import PublishEngine
from insightcast.infrastructure.openai_client import StructuredOpenAIClient
from insightcast.infrastructure.ytdlp_client import YouTubeMetadata
from insightcast.storage.file_job_writer import FileJobWriter
from insightcast.storage.video_store import VideoStore


def _transcript_excerpt(transcript: Transcript, candidate: Candidate) -> str:
    return " ".join(
        segment.text
        for segment in transcript.segments
        if segment.end_seconds > candidate.start_seconds
        and segment.start_seconds < candidate.end_seconds
    )


def _load_source_metadata(video_root: Path) -> YouTubeMetadata:
    manifest_path = video_root / "source" / "manifest.json"
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        source_metadata = payload["source_metadata"]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError) as exc:
        raise ValueError(f"Could not read source metadata: {manifest_path}") from exc
    return YouTubeMetadata.model_validate(source_metadata)


def _build_publish_engine(settings: Settings) -> PublishEngine:
    sdk = OpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        timeout=settings.openai_timeout_seconds,
        max_retries=0,
    )
    structured = StructuredOpenAIClient(
        sdk,
        timeout_seconds=settings.openai_timeout_seconds,
        max_retries=settings.openai_max_retries,
        retry_sleep_seconds=settings.openai_retry_sleep_seconds,
    )
    return PublishEngine(
        client=structured,
        model=settings.effective_metadata_model,
        writer=FileJobWriter(),
    )


async def run_metadata(
    video_id: str,
    analysis_id: str,
    candidate_id: str,
    *,
    output_dir: Path,
    output: Path | None = None,
    publish_engine: Any | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    resolved_stdout = stdout or sys.stdout
    resolved_stderr = stderr or sys.stderr
    try:
        store = VideoStore(output_dir, FileJobWriter())
        analyses = store.list_analyses(video_id)
        analysis = next(
            (entry for entry in analyses if entry.manifest.analysis_id == analysis_id),
            None,
        )
        if analysis is None:
            print(
                f"Analysis not found: video_id={video_id} analysis_id={analysis_id}",
                file=resolved_stderr,
            )
            return 2

        resolved_candidate_id = candidate_id.upper()
        candidate_path = analysis.candidate_paths.get(resolved_candidate_id)
        if candidate_path is None:
            print(f"Candidate not found: {resolved_candidate_id}", file=resolved_stderr)
            return 2
        candidate = Candidate.model_validate_json(candidate_path.read_text(encoding="utf-8"))
        transcript_path = (
            analysis.root
            / "transcripts"
            / analysis.manifest.transcript_id
            / "transcript.json"
        )
        transcript = Transcript.model_validate_json(
            transcript_path.read_text(encoding="utf-8")
        )
        source_metadata = _load_source_metadata(analysis.root)
        destination = (
            output
            if output is not None
            else candidate_path.parent / "youtube-metadata.preview.json"
        )
        publisher = publish_engine or _build_publish_engine(get_settings())
        generated = await publisher.generate(
            source_metadata=source_metadata,
            candidate_suggested_title=candidate.suggested_title,
            summary=candidate.summary,
            transcript_excerpt=_transcript_excerpt(transcript, candidate),
            candidate_core_claim=candidate.core_claim,
            candidate_payoff=candidate.payoff,
            candidate_argument_arc=candidate.argument_arc,
            candidate_boundary_notes=candidate.boundary_notes,
            destination=destination,
        )
    except (InsightCastError, OSError, UnicodeDecodeError, ValidationError, ValueError) as exc:
        print(f"Metadata regeneration failed: {exc}", file=resolved_stderr)
        return 2

    print("Metadata regenerated.", file=resolved_stdout)
    print(f"Output: {destination}", file=resolved_stdout)
    print(f"Title: {generated.title}", file=resolved_stdout)
    for variant in generated.title_variants:
        print(f"Variant {variant.strategy}: {variant.title}", file=resolved_stdout)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cast_metadata",
        description="Regenerate YouTube metadata for a persisted analysis candidate.",
    )
    parser.add_argument("video_id")
    parser.add_argument("analysis_id")
    parser.add_argument("candidate_id")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Override OUTPUT_DIR for persisted InsightCast artifacts.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write metadata JSON to this path instead of the candidate preview file.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = get_settings()
    output_dir = args.output_dir or settings.output_dir
    return asyncio.run(
        run_metadata(
            args.video_id,
            args.analysis_id,
            args.candidate_id,
            output_dir=output_dir,
            output=args.output,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
