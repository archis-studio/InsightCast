import re
from urllib.parse import parse_qs, urlparse

from insightcast.core.exceptions import InsightCastError
from insightcast.domain.enums import ErrorCode

_VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")
_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com"}


def _invalid_url(url: str) -> InsightCastError:
    return InsightCastError(
        ErrorCode.INVALID_YOUTUBE_URL,
        "A valid YouTube video URL is required.",
        details={"youtube_url": url},
    )


def normalize_youtube_url(url: str) -> str:
    try:
        parsed = urlparse(url.strip())
    except (AttributeError, ValueError) as exc:
        raise _invalid_url(str(url)) from exc

    host = (parsed.hostname or "").lower()
    video_id: str | None = None
    if parsed.scheme in {"http", "https"} and host == "youtu.be":
        video_id = parsed.path.strip("/").split("/", 1)[0]
    elif parsed.scheme in {"http", "https"} and host in _YOUTUBE_HOSTS:
        if parsed.path.rstrip("/") == "/watch":
            video_id = parse_qs(parsed.query).get("v", [None])[0]
        else:
            parts = [part for part in parsed.path.split("/") if part]
            if len(parts) == 2 and parts[0] in {"embed", "shorts"}:
                video_id = parts[1]

    if video_id is None or _VIDEO_ID_PATTERN.fullmatch(video_id) is None:
        raise _invalid_url(url)
    return f"https://www.youtube.com/watch?v={video_id}"


def validate_youtube_video_id(video_id: str) -> str:
    if _VIDEO_ID_PATTERN.fullmatch(video_id) is None:
        raise InsightCastError(
            ErrorCode.INVALID_CACHE_TARGET,
            "A valid YouTube video ID is required.",
            details={"video_id": video_id},
        )
    return video_id


def extract_youtube_video_id(url: str) -> str:
    normalized = normalize_youtube_url(url)
    return parse_qs(urlparse(normalized).query)["v"][0]
