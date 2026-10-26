"""Typed behavior configuration persisted locally and snapshotted per conversation."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator



DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful, friendly assistant in a live browser conversation. "
    "Keep replies natural, concise, and useful, usually one or two sentences. "
    "Ask one question at a time. Give more detail when the user asks for it."
)
DEFAULT_GREETING_TEXT = "Hello, how can I help you today?"


class RuntimeModel(BaseModel):
    """Reject misspelled configuration fields rather than silently ignoring them."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class STTCfg(RuntimeModel):
    """Flux audio and turn boundary settings."""
    model: Literal["flux-general-multi", "flux-general-en"] = "flux-general-multi"
    language_hints: list[str] = Field(default_factory=lambda: ["en", "hi"])
    eot_threshold: float = Field(default=0.65, ge=0.5, le=1.0)
    eot_timeout_ms: int = Field(default=700, ge=500, le=60000)
    # Overlap Flux connection with browser setup; the greeting still waits for readiness.
    defer_connect_on_start: bool = True
    # Pipecat's POST-STT user-turn debounce before the LLM fires — NOT Deepgram
    # endpointing (that is eot_timeout_ms). Pipecat's ExternalUserTurnStopStrategy
    # default is 500ms; because UserStoppedSpeakingFrame (a SystemFrame) overtakes
    # the queued finalized TranscriptionFrame, the immediate trigger misses and
    # THIS timeout can fire the turn. Measure frame ordering rather than assuming
    # an identical delay on every turn. Default 500 preserves existing tuning;
    # lower = less latency
    # but too low risks splitting a turn whose final transcript arrives in pieces.
    turn_stop_timeout_ms: int = Field(default=500, ge=100, le=1000)
    # --- EagerEndOfTurn telemetry (Stage 1; OFF by default — no behavior change) ---
    # When enabled, Deepgram Flux is asked with this threshold and begins emitting
    # EagerEndOfTurn/TurnResumed events. Stage 1 ONLY measures the would-be LLM head
    # start; it never starts a speculative draft and never speaks early. The Flux
    # rule is eager_eot_threshold <= eot_threshold. None = not sent (eager off).
    enable_eager_eot: bool = False
    eager_eot_threshold: float | None = Field(default=None, ge=0.3, le=0.9)

    @field_validator("language_hints")
    @classmethod
    def supported_hints(cls, hints: list[str]) -> list[str]:
        """Validate the language codes supported by Flux Multilingual."""
        supported = {"en", "es", "fr", "de", "hi", "ru", "pt", "ja", "it", "nl"}
        if any(h not in supported for h in hints):
            raise ValueError("Unsupported Flux language hint")
        return list(dict.fromkeys(hints))

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


class LLMCfg(RuntimeModel):
    """Cerebras model and bounded generation settings."""
    provider: Literal["cerebras"] = "cerebras"
    model: Literal["qwen-3.8-27b", "gpt-oss-120b"] = "qwen-3.8-27b"
    reasoning_effort: Literal["none", "low", "medium", "high"] = "none"
    temperature: float = Field(default=0.7, ge=0, le=2)
    max_tokens: int = Field(default=1024, ge=16, le=2048)
    request_timeout_seconds: float = Field(default=10.0, ge=1, le=60)
    # --- Stage-2 speculative LLM (OFF by default — no behavior change) ---
    # When enabled, an out-of-band Cerebras draft is started on Deepgram Flux
    # EagerEndOfTurn and, if the normalized eager transcript matches the final
    # transcript at EndOfTurn, released/continued in place of the normal LLM call
    # (hiding the turn-stop debounce + eager->final gap). It is triggered by eager
    # events, so it REQUIRES stt.enable_eager_eot — enforced by RuntimeConfig's
    # cross-field validator. The flag lives here because it is LLM behavior.
    enable_speculative_llm: bool = False


class TTSCfg(RuntimeModel):
    """Cartesia model and user-selected voice."""
    provider: Literal["cartesia"] = "cartesia"
    model: str = Field(default="sonic-3.6-2026-08-27", min_length=1)
    voice_id: str = ""
    language_mode: Literal["per_turn"] = "per_turn"


class PromptCfg(RuntimeModel):
    """Persona and directly spoken opening text."""
    system_prompt: str = Field(default=DEFAULT_SYSTEM_PROMPT, min_length=1)
    greeting_text: str = Field(default=DEFAULT_GREETING_TEXT, min_length=1, max_length=500)


class MemoryCfg(RuntimeModel):
    """Bounded in-conversation memory without another storage service."""

    recent_turn_pairs: int = Field(default=6, ge=1, le=30)
    enable_running_summary_after_turn: int = Field(default=10, ge=1, le=1000)
    summary_update_every_turns: int = Field(default=5, ge=1, le=100)
    max_summary_tokens: int = Field(default=250, ge=1, le=2000)
    max_pinned_facts: int = Field(default=12, ge=0, le=100)
    max_recall_snippets: int = Field(default=2, ge=0, le=10)
    # Min content-token overlap for a recall snippet to be injected (an explicit
    # "you told me…/pehle…" relaxes this to 1). Higher = less noise, fewer recalls.
    recall_min_overlap: int = Field(default=2, ge=1, le=6)
    # Aggregate budget (token ~ 4 chars) for the dynamic memory sections (facts +
    # recall + running summary) so the per-turn system block can't inflate TTFB.
    max_memory_block_tokens: int = Field(default=600, ge=100, le=2000)
    # DEPRECATED: superseded by always-on lexical recall (search always runs;
    # injection is gated by score, not by this flag). Kept for back-compat.
    recall_only_when_user_asks: bool = True


class OpsCfg(RuntimeModel):
    """Frontend-editable local readiness and shutdown budgets."""
    debug_log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    browser_ready_timeout_secs: float = Field(default=30.0, ge=5, le=120)
    shutdown_timeout_secs: float = Field(default=30.0, ge=1, le=60)


class RuntimeConfig(RuntimeModel):
    """The single global runtime config document."""

    version: int = 1
    stt: STTCfg = Field(default_factory=STTCfg)
    llm: LLMCfg = Field(default_factory=LLMCfg)
    tts: TTSCfg = Field(default_factory=TTSCfg)
    prompt: PromptCfg = Field(default_factory=PromptCfg)
    memory: MemoryCfg = Field(default_factory=MemoryCfg)
    ops: OpsCfg = Field(default_factory=OpsCfg)

    @model_validator(mode="after")
    def validate_browser_config(self) -> "RuntimeConfig":
        """Validate dependencies between generation and turn-boundary settings."""
        if self.llm.enable_speculative_llm and not self.stt.enable_eager_eot:
            raise ValueError("Speculative LLM requires enable_eager_eot")
        if self.llm.model == "gpt-oss-120b" and self.llm.reasoning_effort == "none":
            raise ValueError("GPT-OSS reasoning_effort must be low, medium, or high")
        if not self.prompt.greeting_text.strip() or not self.prompt.system_prompt.strip():
            raise ValueError("Greeting and system prompt cannot be blank")
        return self

    @classmethod
    def default(cls, settings: object = None) -> "RuntimeConfig":
        """Create canonical defaults without reading behavior from the environment."""
        return cls()
