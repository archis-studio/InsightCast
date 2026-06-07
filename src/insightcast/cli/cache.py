import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from insightcast.core.exceptions import InsightCastError
from insightcast.storage.file_job_writer import FileJobWriter
from insightcast.storage.video_store import VideoStore


def _format_size(size: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1024
    raise AssertionError("unreachable")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cast_cache")
    parser.add_argument(
        "--output-dir",
        default=os.getenv("OUTPUT_DIR", "outputs"),
        help=argparse.SUPPRESS,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list", help="List validated source cache entries.")
    remove = subparsers.add_parser("remove", help="Remove one source cache entry.")
    remove.add_argument("video_id")
    clear = subparsers.add_parser("clear", help="Remove all source cache entries.")
    clear.add_argument("--yes", action="store_true", help="Confirm destructive cleanup.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    store = VideoStore(Path(args.output_dir), FileJobWriter())
    try:
        if args.command == "list":
            for entry in store.list_sources():
                print(
                    "\t".join(
                        (
                            entry.video_id,
                            entry.title,
                            _format_size(entry.source_size),
                            _format_size(entry.audio_size),
                            entry.modified_at.isoformat(),
                        )
                    )
                )
            return 0
        if args.command == "remove":
            if not store.remove_source(args.video_id):
                print(f"Cache entry not found: {args.video_id}", file=sys.stderr)
                return 1
            return 0
        if not args.yes:
            print("Refusing to clear source cache without --yes.", file=sys.stderr)
            return 2
        store.clear_sources()
        return 0
    except InsightCastError as exc:
        print(f"{exc.error_code.value}: {exc.message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
