# REVIEW — G14 independent review record

## Round 1 (2026-11-10, reviewer: independent subagent, not the author)
Verdict: **no P0; 1×P1 + 8×P2 + several P3**. Full coverage list included: one-write-path, three-pass apply idempotency (empirically verified), wfid determinism incl. the +=1000 shift reasoning, tenancy (12 endpoints owner-gated, session injection verified at registry.py:656/1428), bounds, topology honesty (declared-only + commit-recheck-before-write), executor ordering, merge disclosure, frontend 2-call discipline, refresh parity, i18n parity, _wfid-first blast radius, DEDUP claims.

### Findings → fixes (commit c0dc82393)
| ID | Finding | Fix |
|---|---|---|
| P1-1 | merge parents selected out of fc order → undo restores drifted order → fingerprint 409 (merge permanently non-undoable) | journal.py: created-with-index replayed sorted by (index, original order); merge_features emits parent deltas sorted by fc index. Regression: test_merge_out_of_order_parents_undo_restores_exact_order |
| P2-1 | split/merge child wfid collision unguarded → child silently lost | collision shift loop in both operators |
| P2-2 | read_only gate divergence (envelope vs metadata) + pending-plan window | preview gate checks both (best-effort metadata); commit-time envelope recheck (fail-closed) |
| P2-3 | sketch wiped before mount confirmed | reset only when layer_id present |
| P2-4 | refresh abort-signal regression | editor-owned AbortController per edit target; signal threaded to refresh |
| P2-5 | vertex-only hit testing (polygon interior dead) | vertex OR point-in-polygon (exterior minus holes) |
| P2-6 | stale split/vertex targets across tool switches | tool-change cleanup effect |
| P2-7 | hole-preservation invariant claimed not enforced | union-area invariant enforced in split_feature (typed rejection) |
| P2-8 | container property values could enter datasets (then uneditable) | scalar-only on create new fields + promote validation |
| P3 | top-level id fallback; merge 0==False distinct | fixed |

### Accepted-as-is (documented)
- no_overlap m²→deg² lon-scale approximation (disclosed in finding detail).
- MAX_CREATED_PER_PLAN enforced per-op (bounded by 8-op limit + 256KB entry gate).
- MVT (>5000 feature) layers have no inline features → map gestures can't pick features; server-side ops still work (documented limitation).
- `dataset_read_only` reserved code stays reserved (used `dataset_read_only_source`).

## Round 2 (re-review of c0dc82393): found NEW blocking regression
The P1 fix's collection-time upper bound (`index <= len(out)` after removals) mis-routed well-formed high removed-indices to the append path → multi-delete undo scrambled order → fingerprint 409 (35 failing patterns N≤8 scan vs 0 pre-fix). Also: `datasets._artifact_metadata` isinstance(dict) no-op; commit-time envelope recheck unreachable behind CAS (metadata window still open).

## Round 3 (final verification of 4c1801382): **NON-BLOCKING — clear to merge**
- Reviewer independently re-verified with own exhaustive sweep beyond the author's (N≤9, every subset size, in-order + 3 random delta shuffles, mixed delete+created shuffled, malformed-index clamp determinism): **0 order/fingerprint failures**; old no-presence journals degrade to update semantics.
- Three genuine REST regressions verified (first-3-of-4 / scattered {1,4,5}-of-7 / tail {3,4}-of-5, exact wfid+name order, no 409).
- `_artifact_metadata` fix verified; commit_plan metadata read_only mirror verified with correct FeatureEditBlocked passthrough; promote non-dict guard = typed INVALID_FEATURES.
- Full suite 123 passed (reviewer reproduced).
- Remaining known limitations = accepted P3s (m²→deg² lon-only approx, per-op MAX_CREATED naming, MVT edit gating, Multi* hit-testing) — none blocking. Verdict: "Ship it."

