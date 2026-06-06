from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8765, ge=1, le=65535)
    output_dir: Path = Path("outputs")
    work_dir: Path = Path(".work")

    openai_api_key: str
    openai_base_url: str | None = None
    llm_model: str = "gpt-5.4-mini"
    curator_model: str | None = None
    translation_model: str | None = None
    metadata_model: str | None = None

    transcription_provider: Literal["openai", "local"] = "openai"
    openai_transcription_model: str = "whisper-1"
    openai_transcription_max_upload_mb: int = Field(default=24, ge=1, le=25)
    whisper_model_size: str = "large-v3"
    whisper_device: str = "auto"

    ffmpeg_bin: str = "ffmpeg"
    video_max_height: int = Field(default=1080, ge=1, le=4320)
    video_crf: int = Field(default=18, ge=0, le=51)
    openai_timeout_seconds: float = Field(default=120, gt=0)
    openai_max_retries: int = Field(default=2, ge=0, le=10)

    @field_validator("output_dir", "work_dir")
    @classmethod
    def resolve_path(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    @field_validator(
        "api_host",
        "llm_model",
        "openai_transcription_model",
        "whisper_model_size",
        "whisper_device",
        "ffmpeg_bin",
    )
    @classmethod
    def reject_empty_string(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("value must not be empty")
        return stripped

    @field_validator("openai_api_key")
    @classmethod
    def validate_openai_api_key(cls, value: str) -> str:
        stripped = value.strip()
        normalized = stripped.lower()
        placeholders = {
            "",
            "replace-me",
            "your-api-key",
            "your_openai_api_key",
            "sk-xxx",
        }
        if normalized in placeholders:
            raise ValueError("OPENAI_API_KEY is missing or is an obvious placeholder")
        return stripped

    @field_validator("openai_base_url", "curator_model", "translation_model", "metadata_model")
    @classmethod
    def blank_optional_strings_are_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @property
    def effective_curator_model(self) -> str:
        return self.curator_model or self.llm_model

    @property
    def effective_translation_model(self) -> str:
        return self.translation_model or self.llm_model

    @property
    def effective_metadata_model(self) -> str:
        return self.metadata_model or self.llm_model


@lru_cache
def get_settings() -> Settings:
    return Settings()

