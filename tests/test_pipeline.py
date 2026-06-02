from unittest.mock import AsyncMock, patch

import inspect
import pytest
from pipecat.frames.frames import BotStoppedSpeakingFrame, TTSSpeakFrame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameProcessor

from app.config import get_settings
from app.models.conversation_models import (
    ConversationState,
    RuntimeAction,
    RuntimeResult,
)
from app.runtime import build_config_snapshot, create_session, delete_session, get_session
from app.services.conversation_runtime import ConversationRuntime
from app.services.pipecat_pipeline import (
    RUNTIME_ERROR_FALLBACK_TEXT,
    UserConversationProcessor,
    _build_pipeline_processors,
)
from app.services import pipecat_pipeline


class DummyProcessor(FrameProcessor):
    pass


class DummyTransport:
    def input(self):
        return DummyProcessor()

    def output(self):
        return DummyProcessor()


class DummyAggregatorPair:
    def user(self):
        return DummyProcessor()

    def assistant(self):
        return DummyProcessor()


class FakeConversationRuntime:
    def __init__(
        self,
        *,
        call_sid: str = "call_123",
        stream_sid: str | None = "stream_456",
        phase: str = "readiness",
        result: RuntimeResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self.state = ConversationState(call_sid=call_sid, stream_sid=stream_sid, phase=phase)
        self.result = result or _runtime_result(call_sid=call_sid, stream_sid=stream_sid)
        self.error = error
        self.handle_calls = []
        self.set_phase_calls = []

    def snapshot_state(self) -> ConversationState:
        return self.state.model_copy(deep=True)

    def set_phase(self, phase: str) -> None:
        self.set_phase_calls.append(phase)
        self.state.phase = phase

    def handle_turn(self, turn):
        self.handle_calls.append(turn)
        if self.error is not None:
            raise self.error
        self.state = self.result.state.model_copy(deep=True)
        return self.result


def _runtime_result(
    *,
    call_sid: str = "call_123",
    stream_sid: str | None = "stream_456",
    assistant_text: str = "Assistant speech.",
    user_transcript_text: str | None = "Runtime user text.",
    assistant_transcript_text: str | None = "Runtime assistant transcript.",
    phase: str = "collecting",
    should_end_call: bool = False,
) -> RuntimeResult:
    return RuntimeResult(
        action=RuntimeAction(
            action_type="close" if should_end_call else "acknowledge",
            should_end_call=should_end_call,
            reason="test",
        ),
        assistant_text=assistant_text,
        should_end_call=should_end_call,
        state=ConversationState(call_sid=call_sid, stream_sid=stream_sid, phase=phase),
        user_transcript_text=user_transcript_text,
        assistant_transcript_text=assistant_transcript_text,
    )


def _processor(
    *,
    runtime: FakeConversationRuntime | None = None,
    call_sid: str = "call_123",
    stream_sid: str = "stream_456",
    greeting_audio_pending: bool = False,
) -> UserConversationProcessor:
    return UserConversationProcessor(
        call_sid=call_sid,
        stream_sid=stream_sid,
        conversation_runtime=runtime
        or FakeConversationRuntime(call_sid=call_sid, stream_sid=stream_sid),
        greeting_audio_pending=greeting_audio_pending,
    )


def test_pipeline_processors_create_conversation_runtime_and_initial_snapshot():
    settings = get_settings()
    transport = DummyTransport()
    snapshot = build_config_snapshot(settings)
    create_session("call_123", "stream_456", settings, config_snapshot=snapshot)

    try:
        with (
            patch("app.services.pipecat_pipeline._build_stt", return_value=DummyProcessor()),
            patch("app.services.pipecat_pipeline._build_tts", return_value=DummyProcessor()),
            patch("app.services.pipecat_pipeline._build_context", return_value=object()),
            patch(
                "app.services.pipecat_pipeline._build_aggregator_pair",
                return_value=DummyAggregatorPair(),
            ),
        ):
            processors = _build_pipeline_processors(
                transport=transport,
                snapshot=snapshot,
                settings=settings,
                call_sid="call_123",
                stream_sid="stream_456",
            )

        assert isinstance(processors[2], UserConversationProcessor)
        assert len(processors) == 7
        assert isinstance(processors[2]._conversation_runtime, ConversationRuntime)
        session = get_session("call_123")
        assert session is not None
        assert session["conversation_state"]["phase"] == "readiness"
        assert not isinstance(session["conversation_state"], ConversationRuntime)
    finally:
        delete_session("call_123")


@pytest.mark.asyncio
async def test_outbound_early_hello_during_greeting_is_ignored():
    settings = get_settings()
    create_session("call_123", "stream_456", settings)

    try:
        runtime = FakeConversationRuntime(phase="greeting")
        processor = _processor(runtime=runtime, greeting_audio_pending=True)
        processor.push_frame = AsyncMock()

        await processor.process_frame(
            TranscriptionFrame(
                text="Hello?",
                user_id="user",
                timestamp="now",
                finalized=True,
            )
        )

        session = get_session("call_123")
        assert session is not None
        assert session["transcript"] == []
        assert runtime.state.phase == "greeting"
        assert runtime.handle_calls == []
        assert processor.last_turn_collection_result is not None
        assert processor.last_turn_collection_result.ignored_reason == "greeting_pending"
        processor.push_frame.assert_not_awaited()
    finally:
        delete_session("call_123")


@pytest.mark.asyncio
async def test_bot_stopped_speaking_flips_readiness_only_when_greeting_pending():
    settings = get_settings()
    create_session("call_123", "stream_456", settings)

    try:
        runtime = FakeConversationRuntime(phase="greeting")
        processor = _processor(runtime=runtime, greeting_audio_pending=True)
        processor.push_frame = AsyncMock()

        await processor.process_frame(BotStoppedSpeakingFrame())

        assert runtime.set_phase_calls == ["readiness"]
        assert runtime.state.phase == "readiness"
        session = get_session("call_123")
        assert session is not None
        assert session["conversation_state"]["phase"] == "readiness"
    finally:
        delete_session("call_123")


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["collecting", "closing"])
async def test_later_bot_stopped_speaking_does_not_reset_runtime_phase(phase):
    runtime = FakeConversationRuntime(phase=phase)
    processor = _processor(runtime=runtime, greeting_audio_pending=False)
    processor.push_frame = AsyncMock()

    await processor.process_frame(BotStoppedSpeakingFrame())

    assert runtime.set_phase_calls == []
    assert runtime.state.phase == phase


@pytest.mark.asyncio
async def test_raw_finalized_transcription_does_not_trigger_runtime_or_legacy_controller():
    settings = get_settings()
    create_session("call_123", "stream_456", settings)

    try:
        runtime = FakeConversationRuntime(phase="readiness")
        processor = _processor(runtime=runtime)
        processor.push_frame = AsyncMock()

        await processor.process_frame(
            TranscriptionFrame(
                text="Yeah, please go ahead.",
                user_id="user",
                timestamp="now",
                finalized=True,
            )
        )

        session = get_session("call_123")
        assert session is not None
        assert session["transcript"] == []
        assert runtime.handle_calls == []
        pushed = processor.push_frame.await_args.args[0]
        assert isinstance(pushed, TranscriptionFrame)
    finally:
        delete_session("call_123")


@pytest.mark.asyncio
async def test_committed_turn_runtime_result_is_spoken_logged_and_snapshotted():
    settings = get_settings()
    create_session("call_123", "stream_456", settings)

    try:
        result = _runtime_result(
            assistant_text="Assistant spoken text.",
            user_transcript_text="User display text.",
            assistant_transcript_text="Assistant display text.",
            phase="collecting",
        )
        runtime = FakeConversationRuntime(phase="readiness", result=result)
        processor = _processor(runtime=runtime)
        processor.push_frame = AsyncMock()

        await processor.handle_end_of_turn("Yeah, please go ahead.")

        session = get_session("call_123")
        assert session is not None
        assert session["transcript"] == [
            {
                "event_index": 1,
                "role": "user",
                "phase": "readiness",
                "text": "User display text.",
            },
            {
                "event_index": 2,
                "role": "assistant",
                "phase": "task",
                "text": "Assistant display text.",
            },
        ]
        assert session["conversation_state"]["phase"] == "collecting"
        assert len(runtime.handle_calls) == 1
        pushed = processor.push_frame.await_args.args[0]
        assert isinstance(pushed, TTSSpeakFrame)
        assert pushed.text == "Assistant spoken text."
        assert getattr(pushed, "append_to_context", True) is True
    finally:
        delete_session("call_123")


@pytest.mark.asyncio
async def test_committed_turn_uses_turn_text_when_result_user_transcript_is_missing():
    settings = get_settings()
    create_session("call_123", "stream_456", settings)

    try:
        runtime = FakeConversationRuntime(
            phase="collecting",
            result=_runtime_result(
                user_transcript_text=None,
                assistant_transcript_text=None,
                assistant_text="Thanks.",
                phase="closing",
            ),
        )
        processor = _processor(runtime=runtime)
        processor.push_frame = AsyncMock()

        await processor.handle_end_of_turn("  My name is Ana.  ")

        session = get_session("call_123")
        assert session is not None
        assert session["transcript"][0]["phase"] == "task"
        assert session["transcript"][0]["text"] == "My name is Ana."
        assert session["transcript"][1]["phase"] == "closing"
        assert session["transcript"][1]["text"] == "Thanks."
    finally:
        delete_session("call_123")


@pytest.mark.asyncio
async def test_runtime_exception_uses_safe_fallback_once_without_legacy_controller():
    settings = get_settings()
    create_session("call_123", "stream_456", settings)

    try:
        runtime = FakeConversationRuntime(phase="collecting", error=RuntimeError("boom"))
        processor = _processor(runtime=runtime)
        processor.push_frame = AsyncMock()

        await processor.handle_end_of_turn("My name is Ana.")

        session = get_session("call_123")
        assert session is not None
        assert session["transcript"] == [
            {
                "event_index": 1,
                "role": "user",
                "phase": "task",
                "text": "My name is Ana.",
            },
            {
                "event_index": 2,
                "role": "assistant",
                "phase": "closing",
                "text": RUNTIME_ERROR_FALLBACK_TEXT,
            },
        ]
        assert session["conversation_state"]["phase"] == "closing"
        assert processor.push_frame.await_count == 1
        pushed = processor.push_frame.await_args.args[0]
        assert isinstance(pushed, TTSSpeakFrame)
        assert pushed.text == RUNTIME_ERROR_FALLBACK_TEXT
    finally:
        delete_session("call_123")


@pytest.mark.asyncio
async def test_processing_lock_suppresses_overlapping_committed_turn():
    settings = get_settings()
    create_session("call_123", "stream_456", settings)

    try:
        runtime = FakeConversationRuntime(phase="collecting")
        processor = _processor(runtime=runtime)
        await processor._turn_processing_lock.acquire()
        try:
            result = await processor.handle_end_of_turn("I am in Pune.")
        finally:
            processor._turn_processing_lock.release()

        assert result.turn is not None
        assert processor.last_turn_collection_result is not None
        assert processor.last_turn_collection_result.ignored_reason == "turn_processing"
        assert runtime.handle_calls == []
    finally:
        delete_session("call_123")


@pytest.mark.asyncio
async def test_should_end_call_speaks_final_tts_once_and_late_turn_is_suppressed():
    settings = get_settings()
    create_session("call_123", "stream_456", settings)

    try:
        runtime = FakeConversationRuntime(
            phase="collecting",
            result=_runtime_result(
                assistant_text="Goodbye.",
                assistant_transcript_text="Goodbye.",
                phase="closing",
                should_end_call=True,
            ),
        )
        processor = _processor(runtime=runtime)
        processor.push_frame = AsyncMock()

        await processor.handle_end_of_turn("Stop.")
        await processor.handle_end_of_turn("Are you still there?")

        assert len(runtime.handle_calls) == 1
        assert processor.push_frame.await_count == 1
        assert processor.last_turn_collection_result is not None
        assert processor.last_turn_collection_result.ignored_reason == "already_closing"
        pushed = processor.push_frame.await_args.args[0]
        assert pushed.text == "Goodbye."
    finally:
        delete_session("call_123")


@pytest.mark.asyncio
async def test_committed_turn_does_not_rebuild_config_snapshot():
    settings = get_settings()
    snapshot = build_config_snapshot(settings)
    create_session("call_123", "stream_456", settings, config_snapshot=snapshot)

    try:
        runtime = FakeConversationRuntime(phase="readiness")
        processor = _processor(runtime=runtime)
        processor.push_frame = AsyncMock()

        with patch("app.services.pipecat_pipeline.build_config_snapshot") as mock_build:
            await processor.handle_end_of_turn("Yes.")

        mock_build.assert_not_called()
    finally:
        delete_session("call_123")


def test_two_processors_do_not_share_runtime_instance_or_state():
    settings = get_settings()
    create_session("call_a", "stream_a", settings)
    create_session("call_b", "stream_b", settings)

    try:
        first = _processor(
            call_sid="call_a",
            stream_sid="stream_a",
            runtime=FakeConversationRuntime(call_sid="call_a", stream_sid="stream_a"),
        )
        second = _processor(
            call_sid="call_b",
            stream_sid="stream_b",
            runtime=FakeConversationRuntime(call_sid="call_b", stream_sid="stream_b"),
        )

        assert first._conversation_runtime is not second._conversation_runtime
        first._conversation_runtime.set_phase("closing")
        assert second._conversation_runtime.snapshot_state().phase == "readiness"
        assert not isinstance(get_session("call_a")["conversation_state"], ConversationRuntime)
        assert not isinstance(get_session("call_b")["conversation_state"], ConversationRuntime)
    finally:
        delete_session("call_a")
        delete_session("call_b")


def test_pipeline_bridge_has_no_legacy_turn_decision_references():
    source = inspect.getsource(pipecat_pipeline)
    assert "decide_next_turn" not in source
    assert "apply_turn_decision" not in source
    assert "ConversationRuntimeState" not in source
