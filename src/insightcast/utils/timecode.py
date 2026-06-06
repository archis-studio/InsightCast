import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

_CLOCK_PATTERN = re.compile(
    r"^(?P<hours>\d{2,}):(?P<minutes>\d{2}):(?P<seconds>\d{2})(?:\.(?P<fraction>\d+))?$"
)


def _as_decimal(value: object) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("boolean is not a valid time value")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            raise ValueError("time value must not be empty")
        try:
            return Decimal(stripped)
        except InvalidOperation as exc:
            raise ValueError(f"invalid time value: {value}") from exc
    raise ValueError(f"unsupported time value: {value!r}")


def parse_timecode(value: object) -> float:
    if isinstance(value, str) and ":" in value:
        match = _CLOCK_PATTERN.fullmatch(value.strip())
        if match is None:
            raise ValueError(f"invalid timecode: {value}")
        hours = int(match.group("hours"))
        minutes = int(match.group("minutes"))
        seconds = int(match.group("seconds"))
        if minutes >= 60 or seconds >= 60:
            raise ValueError(f"invalid timecode: {value}")
        fraction = match.group("fraction") or "0"
        total = Decimal(hours * 3600 + minutes * 60 + seconds) + Decimal(f"0.{fraction}")
    else:
        total = _as_decimal(value)
    if not total.is_finite() or total < 0:
        raise ValueError("time value must be a finite non-negative number")
    return float(total)


def _rounded_units(seconds: float | Decimal, units_per_second: int) -> int:
    value = _as_decimal(seconds)
    if not value.is_finite() or value < 0:
        raise ValueError("time value must be a finite non-negative number")
    return int((value * units_per_second).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def format_srt_time(seconds: float | Decimal) -> str:
    total_milliseconds = _rounded_units(seconds, 1000)
    hours, remainder = divmod(total_milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},{milliseconds:03d}"


def format_ass_time(seconds: float | Decimal) -> str:
    total_centiseconds = _rounded_units(seconds, 100)
    hours, remainder = divmod(total_centiseconds, 360_000)
    minutes, remainder = divmod(remainder, 6000)
    whole_seconds, centiseconds = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{whole_seconds:02d}.{centiseconds:02d}"
