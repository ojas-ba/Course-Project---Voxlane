# PHASE 2 TURN COLLECTOR

## Goal

Introduce committed-turn collection before conversation actions are triggered.

## Scope

- Add TurnCollector contract and integration point after STT finalization.

## Out Of Scope

- New latency optimizations or dual-lane response generation.

## Files Likely Affected

- New turn collector module, pipeline handoff wiring, unit tests around commit logic.

## Acceptance Criteria

- Bot responses are driven only by committed turns, not partial fragments.