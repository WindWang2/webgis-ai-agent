# ADR-0215: Evidence-Backed Context Revalidation & Safe Reuse

- Status: Proposed
- Date: 2026-09-26
- Line: `harness/f05-context-revalidation-reuse-v1`
- Related: ADR-0206 (GIS Context Layered Scopes — the invalidation engine this ADR completes),
  ADR-0197 (Durable Mission Runtime), ADR-0212 (Trace/Replay Decision Provenance),
  `project_knowledge` projection reuse verdicts, `evidence_claim` currency,
  `gis_world_state` mutation envelopes

## Context

ADR-0206 shipped the invalidation half of context freshness: basis drift stales working-context
fields, decisions and findings, and propagates `STALE` into the claim store. Its own review
record names what was deliberately left out, and each gap breaks the long-term loop:

1. **Stale is terminal.** `GISWorkingContext.clear_stale` has zero callers. A fact that went
   stale can never become current again, so "会变 stale" never grows into situational memory —
   every drift permanently amputates context.
2. **Reuse queries are under-fed.** The hot path's reuse fetch passes only bbox + time label;
   the dataset content revisions the working context already holds never reach
   `find_reuse_candidates`, so request-level input-liveness downgrade cannot fire and upstream
   fingerprint agreement stays unverifiable.
3. **User-edit identity is per-copy.** Edit records dedupe on `(layer_id, kind, seq)` with a
   per-copy `seq`; two replicas converging through the store's rebase path can hold the same
   logical edit twice. The MapSpec layer already mints `mutation_id` per mutation, but it never
   reaches the working-context edit record.

Non-goals (recon-verified as solved elsewhere — do not rebuild): TTL/expiry semantics (forbidden
here — time alone may never restore anything); prompt context budgeting; text-similarity reuse;
MapSpec mutation dedup (lifecycle_engine `_mutation_dedup` ring); claim verification itself
(the existing verify pipeline owns verdicts; this ADR only *consumes* them).

## Decision

### D1 — Revalidation is evidence-checked state transition, never narrative (new `revalidation.py`)

A stale fact returns to current only through a deterministic engine path that re-checks evidence
itself. Three closed revalidation kinds:

- `BASIS_RECONFIRMED` — for working-context stale *markers* (`wc.stale` entries): the current
  deterministic `SessionObservation` re-confirms the accepted basis for that field (the observed
  world matches the basis the conclusions were accepted under, and the field's attributed
  findings are all resolved). Evidence = the observation's field values, recorded in the receipt.
- `CLAIM_REVERIFIED` — for stale findings: the live claim (resolved via the existing bounded
  process-local claim lookup) is `SUPPORTED` **and** its supporting evidence tokens match the
  current basis fingerprints (dataset content revisions re-checked against the authoritative
  liveness token). The engine never trusts the caller's description of the claim — it re-reads
  the claim and re-checks the tokens.
- `EDIT_REPLAY` — not a revalidation: user edits are never invalidated (user-wins, unchanged).

Write authority: only the engine functions in `revalidation.py`, invoked from (a) the hot-path
assembly (passive re-confirmation) and (b) one typed tool (active re-verification request).
LLM/tool callers can only *name* a claim id or field; every upgrade decision is made by the
engine against live state. An LLM cannot upgrade by assertion — a receipt with verdict
`rejected` + reason code is the only output when checks fail.

### D2 — `RevalidationReceipt` (bounded, serializable, replayable)

```
RevalidationReceipt:
  receipt_id     deterministic: rtv-<mission>-<n> (n = per-context monotonic counter, persists in payload)
  kind           BASIS_RECONFIRMED | CLAIM_REVERIFIED
  target         field path ("basis.aoi") or claim id
  basis_revision working-context revision the restore is stamped under (post-bump)
  prior_reason   the stale reason being cleared (verbatim from wc.stale)
  evidence       ≤4 × EvidenceRef {ref, token} — observation values / claim evidence tokens / live liveness token
  checks         ≤4 × {check, verdict(pass|fail), detail} — the exact engine checks that ran
  verdict        restored | rejected
  reject_reason  closed reason code (evidence_mismatch | claim_not_supported | target_unknown |
                 loop_guard | basis_advanced | ...)
  turn_id        bounded
```

Stored in a bounded FIFO ring on the working context (`revalidations`, ≤8) — the durable record
of every restore attempt, successful or not. No `valid_until`: a restored fact re-stales through
the *existing* invalidation engine the moment the basis drifts again. Receipt ids and checks are
pure functions of (context, observation, live claim state) → replaying the same turn sequence
reproduces the same receipts byte-for-byte.

### D3 — Findings carry their staleness attribution (schema `gis_working_context.v2`)

- `FindingRef` gains `stale_reasons: List[str]` (≤4) — set by the invalidation engine when the
  finding is staled, cleared on restore. This is what makes `BASIS_RECONFIRMED` decidable: a
  stale marker clears only when every finding attributed to it has left the stale set.
- `UserEditRecord` gains `op_id: str` — the MapSpec `mutation_id` (provenance-carried) when
  known, else "". Identity for dedup; `seq` stays a per-copy ordering hint only.
- Root gains `revalidations: List[RevalidationReceipt]` (≤8) and `rtv_seq: int` (receipt
  counter).
- `from_payload` accepts v1 payloads (new fields default); the store row's `schema_version`
  column is now also refreshed on update (was insert-only).

### D4 — Loop protection (bounded oscillation guard)

Per (claim, basis_revision) the engine restores at most once per distinct evidence token: a
claim that re-stales after a restore at the same basis revision is rejected with
`loop_guard` until a *new* verification event (different evidence token) exists. Passive
re-confirmation is capped (≤4 candidate markers per turn). Receipt ring FIFO keeps the payload
bounded. No counters grow unbounded; no restore path can loop with itself.

### D5 — Reuse identity is fingerprint-fed (new `reuse_identity.py` + hotpath wiring)

`reuse_query_from_context(wc) -> ReuseQuery` builds the full fingerprint query from the working
context: `bbox = basis.aoi_bbox`, `temporal_label = basis.time_period`,
`dataset_fingerprints = {ref_id: content_revision for accepted datasets}` (bounded ≤12).
`method_key` stays `None` unless the basis actually carries a method — unknown never guessed
(fail-closed, consistent with retrieval discipline). The hot-path reuse fetch switches to this
constructor, which activates the existing request-level input liveness downgrade: a dataset the
plan still claims but whose authoritative fingerprint has moved marks every candidate
`not exact` with `request_input_stale:<ds>` — closing ADR-0206 follow-up #3 without touching
verdict semantics.

### D6 — Cross-replica user-edit idempotency

- Observation projects user-hidden layers **with** their provenance `mutation_id`s; the
  invalidation engine records edits via `add_user_edit(op_id=...)`.
- `add_user_edit` dedupes on `op_id` when present (fallback to the old `(layer_id, kind, seq)`
  key for legacy records), and `_rebase` unions edits by the same identity → two replicas
  replaying one user delivery converge to one record regardless of which copy assigned which
  `seq`. Conflicts still resolve through the store's revision CAS; user-wins is preserved
  (edits are never overwritten by the engine, only deduped).

### D7 — Cross-session continuation hardening (tests + one guard)

Binding durability, lazy terminal purge, org/project scope guards and card stale filtering all
exist from ADR-0206; F05 adds the missing scenario coverage (session B resumes session A's
mission: stale findings filtered, no cross-org reuse, terminal mission purged on load) and one
hardening: the card renders a restored finding only after its claim re-check (D1), so a
cross-session reader can never see a restored-but-unverified fact.

### D8 — Observability: reason-grade receipts

`ContextCardReceipt` gains `stale_reason_kinds` (bounded distribution of stale reasons by change
kind), `reuse_reject_reasons` (bounded stale_cause histogram) and revalidation counts
(`rtv_restored`, `rtv_rejected`). The per-turn log line extends accordingly (still one bounded
line). Rejected revalidations are first-class observability — a restore attempt that failed a
check is as important as one that succeeded.

### D10 — Fingerprint vocabulary bridge (authoritative tokens on the basis)

Two fingerprint vocabularies exist and must not be compared across: MapSpec sources carry an
integer-ish `content_revision` (what `diff_against` compares for mapspec-side drift), while the
project_dataset authority and the reuse ref-tags speak sha256 `version_fingerprint`
(`liveness.live_version_token`). `BasisDataset` therefore gains `version_fingerprint: str` —
the authoritative token **at acceptance time**, recorded by a hot-path reconciliation step
(`_reconcile_dataset_fingerprints`, bounded ≤12 single-row scalar reads, run in the existing
`asyncio.to_thread` bucket):

- token unknown → record it (learning, not drift — consistent with the diff discipline);
- recorded token ≠ live token → emit `DATASET_VERSION_CHANGED` (authority-side drift the
  mapspec-side diff cannot see — the dataset under an unchanged MapSpec was re-versioned);
- authority row gone → skip (honest unknown; the reuse query reports `request_input_gone`).

The reuse query then feeds `{authority_id: recorded_token}` as `dataset_fingerprints`, which
activates retrieval's existing request-level liveness downgrade for genuinely stale planner
inputs. Namespace mismatch (ref_id is not a project_dataset id) degrades to no fingerprint →
`upstream_unverified` — never a false positive.

### D9 — Flag

`GIS_CONTEXT_REVALIDATION` (default ON, `0` restores post-#1487 behavior exactly). The
revalidation pass mutates state (clears markers, restores findings), so it gets its own kill
switch, one level below the master `GIS_CONTEXT_SCOPES` gate.

## Failure / Security / Resource Semantics

- **No TTL**: nothing time-based ever flips stale→current. Every restore carries evidence refs
  recorded at restore time.
- **Fail-closed**: unknown claim, unknown field, evidence token mismatch, loop guard → receipt
  verdict `rejected` + reason code; state unchanged.
- **Boundedness**: receipts ≤8 × (~300 B), stale_reasons ≤4/finding, evidence ≤4/receipt,
  revalidation attempts ≤4 markers + ≤8 claims per turn. Payload still hard-gated at 16 KB.
- **Isolation**: revalidation only touches the caller's mission context (org-guarded store);
  claim lookup is the existing process-local bounded scan; liveness checks are the existing
  single-row scalar queries.
- **Replay**: receipt ids, checks and verdicts are pure functions of recorded inputs
  (observation, claim state, live tokens) — ADR-0212 replay discipline holds.

## Acceptance Matrix

| Requirement | Test |
|---|---|
| Invalidation→re-verify→current closed loop | `test_revalidation.py` (claim path) |
| Basis marker restore requires attributed findings resolved + observation re-confirmation | `test_revalidation.py` |
| LLM/assertion cannot upgrade; failed checks → rejected receipt, state unchanged | `test_revalidation.py` negative class |
| Loop guard: restore→re-stale→restore at same basis revision rejected | `test_revalidation.py` |
| Replay determinism: same inputs → same receipt ids/verdicts | `test_revalidation.py` |
| Dataset version change blocks reuse (`request_input_stale`); style-only/no-change negatives | `test_reuse_identity.py`, `test_revalidation_scenarios.py` |
| CRS change invalidates; re-confirmed basis restores; user restyle never invalidates | `test_revalidation_scenarios.py` |
| Cross-replica edit replay: same mutation_id → single record after rebase | `test_user_edit_idempotency.py` |
| Cross-session resume: stale filtered, terminal purge, org guard | `test_continuation_scenario.py` (extended) |
| Kill switch | `GIS_CONTEXT_REVALIDATION=0` no-op test |
| Observability | receipt field tests in `test_revalidation.py` |
