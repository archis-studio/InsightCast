import subprocess
from pathlib import Path

import pytest

from insightcast.core.exceptions import InsightCastError
from insightcast.domain.enums import ErrorCode
from insightcast.infrastructure.ffmpeg_client import FfmpegClient


class RecordingRunner:
    def __init__(self, returncode: int = 0, stderr: str = "") -> None:
        self.returncode = returncode
        self.stderr = stderr
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        return subprocess.CompletedProcess(
            args=args,
            returncode=self.returncode,
            stdout="",
            stderr=self.stderr,
        )


@pytest.mark.asyncio
async def test_probe_checks_configured_ffmpeg_binary() -> None:
    runner = RecordingRunner()

    await FfmpegClient(ffmpeg_bin="/opt/bin/ffmpeg", runner=runner).probe()

    assert runner.calls == [["/opt/bin/ffmpeg", "-version"]]


@pytest.mark.asyncio
async def test_extract_audio_compresses_to_mono_mp3(tmp_path: Path) -> None:
    runner = RecordingRunner()
    client = FfmpegClient(runner=runner)
    source = tmp_path / "source.mp4"
    destination = tmp_path / "audio.mp3"

    await client.extract_audio(source, destination)

    command = runner.calls[0]
    assert command[:4] == ["ffmpeg", "-y", "-i", str(source.resolve())]
    assert command[4:-1] == ["-vn", "-ac", "1", "-ar", "16000", "-b:a", "64k"]
    assert command[-1] == str(destination.resolve())


@pytest.mark.asyncio
async def test_cut_clip_reencodes_precise_h264_aac_output(tmp_path: Path) -> None:
    runner = RecordingRunner()
    destination = tmp_path / "clip.mp4"

    await FfmpegClient(crf=20, preset="veryfast", runner=runner).cut_clip(
        tmp_path / "source.mp4",
        destination,
        start_seconds=12.5,
        end_seconds=25,
    )

    command = runner.calls[0]
    assert command[2:6] == ["-ss", "12.500", "-to", "25.000"]
    assert command[-9:-1] == [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-c:a",
        "aac",
    ]
    assert command[-1] == str(destination.resolve())


@pytest.mark.asyncio
async def test_burn_subtitles_uses_ass_filter_h264_and_aac(tmp_path: Path) -> None:
    runner = RecordingRunner()
    client = FfmpegClient(crf=18, preset="veryfast", runner=runner)
    ass_path = tmp_path / "captions.ass"

    await client.burn_subtitles(
        tmp_path / "clip.mp4",
        ass_path,
        tmp_path / "burned.mp4",
    )

    command = runner.calls[0]
    assert "-vf" in command
    assert command[command.index("-vf") + 1].startswith("ass=")
    assert str(ass_path.resolve()) in command[command.index("-vf") + 1]
    assert command[-9:-1] == [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-c:a",
        "aac",
    ]


@pytest.mark.asyncio
async def test_probe_failure_uses_ffmpeg_not_available_error() -> None:
    runner = RecordingRunner(returncode=127, stderr="not found")

    with pytest.raises(InsightCastError) as exc_info:
        await FfmpegClient(runner=runner).probe()

    assert exc_info.value.error_code == ErrorCode.FFMPEG_NOT_AVAILABLE


@pytest.mark.asyncio
async def test_render_failure_preserves_subprocess_details() -> None:
    runner = RecordingRunner(returncode=1, stderr="codec error")

    with pytest.raises(InsightCastError) as exc_info:
        await FfmpegClient(runner=runner).burn_subtitles(
            Path("clip.mp4"),
            Path("captions.ass"),
            Path("burned.mp4"),
        )

    assert exc_info.value.error_code == ErrorCode.VIDEO_RENDER_FAILED
    assert exc_info.value.details["returncode"] == 1
    assert "codec error" in exc_info.value.details["stderr"]
