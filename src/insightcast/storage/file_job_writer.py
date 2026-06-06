import json
import os
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from insightcast.domain.models import BaseJob


def _json_default(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


class FileJobWriter:
    def write_job(self, job: BaseJob) -> Path:
        return self.write_json(job.output_dir / "job_state.json", job)

    def write_json(self, path: Path, payload: Any) -> Path:
        resolved_path = path.expanduser().resolve()
        resolved_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = resolved_path.with_suffix(f"{resolved_path.suffix}.tmp")
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            default=_json_default,
        )
        with temporary_path.open("w", encoding="utf-8", newline="\n") as output:
            output.write(serialized)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        temporary_path.replace(resolved_path)
        return resolved_path

