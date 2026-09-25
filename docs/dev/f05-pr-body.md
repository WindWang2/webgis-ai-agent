# PR: feat(f05): Evidence-Backed Context Revalidation & Safe Reuse (ADR-0215)

## Baseline & Dedup

- **Baseline**: `origin/master @ 9e1ad22907e99cd7b4721153294448ce6496c717` (2026-09-24, live
  re-fetched at execution time; the 2026-09-25 seed snapshot was verified not drifted).
- **Dedup vs open PRs (#1497–#1504, sibling directions f08–f15)**: none of them touches
  `app/services/gis_context/` or `app/services/project_knowledge/` (per-PR changed-file check in
  `docs/dev/f05-context-revalidation-reuse-recon.md` §2). Only benign touches: `chat.py` is NOT
  modified by this PR (hot-path wiring stays inside `gis_context/hotpath.py`), and the
  `app/tools/__init__.py` registration is one additive line (merge-order independent).
- **Dedup vs merged work**: ADR-0206 (#1487) explicitly listed this PR's scope as out-of-scope
  follow-ups (stale-clearing semantics, user-edit union dedup across rebase copies,
  `find_reuse_candidates` ignoring `dataset_fingerprints`) — nothing here is re-implemented.

## Problem Evidence

1. `GISWorkingContext.clear_stale` had **zero callers** — a stale fact could never return to
   current; "会变 stale" could not grow into long-term situational memory.
2. The hot path's reuse fetch passed only `bbox` + `temporal_label` to `find_reuse_candidates` —
   the working context's dataset revisions never reached the verdict engine, so
   `request_input_stale/gone` could not fire and candidates silently carried
   `upstream_unverified`.
3. `UserEditRecord` identity was `(layer_id, kind, seq)` with per-copy `seq` — two replicas
   converging through the store's rebase path could hold one user delivery twice; the MapSpec
   layer already mints `mutation_id` per mutation but it never reached the working context.
4. Stale observability was count-only (no *why*).

## Architecture (ADR-0215; full detail in docs/adr/0215 + docs/dev/f05-…)

- **`gis_context/revalidation.py` (new)** — the only write authority for stale→current:
  - `CLAIM_REVERIFIED`: engine re-reads the live claim, re-checks accepted dataset fingerprints
    against authoritative liveness tokens, re-runs the deterministic `evidence_claim.verify_claim`
    (persisting the authoritative status). Callers may only *name* claim ids — narrative never
    participates; failures produce `rejected` receipts with closed reason codes.
  - `DECISION_REAFFIRM`: exact-text match against a stale decision (match-only — cannot invent).
  - `BASIS_RECONFIRMED` (passive): a stale marker clears only when every fact attributed to that
    field has left the stale set and the observation currently confirms the field. `goal` is
    intentionally outside the closed set.
  - **No TTL anywhere**: restored facts re-stale through the ordinary invalidation engine on the
    next drift. Loop protection is state-based (idempotent no-ops) + passive cap (≤4/turn) +
    passive rejections stay in-memory (a persistent blocker cannot flood the receipt ring or turn
    read-mostly turns into writes).
- **`RevalidationReceipt`** (schema `gis_working_context.v2`): deterministic ids (`rtv-<seq>`),
  evidence refs, engine checks, verdict, prior stale reason — bounded FIFO ring (≤8) on the
  context. Replayable: identical turn sequences reproduce identical receipts.
- **`gis_context/reuse_identity.py` (new)** — `BasisDataset` now carries the authoritative
  `version_fingerprint` (+ resolved `authority_id`) recorded at acceptance via bounded
  `live_version_token` probes; reconciliation *learns* tokens (learning ≠ drift), detects
  **authority-side re-versioning the MapSpec diff cannot see** (emits `DATASET_VERSION_CHANGED`
  and adopts the new token so the event cannot re-fire), and the reuse query feeds
  `{authority_id: accepted_token}` to `find_reuse_candidates`, activating the existing
  `request_input_stale/gone` downgrades. Unresolved refs degrade to honest `upstream_unverified`;
  method keys are never guessed from bare recipe ids.
- **user_edit idempotency**: `op_id` = provenance-carried MapSpec `mutation_id`, projected
  deterministically by `observation.py`; `add_user_edit`/`_rebase` dedupe by op identity — one
  delivery across replicas converges to one record. Rebase now prefers the *newer engine state*
  for decisions/findings (reaffirm/restore re-stamps propagate), and on revision ties the safer
  status wins (stale > supported) so a rebase can never resurrect a revoked conclusion. The
  receipt ring unions by deterministic id (CAS losers keep their evidence trail).
- **Cross-session continuation**: `bind_session_mission` (explicit mission id; org-invisible = no
  existence leak; terminal missions refused + lazily purged; project scope gate) — caller org is
  resolved from server-side sources only (explicit arg → durable binding → session turn-context
  tenant scan), never from the model. Typed tools `webgis_context_revalidate` /
  `webgis_context_bind_mission` (one additive registration line).
- **Observability**: `ContextCardReceipt` gains `stale_reason_kinds`, `reuse_reject_reasons`,
  `rtv_restored`, `rtv_rejected`; the per-turn log line is reason-grade.
- **Flags**: `GIS_CONTEXT_REVALIDATION` (default ON; `0` restores post-#1487 behavior exactly —
  no passive reconfirmation, no fingerprint reconciliation; the explicit tool path stays
  available by design). `GIS_CONTEXT_SCOPES=0` still disables everything byte-identically.

## Key Contracts (single-truth discipline)

- Revalidation reuses the existing currency end-to-end: `ClaimStore` status,
  `verify_claim` verdicts, `live_version_token` probes — **no second verdict system**; receipts
  are observations of engine transitions, not a parallel truth.
- Payload budget: receipt ring + attribution + fingerprints stay inside the fail-closed 16 KB
  payload gate (tested with a full ring).
- Schema `gis_working_context.v2` loads v1 payloads unchanged (defaults); the store row's
  `schema_version` column is now refreshed on update as well.

## Files

- New: `app/services/gis_context/revalidation.py`, `app/services/gis_context/reuse_identity.py`,
  `app/tools/context_revalidation_tools.py`, 4 test files, ADR-0215, recon/design docs.
- Modified: `working_context.py`, `store.py`, `invalidation.py`, `observation.py`, `hotpath.py`,
  `card.py`, `flags.py`, `session_ctx.py` (two additive bounded scans), `app/tools/__init__.py`
  (one line), `CHANGELOG.md`.

## Test Evidence (local, serial, --no-cov)

- New suites: `test_revalidation.py` (20) — closed loop, every reject reason, loop guard,
  marker reconfirm, reaffirm, replay determinism, ring bounds, v1 compat, claim-store currency;
  `test_reuse_identity.py` (11) — exact-reuse with matching fingerprints, authority drift blocks
  exact, request_input_gone hard block, reconcile learn/drift/alias/gone/unresolved;
  `test_user_edit_idempotency.py` (10) — replay dedup, cross-replica rebase convergence,
  user-wins, legacy behavior, decision/finding rebase rules, receipt-ring union;
  `test_revalidation_scenarios.py` (6) — end-to-end closed loop through the hot path, authority
  drift, style-only & no-change non-events, flag-off parity, cross-session continuation,
  terminal-mission refusal + purge.
- Neighborhood regression: `tests/unit/gis_context/` 98 passed; project_knowledge verdicts /
  invalidation / scenario / claim-lookup suites green; gis_world_state / provenance / situation /
  gis_memory consumers 61 passed; chat turn-injection + PI bridge 24 passed; API compatibility
  (openapi snapshot) 12 passed; tool-name integrity & session ownership 41 passed. Ruff clean on
  all touched paths.
- No GitHub Actions wait is required per task discipline; CI will run post-PR.

## Compatibility

- Additive schema (v1-readable), additive DB payload (no migration — payload is JSONB; row
  `schema_version` column updated on save), no API route changes (openapi snapshot green), no
  flag flips, one additive tool-registration line. Kill switches:
  `GIS_CONTEXT_REVALIDATION=0` → post-#1487 behavior; `GIS_CONTEXT_SCOPES=0` → pre-ADR-0206
  behavior.

## Independent Review

Subagent C (independent reviewer) deep-reviewed `origin/master...HEAD` across nine axes
(single-truth, invalidation convergence, concurrency/CAS, tenant isolation, user-wins,
boundedness, compatibility, test effectiveness, P0/P1/P2) — findings and fixes recorded in
`docs/dev/f05-context-revalidation-reuse-review.md` (see final section for verdict).

## Out of Scope

- Recompute orchestration (producing NEW findings under a new basis) — the existing pipeline
  owns computation; this PR owns the freshness protocol around it.
- Expanding captured user-edit kinds beyond `hide` (restyle/reorder details live in MapSpec, not
  the working context).
- The legacy WS presentation channel writing provenance without CAS (pre-existing, disclosed in
  code; untouched).
- LLM context budget (direction 04), TTL semantics (forbidden), text-similarity reuse
  (forbidden).
