from pathlib import Path

import pytest
from pydantic import ValidationError

from insightcast.core.config import Settings


def test_settings_resolve_paths_and_fall_back_to_default_model(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        openai_api_key="sk-test-value",
        output_dir=tmp_path / "outputs",
        work_dir=tmp_path / ".work",
        llm_model="gpt-test",
        curator_model="",
        translation_model=None,
        metadata_model="gpt-metadata",
    )

    assert settings.output_dir == (tmp_path / "outputs").resolve()
    assert settings.work_dir == (tmp_path / ".work").resolve()
    assert settings.effective_curator_model == "gpt-test"
    assert settings.effective_translation_model == "gpt-test"
    assert settings.effective_metadata_model == "gpt-metadata"


@pytest.mark.parametrize(
    "api_key",
    ["", "replace-me", "your-api-key", "sk-xxx", "sk-your-api-key-here"],
)
def test_openai_provider_rejects_missing_or_placeholder_key(api_key: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, openai_api_key=api_key)


def test_local_provider_is_supported_with_openai_key_for_text_engines() -> None:
    settings = Settings(
        _env_file=None,
        transcription_provider="local",
        openai_api_key="sk-test-value",
        llm_model="local-compatible-model",
    )

    assert settings.transcription_provider == "local"


def test_candidate_defaults_are_configurable() -> None:
    settings = Settings(
        _env_file=None,
        openai_api_key="sk-test-value",
        default_candidate_count=4,
        default_min_duration_minutes=6.5,
        default_max_duration_minutes=9,
    )

    assert settings.default_candidate_count == 4
    assert settings.default_min_duration_minutes == 6.5
    assert settings.default_max_duration_minutes == 9


def test_subtitle_font_sizes_are_configurable() -> None:
    settings = Settings(
        _env_file=None,
        openai_api_key="sk-test-value",
        subtitle_chinese_font_size=84,
        subtitle_english_font_size=68,
    )

    assert settings.subtitle_chinese_font_size == 84
    assert settings.subtitle_english_font_size == 68


def test_analysis_cli_settings_have_defaults() -> None:
    settings = Settings(_env_file=None, openai_api_key="sk-test-value")

    assert settings.api_base_url == "http://127.0.0.1:8765"
    assert settings.analyze_poll_interval_seconds == 30
    assert settings.ytdlp_js_runtime == "node"
    assert settings.openai_transcription_max_upload_mb == 8
    assert settings.openai_transcription_max_attempts == 3
    assert settings.openai_transcription_retry_sleep_seconds == 0
    assert settings.openai_retry_sleep_seconds == 10
    assert settings.subtitle_chinese_font_size == 72
    assert settings.subtitle_english_font_size == 60
    assert settings.subtitle_timing_normalization is True
    assert settings.subtitle_timing_offset_seconds == -0.12
    assert settings.subtitle_min_duration_seconds == 0.75
    assert settings.subtitle_max_extension_seconds == 0.30
    assert settings.subtitle_min_gap_seconds == 0.08
    assert settings.llm_capability_profile == "openai_strict"
    assert settings.effective_translation_batch_size == 24


def test_local_conservative_profile_uses_smaller_translation_batches() -> None:
    settings = Settings(
        _env_file=None,
        openai_api_key="sk-test-value",
        llm_capability_profile="local_conservative",
    )

    assert settings.effective_translation_batch_size == 12


def test_translation_batch_size_override_wins_over_profile() -> None:
    settings = Settings(
        _env_file=None,
        openai_api_key="sk-test-value",
        llm_capability_profile="local_conservative",
        translation_batch_size=18,
    )

    assert settings.effective_translation_batch_size == 18


def test_analysis_cli_settings_load_environment_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("API_BASE_URL", "https://api.example.test/base/")
    monkeypatch.setenv("ANALYZE_POLL_INTERVAL_SECONDS", "2.5")
    monkeypatch.setenv("YTDLP_JS_RUNTIME", "bun")
    monkeypatch.setenv("OPENAI_TRANSCRIPTION_MAX_ATTEMPTS", "5")
    monkeypatch.setenv("OPENAI_TRANSCRIPTION_RETRY_SLEEP_SECONDS", "1.5")
    monkeypatch.setenv("OPENAI_RETRY_SLEEP_SECONDS", "7.5")
    monkeypatch.setenv("SUBTITLE_CHINESE_FONT_SIZE", "88")
    monkeypatch.setenv("SUBTITLE_ENGLISH_FONT_SIZE", "70")
    monkeypatch.setenv("SUBTITLE_TIMING_NORMALIZATION", "false")
    monkeypatch.setenv("SUBTITLE_TIMING_OFFSET_SECONDS", "-0.2")
    monkeypatch.setenv("SUBTITLE_MIN_DURATION_SECONDS", "0.65")
    monkeypatch.setenv("SUBTITLE_MAX_EXTENSION_SECONDS", "0.2")
    monkeypatch.setenv("SUBTITLE_MIN_GAP_SECONDS", "0.1")

    settings = Settings(_env_file=None, openai_api_key="sk-test-value")

    assert settings.api_base_url == "https://api.example.test/base"
    assert settings.analyze_poll_interval_seconds == 2.5
    assert settings.ytdlp_js_runtime == "bun"
    assert settings.openai_transcription_max_attempts == 5
    assert settings.openai_transcription_retry_sleep_seconds == 1.5
    assert settings.openai_retry_sleep_seconds == 7.5
    assert settings.subtitle_chinese_font_size == 88
    assert settings.subtitle_english_font_size == 70
    assert settings.subtitle_timing_normalization is False
    assert settings.subtitle_timing_offset_seconds == -0.2
    assert settings.subtitle_min_duration_seconds == 0.65
    assert settings.subtitle_max_extension_seconds == 0.2
    assert settings.subtitle_min_gap_seconds == 0.1


def test_ytdlp_js_runtime_can_be_disabled() -> None:
    settings = Settings(_env_file=None, openai_api_key="sk-test-value", ytdlp_js_runtime="")

    assert settings.ytdlp_js_runtime is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("api_port", 0),
        ("api_port", 65536),
        ("video_crf", -1),
        ("video_crf", 52),
        ("video_x264_preset", ""),
        ("video_x264_preset", "fastest"),
        ("video_max_height", 0),
        ("llm_model", ""),
        ("default_candidate_count", 0),
        ("default_candidate_count", 27),
        ("default_min_duration_minutes", 0),
        ("default_max_duration_minutes", 0),
        ("analyze_poll_interval_seconds", 0),
        ("analyze_poll_interval_seconds", -1),
        ("openai_transcription_max_attempts", 0),
        ("openai_transcription_timeout_seconds", 0),
        ("openai_transcription_retry_sleep_seconds", -1),
        ("openai_retry_sleep_seconds", -1),
        ("subtitle_chinese_font_size", 0),
        ("subtitle_english_font_size", 0),
        ("subtitle_min_duration_seconds", -1),
        ("subtitle_max_extension_seconds", -1),
        ("subtitle_min_gap_seconds", -1),
        ("translation_batch_size", 0),
    ],
)
def test_settings_reject_invalid_ranges_and_empty_models(field: str, value: object) -> None:
    values = {"openai_api_key": "sk-test-value", field: value}

    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)


def test_settings_reject_candidate_default_duration_inversion() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            openai_api_key="sk-test-value",
            default_min_duration_minutes=12,
            default_max_duration_minutes=8,
        )


@pytest.mark.parametrize(
    "api_base_url",
    [
        "",
        "ftp://api.example.test",
        "http:///missing-host",
        "https://api.example.test/path?query=value",
        "https://api.example.test/path#fragment",
    ],
)
def test_settings_reject_invalid_api_base_url(api_base_url: str) -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            openai_api_key="sk-test-value",
            api_base_url=api_base_url,
        )
