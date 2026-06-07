from datetime import UTC, datetime, timedelta, timezone

import pytest

from insightcast.utils.files import (
    build_analysis_job_dir_name,
    build_direct_job_dir_name,
    build_render_dir_name,
    build_run_id,
    build_video_dir_name,
    sanitize_filename,
)


def test_sanitize_filename_preserves_readable_unicode_and_removes_reserved_characters() -> None:
    assert sanitize_filename('  台灣 AI: "Future" / Podcast?  ') == "台灣-ai-future-podcast"


def test_sanitize_filename_has_fallback_and_length_bound() -> None:
    assert sanitize_filename("...") == "untitled"
    assert len(sanitize_filename("a" * 200, max_length=40)) == 40


def test_output_directory_names_include_timestamp_title_kind_and_short_id() -> None:
    now = datetime(2026, 6, 6, 14, 30, tzinfo=UTC)

    assert (
        build_analysis_job_dir_name("Video Title", "a1b2c3d4", now)
        == "20260606-143000_video-title_a1b2c3"
    )
    assert (
        build_direct_job_dir_name("Video Title", "d4e5f6a7", now)
        == "20260606-143000_video-title_direct_d4e5f6"
    )
    assert build_render_dir_name(now, "render123") == "20260606-143000-render"


def test_video_directory_name_uses_validated_id_and_sanitized_title() -> None:
    assert build_video_dir_name("abc123DEF_-", "A Useful / Talk") == "abc123DEF_-_a-useful-talk"


def test_run_id_uses_utc_timestamp_and_short_unique_id() -> None:
    created_at = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)

    assert build_run_id(created_at, "abcdef1234") == "20260607-120000-abcdef"


def test_run_id_normalizes_timezone_aware_datetime_to_utc() -> None:
    created_at = datetime(2026, 6, 7, 20, 0, tzinfo=timezone(timedelta(hours=8)))

    assert build_run_id(created_at, "abcdef1234") == "20260607-120000-abcdef"


def test_run_id_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        build_run_id(datetime(2026, 6, 7, 12, 0), "abcdef1234")
