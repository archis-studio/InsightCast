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


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("api_port", 0),
        ("api_port", 65536),
        ("video_crf", -1),
        ("video_crf", 52),
        ("video_max_height", 0),
        ("llm_model", ""),
    ],
)
def test_settings_reject_invalid_ranges_and_empty_models(field: str, value: object) -> None:
    values = {"openai_api_key": "sk-test-value", field: value}

    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)
