# Phase 0: Docs Cleanup And Baseline

## Status

Completed

## Goal

Establish a clean documentation hierarchy and a stable V2 engineering specification before implementation.

## What Was Done

- Created structured `docs/` hierarchy (`current`, `plans`, `decisions`, `archive`).
- Archived legacy phase/spec/notes docs without deleting historical information.
- Added and tightened `docs/specs/VOICE_AGENT_V2_SPEC.md` as V2 source-of-truth.
- Updated docs index and source-of-truth priority guidance.

## Commits

- `62acc39` `docs: reorganize project documentation for voice agent v2 rebuild`
- `24bdf3e` `docs: add voice agent v2 engineering spec`
- `1708139` `docs: tighten voice agent v2 engineering spec`

## Files Changed

- `docs/README.md`
- `docs/current/*`
- `docs/decisions/*`
- `docs/plans/*`
- `docs/specs/VOICE_AGENT_V2_SPEC.md`
- `docs/archive/*`
- root `README.md` and `AGENTS.md` (docs pointers)

## Verification

- Docs hierarchy exists and legacy docs are preserved under `docs/archive/`.
- Source-of-truth spec is linked from docs README.
- No runtime behavior change was introduced by docs work.

## Known Follow-Ups

- Live provider tests should be optional if provider billing/credits are unavailable.

## Exit Criteria

- V2 spec and ADR alignment completed.
- Phase plans ready for implementation-level expansion.
- Team can start Phase 1 with clear boundaries and prerequisites.