from pipecat.frames.frames import AudioRawFrame
f = AudioRawFrame(audio=b"\x00\x00", sample_rate=16000, num_channels=1)
print("Attributes:", dir(f))
print("id exists:", hasattr(f, "id"))
print("broadcast_sibling_id exists:", hasattr(f, "broadcast_sibling_id"))
