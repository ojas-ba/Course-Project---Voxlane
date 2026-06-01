"""Typed behavior configuration models for Voice Agent V2 profiles."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class STTConfig(BaseModel):
    """Deepgram Flux speech-to-text behavior configuration."""

    model_config = ConfigDict(frozen=True)

    id: str
    provider: Literal["deepgram"] = "deepgram"
    model: str = "flux-general-en"
    sample_rate: Literal[16000] = 16000
    eot_threshold: float = 0.75
    eot_timeout_ms: int = 1500
    eager_eot_threshold: float | None = None
    language_hint: list[str] | None = None


class LLMConfig(BaseModel):
    """Cerebras LLM behavior configuration."""

    model_config = ConfigDict(frozen=True)

    id: str
    provider: Literal["cerebras"] = "cerebras"
    model: str
    temperature: float = 0.2
    max_tokens: int = 160
    timeout_secs: float = 8.0
    max_retries: int = 1
    structured_outputs: bool = True
    reasoning_effort: str | None = "low"


class TTSConfig(BaseModel):
    """Cartesia text-to-speech behavior configuration."""

    model_config = ConfigDict(frozen=True)

    id: str
    provider: Literal["cartesia"] = "cartesia"
    model: str
    voice_id: str
    sample_rate: Literal[16000] = 16000
    max_buffer_delay_ms: int = 0
    text_aggregation_mode: str = "sentence"


class GreetingConfig(BaseModel):
    """Outbound greeting behavior configuration."""

    model_config = ConfigDict(frozen=True)

    mode: Literal["static", "llm_generated"] = "llm_generated"
    static_text: str
    generation_prompt: str | None = None
    generation_instructions: str | None = None
    generation_timeout_secs: float = 3.0
    fallback_text: str


class FallbackPolicy(BaseModel):
    """Default voice fallback behavior for unclear or unsupported turns."""

    model_config = ConfigDict(frozen=True)

    clarification_text: str = (
        "Sorry, I want to make sure I got that right. Could you repeat that briefly?"
    )
    refusal_text: str = "No problem. We can stop here. Thank you for your time."
    stop_text: str = "Understood. I will end the call now. Thank you."
    max_attempts: int = 2


class SlotDefinition(BaseModel):
    """A configured piece of information the bot may collect later."""

    model_config = ConfigDict(frozen=True)

    id: str
    label: str
    description: str | None = None
    type: Literal[
        "text",
        "number",
        "boolean",
        "categorical",
        "phone",
        "email",
        "date",
        "datetime",
    ] = "text"
    required: bool = True
    allowed_values: list[str] | None = None
    question: str
    reprompt: str | None = None
    confirmation_required: bool = False
    max_attempts: int = 2


class FlowNode(BaseModel):
    """A node in a future flow-driven conversation definition."""

    model_config = ConfigDict(frozen=True)

    id: str
    type: Literal[
        "message",
        "readiness_check",
        "slot_collection",
        "question_answer",
        "confirmation",
        "closing",
        "handoff",
        "custom",
    ]
    slot_id: str | None = None
    assistant_text: str | None = None
    next_node_id: str | None = None
    on_success_node_id: str | None = None
    on_skip_node_id: str | None = None
    on_refusal_node_id: str | None = None
    on_error_node_id: str | None = None


class FlowDefinition(BaseModel):
    """Future-facing task flow definition for a bot profile."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    version: str
    description: str | None = None
    use_case_type: Literal[
        "interview",
        "support_intake",
        "lead_qualification",
        "appointment_booking",
        "feedback_collection",
        "custom",
    ]
    slots: list[SlotDefinition] = Field(default_factory=list)
    nodes: list[FlowNode] = Field(default_factory=list)
    start_node_id: str
    fallback_rules: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class BotProfile(BaseModel):
    """Top-level behavior profile used to seed per-call snapshots."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    description: str | None = None
    system_prompt: str
    flow_id: str
    stt_config_id: str
    llm_config_id: str
    tts_config_id: str
    greeting_config: GreetingConfig
    fallback_policy: FallbackPolicy
    is_active: bool = True
    created_at: datetime
    updated_at: datetime


class CallConfigSnapshot(BaseModel):
    """JSON-safe legacy-compatible config snapshot for one call."""

    model_config = ConfigDict(extra="allow", frozen=True)

    SYSTEM_PROMPT: str
    LLM_MODEL: str
    LLM_TEMPERATURE: float
    TTS_VOICE_ID: str
    TTS_MODEL: str
    INITIAL_GREETING_PROMPT: str
    INITIAL_GREETING_INSTRUCTIONS: str
    INITIAL_GREETING_FALLBACK_TEXT: str
    SAMPLE_RATE: Literal[16000] = 16000
    snapshot_version: Literal["v2_phase1"] = "v2_phase1"
    bot_profile: dict[str, Any]
    stt_config: dict[str, Any]
    llm_config: dict[str, Any]
    tts_config: dict[str, Any]
    greeting_config: dict[str, Any]
    fallback_policy: dict[str, Any]
    flow_definition: dict[str, Any]
    profile_resolution: dict[str, Any]
