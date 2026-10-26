# Latency

The browser uses the existing streamed Flux → Cerebras → Cartesia pipeline.
No latency number from the old telephone prototype is a browser performance guarantee.

## What stays fast

- Cerebras final-answer tokens stream into Cartesia sentence synthesis; the full response is not buffered.
- Qwen runs with reasoning explicitly disabled in normal and speculative requests.
  The prompt keeps spoken answers short; 1024 tokens is an output ceiling, not a target to generate.
- VAD is warmed locally at startup. Flux connection overlaps media setup.
- The greeting waits for browser playback and Flux readiness instead of using a carrier settle delay.
- Bounded recent context and the existing memory budget keep prompt work controlled.
- Eager/speculative behavior is configurable. A matching draft hides part of the generation wait;
  it never speaks before final EOT. Compare enabled/disabled runs rather than assuming a saving.
- Normal and draft requests have a total deadline and SDK retries are disabled.

## What to measure

The existing observer distinguishes acoustic/VAD-stop anchors, turn release/commit, LLM TTFB,
TTS first audio, and merged turns. See [METRICS](METRICS.md) for the stored fields.
These are backend events. Network jitter, codec buffering, and browser playback add time.

Flux `eot_timeout_ms` is a maximum silence fallback, not necessarily the wait on every turn.
Pipecat's separate `turn_stop_timeout_ms` is the post-STT debounce. Tune one variable at a time
through Settings after a person has established the browser baseline.
The repaired local setup restores the 700 ms Flux silence fallback, keeping the existing
500 ms post-STT debounce. These are starting values, not recovered historical MongoDB tuning.

The first greeting can wait for provider connection readiness. This trades some first-connect
speed for not losing the first utterance. It is separate from steady-state turn responsiveness.

## Validation ownership

The AI verifies configuration, provider request shape, metrics anchors, and protocol/lifecycle code.
A person validates the actual pause heard between speech and reply. Unit tests and silent media
tests do not prove voice quality, realistic latency, or echo behavior.
