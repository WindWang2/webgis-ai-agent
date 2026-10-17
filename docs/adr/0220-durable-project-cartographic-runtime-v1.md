# ADR-0220: Durable Project Cartographic Runtime V1

## Status

Proposed (implemented on `zcode/durable-project-cartographic-runtime-v1`)

## Context

The harness has three mature but deliberately bounded durability systems:

1. **WorkspaceSnapshotService** (V4/V6) — project-scoped manifest snapshots of the
   artifact ledger + layer refs + BlobStore durable pointers. Its declared boundary:
   evidence and ledger only, "绝不反向驱动 spec" — it never restores MapSpec content
   and never constructs a session runtime.
2. **WorkflowResumeAnchor** (V5/V6) — DB-durable recovery pointer that constructs a
   *new session* from a dying one (plan chapters, ref rehydration, ref rewrite,
   post-resume verify). Its boundary: no MapSpec/disk-checkpoint restore, no
   workspace-snapshot integration.
3. **MapSpec checkpoint system** — session-scoped checkpoints; named ones are
   self-contained, but the checkpoint directory itself dies with the session
   (`clear_session` / `sweep_expired_session_files`).

The remaining crack: after a session expires and the process restarts, the project
still "sees" its records (datasets, artifacts, map product versions), but there is
no way to construct a **verified new session runtime** that rehydrates the
cartographic context (MapSpec + source payloads + plan projection) under project
authorization. MapProductVersion style-restore covers only the presentation face of
a published version; the anchor covers only plan chapters; the workspace snapshot
covers only the ledger. Nothing composes them, and nothing survives for sources
whose refs die between freeze and resume.

## Decision

Add a **ProjectRuntimeSnapshot V1**: an explicitly-created, project-scoped,
versioned manifest that composes the existing authorities instead of duplicating
them, plus a **verify-first resume protocol** that constructs a new session from it.

### 1. Snapshot is a manifest, not a second database

`ProjectRuntimeSnapshotV1` (schema_version=1) records only:

- identity: `snapshot_id`, `project_id`, `session_id` (created-from), `created_by`,
  `created_at`, `freeze_reason` (explicit user save / named checkpoint / publish)
- authority references: `workspace_snapshot_id` (the artifact-lineage authority
  created at freeze via WorkspaceSnapshotService), `map_product_version_no`
  (optional), workflow identity refs (bounded)
- MapSpec freeze: the **bounded spec document** (precedent:
  `MapProductVersion.mapspec_snapshot` — the doc is presentation + source refs,
  never payloads), its canonical fingerprint, and the mutation revision at freeze
- source bindings: per-source `{source_id, kind, ref, durable_pointer?,
  promotion_class}` (bounded ≤64) — payload bytes live only in the BlobStore
- dataset descriptors: `{dataset_id, name, version_fingerprint}` for attached
  project datasets (bounded ≤32)
- plan projection refs: `envelope_id`, `user_goal`, bounded `progress`, workflow
  instance fingerprint (no plan dump — plan chapters stay anchor territory; the
  resumed session gets a *fresh* SessionPlan with a `resumed_from` block)
- required capabilities: bounded capability ids derived from the workflow instance
  (may be empty — honest), plus the credential presence fingerprint at freeze
- recovery policy: `observation_policy="invalidate"` (V1 fixed: render
  observations / visual evidence are never restored as current), in-flight jobs
  are never restored
- bounded disclosure: refs skipped by promotion budget, sensitive/external sources

It never contains: LLM hidden state, chat history, arbitrary session dumps,
secrets/tokens, BlobStore bytes. (Inline sources' `inlineData` travels inside
the spec document by MapSpec's own design — bounded by the inline feature
limit and the 16MB doc cap; ref-carried payloads are never embedded.)

### 2. Explicit freeze points only

`save_runtime_snapshot` is called from:

- explicit user save (new REST route)
- named checkpoint flow (optional hook — route accepts `freeze_reason`)
- final publish (route callable with `map_product_version_no`)

No per-turn automatic snapshots. A snapshot is written atomically (tmp+replace)
or not at all; a crash leaves either nothing or a complete manifest.

### 3. Session-ref promotion is explicit and classified

At freeze, every MapSpec source ref goes through the promotion planner:

- `already_durable` — has a BlobStore pointer (prior promotion / workspace snapshot)
- `promotable` — live payload, materialized now via
  `workspace/durability.materialize_ref_payload` (raster/parquet/cube/json lanes,
  pre-size budget gate) after a `quota.check_quota` gate (fail-honest refusal, zero
  bytes written on refusal)
- `oversized` — live but exceeds the remaining budget (honest skip)
- `non_promotable` — dead ref with no pointer (disclosed)
- `external_version_pinned` — source points at a ProjectDataset / external URL,
  not a session ref (version-pinned via dataset fingerprint instead)
- `recomputable` — ref has ledger lineage parents (its loss is recoverable by
  re-run; disclosed as such)

Promotion is idempotent (BlobStore CAS by content digest; `persistence_tier`
stamping only ever upgrades, mirroring `_claim_durability`). The manifest carries
durable pointers; blobs are protected from session GC by the same
`persistence_tier="workspace"` stamp the workspace snapshot uses.

### 4. MapSpec source rebinding is a guarded transaction

`rebind_mapspec_sources` rewrites `old ref → new ref` across source entries using
the existing source-shape knowledge (`mapspec_source`), only in the key(s) that
actually hold the old value. Guards:

- content fingerprint equality on the frozen doc (integrity before anything)
- **semantics-drift guard**: canonical JSON compare of the doc with all `ref:`
  values masked — the rebound doc must differ from the frozen doc *only* in ref
  values; any other drift aborts the rebind
- no silent substitution: a source whose ref cannot be restored is cleared
  (dangling disclosure, `resume_from_anchor._rewrite_refs` precedent), never
  pointed at different content
- commit through the store-level rollback-style path (`save_mapspec` with
  `mutation_revision` + `layer_op=("replace", "", layers)`), the same atomic
  commit the engine uses for rollback — never a bare two-step write

### 5. Resume is verify-first

`ResumeRequest → ValidationReport → RecoveryPlan → NewSessionBinding → ResumeReceipt`

Validation (before any state is written):

1. manifest parses + `schema_version` supported (else `unsupported_schema`,
   fail-closed — no speculative upgrade on read)
2. project exists, active, caller authorized (project auth gate; cross-org → 404
   upstream; archived/deleted → `blocked`)
3. frozen doc fingerprint matches the embedded doc (integrity)
4. every durable pointer verifies (`verify_durable_pointer`): missing/digest
   mismatch → `missing_artifact` findings
5. dataset fingerprints compared against current `ProjectDataset` rows: detached
   or changed → `dataset_version_changed` findings
6. required capabilities re-resolved (`capability_status`): unavailable /
   credential missing → `capability_unavailable` findings
7. permission note: caller ≠ creator (same org) → `permission_changed` disclosure

State vocabulary (findings + overall): `ready`, `ready_with_stale_inputs`,
`requires_recompute`, `missing_artifact`, `capability_unavailable`,
`permission_changed`, `dataset_version_changed`, `unsupported_schema`, `blocked`.

Recovery plan: per-source disposition (`restore_from_durable`,
`restore_from_live`, `needs_recompute`, `external_pinned`) + per-dataset verdicts.

Execution (only reachable data is written, nothing is replayed):

- mint a new session id; rehydrate payloads from durable pointers (digest-verified
  reads; binary lanes restore to the new session's disk lane; JSON lanes `store()`
  under new ref ids → ref_map), falling back to the old session / RefSpill if still
  alive (bounded, honest)
- rebind + commit the MapSpec (§4)
- register the artifact ledger from the composed workspace snapshot's
  contracts directly through the registry API with **id remapping**
  (`register_artifact` under remapped ref ids + lineage-input remap +
  honest `expired` marking for unrestored payloads). This deliberately does
  NOT reuse `restore_snapshot(mode="register")`: that path writes payloads
  back to the *old* ref ids via alias indirection, while runtime resume
  mints canonical new ids for JSON payloads (alias-free ledger)
- create a fresh SessionPlan (goal + envelope carried as projection refs; a
  `resumed_from_runtime` block discloses restored/missing/recompute)
- create the Conversation row owned by the caller (closing the ownership-leak
  window, `resume_from_anchor` precedent)
- observations, visual evidence, working-context caches, in-flight jobs: **not**
  restored (invalidated; the new session's situation compiles from restored
  authorities)

The resume never re-executes tools or side effects; it only rehydrates verified
bytes and rebuilt projections.

### 6. Concurrency / CAS

- freeze takes the session mutation lock only for the consistent read of
  (mapspec, revision, plan identity); promotion IO happens after release —
  refs are immutable identities, so a spec mutation racing the freeze yields a
  snapshot of the pre-mutation cut, honestly stamped with its revision
- two tabs freezing concurrently produce two distinct snapshots (id unique,
  atomic writes); retention cap trims oldest
- same-snapshot concurrent resume: a per-snapshot on-disk lease file
  (pid + timestamp, TTL) makes the second concurrent attempt fail fast with an
  honest conflict; sequential resumes remain allowed (each mints a new session —
  resume never mutates the snapshot)
- resume re-checks project auth at start; project vanishing mid-resume leaves an
  honest partial-new-session (disclosed), never a fabricated success

### 7. Retention / GC

- runtime snapshots live in `DATA_DIR/workspaces/<project_id>/runtime-snapshots/`
  (project domain, outside session sweeps — the `workspaces` whitelist already
  exempts the root)
- bounded count per project (25, oldest-trimmed, mirroring workspace snapshot cap)
- deletion is per-snapshot explicit; blobs stay protected by
  `persistence_tier`/reference-counting GC (shared BlobStore, no private copies)
- snapshot delete produces a receipt; blob GC remains the artifact lifecycle's
  job (refcounted), never the snapshot's

## Consequences

- Project open gains a thin, honest surface: list snapshots → inspect
  (validation preview) → resume → receipt. No UI redesign in V1.
- The anchor path remains the session-crash recovery tool; the runtime snapshot
  is the *project-lifetime* recovery tool. They share primitives, not truth.
- Stale never silently becomes current: every dataset/capability/permission drift
  is a finding, and only `ready`/`ready_with_stale_inputs` resumes proceed with
  full disclosure.
- Cost: one extra bounded JSON manifest per freeze + BlobStore promotion bytes
  (quota-gated, budget-capped per freeze at 256MB default).

## Known limitations (V1)

- Freeze trusts the caller's session-ownership gate for the session↔project
  pairing (any session the caller owns can be frozen into any project the
  caller can access — same discipline as workspace snapshots; no
  session→project binding column exists by design).
- Sensitive-classified refs are disclosed, never materialized and never
  restored (hard refusal on both freeze and resume paths).
- Binary lanes (raster/parquet/cube) restore under the same ref id in the new
  session's disk lane (disk lane refs carry no per-session token); JSON refs
  always mint new ids and are rebound in the spec.
- The resume lease is per-process-host (pid + TTL file); multi-pod setups rely
  on TTL expiry (300s) for crash recovery of a stale lease.

## Alternatives considered

- **Dump the whole session (state export)**: rejected — creates a second session
  truth, unbounded, violates single-truth invariants enforced by GC refcounting.
- **Extend WorkflowResumeAnchor with mapspec blocks**: rejected — anchors are
  per-session recovery pointers keyed by user; project-lifetime snapshots need
  project scoping, dataset fingerprints, and promotion receipts that don't belong
  in the anchor's bounded payload.
- **Reuse MapProductVersion as the snapshot**: rejected — versions are per-publish
  artifacts with their own lifecycle; a runtime snapshot can exist without any
  published product and must reference *datasets + capabilities + plan identity*,
  which versions deliberately don't carry.
