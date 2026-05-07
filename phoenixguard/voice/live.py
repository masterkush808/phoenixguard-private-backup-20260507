from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _clip01(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, numeric))


def _pct01(value: Any) -> str:
    return f"{_clip01(value) * 100.0:.0f}%"


def _label(value: Any, *, fallback: str = "") -> str:
    text = str(value or "").strip().replace("_", " ")
    return text or fallback


class LocalWindowTrackerVoiceController:
    def __init__(self, tracker_service: Any) -> None:
        self.tracker_service = tracker_service

    @property
    def configured(self) -> bool:
        return True

    def get_session(self, session_id: str) -> dict[str, Any]:
        return dict(self.tracker_service.get_session(session_id))

    def start_session(self, session_id: str) -> dict[str, Any]:
        return dict(self.tracker_service.start_session(session_id))

    def stop_session(self, session_id: str) -> dict[str, Any]:
        return dict(self.tracker_service.stop_session(session_id))

    def capture_once(self, session_id: str) -> dict[str, Any]:
        return dict(self.tracker_service.capture_once(session_id))

    def update_interval(self, session_id: str, *, capture_interval_sec: float) -> dict[str, Any]:
        return dict(
            self.tracker_service.update_session_controls(
                session_id,
                capture_interval_sec=float(capture_interval_sec),
            )
        )


def build_market_context_from_tracker_session(session: Mapping[str, Any] | None) -> dict[str, str]:
    if not isinstance(session, Mapping) or not session:
        return {
            "market_summary": "Run or refresh a signal and I will translate the active market read into plain English.",
            "transition_summary": "Transition commentary will appear here after the chart has a fresh structural read.",
            "risk_summary": "Risk posture is waiting on a live chart read.",
            "signal_summary": "No active signal summary is available yet.",
        }

    latest_signal = _mapping(session.get("latest_signal", {}))
    tracking_summary = _mapping(session.get("tracking_summary", {}))
    behavior = _mapping(latest_signal.get("behavior", {}))
    action = str(latest_signal.get("action", latest_signal.get("execution_action", "HOLD")) or "HOLD").upper()
    confidence = _clip01(
        latest_signal.get(
            "effective_confidence",
            latest_signal.get("confidence", latest_signal.get("raw_confidence", 0.0)),
        )
    )
    market = _label(latest_signal.get("market", session.get("market", "")))
    timeframe = _label(latest_signal.get("timeframe", tracking_summary.get("detected_timeframe", "")))
    global_direction = str(tracking_summary.get("global_direction", "HOLD") or "HOLD").upper()
    local_direction = str(tracking_summary.get("local_direction", "HOLD") or "HOLD").upper()
    summary = str(latest_signal.get("summary", "") or "").strip()
    status = _label(session.get("status", latest_signal.get("status", "")), fallback="idle").lower()
    last_error = str(session.get("last_error", "") or "").strip()
    current_state = _label(behavior.get("current_state", ""), fallback="mixed transition")
    next_state = _label(behavior.get("next_most_likely_state", ""))
    move_quality = _label(behavior.get("move_quality", ""))

    if summary:
        market_summary = summary
    elif status in {"awaiting focus", "waiting for window", "warming", "error"}:
        market_summary = last_error or f"The tracker is currently {status}."
    elif action == "HOLD":
        market_summary = "The market is moving, but the setup is not clean enough to execute yet."
    else:
        direction_note = local_direction if local_direction in {"BUY", "SELL"} else global_direction
        scope_parts = []
        if market:
            scope_parts.append(market)
        if timeframe:
            scope_parts.append(timeframe)
        scope_text = " on ".join(scope_parts) if scope_parts else "the active chart"
        market_summary = (
            f"{scope_text} is leaning {action} with about {_pct01(confidence)} confidence, "
            f"and local structure is pointing {direction_note}."
        )

    transition_parts: list[str] = []
    if current_state:
        transition_parts.append(f"The current behavior reads like {current_state}.")
    if next_state:
        transition_parts.append(f"The next likely transition is {next_state}.")
    if global_direction in {"BUY", "SELL"} and local_direction in {"BUY", "SELL"}:
        if global_direction == local_direction:
            transition_parts.append(f"Both the broader and local structure are aligned {global_direction}.")
        else:
            transition_parts.append(
                f"The broader structure is {global_direction}, but the local structure is pushing {local_direction}."
            )
    if move_quality:
        transition_parts.append(f"Move quality looks {move_quality}.")
    transition_summary = " ".join(transition_parts).strip() or "The current transition is still forming."

    risk_parts: list[str] = []
    if status in {"awaiting focus", "waiting for window"}:
        risk_parts.append("The tracker cannot make a reliable call until the broker surface is locked and visible.")
    elif status == "warming":
        risk_parts.append("The tracker is still warming up, so the read is not mature yet.")
    elif status == "error":
        risk_parts.append(last_error or "The tracker is in an error state.")
    else:
        if action == "HOLD":
            risk_parts.append("There is no clean executable side yet, so forcing a trade would be low quality.")
        if confidence < 0.58:
            risk_parts.append("Confidence is still below the stronger execution band.")
        if move_quality and move_quality.lower() not in {"clean", "strong", "explosive", "trend"}:
            risk_parts.append(f"Move quality looks {move_quality}, so follow-through risk is still present.")
    risk_summary = " ".join(risk_parts).strip() or "Risk is controlled right now, but stay aligned with the active trigger."

    signal_prefix = f"Current action is {action} with {_pct01(confidence)} confidence."
    if market and timeframe:
        signal_prefix = f"Current action is {action} on {market} {timeframe} with {_pct01(confidence)} confidence."
    elif market:
        signal_prefix = f"Current action is {action} on {market} with {_pct01(confidence)} confidence."
    elif timeframe:
        signal_prefix = f"Current action is {action} on {timeframe} with {_pct01(confidence)} confidence."
    signal_summary = f"{signal_prefix} {market_summary}".strip()

    return {
        "market_summary": market_summary,
        "transition_summary": transition_summary,
        "risk_summary": risk_summary,
        "signal_summary": signal_summary,
    }
