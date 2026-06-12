from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any, Iterable, Mapping, Sequence


@dataclass(frozen=True)
class TimingWindow:
    start_seconds: int
    end_seconds: int

    def __post_init__(self) -> None:
        if self.start_seconds < 0:
            raise ValueError("window start_seconds must be >= 0")
        if self.end_seconds < self.start_seconds:
            raise ValueError("window end_seconds must be >= start_seconds")

    def contains(self, value_seconds: int) -> bool:
        return self.start_seconds <= value_seconds <= self.end_seconds


@dataclass(frozen=True)
class TimingProfile:
    symbol: str
    timeframe: str
    setup_type: str
    average_setup_duration_seconds: int
    median_setup_duration_seconds: int
    min_safe_expiry_seconds: int
    max_safe_expiry_seconds: int
    best_historical_expiry_seconds: int
    late_entry_threshold_seconds: int
    fakeout_window: TimingWindow
    reversal_window: TimingWindow
    continuation_window: TimingWindow

    def __post_init__(self) -> None:
        if self.average_setup_duration_seconds <= 0:
            raise ValueError("average_setup_duration_seconds must be > 0")
        if self.median_setup_duration_seconds <= 0:
            raise ValueError("median_setup_duration_seconds must be > 0")
        if self.min_safe_expiry_seconds <= 0:
            raise ValueError("min_safe_expiry_seconds must be > 0")
        if self.max_safe_expiry_seconds < self.min_safe_expiry_seconds:
            raise ValueError("max_safe_expiry_seconds must be >= min_safe_expiry_seconds")
        if not self.min_safe_expiry_seconds <= self.best_historical_expiry_seconds <= self.max_safe_expiry_seconds:
            raise ValueError("best_historical_expiry_seconds must be inside safe expiry range")
        if self.late_entry_threshold_seconds <= 0:
            raise ValueError("late_entry_threshold_seconds must be > 0")

    @property
    def key(self) -> str:
        return profile_key(self.symbol, self.timeframe, self.setup_type)


@dataclass(frozen=True)
class TimingValidation:
    allowed: bool
    reason_codes: tuple[str, ...]
    profile_key: str
    recommended_expiry_seconds: int
    observed_entry_age_seconds: int
    observed_expiry_seconds: int


def normalize_token(value: object, default: str = "*") -> str:
    token = str(value if value is not None else default).strip().lower()
    return token or default


def profile_key(symbol: object, timeframe: object, setup_type: object) -> str:
    return "|".join(
        (
            normalize_token(symbol),
            normalize_token(timeframe),
            normalize_token(setup_type),
        )
    )


def _coerce_int(value: object, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def _event_value(event: Mapping[str, Any], names: Sequence[str], default: int = 0) -> int:
    for name in names:
        if name in event:
            return _coerce_int(event.get(name), default)
    timing = event.get("execution_timing")
    if isinstance(timing, Mapping):
        for name in names:
            if name in timing:
                return _coerce_int(timing.get(name), default)
    return default


def build_timing_profile(
    *,
    symbol: str,
    timeframe: str,
    setup_type: str,
    historical_durations_seconds: Iterable[int],
    safe_expiry_range_seconds: tuple[int, int],
    best_historical_expiry_seconds: int,
    late_entry_threshold_seconds: int,
    fakeout_window_seconds: tuple[int, int],
    reversal_window_seconds: tuple[int, int],
    continuation_window_seconds: tuple[int, int],
) -> TimingProfile:
    durations = tuple(int(value) for value in historical_durations_seconds if int(value) > 0)
    if not durations:
        raise ValueError("historical_durations_seconds must contain at least one positive value")
    return TimingProfile(
        symbol=symbol,
        timeframe=timeframe,
        setup_type=setup_type,
        average_setup_duration_seconds=round(sum(durations) / len(durations)),
        median_setup_duration_seconds=round(float(median(durations))),
        min_safe_expiry_seconds=int(safe_expiry_range_seconds[0]),
        max_safe_expiry_seconds=int(safe_expiry_range_seconds[1]),
        best_historical_expiry_seconds=int(best_historical_expiry_seconds),
        late_entry_threshold_seconds=int(late_entry_threshold_seconds),
        fakeout_window=TimingWindow(*fakeout_window_seconds),
        reversal_window=TimingWindow(*reversal_window_seconds),
        continuation_window=TimingWindow(*continuation_window_seconds),
    )


DEFAULT_TIMING_PROFILES: dict[str, TimingProfile] = {
    profile.key: profile
    for profile in (
        TimingProfile(
            symbol="*",
            timeframe="*",
            setup_type="continuation",
            average_setup_duration_seconds=150,
            median_setup_duration_seconds=135,
            min_safe_expiry_seconds=120,
            max_safe_expiry_seconds=300,
            best_historical_expiry_seconds=180,
            late_entry_threshold_seconds=210,
            fakeout_window=TimingWindow(0, 45),
            reversal_window=TimingWindow(45, 105),
            continuation_window=TimingWindow(60, 210),
        ),
        TimingProfile(
            symbol="*",
            timeframe="*",
            setup_type="reversal",
            average_setup_duration_seconds=180,
            median_setup_duration_seconds=165,
            min_safe_expiry_seconds=180,
            max_safe_expiry_seconds=420,
            best_historical_expiry_seconds=300,
            late_entry_threshold_seconds=260,
            fakeout_window=TimingWindow(0, 60),
            reversal_window=TimingWindow(90, 260),
            continuation_window=TimingWindow(150, 300),
        ),
        TimingProfile(
            symbol="*",
            timeframe="*",
            setup_type="fakeout",
            average_setup_duration_seconds=90,
            median_setup_duration_seconds=75,
            min_safe_expiry_seconds=60,
            max_safe_expiry_seconds=180,
            best_historical_expiry_seconds=90,
            late_entry_threshold_seconds=120,
            fakeout_window=TimingWindow(0, 90),
            reversal_window=TimingWindow(60, 150),
            continuation_window=TimingWindow(90, 180),
        ),
    )
}


def resolve_timing_profile(
    symbol: object,
    timeframe: object,
    setup_type: object,
    profiles: Mapping[str, TimingProfile] | None = None,
) -> TimingProfile:
    profile_map = profiles or DEFAULT_TIMING_PROFILES
    candidates = (
        profile_key(symbol, timeframe, setup_type),
        profile_key(symbol, "*", setup_type),
        profile_key("*", timeframe, setup_type),
        profile_key("*", "*", setup_type),
        profile_key("*", "*", "continuation"),
    )
    for candidate in candidates:
        profile = profile_map.get(candidate)
        if profile is not None:
            return profile
    raise KeyError(f"no timing profile for {profile_key(symbol, timeframe, setup_type)}")


def validate_timing_event(
    event: Mapping[str, Any],
    profiles: Mapping[str, TimingProfile] | None = None,
) -> TimingValidation:
    symbol = event.get("symbol", event.get("pair", "*"))
    timeframe = event.get("timeframe", event.get("tf", "*"))
    setup_type = event.get("setup_type", event.get("structure_setup", "continuation"))
    profile = resolve_timing_profile(symbol, timeframe, setup_type, profiles)
    entry_age = _event_value(
        event,
        ("entry_age_seconds", "setup_age_seconds", "elapsed_seconds", "seconds_since_setup"),
        default=0,
    )
    expiry = _event_value(event, ("expiry_seconds", "requested_expiry_seconds"), default=0)

    reasons: list[str] = []
    if entry_age > profile.late_entry_threshold_seconds:
        reasons.append("late_entry")
    if expiry < profile.min_safe_expiry_seconds:
        reasons.append("expiry_too_early")
    if expiry > profile.max_safe_expiry_seconds:
        reasons.append("expiry_too_late")

    normalized_setup = normalize_token(setup_type, "continuation")
    if normalized_setup == "reversal" and entry_age < profile.reversal_window.start_seconds:
        reasons.append("reversal_needs_wait")
    elif normalized_setup == "continuation" and not profile.continuation_window.contains(entry_age):
        reasons.append("outside_continuation_window")
    elif normalized_setup == "fakeout" and profile.fakeout_window.contains(entry_age):
        reasons.append("fakeout_window")

    return TimingValidation(
        allowed=not reasons,
        reason_codes=tuple(reasons),
        profile_key=profile.key,
        recommended_expiry_seconds=profile.best_historical_expiry_seconds,
        observed_entry_age_seconds=entry_age,
        observed_expiry_seconds=expiry,
    )
