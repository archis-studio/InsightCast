from decimal import Decimal

import pytest

from insightcast.utils.timecode import format_ass_time, format_srt_time, parse_timecode


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (90, 90.0),
        (Decimal("90.25"), 90.25),
        ("90.25", 90.25),
        ("00:01:30", 90.0),
        ("01:02:03.500", 3723.5),
    ],
)
def test_parse_timecode_accepts_seconds_and_clock_values(value: object, expected: float) -> None:
    assert parse_timecode(value) == expected


@pytest.mark.parametrize("value", [-1, True, "", "00:70:00", "abc", "1:2"])
def test_parse_timecode_rejects_invalid_values(value: object) -> None:
    with pytest.raises(ValueError):
        parse_timecode(value)


def test_subtitle_time_formatters_round_milliseconds_deterministically() -> None:
    assert format_srt_time(3723.4567) == "01:02:03,457"
    assert format_ass_time(3723.4567) == "1:02:03.46"

