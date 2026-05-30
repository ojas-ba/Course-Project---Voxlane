import wave
wav_path = "tests/assets/test_speech.wav"
with wave.open(wav_path, "rb") as w:
    print("Params:", w.getparams())
