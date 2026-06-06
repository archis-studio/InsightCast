import pytest

from insightcast.core.exceptions import InsightCastError
from insightcast.domain.enums import ErrorCode
from insightcast.utils.youtube import normalize_youtube_url


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=abc123DEF_-&list=ignored",
        "https://youtube.com/watch?v=abc123DEF_-",
        "https://youtu.be/abc123DEF_-?t=10",
        "https://www.youtube.com/embed/abc123DEF_-",
        "https://www.youtube.com/shorts/abc123DEF_-",
    ],
)
def test_normalize_youtube_url_returns_one_canonical_watch_url(url: str) -> None:
    assert normalize_youtube_url(url) == "https://www.youtube.com/watch?v=abc123DEF_-"


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/watch?v=abc123DEF_-",
        "https://youtube.com/watch",
        "https://youtu.be/too-short",
        "not a url",
    ],
)
def test_normalize_youtube_url_rejects_invalid_inputs(url: str) -> None:
    with pytest.raises(InsightCastError) as exc_info:
        normalize_youtube_url(url)

    assert exc_info.value.error_code == ErrorCode.INVALID_YOUTUBE_URL

