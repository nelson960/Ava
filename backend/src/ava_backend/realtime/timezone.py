from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MAX_TIMEZONE_ID_CHARACTERS = 64


def validate_iana_timezone_id(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("timezoneId must be an IANA timezone identifier")
    if not value or value != value.strip() or len(value) > MAX_TIMEZONE_ID_CHARACTERS:
        raise ValueError("timezoneId must be an IANA timezone identifier")
    try:
        ZoneInfo(value)
    except (ValueError, ZoneInfoNotFoundError) as error:
        raise ValueError(
            "timezoneId must be a known IANA timezone identifier"
        ) from error
    return value
