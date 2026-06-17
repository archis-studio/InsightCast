from insightcast.engines.lingo_engine import SubtitleItem
from insightcast.utils.ass import serialize_bilingual_ass
from insightcast.utils.srt import serialize_traditional_chinese_srt


def test_serialize_traditional_chinese_srt_uses_utf8_compatible_text_and_timing() -> None:
    items = [
        SubtitleItem(
            segment_id="s1",
            start_seconds=0,
            end_seconds=2.25,
            english_text="Hello",
            traditional_chinese_text="哈囉",
        ),
        SubtitleItem(
            segment_id="s2",
            start_seconds=2.25,
            end_seconds=4,
            english_text="Taiwan",
            traditional_chinese_text="台灣",
        ),
    ]

    assert serialize_traditional_chinese_srt(items) == (
        "1\n00:00:00,000 --> 00:00:02,250\n哈囉\n\n"
        "2\n00:00:02,250 --> 00:00:04,000\n台灣\n"
    )


def test_serialize_bilingual_ass_has_styles_escaped_text_and_stable_events() -> None:
    items = [
        SubtitleItem(
            segment_id="s1",
            start_seconds=1,
            end_seconds=3.456,
            english_text=r"Use {braces}\nand newline",
            traditional_chinese_text="使用{括號}\n以及換行",
        )
    ]

    output = serialize_bilingual_ass(items, title="Video Title")

    assert "[Script Info]" in output
    assert "PlayResX: 1920" in output
    assert "Style: English" in output
    assert "Style: TraditionalChinese" in output
    assert output.index("Style: TraditionalChinese") < output.index("Style: English")
    chinese_style = next(
        line for line in output.splitlines() if line.startswith("Style: TraditionalChinese")
    )
    english_style = next(
        line for line in output.splitlines() if line.startswith("Style: English")
    )
    assert chinese_style.split(",")[2] == "54"
    assert english_style.split(",")[2] == "50"
    assert "&H0082E0FF" in chinese_style
    assert int(chinese_style.split(",")[-2]) > int(english_style.split(",")[-2])
    assert "Dialogue: 0,0:00:01.00,0:00:03.46,English" in output
    assert output.index(
        "Dialogue: 1,0:00:01.00,0:00:03.46,TraditionalChinese"
    ) < output.index("Dialogue: 0,0:00:01.00,0:00:03.46,English")
    assert r"Use \{braces\}\\nand newline" in output
    assert r"使用\{括號\}\N以及換行" in output
