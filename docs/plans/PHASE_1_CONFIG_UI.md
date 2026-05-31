# PHASE 1 CONFIG UI

## Goal

Move behavior configuration ownership from `.env` into UI/DB-backed profile settings.

## Scope

- Define BotProfile configuration surface and migration plan for behavior fields.

## Out Of Scope

- Audio pipeline or conversation-engine logic changes.

## Files Likely Affected

- `app/config.py`, dashboard config routes/views, persistence models for profile config.

## Acceptance Criteria

- Behavior config values are no longer source-of-truth in `.env`.