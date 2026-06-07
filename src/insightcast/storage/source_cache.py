import json
import shutil
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, ValidationError

from insightcast.core.exceptions import InsightCastError
from insightcast.domain.enums import ErrorCode
from insightcast.infrastructure.ytdlp_client import YouTubeMetadata
from insightcast.utils.youtube import validate_youtube_video_id


class SourceCacheEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    directory: Path
    source_video: Path
    source_audio: Path
    metadata_path: Path
    metadata: YouTubeMetadata


class SourceCacheListing(BaseModel):
    model_config = ConfigDict(extra="forbid")

    video_id: str
    title: str
    source_size: int
    audio_size: int
    modified_at: datetime


class SourceCache:
    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()

    def entry_dir(self, video_id: str) -> Path:
        validated = validate_youtube_video_id(video_id)
        return self._contained(self.root / validated)

    def load(self, video_id: str) -> SourceCacheEntry | None:
        return self._load_from_directory(video_id, self.entry_dir(video_id))

    def create_staging(self, video_id: str) -> Path:
        validated = validate_youtube_video_id(video_id)
        self.root.mkdir(parents=True, exist_ok=True)
        staging = self.root / f".{validated}-{uuid4().hex}.tmp"
        staging.mkdir()
        return staging.resolve()

    def write_metadata(self, directory: Path, metadata: YouTubeMetadata) -> Path:
        resolved = self._contained(directory)
        destination = resolved / "metadata.json"
        temporary = destination.with_suffix(".json.tmp")
        payload = metadata.model_dump(mode="json")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(destination)
        return destination.resolve()

    def promote(self, video_id: str, staging: Path) -> SourceCacheEntry:
        target = self.entry_dir(video_id)
        resolved_staging = self._contained(staging)
        staged_entry = self._load_from_directory(video_id, resolved_staging)
        if staged_entry is None:
            raise InsightCastError(
                ErrorCode.SOURCE_CACHE_INVALID,
                "Source cache staging entry is incomplete or invalid.",
                details={"video_id": video_id},
                stage="ingesting",
            )

        backup = self.root / f".{video_id}-{uuid4().hex}.backup"
        moved_existing = False
        try:
            if target.exists():
                target.replace(backup)
                moved_existing = True
            resolved_staging.replace(target)
        except Exception:
            if moved_existing and backup.exists() and not target.exists():
                backup.replace(target)
            raise
        else:
            if backup.exists():
                shutil.rmtree(backup)

        entry = self.load(video_id)
        if entry is None:
            raise InsightCastError(
                ErrorCode.SOURCE_CACHE_INVALID,
                "Promoted source cache entry could not be validated.",
                details={"video_id": video_id},
                stage="ingesting",
            )
        return entry

    def discard_staging(self, staging: Path) -> None:
        resolved = self._contained(staging)
        if resolved.exists() and resolved.name.startswith(".") and resolved.name.endswith(".tmp"):
            shutil.rmtree(resolved)

    def list_entries(self) -> list[SourceCacheListing]:
        if not self.root.exists():
            return []
        listings: list[SourceCacheListing] = []
        for directory in sorted(self.root.iterdir()):
            if not directory.is_dir() or directory.name.startswith("."):
                continue
            try:
                entry = self.load(directory.name)
            except InsightCastError:
                continue
            if entry is None:
                continue
            modified = max(
                path.stat().st_mtime
                for path in (entry.source_video, entry.source_audio, entry.metadata_path)
            )
            listings.append(
                SourceCacheListing(
                    video_id=entry.metadata.video_id,
                    title=entry.metadata.title,
                    source_size=entry.source_video.stat().st_size,
                    audio_size=entry.source_audio.stat().st_size,
                    modified_at=datetime.fromtimestamp(modified).astimezone(),
                )
            )
        return listings

    def remove(self, video_id: str) -> bool:
        target = self.entry_dir(video_id)
        if self.load(video_id) is None:
            return False
        shutil.rmtree(target)
        return True

    def clear(self) -> int:
        if not self.root.exists():
            return 0
        removed = 0
        for child in list(self.root.iterdir()):
            target = self._contained(child)
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
            removed += 1
        return removed

    def _load_from_directory(
        self,
        video_id: str,
        directory: Path,
    ) -> SourceCacheEntry | None:
        if not directory.is_dir():
            return None
        source_video = directory / "source.mp4"
        source_audio = directory / "audio.mp3"
        metadata_path = directory / "metadata.json"
        if not self._is_non_empty_file(source_video) or not self._is_non_empty_file(
            source_audio
        ):
            return None
        try:
            metadata = YouTubeMetadata.model_validate_json(
                metadata_path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError, ValueError):
            return None
        if metadata.video_id != video_id:
            return None
        return SourceCacheEntry(
            directory=directory.resolve(),
            source_video=source_video.resolve(),
            source_audio=source_audio.resolve(),
            metadata_path=metadata_path.resolve(),
            metadata=metadata,
        )

    def _contained(self, path: Path) -> Path:
        resolved = path.expanduser().resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise InsightCastError(
                ErrorCode.INVALID_CACHE_TARGET,
                "Cache operation must remain inside the source cache root.",
                details={"cache_root": str(self.root)},
            )
        return resolved

    @staticmethod
    def _is_non_empty_file(path: Path) -> bool:
        try:
            return path.is_file() and path.stat().st_size > 0
        except OSError:
            return False
