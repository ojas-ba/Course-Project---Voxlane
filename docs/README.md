# Voice Agent Documentation

This repository contains a local-development voice agent prototype using FastAPI, Exotel, Pipecat, Deepgram Flux, Cerebras, Cartesia, and MongoDB-style call-log persistence.

## Source Of Truth

- Current architecture and behavior contracts: `docs/current/`
- Rebuild roadmap and phase plans: `docs/plans/`
- Accepted architecture decisions (ADRs): `docs/decisions/`
- Historical specs/phase docs/notes: `docs/archive/`

## Start Here

- Read `docs/current/ARCHITECTURE.md`
- Read `docs/current/CONFIGURATION.md`
- Read `docs/current/RUNTIME_FLOW.md`
- For V2 work, begin with `docs/plans/VOICE_AGENT_V2_REBUILD_PLAN.md`

## Main V2 Specification

- `docs/specs/VOICE_AGENT_V2_SPEC.md` - source-of-truth engineering specification for the V2 rebuild.

## Source-Of-Truth Priority

1. `docs/specs/VOICE_AGENT_V2_SPEC.md`
2. `docs/decisions/`
3. `docs/current/`
4. `docs/plans/`
5. `docs/archive/`

Meaning:

- spec defines what to build
- ADRs define fixed decisions
- current docs explain architecture/runtime
- plans explain implementation order
- archive is historical reference only
