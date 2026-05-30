from app.services.transport_factory import build_exotel_serializer

def test_exotel_serializer_wiring():
    serializer = build_exotel_serializer(stream_sid="stream_123", call_sid="call_456")
    
    assert getattr(serializer, "_stream_sid", getattr(serializer, "stream_sid", None)) == "stream_123"
    assert getattr(serializer, "_call_sid", getattr(serializer, "call_sid", None)) == "call_456"
    
    # Assert 16kHz explicit requirement
    params = getattr(serializer, "_params", getattr(serializer, "params", None))
    assert params.exotel_sample_rate == 16000
    assert params.sample_rate == 16000
