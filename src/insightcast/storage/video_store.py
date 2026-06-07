import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar

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
        return sorted(
            path.resolve()
            for path in self.videos_root.iterdir()
            if path.is_dir() and path.name.startswith(prefix)
        )

    def find_video(self, video_id: str) -> VideoEntry | None:
        roots = self.matching_video_roots(video_id)
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
        return VideoEntry(
            root=root,
            manifest=self.read_manifest(root / "video.json", VideoManifest),
        )

    def ensure_video(
        self,
        metadata: YouTubeMetadata,
        original_url: str,
    ) -> VideoEntry:
        video_id = validate_youtube_video_id(metadata.video_id)
        existing = self.find_video(video_id)
        now = datetime.now(UTC)
        if existing is None:
            root = self.videos_root / build_video_dir_name(video_id, metadata.title)
            root.mkdir(parents=True)
            first_seen_at = now
        else:
            root = existing.root
            first_seen_at = existing.manifest.first_seen_at

        manifest = VideoManifest(
            video_id=video_id,
            original_youtube_url=original_url,
            normalized_youtube_url=normalize_youtube_url(original_url),
            title=metadata.title,
            uploader=metadata.uploader,
            upload_date=metadata.upload_date,
            first_seen_at=first_seen_at,
            last_seen_at=now,
            source_manifest_path=Path("source/manifest.json"),
        )
        self.writer.write_json(root / "video.json", manifest)
        return VideoEntry(root=root.resolve(), manifest=manifest)

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
