import fcntl
import json
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, ValidationError

from insightcast.core.exceptions import InsightCastError
from insightcast.domain.enums import ErrorCode
from insightcast.infrastructure.ytdlp_client import YouTubeMetadata
from insightcast.storage.file_job_writer import FileJobWriter
from insightcast.storage.manifests import (
    SCHEMA_VERSION,
    ManifestModel,
    VideoManifest,
    validate_relative_path,
)
from insightcast.utils.files import build_video_dir_name
from insightcast.utils.youtube import normalize_youtube_url, validate_youtube_video_id

ManifestType = TypeVar("ManifestType", bound=ManifestModel)


class VideoEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root: Path
    manifest: VideoManifest


class VideoStore:
    def __init__(self, output_root: Path, writer: FileJobWriter) -> None:
        self.output_root = output_root.expanduser().resolve()
        self.videos_root = self.output_root / "videos"
        self.writer = writer

    def matching_video_roots(self, video_id: str) -> list[Path]:
        prefix = f"{validate_youtube_video_id(video_id)}_"
        if not self.videos_root.exists():
            return []
        resolved_videos_root = self.videos_root.resolve()
        roots: list[Path] = []
        for path in self.videos_root.iterdir():
            if (
                not path.name.startswith(prefix)
                or path.is_symlink()
                or not path.is_dir()
            ):
                continue
            resolved = path.resolve()
            if resolved_videos_root not in resolved.parents:
                continue
            roots.append(resolved)
        return sorted(roots)

    def find_video(self, video_id: str) -> VideoEntry | None:
        validated_video_id = validate_youtube_video_id(video_id)
        roots = self.matching_video_roots(validated_video_id)
        if not roots:
            return None
        if len(roots) > 1:
            raise InsightCastError(
                ErrorCode.STORAGE_CONFLICT,
                "Multiple managed roots exist for the same video.",
                details={
                    "video_id": video_id,
                    "roots": [str(root) for root in roots],
                },
            )
        root = roots[0]
        manifest_path = root / "video.json"
        manifest = self.read_manifest(manifest_path, VideoManifest)
        if manifest.video_id != validated_video_id:
            raise self._invalid_manifest(manifest_path, "video_id_mismatch")
        return VideoEntry(
            root=root,
            manifest=manifest,
        )

    def ensure_video(
        self,
        metadata: YouTubeMetadata,
        original_url: str,
    ) -> VideoEntry:
        video_id = validate_youtube_video_id(metadata.video_id)
        normalized_url = normalize_youtube_url(original_url)
        with self._video_lock(video_id):
            existing = self.find_video(video_id)
            now = datetime.now(UTC)
            first_seen_at = (
                now if existing is None else existing.manifest.first_seen_at
            )
            manifest = VideoManifest(
                video_id=video_id,
                original_youtube_url=original_url,
                normalized_youtube_url=normalized_url,
                title=metadata.title,
                uploader=metadata.uploader,
                upload_date=metadata.upload_date,
                first_seen_at=first_seen_at,
                last_seen_at=now,
                source_manifest_path=Path("source/manifest.json"),
            )
            if existing is not None:
                self.writer.write_json(existing.root / "video.json", manifest)
                return VideoEntry(root=existing.root, manifest=manifest)
            return self._create_video_root(video_id, metadata.title, manifest)

    def resolve_relative(self, owner: Path, relative: Path) -> Path:
        try:
            validated = validate_relative_path(relative)
        except (TypeError, ValueError) as exc:
            raise self._invalid_artifact(relative) from exc

        resolved_owner = owner.expanduser().resolve()
        resolved = (resolved_owner / validated).resolve()
        if resolved != resolved_owner and resolved_owner not in resolved.parents:
            raise self._invalid_artifact(relative)
        return resolved

    def read_manifest(
        self,
        path: Path,
        model_type: type[ManifestType],
    ) -> ManifestType:
        resolved = path.expanduser().resolve()
        try:
            raw = resolved.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise self._invalid_manifest(resolved, "encoding") from exc
        except OSError as exc:
            raise self._invalid_manifest(resolved, "io") from exc

        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            raise self._invalid_manifest(resolved, "json") from exc

        if isinstance(payload, dict) and payload.get("schema_version") != SCHEMA_VERSION:
            raise self._invalid_manifest(resolved, "unsupported_schema")

        try:
            return model_type.model_validate_json(raw)
        except (ValidationError, TypeError, ValueError) as exc:
            raise self._invalid_manifest(resolved, "validation") from exc

    def _create_video_root(
        self,
        video_id: str,
        title: str,
        manifest: VideoManifest,
    ) -> VideoEntry:
        root = self.videos_root / build_video_dir_name(video_id, title)
        staging = self.videos_root / f".{video_id}-{uuid4().hex}.tmp"
        staging.mkdir()
        try:
            self.writer.write_json(staging / "video.json", manifest)
            staging.replace(root)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return VideoEntry(root=root.resolve(), manifest=manifest)

    @contextmanager
    def _video_lock(self, video_id: str) -> Iterator[None]:
        self.videos_root.mkdir(parents=True, exist_ok=True)
        locks_root = self.videos_root / ".locks"
        locks_root.mkdir(exist_ok=True)
        lock_path = locks_root / f"{video_id}.lock"
        with lock_path.open("a+b") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _invalid_artifact(relative: Path) -> InsightCastError:
        return InsightCastError(
            ErrorCode.ARTIFACT_PATH_INVALID,
            "Artifact path must be a contained relative path.",
            details={"relative_path": str(relative)},
        )

    @staticmethod
    def _invalid_manifest(path: Path, reason: str) -> InsightCastError:
        return InsightCastError(
            ErrorCode.MANIFEST_INVALID,
            "Manifest could not be read or validated.",
            details={
                "manifest_path": str(path),
                "reason": reason,
            },
        )
