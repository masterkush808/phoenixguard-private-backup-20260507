from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


VISUAL_PLAY_MEMORY_BANK_VERSION = "PG_VISUAL_PLAY_MEMORY_BANK_V3"


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    if parsed != parsed or parsed in {float("inf"), float("-inf")}:
        return float(default)
    return float(parsed)


def _clip01(value: Any, default: float = 0.0) -> float:
    return max(0.0, min(1.0, _float(value, default)))


def _side(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text in {"BUY", "BULL", "BULLISH", "UP", "CALL", "GOOD_BUY"}:
        return "BUY"
    if text in {"SELL", "BEAR", "BEARISH", "DOWN", "PUT", "GOOD_SELL"}:
        return "SELL"
    return "HOLD"


def _upper(value: Any, default: str = "") -> str:
    text = str(value or "").strip().upper().replace(" ", "_").replace("-", "_")
    return text or default


def _good_for_side(outcome: str, side: str) -> bool:
    normalized = _upper(outcome)
    if side == "BUY":
        return normalized in {"GOOD_BUY", "BUY_WIN", "WIN", "GOOD", "DIRECT_CONTINUATION", "FAVOURABLE"}
    if side == "SELL":
        return normalized in {"GOOD_SELL", "SELL_WIN", "WIN", "GOOD", "DIRECT_CONTINUATION", "FAVOURABLE"}
    return normalized in {"WIN", "GOOD", "FAVOURABLE"}


def _bad_outcome(outcome: str) -> bool:
    normalized = _upper(outcome)
    return any(token in normalized for token in ("LOSS", "FAILED", "BAD", "LATE", "DRAWDOWN"))


@dataclass(frozen=True, slots=True)
class VisualPlayMemory:
    memory_id: str
    setup_type: str
    side: str
    regime: str
    entry_location: str
    path_class: str
    outcome: str
    similarity: float
    notes: str = ""

    @staticmethod
    def from_row(row: Mapping[str, Any], index: int = 0) -> "VisualPlayMemory":
        setup_type = _upper(row.get("setup_type") or row.get("play_type") or row.get("setup") or row.get("best_match_setup") or "UNKNOWN")
        outcome = _upper(row.get("outcome") or row.get("historical_outcome") or row.get("best_match_outcome") or "")
        side = _side(row.get("side") or row.get("label") or row.get("dominant_side") or row.get("action") or outcome)
        if side == "HOLD":
            side = "BUY" if "BUY" in setup_type else "SELL" if "SELL" in setup_type else "HOLD"
        return VisualPlayMemory(
            memory_id=str(row.get("memory_id") or row.get("entry_id") or row.get("match_id") or f"mem_{index:03d}"),
            setup_type=setup_type,
            side=side,
            regime=_upper(row.get("regime") or row.get("market_regime") or row.get("macro_trend") or "UNKNOWN"),
            entry_location=_upper(row.get("entry_location") or row.get("price_location") or row.get("current_location") or "UNKNOWN"),
            path_class=_upper(row.get("path_class") or row.get("intent_next") or row.get("outcome_path") or "UNKNOWN"),
            outcome=outcome or "UNKNOWN",
            similarity=_clip01(row.get("similarity", row.get("memory_similarity", row.get("similarity_score", 0.58))), 0.58),
            notes=str(row.get("notes") or row.get("reason") or ""),
        )

    def as_match(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "similarity": round(self.similarity, 4),
            "setup_type": self.setup_type,
            "side": self.side,
            "regime": self.regime,
            "entry_location": self.entry_location,
            "path_class": self.path_class,
            "historical_outcome": self.outcome,
            "notes": self.notes,
        }


class VisualPlayMemoryBank:
    def __init__(self, memories: Sequence[VisualPlayMemory] | None = None) -> None:
        self._memories = list(memories or [])

    @classmethod
    def from_rows(cls, rows: Sequence[Mapping[str, Any]] | None) -> "VisualPlayMemoryBank":
        return cls([VisualPlayMemory.from_row(row, index) for index, row in enumerate(rows or [])])

    @property
    def is_loaded(self) -> bool:
        return bool(self._memories)

    def confirm(
        self,
        *,
        side: str,
        market_play: Mapping[str, Any],
        regime: Mapping[str, Any],
        price_location: Mapping[str, Any],
        top_k: int = 5,
    ) -> dict[str, Any]:
        resolved_side = _side(side or market_play.get("side_bias"))
        play = _upper(market_play.get("primary_play"))
        secondary = _upper(market_play.get("secondary_play"))
        regime_primary = _upper(regime.get("primary"))
        location = _upper(price_location.get("relative_location") or price_location.get("price_location"))

        scored: list[tuple[float, VisualPlayMemory]] = []
        for memory in self._memories:
            score = memory.similarity
            if resolved_side in {"BUY", "SELL"} and memory.side == resolved_side:
                score += 0.12
            if memory.setup_type == play or (secondary and memory.setup_type == secondary):
                score += 0.18
            elif play and play in memory.setup_type:
                score += 0.10
            if regime_primary and memory.regime == regime_primary:
                score += 0.07
            if location and memory.entry_location == location:
                score += 0.05
            if _good_for_side(memory.outcome, resolved_side):
                score += 0.04
            if _bad_outcome(memory.outcome):
                score -= 0.08
            scored.append((_clip01(score), memory))

        scored.sort(key=lambda item: item[0], reverse=True)
        matches = []
        buy_weight = 0.0
        sell_weight = 0.0
        failed_weight = 0.0
        for score, memory in scored[: max(0, top_k)]:
            match = memory.as_match()
            match["similarity"] = round(score, 4)
            matches.append(match)
            if memory.side == "BUY":
                buy_weight += score
            elif memory.side == "SELL":
                sell_weight += score
            if _bad_outcome(memory.outcome):
                failed_weight += score

        memory_vote = "BUY" if buy_weight > sell_weight + 1e-6 else "SELL" if sell_weight > buy_weight + 1e-6 else "HOLD"
        top_similarity = matches[0]["similarity"] if matches else 0.0
        adjustment = 0.0
        if memory_vote == resolved_side and top_similarity >= 0.62:
            adjustment = min(0.08, 0.025 + 0.055 * top_similarity)
        elif memory_vote in {"BUY", "SELL"} and resolved_side in {"BUY", "SELL"} and memory_vote != resolved_side:
            adjustment = -min(0.08, 0.03 + 0.05 * top_similarity)
        if failed_weight > max(buy_weight, sell_weight) * 0.42:
            adjustment -= 0.04
        adjustment = max(-0.12, min(0.12, adjustment))

        warning = ""
        if failed_weight > 0.0 and failed_weight >= max(buy_weight, sell_weight) * 0.34:
            warning = "Similar memory includes failed or late entries; use as caution, not authority."
        elif matches and top_similarity < 0.60:
            warning = "Memory similarity is weak; treat recall as background evidence only."

        return {
            "version": VISUAL_PLAY_MEMORY_BANK_VERSION,
            "memory_confirmation": {
                "top_matches": matches,
                "memory_vote": memory_vote,
                "confidence_adjustment": round(adjustment, 4),
                "similarity": round(top_similarity, 4),
                "confirmed": bool(memory_vote == resolved_side and top_similarity >= 0.62 and adjustment >= 0.0),
                "warning": warning,
                "rule": "Memory confirms structure; memory does not force execution.",
            },
        }


def _memory_rows_from_snapshot(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = _rows(snapshot.get("visual_play_memory") or snapshot.get("memory_rows") or snapshot.get("memory_matches") or snapshot.get("history_matches"))
    if rows:
        return rows
    history = _mapping(snapshot.get("history_context") or snapshot.get("historical_pattern"))
    best_matches = _rows(history.get("best_matches"))
    if best_matches:
        return best_matches
    if history:
        row = {
            "memory_id": history.get("best_match_id") or history.get("best_match_setup") or "history_best_match",
            "setup_type": history.get("best_match_setup"),
            "side": history.get("side") or history.get("dominant_side"),
            "outcome": history.get("best_match_outcome"),
            "entry_location": history.get("where_history_would_enter"),
            "similarity": max(_clip01(history.get("similarity_to_winning_setups"), 0.0), _clip01(history.get("similarity_to_losing_setups"), 0.0)),
            "notes": history.get("reason") or history.get("similarity_state"),
        }
        if row["setup_type"] or row["outcome"] or row["similarity"] > 0.0:
            return [row]
    return []


def analyze_visual_play_memory_confirmation(
    snapshot: Mapping[str, Any] | None,
    *,
    side: str,
    market_play: Mapping[str, Any],
    regime: Mapping[str, Any],
    price_location: Mapping[str, Any],
    top_k: int = 5,
) -> dict[str, Any]:
    source = dict(snapshot or {})
    bank = VisualPlayMemoryBank.from_rows(_memory_rows_from_snapshot(source))
    return bank.confirm(
        side=side,
        market_play=market_play,
        regime=regime,
        price_location=price_location,
        top_k=top_k,
    )


__all__ = [
    "VISUAL_PLAY_MEMORY_BANK_VERSION",
    "VisualPlayMemory",
    "VisualPlayMemoryBank",
    "analyze_visual_play_memory_confirmation",
]
