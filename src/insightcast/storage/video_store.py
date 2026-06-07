import fcntl
import json
import re
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
        self._validated_videos_root()

    def matching_video_roots(self, video_id: str) -> list[Path]:
        prefix = f"{validate_youtube_video_id(video_id)}_"
        resolved_videos_root = self._validated_videos_root()
        if not resolved_videos_root.exists():
            return []
        roots: list[Path] = []
        for path in resolved_videos_root.iterdir():
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
        manifest_path = self._managed_manifest_path(root)
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
                manifest_path = self._managed_manifest_path(existing.root)
                self.writer.write_json(manifest_path, manifest)
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
        manifest_path = path.expanduser().absolute()
        if manifest_path.is_symlink() or (
            manifest_path.exists() and not manifest_path.is_file()
        ):
            raise self._invalid_manifest(manifest_path, "not_regular_file")
        try:
            raw = manifest_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise self._invalid_manifest(manifest_path, "encoding") from exc
        except OSError as exc:
            raise self._invalid_manifest(manifest_path, "io") from exc

        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            raise self._invalid_manifest(manifest_path, "json") from exc

        if isinstance(payload, dict) and payload.get("schema_version") != SCHEMA_VERSION:
            raise self._invalid_manifest(manifest_path, "unsupported_schema")

        try:
            return model_type.model_validate_json(raw)
        except (ValidationError, TypeError, ValueError) as exc:
            raise self._invalid_manifest(manifest_path, "validation") from exc

    def _create_video_root(
        self,
        video_id: str,
        title: str,
        manifest: VideoManifest,
    ) -> VideoEntry:
        videos_root = self._validated_videos_root()
        root = videos_root / build_video_dir_name(video_id, title)
        staging = videos_root / f".{video_id}-{uuid4().hex}.tmp"
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
        videos_root = self._validated_videos_root(create=True)
        locks_root = videos_root / ".locks"
        if locks_root.is_symlink():
            raise self._invalid_store_path(locks_root, "symlink")
        if locks_root.exists() and not locks_root.is_dir():
            raise self._invalid_store_path(locks_root, "not_directory")
        locks_root.mkdir(exist_ok=True)
        if not locks_root.is_dir() or videos_root not in locks_root.resolve().parents:
            raise self._invalid_store_path(locks_root, "outside_videos_root")
        lock_path = locks_root / f"{video_id}.lock"
        if lock_path.is_symlink() or (lock_path.exists() and not lock_path.is_file()):
            raise self._invalid_store_path(lock_path, "not_regular_file")
        with lock_path.open("a+b") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                self._validated_videos_root()
                self._cleanup_stale_staging(video_id)
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _validated_videos_root(self, *, create: bool = False) -> Path:
        if create:
            self.output_root.mkdir(parents=True, exist_ok=True)
        if self.output_root.exists() and not self.output_root.is_dir():
            raise self._invalid_store_path(self.output_root, "not_directory")
        if self.videos_root.is_symlink():
            raise self._invalid_store_path(self.videos_root, "symlink")
        if create:
            self.videos_root.mkdir(exist_ok=True)
        if not self.videos_root.exists():
            return self.videos_root
        if not self.videos_root.is_dir():
            raise self._invalid_store_path(self.videos_root, "not_directory")
        resolved = self.videos_root.resolve()
        if self.output_root not in resolved.parents:
            raise self._invalid_store_path(self.videos_root, "outside_output_root")
        return resolved

    def _managed_manifest_path(self, root: Path) -> Path:
        videos_root = self._validated_videos_root()
        managed_root = root.expanduser().absolute()
        if (
            managed_root.is_symlink()
            or not managed_root.is_dir()
            or videos_root not in managed_root.resolve().parents
        ):
            raise self._invalid_store_path(managed_root, "invalid_video_root")
        manifest_path = managed_root / "video.json"
        if manifest_path.is_symlink() or (
            manifest_path.exists() and not manifest_path.is_file()
        ):
            raise self._invalid_manifest(manifest_path, "not_regular_file")
        return manifest_path

    def _cleanup_stale_staging(self, video_id: str) -> None:
        pattern = re.compile(rf"^\.{re.escape(video_id)}-[0-9a-f]{{32}}\.tmp$")
        for child in self.videos_root.iterdir():
            if (
                pattern.fullmatch(child.name)
                and not child.is_symlink()
                and child.is_dir()
            ):
                shutil.rmtree(child)

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

    @staticmethod
    def _invalid_store_path(path: Path, reason: str) -> InsightCastError:
        return InsightCastError(
            ErrorCode.ARTIFACT_PATH_INVALID,
            "Managed storage path is invalid.",
            details={
                "path": str(path.absolute()),
                "reason": reason,
            },
        )
