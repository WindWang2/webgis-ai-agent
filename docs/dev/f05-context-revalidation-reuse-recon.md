# F05 — Context Freshness / Revalidation / Safe Reuse — Recon

- Date: 2026-09-26
- Baseline: `origin/master` @ `9e1ad22907e99cd7b4721153294448ce6496c717` (2026-09-24, merge of #1494)
- Branch: `zcode/f05-context-revalidation-reuse-20260926-9e1ad229`
- Worktree: `../wt-webgis-f05-context-revalidation-reuse-20260926-9e1ad229`
- Author: ZCode autonomous run (direction F05), recon re-verified from live GitHub state, not the 2026-09-25 seed snapshot

## 1. Baseline state (live re-verification)

- `git fetch origin --prune` → `origin/master = 9e1ad229` (2026-09-24 05:00 +0800). The seed
  snapshot SHA is still current; **no drift**.
- Last functional wave: #1479–#1488 merged 2026-09-21 (9 PRs), followed by a dependabot wave
  #1490–#1496 (docs/CI/deps only, no code overlap with this direction).
- Recent 50 commits are: the #1479–#1488 functional wave + dependabot merges. Nothing after
  9e1ad229.
- Local `master` in the primary working directory is **stale/diverged** (16 ahead / 187 behind;
  missing the whole gis_context subsystem). All work therefore happens in the new worktree
  created from `origin/master`; the primary working directory is untouched.

## 2. Open PR overlap check (changed-file intersection)

Open PRs at recon time: #1489 (dependabot/docker), #1497–#1504 (sibling directions f08–f15).

| PR | Direction | Touches `app/services/gis_context/`? | Touches `app/services/project_knowledge/`? | Overlap risk |
|---|---|---|---|---|
| #1497 f12 | map plan compiler | no | no | none (shares `app/tools/__init__.py` registration list only) |
| #1498 f13 | render runtime data plane | no | no | `app/api/routes/chat.py` — both edit the same file; my changes are confined to `assemble_gis_context_card` internals + `_build_cartography_turn_context` untouched |
| #1499 f08 | workflow resource scheduler | no | no | none |
| #1500 f15 | visual observation/repair | no | no | `app/api/routes/chat.py` (same as above), `tests/quality/snapshots/openapi.json` (I add no routes → no snapshot churn) |
| #1501 f11 | composition contract | no | no | none (its `app/tools/__init__.py` entry is additive, same as mine; merge-order independent) |
| #1502 f10 | cartographic grammar | no | no | none |
| #1503 f09 | trace/replay oracle v3 | no | no | none |
| #1504 f14 | publication/export parity | no | no | none |

**Conclusion: the F05 hot zone (`gis_context/`, `project_knowledge/`, `gis_world_state/provenance.py`)
is free of open-PR conflicts.** The only shared files are `app/api/routes/chat.py` (I make no
changes there — hotpath wiring stays inside `gis_context/hotpath.py`) and the additive
`app/tools/__init__.py` registration list (one line, conflict-trivial).

## 3. Already done (must not rebuild)

- **Working context + invalidation engine (#1487, ADR-0206)** — `app/services/gis_context/`:
  mission-scoped `GISWorkingContext` (schema `gis_working_context.v1`, ≤16 KB payload, revision
  CAS), closed-rule invalidation for `AOI/DATASET_VERSION/TIME_PERIOD/CRS/MEASURE/PRODUCT_GOAL`
  changes, claim `STALE` propagation via the existing `ClaimStore`, deterministic
  `observe_session`, bounded `[GIS_CONTEXT]` card, default-on with `GIS_CONTEXT_SCOPES` kill
  switch, lazy terminal-mission purge, org/project scope guards.
- **Fingerprint-based cross-mission reuse verdicts** — `app/services/project_knowledge/retrieval.py::
  find_reuse_candidates`: liveness re-check against authoritative version tokens
  (`liveness.py::live_version_token`, e.g. `ProjectDataset.version_fingerprint`),
  AOI-containment, temporal/method equality, upstream dataset fingerprint comparison,
  claim-SUPPORTED positive proof, three-value verdict (`exact` / `recompute_partial` /
  `not_reusable`) with readable reasons. Name similarity never participates.
- **Mutation idempotency at the MapSpec layer** — `app/services/mapspec/lifecycle_engine.py`
  `_mutation_dedup` ring (per session, FIFO 64, committed-only entries, checked before CAS) +
  `MutationEnvelope.mutation_id` (server-minted fallback) + provenance entries carrying
  `mutation_id` / `client_optimistic_id` in `detail` (`gis_world_state/mutation.py`).
- **Claim currency** — `ClaimStatus` 6-state enum; `ClaimStore.mark_claim_status`;
  process-local claim lookup `session_ctx.find_claim` (bounded scan, fail-closed to `None`).

## 4. Still missing (F05 scope, confirmed against master)

Exactly the three follow-ups ADR-0206 §Review Record names out-of-scope, plus the two
direction-mandated builds:

1. **No revalidation path.** `wc.clear_stale` has **zero callers** in the repo; a stale finding /
   stale basis marker can never return to current. There is no receipt, no evidence check, no
   loop protection — "stale forever" is the current semantics.
2. **Reuse query from the hot path is under-fed.** `hotpath._fetch_reuse_candidates` passes only
   `bbox` + `temporal_label`; the working context's dataset content revisions are **not** passed
   as `dataset_fingerprints`, so request-level liveness downgrade (`request_input_stale`) never
   fires from the card path and upstream fingerprint agreement is unverifiable.
3. **user_edit identity is per-copy.** `UserEditRecord` dedupes on `(layer_id, kind, seq)` but
   `seq = max+1` is assigned per working-context copy; `_rebase` unions two divergent copies →
   the same logical edit appears twice with different seqs. The MapSpec `mutation_id` exists in
   provenance but never reaches the working-context edit record.
4. **Cross-session continuation is implemented but untested as a scenario.** Binding/purge/scope
   guards exist; there is no end-to-end test that session B resumes session A's mission with
   stale facts filtered and no cross-org leakage through the reuse path.
5. **Observability is count-only.** The turn receipt counts stale fields and reuse verdicts but
   not *reasons* (why stale, why reuse rejected, revalidation outcomes).

## 5. Overlap / Already Done / Still Missing / Must Not Touch / Integration Seams

| Column | Content |
|---|---|
| **Overlap** | None with open PRs (§2). Within master: none — no PR is open against gis_context. |
| **Already done** | Invalidation rule table; claim STALE propagation; fingerprint reuse verdicts; MapSpec mutation dedup ring; revision-CAS store with org guards; lazy purge; card stale filtering. |
| **Still missing** | Revalidation protocol + receipts; fingerprint-fed reuse query; user-edit op identity across rebase copies; cross-session continuation scenario coverage; reason-grade observability. |
| **Must not touch** | `mission_runtime` lease/epoch semantics (chat writes must never touch the mission lease — ADR-0206 store doctrine); `project_knowledge` verdict semantics (verdicts already fail-closed; I only *feed* them better inputs); MapSpec `_mutation_dedup` semantics (orthogonal layer); claim verify pipeline internals (I consume its verdicts, never fabricate them); TTL-based auto-restore (forbidden by direction). |
| **Integration seams** | (a) `hotpath.assemble_gis_context_card` step between invalidate-save and render — revalidation pass + fingerprint reuse query; (b) `observation.py` provenance projection — carry `mutation_id` as edit op identity; (c) `session_ctx.find_claim` / `ClaimStore` — live claim status for revalidation checks; (d) `liveness.live_version_token` — dataset fingerprint confirmation for restore evidence; (e) `app/tools/__init__.py` — one additive registration line for the typed revalidation tool. |

## 6. Known test baseline

- `tests/unit/gis_context/` (7 files, 51 tests): **51 passed** on the worktree at 9e1ad229
  (runtime ~44 s, serial). This is the pre-change neighborhood baseline.
- Master-level known failures: none observed in this directory; broader-suite flake status not
  re-validated in this recon (out of F05 scope; the PR runs targeted + neighborhood suites only).

## 7. Producer-anchor notes (for the implementer)

- `ProvenanceEntry.detail` carries `mutation_id` for every successful mutation
  (`gis_world_state/mutation.py` single + batch paths) — this is the stable cross-replica op id
  for user edits; `client_optimistic_id` is a secondary echo, not guaranteed unique.
- `observation._user_hidden_from_provenance` currently drops everything but `target`; the
  mutation_id is available one key away (`entry["detail"]["mutation_id"]`).
- `store.save` update path does not refresh the row's `schema_version` column (insert-only) —
  a payload-schema bump must also update the column on update.
- `card.render_gis_context_card` filters `stale/contradicted/unsupported` findings; a restored
  finding therefore renders automatically once its status flips back — no card change needed
  beyond receipt counts.
- The `ReuseQuery.dataset_fingerprints` namespace is `project_dataset` authority ids
  (`ds_*`); working-context `BasisDataset.ref_id` is the MapSpec source ref. Alignment holds
  when MapSpec sources carry project-dataset refs (the lifecycle engine prefetches
  `content_revision` per ref at attach time); where the namespaces differ the lookup degrades to
  `upstream_unverified` (fail-closed, honest) — never a false positive.
