import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from pipecat.frames.frames import BotStoppedSpeakingFrame, TTSSpeakFrame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameProcessor

from app.config import get_settings
from app.runtime import build_config_snapshot, create_session, delete_session, get_session
from app.services.conversation_controller import ConversationRuntimeState, TurnDecision
from app.services.pipecat_pipeline import (
    UserConversationProcessor,
    _build_pipeline_processors,
)


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


def test_pipeline_processors_route_user_text_through_controller():
    settings = get_settings()
    transport = DummyTransport()

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
            snapshot=build_config_snapshot(settings),
            settings=settings,
            call_sid="call_123",
        )

    assert isinstance(processors[2], UserConversationProcessor)
    assert len(processors) == 7


@pytest.mark.asyncio
async def test_outbound_early_hello_during_greeting_is_ignored():
    settings = get_settings()
    create_session("call_123", "stream_456", settings)

    try:
        state = ConversationRuntimeState(
            phase="greeting",
            greeting_audio_pending=True,
        )
        processor = UserConversationProcessor(
            call_sid="call_123",
            state=state,
            snapshot=build_config_snapshot(settings),
            settings=settings,
            opening_mode="outbound",
        )
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
        assert state.phase == "greeting"
        assert processor.last_turn_collection_result is not None
        assert processor.last_turn_collection_result.ignored_reason == "greeting_pending"
        processor.push_frame.assert_not_awaited()
    finally:
        delete_session("call_123")


@pytest.mark.asyncio
async def test_greeting_stop_moves_to_readiness():
    settings = get_settings()
    state = ConversationRuntimeState(
        phase="greeting",
        greeting_audio_pending=True,
    )
    processor = UserConversationProcessor(
        call_sid="call_123",
        state=state,
        snapshot=build_config_snapshot(settings),
        settings=settings,
        opening_mode="outbound",
    )
    processor.push_frame = AsyncMock()

    await processor.process_frame(BotStoppedSpeakingFrame())

    assert state.greeting_audio_pending is False
    assert state.phase == "awaiting_readiness"


@pytest.mark.asyncio
async def test_raw_finalized_transcription_does_not_trigger_turn_decision():
    settings = get_settings()
    create_session("call_123", "stream_456", settings)

    try:
        state = ConversationRuntimeState(phase="awaiting_readiness")
        processor = UserConversationProcessor(
            call_sid="call_123",
            state=state,
            snapshot=build_config_snapshot(settings),
            settings=settings,
            opening_mode="outbound",
        )
        processor.push_frame = AsyncMock()

        with patch(
            "app.services.pipecat_pipeline.decide_next_turn",
            new_callable=AsyncMock,
        ) as mock_decide:
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
        mock_decide.assert_not_awaited()
        pushed = processor.push_frame.await_args.args[0]
        assert isinstance(pushed, TranscriptionFrame)
    finally:
        delete_session("call_123")


@pytest.mark.asyncio
async def test_committed_turn_decision_is_spoken_and_logged():
    settings = get_settings()
    create_session("call_123", "stream_456", settings)

    try:
        state = ConversationRuntimeState(phase="awaiting_readiness")
        processor = UserConversationProcessor(
            call_sid="call_123",
            state=state,
            snapshot=build_config_snapshot(settings),
            settings=settings,
            opening_mode="outbound",
        )
        processor.push_frame = AsyncMock()

        with patch(
            "app.services.pipecat_pipeline.decide_next_turn",
            new_callable=AsyncMock,
        ) as mock_decide:
            mock_decide.return_value = TurnDecision(
                phase="task",
                assistant_text="Great, thank you. What is your full name?",
                user_intent="ready",
            )
            await processor.process_frame(
                TranscriptionFrame(
                    text="Yeah, please go ahead.",
                    user_id="user",
                    timestamp="now",
                    finalized=True,
                )
            )
            await processor.handle_end_of_turn("Yeah, please go ahead.")

        session = get_session("call_123")
        assert session is not None
        assert session["transcript"][-1] == {
            "event_index": 2,
            "role": "assistant",
            "phase": "task",
            "text": "Great, thank you. What is your full name?",
        }
        pushed = processor.push_frame.await_args.args[0]
        assert isinstance(pushed, TTSSpeakFrame)
        assert pushed.text == "Great, thank you. What is your full name?"
        assert state.phase == "task"
    finally:
        delete_session("call_123")


@pytest.mark.asyncio
async def test_two_quick_committed_turns_do_not_create_concurrent_decisions():
    settings = get_settings()
    create_session("call_123", "stream_456", settings)

    try:
        state = ConversationRuntimeState(phase="task")
        processor = UserConversationProcessor(
            call_sid="call_123",
            state=state,
            snapshot=build_config_snapshot(settings),
            settings=settings,
        )
        processor.push_frame = AsyncMock()
        first_decision_started = asyncio.Event()
        release_first_decision = asyncio.Event()

        async def _slow_decide(**kwargs):
            first_decision_started.set()
            await release_first_decision.wait()
            return TurnDecision(
                phase="task",
                assistant_text="Thanks. What is your location?",
                user_intent="answer",
            )

        with patch(
            "app.services.pipecat_pipeline.decide_next_turn",
            new_callable=AsyncMock,
        ) as mock_decide:
            mock_decide.side_effect = _slow_decide
            first_task = asyncio.create_task(processor.handle_end_of_turn("My name is Ana."))
            await first_decision_started.wait()

            second_result = await processor.handle_end_of_turn("I am in Pune.")

            release_first_decision.set()
            await first_task

        assert mock_decide.await_count == 1
        assert second_result.turn is not None
        assert processor.last_turn_collection_result is not None
        assert processor.last_turn_collection_result.ignored_reason == "turn_processing"
    finally:
        delete_session("call_123")
