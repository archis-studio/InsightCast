import json
import os
import tempfile
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
        expanded_path = path.expanduser()
        resolved_path = expanded_path.parent.resolve() / expanded_path.name
        resolved_path.parent.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            default=_json_default,
        )
        descriptor, temporary_name = tempfile.mkstemp(
            dir=resolved_path.parent,
            prefix=f".{resolved_path.name}.",
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
            temporary_path.replace(resolved_path)
        except BaseException:
            if descriptor >= 0:
                os.close(descriptor)
            temporary_path.unlink(missing_ok=True)
            raise
        return resolved_path
