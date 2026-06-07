import re
import unicodedata
from datetime import UTC, datetime

from insightcast.utils.youtube import validate_youtube_video_id

_SEPARATOR_PATTERN = re.compile(r"[-\s]+")


def sanitize_filename(value: str, *, max_length: int = 80) -> str:
    if max_length < 1:
        raise ValueError("max_length must be positive")
    normalized = unicodedata.normalize("NFKC", value).strip().lower()
    characters = [
        character if character.isalnum() else "-"
        for character in normalized
        if character != "\x00"
    ]
    slug = _SEPARATOR_PATTERN.sub("-", "".join(characters)).strip("-")
    bounded = slug[:max_length].rstrip("-")
    return bounded or "untitled"


def _timestamp(value: datetime) -> str:
    return value.strftime("%Y%m%d-%H%M%S")


def build_analysis_job_dir_name(title: str, job_id: str, created_at: datetime) -> str:
    return f"{_timestamp(created_at)}_{sanitize_filename(title)}_{job_id[:6]}"


def build_direct_job_dir_name(title: str, job_id: str, created_at: datetime) -> str:
    return f"{_timestamp(created_at)}_{sanitize_filename(title)}_direct_{job_id[:6]}"


def build_render_dir_name(created_at: datetime, render_id: str) -> str:
    return f"{_timestamp(created_at)}-{render_id[:6]}"


def build_video_dir_name(video_id: str, title: str) -> str:
    return f"{validate_youtube_video_id(video_id)}_{sanitize_filename(title)}"


def build_run_id(created_at: datetime, unique_id: str) -> str:
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        raise ValueError("created_at must be timezone-aware")
    return f"{_timestamp(created_at.astimezone(UTC))}-{unique_id[:6]}"
