# Local runbook

## Start

1. Activate the Python environment and install with `python -m pip install -e .`.
2. Configure the three provider keys in your own `.env`; see `.env.example`.
3. Run `npm --prefix frontend ci` and `npm --prefix frontend run build`.
4. Run `python -m uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1`.
5. Open `http://localhost:8000/config`, enter a Cartesia voice ID, and save.
6. Open `http://localhost:8000`, Start, and allow the mic.

A missing frontend build, invalid config file, or unwritable data folder fails startup.
A missing voice blocks Start before provider work. Old Exotel/Mongo environment variables are ignored;
the application needs no phone service, database, or tunnel.

## Normal automated checks

`python -m pytest -m "not live"`

`npm --prefix frontend test`

`npm --prefix frontend run build`

The suite covers local file storage, request/route contracts, provider request payloads,
pipeline assembly, language/memory/metrics, and session cleanup. The local WebRTC test uses
silence and substituted providers. It verifies protocol wiring, not audible behavior.

Provider checks are opt-in: `python -m pytest -m live`. Refresh keys and configure the voice first.
Do not run them implicitly as part of normal verification.

## Human browser checklist

- Full opening greeting is audible once, including after a fresh process start.
- English, Hindi, and switching languages produce complete, natural replies.
- Earlier details are recalled correctly. No language tags or reasoning appear in captions/audio.
- Mute/unmute and blocked-playback Enable audio behave clearly.
- End stops audio and reports whether saving succeeded; inspect the history link.
- A second tab is rejected while a conversation is active or stopping.
- Refresh/tab close and provider failure release resources; start another conversation afterward.
- Compare felt responsiveness with the server metrics without treating them as speaker playback timings.

## Troubleshooting

- Microphone denied: allow permission for localhost, then retry. Check the selected OS microphone.
- No playback: click Enable audio and check the browser/OS speaker output. Use headphones for echo.
- Setup timeout/provider error: check credentials, quota, model/voice access, and structured logs.
- Save failure: inspect disk permissions/space, then click Retry saving. The finalized payload stays
  in server memory until saved; keep Python open while retrying. The button uses the same End endpoint.
- End takes longer: cleanup continues and blocks a new call until the runner actually exits.
  Retry saving joins that cleanup. A permanently stuck runner requires a restart, which may lose its active data.
- Protocol mismatch: run `npm --prefix frontend ci` and rebuild. The build rejects clients outside
  RTVI 1.3.0; do not override the advertised protocol version.
- Input sounds wrong: the Talk page names the selected microphone, and structured logs include its
  actual sample rate, channel count, and echo/noise/gain settings. Compare headphones and speakers,
  English/Hindi, and deliberate pauses. Browser native rates may differ from the fixed 16 kHz pipeline.
- Config typo: correct `data/runtime_config.json` and restart. Existing files are never silently reset.

Normal data is under `data/`, logs under `logs/`. Both are ignored by Git.
A hard process crash may lose the active conversation; completed JSON records remain.
