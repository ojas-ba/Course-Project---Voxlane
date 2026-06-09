# Documentation

End-to-end documentation for the **VoiceAgent (V3)** — a low-latency, multilingual (Hindi + English)
phone agent built on Exotel + Pipecat + Deepgram Flux + Cerebras + Cartesia.

> Start with the root **[../README.md](../README.md)** for the project overview and quickstart.

---

## 📖 Reading order

**New to the project?** Read these three in order:

1. **[ARCHITECTURE](ARCHITECTURE.md)** — what the pieces are and how a call flows through the pipeline.
2. **[LATENCY](LATENCY.md)** — the core idea: why it feels fast, and what makes it fast.
3. **[METRICS](METRICS.md)** — how every turn is measured and shown in the dashboard.

---

## 🗂️ All docs

| Doc | Topic |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Components, the 11-stage pipeline order (and *why*), the full call lifecycle, frame flow. |
| [LATENCY.md](LATENCY.md) | Endpointing vs pipeline budget, **responsiveness vs total**, every latency technique, tuning. |
| [METRICS.md](METRICS.md) | The per-turn record, all timing anchors, the call-outcome taxonomy, dashboard rollups. |
| [SPECULATION.md](SPECULATION.md) | EagerEOT telemetry (Stage 1) + the speculative-draft LLM gate (Stage 2). |
| [MEMORY.md](MEMORY.md) | Recent window, pinned facts, running summary, lexical recall, token budget. |
| [CONFIGURATION.md](CONFIGURATION.md) | Two-tier config, the per-call snapshot, every runtime knob + validators. |
| [TELEPHONY.md](TELEPHONY.md) | Exotel webhook + WebSocket, outbound vs inbound, fail-closed greeting. |
| [RUNBOOK.md](RUNBOOK.md) | Local run + live-call validation checklist. |
| [decisions/](decisions/) | Accepted architecture decisions (ADRs). |

---

## 🧭 Find it by topic

| If you want to understand… | Read |
|---|---|
| The processor chain & frame flow | [ARCHITECTURE](ARCHITECTURE.md) §3 |
| Why "1.4 s latency" but it feels snappy | [LATENCY](LATENCY.md) §1–2 |
| The endpointing knobs (`eot_timeout`, `turn_stop_timeout`) | [LATENCY](LATENCY.md) §3, [CONFIGURATION](CONFIGURATION.md) §3 |
| `responsiveness` vs `total` in the dashboard | [METRICS](METRICS.md) §3, §6 |
| The `<en>`/`<hi>` language routing | [ARCHITECTURE](ARCHITECTURE.md) §3 |
| How the bot "remembers" earlier turns | [MEMORY](MEMORY.md) |
| The speculative-LLM draft/gate | [SPECULATION](SPECULATION.md) |
| Every config field + default | [CONFIGURATION](CONFIGURATION.md) §3, §6 |
| Outbound dialing & the fail-closed greeting | [TELEPHONY](TELEPHONY.md) §2, §4 |
| Why 16 kHz everywhere | [decisions/ADR_001](decisions/ADR_001_SAMPLE_RATE_16000.md) |

---

## 🏛️ Architecture decisions (ADRs)

| ADR | Decision |
|---|---|
| [001](decisions/ADR_001_SAMPLE_RATE_16000.md) | Fixed 16 kHz transport/pipeline sample rate. |
| [002](decisions/ADR_002_ENV_SECRETS_ONLY.md) | Only secrets in `.env`; tuning lives in MongoDB. |
| [003](decisions/ADR_003_PIPELINE_IS_NOT_BRAIN.md) | The pipeline routes frames; conversation logic lives elsewhere. |
| [004](decisions/ADR_004_TURN_BASED_ARCHITECTURE_FIRST.md) | Turn-based architecture first. |
| [005](decisions/ADR_005_SINGLE_CALL_PROTOTYPE_SCOPE.md) | Single-call prototype scope. |

> Diagrams in these docs are [Mermaid](https://mermaid.js.org/) — they render natively on GitHub.
