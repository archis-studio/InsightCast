from datetime import UTC, datetime

from insightcast.utils.files import (
    build_analysis_job_dir_name,
    build_direct_job_dir_name,
    build_render_dir_name,
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
