# Voicebot V3 — Local Run & Live Exotel Validation Runbook

> No secrets in this file. Fill real keys only in your local `.env` (gitignored).
> This validates Phase 1 end-to-end. Until these steps pass, Phase 1 is NOT complete.

## 1. Prerequisites
- Python venv at `.venv` with deps installed (`pip install -e .`).
- **MongoDB running** and reachable (V3 requires it; the app fails to start otherwise).
- ngrok (or equivalent) account/binary.
- Real provider credentials: Deepgram, Cartesia, Cerebras; Exotel account + Exophone + Voicebot Applet flow.

## 2. Configure `.env` (secrets/infra only)
Copy `.env.example` → `.env` and set:
```
DEEPGRAM_API_KEY=...        CARTESIA_API_KEY=...     CEREBRAS_API_KEY=...
TTS_VOICE_ID=...            # seed only; editable later in /config
MONGO_URI=mongodb://localhost:27017     MONGO_DB_NAME=voice_agent_dev
EXOTEL_ACCOUNT_SID=...  EXOTEL_API_KEY=...  EXOTEL_API_TOKEN=...
EXOTEL_VIRTUAL_NUMBER=...  EXOTEL_FLOW_ID=...  EXOTEL_SUBDOMAIN=api.in.exotel.com
PUBLIC_NGROK_URL=your-subdomain.ngrok-free.app
```
All tuning (STT/LLM/TTS/memory/prompts) lives in Mongo `runtime_config`, edited at `/config`.

## 3. Start the app
```
./.venv/Scripts/python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
```
- Expected: startup logs "MongoDB client initialized" then "Seeded default runtime_config document" (first run).
- If Mongo is down: startup **fails clearly** (by design). Start Mongo and retry.

## 4. Start ngrok and confirm the webhook URL
```
ngrok http 8000
```
- Ensure `PUBLIC_NGROK_URL` matches the ngrok host.
- `GET /telephony/webhook?CallSid=test` must return `{"url":"wss://<ngrok>/telephony/exotel?sample-rate=16000"}`.
- Point the Exotel Voicebot Applet dynamic URL at `https://<ngrok>/telephony/webhook`.

## 5. Review config
- Open `http://127.0.0.1:8000/config`.
- Confirm: STT `flux-general-multi`, hints `en, hi`, EOT 0.65 / 700; LLM `gpt-oss-120b`; TTS voice/model; prompts. Edit + Save; re-open to confirm persistence (no "could not load" banner).

## 6. Place the outbound call
- Enter your phone number (E.164, e.g. `+9198...`) → "Generate greeting & call".
- Expected server logs: greeting generated → `Outbound call initiated successfully` with a CallSid.
- If greeting generation fails: **no call is placed**, the page shows an error, and an `outbound_attempts` doc is written. (Test this on purpose: temporarily set an invalid `CEREBRAS_API_KEY`.)

## 7. Expected logs when the call connects
- `Session created, building transport` → `Transport ready, handing off to pipeline service` → `Pipeline runner starting`.
- `First bot speech latency` after the greeting plays.
- Per user turn: `Turn latency measured` is NOT used in V3; look for the turn record appearing in the call log after the call.

## 8. Conversation tests (the real validation)
1. **Greeting first:** the bot speaks immediately; you don't say "hello".
2. **English:** say "Hey, my name is Ojas." → coherent English reply.
3. **Hindi/Hinglish:** "haan mai abhi ghar ja raha hu" → understood, replied in kind.
4. **Switch to English:** "continue in English" → next reply in English.
5. **Switch to Hindi:** "Hindi mein bolo" → next reply in Hindi.
6. **Memory (20–30 turns):** state a fact early (e.g. "remember I prefer short answers"); after ~20 turns ask about it → bot recalls it; no obvious latency spike around turns 10/15/20 (summary refresh).

## 9. Inspect results
- `GET /calls` → your call with avg/max "User Stop → First Audio Sent".
- `GET /calls/{call_sid}` → greeting text, full transcript, per-turn table.
- **Metrics that must be non-null** for a normal turn: `user_text`, `assistant_text`, `detected_language`, `response_language`, `primary_latency_ms`, `llm_total_ms`. (`user_stopped_is_eot_fallback=true` is acceptable — it means the EOT timestamp was used.)

## 10. Failure-path test
- Force greeting failure (bad Cerebras key) → confirm: no call, frontend error, an `outbound_attempts` record with `reason: greeting_failed`.

## 11. Known live unknowns to watch
- Native Flux EOT → LLM → TTS actually produces a spoken reply (the #1 thing to confirm on turn 1).
- Hindi voice naturalness with the configured Cartesia voice.
- Flux Hinglish/code-switch accuracy on real 16 kHz phone audio.
- Cerebras GPT-OSS per-turn latency under live load.

## 12. If something fails
- Capture the server logs + the `/calls/{call_sid}` page. Fix only the failing issue; do not start Phase 2. Re-run from step 6.
