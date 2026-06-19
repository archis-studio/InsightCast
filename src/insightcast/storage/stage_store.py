from pathlib import Path

from insightcast.domain.stages import StageManifest


class StageStore:
    def read(self, path: Path) -> StageManifest:
        return StageManifest.model_validate_json(path.read_text(encoding="utf-8"))

    def read_optional(self, path: Path) -> StageManifest | None:
        if not path.is_file():
            return None
        return self.read(path)

    def write(self, path: Path, manifest: StageManifest) -> Path:
        resolved = path.expanduser().resolve()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        tmp = resolved.with_suffix(f"{resolved.suffix}.tmp")
        tmp.write_text(
            manifest.model_dump_json(indent=2, exclude_computed_fields=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        tmp.replace(resolved)
        return resolved
