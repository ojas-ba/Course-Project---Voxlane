# Configuration

Only provider credentials and server settings live in `.env`. Behavior is stored in
`data/runtime_config.json`, edited at `/config`, and snapshotted before each conversation.

## Defaults

| Group | Default |
|---|---|
| STT | Flux Multilingual, English/Hindi hints, EOT threshold 0.65, silence fallback 700 ms. |
| Turn debounce | Pipecat post-STT delay 500 ms; separate from Flux endpointing. |
| LLM | `qwen-3.8-27b`, reasoning `none`, temperature 0.7, completion limit 1024, total request timeout 10 seconds. |
| TTS | `sonic-3.6-2026-08-27`; voice ID must be entered before Start. |
| Greeting | Directly spoken `Hello, how can I help you today?`; no extra LLM generation. |
| Readiness / shutdown wait | 30 seconds / 30 seconds. Cleanup continues after an HTTP wait expires. |
| Memory | Existing recent-window, facts, local summary, and lexical recall settings. |

The models in `app/models/runtime_config_models.py` define all ranges and supported values.
Misspelled fields and invalid model/reasoning combinations are rejected.
The no-reasoning profile uses Qwen with `reasoning_effort=none` in both normal and speculative requests.
GPT-OSS remains selectable but cannot disable reasoning; low reasoning and hidden reasoning still generate thinking tokens.
Eager EOT and speculative LLM controls are available. Speculation requires eager EOT;
an enabled eager threshold is required and must not exceed the final EOT threshold.
New installations leave these optional flags off. The repaired local configuration enables
both with eager threshold 0.5; the previous local settings are backed up in
`data/runtime_config.before-debug-fixes.json`. These trial values do not claim to recover old MongoDB edits.

## Persistence rules

Startup creates missing storage and seeds defaults only when the config is absent.
Malformed existing config or unwritable storage fails startup clearly, without replacing data.
Saving Settings validates first, atomically replaces the file, then updates the cache.
A canceled HTTP request does not interrupt that write/cache transaction.

Active conversations keep their existing snapshot. UI saves affect the next conversation.
Manual edits to the JSON file are read on restart. Model, voice, prompt, greeting,
and timeout values are not read from old behavior environment variables.

Settings currently uses ordinary form submission with an HTMX-enhanced replacement of the
settings panel; validation/write errors are displayed. History remains at `/calls`.
The log-level setting is applied on application restart.

See [ADR-006](decisions/ADR_006_BROWSER_LOCAL_FILES.md) for the replacement of MongoDB storage.
