from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
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
    api_base_url: str = "http://127.0.0.1:8765"
    analyze_poll_interval_seconds: float = Field(default=30, gt=0)
    output_dir: Path = Path("outputs")
    work_dir: Path = Path(".work")

    openai_api_key: str
    openai_base_url: str | None = None
    llm_model: str = "gpt-5.4-mini"
    curator_model: str | None = None
    translation_model: str | None = None
    metadata_model: str | None = None
    llm_capability_profile: Literal["openai_strict", "local_conservative"] = (
        "openai_strict"
    )
    translation_batch_size: int | None = Field(default=None, ge=1, le=100)

    transcription_provider: Literal["openai", "local"] = "openai"
    openai_transcription_model: str = "whisper-1"
    openai_transcription_max_upload_mb: int = Field(default=8, ge=1, le=25)
    openai_transcription_max_attempts: int = Field(default=3, ge=1, le=10)
    openai_transcription_timeout_seconds: float = Field(default=240, gt=0)
    openai_transcription_retry_sleep_seconds: float = Field(default=0, ge=0)
    whisper_model_size: str = "large-v3"
    whisper_device: str = "auto"

    ffmpeg_bin: str = "ffmpeg"
    ytdlp_js_runtime: str | None = "node"
    video_max_height: int = Field(default=1080, ge=1, le=4320)
    video_crf: int = Field(default=18, ge=0, le=51)
    video_x264_preset: Literal[
        "ultrafast",
        "superfast",
        "veryfast",
        "faster",
        "fast",
        "medium",
        "slow",
        "slower",
        "veryslow",
        "placebo",
    ] = "veryfast"
    subtitle_chinese_font_size: int = Field(default=72, ge=1, le=300)
    subtitle_english_font_size: int = Field(default=60, ge=1, le=300)
    subtitle_timing_normalization: bool = True
    subtitle_timing_offset_seconds: float = -0.12
    subtitle_min_duration_seconds: float = Field(default=0.75, ge=0)
    subtitle_max_extension_seconds: float = Field(default=0.30, ge=0)
    subtitle_min_gap_seconds: float = Field(default=0.08, ge=0)
    openai_timeout_seconds: float = Field(default=120, gt=0)
    openai_max_retries: int = Field(default=2, ge=0, le=10)
    openai_retry_sleep_seconds: float = Field(default=10, ge=0)
    default_candidate_count: int = Field(default=2, ge=1, le=26)
    default_min_duration_minutes: float = Field(default=8, gt=0)
    default_max_duration_minutes: float = Field(default=12, gt=0)

    @model_validator(mode="after")
    def validate_candidate_duration_defaults(self) -> "Settings":
        if self.default_max_duration_minutes < self.default_min_duration_minutes:
            raise ValueError(
                "DEFAULT_MAX_DURATION_MINUTES must be at least "
                "DEFAULT_MIN_DURATION_MINUTES"
            )
        return self

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
        "video_x264_preset",
    )
    @classmethod
    def reject_empty_string(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("value must not be empty")
        return stripped

    @field_validator("api_base_url")
    @classmethod
    def validate_api_base_url(cls, value: str) -> str:
        stripped = value.strip().rstrip("/")
        parsed = urlsplit(stripped)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("API_BASE_URL must be a non-empty HTTP or HTTPS URL")
        return stripped

    @field_validator("openai_api_key")
    @classmethod
    def validate_openai_api_key(cls, value: str) -> str:
        stripped = value.strip()
        normalized = stripped.lower()
        placeholder_markers = (
            "replace-me",
            "your-api-key",
            "your_openai_api_key",
            "sk-xxx",
            "placeholder",
        )
        if not normalized or any(marker in normalized for marker in placeholder_markers):
            raise ValueError("OPENAI_API_KEY is missing or is an obvious placeholder")
        return stripped

    @field_validator(
        "openai_base_url",
        "curator_model",
        "translation_model",
        "metadata_model",
        "ytdlp_js_runtime",
    )
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

    @property
    def effective_translation_batch_size(self) -> int:
        if self.translation_batch_size is not None:
            return self.translation_batch_size
        if self.llm_capability_profile == "local_conservative":
            return 12
        return 24


@lru_cache
def get_settings() -> Settings:
    return Settings()
