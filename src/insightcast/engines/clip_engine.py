from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from insightcast.domain.models import Candidate, TranscriptSegment
from insightcast.utils.ass import serialize_bilingual_ass
from insightcast.utils.srt import serialize_traditional_chinese_srt


class ClipArtifacts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    traditional_chinese_srt: Path
    bilingual_ass: Path
    burned_video: Path


class ClipEngine:
    def __init__(self, *, ffmpeg: Any, lingo: Any) -> None:
        self.ffmpeg = ffmpeg
        self.lingo = lingo

    async def render(
        self,
        *,
        source_video: Path,
        transcript_segments: list[TranscriptSegment],
        selection: Candidate,
        output_dir: Path,
        work_dir: Path,
    ) -> ClipArtifacts:
        resolved_output_dir = output_dir.expanduser().resolve()
        resolved_work_dir = work_dir.expanduser().resolve()
        resolved_output_dir.mkdir(parents=True, exist_ok=True)
        resolved_work_dir.mkdir(parents=True, exist_ok=True)

        temporary_clip = resolved_work_dir / "video.unburned.mp4"
        srt_path = resolved_output_dir / "subtitles.zh-TW.srt"
        ass_path = resolved_output_dir / "subtitles.bilingual.ass"
        burned_path = resolved_output_dir / "video.mp4"

        await self.ffmpeg.cut_clip(
            source_video,
            temporary_clip,
            start_seconds=selection.start_seconds,
            end_seconds=selection.end_seconds,
        )
        subtitle_items = await self.lingo.translate_clip(
            segments=transcript_segments,
            clip_start_seconds=selection.start_seconds,
            clip_end_seconds=selection.end_seconds,
        )
        srt_path.write_text(
            serialize_traditional_chinese_srt(subtitle_items),
            encoding="utf-8",
            newline="\n",
        )
        ass_path.write_text(
            serialize_bilingual_ass(subtitle_items, title=selection.suggested_title),
            encoding="utf-8",
            newline="\n",
        )
        await self.ffmpeg.burn_subtitles(temporary_clip, ass_path, burned_path)
        temporary_clip.unlink()
        return ClipArtifacts(
            traditional_chinese_srt=srt_path,
            bilingual_ass=ass_path,
            burned_video=burned_path,
        )
