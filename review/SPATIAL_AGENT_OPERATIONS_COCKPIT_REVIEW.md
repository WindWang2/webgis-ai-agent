# Review — Spatial Agent Operations Cockpit (commits 86378793 + 77535e47)

- **Branch**: `frontend/spatial-agent-ops-cockpit-v1` (worktree `webgis-wt-agent-ops-cockpit-v1`), base `origin/master` = `faa453a8`, +2 commits, +4050/−13 across 31 files.
- **Reviewer**: Subagent B (independent adversarial review). Read-only except this memo. All one-off probes were written under `tmp/` / `frontend/` and **deleted** after use.
- **Date**: 2026-09-16

---

## Summary verdict

Solid, honest, well-disciplined read-side projection with two real, reproducible P1 defects — one in the new frontend revision guard (persistent wrong-mission data after switching missions), one in the resume operator action (server-declined resume surfaces as success). Both are small, contained fixes. **Do not merge until both P1s are fixed.** Everything else (tenancy, ownership chain, secret/CoT bounding, kill-switch honesty, i18n parity, seam discipline, backward compat) checked out clean under adversarial probing.

---

## P0 / P1 findings

### P0 — none.

### P1-1 · Stale-revision guard survives `resetKey` → UI persistently shows the WRONG mission's detail/timeline

- **Where**: `frontend/lib/cockpit/use-cockpit-projection.ts:62-77` (`lastRevisionRef`/`lastDataRef` are never reset when `resetKey` changes) + consumers `frontend/components/cockpit/mission-view.tsx:69-86` (detail & timeline hooks, `resetKey: selectedId`, `revisionOf: mission.revision`).
- **Mechanism**: `useBoundedPoll` clears its own `data` on resetKey change and refetches, but the cockpit wrapper's monotonic guard keeps the *previous mission's* revision. Switching from mission A (high revision) to mission B (lower revision — any newly created mission starts at rev 1) makes `guardedFetcher` classify **every** B response as "late old projection" and return A's stored detail instead (`use-cockpit-projection.ts:72-75`). The misdisplay is **persistent**, not transient: it self-heals only when B's revision climbs above A's last-seen revision.
- **Impact**: mission state / goal / lease / frontier / checkpoint timeline of mission A render under mission B's selection; operator action buttons are gated on A's state while acting on B (`selectedId`). Exactly the "UI shows state the server does not own" class the PR claims to avoid. The session views are immune (they fall back to monotonic `generated_at`); the list view is immune for the same reason.
- **Reproduction (executed, then deleted)**: `renderHook` with `resetKey: 'A'→'B'`, `revisionOf: d.mission.revision`, envelopes A(rev 42) then B(rev 3). Result after switch: `data = {"mission":{"mission_id":"A","revision":42,"state":"running"}}` — assertion `expect(mission_id).toBe('B')` **failed**. The existing test `use-cockpit-projection.test.ts:136-154` only covers the *increasing* direction (rev 1→2), which masks the bug.
- **Suggested fix**: reset `lastRevisionRef`/`lastDataRef` in a `useEffect` keyed on `resetKey` (or key the guard state by resetKey).
- **Blocks PR**: yes.

### P1-2 · `resume` that the server declined (ok:false, HTTP 200) is presented as success

- **Where**: backend `app/api/routes/mission_runtime.py:112-124` (`resume_mission` maps only `FencingError`/`TransitionRejected` to 409; `svc.resume` → `MissionRecoveryCoordinator.recover` (`app/services/mission_runtime/recovery.py:120-160`) **returns** `{"ok": False, "reason": ...}` dicts without raising) consumed unchanged by the new frontend `frontend/lib/api/cockpit.ts:380-385` (`resumeCockpitMission` → `Record<string, unknown>`) and `frontend/components/cockpit/mission-view.tsx:90-112` (`runOp` never inspects `ok`).
- **Mechanism**: when the mission's lease is held live by another worker, `acquire_lease` returns `None` (`app/services/mission_runtime/store.py:244-247`) and `recover()` returns `{"ok": False, "reason": "LEASE_HELD"}`. Route → HTTP 200. `runOp` clears `armed`, bumps the refetch, and shows **no error**. Contrast: start/suspend/cancel raise → 409 → the UI shows the conflict message. `resume` is the odd one out, and lease-held is the *normal* state for a running mission (the mirror even enables Resume for `running`).
- **Reproduction (executed against the real service, in-memory SQLite, then deleted)**:
  `svc.start(m, worker_id="agent-runner")` → `svc.resume(m, worker_id="ops-console")` →
  `RESUME RESULT: {'ok': False, 'reason': 'LEASE_HELD', 'lease_owner': 'agent-runner'}`, `RAISED: False`, state remains `running`.
  Other silent-decline reasons with the same shape: `LEASE_ACQUIRE_FAILED`, `ALREADY_TERMINAL`, `CANNOT_ENTER_RECOVERING_FROM_*`.
- **Impact**: operator believes resume was submitted; no feedback; the view "recovers" only because the poll shows an unchanged mission. Breaks the component's own stated discipline ("409/其他错误只呈现错误，不改视图状态").
- **Suggested fix**: in `runOp`, when `op === 'resume'`, check `res.ok === false` and surface `res.reason` via `setActionError` (keep the view server-truthful). Backend could also map `ok:false` to 409, but the frontend check is the minimal contained fix.
- **Blocks PR**: yes.

---

## Dimension-by-dimension findings

### 1. Correctness / fail-open
- **missionAllows() mirror vs server `_TRANSITION_MAP`**: compared state-by-state — **exact match** for all 10 states (`frontend/lib/api/cockpit.ts:28-39` vs `app/services/mission_runtime/contracts.py:56-91`), terminal states empty on both sides. No drift today. However the mirror has **no test pinning it** (see P3-2).
- Op-target semantics: `start:['planning','running']` matches `CREATED→PLANNING→RUNNING`; `resume` targets include `recovering/running/partially_complete/waiting_dependency` which all legally reach `RECOVERING` per `transition_allowed` (and `recover()`'s suspend-first force path). Buttons are gating-only; server arbitrates. Fine, except the resume **result** handling above (P1-2).
- Frontend does not re-derive mission state anywhere; `stateTone` is visual-only. The only "success while unknown" path is P1-2.

### 2. Security / tenancy
- **Anonymous access**: `/cockpit/health` is intentionally anonymous and discloses only `{enabled, schema}` (same discipline as `/mission-runtime/health`). All other endpoints require `get_current_user` (401 verified by `test_missions_list_requires_auth`).
- **Mission org isolation**: `list_unfinished(org_id=org)`, `get_mission(mission_id, org_id)` → 404 before detail/timeline/swarm/diagnostics (cross-org 404 tested; no existence oracle — same 404 for unknown and foreign). Swarm listing is by mission_id without org filter, but mission_id is `msn-<16 hex uuid4>` (64-bit random) and the parent-mission org check gates first. Fine.
- **Session ownership**: cockpit session endpoints stack `require_owned_session` (real impl: `get_session_meta` → `_authorize`, verified fail-closed at `app/services/history_service_async.py:511-545`: bound sessions only for the owner; anonymous sessions need constant-time owner_token match; NULL/NULL legacy fail-closed) **plus** `get_current_user`. Header-only `X-Session-Token` (no URL token), fetch Headers API rejects CRLF → no header injection. Fine.
- **Secret/CoT leakage**: `TraceEvent.to_dict` (`app/services/gis_harness/trace.py:67-75`) truncates detail to ≤8 keys/≤96 chars over a closed 4-stage vocabulary; call sites (`runtime_repair.py:341`, `completion/pipeline.py:928`) pass only counts/status enums. `Claim.to_bounded_dict`, `Scope.to_bounded_dict`, `RelationEdge.to_bounded_dict` all bounded (`evidence_claim/contracts.py`). `SkillGuidanceBundle.to_bounded_dict` exposes decision/projection/shadow/pi_context — bounded policy rationale, identical to what `tools.py:677` already stores process-locally. **Note (P3-8)**: `session_skill` returns `ctx.skill_guidance` as stored without re-bounding — safe today because the only writer stores the bounded dict; defense-in-depth would re-bound at read.
- **XSS**: no `dangerouslySetInnerHTML` anywhere in `frontend/components/cockpit/**` (grep clean); all refs/goal text rendered via React text nodes.

### 3. Concurrency / races
- resetKey semantics: `useBoundedPoll` generation guard + abort-on-reset correct; the **revision guard bug across resets is P1-1**.
- Double-POST: `runOp` busy flag + confirm button `disabled={actionBusy}` + transport never retries non-idempotent methods (`transport.ts:31,328`). OK.
- Abort on unmount/session switch: poll controllers aborted (unmount + resetKey effects); health probe aborted on unmount (`agent-ops-cockpit.tsx:52-59`). Operator POSTs intentionally not aborted (fire-and-forget with server arbitration) — acceptable.
- Poll storm: server `poll_after_ms` (2000/15000) clamped to [3000, 30000] both in `clampPollAfterMs` and `useBoundedPoll`'s `MIN_POLL_INTERVAL_MS`; hidden tab pauses; visibility resume refetches; error cap 3. No storm.
- Two tabs, same mission: both act as constant `worker_id="ops-console"`; `acquire_lease` treats same-owner as renew (no fencing between two operator tabs) — see P3-4.

### 4. Performance / memory
- 1200 synthetic trace events render windowed (test asserts mounted < 200); evidence claims (≤200 fetched) also via `VirtualList`. Server caps: trace 64, evidence 500, missions 200 (`le=` 422 verified), checkpoints 32.
- `list_swarm_runs_for_mission` is unbounded (P3-5).
- No `window.addEventListener` leftovers in the new components (grep clean — only `useBoundedPoll`'s managed visibilitychange); no console.log/debugger/localStorage in new files.
- No polling when panel inactive: views render only when their tab is active; `enabled=false` → 0 requests (kill-switch test additionally asserts zero projection calls).

### 5. Backward compatibility
- All seam edits **append-only**: `nav-rail.tsx` (new last group), `hud-types.ts` (union extension), `workbenchSlice.ts` (append `'cockpit'` to the 3 mode lists), `context-panel.tsx` (new registration row + boundary), `messages.ts` (new catalog), `layout.json` (key additions only). No existing key/behavior removed.
- nav-rail ArrowUp-from-last / End now landing on `cockpit` instead of `ops` is the direct, consistent consequence of the established append-only tab registration rule (same as when `ops` was appended in ADR-0142); tests updated accordingly. Acceptable.
- openapi snapshot: purely additive — 8 new `cockpit_*` operations, **0 removed lines** (`git diff origin/master...HEAD -- tests/quality/snapshots/openapi.json`).
- `tests/quality/test_api_compatibility.py` passes.

### 6. Observability honesty
- Kill-switch: 503 `cockpit_disabled` on reads; health stays truthful (`enabled:false`); UI shows an explicit "server-side kill switch, not a frontend failure" empty state (en+zh) with zero projection requests (test-asserted).
- Process-local disclosure: `mission.processLocalNote` rendered in SkillView footer and mission empty state; TraceView carries the "no chain-of-thought / secrets" note. **EvidenceView lacks the note** (P3-7) — minor.
- i18n zh/en parity: `test/i18n/key-completeness.test.ts` passes (6 tests) and my independent flatten-diff found zero zh-only/en-only keys. Boundary label `panel.boundary.cockpit` present in both locales.
- **But**: LiveDot renders the raw key `cockpit.mission.state.running` (P2-1) — visible in every enabled cockpit header.

### 7. Test honesty
- Backend route tests (`tests/unit/cockpit/test_cockpit_routes.py`): hermetic (in-memory SQLite StaticPool, real minted JWTs, real app wiring), assert server-owned values (`state`, `revision`, checkpoint ordering, org filtering, envelope protocol), adversarial trace-bounding test (12 oversized keys → ≤8/≤96 enforced), kill-switch honesty, empty-swarm ≠ 404. Good.
- Caveats: `require_owned_session` and `_effective_org` are dependency-overridden in every test — the *real* ownership proof and tenancy indirection are never exercised through these routes (P3-3); no test covers "session endpoints still work with `GIS_MISSION_RUNTIME=0`" despite the docstring claim.
- Frontend: the "409 conflict" test never constructs a real `ApiError` (`agent-ops-cockpit.test.tsx:172-174` uses `Object.assign(new Error(...), {status:409})`), so `err instanceof ApiError && err.status === 409` is false and the assertion passes through the *generic* error branch via `/lease/i` matching `LEASE_ACQUIRE_FAILED`. The 409-specific branch and the `action.conflict` copy are unverified (P2-2). The no-optimistic-write half of that test is still valid.
- `missionAllows`/`MISSION_ALLOWED_TRANSITIONS` are untested (P3-2).

### 8. Contract drift (field-by-field)
- Missions list envelope (`schema/generated_at/missions/has_active/poll_after_ms`) ✓; `_mission_summary` fields all declared in `CockpitMissionSummary` ✓.
- Detail: `MissionRecord.model_dump` ⊇ `CockpitMissionRecord` (extra `owner_scope` tolerated by the index signature); `MissionDiagnostics` fields match ✓.
- Timeline envelope + `checkpoints` dict shape ✓ (bounded desc, tested).
- Swarm: `SwarmRunDurable`/`SwarmTaskReceipt` fields match `CockpitSwarmRun`/`CockpitSwarmTask` ✓.
- Evidence: `Claim.to_bounded_dict` ⊇ `CockpitClaim` (extra scope/metadata fields tolerated); `RelationEdge.to_bounded_dict` includes extra `metadata` (unrendered) ✓; `truncated` computed honestly against full stats ✓.
- Skill/trace envelopes match `TraceEvent.to_dict`/`summary`/`counters` shapes ✓.

---

## Confirmed-fine items (evidence)

| Item | Evidence |
|---|---|
| Mirror = server transition map | manual state-by-state diff, `cockpit.ts:28-39` vs `contracts.py:56-91` — identical |
| `_authorize` fail-closed | `history_service_async.py:511-545`: anonymous→None on bound sessions; NULL/NULL→None; hmac compare owner_token |
| Session endpoints need Bearer + ownership | `cockpit.py:196-281` double dependency; 401/404 tested |
| Org isolation incl. cross-org 404 | `test_missions_list_org_scoped`, `test_mission_detail_cross_org_404`, `test_unknown_mission_404` |
| Trace/claim/skill bounded at source | `trace.py:67-75` (≤8/≤96, closed vocab), `evidence_claim/contracts.py` bounded dicts, `tools.py:677` stores bounded dict |
| No XSS surface | grep `dangerouslySetInnerHTML` in cockpit/** = none |
| POST never retried; no double-fire | `transport.ts:31,328` (non-idempotent → 1 attempt); `runOp` busy flag |
| Poll clamps + pause + cap | `clampPollAfterMs` [3000,30000]; `use-cluster-poll.ts` hidden/error-cap/unmount |
| openapi additive only | 0 removed lines in snapshot diff; 8 new operations |
| No geoai / PR-#1336 overlap | `git diff origin/master...HEAD --stat | grep -i geoai` → empty |
| No PR-#1335 collision | `git diff origin/master...origin/fix/harness-claim-mission-failclosed -- app/api/routes/cockpit.py` → empty |
| zh/en parity | key-completeness 6/6 + independent flatten-diff: 0 asymmetric keys |
| Virtualization | 1200-event test: mounted rows < 200, total honestly displayed |

---

## Test-run results (exact)

| Suite | Command | Result |
|---|---|---|
| Backend | `pytest tests/unit/cockpit tests/unit/mission_runtime tests/quality/test_api_compatibility.py -q -p no:cacheprovider` (serial) | **50 passed**, 69 warnings, 115.32s |
| Frontend targeted | `pnpm vitest run components/cockpit lib/cockpit lib/api/cockpit.test.ts test/i18n/key-completeness.test.ts` | **4 files / 30 tests passed** (api 10, hook 8, i18n 6, component 6) |
| Typecheck | `pnpm typecheck` | clean (tsc + tsconfig.test) |
| Lint | `pnpm lint` (eslint --max-warnings 0) | clean |
| Adversarial repro (deleted) | cross-resetKey guard | 1 test **failed** as predicted (P1-1 demonstrated) |
| Adversarial probe (deleted) | resume lease-held | returned `{'ok': False, 'reason': 'LEASE_HELD'}` without raising (P1-2 demonstrated) |

---

## P2 notes

- **P2-1 · LiveDot renders raw i18n key** — `frontend/components/cockpit/cockpit-shared.tsx:131` uses `t('mission.state.running')` but the catalog key is top-level `state.running` (`mission.state` is the string "状态"). Probe confirmed `defaultTranslator('cockpit.mission.state.running')` → literal `"cockpit.mission.state.running"` while `'cockpit.state.running'` → "运行中". Every enabled cockpit header shows the raw key. Fix: `t('state.running')` (and pick a neutral label for the LiveDot, e.g. a dedicated `live` key).
- **P2-2 · 409 test doesn't exercise the 409 branch** — `agent-ops-cockpit.test.tsx:172-174`: mock with `Object.assign(new Error(...), {status: 409})` is not `instanceof ApiError`, so `mission-view.tsx:104` takes the generic branch; the test's `/lease/i` assertion matches `LEASE_ACQUIRE_FAILED` from the *generic* message. Use `new ApiError(409, 'Conflict', {detail: ...})` so the conflict path and `action.conflict` copy are actually asserted.

## P3 notes

- **P3-1** Frontier "blocked" bucket mislabel: `mission-view.tsx:225` renders `t('mission.blocked')` ("受阻原因"/"Blocked reason") for the blocked frontier bucket; the intended key `mission.blockedBucket` ("受阻") exists but is unused.
- **P3-2** `MISSION_ALLOWED_TRANSITIONS` mirror is untested — add a test pinning it to `contracts.TRANSITIONS` (or generate from a shared source) to prevent silent drift.
- **P3-3** Cockpit route tests override `require_owned_session` + `_effective_org`; the real ownership/tenancy chain isn't exercised through these routes (relied upon from jobs.py coverage). Also no test that session endpoints remain usable with `GIS_MISSION_RUNTIME=0` despite the session_skill docstring claim.
- **P3-4** Constant `worker_id="ops-console"`: two operator tabs cannot fence each other (same-owner lease renew, `store.py:244-250`). Acceptable single-operator design; document it.
- **P3-5** `list_swarm_runs_for_mission` has no LIMIT (pre-existing store method, reused by cockpit + diagnostics). Unbounded over very long mission lifetimes; consider a cap.
- **P3-6** Hardcoded `'zh-CN'` locale in `fmtTime` (`mission-view.tsx:217-218,258`) and locale-less `toLocaleTimeString` in TraceView — en-US users get zh-formatted timestamps.
- **P3-7** EvidenceView lacks the process-local disclosure note (present in SkillView); the key text already covers "evidence".
- **P3-8** `session_skill` returns `ctx.skill_guidance` as stored without re-bounding at read time (writer is bounded today; defensive re-bound would harden against future writers).

## False positives investigated and dismissed

1. *"Swarm endpoint queries without org_id → tenancy bypass"* — dismissed: gated by `_get_mission_or_404` (org-scoped 404) and 64-bit random mission ids; rows also carry org_id.
2. *"Session views hit the same stale-revision bug across session switches"* — dismissed: they fall back to envelope `generated_at` (monotonic server time), so late old-session responses are correctly rejected and new-session responses always pass.
3. *"Double-click on confirm → double POST"* — dismissed: React re-render between events + `disabled={actionBusy}` + `runOp` early return + transport refuses POST retry.
4. *"Header injection via ownerToken"* — dismissed: ownerToken travels only in `X-Session-Token` via the Headers API (invalid values throw), never in URLs; `encodeURIComponent` used on all path params.
5. *"nav-rail 'last tab' change breaks keyboard nav for ops"* — dismissed: append-only registration makes cockpit the last tab by the same rule that made ops last; tests updated; Home still lands on chat.
6. *"i18n keys incomplete"* — dismissed by parity test + independent cross-check of every static and dynamic key used by the components (only the LiveDot wrong-key bug above, which is a wrong key, not a missing one).
7. *"resume mirror allows nonsense states (e.g. created→resume)"* — dismissed: `recover()` legally handles those via the suspend-first force path; server arbitrates regardless.

---

## Merge recommendation

**DO NOT MERGE YET** — fix P1-1 (reset the revision guard on `resetKey` change in `use-cockpit-projection.ts`) and P1-2 (surface `ok:false` reasons from `resume` in `runOp`), optionally sweep P2-1/P2-2 in the same pass; after those two contained fixes this branch is ready, with my recommendation to merge.
