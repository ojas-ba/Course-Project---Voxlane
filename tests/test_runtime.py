from app.config import get_settings
from app.runtime import (
    PENDING_OUTBOUND_CALLS,
    append_session_transcript_event,
    attach_pending_call_sid,
    build_config_snapshot,
    cleanup_expired_pending_outbound_contexts,
    create_pending_outbound_context,
    create_session,
    delete_session,
    get_pending_outbound_context,
    get_session,
    pop_pending_outbound_context,
    update_session_conversation_state,
)


def test_pending_outbound_context_lifecycle():
    PENDING_OUTBOUND_CALLS.clear()
    settings = get_settings()
    context = create_pending_outbound_context(
        to_number="+919876543210",
        config_snapshot=build_config_snapshot(settings),
        greeting_text="Hi there.",
    )

    assert context["call_sid"] is None
    attach_pending_call_sid(context, call_sid="call_123")

    assert get_pending_outbound_context("call_123") == context
    assert pop_pending_outbound_context("call_123") == context
    assert get_pending_outbound_context("call_123") is None


def test_append_session_transcript_event():
    settings = get_settings()
    create_session("call_123", "stream_456", settings)

    try:
        append_session_transcript_event(
            "call_123",
            role="assistant",
            phase="greeting",
            text="Hi, is now a good time?",
        )

        session = get_session("call_123")
        assert session is not None
        assert session["transcript"] == [
            {
                "event_index": 1,
                "role": "assistant",
                "phase": "greeting",
                "text": "Hi, is now a good time?",
            }
        ]
    finally:
        delete_session("call_123")


def test_cleanup_expired_pending_outbound_contexts():
    PENDING_OUTBOUND_CALLS.clear()
    settings = get_settings()
    context = create_pending_outbound_context(
        to_number="+919876543210",
        config_snapshot=build_config_snapshot(settings),
    )
    attach_pending_call_sid(context, call_sid="call_123")
    context["created_at"] = 0.0

    assert cleanup_expired_pending_outbound_contexts(1.0) == 1
    assert get_pending_outbound_context("call_123") is None


def test_update_session_conversation_state_stores_json_safe_deep_copy():
    settings = get_settings()
    create_session("call_123", "stream_456", settings)
    snapshot = {
        "call_sid": "call_123",
        "phase": "readiness",
        "slots": {"name": {"slot_id": "name", "status": "missing"}},
    }

    try:
        update_session_conversation_state("call_123", snapshot)
        snapshot["slots"]["name"]["status"] = "collected"

        session = get_session("call_123")
        assert session is not None
        assert session["conversation_state"] == {
            "call_sid": "call_123",
            "phase": "readiness",
            "slots": {"name": {"slot_id": "name", "status": "missing"}},
        }
    finally:
        delete_session("call_123")


def test_update_session_conversation_state_noops_for_missing_session():
    update_session_conversation_state(
        "missing_call",
        {"call_sid": "missing_call", "phase": "readiness"},
    )

    assert get_session("missing_call") is None
