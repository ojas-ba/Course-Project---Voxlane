"""Per-call language detection, explicit-override parsing, and resolution.

Hindi + English from day one. Detected language comes from Deepgram Flux
``TranscriptionFrame.language``; explicit user commands ("continue in English",
"Hindi mein bolo", ...) override detection until changed again.
"""

from __future__ import annotations

import re
from typing import Any

from pipecat.transcriptions.language import Language

DEFAULT_LANGUAGE = "en"

# Codes we actively support in Phase 1 (others still map through if Flux returns them).
_CODE_TO_ENUM = {
    "en": Language.EN,
    "hi": Language.HI,
}

_ENGLISH_OVERRIDE = re.compile(
    r"\b(?:continue\s+in\s+english|speak\s+(?:in\s+)?english|switch\s+to\s+english"
    r"|in\s+english\s+please|english\s+(?:mein|me)\s+(?:bolo|baat\s+karo))\b",
    re.IGNORECASE,
)
_HINDI_OVERRIDE = re.compile(
    r"\b(?:continue\s+in\s+hindi|speak\s+(?:in\s+)?hindi|switch\s+to\s+hindi"
    r"|hindi\s+(?:mein|me)\s+(?:bolo|baat\s+karo))\b",
    re.IGNORECASE,
)


def to_language_enum(code: str) -> Language | None:
    """Map a short language code (e.g. 'en', 'hi') to a Pipecat Language enum."""
    return _CODE_TO_ENUM.get(_normalize_code(code))


def language_hints_to_enums(codes: list[str]) -> list[Language]:
    """Map configured hint codes to Language enums, dropping unknowns."""
    out: list[Language] = []
    for code in codes:
        enum = to_language_enum(code)
        if enum is not None:
            out.append(enum)
    return out


def _normalize_code(value: Any) -> str:
    """Normalize a Language enum / string into a base code like 'en' or 'hi'."""
    if value is None:
        return ""
    raw = value.value if isinstance(value, Language) else str(value)
    return raw.strip().lower().split("-")[0]


def detect_language_from_transcription(frame: Any) -> str | None:
    """Read the detected language code from a TranscriptionFrame, if present."""
    code = _normalize_code(getattr(frame, "language", None))
    return code or None


def parse_language_override(text: str) -> str | None:
    """Return 'en'/'hi' if the text contains an explicit language switch command."""
    if not text:
        return None
    if _ENGLISH_OVERRIDE.search(text):
        return "en"
    if _HINDI_OVERRIDE.search(text):
        return "hi"
    return None


def new_language_state() -> dict[str, Any]:
    return {
        "last_user_language": DEFAULT_LANGUAGE,
        "response_language": DEFAULT_LANGUAGE,
        "explicit_language_override": None,
        "turn_count": 0,
    }


def update_language_state(
    state: dict[str, Any],
    *,
    user_text: str,
    detected_language: str | None,
) -> dict[str, Any]:
    """Update language state for one committed user turn and resolve response language."""
    state["turn_count"] = int(state.get("turn_count", 0)) + 1

    override = parse_language_override(user_text)
    if override is not None:
        state["explicit_language_override"] = override

    if detected_language:
        state["last_user_language"] = detected_language

    state["response_language"] = resolve_response_language(state)
    return state


def resolve_response_language(state: dict[str, Any]) -> str:
    """Explicit override wins; otherwise mirror the latest detected user language."""
    override = state.get("explicit_language_override")
    if override:
        return override
    return state.get("last_user_language") or DEFAULT_LANGUAGE


def language_instruction(response_language: str) -> str:
    """The per-turn instruction injected into the LLM system message."""
    name = {"en": "English", "hi": "Hindi"}.get(response_language, response_language)
    return (
        f"Respond in {name}. Mirror the user's language naturally (Hindi/Hinglish or "
        f"English) and only switch when the user explicitly asks."
    )
