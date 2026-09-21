# ADR-0204: Layered GIS Context Scopes — Mission/Project Knowledge on the Default Pi Hot Path

- Status: Proposed
- Date: 2026-09-20
- Line: `harness/gis-context-layered-scopes-v1`
- Related: ADR-0197 (Durable Mission Runtime), ADR-0180 (GIS Situation / World Model),
  ADR-0134 (Context Layers V7), ADR-0119 (Durable Context V6), ADR-0069 (Project Cartographic
  Memory), ADR-0033 (Deep ChatContextAssembler), ADR-0038 (KnowledgeEngine), ADR-0190
  (Proactive Spatial Memory), project_knowledge projection (`GIS_PROJECT_KNOWLEDGE`),
  hotpath convergence D-series (#1395 / PR #1477)

## Context

Direction 6 mission: converge the existing-but-opt-in Mission, Project Knowledge, Session Context
and Claim mechanisms into a layered context system that is **default-available on the Harness Pi
hot path** — not a second chat-memory product:

```
Turn Context ⊂ Session Context ⊂ Mission Context ⊂ Project Context
```

Recon of `origin/master` @ `5a4d4632` (post-#1477) found the scope towers **built but not
stacked**:

| Layer | Existing system | State |
|---|---|---|
| turn | gis_situation 11-partition compile, verdict block | default ON, session-scope only |
| session | gis_situation diff snapshot, `HotpathTurnContext` (process-local, 256 FIFO, no TTL) | default ON; no durable mission binding |
| mission | `gis_missions` + lease/CAS/frontier (ADR-0197) | durable store **complete**, but the chat Pi path never reads it: `maybe_bind_mission_for_turn` is wired only on the swarm delegation path (`agent_pi_bridge.py`); chat turns get zero mission context |
| project | project_knowledge projection + 3-value reuse verdict + 1600-char card | flag default OFF; card's only consumer is REST — no chat injection |

Gaps this ADR closes (verified against master `5a4d4632`, i.e. post-#1477):

1. **No mission-scoped GIS working context.** Mission rows carry goal/frontier/refs but not the
   cartographic working state: accepted basis (AOI, datasets+versions, time period, CRS, measure,
   recipe/product, export target), accepted assumptions, rejected alternatives, verified findings
   (claim refs), unresolved constraints, user edits. Nothing survives a mission spanning sessions
   except opaque refs.
2. **No invalidation interlock.** Four independent invalidation systems exist
   (project_knowledge rules, evidence_claim freshness, spatial_events bridge, mission
   `revise_goal`) and none of them is driven by *working-context change*: AOI shift, dataset
   version bump, time period change, CRS change, measure change, product goal change. CRS change
   has **no** invalidation path anywhere. Old conclusions can be silently reused after the world
   moved.
3. **Mission/Claim binding is tenant+session only** — claims never reference the mission they
   were produced under, so mission-scoped findings cannot be listed or invalidated as a set.
4. **The #1477 wiring is real but partial and carries a dormant defect.** Chat-side
   `_maybe_bind_pi_mission` and the `<project_knowledge>` block exist on the Pi path, but
   (a) both flags default OFF, so the default hot path is dark; (b) the bind helper never passes
   the session's already-bound mission id — enabling `GIS_MISSION_HOTPATH` would create **one new
   mission row per chat turn** (row explosion); (c) even with both enabled, a turn carries no
   working state: no basis, no decisions, no invalidation, no continuity beyond opaque refs.
   This ADR fixes (b), promotes (a) and builds the missing layer.

Non-goals (recon-verified as already built elsewhere — **do not rebuild**): mission durable
ledger/leases (ADR-0197), project knowledge projection/liveness/reuse verdict
(`project_knowledge/`), session situation compile/diff (`gis_situation/`), claim verify pipeline
(`evidence_claim/`), governor context accounting (`governor/context_link.py`), nine-domain
context layers (`context_layers.py`), vector knowledge base (ADR-0038).

## Decision

### D1 — New package `app/services/gis_context/`, no parallel truth

One small package owns the **layer glue**: scope contract, mission-scoped working context,
invalidation engine, bounded card, hot-path assembly. It reuses every authority listed above and
introduces exactly one new durable store.

### D2 — Context Scope Contract (M1)

`scope.py` defines the typed, serializable, bounded scope vocabulary shared by all layers:

- `ScopeTier`: `turn | session | mission | project`.
- `SensitivityClass`: `session_local | project_scoped | org_scoped` — controls injection
  eligibility (project/user-scoped facts are never rendered outside their owning project).
- `ScopeRef`: `{tier, scope_id, org_id, project_id, user_id, source, revision, sensitivity,
  policy}` with `to_bounded_dict()` (all strings length-capped).
- `InvalidationPolicy`: `derived | on_session_end | on_mission_terminal | manual` — when the
  record may be dropped/reused.

Turn scope stays derived-per-turn (no storage). Session scope stays where it is
(gis_situation + session map_state). Project scope stays project_knowledge. Mission scope gets
the new working-context store. The contract is the *joint* — not a new truth for any layer.

### D3 — Mission-scoped GIS Working Context (M2)

`working_context.py` + `store.py` + `app/models/gis_context.py` + migration `0095`:
`gis_working_contexts` — one bounded row per mission (`mission_id` PK), payload ≤16 KB
(validated), `revision` CAS (integer compare-and-swap — **not** mission lease fencing: chat
turns are single-writer per session and must never touch the mission lease, whose epoch bumping
would fence out live swarm workers), `state ∈ {active, purged}`, org/project/user scope columns.

`GISWorkingContext` (schema `gis_working_context.v1`, pydantic `extra="forbid"`, every list
bounded):

- **basis** — the accepted working state: AOI bbox/name, time period, CRS, measure
  (field/unit/statistic), datasets (ref_id + accepted content_revision, ≤12), recipe/product
  refs, export target.
- **decisions** — accepted assumptions (≤8), rejected alternatives (≤8), unresolved constraints
  (≤8); each `{text ≤200, turn_id, basis_revision}`.
- **findings** — verified claim refs (≤8) `{claim_id, status, basis_revision}` — the
  mission↔claim binding gap.
- **user_edits** — append-only user canvas decisions (≤12) `{seq, layer_id, kind, turn_id}`;
  user-wins: never invalidated or overwritten by the engine.
- **stale** — `{field: reason}` map produced by the invalidation engine.
- `revision`, `goal_revision_mirror`, `updated_turn_id`, scope columns.

Assumptions/alternatives/constraints enter via typed store APIs (SessionPlan constraint
chapters, tool/interaction events) — **never chat NLP**; deterministic code only.

### D4 — Observation + Invalidation Engine (M3)

`observation.py` derives a `SessionObservation` deterministically from existing authorities
(mapspec sources/layers, situation snapshot, delivery hints) — no LLM. `invalidation.py` diffs
observation vs the working basis into typed `ContextChange` events and applies a closed rule
table:

| Change | Stales |
|---|---|
| `AOI_CHANGED` | aoi, findings/assumptions with spatial basis, reuse verdicts next turn |
| `DATASET_VERSION_CHANGED` | dataset entry, derived layers on that ref, findings bound to it |
| `TIME_PERIOD_CHANGED` | temporal findings/assumptions |
| `CRS_CHANGED` | crs, derived layers, all geometry-bearing findings |
| `MEASURE_CHANGED` | statistic findings, selected recipe |
| `PRODUCT_GOAL_CHANGED` | product ref, recipe, assumptions re-check |
| `USER_EDIT` | nothing stale — recorded; flagged layers are user-wins |

Effects: (1) stale fields persist on the working context with reasons; (2) affected mission
findings have their ClaimStore claims marked `STALE` via the existing freshness API — reusing
the four-systems' common currency instead of adding a fifth verdict; (3) the card reports the
stale map. Stale facts are never injected as if current.

### D5 — Bounded context card + injection (M5, M4)

`card.py` renders `[GIS_CONTEXT]` ≤1600 chars / ≤24 items: basis → decisions → unresolved
constraints → user-edit notice (user-wins) → active findings → project reuse candidates
(exact / recompute_partial / not_reusable + reasons, from `find_reuse_candidates`). Discipline
inherited from the project_knowledge card: stale filtered (counted, not rendered), honest
omission receipt, deterministic order, fenced untrusted strings.

`hotpath.py::assemble_gis_context_card` runs inside `chat._build_cartography_turn_context`
after the existing three blocks: resolve durable mission binding (`session map_state
`_mission_binding``; process-local `session_ctx` stays a cache) → load → observe → invalidate →
CAS-save → render. Receipt `{bound, hit, miss_reason, stale, reuse, chars}` logged bounded.
Soft combined budget: if the assembled blocks already exceed 4500 chars, the new card yields
(`budget_skipped`) — verdict/memory blocks keep priority.

### D6 — Default-on promotion with kill switches (M4)

- New master switch `GIS_CONTEXT_SCOPES` (**default ON**) gates the whole layered injection +
  working context + the chat-path sticky auto-bind. `0` restores today's behavior exactly.
- `GIS_PROJECT_KNOWLEDGE` default OFF → **ON**: routes carry IDOR-gated auth, hooks are
  fail-safe observers, correctness never depended on hooks (lazy liveness), and an empty store
  degrades to "no reuse section". Kill switch preserved; module docstring updated. Promotion
  also activates #1477's `<project_knowledge>` block — which the `[GIS_CONTEXT]` card then
  dedupes against (reuse section rendered only when the block is absent — M5 no-duplicate).
- `GIS_MISSION_HOTPATH` stays opt-in (it gates *swarm durable mission creation*, an unrelated
  blast radius — flipping it from a context PR would couple them). This ADR fixes its chat-side
  helper to be **sticky** (pass the session's bound mission id for reuse) so that enabling it
  can no longer create one row per turn; flag-on and `GIS_CONTEXT_SCOPES` converge on the same
  single mission per session.
- Backend absent (no mission, no project, DB down): every step degrades to empty card +
  receipt reason. Injection is fail-open; invalidation is fail-closed (applied before render).

### D7 — Mission terminal behavior

Lazy purge is authoritative: loading a working context whose mission is terminal purges the row
(`state=purged`, payload dropped) and reports `miss_reason=mission_terminal`. Eager purge hooks
`MissionRuntimeService.complete/cancel` as hygiene. Terminal missions cannot leak context into
new turns.

## Failure / Security / Resource Semantics

- **Idempotency/replay**: every save is revision-CAS; observation+invalidation are pure
  functions of (store, observation); re-running a turn converges, never duplicates (edits
  deduped by `(layer_id, kind, seq)`).
- **Isolation**: all reads/writes keyed by `org_id` + `mission_id`; card refuses to render when
  the context's project ≠ the turn's project; sessions never share mission bindings; claims
  remain tenant+session-scoped in their store.
- **Boundedness**: payload ≤16 KB, card ≤1600 chars, lists capped, strings capped; no raw
  FeatureCollections, rasters, or model output ever enter context — refs only.
- **Observability**: one bounded receipt per turn (hit/miss/reuse/stale reasons) — no PII, no
  goal text.
- **Backward compat**: flag-off = byte-identical prior behavior; new table is additive; no
  existing API/contract changes.

## Migration / Rollback

Migration `0095_gis_working_contexts` (additive table; watermark 94→95 via
`scripts/alloc_migration.py`). Rollback = set `GIS_CONTEXT_SCOPES=0` (and
`GIS_PROJECT_KNOWLEDGE=0` if desired); table may remain.

## Acceptance Matrix

| Requirement | Test |
|---|---|
| Scope isolation (turn⊂session⊂mission⊂project) | `test_scope_contract.py`, `test_hotpath_wiring.py` |
| Project/session leakage prevention | sensitivity gate + cross-project card test |
| AOI / dataset-version / CRS / measure invalidation | `test_invalidation.py` rule table |
| User edit persistence (user-wins) | engine never stales edits; continuity scenario |
| Stale claim not reused | claim STALE propagation + card stale filtering |
| Bounded project knowledge card | `test_card.py` budget/omission |
| Mission terminal behavior | lazy purge + eager purge tests |
| Default-on with backend absent | wiring test with no mission/project/DB |
| Kill switch | `GIS_CONTEXT_SCOPES=0` byte-identical path |
| Multi-turn cartography continuity (schools→primary→hide→palette→districts→export) | `test_continuity_scenario.py` |
| Concurrent sessions | two sessions/two missions cross-talk test |
| CAS under concurrent writers | store save conflict test |

## Review Record (independent gate, post-fix)

An independent review (ACCEPT-AFTER-FIXES) found and this PR fixed:

- **P0-1** Observation anchors did not match real producer schemas (tests had
  fabricated `view.bounds`/`layer.metric`/provenance-dict shapes) — anchors now
  mirror the producers: AOI from `map_state.viewport.bounds` → `_viewport_bbox`
  over the real `{center,zoom}` view; CRS from source-level `crs`/`profile.crs`;
  measure from `legend_spec.field`; user-hidden derived from the ProvenanceEntry
  *list* with the gis_situation predicate. Real-schema projection tests added.
- **P0-2** `store.save` update paths lacked org predicates → every write now
  carries an org guard (update, rebase-load, `_save_final`).
- **P1-1** empty-org reads were fail-open → fail-closed (org-carrying rows are
  invisible to unknown-org requesters).
- **P1-2** concurrent first-insert (IntegrityError) now routes to the rebase path.
- **P1-3** mission binding stickiness closed durably: `maybe_bind_mission_for_pi_turn`
  resolves the durable `_mission_binding` too (multi-pod/restart safe); binding
  persist failure is observable in the receipt.
- **P1-4/P1-5** tests: continuity scenario now persists its findings before
  re-asserting (the stale-not-rendered claim has real coverage); the CAS test
  models the real protocol (pre-bump expected revision) and asserts concrete
  revisions.
- **P2s fixed in-PR**: ScopeRef/sensitivity gate now actually guards rendering;
  caps use the contract constants; CAS expected-revision correct for revision-1
  rows; budget yield moved before the reuse fetch; corrupted-payload loads
  degrade to a clean miss; single-fence card body (budget efficiency); stale
  "default OFF" docstrings refreshed.

Known follow-ups (explicitly out of scope, tracked in the PR): stale-clearing
semantics (re-verification flips a stale fact back to current — needs an
evidence path, not a timer), user-edit union dedup across rebase copies
(seq is per-copy), per-turn info log sampling, `find_reuse_candidates` for the
card ignores `dataset_fingerprints` (working context stores revisions but the
reuse query should pass them as fingerprints once dataset ids align with
project_dataset authority ids).
