from __future__ import annotations

from dataclasses import dataclass
import difflib
import re
from typing import Any


_NUMBER_WORDS: dict[str, int] = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
}

_SENSITIVE_DISCLOSURE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(secret|token|api key|apikey|credential|password|passphrase|private key)\b",
        r"\b(env|environment variable|dotenv|backend data|raw config|config file)\b",
        r"\b(show|reveal|dump|print|read out|expose)\b.*\b(secret|token|credential|password|passphrase)\b",
    )
)


@dataclass(frozen=True, slots=True)
class VoiceIntentSpec:
    name: str
    description: str
    phrases: tuple[str, ...]
    keywords: tuple[tuple[str, ...], ...] = ()
    requires_seconds: bool = False


@dataclass(frozen=True, slots=True)
class VoiceIntentMatch:
    name: str
    confidence: float
    slots: dict[str, Any]
    blocked_sensitive_request: bool = False


VOICE_INTENT_SPECS: tuple[VoiceIntentSpec, ...] = (
    VoiceIntentSpec(
        name="voice.help",
        description="List the main voice command capabilities.",
        phrases=("help", "what can you do", "show commands", "list commands"),
        keywords=(("help", "commands", "do"),),
    ),
    VoiceIntentSpec(
        name="voice.enable",
        description="Enable the 808 voice layer.",
        phrases=("turn voice on", "enable voice", "wake up", "resume voice"),
        keywords=(("voice",), ("on", "enable", "resume", "wake")),
    ),
    VoiceIntentSpec(
        name="voice.disable",
        description="Disable the 808 voice layer.",
        phrases=("turn voice off", "disable voice", "mute voice", "go silent"),
        keywords=(("voice",), ("off", "disable", "mute", "silent")),
    ),
    VoiceIntentSpec(
        name="voice.listening.enable",
        description="Resume active listening.",
        phrases=("resume listening", "start listening", "listen again"),
        keywords=(("listen", "listening"), ("start", "resume", "enable")),
    ),
    VoiceIntentSpec(
        name="voice.listening.disable",
        description="Pause active listening.",
        phrases=("pause listening", "stop listening", "stop hearing me"),
        keywords=(("listen", "listening", "hearing"), ("pause", "stop", "disable")),
    ),
    VoiceIntentSpec(
        name="tracker.timer.enable",
        description="Start automatic tracker capture.",
        phrases=(
            "turn automatic timer on",
            "start the automatic timer",
            "resume automatic tracking",
            "switch the automatic timer on",
        ),
        keywords=(("automatic", "auto"), ("timer", "tracking", "tracker"), ("on", "start", "resume", "enable", "switch")),
    ),
    VoiceIntentSpec(
        name="tracker.timer.disable",
        description="Stop automatic tracker capture.",
        phrases=(
            "turn automatic timer off",
            "stop the automatic timer",
            "pause automatic tracking",
            "switch the automatic timer off",
        ),
        keywords=(("automatic", "auto"), ("timer", "tracking", "tracker"), ("off", "stop", "pause", "disable", "switch")),
    ),
    VoiceIntentSpec(
        name="tracker.interval.set",
        description="Set the tracker capture interval in seconds.",
        phrases=(
            "set timer to 3 seconds",
            "change the timer to 5 seconds",
            "make the interval 1 second",
            "adjust the timer from 3 seconds to 5 seconds",
        ),
        keywords=(("timer", "interval", "seconds", "second", "time"), ("set", "change", "make", "adjust", "move")),
        requires_seconds=True,
    ),
    VoiceIntentSpec(
        name="tracker.capture.once",
        description="Run one immediate tracker capture.",
        phrases=("capture once", "scan right now", "analyze one frame now"),
        keywords=(("capture", "scan", "analyze"), ("once", "now", "immediate")),
    ),
    VoiceIntentSpec(
        name="market.summary",
        description="Read the current market state in plain English.",
        phrases=("what is the market saying", "read the market", "market summary"),
        keywords=(("market", "signal", "setup"), ("summary", "saying", "read", "status")),
    ),
    VoiceIntentSpec(
        name="market.transitions",
        description="Explain the current transition or structural shift.",
        phrases=("tell me the transitions", "explain the transition", "what changed in the structure"),
        keywords=(("transition", "transitions", "changed", "shift"),),
    ),
    VoiceIntentSpec(
        name="market.risk",
        description="Explain the current risk posture in plain English.",
        phrases=("summarize the risk", "what is the risk right now", "risk status"),
        keywords=(("risk",), ("status", "summary", "right")),
    ),
    VoiceIntentSpec(
        name="market.signal",
        description="Read the active signal in plain English.",
        phrases=("read the current signal", "what is the active signal", "plain english signal"),
        keywords=(("signal", "setup"), ("read", "plain", "current", "active")),
    ),
    VoiceIntentSpec(
        name="dashboard.open",
        description="Open the live PhoenixGuard dashboard.",
        phrases=("open the dashboard", "show me the dashboard", "bring up phoenixguard"),
        keywords=(("dashboard", "phoenixguard", "tracker"), ("open", "show", "bring")),
    ),
    VoiceIntentSpec(
        name="session.status",
        description="Read the current voice and tracker status.",
        phrases=("status report", "give me the current status", "where are we now"),
        keywords=(("status", "report"),),
    ),
)


def _normalize_text(text: str) -> str:
    lowered = str(text or "").strip().lower()
    lowered = re.sub(r"[^\w\s.]", " ", lowered)
    lowered = re.sub(r"\s+", " ", lowered)
    return lowered.strip()


def _normalize_tokens(text: str) -> list[str]:
    return [token for token in _normalize_text(text).split(" ") if token]


def _number_from_tokens(tokens: list[str]) -> int | None:
    if not tokens:
        return None
    for index, token in enumerate(tokens):
        if token.isdigit():
            return int(token)
        if token in _NUMBER_WORDS:
            if index + 1 < len(tokens) and tokens[index + 1] in _NUMBER_WORDS and _NUMBER_WORDS[token] >= 20:
                return _NUMBER_WORDS[token] + _NUMBER_WORDS[tokens[index + 1]]
            return _NUMBER_WORDS[token]
    return None


def _extract_seconds(text: str) -> float | None:
    normalized = _normalize_text(text)
    number_match = re.search(r"(\d+(?:\.\d+)?)\s*(second|seconds|sec|secs|s)\b", normalized)
    if number_match:
        return float(number_match.group(1))
    tokens = _normalize_tokens(normalized)
    for index, token in enumerate(tokens):
        if token in {"second", "seconds", "sec", "secs", "s"} and index > 0:
            candidate = _number_from_tokens(tokens[max(0, index - 2):index])
            if candidate is not None:
                return float(candidate)
    if "timer" in tokens or "interval" in tokens:
        candidate = _number_from_tokens(tokens)
        if candidate is not None:
            return float(candidate)
    return None


def _token_matches(token: str, candidate: str) -> bool:
    if token == candidate:
        return True
    return difflib.SequenceMatcher(None, token, candidate).ratio() >= 0.82


def _score_keywords(tokens: list[str], groups: tuple[tuple[str, ...], ...]) -> float:
    if not groups:
        return 0.0
    matched = 0
    for group in groups:
        if any(_token_matches(token, alias) for token in tokens for alias in group):
            matched += 1
    return matched / max(len(groups), 1)


def _score_phrases(normalized_text: str, phrases: tuple[str, ...]) -> float:
    if not phrases:
        return 0.0
    best = 0.0
    for phrase in phrases:
        normalized_phrase = _normalize_text(phrase)
        if not normalized_phrase:
            continue
        if normalized_phrase in normalized_text:
            return 1.0
        ratio = difflib.SequenceMatcher(None, normalized_text, normalized_phrase).ratio()
        best = max(best, ratio)
    return best


def blocks_sensitive_disclosure(text: str) -> bool:
    normalized = str(text or "").strip()
    return any(pattern.search(normalized) is not None for pattern in _SENSITIVE_DISCLOSURE_PATTERNS)


def parse_voice_command(text: str) -> VoiceIntentMatch:
    normalized = _normalize_text(text)
    if not normalized:
        return VoiceIntentMatch(name="voice.help", confidence=0.0, slots={})
    if blocks_sensitive_disclosure(normalized):
        return VoiceIntentMatch(
            name="security.block_sensitive_disclosure",
            confidence=1.0,
            slots={},
            blocked_sensitive_request=True,
        )

    tokens = _normalize_tokens(normalized)
    best_spec: VoiceIntentSpec | None = None
    best_score = 0.0
    for spec in VOICE_INTENT_SPECS:
        phrase_score = _score_phrases(normalized, spec.phrases)
        keyword_score = _score_keywords(tokens, spec.keywords)
        requires_seconds_bonus = 0.15 if spec.requires_seconds and _extract_seconds(normalized) is not None else 0.0
        score = max(phrase_score, 0.55 * phrase_score + 0.45 * keyword_score) + requires_seconds_bonus
        if score > best_score:
            best_score = score
            best_spec = spec

    if best_spec is None or best_score < 0.48:
        return VoiceIntentMatch(name="voice.help", confidence=max(best_score, 0.0), slots={})

    slots: dict[str, Any] = {}
    if best_spec.requires_seconds:
        seconds = _extract_seconds(normalized)
        if seconds is not None:
            slots["seconds"] = float(seconds)
    return VoiceIntentMatch(name=best_spec.name, confidence=min(best_score, 1.0), slots=slots)


def public_voice_command_catalog() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for spec in VOICE_INTENT_SPECS:
        rows.append(
            {
                "name": spec.name,
                "description": spec.description,
                "examples": list(spec.phrases[:3]),
            }
        )
    return rows
