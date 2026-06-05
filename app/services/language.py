"""Per-call language state, the LLM response-language policy, and prefix parsing.

V3 language routing is **LLM-led**: the main Cerebras response begins with a tiny
tag — ``<en>`` or ``<hi>`` — chosen by the model. A ``LanguagePrefixProcessor``
(between the LLM and Cartesia TTS) strips that tag, switches Cartesia's language
before any spoken text is forwarded, and streams the rest straight through.

There is intentionally **no** regex "language override" engine here anymore: a
growing list of "english mein bolo / baat kijiye / continue in english" patterns
never ends. Deepgram's detected language is kept only as a *weak hint* fed into
the prompt; the model is the authority.

Everything here is deterministic and JSON-safe.
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

SUPPORTED_RESPONSE_LANGUAGES = ("en", "hi")

# The tag the LLM must emit at the very start of every reply. Kept short so the
# prefix buffer stays tiny and streaming is barely delayed.
LANGUAGE_PREFIX_MAX_CHARS = 24

# Always-injected policy (code-owned, NOT user-editable) so that editing the
# persona prompt in /config can never break the <en>/<hi> parser contract.
RESPONSE_LANGUAGE_POLICY = (
    "You are a low-latency multilingual phone assistant.\n"
    "Begin EVERY reply with exactly one language tag: <en> or <hi>.\n"
    "  <en> = reply in English only.\n"
    "  <hi> = reply in simple, natural Hindi/Hinglish suitable for an Indian phone call.\n"
    "Immediately after the tag, write only the words to be spoken.\n"
    "Rules:\n"
    "- If the user explicitly asks for English, use <en> and reply in English only.\n"
    "- If the user explicitly asks for Hindi or Hinglish, use <hi>.\n"
    "- If there is no explicit request, mirror the latest user's language and style.\n"
    "- Keep phone replies short: usually 1-2 sentences. Ask one question at a time.\n"
    "- Give a longer answer only when the user explicitly asks for detail.\n"
    "- The tag is for the system and must NOT be spoken or explained.\n"
    "- Do not output JSON, markdown, code fences, or quotes around your reply."
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


def coerce_response_language(value: Any, *, fallback: str = DEFAULT_LANGUAGE) -> str:
    """Return a supported response-language code ('en'/'hi'), else the fallback."""
    code = _normalize_code(value)
    if code in SUPPORTED_RESPONSE_LANGUAGES:
        return code
    return fallback if fallback in SUPPORTED_RESPONSE_LANGUAGES else DEFAULT_LANGUAGE


def detect_language_from_transcription(frame: Any) -> str | None:
    """Read the detected language code from a TranscriptionFrame, if present."""
    code = _normalize_code(getattr(frame, "language", None))
    return code or None


# --- per-call language state ------------------------------------------------


def new_language_state() -> dict[str, Any]:
    """Fresh per-call language state.

    ``last_user_language`` is the weak STT hint; ``response_language`` is the
    last language the LLM actually chose (authoritative for fallback).
    """
    return {
        "last_user_language": DEFAULT_LANGUAGE,
        "response_language": DEFAULT_LANGUAGE,
        "turn_count": 0,
    }


def record_stt_language_hint(state: dict[str, Any], detected_language: str | None) -> dict[str, Any]:
    """Record the weak STT-detected language for one user turn (no TTS decision)."""
    state["turn_count"] = int(state.get("turn_count", 0)) + 1
    if detected_language:
        state["last_user_language"] = detected_language
    return state


def record_response_language(state: dict[str, Any], response_language: str) -> dict[str, Any]:
    """Record the language the LLM chose for its reply (authoritative)."""
    state["response_language"] = coerce_response_language(
        response_language, fallback=state.get("response_language", DEFAULT_LANGUAGE)
    )
    return state


def fallback_response_language(state: dict[str, Any] | None) -> str:
    """Pick a language when the LLM prefix is missing/malformed.

    Order: last response language → STT detected hint → English. An invalid
    value in one slot is skipped (it does not short-circuit to English).
    """
    if not state:
        return DEFAULT_LANGUAGE
    for key in ("response_language", "last_user_language"):
        code = _normalize_code(state.get(key))
        if code in SUPPORTED_RESPONSE_LANGUAGES:
            return code
    return DEFAULT_LANGUAGE


# NOTE (LANG-2, deferred by design): the response language currently mirrors the
# latest user turn (Option A) — the model re-decides every turn from this weak
# hint. Making an explicit "speak English from now on" request *persist*
# (Option B) would need a sticky pinned_response_language, set on detected
# switch-intent, that overrides this hint until the user changes it again. That is
# a product decision, not a confirmed bug, so it is intentionally NOT implemented.
def language_hint_line(state: dict[str, Any] | None) -> str:
    """A short weak-hint line for the prompt (the model still decides)."""
    code = _normalize_code((state or {}).get("last_user_language")) or DEFAULT_LANGUAGE
    name = {"en": "English", "hi": "Hindi/Hinglish"}.get(code, code)
    return f"(Weak hint: the user's most recent turn looked like {name}. You decide the reply language.)"


# --- <en>/<hi> prefix parsing (used by LanguagePrefixProcessor) -------------

_PREFIX_RE = re.compile(r"\s*<(en|hi)>", re.IGNORECASE)
_PREFIX_CANDIDATES = ("<en>", "<hi>")

# Strips a leading, well-formed-ish ``<...>`` fragment (used ONLY on the fallback
# path, so a malformed tag like ``<eng>`` / ``<<en>>`` is never spoken).
_FALLBACK_TAGLIKE_RE = re.compile(r"^\s*<[^>]{0,12}>\s*")


def strip_language_prefix_tag(text: str) -> str:
    """Strip a leading, well-formed ``<en>``/``<hi>`` tag off a complete line.

    Used for the greeting, which is produced by a SEPARATE one-shot LLM call and
    spoken via ``TTSSpeakFrame`` — it never passes through the
    ``LanguagePrefixProcessor``, so a leaked tag must be removed here before the
    greeting is spoken or stored. Returns the text unchanged when there is no
    leading tag.

        "<en>Hi there" -> "Hi there"
        "<hi> Namaste" -> "Namaste"
        "Hi there"     -> "Hi there"
    """
    if not text:
        return text
    match = _PREFIX_RE.match(text)
    if match is not None:
        return text[match.end():].lstrip()
    return text


def sanitize_prefix_fallback_text(text: str) -> str:
    """Clean a malformed ``<...>`` control fragment off fallback spoken text.

    Narrow by design (not a parser): strips one leading ``<...>`` token, then any
    stray leading angle brackets, so the result never begins with ``<``/``>`` and
    never carries an obvious tag fragment to TTS. May return "" (caller then
    speaks a short safe line). Well-formed ``<en>``/``<hi>`` tags are handled
    upstream and never reach here.
    """
    cleaned = _FALLBACK_TAGLIKE_RE.sub("", text or "")
    cleaned = cleaned.lstrip("<> \t\r\n")
    return cleaned.strip()


def try_parse_language_prefix(
    buffer: str, *, max_prefix_chars: int = LANGUAGE_PREFIX_MAX_CHARS
) -> tuple[str | None, str, bool]:
    """Attempt to parse a leading ``<en>``/``<hi>`` tag from accumulated text.

    Returns ``(language, remainder, done)``:
      - ``language`` is 'en'/'hi' if a valid tag was found, else None.
      - ``remainder`` is the text after the tag (or the raw buffer on giving up).
      - ``done`` is True once a decision is final (found, or fall back). When
        False, the caller should keep buffering more LLM text.

    Handles the tag split across multiple LLM text frames and leading
    whitespace. Gives up (``done=True``, ``language=None``) once it is clear no
    valid tag can form, or the buffer exceeds ``max_prefix_chars`` — so a model
    that forgets the tag never stalls the turn.
    """
    match = _PREFIX_RE.match(buffer)
    if match is not None:
        return match.group(1).lower(), buffer[match.end():], True

    stripped = buffer.lstrip().lower()
    still_building = stripped == "" or any(c.startswith(stripped) for c in _PREFIX_CANDIDATES)
    if still_building and len(buffer) < max_prefix_chars:
        return None, buffer, False
    return None, buffer, True
