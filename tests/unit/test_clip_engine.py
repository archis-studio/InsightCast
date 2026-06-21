from pathlib import Path

import pytest

from insightcast.domain.models import Candidate, TranscriptSegment
from insightcast.engines.clip_engine import ClipEngine
from insightcast.engines.lingo_engine import SubtitleItem
from insightcast.utils.ass import BilingualAssStyle


class FakeFfmpeg:
    def __init__(self, *, fail_burn: bool = False) -> None:
        self.fail_burn = fail_burn

    async def cut_clip(
        self,
        source: Path,
        destination: Path,
        *,
        start_seconds: float,
        end_seconds: float,
    ) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"temporary clip")
        return destination

    async def burn_subtitles(self, source: Path, ass_path: Path, destination: Path) -> Path:
        if self.fail_burn:
            raise RuntimeError("burn failed")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"burned")
        return destination


class FakeLingo:
    async def translate_clip(
        self,
        *,
        segments: list[TranscriptSegment],
        clip_start_seconds: float,
        clip_end_seconds: float,
    ) -> list[SubtitleItem]:
        return [
            SubtitleItem(
                segment_id="s1",
                start_seconds=0,
                end_seconds=2,
                english_text="Hello",
                traditional_chinese_text="哈囉",
            )
        ]


def candidate() -> Candidate:
    return Candidate(
        candidate_id="A",
        start_seconds=10,
        end_seconds=20,
        suggested_title="Useful idea",
        selection_reason="Complete",
        summary="Summary",
    )


@pytest.mark.asyncio
async def test_clip_engine_writes_assets_and_deletes_temporary_clip_on_success(
    tmp_path: Path,
) -> None:
    work_dir = tmp_path / ".work"
    output_dir = tmp_path / "renders" / "candidate-a"
    engine = ClipEngine(ffmpeg=FakeFfmpeg(), lingo=FakeLingo())

    artifacts = await engine.render(
        source_video=tmp_path / "source.mp4",
        transcript_segments=[
            TranscriptSegment(segment_id="s1", start_seconds=10, end_seconds=12, text="Hello")
        ],
        selection=candidate(),
        output_dir=output_dir,
        work_dir=work_dir,
    )

    assert artifacts.traditional_chinese_srt.name == "subtitles.zh-TW.srt"
    assert artifacts.bilingual_ass.name == "subtitles.bilingual.ass"
    assert artifacts.burned_video.name == "video.mp4"
    assert artifacts.traditional_chinese_srt.read_text(encoding="utf-8").endswith("哈囉\n")
    assert "Style: English" in artifacts.bilingual_ass.read_text(encoding="utf-8")
    assert artifacts.burned_video.read_bytes() == b"burned"
    assert not (work_dir / "video.unburned.mp4").exists()


@pytest.mark.asyncio
async def test_clip_engine_retains_temporary_clip_when_render_fails(tmp_path: Path) -> None:
    work_dir = tmp_path / ".work"
    engine = ClipEngine(ffmpeg=FakeFfmpeg(fail_burn=True), lingo=FakeLingo())

    with pytest.raises(RuntimeError, match="burn failed"):
        await engine.render(
            source_video=tmp_path / "source.mp4",
            transcript_segments=[],
            selection=candidate(),
            output_dir=tmp_path / "output",
            work_dir=work_dir,
        )

    assert (work_dir / "video.unburned.mp4").exists()


@pytest.mark.asyncio
async def test_clip_engine_exposes_individual_render_steps(tmp_path) -> None:
    ffmpeg = FakeFfmpeg()
    lingo = FakeLingo()
    engine = ClipEngine(ffmpeg=ffmpeg, lingo=lingo)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    work_dir = tmp_path / "work"
    output_dir = tmp_path / "render"
    candidate = Candidate(
        candidate_id="A",
        start_seconds=10,
        end_seconds=12,
        suggested_title="Title",
        selection_reason="Reason",
        summary="Summary",
    )

    temporary = await engine.cut_clip(source, candidate, work_dir)
    subtitles = await engine.translate_subtitles(
        [TranscriptSegment(segment_id="s1", start_seconds=10, end_seconds=12, text="Hello")],
        candidate,
    )
    srt, ass = engine.write_subtitles(subtitles, candidate, output_dir)
    burned = await engine.burn_subtitles(temporary, ass, output_dir)

    assert temporary == work_dir / "video.unburned.mp4"
    assert srt == output_dir / "subtitles.zh-TW.srt"
    assert ass == output_dir / "subtitles.bilingual.ass"
    assert burned == output_dir / "video.mp4"


def test_clip_engine_writes_configured_ass_font_sizes(tmp_path: Path) -> None:
    engine = ClipEngine(
        ffmpeg=FakeFfmpeg(),
        lingo=FakeLingo(),
        subtitle_style=BilingualAssStyle(chinese_font_size=84, english_font_size=68),
    )
    output_dir = tmp_path / "render"

    _, ass = engine.write_subtitles(
        [
            SubtitleItem(
                segment_id="s1",
                start_seconds=0,
                end_seconds=1,
                english_text="Hello",
                traditional_chinese_text="哈囉",
            )
        ],
        candidate(),
        output_dir,
    )

    ass_text = ass.read_text(encoding="utf-8")
    assert "Style: TraditionalChinese,PingFang TC,84," in ass_text
    assert "Style: English,Arial,68," in ass_text
