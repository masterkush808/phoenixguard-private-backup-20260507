from __future__ import annotations

from datetime import datetime
from time import tzname
from typing import Any

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except Exception:  # pragma: no cover - Python always provides this on 3.11+
    ZoneInfo = None  # type: ignore[assignment]
    ZoneInfoNotFoundError = Exception  # type: ignore[assignment]


def default_timezone_name() -> str:
    try:
        local_zone = datetime.now().astimezone().tzinfo
        if local_zone is not None:
            key = getattr(local_zone, "key", "")
            if key:
                return str(key)
            label = str(local_zone)
            if label and label.upper() != "UTC":
                return label
    except Exception:
        pass
    for label in tzname:
        normalized = str(label or "").strip()
        if normalized:
            return normalized
    return "UTC"


def _resolve_tzinfo(timezone_name: str | None) -> Any:
    normalized = str(timezone_name or "").strip()
    if not normalized:
        return datetime.now().astimezone().tzinfo
    if ZoneInfo is None:
        return datetime.now().astimezone().tzinfo
    try:
        return ZoneInfo(normalized)
    except ZoneInfoNotFoundError:
        return datetime.now().astimezone().tzinfo


def local_now(timezone_name: str | None = None) -> datetime:
    tzinfo = _resolve_tzinfo(timezone_name)
    return datetime.now(tz=tzinfo)


def part_of_day(moment: datetime) -> str:
    hour = int(moment.hour)
    if hour < 12:
        return "morning"
    if hour < 17:
        return "afternoon"
    return "evening"


def greeting_for_time(
    *,
    timezone_name: str | None = None,
    target_name: str = "Master",
) -> str:
    moment = local_now(timezone_name)
    addressee = str(target_name or "Master").strip() or "Master"
    return f"Good {part_of_day(moment)} {addressee}"
