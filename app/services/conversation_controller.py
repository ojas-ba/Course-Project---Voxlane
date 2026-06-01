"""LLM-led conversation controller for outbound voice calls."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from loguru import logger
from pydantic import BaseModel, Field, ValidationError
from pipecat.frames.frames import (
    EndFrame,
    Frame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    StartFrame,
    TextFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.cerebras.llm import CerebrasLLMService, CerebrasLLMSettings

from app.config import Settings
from app.runtime import ConfigSnapshot

ConversationPhase = Literal["greeting", "awaiting_readiness", "task", "closing"]
TurnPhase = Literal["readiness", "task", "closing"]
UserIntent = Literal["ready", "not_now", "question", "answer", "unclear"]

SAFE_CLARIFICATION_TEXT = "Sorry, I want to make sure I got that right. Could you repeat that briefly?"
SAFE_GREETING_FALLBACK = "Hello. I am calling for a short conversation. Is now a good time?"
MAX_ASSISTANT_TEXT_CHARS = 320


class TurnDecision(BaseModel):
    """Validated internal decision for one assistant turn."""

    phase: TurnPhase
    assistant_text: str = Field(min_length=1, max_length=MAX_ASSISTANT_TEXT_CHARS)
    user_intent: UserIntent
    should_end_call: bool = False


@dataclass
class ConversationRuntimeState:
    """Serializable-lite conversation state held for one live pipeline."""

    phase: ConversationPhase
    greeting_audio_pending: bool = False
    last_user_text: str | None = None
    recent_events: list[dict[str, str]] = field(default_factory=list)


class _ContextSource(FrameProcessor):
    def __init__(self, context: LLMContext) -> None:
        super().__init__()
        self._context = context

    async def process_frame(
        self,
        frame: Frame,
        direction: FrameDirection = FrameDirection.DOWNSTREAM,
    ) -> None:
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)
        if isinstance(frame, StartFrame):
            await self.push_frame(LLMContextFrame(context=self._context))


class _TextCollector(FrameProcessor):
    def __init__(self) -> None:
        super().__init__()
        self.text_parts: list[str] = []

    async def process_frame(
        self,
        frame: Frame,
        direction: FrameDirection = FrameDirection.DOWNSTREAM,
    ) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TextFrame):
            self.text_parts.append(frame.text)
        elif isinstance(frame, LLMFullResponseEndFrame):
            await self.pipeline_task.queue_frame(EndFrame())
        await self.push_frame(frame, direction)

    @property
    def text(self) -> str:
        return "".join(self.text_parts).strip()


def _normalize_text(text: str) -> str:
    return " ".join(text.split())


def _build_cerebras_llm(snapshot: ConfigSnapshot, settings: Settings) -> CerebrasLLMService:
    return CerebrasLLMService(
        api_key=settings.cerebras_api_key,
        settings=CerebrasLLMSettings(
            model=snapshot["LLM_MODEL"],
            temperature=snapshot["LLM_TEMPERATURE"],
        ),
    )


async def _run_llm_text_generation(
    *,
    snapshot: ConfigSnapshot,
    settings: Settings,
    messages: list[dict[str, str]],
) -> str:
    """Run a small isolated Pipecat LLM pipeline and collect text output."""

    context = LLMContext(messages=messages)
    source = _ContextSource(context)
    llm = _build_cerebras_llm(snapshot, settings)
    collector = _TextCollector()

    pipeline = Pipeline([source, llm, collector])
    task = PipelineTask(pipeline, params=PipelineParams(allow_interruptions=False))
    runner = PipelineRunner(handle_sigint=False)
    await runner.run(task)

    if not collector.text:
        raise ValueError("LLM generation returned no text")
    return collector.text


def _extract_json_object(text: str) -> dict[str, Any] | None:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _has_simulated_speaker(text: str) -> bool:
    return re.search(
        r"\b(user|candidate|caller|interviewer|assistant|bot)\s*:",
        text,
        flags=re.IGNORECASE,
    ) is not None


def _question_count(text: str) -> int:
    return text.count("?")


def _is_valid_assistant_text(text: str) -> bool:
    cleaned = _normalize_text(text)
    if not cleaned or len(cleaned) > MAX_ASSISTANT_TEXT_CHARS:
        return False
    if _has_simulated_speaker(cleaned):
        return False
    if _question_count(cleaned) > 1:
        return False
    return True


def validate_turn_decision(raw: dict[str, Any]) -> TurnDecision:
    """Validate and safety-check a model-generated TurnDecision."""

    decision = TurnDecision.model_validate(raw)
    decision.assistant_text = _normalize_text(decision.assistant_text)
    if not _is_valid_assistant_text(decision.assistant_text):
        raise ValueError("assistant_text failed voice safety validation")
    return decision


def safe_turn_decision(*, phase: TurnPhase = "task") -> TurnDecision:
    """Return a safe clarification decision for malformed model output."""

    return TurnDecision(
        phase=phase,
        assistant_text=SAFE_CLARIFICATION_TEXT,
        user_intent="unclear",
        should_end_call=False,
    )


def validate_greeting_text(text: str) -> str:
    """Validate a generated greeting and return normalized spoken text."""

    cleaned = _normalize_text(text)
    lowered = cleaned.lower()
    invalid_phrases = (
        "go ahead",
        "works for me",
        "this works for me",
        "yes,",
        "yeah,",
        "i am ready",
        "i'm ready",
    )
    readiness_cues = ("good time", "available", "ready", "continue", "speak", "talk")

    if not _is_valid_assistant_text(cleaned):
        raise ValueError("generated greeting failed voice safety validation")
    if any(phrase in lowered for phrase in invalid_phrases):
        raise ValueError(f"generated greeting sounded like callee consent: {cleaned}")
    if "?" not in lowered and not any(cue in lowered for cue in readiness_cues):
        raise ValueError(f"generated greeting did not ask readiness: {cleaned}")
    return cleaned


async def generate_outbound_greeting_text(
    *,
    snapshot: ConfigSnapshot,
    settings: Settings,
    to_number: str | None = None,
) -> str:
    """Generate and validate the personalized outbound greeting before dialing."""

    system_prompt = (
        "You generate only the assistant's opening greeting for an outbound voice call. "
        "Do not answer as the callee. Do not begin the task. "
        "Ask whether the person is available or ready to continue. "
        "Return only the words the assistant should speak."
    )
    user_prompt = (
        f"Scenario instructions:\n{snapshot['SYSTEM_PROMPT']}\n\n"
        f"Greeting instructions:\n{snapshot['INITIAL_GREETING_INSTRUCTIONS']}\n\n"
        f"Fallback greeting style:\n{snapshot['INITIAL_GREETING_FALLBACK_TEXT']}\n\n"
        f"Destination number, if useful: {to_number or 'unknown'}"
    )
    raw = await _run_llm_text_generation(
        snapshot=snapshot,
        settings=settings,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    return validate_greeting_text(raw)


async def decide_next_turn(
    *,
    user_text: str,
    state: ConversationRuntimeState,
    snapshot: ConfigSnapshot,
    settings: Settings,
) -> TurnDecision:
    """Ask the LLM for a structured decision for one user turn."""

    history = "\n".join(
        f"{event['role']}[{event['phase']}]: {event['text']}"
        for event in state.recent_events[-8:]
    )
    system_prompt = (
        "You are the internal controller for a generalized outbound voice agent. "
        "Understand the user's latest message and decide the next assistant move. "
        "Return strict JSON only with keys: phase, assistant_text, user_intent, should_end_call. "
        "phase must be readiness, task, or closing. "
        "user_intent must be ready, not_now, question, answer, or unclear. "
        "assistant_text must be natural speech for the assistant only. "
        "Never include user/candidate/caller lines. Never simulate the user. "
        "Ask at most one question."
    )
    user_prompt = (
        f"Scenario instructions:\n{snapshot['SYSTEM_PROMPT']}\n\n"
        f"Current phase: {state.phase}\n"
        f"Recent transcript:\n{history or '(none)'}\n\n"
        f"Latest user message:\n{user_text}"
    )

    try:
        raw = await _run_llm_text_generation(
            snapshot=snapshot,
            settings=settings,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        parsed = _extract_json_object(raw)
        if parsed is None:
            raise ValueError(f"LLM did not return JSON: {raw}")
        return validate_turn_decision(parsed)
    except (ValidationError, ValueError, Exception) as exc:
        logger.warning("Turn decision failed validation; using safe fallback", error=str(exc))
        fallback_phase: TurnPhase = "readiness" if state.phase == "awaiting_readiness" else "task"
        return safe_turn_decision(phase=fallback_phase)


def apply_turn_decision(state: ConversationRuntimeState, decision: TurnDecision) -> None:
    """Update runtime conversation phase from a validated TurnDecision."""

    if decision.should_end_call or decision.phase == "closing" or decision.user_intent == "not_now":
        state.phase = "closing"
    elif decision.phase == "readiness":
        state.phase = "awaiting_readiness"
    else:
        state.phase = "task"
