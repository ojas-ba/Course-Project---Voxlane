# PHASE 4 LLM ADAPTER AND METRICS

## Goal

Add bounded LLM adapter behavior and richer observability for model steps.

## Scope

- Implement classification/extraction/phrasing boundaries and metrics capture.

## Out Of Scope

- End-to-end production scaling concerns or queue architecture.

## Files Likely Affected

- LLM adapter module, metrics schema/logging, related tests and dashboard exposure hooks.

## Acceptance Criteria

- LLM interactions are bounded, measurable, and traceable in logs/metrics.