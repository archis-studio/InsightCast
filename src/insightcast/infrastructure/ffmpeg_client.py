import asyncio
import json
import subprocess
from collections.abc import Callable
from pathlib import Path

from insightcast.core.exceptions import InsightCastError
from insightcast.domain.enums import ErrorCode

ProcessRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]


def _run_process(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=False)


class FfmpegClient:
    def __init__(
        self,
        *,
        ffmpeg_bin: str = "ffmpeg",
        crf: int = 18,
        preset: str = "veryfast",
        runner: ProcessRunner = _run_process,
    ) -> None:
        self.ffmpeg_bin = ffmpeg_bin
        self.crf = crf
        self.preset = preset
        self.runner = runner

    async def probe(self) -> None:
        await self._execute(
            [self.ffmpeg_bin, "-version"],
            error_code=ErrorCode.FFMPEG_NOT_AVAILABLE,
            message="FFmpeg is not available.",
            stage="startup",
        )

    async def media_profile(self, media_path: Path) -> dict[str, object]:
        source = media_path.expanduser().resolve()
        result = await self._execute(
            [
                self._ffprobe_bin(),
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(source),
            ],
            error_code=ErrorCode.VIDEO_RENDER_FAILED,
            message="FFprobe failed to inspect media.",
            stage="rendering",
        )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise InsightCastError(
                ErrorCode.VIDEO_RENDER_FAILED,
                "FFprobe returned invalid media metadata.",
                details={"reason": str(exc)},
                stage="rendering",
            ) from exc
        return _media_profile_from_ffprobe(payload)

    async def extract_audio(self, source_video: Path, destination: Path) -> Path:
        source = source_video.expanduser().resolve()
        output = self._prepare_destination(destination)
        await self._execute(
            [
                self.ffmpeg_bin,
                "-y",
                "-i",
                str(source),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-b:a",
                "64k",
                str(output),
            ],
            error_code=ErrorCode.AUDIO_EXTRACTION_FAILED,
            message="FFmpeg failed to extract audio.",
            stage="ingesting",
        )
        return output

    async def cut_clip(
        self,
        source_video: Path,
        destination: Path,
        *,
        start_seconds: float,
        end_seconds: float,
    ) -> Path:
        source = source_video.expanduser().resolve()
        output = self._prepare_destination(destination)
        await self._execute(
            [
                self.ffmpeg_bin,
                "-y",
                "-ss",
                f"{start_seconds:.3f}",
                "-to",
                f"{end_seconds:.3f}",
                "-i",
                str(source),
                "-c:v",
                "libx264",
                "-preset",
                self.preset,
                "-crf",
                str(self.crf),
                "-c:a",
                "aac",
                str(output),
            ],
            error_code=ErrorCode.VIDEO_RENDER_FAILED,
            message="FFmpeg failed to cut the selected clip.",
            stage="rendering",
        )
        return output

    async def burn_subtitles(
        self,
        source_clip: Path,
        ass_path: Path,
        destination: Path,
    ) -> Path:
        source = source_clip.expanduser().resolve()
        subtitles = ass_path.expanduser().resolve()
        output = self._prepare_destination(destination)
        await self._execute(
            [
                self.ffmpeg_bin,
                "-y",
                "-i",
                str(source),
                "-vf",
                f"ass={subtitles}",
                "-c:v",
                "libx264",
                "-preset",
                self.preset,
                "-crf",
                str(self.crf),
                "-c:a",
                "aac",
                str(output),
            ],
            error_code=ErrorCode.VIDEO_RENDER_FAILED,
            message="FFmpeg failed to burn bilingual subtitles.",
            stage="rendering",
        )
        return output

    @staticmethod
    def _prepare_destination(destination: Path) -> Path:
        output = destination.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        return output

    async def _execute(
        self,
        command: list[str],
        *,
        error_code: ErrorCode,
        message: str,
        stage: str,
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = await asyncio.to_thread(self.runner, command)
        except OSError as exc:
            raise InsightCastError(
                error_code,
                message,
                details={"reason": str(exc)},
                stage=stage,
            ) from exc
        if result.returncode != 0:
            raise InsightCastError(
                error_code,
                message,
                details={
                    "returncode": result.returncode,
                    "stderr": result.stderr[-4000:],
                },
                stage=stage,
            )
        return result

    def _ffprobe_bin(self) -> str:
        ffmpeg_path = Path(self.ffmpeg_bin)
        if ffmpeg_path.name == "ffmpeg":
            return str(ffmpeg_path.with_name("ffprobe"))
        return self.ffmpeg_bin


def _media_profile_from_ffprobe(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        return {}
    streams = payload.get("streams")
    video_stream: dict[str, object] | None = None
    if isinstance(streams, list):
        for stream in streams:
            if isinstance(stream, dict) and stream.get("codec_type") == "video":
                video_stream = stream
                break
    media_format = payload.get("format")
    if not isinstance(media_format, dict):
        media_format = {}
    profile: dict[str, object] = {}
    if video_stream is not None:
        _copy_string(profile, "codec", video_stream.get("codec_name"))
        _copy_int(profile, "width", video_stream.get("width"))
        _copy_int(profile, "height", video_stream.get("height"))
        _copy_string(profile, "pixel_format", video_stream.get("pix_fmt"))
        fps = _parse_frame_rate(video_stream.get("avg_frame_rate"))
        if fps is not None:
            profile["fps"] = fps
    duration = _parse_float(media_format.get("duration"))
    if duration is not None:
        profile["duration_seconds"] = duration
    bitrate = _parse_int(media_format.get("bit_rate"))
    if bitrate is not None:
        profile["bitrate"] = bitrate
    return profile


def _copy_string(destination: dict[str, object], key: str, value: object) -> None:
    if isinstance(value, str) and value:
        destination[key] = value


def _copy_int(destination: dict[str, object], key: str, value: object) -> None:
    parsed = _parse_int(value)
    if parsed is not None:
        destination[key] = parsed


def _parse_int(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _parse_float(value: object) -> float | None:
    if isinstance(value, int | float):
        return round(float(value), 3)
    if isinstance(value, str):
        try:
            return round(float(value), 3)
        except ValueError:
            return None
    return None


def _parse_frame_rate(value: object) -> float | None:
    if not isinstance(value, str) or not value or value == "0/0":
        return None
    if "/" not in value:
        return _parse_float(value)
    numerator, denominator = value.split("/", 1)
    try:
        denominator_float = float(denominator)
        if denominator_float == 0:
            return None
        return round(float(numerator) / denominator_float, 3)
    except ValueError:
        return None
