"""Bounded in-call memory for 20-30 turn calls.

Memory = recent window (kept in the LLM context) + pinned facts + a small
running summary that only kicks in once the call gets long + optional recall
snippets when the user explicitly refers to earlier context. The full transcript
is persisted for the Call Detail page but is NEVER injected wholesale.

Everything here is deterministic and JSON-safe. Pinned-fact extraction is
rule-based (no extra LLM call). The running summary is a cheap deterministic
compaction updated AFTER the reply (never before), so it cannot add latency to
the hot STT -> LLM -> TTS path.
"""

from __future__ import annotations

import re
from typing import Any

_FILLER = {
    "haan", "han", "ok", "okay", "k", "hmm", "uh", "um", "yeah", "ya", "yes",
    "no", "nope", "sure", "wait", "hello", "hi", "bro", "sir", "bhai", "acha", "accha",
}

_PINNED_RE = re.compile(
    r"\b(i want|i need|i'd like|i would like|i prefer|please (?:use|make|keep|don'?t)"
    r"|don'?t|do not|make sure|remember that|my name is|i am|i'm|use .+ not )\b",
    re.IGNORECASE,
)

_RECALL_RE = re.compile(
    r"\b(what did i say|as i (?:said|told|mentioned)|earlier i|like i said"
    r"|remember what i|go back to that|the thing i mentioned)\b",
    re.IGNORECASE,
)


def new_call_memory() -> dict[str, Any]:
    return {"pinned_facts": [], "running_summary": None, "summary_last_updated_turn": 0}


def _is_filler(text: str) -> bool:
    words = [w for w in re.findall(r"[\w']+", text.lower())]
    if len(words) < 3:
        return True
    return all(w in _FILLER for w in words)


def extract_pinned_facts(
    user_text: str, existing: list[str], *, max_pinned: int
) -> list[str]:
    """Return the updated pinned-fact list after considering one user turn."""
    cleaned = " ".join((user_text or "").split())
    if not cleaned or _is_filler(cleaned) or not _PINNED_RE.search(cleaned):
        return existing
    fact = cleaned[:160]
    lowered = {f.lower() for f in existing}
    if fact.lower() in lowered:
        return existing
    updated = existing + [fact]
    # Keep the most recent facts within the cap.
    return updated[-max_pinned:]


def recall_triggered(user_text: str) -> bool:
    return bool(_RECALL_RE.search(user_text or ""))


def select_recall_snippets(
    transcript: list[dict[str, Any]], *, max_snippets: int
) -> list[str]:
    """Pick a couple of earlier user utterances as recall snippets."""
    user_lines = [
        " ".join(str(ev.get("text", "")).split())
        for ev in transcript
        if ev.get("role") == "user" and ev.get("text")
    ]
    # Earliest user lines are the most likely "earlier context".
    return user_lines[:max_snippets]


def should_update_summary(turn_count: int, memory_cfg: dict[str, Any]) -> bool:
    enable_after = int(memory_cfg.get("enable_running_summary_after_turn", 10))
    every = max(1, int(memory_cfg.get("summary_update_every_turns", 5)))
    if turn_count < enable_after:
        return False
    return turn_count % every == 0


def build_running_summary(
    transcript: list[dict[str, Any]],
    *,
    recent_turn_pairs: int,
    max_summary_tokens: int,
) -> str:
    """Deterministically compact the older portion of the call into a summary.

    Older = everything except the most recent ``recent_turn_pairs`` exchanges.
    Cheap string work only (a token ~ 4 chars used as the length budget).
    """
    user_lines = [
        " ".join(str(ev.get("text", "")).split())
        for ev in transcript
        if ev.get("role") == "user" and ev.get("text")
    ]
    older = user_lines[: max(0, len(user_lines) - recent_turn_pairs)]
    if not older:
        return ""
    body = "; ".join(older)
    budget = max_summary_tokens * 4
    if len(body) > budget:
        body = body[:budget].rsplit(" ", 1)[0] + "…"
    return f"Earlier in this call the user said: {body}"


def update_summary_if_due(
    memory: dict[str, Any],
    *,
    turn_count: int,
    transcript: list[dict[str, Any]],
    memory_cfg: dict[str, Any],
) -> dict[str, Any]:
    """Post-turn summary refresh (never called before a reply)."""
    if not should_update_summary(turn_count, memory_cfg):
        return memory
    summary = build_running_summary(
        transcript,
        recent_turn_pairs=int(memory_cfg.get("recent_turn_pairs", 6)),
        max_summary_tokens=int(memory_cfg.get("max_summary_tokens", 250)),
    )
    if summary:
        memory["running_summary"] = summary
        memory["summary_last_updated_turn"] = turn_count
    return memory


def build_system_block(
    base_system_prompt: str,
    *,
    language_policy: str,
    language_hint: str | None = None,
    pinned_facts: list[str],
    running_summary: str | None,
    recall_snippets: list[str],
) -> str:
    """Assemble the dynamic system message for one turn.

    ``language_policy`` is the always-injected, code-owned <en>/<hi> contract;
    ``language_hint`` is an optional weak STT-derived hint. The persona prompt
    stays user-editable while the parser contract cannot be edited away.
    """
    parts = [base_system_prompt.strip(), language_policy.strip()]
    if language_hint:
        parts.append(language_hint.strip())
    if pinned_facts:
        parts.append("Known facts for this call:\n- " + "\n- ".join(pinned_facts))
    if running_summary:
        parts.append(running_summary)
    if recall_snippets:
        parts.append("Earlier context the user referenced:\n- " + "\n- ".join(recall_snippets))
    return "\n\n".join(p for p in parts if p)


def trim_recent_messages(
    messages: list[Any], *, recent_turn_pairs: int
) -> list[Any]:
    """Keep only the most recent ``2 * recent_turn_pairs`` non-system messages."""
    non_system = [m for m in messages if _role(m) != "system"]
    keep = max(0, recent_turn_pairs * 2)
    return non_system[-keep:] if keep else []


def _role(message: Any) -> str:
    if isinstance(message, dict):
        return str(message.get("role", ""))
    return str(getattr(message, "role", ""))
