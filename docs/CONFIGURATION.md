# Configuration

Only provider credentials and server settings live in `.env`. Behavior is stored in
`data/runtime_config.json`, edited at `/config`, and snapshotted before each conversation.

## Defaults

| Group | Default |
|---|---|
| STT | Flux Multilingual, English/Hindi hints, EOT threshold 0.65, silence fallback 700 ms. |
| Turn debounce | Pipecat post-STT delay 500 ms; separate from Flux endpointing. |
| LLM | `qwen-3.8-27b`, reasoning `none`, temperature 0.7, completion limit 160, request timeout 10 seconds. |
| TTS | `sonic-3.6-2026-08-27`; voice ID must be entered before Start. |
| Greeting | Directly spoken `Hello, how can I help you today?`; no extra LLM generation. |
| Readiness / shutdown | 30 seconds / 10 seconds. |
| Memory | Existing recent-window, facts, local summary, and lexical recall settings. |

The models in `app/models/runtime_config_models.py` define all ranges and supported values.
Misspelled fields and invalid model/reasoning combinations are rejected.
GPT-OSS, if deliberately selected, requires reasoning low/medium/high rather than none.
Eager/speculative replies are disabled for this release.

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
