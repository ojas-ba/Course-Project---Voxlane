# Architecture

The application serves one local browser conversation. Python runs FastAPI and Pipecat;
the browser runs a bundled plain JavaScript client in a Jinja page. HTMX saves Settings
without replacing the active conversation panel.

## Components

| Component | Responsibility |
|---|---|
| `main.py` | Load credentials, validate files, warm VAD, register routes, drain resources on shutdown. |
| `routers/browser.py` | Talk page, POST/PATCH offer, and explicit End HTTP endpoint. |
| `services/browser_session.py` | Own the single peer, readiness tasks, runner, and once-only cleanup. |
| `services/transport_factory.py` | SmallWebRTC with audio-only pipeline frames fixed at 16000 Hz. |
| `services/pipecat_pipeline.py` | Build services/processors, attach observers, run the pipeline, and report its result. |
| `runtime.py` | The only active-session data registry; JSON-safe values, no provider objects. |
| `services/runtime_config.py`, `config_store.py` | Validated config cache, serialized saves, immutable snapshots. |
| `storage.py` | Atomic local JSON config/history files. |
| `routers/dashboard.py` | Settings and saved transcript/metrics views. |

## Audio and event flow

```mermaid
flowchart LR
    Browser[Browser] <-->|SmallWebRTC audio/events| Transport
    Transport --> Flux[Deepgram Flux]
    Flux --> Hint[Language hint]
    Hint --> Memory
    Memory --> User[User aggregator]
    User --> LLM[Cerebras Qwen]
    LLM --> Prefix[Language prefix]
    Prefix --> TTS[Cartesia]
    TTS --> Output[Transport output]
    Output --> Assistant[Assistant aggregator]
    Output --> Browser
```

Flux final transcription/EndOfTurn drives committed turns. The existing turn collector,
memory, language-prefix processor, and metrics side channels remain intact.
The LLM emits `<en>` or `<hi>`; the prefix processor switches the TTS language and
removes the prefix before speech. Speculative/eager replies are disabled in browser configuration.

Observers are registered on `PipelineTask`, including `UserBotLatencyObserver` and
the existing turn-metrics observer. `SileroVADAnalyzer()` remains in user-aggregator params.
Pipecat 1.2.1 automatically supplies RTVI; no duplicate RTVI observer is registered.

## Session lifecycle

The session manager reserves its slot before initializing a peer and retains the connection
and tasks on `app.state`. The pipeline hands it the task and STT-readiness callback before
running. Matching active peer IDs renegotiate; unknown IDs and concurrent starts are rejected.

The browser completes the RTVI handshake and starts playback of the silent remote track.
It then sends `ui-ready`; blocked playback has an Enable audio button.
The server waits for media, RTVI, UI, and Flux readiness, then queues the configured greeting once.
Microphone input stays muted through the greeting. The browser enables input after the first
greeting speaking-stop callback, respecting the user's Mute selection.

End, disconnect, provider failure, setup timeout, and shutdown request the same cleanup task.
Callbacks signal cleanup without waiting for their own runner. Cleanup cancels pending work,
awaits pipeline cancellation/runner completion, finalizes once, atomically saves, closes the peer,
and removes runtime state. A stuck runner blocks replacement sessions and requires restart.

## Boundaries

- One process/worker and one conversation. No multi-user, background worker, media relay, or public hosting.
- Browser/device codec rates can differ; SmallWebRTC converts at the fixed 16 kHz pipeline boundary.
- API keys stay server-side. Behavior is in local JSON and snapshotted per conversation.
- History uses a versioned envelope around the existing `CallLog`; no MongoDB migration.
- Backend timing is not measured browser-speaker latency. Audible behavior requires human validation.

See [configuration](CONFIGURATION.md), [metrics](METRICS.md), [runbook](RUNBOOK.md), and
[ADR-006](decisions/ADR_006_BROWSER_LOCAL_FILES.md).
