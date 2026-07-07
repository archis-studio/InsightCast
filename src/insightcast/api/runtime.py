import sys
from dataclasses import dataclass
from pathlib import Path

from openai import OpenAI

from insightcast.core.config import Settings
from insightcast.engines.clip_engine import ClipEngine
from insightcast.engines.curator_engine import CuratorEngine
from insightcast.engines.lingo_engine import LingoEngine
from insightcast.engines.publish_engine import PublishEngine
from insightcast.engines.source_engine import SourceEngine
from insightcast.infrastructure.ffmpeg_client import FfmpegClient
from insightcast.infrastructure.openai_client import StructuredOpenAIClient
from insightcast.infrastructure.transcription.local_whisper_client import LocalWhisperClient
from insightcast.infrastructure.transcription.openai_transcription_client import (
    OpenAITranscriptionClient,
)
from insightcast.infrastructure.ytdlp_client import YtDlpClient
from insightcast.services.job_service import JobService
from insightcast.storage.file_job_writer import FileJobWriter
from insightcast.storage.video_store import VideoStore
from insightcast.utils.ass import BilingualAssStyle


@dataclass(frozen=True)
class AppRuntime:
    service: JobService
    ffmpeg: FfmpegClient


def build_runtime(settings: Settings) -> AppRuntime:
    ffmpeg = FfmpegClient(
        ffmpeg_bin=settings.ffmpeg_bin,
        crf=settings.video_crf,
        preset=settings.video_x264_preset,
    )
    ytdlp = YtDlpClient(
        executable=str(Path(sys.executable).with_name("yt-dlp")),
        max_height=settings.video_max_height,
        js_runtime=settings.ytdlp_js_runtime,
    )
    sdk = OpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        timeout=settings.openai_timeout_seconds,
        max_retries=0,
    )
    structured = StructuredOpenAIClient(
        sdk,
        timeout_seconds=settings.openai_timeout_seconds,
        max_retries=settings.openai_max_retries,
        retry_sleep_seconds=settings.openai_retry_sleep_seconds,
    )
    if settings.transcription_provider == "local":
        transcription = LocalWhisperClient(
            model_size=settings.whisper_model_size,
            device=settings.whisper_device,
        )
    else:
        transcription = OpenAITranscriptionClient(
            sdk.audio.transcriptions,
            model=settings.openai_transcription_model,
            max_upload_mb=settings.openai_transcription_max_upload_mb,
            max_attempts=settings.openai_transcription_max_attempts,
            request_timeout_seconds=settings.openai_transcription_timeout_seconds,
            retry_sleep_seconds=settings.openai_transcription_retry_sleep_seconds,
        )
    writer = FileJobWriter()
    video_store = VideoStore(settings.output_dir, writer)
    source = SourceEngine(ytdlp=ytdlp, ffmpeg=ffmpeg, video_store=video_store)
    lingo = LingoEngine(
        client=structured,
        model=settings.effective_translation_model,
        batch_size=settings.effective_translation_batch_size,
    )
    service = JobService(
        output_root=settings.output_dir,
        work_root=settings.work_dir,
        source_engine=source,
        transcription_client=transcription,
        curator_engine=CuratorEngine(
            client=structured,
            model=settings.effective_curator_model,
            enable_selection_review=True,
        ),
        clip_engine=ClipEngine(
            ffmpeg=ffmpeg,
            lingo=lingo,
            subtitle_style=BilingualAssStyle(
                chinese_font_size=settings.subtitle_chinese_font_size,
                english_font_size=settings.subtitle_english_font_size,
            ),
        ),
        publish_engine=PublishEngine(
            client=structured,
            model=settings.effective_metadata_model,
            writer=writer,
        ),
        writer=writer,
    )
    return AppRuntime(service=service, ffmpeg=ffmpeg)
