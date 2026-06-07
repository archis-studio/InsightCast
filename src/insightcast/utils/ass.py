from collections.abc import Sequence

from insightcast.engines.lingo_engine import SubtitleItem
from insightcast.utils.timecode import format_ass_time


def _escape_ass_text(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("\r\n", "\\N")
        .replace("\r", "\\N")
        .replace("\n", "\\N")
    )


def serialize_bilingual_ass(items: Sequence[SubtitleItem], *, title: str) -> str:
    style_fields = (
        "Name",
        "Fontname",
        "Fontsize",
        "PrimaryColour",
        "SecondaryColour",
        "OutlineColour",
        "BackColour",
        "Bold",
        "Italic",
        "Underline",
        "StrikeOut",
        "ScaleX",
        "ScaleY",
        "Spacing",
        "Angle",
        "BorderStyle",
        "Outline",
        "Shadow",
        "Alignment",
        "MarginL",
        "MarginR",
        "MarginV",
        "Encoding",
    )
    chinese_style = (
        "TraditionalChinese",
        "PingFang TC",
        "46",
        "&H0082E0FF",
        "&H000000FF",
        "&H00101010",
        "&H80000000",
        "0",
        "0",
        "0",
        "0",
        "100",
        "100",
        "0",
        "0",
        "1",
        "3",
        "1",
        "2",
        "80",
        "80",
        "165",
        "1",
    )
    english_style = (
        "English",
        "Arial",
        "44",
        "&H00FFFFFF",
        "&H000000FF",
        "&H00101010",
        "&H80000000",
        "0",
        "0",
        "0",
        "0",
        "100",
        "100",
        "0",
        "0",
        "1",
        "3",
        "1",
        "2",
        "80",
        "80",
        "90",
        "1",
    )
    header_lines = [
        "[Script Info]",
        f"Title: {title}",
        "ScriptType: v4.00+",
        "PlayResX: 1920",
        "PlayResY: 1080",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        f"Format: {', '.join(style_fields)}",
        f"Style: {','.join(chinese_style)}",
        f"Style: {','.join(english_style)}",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    header = "\n".join(header_lines) + "\n"
    events: list[str] = []
    for item in items:
        start = format_ass_time(item.start_seconds)
        end = format_ass_time(item.end_seconds)
        events.append(
            "Dialogue: "
            f"1,{start},{end},TraditionalChinese,,0,0,0,,"
            f"{_escape_ass_text(item.traditional_chinese_text)}"
        )
        events.append(
            f"Dialogue: 0,{start},{end},English,,0,0,0,,{_escape_ass_text(item.english_text)}"
        )
    return header + "\n".join(events) + ("\n" if events else "")
