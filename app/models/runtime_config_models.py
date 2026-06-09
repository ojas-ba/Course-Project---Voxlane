"""Typed models for the single global V3 runtime configuration.

There is exactly one runtime config document (``_id="global"``) in MongoDB.
Only secrets/infra live in ``.env``; all tuning/behavior lives here and is
editable from the frontend. These models are mutable (editable) by design,
unlike the frozen V2 profile models.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.config import Settings

GLOBAL_CONFIG_ID = "global"

DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful, friendly assistant on a live phone call. Keep replies "
    "natural, concise, and genuinely useful. Because this is a phone call, keep "
    "answers short — usually 1-2 sentences — and ask one question at a time. "
    "Give a longer answer only when the caller explicitly asks for detail."
)
DEFAULT_GREETING_PROMPT = (
    "Generate a short, warm English opening greeting for a phone call. The "
    "assistant should introduce itself briefly and invite the user to speak. "
    "Keep it under one sentence."
)


class STTCfg(BaseModel):
    model: str = "flux-general-multi"
    language_hints: list[str] = Field(default_factory=lambda: ["en", "hi"])
    eot_threshold: float = 0.65
    eot_timeout_ms: int = 700
    # Connect the Flux websocket in the background on start so the opening
    # greeting isn't blocked ~1s waiting for STT (see deferred_flux_stt.py).
    defer_connect_on_start: bool = True
    # Pipecat's POST-STT user-turn debounce before the LLM fires — NOT Deepgram
    # endpointing (that is eot_timeout_ms). Pipecat's ExternalUserTurnStopStrategy
    # default is 500ms; because UserStoppedSpeakingFrame (a SystemFrame) overtakes
    # the queued finalized TranscriptionFrame, the immediate trigger misses and
    # THIS timeout is what actually fires the turn — so it is a fixed per-turn
    # pre-LLM delay. Default 500 preserves current behavior; lower = less latency
    # but too low risks splitting a turn whose final transcript arrives in pieces.
    turn_stop_timeout_ms: int = Field(default=500, ge=100, le=1000)
    # --- EagerEndOfTurn telemetry (Stage 1; OFF by default — no behavior change) ---
    # When enabled, Deepgram Flux is asked with this threshold and begins emitting
    # EagerEndOfTurn/TurnResumed events. Stage 1 ONLY measures the would-be LLM head
    # start; it never starts a speculative draft and never speaks early. The Flux
    # rule is eager_eot_threshold <= eot_threshold. None = not sent (eager off).
    enable_eager_eot: bool = False
    eager_eot_threshold: float | None = Field(default=None, ge=0.3, le=0.9)

    @model_validator(mode="after")
    def _validate_eager_threshold(self) -> "STTCfg":
        # Only constrains when eager is actually enabled, so it is a no-op for the
        # disabled default and for all existing stored configs.
        if self.enable_eager_eot:
            if self.eager_eot_threshold is None:
                # Without a threshold the feature would silently collect nothing
                # (Flux is never asked for eager events) — surface that as a save
                # error instead of a config that looks on but does nothing.
                raise ValueError("eager_eot_threshold is required when enable_eager_eot is true")
            if self.eager_eot_threshold > self.eot_threshold:
                raise ValueError(
                    "eager_eot_threshold must be <= eot_threshold "
                    f"(got {self.eager_eot_threshold} > {self.eot_threshold})"
                )
        return self


class LLMCfg(BaseModel):
    provider: Literal["cerebras"] = "cerebras"
    model: str = "gpt-oss-120b"
    temperature: float = 0.7
    max_tokens: int = 160
    request_timeout_seconds: float = 10.0
    # --- Stage-2 speculative LLM (OFF by default — no behavior change) ---
    # When enabled, an out-of-band Cerebras draft is started on Deepgram Flux
    # EagerEndOfTurn and, if the normalized eager transcript matches the final
    # transcript at EndOfTurn, released/continued in place of the normal LLM call
    # (hiding the turn-stop debounce + eager->final gap). It is triggered by eager
    # events, so it REQUIRES stt.enable_eager_eot — enforced by RuntimeConfig's
    # cross-field validator. The flag lives here because it is LLM behavior.
    enable_speculative_llm: bool = False


class TTSCfg(BaseModel):
    provider: Literal["cartesia"] = "cartesia"
    model: str = "sonic-3.5"
    voice_id: str = ""
    language_mode: Literal["per_turn", "fixed"] = "per_turn"


class PromptCfg(BaseModel):
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    greeting_prompt: str = DEFAULT_GREETING_PROMPT


class MemoryCfg(BaseModel):
    recent_turn_pairs: int = 6
    enable_running_summary_after_turn: int = 10
    summary_update_every_turns: int = 5
    max_summary_tokens: int = 250
    max_pinned_facts: int = 12
    max_recall_snippets: int = 2
    # Min content-token overlap for a recall snippet to be injected (an explicit
    # "you told me…/pehle…" relaxes this to 1). Higher = less noise, fewer recalls.
    recall_min_overlap: int = Field(default=2, ge=1, le=6)
    # Aggregate budget (token ~ 4 chars) for the dynamic memory sections (facts +
    # recall + running summary) so the per-turn system block can't inflate TTFB.
    max_memory_block_tokens: int = Field(default=600, ge=100, le=2000)
    # DEPRECATED: superseded by always-on lexical recall (search always runs;
    # injection is gated by score, not by this flag). Kept for back-compat.
    recall_only_when_user_asks: bool = True


class OpsCfg(BaseModel):
    debug_log_level: str = "INFO"


class RuntimeConfig(BaseModel):
    """The single global runtime config document."""

    version: int = 1
    stt: STTCfg = Field(default_factory=STTCfg)
    llm: LLMCfg = Field(default_factory=LLMCfg)
    tts: TTSCfg = Field(default_factory=TTSCfg)
    prompt: PromptCfg = Field(default_factory=PromptCfg)
    memory: MemoryCfg = Field(default_factory=MemoryCfg)
    ops: OpsCfg = Field(default_factory=OpsCfg)

    @model_validator(mode="after")
    def _validate_speculative_requires_eager(self) -> "RuntimeConfig":
        # Stage-2 speculation is triggered by Flux EagerEndOfTurn events, which are
        # only emitted when stt.enable_eager_eot is on (and a threshold is set).
        # Enabling speculation without eager would be silently inert, so surface it
        # as a save error rather than a config that looks on but does nothing.
        # No-op when speculation is off, so all existing stored configs validate.
        if self.llm.enable_speculative_llm and not self.stt.enable_eager_eot:
            raise ValueError(
                "llm.enable_speculative_llm requires stt.enable_eager_eot "
                "(speculation is triggered by EagerEndOfTurn events)"
            )
        return self

    @classmethod
    def default(cls, settings: Settings) -> "RuntimeConfig":
        """Build the default config, seeding the TTS voice from env (Phase 1)."""

        return cls(
            tts=TTSCfg(voice_id=settings.tts_voice_id, model=settings.tts_model),
        )
