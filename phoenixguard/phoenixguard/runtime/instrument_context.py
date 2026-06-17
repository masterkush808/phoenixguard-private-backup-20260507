from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any, Mapping


INSTRUMENT_CONTEXT_LOCK_SCHEMA_VERSION = "INSTRUMENT_CONTEXT_LOCK_V1"
INSTRUMENT_CONTEXT_LOCK_V2_SCHEMA_VERSION = "INSTRUMENT_CONTEXT_LOCK_V2"

IDENTITY_CONFIRMED = "IDENTITY_CONFIRMED"
IDENTITY_LOCKED_BY_USER_PROFILE = "IDENTITY_LOCKED_BY_USER_PROFILE"
IDENTITY_VISUAL_CONTINUITY_CONFIRMED = "IDENTITY_VISUAL_CONTINUITY_CONFIRMED"
IDENTITY_UNKNOWN_BUT_PAPER_SAFE = "IDENTITY_UNKNOWN_BUT_PAPER_SAFE"
IDENTITY_UNKNOWN_NOT_EXECUTABLE = "IDENTITY_UNKNOWN_NOT_EXECUTABLE"

# Backward-compatible internal aliases used by older callers/tests. Public
# packet output uses the exact V1 state names above.
IDENTITY_UNRESOLVED = IDENTITY_UNKNOWN_NOT_EXECUTABLE
IDENTITY_BROKER_CONFIRMED = IDENTITY_CONFIRMED
IDENTITY_USER_LOCKED = IDENTITY_LOCKED_BY_USER_PROFILE
IDENTITY_PROFILE_LOCKED = IDENTITY_LOCKED_BY_USER_PROFILE
IDENTITY_CONTINUITY_LOCKED = IDENTITY_VISUAL_CONTINUITY_CONFIRMED
IDENTITY_INVALIDATED = "IDENTITY_INVALIDATED"

PAPER_SAFE_IDENTITY_STATES = {
    IDENTITY_CONFIRMED,
    IDENTITY_BROKER_CONFIRMED,
    IDENTITY_USER_LOCKED,
    IDENTITY_PROFILE_LOCKED,
    IDENTITY_CONTINUITY_LOCKED,
    IDENTITY_UNKNOWN_BUT_PAPER_SAFE,
}
BROKER_CLICK_SAFE_IDENTITY_STATES = {
    IDENTITY_CONFIRMED,
    IDENTITY_BROKER_CONFIRMED,
}

MISSING_TIMEFRAME = "MISSING_TIMEFRAME"
INSTRUMENT_CONTEXT_INVALIDATED = "INSTRUMENT_CONTEXT_INVALIDATED"
INSTRUMENT_CONTEXT_NOT_PAPER_SAFE = "INSTRUMENT_CONTEXT_NOT_PAPER_SAFE"
INSTRUMENT_CONTEXT_NOT_BROKER_CLICK_SAFE = "INSTRUMENT_CONTEXT_NOT_BROKER_CLICK_SAFE"

CTX_UNKNOWN = "UNKNOWN"
CTX_VISUAL_CONTEXT_LOCKED = "VISUAL_CONTEXT_LOCKED"
CTX_USER_PROFILE_LOCKED = "USER_PROFILE_LOCKED"
CTX_BROKER_SURFACE_LOCKED = "BROKER_SURFACE_LOCKED"
CTX_BROKER_CLICK_SAFE = "BROKER_CLICK_SAFE"
CTX_INVALIDATED = "INVALIDATED"

V2_ACTIVE_STATES = {
    CTX_VISUAL_CONTEXT_LOCKED,
    CTX_USER_PROFILE_LOCKED,
    CTX_BROKER_SURFACE_LOCKED,
    CTX_BROKER_CLICK_SAFE,
}

V2_PUBLIC_STATES = {
    CTX_UNKNOWN,
    CTX_VISUAL_CONTEXT_LOCKED,
    CTX_USER_PROFILE_LOCKED,
    CTX_BROKER_SURFACE_LOCKED,
    CTX_BROKER_CLICK_SAFE,
    CTX_INVALIDATED,
}


@dataclass(frozen=True)
class InstrumentContextValidation:
    ok: bool
    mode: str
    issues: tuple[str, ...] = field(default_factory=tuple)

    @property
    def first_reason(self) -> str:
        return self.issues[0] if self.issues else "OK"

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "mode": self.mode,
            "issues": list(self.issues),
            "first_reason": self.first_reason,
        }


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _clean_str(value: Any) -> str:
    return str(value or "").strip()


def _clean_upper(value: Any) -> str:
    return _clean_str(value).upper()


def _first_text(*values: Any) -> str:
    for value in values:
        text = _clean_str(value)
        if text:
            return text
    return ""


def _clip01(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    if parsed != parsed or parsed in {float("inf"), float("-inf")}:
        return float(default)
    return max(0.0, min(1.0, float(parsed)))


def _float_like(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    if parsed != parsed or parsed in {float("inf"), float("-inf")}:
        return float(default)
    return float(parsed)


def _int_like(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return int(default)


def _bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _bool_or_none(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return _bool(value)


def _first_bool(*values: Any) -> bool | None:
    for value in values:
        parsed = _bool_or_none(value)
        if parsed is not None:
            return parsed
    return None


def _rect_tuple(value: Any) -> tuple[int, int, int, int] | None:
    if isinstance(value, Mapping):
        if all(key in value for key in ("left", "top", "right", "bottom")):
            try:
                return (
                    int(float(value["left"])),
                    int(float(value["top"])),
                    int(float(value["right"])),
                    int(float(value["bottom"])),
                )
            except (TypeError, ValueError):
                return None
        value = value.get("rect") or value.get("bbox") or value.get("window_rect")
    if isinstance(value, (list, tuple)) and len(value) == 4:
        try:
            return tuple(int(float(item)) for item in value)  # type: ignore[return-value]
        except (TypeError, ValueError):
            return None
    return None


def _rect_close(current: tuple[int, int, int, int] | None, baseline: tuple[int, int, int, int] | None, *, tolerance: int = 6) -> bool:
    if current is None or baseline is None:
        return False
    return all(abs(int(a) - int(b)) <= int(tolerance) for a, b in zip(current, baseline))


def _first_present(*values: Any) -> Any:
    for value in values:
        if isinstance(value, str):
            if value.strip():
                return value
        elif value is not None:
            return value
    return None


def _evidence_bool(
    field_name: str,
    *,
    explicit: Mapping[str, Any],
    identity_lock: Mapping[str, Any],
    row: Mapping[str, Any],
    broker_surface: Mapping[str, Any],
) -> bool | None:
    explicit_evidence = _mapping(explicit.get("evidence"))
    identity_evidence = _mapping(identity_lock.get("evidence"))
    row_evidence = _mapping(row.get("evidence"))
    broker_evidence = _mapping(broker_surface.get("evidence"))
    return _first_bool(
        explicit.get(field_name),
        explicit_evidence.get(field_name),
        identity_lock.get(field_name),
        identity_evidence.get(field_name),
        row.get(field_name),
        row_evidence.get(field_name),
        broker_surface.get(field_name),
        broker_evidence.get(field_name),
    )


def normalize_identity_state(value: Any) -> str:
    text = _clean_upper(value)
    if text in {"", "NONE", "UNKNOWN", "UNCONFIRMED", "UNLOCKED"}:
        return IDENTITY_UNRESOLVED
    aliases = {
        "IDENTITY_CONFIRMED": IDENTITY_CONFIRMED,
        "IDENTITY_LOCKED_BY_USER_PROFILE": IDENTITY_LOCKED_BY_USER_PROFILE,
        "IDENTITY_VISUAL_CONTINUITY_CONFIRMED": IDENTITY_VISUAL_CONTINUITY_CONFIRMED,
        "IDENTITY_UNKNOWN_BUT_PAPER_SAFE": IDENTITY_UNKNOWN_BUT_PAPER_SAFE,
        "IDENTITY_UNKNOWN_NOT_EXECUTABLE": IDENTITY_UNKNOWN_NOT_EXECUTABLE,
        "OCR": IDENTITY_CONFIRMED,
        "OCR_CONFIRMED": IDENTITY_CONFIRMED,
        "MARKET_CONFIRMED": IDENTITY_CONFIRMED,
        "CONFIRMED": IDENTITY_CONFIRMED,
        "CONFIRMED_BY_OCR": IDENTITY_CONFIRMED,
        "BROKER": IDENTITY_BROKER_CONFIRMED,
        "BROKER_SAFE": IDENTITY_BROKER_CONFIRMED,
        "BROKER_CLICK_SAFE": IDENTITY_BROKER_CONFIRMED,
        "BROKER_CONFIRMED": IDENTITY_BROKER_CONFIRMED,
        "USER": IDENTITY_USER_LOCKED,
        "USER_LOCK": IDENTITY_USER_LOCKED,
        "USER_LOCKED": IDENTITY_USER_LOCKED,
        "PROFILE": IDENTITY_PROFILE_LOCKED,
        "PROFILE_LOCK": IDENTITY_PROFILE_LOCKED,
        "PROFILE_LOCKED": IDENTITY_PROFILE_LOCKED,
        "USER_PROFILE_LOCKED": IDENTITY_LOCKED_BY_USER_PROFILE,
        "LOCKED_BY_USER_PROFILE": IDENTITY_LOCKED_BY_USER_PROFILE,
        "CONTINUITY": IDENTITY_CONTINUITY_LOCKED,
        "CONTINUITY_LOCK": IDENTITY_CONTINUITY_LOCKED,
        "CONTINUITY_LOCKED": IDENTITY_CONTINUITY_LOCKED,
        "VISUAL_CONTINUITY": IDENTITY_VISUAL_CONTINUITY_CONFIRMED,
        "VISUAL_CONTINUITY_CONFIRMED": IDENTITY_VISUAL_CONTINUITY_CONFIRMED,
        "INVALID": IDENTITY_INVALIDATED,
        "INVALIDATED": IDENTITY_INVALIDATED,
        "IDENTITY_INVALIDATED": IDENTITY_INVALIDATED,
    }
    return aliases.get(text, text)


def normalize_instrument_context_state(value: Any) -> str:
    text = _clean_upper(value)
    aliases = {
        "": CTX_UNKNOWN,
        "NONE": CTX_UNKNOWN,
        "UNKNOWN": CTX_UNKNOWN,
        "UNCONFIRMED": CTX_UNKNOWN,
        "UNLOCKED": CTX_UNKNOWN,
        "VISUAL_LOCKED": CTX_VISUAL_CONTEXT_LOCKED,
        "VISUAL_CONTINUITY_CONFIRMED": CTX_VISUAL_CONTEXT_LOCKED,
        "IDENTITY_VISUAL_CONTINUITY_CONFIRMED": CTX_VISUAL_CONTEXT_LOCKED,
        "USER_LOCKED": CTX_USER_PROFILE_LOCKED,
        "PROFILE_LOCKED": CTX_USER_PROFILE_LOCKED,
        "USER_PROFILE_LOCKED": CTX_USER_PROFILE_LOCKED,
        "IDENTITY_LOCKED_BY_USER_PROFILE": CTX_USER_PROFILE_LOCKED,
        "BROKER_LOCKED": CTX_BROKER_SURFACE_LOCKED,
        "BROKER_SURFACE_LOCKED": CTX_BROKER_SURFACE_LOCKED,
        "BROKER_CONFIRMED": CTX_BROKER_SURFACE_LOCKED,
        "IDENTITY_CONFIRMED": CTX_BROKER_SURFACE_LOCKED,
        "BROKER_SAFE": CTX_BROKER_CLICK_SAFE,
        "BROKER_CLICK_SAFE": CTX_BROKER_CLICK_SAFE,
        "INVALID": CTX_INVALIDATED,
        "INVALIDATED": CTX_INVALIDATED,
        "IDENTITY_INVALIDATED": CTX_INVALIDATED,
    }
    normalized = aliases.get(text, text)
    return normalized if normalized in V2_PUBLIC_STATES else CTX_UNKNOWN


def _context_invalidation_reason(
    locked_context: Mapping[str, Any],
    *,
    session_id: str,
    timeframe: str,
    viewport_hash: str,
) -> str:
    locked_session = _clean_str(locked_context.get("session_id"))
    locked_timeframe = _clean_upper(locked_context.get("timeframe"))
    locked_viewport_hash = _clean_str(locked_context.get("viewport_hash"))
    if locked_session and session_id and locked_session != session_id:
        return "SESSION_CHANGED"
    if locked_timeframe and timeframe and locked_timeframe != timeframe:
        return "TIMEFRAME_CHANGED"
    if locked_viewport_hash and viewport_hash and locked_viewport_hash != viewport_hash:
        return "VIEWPORT_HASH_CHANGED"
    return ""


def _runtime_lock_invalidation_reason(
    locked_context: Mapping[str, Any],
    *,
    session_id: str,
    timeframe: str,
    viewport_hash: str,
    broker_surface_hash: str,
    window_handle: str,
    current_window_rect: tuple[int, int, int, int] | None,
    baseline_window_rect: tuple[int, int, int, int] | None,
    window_rect_tolerance_px: int,
    calibration_layout_id: str,
) -> str:
    reason = _context_invalidation_reason(
        locked_context,
        session_id=session_id,
        timeframe=timeframe,
        viewport_hash=viewport_hash,
    )
    if reason:
        return reason

    locked_surface_hash = _first_text(
        locked_context.get("broker_surface_hash"),
        locked_context.get("surface_hash"),
    )
    if locked_surface_hash and broker_surface_hash and locked_surface_hash != broker_surface_hash:
        return "BROKER_SURFACE_HASH_CHANGED"

    locked_window_handle = _first_text(locked_context.get("window_handle"), locked_context.get("hwnd"))
    if locked_window_handle and window_handle and locked_window_handle != window_handle:
        return "WINDOW_HANDLE_CHANGED"

    if baseline_window_rect is not None and current_window_rect is not None:
        if not _rect_close(current_window_rect, baseline_window_rect, tolerance=window_rect_tolerance_px):
            return "WINDOW_RECT_CHANGED"

    locked_layout_id = _first_text(
        locked_context.get("calibration_layout_id"),
        locked_context.get("layout_id"),
        locked_context.get("expected_calibration_layout_id"),
    )
    if locked_layout_id and calibration_layout_id and locked_layout_id != calibration_layout_id:
        return "CALIBRATION_LAYOUT_CHANGED"

    return ""


def _explicit_lock_symbol(lock: Mapping[str, Any], snapshot: Mapping[str, Any]) -> tuple[str, str]:
    for state, lock_keys, snapshot_keys in (
        (
            IDENTITY_USER_LOCKED,
            ("user_symbol", "user_locked_symbol", "locked_symbol", "display_symbol", "symbol"),
            ("user_symbol", "user_locked_symbol", "locked_symbol"),
        ),
        (
            IDENTITY_PROFILE_LOCKED,
            ("profile_symbol", "profile_locked_symbol", "configured_symbol"),
            ("profile_symbol", "profile_locked_symbol", "configured_symbol"),
        ),
    ):
        for key in lock_keys:
            text = _clean_str(lock.get(key))
            if text:
                return text, state
        for key in snapshot_keys:
            text = _clean_str(snapshot.get(key))
            if text:
                return text, state
    return "", IDENTITY_UNRESOLVED


def build_instrument_context(
    snapshot: Mapping[str, Any] | None,
    *,
    previous_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve chart identity without requiring current OCR text.

    A blank OCR symbol remains study-safe. It becomes packet-safe only when an
    explicit user/profile lock or valid continuity lock supplies the display
    symbol for the same session, timeframe, and viewport.
    """

    row = _mapping(snapshot)
    explicit = _mapping(row.get("instrument_context"))
    symbol_context = _mapping(row.get("symbol_context"))
    identity_lock = _mapping(
        row.get("instrument_identity_lock")
        or row.get("market_identity_lock")
        or row.get("identity_lock")
    )
    execution_controls = _mapping(row.get("execution_controls"))
    previous = _mapping(previous_context or explicit.get("previous_context"))

    session_id = _first_text(row.get("session_id"), explicit.get("session_id"), previous.get("session_id"))
    timeframe = _clean_upper(
        _first_text(
            explicit.get("timeframe"),
            symbol_context.get("timeframe"),
            row.get("timeframe"),
            row.get("focus_timeframe"),
            row.get("detected_timeframe"),
            identity_lock.get("timeframe"),
            previous.get("timeframe"),
        )
    )
    viewport_hash = _first_text(
        explicit.get("viewport_hash"),
        row.get("viewport_hash"),
        row.get("focus_region_hash"),
        row.get("input_viewport_hash"),
        row.get("input_frame_hash"),
        row.get("frame_hash"),
        previous.get("viewport_hash"),
    )
    broker_surface = _mapping(row.get("broker_surface"))
    broker_surface_hash = _first_text(
        explicit.get("broker_surface_hash"),
        row.get("broker_surface_hash"),
        broker_surface.get("broker_surface_hash"),
        broker_surface.get("surface_hash"),
        previous.get("broker_surface_hash"),
    )
    ocr_symbol = _first_text(
        explicit.get("ocr_symbol"),
        symbol_context.get("ocr_symbol"),
        row.get("ocr_symbol"),
        row.get("ocr_market"),
        row.get("detected_symbol"),
        row.get("detected_market"),
    )
    observed_symbol = _first_text(row.get("symbol"), row.get("market"), symbol_context.get("symbol"))
    locked_symbol, lock_state = _explicit_lock_symbol(identity_lock, row)
    display_symbol = _first_text(
        explicit.get("display_symbol"),
        symbol_context.get("display_symbol"),
        locked_symbol,
        observed_symbol,
        ocr_symbol,
    )
    explicit_state = normalize_identity_state(explicit.get("identity_state"))
    requested_state = normalize_identity_state(row.get("identity_state"))

    identity_state = IDENTITY_UNRESOLVED
    source = "unresolved"
    if explicit_state != IDENTITY_UNRESOLVED and display_symbol:
        identity_state = explicit_state
        source = str(explicit.get("source", "instrument_context") or "instrument_context")
    elif requested_state != IDENTITY_UNRESOLVED and display_symbol:
        identity_state = requested_state
        source = str(row.get("identity_source", "snapshot") or "snapshot")
    elif lock_state in {IDENTITY_USER_LOCKED, IDENTITY_PROFILE_LOCKED} and display_symbol:
        identity_state = lock_state
        source = str(
            identity_lock.get("source")
            or ("user_lock" if lock_state == IDENTITY_USER_LOCKED else "profile_lock")
        )
    elif ocr_symbol or observed_symbol:
        identity_state = IDENTITY_CONFIRMED
        display_symbol = display_symbol or ocr_symbol or observed_symbol
        source = "ocr_or_snapshot"
    elif previous and _clean_str(previous.get("display_symbol")):
        invalidation_reason = _context_invalidation_reason(
            previous,
            session_id=session_id,
            timeframe=timeframe,
            viewport_hash=viewport_hash,
        )
        display_symbol = _clean_str(previous.get("display_symbol"))
        if invalidation_reason:
            identity_state = IDENTITY_INVALIDATED
            source = "continuity_invalidated"
        else:
            identity_state = IDENTITY_CONTINUITY_LOCKED
            source = "continuity_lock"

    invalidation_reason = ""
    if identity_state not in {IDENTITY_UNRESOLVED, IDENTITY_CONFIRMED, IDENTITY_BROKER_CONFIRMED, IDENTITY_INVALIDATED}:
        if explicit_state != IDENTITY_UNRESOLVED:
            baseline = explicit
        elif lock_state in {IDENTITY_USER_LOCKED, IDENTITY_PROFILE_LOCKED}:
            baseline = identity_lock
        else:
            baseline = previous
        invalidation_reason = _context_invalidation_reason(
            baseline,
            session_id=session_id,
            timeframe=timeframe,
            viewport_hash=viewport_hash,
        )
        if invalidation_reason:
            identity_state = IDENTITY_INVALIDATED
            source = "lock_invalidated"
    elif identity_state == IDENTITY_INVALIDATED:
        invalidation_reason = _context_invalidation_reason(
            previous or explicit,
            session_id=session_id,
            timeframe=timeframe,
            viewport_hash=viewport_hash,
        ) or str(explicit.get("invalidation_reason", "") or "")

    confidence_default = 1.0 if identity_state in {IDENTITY_USER_LOCKED, IDENTITY_PROFILE_LOCKED} else 0.0
    if identity_state in {IDENTITY_CONFIRMED, IDENTITY_BROKER_CONFIRMED} and display_symbol:
        confidence_default = 0.8
    if identity_state == IDENTITY_CONTINUITY_LOCKED:
        confidence_default = max(0.0, _clip01(previous.get("confidence"), 0.8) * 0.94)
    confidence = _clip01(
        explicit.get(
            "confidence",
            row.get(
                "identity_confidence",
                row.get("market_confidence", symbol_context.get("confidence", confidence_default)),
            ),
        ),
        confidence_default,
    )
    if identity_state == IDENTITY_INVALIDATED:
        confidence = 0.0

    current_now = float(time.time())
    window_handle = _first_text(
        explicit.get("window_handle"),
        explicit.get("hwnd"),
        row.get("window_handle"),
        row.get("hwnd"),
        identity_lock.get("window_handle"),
        identity_lock.get("hwnd"),
    )
    baseline_window_handle = _first_text(
        identity_lock.get("window_handle"),
        identity_lock.get("hwnd"),
        previous.get("window_handle"),
        previous.get("hwnd"),
    )
    current_window_rect = _rect_tuple(
        _first_present(
            explicit.get("window_rect"),
            row.get("window_rect"),
            row.get("broker_window_rect"),
            identity_lock.get("window_rect"),
            identity_lock.get("broker_window_rect"),
        )
    )
    baseline_window_rect = _rect_tuple(
        _first_present(
            identity_lock.get("window_rect"),
            identity_lock.get("broker_window_rect"),
            previous.get("window_rect"),
            previous.get("broker_window_rect"),
        )
    )
    window_handle_stable = _evidence_bool(
        "window_handle_stable",
        explicit=explicit,
        identity_lock=identity_lock,
        row=row,
        broker_surface=broker_surface,
    )
    if window_handle_stable is None:
        window_handle_stable = bool(window_handle and baseline_window_handle and window_handle == baseline_window_handle)
    window_rect_stable = _evidence_bool(
        "window_rect_stable",
        explicit=explicit,
        identity_lock=identity_lock,
        row=row,
        broker_surface=broker_surface,
    )
    rect_tolerance = _int_like(
        explicit.get("window_rect_tolerance_px")
        or identity_lock.get("window_rect_tolerance_px")
        or row.get("window_rect_tolerance_px"),
        6,
    )
    if window_rect_stable is None:
        window_rect_stable = _rect_close(current_window_rect, baseline_window_rect, tolerance=rect_tolerance)
    viewport_hash_stable = _evidence_bool(
        "viewport_hash_stable",
        explicit=explicit,
        identity_lock=identity_lock,
        row=row,
        broker_surface=broker_surface,
    )
    if viewport_hash_stable is None:
        baseline_viewport_hash = _first_text(identity_lock.get("viewport_hash"), previous.get("viewport_hash"))
        viewport_hash_stable = bool(viewport_hash and baseline_viewport_hash and viewport_hash == baseline_viewport_hash)
    broker_surface_hash_stable = _evidence_bool(
        "broker_surface_hash_stable",
        explicit=explicit,
        identity_lock=identity_lock,
        row=row,
        broker_surface=broker_surface,
    )
    if broker_surface_hash_stable is None:
        baseline_surface_hash = _first_text(
            identity_lock.get("broker_surface_hash"),
            previous.get("broker_surface_hash"),
        )
        broker_surface_hash_stable = bool(broker_surface_hash and baseline_surface_hash and broker_surface_hash == baseline_surface_hash)
    calibration_layout_id = _first_text(
        explicit.get("calibration_layout_id"),
        explicit.get("layout_id"),
        row.get("calibration_layout_id"),
        row.get("layout_id"),
        broker_surface.get("calibration_layout_id"),
        broker_surface.get("layout_id"),
        identity_lock.get("calibration_layout_id"),
        identity_lock.get("layout_id"),
    )
    expected_layout_id = _first_text(
        explicit.get("expected_calibration_layout_id"),
        identity_lock.get("expected_calibration_layout_id"),
        row.get("expected_calibration_layout_id"),
        execution_controls.get("expected_calibration_layout_id"),
    )
    calibration_layout_match = _evidence_bool(
        "calibration_layout_match",
        explicit=explicit,
        identity_lock=identity_lock,
        row=row,
        broker_surface=broker_surface,
    )
    if calibration_layout_match is None:
        calibration_layout_match = bool(calibration_layout_id and (not expected_layout_id or calibration_layout_id == expected_layout_id))
    timeframe_user_locked = bool(_clean_upper(identity_lock.get("timeframe")))
    timeframe_known = bool(timeframe or timeframe_user_locked)
    session_active = _evidence_bool(
        "session_active",
        explicit=explicit,
        identity_lock=identity_lock,
        row=row,
        broker_surface=broker_surface,
    )
    if session_active is None:
        session_active = bool(session_id)
    packet_fresh = _evidence_bool(
        "packet_fresh",
        explicit=explicit,
        identity_lock=identity_lock,
        row=row,
        broker_surface=broker_surface,
    )
    if packet_fresh is None:
        valid_until = _float_like(
            explicit.get("valid_until_epoch_sec")
            or explicit.get("valid_until_epoch")
            or identity_lock.get("valid_until_epoch_sec")
            or identity_lock.get("valid_until_epoch")
            or row.get("valid_until_epoch_sec")
            or row.get("valid_until_epoch"),
            0.0,
        )
        packet_fresh = bool(valid_until <= 0.0 or valid_until > current_now)
    models_awake = _evidence_bool(
        "models_awake",
        explicit=explicit,
        identity_lock=identity_lock,
        row=row,
        broker_surface=broker_surface,
    )
    if models_awake is None:
        model_health = _mapping(row.get("runtime_model_health") or row.get("model_health"))
        models_awake = bool(model_health.get("all_required_models_awake", True))
    profile_mismatch = _evidence_bool(
        "profile_mismatch",
        explicit=explicit,
        identity_lock=identity_lock,
        row=row,
        broker_surface=broker_surface,
    )
    if profile_mismatch is None:
        expected_session = _first_text(identity_lock.get("session_id"), previous.get("session_id"))
        expected_timeframe = _clean_upper(_first_text(identity_lock.get("timeframe"), previous.get("timeframe")))
        profile_mismatch = bool(
            (expected_session and session_id and expected_session != session_id)
            or (expected_timeframe and timeframe and expected_timeframe != timeframe)
        )

    runtime_baseline = identity_lock if identity_lock else previous or explicit
    if identity_state in PAPER_SAFE_IDENTITY_STATES and runtime_baseline:
        runtime_invalidation_reason = _runtime_lock_invalidation_reason(
            runtime_baseline,
            session_id=session_id,
            timeframe=timeframe,
            viewport_hash=viewport_hash,
            broker_surface_hash=broker_surface_hash,
            window_handle=window_handle,
            current_window_rect=current_window_rect,
            baseline_window_rect=baseline_window_rect,
            window_rect_tolerance_px=rect_tolerance,
            calibration_layout_id=calibration_layout_id,
        )
        if runtime_invalidation_reason:
            identity_state = IDENTITY_INVALIDATED
            source = "runtime_lock_invalidated"
            invalidation_reason = runtime_invalidation_reason
            confidence = 0.0

    user_profile_locked = bool(identity_state in {IDENTITY_USER_LOCKED, IDENTITY_PROFILE_LOCKED})
    visual_continuity_frames = max(
        0,
        _int_like(
            explicit.get("visual_continuity_frames")
            or identity_lock.get("visual_continuity_frames")
            or row.get("visual_continuity_frames")
            or previous.get("visual_continuity_frames"),
            1 if identity_state == IDENTITY_CONTINUITY_LOCKED else 0,
        ),
    )
    if not ocr_symbol:
        if user_profile_locked and display_symbol:
            symbol_uncertainty = "OCR_SYMBOL_MISSING_USER_PROFILE_LOCKED"
        elif identity_state == IDENTITY_CONTINUITY_LOCKED and display_symbol:
            symbol_uncertainty = "OCR_SYMBOL_MISSING_VISUAL_CONTINUITY"
        elif display_symbol:
            symbol_uncertainty = "OCR_SYMBOL_MISSING_ALTERNATE_SOURCE"
        else:
            symbol_uncertainty = "OCR_SYMBOL_MISSING_UNRESOLVED"
    else:
        symbol_uncertainty = "NONE"
    window_handle_locked = bool(window_handle and window_handle_stable)
    window_rect_locked = bool(current_window_rect is not None and window_rect_stable)
    viewport_hash_locked = bool(viewport_hash and viewport_hash_stable)
    broker_surface_hash_locked = bool(broker_surface_hash and broker_surface_hash_stable)
    calibration_layout_locked = bool(calibration_layout_id and calibration_layout_match)
    v2_evidence = {
        "window_handle_stable": window_handle_locked,
        "window_rect_stable": window_rect_locked,
        "window_rect_tolerance_px": int(rect_tolerance),
        "viewport_hash_stable": viewport_hash_locked,
        "broker_surface_hash_stable": broker_surface_hash_locked,
        "calibration_layout_match": calibration_layout_locked,
        "timeframe_known": bool(timeframe_known),
        "timeframe_user_locked": timeframe_user_locked,
        "session_active": bool(session_active),
        "packet_fresh": bool(packet_fresh),
        "models_awake": bool(models_awake),
        "user_profile_locked": bool(user_profile_locked),
        "profile_mismatch": bool(profile_mismatch),
        "visual_continuity_frames": int(visual_continuity_frames),
        "ocr_symbol_present": bool(ocr_symbol),
        "ocr_symbol_uncertain": symbol_uncertainty != "NONE",
        "symbol_uncertainty": symbol_uncertainty,
        "window_handle": window_handle,
        "window_rect": list(current_window_rect) if current_window_rect is not None else [],
        "viewport_hash": viewport_hash,
        "broker_surface_hash": broker_surface_hash,
        "calibration_layout_id": calibration_layout_id,
    }
    v2_required = (
        "window_handle_stable",
        "window_rect_stable",
        "viewport_hash_stable",
        "broker_surface_hash_stable",
        "calibration_layout_match",
        "timeframe_known",
        "session_active",
        "packet_fresh",
        "models_awake",
    )
    v2_missing = [key for key in v2_required if not v2_evidence.get(key)]
    if v2_evidence["profile_mismatch"]:
        v2_missing.append("profile_mismatch=false")
    if identity_state == IDENTITY_INVALIDATED:
        v2_state = CTX_INVALIDATED
    elif display_symbol and timeframe and not v2_missing and identity_state in PAPER_SAFE_IDENTITY_STATES:
        v2_state = CTX_BROKER_CLICK_SAFE
    elif bool(broker_surface_hash and (broker_surface_hash_stable or broker_surface.get("controls_ready"))):
        v2_state = CTX_BROKER_SURFACE_LOCKED
    elif user_profile_locked:
        v2_state = CTX_USER_PROFILE_LOCKED
    elif bool(viewport_hash and (viewport_hash_stable or visual_continuity_frames > 0)):
        v2_state = CTX_VISUAL_CONTEXT_LOCKED
    else:
        v2_state = CTX_UNKNOWN
    if v2_state == CTX_BROKER_CLICK_SAFE:
        v2_reason = "User/profile or broker-confirmed context has stable window, viewport, broker surface, calibration layout, session, packet freshness, and model health."
        v2_release_condition = "none"
    elif v2_state == CTX_INVALIDATED:
        v2_reason = invalidation_reason or "Instrument context changed or was invalidated."
        v2_release_condition = "restore locked profile, timeframe, viewport, broker surface, and calibration layout"
    else:
        v2_reason = "Instrument context is not broker-click-safe yet."
        v2_release_condition = " + ".join(v2_missing) if v2_missing else "stable locked broker surface evidence"

    paper_safe = bool(
        display_symbol
        and timeframe
        and identity_state in PAPER_SAFE_IDENTITY_STATES
    )
    broker_click_safe = bool(
        display_symbol
        and timeframe
        and v2_state == CTX_BROKER_CLICK_SAFE
    )

    public_identity_state = IDENTITY_UNKNOWN_NOT_EXECUTABLE if identity_state == IDENTITY_INVALIDATED else identity_state

    return {
        "schema_version": INSTRUMENT_CONTEXT_LOCK_SCHEMA_VERSION,
        "identity_state": public_identity_state,
        "display_symbol": display_symbol,
        "ocr_symbol": ocr_symbol,
        "timeframe": timeframe,
        "viewport_hash": viewport_hash,
        "broker_surface_hash": broker_surface_hash,
        "confidence": round(float(confidence), 4),
        "paper_safe": paper_safe,
        "broker_click_safe": broker_click_safe,
        "lock_version": INSTRUMENT_CONTEXT_LOCK_V2_SCHEMA_VERSION,
        "identity_state_v2": v2_state,
        "instrument_context_state": v2_state,
        "symbol_source": source,
        "reason": v2_reason,
        "release_condition": v2_release_condition,
        "evidence": v2_evidence,
        "created_epoch_sec": current_now,
        "valid_until_epoch_sec": current_now + 8.0,
        "window_handle": window_handle,
        "window_rect": list(current_window_rect) if current_window_rect is not None else [],
        "calibration_layout_id": calibration_layout_id,
        "session_id": session_id,
        "source": source,
        "invalidated": identity_state == IDENTITY_INVALIDATED,
        "invalidation_reason": invalidation_reason,
    }


def normalize_instrument_context(value: Mapping[str, Any] | None) -> dict[str, Any]:
    return build_instrument_context({"instrument_context": _mapping(value)})


def symbol_context_from_instrument_context(context: Mapping[str, Any]) -> dict[str, Any]:
    row = _mapping(context)
    return {
        "schema_version": INSTRUMENT_CONTEXT_LOCK_SCHEMA_VERSION,
        "symbol": _clean_str(row.get("display_symbol")),
        "display_symbol": _clean_str(row.get("display_symbol")),
        "ocr_symbol": _clean_str(row.get("ocr_symbol")),
        "timeframe": _clean_upper(row.get("timeframe")),
        "identity_state": normalize_identity_state(row.get("identity_state")),
        "confidence": _clip01(row.get("confidence"), 0.0),
        "paper_safe": _bool(row.get("paper_safe")),
        "broker_click_safe": _bool(row.get("broker_click_safe")),
    }


def validate_instrument_context(
    context: Mapping[str, Any] | None,
    *,
    mode: str = "study",
) -> InstrumentContextValidation:
    row = _mapping(context)
    normalized_mode = _clean_str(mode).lower() or "study"
    issues: list[str] = []
    if not _clean_upper(row.get("timeframe")):
        issues.append(MISSING_TIMEFRAME)
    if normalize_identity_state(row.get("identity_state")) == IDENTITY_INVALIDATED or _bool(row.get("invalidated")):
        issues.append(INSTRUMENT_CONTEXT_INVALIDATED)
    if normalized_mode in {"paper", "packet", "execution", "paper_packet"} and not _bool(row.get("paper_safe")):
        issues.append(INSTRUMENT_CONTEXT_NOT_PAPER_SAFE)
    if normalized_mode in {"broker_click", "broker", "live", "live_click"} and not _bool(row.get("broker_click_safe")):
        issues.append(INSTRUMENT_CONTEXT_NOT_BROKER_CLICK_SAFE)
    return InstrumentContextValidation(
        ok=not issues,
        mode=normalized_mode,
        issues=tuple(dict.fromkeys(issues)),
    )
