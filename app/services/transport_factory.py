"""Transport construction helpers for Exotel WebSocket sessions."""

from fastapi import WebSocket
from pipecat.serializers.exotel import ExotelFrameSerializer
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

from app.config import V1_SAMPLE_RATE


def build_exotel_serializer(stream_sid: str, call_sid: str) -> ExotelFrameSerializer:
    """Build the fixed Exotel serializer for the v1 transport contract."""

    return ExotelFrameSerializer(
        stream_sid=stream_sid,
        call_sid=call_sid,
        params=ExotelFrameSerializer.InputParams(
            exotel_sample_rate=V1_SAMPLE_RATE,
            sample_rate=V1_SAMPLE_RATE,
        ),
    )


def build_exotel_transport(
    websocket: WebSocket,
    stream_sid: str,
    call_sid: str,
) -> FastAPIWebsocketTransport:
    """Build the FastAPI WebSocket transport for an Exotel call."""

    serializer = build_exotel_serializer(stream_sid=stream_sid, call_sid=call_sid)
    return FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            serializer=serializer,
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=V1_SAMPLE_RATE,
            audio_out_sample_rate=V1_SAMPLE_RATE,
        ),
    )
