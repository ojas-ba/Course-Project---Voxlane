# Runbook — Local Run & Live Exotel Validation

> No secrets in this file. Fill real keys only in your local `.env` (gitignored).
> This is the end-to-end validation checklist for a real call. Unit tests prove logic only — a live
> Exotel call is the only proof of voice behavior, latency, and audio quality.
>
> For *how the system works*, see the [docs index](README.md) — especially
> [ARCHITECTURE](ARCHITECTURE.md), [LATENCY](LATENCY.md), and [CONFIGURATION](CONFIGURATION.md).

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
- Confirm: STT `flux-general-multi`, hints `en, hi`, EOT 0.65 / 700, `defer_connect_on_start` true; LLM `gpt-oss-120b`; TTS voice/model; prompts. Edit + Save; re-open to confirm persistence (no "could not load" banner).
- Language is **LLM-led**: the model starts each reply with an `<en>`/`<hi>` tag that the `LanguagePrefixProcessor` strips and uses to set Cartesia's language. There is no regex override list. The `<en>`/`<hi>` contract is injected by code, so it survives any edit to the persona system prompt.

## 6. Place the outbound call
- Enter your phone number (E.164, e.g. `+9198...`) → "Generate greeting & call".
- Expected server logs: greeting generated → `Outbound call initiated successfully` with a CallSid.
- If greeting generation fails: **no call is placed**, the page shows an error, and an `outbound_attempts` doc is written. (Test this on purpose: temporarily set an invalid `CEREBRAS_API_KEY`.)

## 7. Expected logs when the call connects
- `Session created, building transport` → `Transport ready, handing off to pipeline service` → `Pipeline runner starting`.
- With deferred STT: the greeting should reach TTS without waiting for Flux. Look for `Deferred Flux STT connect completed (ready for audio)` arriving around/after the greeting (not before it).
- `First bot speech latency` after the greeting plays.
- Per user turn: the turn record appears in the call log after the call (no per-turn log line during the call).

## 8. Conversation tests (the real validation)
1. **Greeting first:** the bot speaks immediately; you don't say "hello".
2. **English:** say "Hey, my name is Ojas." → coherent English reply (Resp `en`).
3. **Hindi/Hinglish:** "haan mai abhi ghar ja raha hu" → understood, replied in kind (Resp `hi`).
4. **Switch to English:** "speak in English please" → next reply in English (Resp `en`). The LLM tags it `<en>`; the tag must never be spoken.
5. **Switch to Hindi:** "ab Hindi me baat kijiye" → next reply in Hindi (Resp `hi`). This now works **without** any regex pattern — the model decides.
6. **No tag spoken:** at no point should you hear "en", "hi", or angle-bracket tags in the audio.
7. **Overlapping turns (merge):** speak, then immediately speak again before the bot replies. The bot answers once; in Call Detail the row shows `merged N, M` and no turn ID is missing.
8. **Memory (20–30 turns):** state a fact early ("remember I prefer short answers"); after ~20 turns ask about it → bot recalls it; no obvious latency spike around turns 10/15/20 (summary refresh).

## 8b. Greeting-latency comparison (deferred STT)
- With `stt.defer_connect_on_start = true` (default), warm server-side first-audio should be ~0.4–0.6 s (was ~1.5 s when it waited on the Flux connect).
- To compare: set the flag false in `/config`, Save, place a (warm) call, note first-audio; set it true, Save, place another warm call; compare. The first call after a server restart still pays a one-time cold start — compare **warm** calls only.

## 9. Inspect results
- `GET /calls` → your call with **Responsiveness** (median last-word→audio, the felt latency) and **Total**
  (median incl. endpointing). See [METRICS](METRICS.md) and [LATENCY](LATENCY.md) for what these mean.
- `GET /calls/{call_sid}` → greeting text, full transcript, the per-turn table with grouped Pipeline /
  Spec / Memory breakdowns (merged/superseded turns are labelled), plus call-level EagerEOT and memory cards.
- **Metrics that must be non-null** for a normal turn: `user_text`, `assistant_text`, `detected_language`, `response_language`, `primary_latency_ms`, `llm_total_ms`. `assistant_text` must be the **spoken** text only — never an `<en>`/`<hi>` tag. (`user_stopped_is_eot_fallback=true` is acceptable — it means the EOT timestamp was used.)

## 10. Failure-path test
- Force greeting failure (bad Cerebras key) → confirm: no call, frontend error, an `outbound_attempts` record with `reason: greeting_failed`.

## 11. Known live unknowns to watch
- `<en>`/`<hi>` tag compliance: does Cerebras reliably start every reply with the tag (and never speak it)? Watch for any `error_stage=language_prefix_parse` rows.
- Greeting latency improvement from deferred STT on real Exotel audio (compare warm calls, §8b).
- Hindi voice naturalness with the configured Cartesia voice.
- Flux Hinglish/code-switch accuracy on real 16 kHz phone audio.
- Cerebras GPT-OSS per-turn latency under live load.

## 12. If something fails
- Capture the server logs + the `/calls/{call_sid}` page. Fix only the failing issue; do not start Phase 2. Re-run from step 6.
