# PHASE_0_SETUP.md - Setup and Skeleton

## Phase Gate

This is the first implementation phase. No prior phase is required.

## Objective

Create the initial project skeleton and planning-safe setup layer so later phases can implement transport, pipeline, logging, and tests without restructuring the repo.

## Allowed Files

- `pyproject.toml`
- `.gitignore`
- `.env.example`
- `README.md`
- `main.py`
- `app/__init__.py`
- `app/config.py`
- `app/database.py`
- `app/runtime.py`
- base package directories needed for later phases

## In Scope

- project package skeleton
- dependency declaration
- environment example file
- settings structure
- basic application entrypoint
- database accessor placeholder
- ignore rules for secrets and generated artifacts

## Out of Scope

- real Exotel integration
- real pipeline orchestration
- logging implementation details
- production deployment setup
- live voice or telephony validation

## Acceptance Criteria

- The repository has a coherent Python project skeleton.
- Configuration can be represented through a typed settings layer.
- A minimal application entrypoint exists and imports cleanly.
- Secrets are excluded from committed runtime files.
- Later phases can add routers, services, models, and tests without moving the foundation around.

## Self-Verification

Run normal setup-level checks only, such as:

- import sanity for the new package structure
- settings object import and construction
- minimal app import sanity

Do not claim voice, WebSocket, or Exotel behavior was validated in this phase.

## Ask First

- adding dependencies beyond the agreed stack
- changing Python version target
- introducing infrastructure beyond local development needs

## Never

- commit real secret values
- create implementation for later phases just to "save time"
- claim runtime telephony is validated

## Completion Checklist

- [ ] Initial Python project skeleton exists.
- [ ] `.env.example` exists and contains placeholders only.
- [ ] `.gitignore` protects local secrets and generated artifacts.
- [ ] `app/config.py` defines typed settings structure.
- [ ] Minimal entrypoint imports cleanly.
- [ ] File layout supports later router, service, model, and test additions.
