from collections.abc import Sequence

from insightcast.engines.lingo_engine import SubtitleItem
from insightcast.utils.timecode import format_srt_time


def serialize_traditional_chinese_srt(items: Sequence[SubtitleItem]) -> str:
    blocks = [
        (
            f"{index}\n"
            f"{format_srt_time(item.start_seconds)} --> {format_srt_time(item.end_seconds)}\n"
            f"{item.traditional_chinese_text.strip()}"
        )
        for index, item in enumerate(items, start=1)
    ]
    return "\n\n".join(blocks) + ("\n" if blocks else "")

