# F05 — Context Revalidation & Safe Reuse — Design

- Date: 2026-09-26
- ADR: `docs/adr/0215-context-revalidation-safe-reuse.md`
- Recon: `docs/dev/f05-context-revalidation-reuse-recon.md`
- Baseline: `origin/master @ 9e1ad229`

## Module map (all new code in `app/services/gis_context/` unless noted)

| Module | Status | Content |
|---|---|---|
| `working_context.py` | extended (schema `gis_working_context.v2`) | `EvidenceRef` / `CheckResult` / `RevalidationReceipt` contracts; `UserEditRecord.op_id`; `FindingRef.stale_reasons` / `DecisionRecord.stale_reasons`; `BasisDataset.version_fingerprint`; root `revalidations` ring (≤8) + `rtv_seq`; `add_user_edit(op_id=…)` identity dedup; v1 payload compat |
| `revalidation.py` | **new** | Closed revalidation engine: `revalidate_claims` / `reaffirm_decisions` / `reconfirm_markers` (passive) → `List[RevalidationReceipt]`; loop guard; deterministic receipt ids (`rtv-<seq>`); write authority lives here and nowhere else |
| `reuse_identity.py` | **new** | `reconcile_dataset_fingerprints(wc, db, project_id)` (authority-token learning + drift detection); `reuse_query_from_context(wc)` (full fingerprint-fed `ReuseQuery`) |
| `observation.py` | extended | `SessionObservation.user_edit_ops: Dict[layer_id, op_id]` from provenance `detail.mutation_id` |
| `invalidation.py` | extended | per-field `stale_reasons` attribution on findings/decisions; user edits recorded with `op_id` |
| `store.py` | extended | `schema_version` refreshed on update path; rebase prefers newer engine state for decisions (`basis_revision` monotonic), unions edits by `op_id` |
| `flags.py` | extended | `GIS_CONTEXT_REVALIDATION` (default ON, killable) |
| `hotpath.py` | extended | fingerprint reconcile step (in `to_thread`) folded into the single invalidation save; passive marker reconfirmation; fingerprint-fed reuse fetch; reason-grade receipt + log; `bind_session_mission` (cross-session continuation entry) |
| `card.py` | extended (receipt only) | `ContextCardReceipt` gains `stale_reason_kinds` / `reuse_reject_reasons` / `rtv_restored` / `rtv_rejected` |
| `app/tools/context_revalidation_tools.py` | **new** | typed tools `webgis_context_revalidate` (claim ids + reaffirm texts) and `webgis_context_bind_mission` (explicit mission rebinding for a new session); one additive registration line in `app/tools/__init__.py` |

## Revalidation semantics (normative)

Restore paths (each produces a receipt, success **and** failure):

1. **CLAIM_REVERIFIED** (`claim_ids=[…]` via tool): engine re-resolves the live claim
   (process-local bounded lookup), re-checks basis dataset liveness (recorded
   `version_fingerprint` vs `live_version_token` — drift ⇒ `basis_advanced`), then re-runs the
   deterministic `evidence_claim.verify_claim` (persisting the authoritative status). `SUPPORTED`
   ⇒ finding restored: status synced, `basis_revision` re-stamped to the current revision,
   `stale_reasons` cleared. Anything else ⇒ `rejected` + closed reason code, state untouched.
2. **DECISION_REAFFIRM** (`reaffirm_texts=[…]` via tool): exact-text match against a
   `stale_basis` decision; re-stamps `basis_revision`, clears the flag. Cannot invent decisions
   (match-only).
3. **BASIS_RECONFIRMED** (passive, hot path): a stale *marker* (one of the six rule-table field
   families; `goal` intentionally excluded) clears when no finding/decision carries that field in
   `stale_reasons`, no legacy unattributed stale records remain, and the observation currently
   knows the field. Cap ≤4 markers per turn.

Loop protection: one restore per (target, basis revision, evidence digest) — the receipt ring is
checked before restoring; a repeat is `rejected/loop_guard`. No TTL anywhere: restored facts
re-stale through the normal invalidation engine on the next drift.

## Receipt id / replay determinism

`receipt_id = f"rtv-{rtv_seq}"` with `rtv_seq` persisted on the context and incremented only on
appended receipts; checks/verdicts are pure functions of (context, observation, live claim state,
live tokens) — replaying the same turn sequence reproduces ids and verdicts.

## Rebase / concurrency notes

- `expected_revision` captured **after load, before any mutation** (invalidation + revalidation
  bumps fold into one CAS save).
- Edits: identity = `op_id` (MapSpec `mutation_id`) when present; rebase unions by the same
  identity, so one user delivery converges to one record across replicas. Legacy records without
  `op_id` keep the historical append behavior.
- Decisions: rebase replaces a stored decision with an incoming one carrying a strictly greater
  `basis_revision` (reaffirm propagation across replicas); otherwise stored history wins.

## Test plan (tests/unit/gis_context/)

| File | Covers |
|---|---|
| `test_revalidation.py` | claim restore happy path; each reject reason; loop guard; marker reconfirm; reaffirm; receipt bounds/ring; replay determinism; v1 payload compat |
| `test_reuse_identity.py` | fingerprint reconcile (learn/drift/gone); reuse query construction; retrieval integration: authority drift ⇒ no `exact` |
| `test_user_edit_idempotency.py` | op_id dedup; cross-replica rebase convergence; observation op propagation; negatives |
| `test_revalidation_scenarios.py` | end-to-end: invalidation→reverify→current; CRS/dataset negatives; style-only & no-change non-events; flag-off no-op |
| `test_continuation_scenario.py` | extended: session-B bind, stale filtering, terminal purge, org/project guards |

Neighborhood regression: full `tests/unit/gis_context/` + `tests/test_project_knowledge_*.py`
(verdict semantics untouched — guard) + hotpath-adjacent suites.
