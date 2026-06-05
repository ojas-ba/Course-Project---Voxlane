"""LLM-led outbound greeting generation for voice calls."""

from __future__ import annotations

import re

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
from app.services import language as lang

MAX_ASSISTANT_TEXT_CHARS = 320


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
    return True


def validate_greeting_text(text: str) -> str:
    """Validate a generated greeting and return normalized spoken text.

    V3: no readiness/consent gate. The greeting only needs to be a clean,
    non-empty, single spoken line. Invalid output raises ValueError, which the
    outbound flow treats as a hard failure (the call is NOT placed).
    """

    cleaned = _normalize_text(text)
    # The greeting bypasses LanguagePrefixProcessor (it's spoken via TTSSpeakFrame),
    # so strip any leaked <en>/<hi> tag here before it is spoken or stored. The
    # greeting prompt's "do not include <en>" instruction primes small models to
    # emit exactly that, so we never rely on the prompt alone (LANG-1).
    cleaned = lang.strip_language_prefix_tag(cleaned)
    if not _is_valid_assistant_text(cleaned):
        raise ValueError("generated greeting failed voice safety validation")
    return cleaned


async def generate_outbound_greeting_text(
    *,
    snapshot: ConfigSnapshot,
    settings: Settings,
    to_number: str | None = None,
) -> str:
    """Generate and validate the LLM opening greeting before dialing.

    Raises on empty/invalid output or any provider error. The caller must not
    place the call when this raises — there is no static/canned fallback.
    """

    system_prompt = (
        "You generate ONLY the assistant's spoken opening greeting for an "
        "outbound voice call. Return only the words the assistant should speak "
        "— no labels, no quotes, no stage directions."
    )
    user_prompt = (
        f"Assistant persona:\n{snapshot['SYSTEM_PROMPT']}\n\n"
        f"Greeting instructions:\n{snapshot['GREETING_PROMPT']}\n\n"
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
