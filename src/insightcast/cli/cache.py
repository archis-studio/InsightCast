import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from insightcast.core.exceptions import InsightCastError
from insightcast.domain.enums import ErrorCode
from insightcast.storage.file_job_writer import FileJobWriter
from insightcast.storage.manifests import VideoManifest
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
    subparsers.add_parser("list", help="List managed videos and source readiness.")
    remove = subparsers.add_parser("remove", help="Remove one video's source files.")
    remove.add_argument("video_id")
    clear = subparsers.add_parser("clear", help="Remove source files for all videos.")
    clear.add_argument("--yes", action="store_true", help="Confirm destructive cleanup.")
    return parser


def _managed_video_ids(store: VideoStore) -> list[str]:
    videos_root = store.videos_root
    if not videos_root.exists():
        return []
    video_ids: list[str] = []
    for root in sorted(videos_root.iterdir()):
        if root.name.startswith("."):
            continue
        if root.is_symlink() or not root.is_dir():
            raise InsightCastError(
                ErrorCode.MANIFEST_INVALID,
                "Managed video root is not a regular directory.",
                details={"root": str(root.absolute())},
            )
        manifest = store.read_manifest(root / "video.json", VideoManifest)
        video = store.find_video(manifest.video_id)
        if video is None or video.root != root.resolve():
            raise InsightCastError(
                ErrorCode.STORAGE_CONFLICT,
                "Managed video manifest does not match its directory.",
                details={
                    "video_id": manifest.video_id,
                    "root": str(root.resolve()),
                },
            )
        video_ids.append(manifest.video_id)
    return video_ids


def _list_videos(store: VideoStore) -> None:
    for video_id in _managed_video_ids(store):
        video = store.find_video(video_id)
        assert video is not None
        lookup = store.load_source(video_id)
        entry = lookup.entry
        readiness = {
            "hit": "ready",
            "miss": "missing",
            "repair": "repair",
        }[lookup.status]
        print(
            "\t".join(
                (
                    video_id,
                    video.manifest.title,
                    readiness,
                    _format_size(entry.manifest.source_video_size if entry else 0),
                    _format_size(
                        entry.manifest.transcription_audio_size if entry else 0
                    ),
                    entry.manifest.source_fingerprint if entry else "-",
                )
            )
        )


def _print_error(exc: InsightCastError) -> None:
    print(f"{exc.error_code.value}: {exc.message}", file=sys.stderr)
    if exc.details:
        print(
            json.dumps(exc.details, indent=2, sort_keys=True, ensure_ascii=False),
            file=sys.stderr,
        )


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        store = VideoStore(Path(args.output_dir), FileJobWriter())
        if args.command == "list":
            _list_videos(store)
            return 0
        if args.command == "remove":
            if not store.remove_source(args.video_id):
                print(f"Source files not found: {args.video_id}", file=sys.stderr)
                return 1
            return 0
        if not args.yes:
            print("Refusing to clear source files without --yes.", file=sys.stderr)
            return 2
        video_ids = _managed_video_ids(store)
        for video_id in video_ids:
            store.remove_source(video_id)
        return 0
    except InsightCastError as exc:
        _print_error(exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
