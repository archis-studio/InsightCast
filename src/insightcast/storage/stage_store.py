import os
import tempfile
from pathlib import Path

from pydantic import ValidationError

from insightcast.core.exceptions import InsightCastError
from insightcast.domain.enums import ErrorCode
from insightcast.domain.stages import StageManifest


class StageStore:
    def read(self, path: Path) -> StageManifest:
        manifest_path = self._manifest_path(path)
        self._reject_invalid_existing_manifest(manifest_path)
        try:
            raw = manifest_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise self._invalid_manifest(manifest_path, "encoding") from exc
        except OSError as exc:
            raise self._invalid_manifest(manifest_path, "io") from exc

        try:
            return StageManifest.model_validate_json(raw)
        except (ValidationError, TypeError, ValueError) as exc:
            raise self._invalid_manifest(manifest_path, "validation") from exc

    def read_optional(self, path: Path) -> StageManifest | None:
        manifest_path = self._manifest_path(path)
        if not manifest_path.exists() and not manifest_path.is_symlink():
            return None
        return self.read(manifest_path)

    def write(self, path: Path, manifest: StageManifest) -> Path:
        resolved = self._manifest_path(path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        self._reject_invalid_existing_manifest(resolved)
        serialized = manifest.model_dump_json(
            indent=2,
            exclude_computed_fields=True,
        )
        descriptor, temporary_name = tempfile.mkstemp(
            dir=resolved.parent,
            prefix=f".{resolved.name}.",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)
        try:
            output = os.fdopen(
                descriptor,
                "w",
                encoding="utf-8",
                newline="\n",
            )
            descriptor = -1
            with output:
                output.write(serialized)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            temporary_path.replace(resolved)
        except BaseException:
            if descriptor >= 0:
                os.close(descriptor)
            temporary_path.unlink(missing_ok=True)
            raise
        return resolved

    @staticmethod
    def _manifest_path(path: Path) -> Path:
        expanded = path.expanduser()
        return expanded.parent.resolve() / expanded.name

    @classmethod
    def _reject_invalid_existing_manifest(cls, path: Path) -> None:
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise cls._invalid_manifest(path, "not_regular_file")

    @staticmethod
    def _invalid_manifest(path: Path, reason: str) -> InsightCastError:
        return InsightCastError(
            ErrorCode.MANIFEST_INVALID,
            "Stage manifest could not be read or validated.",
            details={
                "manifest_path": str(path),
                "reason": reason,
            },
            stage="stage_manifest",
        )
