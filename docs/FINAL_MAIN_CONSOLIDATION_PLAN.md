# Final main consolidation

Prepared on **8 November 2026 (Asia/Kolkata)** for `ojas-ba/VoiceAgent---Project`.

**Final PR branch:** `consolidate/final-main`. **Target:** existing `main`.
The branch contains the latest Sarvam voice agent, interview workflow, camera monitoring,
and UI, with both original Git histories preserved. The review findings are addressed and
the normal automated checks pass. The PR must remain unmerged until human acceptance.

## Remote branch review

All six remote branches and tags were fetched before implementation. These are the source
tips reviewed; dates are commit dates in India time.

| Branch | Commit | Date | Disposition |
|---|---|---|---|
| `ai-interviewer` | `8b2b2b4` | 30 Oct 2026, 14:32:58 | Latest implementation; chosen code baseline. |
| `ai-interviewer-prototype` | `ceb3bc2` | 29 Oct 2026 | Already included in the latest branch. |
| `main` | `6be9fa2` | 26 Oct 2026 | Existing main history preserved in the integration branch. |
| `fix/browser-voice-session` | `52d369b` | 26 Oct 2026 | Already included in existing main. |
| `browser-voice-demo` | `4cf6811` | 16 Oct 2026 | Already included in existing main. |
| `v3-pipecat-voiceagent` | `e401739` | 10 Jun 2026 | Already included in existing main. |

The remote default branch is already `main`. Explicit branch names are used because the
local cached `origin/HEAD` still references the older V3 branch.

## How the histories were consolidated

The Sarvam commit `36d0bae` has no parent. The interview branch therefore had no common
ancestor with existing main, despite continuing its browser-agent files.

```text
Existing history: b001cdf ... browser demo/fix merges ... 6be9fa2
Separate history: 36d0bae -> ceb3bc2 -> 8b2b2b4
                                      |
                                      + main history joined in a550196
```

The integration branch was created from `origin/ai-interviewer`. Merge commit
`a55019660086842de70e4dca82f8898752897ae5` connects it to the existing main:

```powershell
git switch -c consolidate/final-main origin/ai-interviewer
git merge --allow-unrelated-histories --no-ff --strategy=ours origin/main -m "Preserve main history with final interview code"
```

This deliberately preserves the latest branch's complete file tree while recording main
as a second parent. The tree was confirmed identical to `8b2b2b4` immediately after that
merge, before the focused corrections below. Both source tips are ancestors of this branch.

The reviewed baseline differs from existing main in 99 files: 55 added, 44 modified, none
deleted. Every older remote branch is covered by one of the two preserved histories, so
separate merges of those branches are unnecessary.

## Code review and final corrections

| Area | Result |
|---|---|
| Voice | Retains Sarvam realtime STT, Cerebras, Bulbul v3 TTS, and English/Hindi/Kannada/Marathi/Telugu support. The ordinary Talk page remains available. |
| Transport and observers | Keeps audio-only SmallWebRTC and pipeline audio at 16000 Hz, Silero VAD, task-level observer registration, and `UserBotLatencyObserver`. |
| Lifecycle and storage | Retains one media runner, cancellation/drain before finalization, save/checkpoint retry handling, JSON-safe runtime state, config snapshots, and existing conversation envelopes. |
| Interviews | Includes reviewed immutable plans, scoped candidate tokens, explicit progression, accepted-answer checkpoints, reports, human notes, pause/resume, and restart interruption handling. |
| Camera | Keeps consent/withdrawal, local worker inference, compact metadata, honest missing-coverage records, and separation from technical ratings. |
| Startup gap fixed | Missing `candidate-view.js` or shared stylesheets now fail startup with the missing filenames, before configuration/data initialization. Four regression cases cover partial UI installations. |
| Documentation fixed | Updates the provider mission to Sarvam, includes model preparation in both setup guides, and links this report from the documentation index. |

The active Sarvam path intentionally replaces Deepgram/Cartesia. Flux eager EOT and
speculative generation remain unavailable. Historical conversation snapshots are not
rewritten. Consolidation does not restore retired behavior.

No additional project dependencies, routes, persistence formats, audio rates, observer
registration changes, workers, or infrastructure were introduced by the final corrections.
Existing interview routes/dependencies are covered by the accepted
[ADR-007](decisions/ADR_007_SINGLE_INTERVIEW.md).

## Verification completed

Checks ran with Python **3.12.1** and Node **22.14.0**, using the existing project
dependencies and locked frontend packages.

| Check | Result |
|---|---|
| `python -m pytest -m "not live"` | **484 passed, 3 deselected**, including the new startup regressions and local WebRTC/RTVI protocol checks. |
| `npm --prefix frontend test` | **70 passed**, including actual installed SDK signaling/message parsing with device/provider I/O mocked. |
| `npm --prefix frontend run build` | Passed; all browser bundles built. |
| `npm --prefix frontend run prepare:vision` | Both pinned models downloaded and SHA-256 verified. |
| Asset inspection | Candidate presentation bundle, tracked stylesheets, model files, and matching WASM assets are present. |
| Dependency consistency | All 100 installed Python packages compatible. |
| Git checks | Whitespace check passed; both source histories are ancestors; pre-fix merge tree matched the reviewed latest source. |

The earlier documentation-only review could not run the full suites because the local
environment lacked dependencies. Those verification gaps are now resolved. Test output
still contains upstream deprecation warnings; live provider tests were deliberately not run.

Generated bundles, models, the isolated environments, and verification logs remain ignored.
No real `.env`, local configuration/history, or credentials are included in the commits.

## Human acceptance before merging

A person must check the actual browser behavior:

- Talk: one complete greeting; five languages and switching; memory; Mute/End; saved
  history; recovery after refresh, disconnect, or provider failure.
- Interviews: draft, review, activation, private candidate link, device preparation,
  question/answer flow, pause/resume, permanent ending, reports, and human notes.
- Camera: consent and withdrawal, voice-only fallback, visible unavailable/gap states,
  and observations kept separate from answer assessment.
- Voice quality, echo behavior, perceived latency, real permissions, and actual camera
  recognition on the intended devices.

Automated and silent protocol checks do not establish audible behavior or realistic latency.
Use the [runbook](RUNBOOK.md) and [interview checklist](INTERVIEWS.md) for those checks.

## Final merge

Review the single PR from `consolidate/final-main` into `main`. Choose **Create a merge
commit** to retain both histories; do not squash or rebase this consolidation.
No force push or replacement of main is required. Keep the source branches through acceptance.

After merging, fetch and verify:

```powershell
git fetch origin --prune
git merge-base --is-ancestor 6be9fa2 origin/main
git merge-base --is-ancestor 8b2b2b4 origin/main
git diff --exit-code origin/consolidate/final-main origin/main
```

Both ancestry checks and the tree comparison must exit 0, assuming no subsequent changes
to main. This task prepares the PR; it does not perform the final merge or deployment.
