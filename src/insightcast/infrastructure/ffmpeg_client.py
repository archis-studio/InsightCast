import asyncio
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
