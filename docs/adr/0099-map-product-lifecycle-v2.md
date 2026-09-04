# ADR-0099: Map Product lifecycle V2 — open / restore / fork / rerun / merge / auto-record

status: Accepted
date: 2026-09-04
relates-to: ADR-0092 (reproducible runtime, ledger A6), ADR-0051 (MapSpec
transaction semantics), ADR-0058 (mutation CAS)

## Context

On master the `map_products` ledger is append-only with a five-dimension
diff, but it is a *reading room*: no way to open a historical version's
content, restore it, fork a lineage, rerun from a version, or merge compatible
changes — and rows carry only a mapspec *fingerprint*, not the document, so
nothing but comparison was ever possible. Recording was manual-only
(`POST /map-products`), so most runs never landed in the ledger.

## Decision

**Rows stay immutable; every lifecycle operation is a NEW row plus a lineage
edge** (migration 0024, all-nullable additive columns: `mapspec_snapshot`,
`label`, `actor`, `parent_version_no`, `lineage_kind ∈
{linear, fork, restore, merge, rerun, auto}`). No Git-pointer semantics:
lineage is DAG evidence, not a movable ref.

1. **Open** (`GET .../{v}/open`) — read-only inspection; reports snapshot
   availability and *honestly degrades* restore modes (pre-snapshot rows →
   compare-only; run-less versions → full restore unavailable). Open never
   touches session state.
2. **Restore style-only** (`POST .../{v}/restore {mode:"style_only"}`) — new
   `RestoreStyleIntent` in the MapSpec lifecycle engine restores the snapshot's
   presentation face (view/layout/components/basemap/time/per-layer
   paint+visible+opacity, matched by layer_id; absent layers skipped and
   disclosed). It rides the full `apply_mutation` transaction (lock, CAS,
   validation, rollback) — origin="system". The recorded restore row carries a
   machine proof: `compute_identity_preserved` (inputs + sorted compute plan
   identical to the source version) and `analysis_executed: false`. Snapshot
   layers missing from the current spec surface as result warnings (honest
   subset, never a fabricated whole map).
3. **Restore full** — only when the version binds a workflow run; it is an
   *explicit replay* of that run (fresh artifacts, input-drift disclosed),
   never a silent in-place resurrection.
4. **Fork** (`POST .../{v}/fork`) — new row copying the source provenance
   (inputs/plan/outputs/snapshot) with `parent_version_no` + `lineage_kind
   = fork`. The fork row is the branch-point evidence.
5. **Rerun from version** (`POST .../{v}/rerun`) — binds version → run →
   first changed step from the pairwise diff drill-down → `rerun_from_step`.
   Style-only versions are refused with the machine reason (no recomputation
   expected — use restore). The rerun lands via the idempotent auto-record.
6. **Constrained merge** (`POST .../merge {from,to}`) — only a style-only
   change × an analysis-only change (disjoint dimensions). Both sides moving
   the same dimension is a structural conflict → 409, never pick-a-winner.
   Merged row = analysis side's compute provenance + style side's mapspec
   fingerprint/snapshot; artifact fingerprints inherited explicitly so the
   diff never lies about `output_changed`.
7. **Auto-record** — `maybe_auto_record_version` after every completed run
   (post-promotion in `WorkflowEngine`): idempotent on
   (workflow_run_id, product_fingerprint); snapshot best-effort (session
   absent/expired still records — open degrades honestly).

## Rejected alternatives

- **Mutable "current pointer" / branch columns**: fake Git semantics over an
  evidence ledger; conflicts and restores become history rewrites.
- **Full-spec replacement on style restore**: would resurrect expired ref
  sources and silently revert newer data layers; presentation-face restore is
  the honest, useful subset.
- **Client-supplied provenance/snapshots on record**: the REST schema stays
  compute-side only; snapshots are captured server-side from the session.

## Consequences

- `POST /map-products` optionally takes `session_id` to capture the snapshot
  server-side; fingerprints/diffs remain non-forgeable.
- The version workspace UI (M7) renders lineage badges and lifecycle actions
  directly from these endpoints.
- `MapSpecResult.warnings` carries skipped-snapshot-layer disclosure.
