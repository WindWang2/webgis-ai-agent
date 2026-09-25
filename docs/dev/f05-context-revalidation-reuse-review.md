# F05 — Context Revalidation & Safe Reuse — Independent Review Record

- Date: 2026-09-26
- Reviewer: Subagent C (independent, read-only; not the implementer)
- Scope: `origin/master...HEAD` @ implementation commit `db809783` + review-fix commits
- Method: nine-axis deep review (single-truth / invalidation convergence / concurrency-CAS /
  tenant isolation / user-wins / boundedness / compatibility / test effectiveness incl. mutation
  probes / P0-P1-P2 classification), plus an independent full-suite run.

## Verdict

**ACCEPT-AFTER-FIXES** → **fixed in this PR** (all P1 + all actionable P2; see ledger below).
The initial review found **no P0**, confirmed the engine core (single write authority, restore
re-stamping with mandatory re-stale, attribution blocking, rebase safety rules, op_id
idempotency, single-CAS-save semantics, tenant fail-closed behavior, boundedness) and validated
98→101 tests green with genuine negative cases and four mutation probes killed by the suite.

## Findings ledger

| ID | Level | Finding | Resolution |
|---|---|---|---|
| P1-1 | P1 | `bind_session_mission` used the **raw** (tool: empty) `org_id`/`project_id` args for the scope gate and the binding write instead of the resolved org → tool-shaped bind was a permanent `scope_mismatch` dead end (fail-closed, no leak). | Fixed: gate + `_persist_binding` now use the resolved `org`; project falls back to the mission's own record (server-side attribution). Regression test: `test_tool_shaped_entries_resolve_scope_server_side` (bind via session_id only). |
| P1-2 | P1 | Same root cause in `request_revalidation`: project-scoped missions (the auto-bind norm) hit a permanent `scope_mismatch` because no project was resolvable from the empty arg. | Fixed identically (`project = explicit or wc.project_id`). Covered by the same regression test (revalidate via session_id only restores). |
| P1-3 | P1 | The hot path fed the reuse query the **freshly resolved live token map** as the "claimed" fingerprints; retrieval's request-level check compares claimed vs live → tautology → `request_input_stale` could never fire on the wired path (the unit test only covered the basis-derived fallback). | Fixed: `accepted_fingerprints(wc)` snapshot is taken **before** reconciliation adopts drift and is what the fetch claims; drift adoption + the invalidation event still fire in the same turn. Regression test: `test_reuse_query_claims_accepted_tokens_not_live` (captures the fingerprints reaching the fetch across a drift-adopting reconcile). |
| P2-1 | P2 | Flag contract drift: ADR D9 implied the revalidation flag covers everything, CHANGELOG/tests said the explicit tool path stays available. | Resolved by contract decision + docs: `GIS_CONTEXT_REVALIDATION` gates **automatic** behavior (passive pass + reconcile) only; the explicit tool path stays available (user-driven, individually checked); both tool entries are gated by the master `GIS_CONTEXT_SCOPES` (`flag_off` refusal). ADR D9, flags.py docstring, CHANGELOG aligned; `test_master_switch_gates_tool_entries` added. |
| P2-2 | P2 | `has_receipt` was dead code and ADR D4 described an evidence-digest ring probe that was never implemented (the real guard is state-based). | Removed `has_receipt`; ADR D4 rewritten to describe the actual state-based guard (not_stale no-op + monotonic re-stamp + restored-only passive cap). |
| P2-3 | P2 | Tool-path CAS loss rebase writes a one-turn-stale basis/stale (self-heals next turn). | Documented in `_rebase` docstring (accepted corner; restore/decision state survives via identity rules). |
| P2-4 | P2 | `upsert_finding` dropped `stale_reasons` for new findings during rebase (over-blocking, safe direction, but degraded marker decidability). | `upsert_finding` gained `stale_reasons`; rebase passes attribution through. |
| P2-5 | P2 | Tool-path rejected receipts were not persisted when nothing restored — contradicted "every attempt is a durable record". | Save now fires whenever receipts exist (`if receipts:`), not only on restores. |
| P2-6 | P2 | `basis.datasets` observation evidence token was a bare `n=<count>`. | Replaced with a content-addressed digest of `ref@revision` pairs. |
| P2-7 | P2 | Design doc referenced extending `test_continuation_scenario.py`; actual coverage lives in `test_revalidation_scenarios.py`. | Design doc corrected. |
| (review note) | P2 | Marker cap counted rejections → blocked markers could starve restorable ones within a turn. | Cap now counts **restores only** (rejections still bounded by the field scan); `test_passive_marker_cap_counts_restores_only` proves no starvation + cap enforcement. |

## Reviewer-validated strengths (kept verbatim from the report)

- Restore re-stamps `basis_revision` → strict `< new_revision` invalidation guarantees the next
  drift re-stales (mutation-killed test).
- Rebase tie rule: stale beats supported on equal revision — a rebase can never resurrect a
  revoked conclusion (mutation-killed test); restores win on strictly higher revision.
- `_refresh_basis` fingerprint carry-over × reconcile drift-adoption converges in one turn for
  pure authority drift (no infinite re-trigger, no dead path).
- Tenant isolation: org resolution chain never model-supplied; `no_org_context` refuses
  unresolvable callers; claim-store scans contained by requiring the claim in the caller's own
  context first.
- No TTL anywhere; passive rejections never persist (read-mostly turns stay read-mostly);
  payload stays inside the 16 KB fail-closed gate with a full receipt ring.

## Post-fix verification

- `tests/unit/gis_context/`: **101 passed** (51 pre-existing + 50 new) — serial, `--no-cov`.
- Neighborhood: project_knowledge verdicts/invalidation/scenario/claim-lookup,
  gis_world_state/provenance/situation/gis_memory consumers → **164 passed**.
- Ruff clean on all touched paths; openapi/API-compatibility snapshot 12 passed (no routes
  changed).
