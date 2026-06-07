import asyncio
import fcntl
import hashlib
import json
import re
import shutil
import stat
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from io import BufferedRandom
from pathlib import Path
from typing import Literal, TypeVar
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, ValidationError

from insightcast.core.exceptions import InsightCastError
from insightcast.domain.enums import ErrorCode
from insightcast.domain.models import Transcript
from insightcast.infrastructure.transcription.base import (
    TranscriptionSpec,
    build_transcript_cache_key,
)
from insightcast.infrastructure.ytdlp_client import YouTubeMetadata
from insightcast.storage.file_job_writer import FileJobWriter
from insightcast.storage.manifests import (
    SCHEMA_VERSION,
    ManifestModel,
    ManifestState,
    SourceManifest,
    TranscriptManifest,
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


class SourceEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root: Path
    directory: Path
    source_video: Path
    source_audio: Path
    video_manifest: VideoManifest
    manifest: SourceManifest
    metadata: YouTubeMetadata


class SourceLookup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["hit", "miss", "repair"]
    entry: SourceEntry | None = None


class SourceListing(BaseModel):
    model_config = ConfigDict(extra="forbid")

    video_id: str
    title: str
    source_size: int
    audio_size: int
    modified_at: datetime


class TranscriptEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root: Path
    directory: Path
    transcript_path: Path
    manifest_path: Path
    manifest: TranscriptManifest
    transcript: Transcript


class SourceTransaction:
    def __init__(self, store: "VideoStore", video_id: str) -> None:
        self.store = store
        self.video_id = validate_youtube_video_id(video_id)
        self._lock_file: BufferedRandom | None = None

    async def __aenter__(self) -> "SourceTransaction":
        lock_file = await self._acquire_lock_cancellation_safe()
        self._lock_file = lock_file
        try:
            self.store._recover_source_backup_unlocked(self.video_id)
        except BaseException:
            self._lock_file = None
            await asyncio.to_thread(self.store._release_video_lock, lock_file)
            raise
        return self

    async def __aexit__(
        self,
        _exc_type: object,
        _exc: BaseException | None,
        _traceback: object,
    ) -> None:
        lock_file = self._lock_file
        self._lock_file = None
        if lock_file is not None:
            await asyncio.to_thread(self.store._release_video_lock, lock_file)

    def load_source(self) -> SourceLookup:
        self._require_active()
        self.store._recover_source_backup_unlocked(self.video_id)
        return self.store._load_source_unlocked(self.video_id)

    def ensure_video(
        self,
        metadata: YouTubeMetadata,
        original_url: str,
    ) -> VideoEntry:
        self._require_active()
        if metadata.video_id != self.video_id:
            raise self.store._invalid_source(
                self.video_id,
                "metadata_video_id_mismatch",
            )
        return self.store._ensure_video_unlocked(metadata, original_url)

    def create_staging(self) -> Path:
        self._require_active()
        return self.store._create_source_staging_unlocked(self.video_id)

    def promote(
        self,
        staging: Path,
        *,
        metadata: YouTubeMetadata,
        downloaded_at: datetime,
        audio_extracted_at: datetime,
    ) -> SourceEntry:
        self._require_active()
        return self.store._promote_source_unlocked(
            self.video_id,
            staging,
            metadata=metadata,
            downloaded_at=downloaded_at,
            audio_extracted_at=audio_extracted_at,
        )

    def discard_staging(self, staging: Path) -> None:
        self._require_active()
        self.store._discard_source_staging_unlocked(self.video_id, staging)

    def _require_active(self) -> None:
        if self._lock_file is None:
            raise RuntimeError("source transaction is not active")

    async def _acquire_lock_cancellation_safe(self) -> BufferedRandom:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[BufferedRandom] = loop.create_future()
        cancelled = threading.Event()

        def worker() -> None:
            lock_file: BufferedRandom | None = None
            try:
                lock_file = self.store._acquire_video_lock(self.video_id)
            except BaseException as exc:
                loop.call_soon_threadsafe(deliver_exception, exc)
                return
            loop.call_soon_threadsafe(deliver_lock, lock_file)

        def deliver_lock(lock_file: BufferedRandom) -> None:
            if cancelled.is_set() or future.cancelled():
                self.store._release_video_lock(lock_file)
                return
            future.set_result(lock_file)

        def deliver_exception(exc: BaseException) -> None:
            if cancelled.is_set() or future.cancelled():
                return
            future.set_exception(exc)

        thread = threading.Thread(
            target=worker,
            name=f"insightcast-source-lock-{self.video_id}",
            daemon=True,
        )
        thread.start()
        try:
            return await future
        except asyncio.CancelledError:
            cancelled.set()
            if future.done() and not future.cancelled():
                self.store._release_video_lock(future.result())
            future.cancel()
            raise


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
        return self._find_video_unlocked(validated_video_id)

    def _find_video_unlocked(self, video_id: str) -> VideoEntry | None:
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
        manifest_path = self._managed_manifest_path(root)
        manifest = self.read_manifest(manifest_path, VideoManifest)
        if manifest.video_id != video_id:
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
        with self._video_lock(video_id):
            return self._ensure_video_unlocked(metadata, original_url)

    def _ensure_video_unlocked(
        self,
        metadata: YouTubeMetadata,
        original_url: str,
    ) -> VideoEntry:
        video_id = validate_youtube_video_id(metadata.video_id)
        existing = self._find_video_unlocked(video_id)
        now = datetime.now(UTC)
        first_seen_at = now if existing is None else existing.manifest.first_seen_at
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
        if existing is not None:
            manifest_path = self._managed_manifest_path(existing.root)
            self.writer.write_json(manifest_path, manifest)
            return VideoEntry(root=existing.root, manifest=manifest)
        return self._create_video_root(video_id, metadata.title, manifest)

    def source_transaction(self, video_id: str) -> SourceTransaction:
        return SourceTransaction(self, video_id)

    def load_source(self, video_id: str) -> SourceLookup:
        validated_video_id = validate_youtube_video_id(video_id)
        with self._video_lock(validated_video_id):
            return self._load_source_unlocked(validated_video_id)

    def _load_source_unlocked(self, video_id: str) -> SourceLookup:
        self._recover_source_backup_unlocked(video_id)
        video = self._find_video_unlocked(video_id)
        if video is None:
            return SourceLookup(status="miss")
        if video.manifest.source_manifest_path != Path("source/manifest.json"):
            return SourceLookup(status="repair")
        source_dir = video.root / "source"
        if not source_dir.exists() and not source_dir.is_symlink():
            return SourceLookup(status="miss")
        entry = self._validate_source_directory(video, source_dir)
        if entry is None:
            return SourceLookup(status="repair")
        return SourceLookup(status="hit", entry=entry)

    def _create_source_staging_unlocked(self, video_id: str) -> Path:
        video = self._find_video_unlocked(video_id)
        if video is None:
            raise self._invalid_source(video_id, "video_missing")
        self._cleanup_stale_source_staging(video.root)
        staging = video.root / f".source-{uuid4().hex}.tmp"
        staging.mkdir()
        return staging.resolve()

    def _promote_source_unlocked(
        self,
        video_id: str,
        staging: Path,
        *,
        metadata: YouTubeMetadata,
        downloaded_at: datetime,
        audio_extracted_at: datetime,
    ) -> SourceEntry:
        if metadata.video_id != video_id:
            raise self._invalid_source(video_id, "metadata_video_id_mismatch")
        video = self._find_video_unlocked(video_id)
        if video is None:
            raise self._invalid_source(video_id, "video_missing")
        resolved_staging = self._managed_source_staging(video, staging)
        source_video = resolved_staging / "source.mp4"
        source_audio = resolved_staging / "audio.mp3"
        if not self._is_regular_non_symlink_file(source_video):
            raise self._invalid_source(video_id, "source_video_invalid")
        if not self._is_regular_non_symlink_file(source_audio):
            raise self._invalid_source(video_id, "source_audio_invalid")
        source_video_size = source_video.stat().st_size
        source_audio_size = source_audio.stat().st_size
        if source_video_size <= 0 or source_audio_size <= 0:
            raise self._invalid_source(video_id, "source_empty")
        manifest = SourceManifest(
            video_id=video_id,
            source_fingerprint=self._sha256_file(source_video),
            fingerprint_algorithm="sha256",
            source_video_path=Path("source/source.mp4"),
            source_video_size=source_video_size,
            transcription_audio_path=Path("source/audio.mp3"),
            transcription_audio_size=source_audio_size,
            downloaded_at=downloaded_at,
            audio_extracted_at=audio_extracted_at,
            source_metadata=metadata.model_dump(mode="json"),
            state=ManifestState.READY,
        )
        self.writer.write_json(resolved_staging / "manifest.json", manifest)
        if self._validate_source_directory(video, resolved_staging) is None:
            raise self._invalid_source(video_id, "staging_invalid")

        target = video.root / "source"
        backup = video.root / f".source-{uuid4().hex}.backup"
        moved_existing = False
        try:
            if target.exists() or target.is_symlink():
                target.replace(backup)
                moved_existing = True
            resolved_staging.replace(target)
            promoted = self._validate_source_directory(video, target)
            if promoted is None:
                raise self._invalid_source(video_id, "promoted_source_invalid")
        except BaseException:
            self._remove_managed_path(target)
            if moved_existing and backup.exists():
                backup.replace(target)
            raise
        else:
            self._remove_managed_path(backup)
            return promoted

    def _discard_source_staging_unlocked(
        self,
        video_id: str,
        staging: Path,
    ) -> None:
        video = self._find_video_unlocked(video_id)
        if video is None:
            return
        resolved_staging = self._managed_source_staging(video, staging)
        self._remove_managed_path(resolved_staging)

    def list_sources(self) -> list[SourceListing]:
        videos_root = self._validated_videos_root()
        if not videos_root.exists():
            return []
        listings: list[SourceListing] = []
        for root in sorted(videos_root.iterdir()):
            if root.name.startswith(".") or root.is_symlink() or not root.is_dir():
                continue
            try:
                manifest = self.read_manifest(root / "video.json", VideoManifest)
                lookup = self.load_source(manifest.video_id)
            except InsightCastError:
                continue
            if lookup.entry is None:
                continue
            entry = lookup.entry
            modified = max(
                path.stat().st_mtime
                for path in (
                    entry.source_video,
                    entry.source_audio,
                    entry.directory / "manifest.json",
                )
            )
            listings.append(
                SourceListing(
                    video_id=entry.manifest.video_id,
                    title=entry.video_manifest.title,
                    source_size=entry.manifest.source_video_size,
                    audio_size=entry.manifest.transcription_audio_size,
                    modified_at=datetime.fromtimestamp(modified, UTC),
                )
            )
        return listings

    def remove_source(self, video_id: str) -> bool:
        validated_video_id = validate_youtube_video_id(video_id)
        with self._video_lock(validated_video_id):
            video = self._find_video_unlocked(validated_video_id)
            if video is None:
                return False
            target = video.root / "source"
            if not target.exists() and not target.is_symlink():
                return False
            self._remove_managed_path(target)
            return True

    def find_ready_transcript(
        self,
        video_id: str,
        spec_or_cache_key: TranscriptionSpec | str,
    ) -> TranscriptEntry | None:
        validated_video_id = validate_youtube_video_id(video_id)
        cache_key = self._transcript_cache_key(spec_or_cache_key)
        with self._video_lock(validated_video_id):
            video = self._find_video_unlocked(validated_video_id)
            if video is None:
                return None
            for entry in self._list_transcripts_unlocked(video):
                if (
                    entry.manifest.state is ManifestState.READY
                    and entry.manifest.cache_key.lower() == cache_key.lower()
                ):
                    return entry
        return None

    def write_transcript(
        self,
        video_id: str,
        spec: TranscriptionSpec,
        transcript: Transcript,
        *,
        created_at: datetime | None = None,
    ) -> TranscriptEntry:
        validated_video_id = validate_youtube_video_id(video_id)
        cache_key = build_transcript_cache_key(spec)
        with self._video_lock(validated_video_id):
            video = self._find_video_unlocked(validated_video_id)
            if video is None:
                raise self._invalid_source(validated_video_id, "video_missing")
            transcripts_root = self._validated_transcripts_root(video, create=True)
            transcript_id = self._transcript_id_for_cache_key(transcripts_root, cache_key)
            target = transcripts_root / transcript_id
            staging = transcripts_root / f".{transcript_id}-{uuid4().hex}.tmp"
            manifest = TranscriptManifest(
                transcript_id=transcript_id,
                cache_key=cache_key,
                source_fingerprint=spec.source_fingerprint,
                provider=spec.provider,
                model=spec.model,
                language=spec.language,
                transcript_path=Path("transcripts") / transcript_id / "transcript.json",
                created_at=created_at or datetime.now(UTC),
                state=ManifestState.READY,
            )
            staging.mkdir()
            try:
                self.writer.write_json(staging / "transcript.json", transcript)
                self.writer.write_json(staging / "manifest.json", manifest)
                if target.exists() or target.is_symlink():
                    self._remove_managed_path(target)
                staging.replace(target)
                promoted = self._validate_transcript_directory(video, target)
                if promoted is None:
                    raise self._invalid_manifest(target / "manifest.json", "validation")
                return promoted
            except BaseException:
                self._remove_managed_path(staging)
                raise

    def list_transcripts(self, video_id: str) -> list[TranscriptEntry]:
        validated_video_id = validate_youtube_video_id(video_id)
        with self._video_lock(validated_video_id):
            video = self._find_video_unlocked(validated_video_id)
            if video is None:
                return []
            return self._list_transcripts_unlocked(video)

    def clear_sources(self) -> int:
        videos_root = self._validated_videos_root()
        if not videos_root.exists():
            return 0
        video_ids: list[str] = []
        for root in sorted(videos_root.iterdir()):
            if root.name.startswith(".") or root.is_symlink() or not root.is_dir():
                continue
            try:
                manifest = self.read_manifest(root / "video.json", VideoManifest)
            except InsightCastError:
                continue
            video_ids.append(manifest.video_id)
        return sum(self.remove_source(video_id) for video_id in video_ids)

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

    def _validate_source_directory(
        self,
        video: VideoEntry,
        directory: Path,
    ) -> SourceEntry | None:
        source_dir = directory.expanduser().absolute()
        if (
            source_dir.is_symlink()
            or not source_dir.is_dir()
            or source_dir.parent.resolve() != video.root
        ):
            return None
        manifest_path = source_dir / "manifest.json"
        try:
            manifest = self.read_manifest(manifest_path, SourceManifest)
        except InsightCastError:
            return None
        if (
            manifest.video_id != video.manifest.video_id
            or manifest.state is not ManifestState.READY
            or manifest.fingerprint_algorithm != "sha256"
            or manifest.source_video_path != Path("source/source.mp4")
            or manifest.transcription_audio_path != Path("source/audio.mp3")
        ):
            return None
        source_video = source_dir / "source.mp4"
        source_audio = source_dir / "audio.mp3"
        if not self._is_regular_non_symlink_file(
            source_video
        ) or not self._is_regular_non_symlink_file(source_audio):
            return None
        try:
            if (
                source_video.stat().st_size != manifest.source_video_size
                or source_audio.stat().st_size != manifest.transcription_audio_size
                or self._sha256_file(source_video).lower()
                != manifest.source_fingerprint.lower()
            ):
                return None
            metadata = YouTubeMetadata.model_validate(manifest.source_metadata)
        except (OSError, ValidationError, TypeError, ValueError):
            return None
        if metadata.video_id != video.manifest.video_id:
            return None
        return SourceEntry(
            root=video.root,
            directory=source_dir.resolve(),
            source_video=source_video.resolve(),
            source_audio=source_audio.resolve(),
            video_manifest=video.manifest,
            manifest=manifest,
            metadata=metadata,
        )

    def _list_transcripts_unlocked(self, video: VideoEntry) -> list[TranscriptEntry]:
        transcripts_root = self._validated_transcripts_root(video)
        if (
            not transcripts_root.exists()
        ):
            return []
        entries: list[TranscriptEntry] = []
        for child in sorted(transcripts_root.iterdir()):
            if child.name.startswith("."):
                continue
            entry = self._validate_transcript_directory(video, child)
            if entry is not None:
                entries.append(entry)
        return entries

    def _validate_transcript_directory(
        self,
        video: VideoEntry,
        directory: Path,
    ) -> TranscriptEntry | None:
        transcript_dir = directory.expanduser().absolute()
        transcripts_root = video.root / "transcripts"
        if (
            transcript_dir.is_symlink()
            or not transcript_dir.is_dir()
            or transcript_dir.parent.resolve() != transcripts_root.resolve()
        ):
            return None
        manifest_path = transcript_dir / "manifest.json"
        try:
            manifest = self.read_manifest(manifest_path, TranscriptManifest)
        except InsightCastError:
            return None
        if (
            manifest.state is not ManifestState.READY
            or manifest.transcript_id != transcript_dir.name
            or manifest.transcript_path
            != Path("transcripts") / manifest.transcript_id / "transcript.json"
        ):
            return None
        transcript_path = transcript_dir / "transcript.json"
        if not self._is_regular_non_symlink_file(transcript_path):
            return None
        try:
            transcript = Transcript.model_validate_json(
                transcript_path.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeDecodeError, ValidationError, TypeError, ValueError):
            return None
        if transcript.language != manifest.language:
            return None
        return TranscriptEntry(
            root=video.root,
            directory=transcript_dir.resolve(),
            transcript_path=transcript_path.resolve(),
            manifest_path=manifest_path.resolve(),
            manifest=manifest,
            transcript=transcript,
        )

    def _validated_transcripts_root(
        self,
        video: VideoEntry,
        *,
        create: bool = False,
    ) -> Path:
        transcripts_root = video.root / "transcripts"
        if transcripts_root.is_symlink():
            raise self._invalid_store_path(transcripts_root, "symlink")
        if create:
            transcripts_root.mkdir(exist_ok=True)
        if not transcripts_root.exists():
            return transcripts_root
        if not transcripts_root.is_dir():
            raise self._invalid_store_path(transcripts_root, "not_directory")
        resolved = transcripts_root.resolve()
        if video.root not in resolved.parents:
            raise self._invalid_store_path(transcripts_root, "outside_video_root")
        return resolved

    def _transcript_id_for_cache_key(
        self,
        transcripts_root: Path,
        cache_key: str,
    ) -> str:
        base = f"tx-{cache_key[:12]}"
        suffix = 0
        while True:
            transcript_id = base if suffix == 0 else f"{base}-{suffix}"
            candidate = transcripts_root / transcript_id
            if not candidate.exists() and not candidate.is_symlink():
                return transcript_id
            try:
                manifest = self.read_manifest(
                    candidate / "manifest.json",
                    TranscriptManifest,
                )
            except InsightCastError:
                suffix += 1
                continue
            if manifest.cache_key.lower() == cache_key.lower():
                return transcript_id
            suffix += 1

    @staticmethod
    def _transcript_cache_key(spec_or_cache_key: TranscriptionSpec | str) -> str:
        if isinstance(spec_or_cache_key, TranscriptionSpec):
            return build_transcript_cache_key(spec_or_cache_key)
        if re.fullmatch(r"[0-9A-Fa-f]{64}", spec_or_cache_key) is None:
            raise ValueError("transcript cache key must be a SHA-256 hex digest")
        return spec_or_cache_key

    def _managed_source_staging(
        self,
        video: VideoEntry,
        staging: Path,
    ) -> Path:
        candidate = staging.expanduser().absolute()
        if (
            candidate.is_symlink()
            or candidate.parent.resolve() != video.root
            or re.fullmatch(r"\.source-[0-9a-f]{32}\.tmp", candidate.name) is None
            or not candidate.is_dir()
        ):
            raise self._invalid_store_path(candidate, "invalid_source_staging")
        return candidate.resolve()

    @contextmanager
    def _video_lock(self, video_id: str) -> Iterator[None]:
        lock_file = self._acquire_video_lock(video_id)
        try:
            yield
        finally:
            self._release_video_lock(lock_file)

    def _acquire_video_lock(self, video_id: str) -> BufferedRandom:
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
        lock_file = lock_path.open("a+b")
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            self._validated_videos_root()
            self._cleanup_stale_staging(video_id)
        except BaseException:
            lock_file.close()
            raise
        return lock_file

    @staticmethod
    def _release_video_lock(lock_file: BufferedRandom) -> None:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        finally:
            lock_file.close()

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
    def _cleanup_stale_source_staging(video_root: Path) -> None:
        pattern = re.compile(r"^\.source-[0-9a-f]{32}\.tmp$")
        for child in video_root.iterdir():
            if (
                pattern.fullmatch(child.name)
                and not child.is_symlink()
                and child.is_dir()
            ):
                shutil.rmtree(child)

    def _recover_source_backup_unlocked(self, video_id: str) -> None:
        video = self._find_video_unlocked(video_id)
        if video is None:
            return
        source_dir = video.root / "source"
        backups = self._source_backups(video.root)
        if self._validate_source_directory(video, source_dir) is not None:
            for backup in backups:
                self._remove_managed_path(backup)
            return
        if source_dir.exists() or source_dir.is_symlink():
            return
        for backup in backups:
            if self._validate_source_directory(video, backup) is not None:
                backup.replace(source_dir)
                for leftover in self._source_backups(video.root):
                    self._remove_managed_path(leftover)
                return

    @staticmethod
    def _source_backups(video_root: Path) -> list[Path]:
        pattern = re.compile(r"^\.source-[0-9a-f]{32}\.backup$")
        return [
            child
            for child in sorted(video_root.iterdir())
            if (
                pattern.fullmatch(child.name)
                and not child.is_symlink()
                and child.is_dir()
            )
        ]

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    @staticmethod
    def _is_regular_non_symlink_file(path: Path) -> bool:
        try:
            return not path.is_symlink() and stat.S_ISREG(path.lstat().st_mode)
        except OSError:
            return False

    @staticmethod
    def _remove_managed_path(path: Path) -> None:
        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)
        elif path.is_dir():
            shutil.rmtree(path)

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

    @staticmethod
    def _invalid_source(video_id: str, reason: str) -> InsightCastError:
        return InsightCastError(
            ErrorCode.SOURCE_CACHE_INVALID,
            "Managed source is incomplete or invalid.",
            details={
                "video_id": video_id,
                "reason": reason,
            },
            stage="ingesting",
        )
