# GROK BOT AUTONOMOUS DEVELOPMENT GOAL
# Direction 01 — Durable GIS Mission Runtime & Distributed Harness Control Plane

Repository:
https://github.com/WindWang2/webgis-ai-agent

You are operating as the primary autonomous engineering agent for this development line.

Your mission is to deeply inspect the CURRENT repository state and implement the next architectural layer of the WebGIS AI Agent Harness:

> Durable GIS Mission Runtime + Distributed Harness Control Plane

This is NOT a generic background-job framework and NOT a second Agent framework.

Pi remains the Agent Host.

The existing GIS Harness, SessionPlan, Workflow Runtime, Execution Graph, Specialist Swarm, Artifact Registry, Map Product Runtime, Resource Governor, Replay, GIS Situation and MapSpec runtime remain authoritative subsystems.

The purpose of this work is to make long-running GIS goals survive:
- chat turns,
- session changes,
- process restarts,
- worker restarts,
- pod failover,
- partial execution,
- retry,
- interrupted swarm execution,

while preserving GIS artifacts, execution evidence, map products and deterministic recovery.

---

## 0. NON-NEGOTIABLE AUTONOMOUS WORKFLOW

Operate fully autonomously.

Do NOT ask the user to:
- choose architecture,
- approve intermediate steps,
- choose between implementation options,
- resolve ordinary conflicts,
- run tests,
- decide filenames,
- confirm migrations.

Use repository evidence and engineering judgment.

Before changing code:

1. `git fetch --all --prune`
2. inspect current `origin/master`
3. inspect all open PRs
4. inspect recent merged PRs
5. inspect open issues
6. inspect PR review comments
7. inspect ADRs, `.agent-work`, review reports and current production call paths

Repository truth at execution time overrides every assumption in this prompt.

At the time this prompt was authored, the only open PR was StoryMap hardening #1318. That is only a snapshot. Re-check.

Do NOT duplicate functionality already merged or currently being implemented by another PR.

---

# 1. WORKTREE

Create an independent worktree from latest `origin/master`.

Suggested branch:

`harness/durable-gis-mission-runtime-v1`

Suggested worktree:

`../webgis-ai-agent-wt-mission-runtime`

Never develop directly in master.

Do not merge your own PR.

---

# 2. ARCHITECTURAL PRINCIPLE

The intended hierarchy is:

```text
Pi Agent Host
     │
GIS Harness
     │
GIS Mission Runtime        <-- THIS WORK
     │
 ┌───┼────────────────────────────┐
 │   │                            │
SessionPlan               Execution/Workflow Runtime
 │                              │
GIS Situation                Swarm
 │                              │
Artifacts / Data / Analysis / Map Product / MapSpec
```

A Mission is NOT:
- another SessionPlan,
- another ExecutionGraph,
- another workflow DAG,
- another agent,
- another task queue.

A Mission is the durable ownership/lifecycle envelope above existing execution systems.

---

# 3. PHASE 0 — DEEP RECON

Read actual production code before designing.

At minimum inspect:

- `app/agent_pi_bridge.py`
- SessionPlan implementation
- GIS Harness kernel/runtime
- workflow runtime V5
- Execution Graph implementation
- plan graph
- Specialist Swarm
- SwarmBridge
- SubagentDispatcher
- resource governor
- session budget ledger
- artifact registry / artifact graph
- data lifecycle
- project service
- GIS Situation
- Map Product runtime
- mutation transactions
- ReplayTrace
- jobs / Celery
- Redis coordination utilities
- DB models + Alembic migrations
- frontend execution/status surfaces
- API/SSE contracts

Specifically re-read the implementation and known boundaries originating from the Specialist Swarm work:
- process-local in-flight swarm state,
- pod restart losing unfinished swarm state,
- process-local turn mutual exclusion,
- missing complete integration between swarm task state and durable workflow ledger.

Do not assume these are still unresolved; verify current code.

Produce internal recon notes documenting:

```text
current source of truth
current durability boundary
current locking boundary
current recovery boundary
current artifact ownership
current retry semantics
current destructive-operation semantics
current distributed coordination primitives
current resource-budget ownership
```

Only after this recon should architecture be frozen.

---

# 4. CORE DELIVERABLE — GIS MISSION CONTRACT

Introduce a versioned Mission domain contract only if the repository does not already contain an equivalent abstraction.

Minimum conceptual state:

```text
Mission
  mission_id
  org_id
  user_id
  project_id?
  root_goal
  goal_revision
  state
  revision
  created_at
  updated_at

  active_session_ids
  session_plan_refs
  workflow_instance_refs
  swarm_run_refs

  artifact_refs
  map_product_refs
  evidence_refs

  checkpoints
  current_frontier

  resource_budget
  resource_consumption

  failure_state
  recovery_state
```

Candidate lifecycle:

```text
created
planning
running
waiting_dependency
partially_complete
suspended
recovering
complete
failed
cancelled
```

Do not blindly use this exact vocabulary if existing repository state machines provide a better canonical vocabulary.

Reuse canonical enums where possible.

Avoid vocabulary proliferation.

---

# 5. DURABLE MISSION LEDGER

Implement durable mission persistence.

Prefer extension/reuse of existing workflow/job/session infrastructure rather than inventing a parallel persistence stack.

The durable ledger must track enough information to recover:

```text
Mission
Mission revision
Goal revision
Execution references
Swarm task references
Artifact references
Receipts
Checkpoint
Retry state
Lease ownership
Recovery attempt
```

Never persist giant GIS payloads into Mission state.

Use ref/ticket semantics consistent with the repository:

```text
ref:...
artifact id
dataset reference
MapProduct ref
```

Zero Big Data in Agent Context remains mandatory.

---

# 6. DURABLE SWARM BRIDGE

Integrate Specialist Swarm with durability without creating a second scheduler.

The target is approximately:

```text
Mission
   ↓
existing Workflow / Execution Graph
   ↓
Swarm tasks
   ↓
durable task state
   ↓
Specialist agent
   ↓
receipt refs
```

Required properties:

- process restart does not lose Mission truth;
- unfinished task can be reconstructed;
- completed task is not rerun unnecessarily;
- surviving artifact refs are reused;
- failed optional branch remains isolated;
- destructive operation preserves at-most-once semantics;
- task receipt is idempotent;
- duplicate delivery is harmless.

If existing workflow runtime already provides suitable durable node persistence, map swarm nodes onto it instead of creating a new Swarm database.

---

# 7. DISTRIBUTED LEASE / FENCING

Process-local mutexes are insufficient for this layer.

Implement or reuse distributed coordination with:

```text
lease
heartbeat
expiry
fencing token
CAS/revision
owner worker
```

Important scenarios:

### Scenario A
Worker A owns Mission M.

Worker A pauses.

Lease expires.

Worker B takes ownership.

Worker A wakes up later.

Worker A MUST NOT commit stale state.

Use fencing/revision discipline.

### Scenario B
Two pods receive the same resume signal.

Exactly one becomes active owner.

The other observes and exits/degrades cleanly.

Do not rely on a naïve Redis SETNX lock without stale-owner protection.

---

# 8. RECOVERY COORDINATOR

Build deterministic startup/recovery logic.

Concept:

```text
scan unfinished Missions
      ↓
inspect durable state
      ↓
validate refs / workflow instances
      ↓
identify completed frontier
      ↓
verify leases
      ↓
resume only incomplete work
```

Recovery must distinguish:

```text
completed
running-but-owner-dead
retryable failure
non-retryable failure
destructive unknown outcome
waiting external dependency
cancelled
```

Never convert uncertainty into success.

---

# 9. CHECKPOINT / RESUME

Mission checkpoints should reference—not duplicate—existing subsystem state.

Checkpoint should be sufficient to reconstruct:

- Mission state
- goal revision
- relevant SessionPlan
- workflow/execution frontier
- swarm run state
- artifact inventory
- MapProduct refs
- resource budget state

Test:

```text
run → checkpoint → kill runtime → recreate service → recover → resume
```

The resumed final state must be semantically equivalent to uninterrupted execution.

---

# 10. CROSS-SESSION MISSION CONTINUITY

A Mission may span multiple sessions/turns.

Example:

Turn 1:
> 分析成都市小学分布并制作地图。

Later:
> 加入中学，并比较主城区教育设施。

Do not restart everything.

Required behavior:

```text
existing Mission
    ↓
goal revision
    ↓
semantic diff
    ↓
reuse valid artifacts
    ↓
invalidate affected subgraph only
    ↓
continue
```

Integrate with existing incremental replanning rather than recreating it.

---

# 11. MISSION-LEVEL ARTIFACT OWNERSHIP

Artifacts should outlive the individual chat turn when Mission still owns them.

Support ownership/reference semantics for:

```text
boundary
dataset
clean dataset
analysis
statistics
chart
MapSpec source
MapProduct
report
StoryMap
simulation
```

Do NOT build a second Artifact Registry.

Extend or reference the existing authoritative Artifact Registry/Graph.

---

# 12. MISSION RESOURCE GOVERNANCE

Current Resource Governor should be reused.

Extend resource accounting conceptually from:

```text
tool/session
```

toward:

```text
Mission
 ├─ session
 ├─ workflow
 ├─ swarm
 └─ heavy jobs
```

Support:
- mission quota,
- consumed budget,
- reservation,
- release,
- retry cost,
- degradation decision.

Do not create billing logic.

---

# 13. RECOVERY / COMPENSATION SEMANTICS

Explicitly model operation classes:

```text
pure
idempotent
repeatable
destructive-at-most-once
compensatable
```

For destructive or uncertain external operations:

NEVER blindly rerun after crash.

Use:

```text
receipt
operation id
idempotency key
outcome probe
manual/degraded unresolved state
```

where appropriate.

---

# 14. OBSERVABILITY

Expose bounded Mission diagnostics:

```text
mission_id
goal
state
revision
current frontier
blocked reason
running nodes
completed nodes
failed nodes
artifact count
swarm status
resource use
last checkpoint
lease owner
recovery count
```

Prefer existing diagnostics/API conventions.

A minimal Mission timeline or inspector can be added if repository UX architecture makes it appropriate, but do not perform a large unrelated frontend redesign.

---

# 15. PARALLEL-DIRECTION OWNERSHIP BOUNDARY

This branch OWNS:

- Mission lifecycle
- durable Mission state
- distributed Mission coordination
- swarm durability integration
- recovery
- Mission checkpoints
- Mission-level budget and artifact references

This branch MUST NOT implement:

- SkillPolicy / skill promotion lifecycle
- Evidence/Claim Graph
- new cartographic algorithms
- StoryMap redesign
- another planner
- another agent framework

Expose clean extension hooks for the other parallel branches.

---

# 16. TEST MATRIX

Build deterministic local tests covering at minimum:

### Lifecycle
- create
- start
- suspend
- resume
- cancel
- complete

### Crash recovery
- crash before task launch
- crash during task
- crash after task completion before receipt commit
- crash after receipt commit
- restart and recover

### Distributed ownership
- two workers contend
- lease expiry
- stale owner write
- fencing rejection
- heartbeat extension

### Swarm
- partially completed swarm
- worker restart
- completed specialists not repeated
- failed optional branch
- destructive task no duplicate execution

### Goal change
- extend goal
- narrow goal
- changed AOI
- new map output
- reuse valid artifacts

### Resource
- exhausted budget
- recovery preserves consumption
- duplicate receipt does not double-charge

### Multi-tenant
- org isolation
- user/session isolation

---

# 17. LOCAL VALIDATION

Do not wait for online CI/CD.

Use local tests.

Control resource usage aggressively.

Rules:

- never run multiple heavyweight backend full suites simultaneously;
- backend pytest parallelism maximum `-n 2`;
- if memory pressure appears, switch to serial;
- do not run frontend production build simultaneously with backend full suite;
- prefer targeted tests first;
- run broad tests only after local convergence;
- do not use giant GIS datasets;
- use synthetic/ref fixtures.

Record master baseline failures separately from branch regressions.

Do not “fix” unrelated baseline failures.

---

# 18. REVIEW

After implementation, perform an independent adversarial review.

Review axes:

```text
architecture
durability
distributed race conditions
idempotency
crash consistency
transaction boundaries
security / tenancy
resource leaks
artifact lifecycle
backward compatibility
performance
```

For every confirmed P0/P1:
- reproduce,
- add failing test,
- fix,
- rerun.

Fix safe P2 findings where practical.

Create:

`review/DURABLE-MISSION-RUNTIME-REVIEW.md`

---

# 19. REBASE / FINAL CONVERGENCE

Before PR:

1. fetch latest master again;
2. inspect any new PRs;
3. rebase/update branch;
4. resolve semantic conflicts;
5. rerun critical tests;
6. regenerate only required generated artifacts using repository-supported commands.

Do not blindly regenerate unrelated snapshots.

---

# 20. PR

Commit in meaningful milestones.

Suggested commit families:

```text
docs: mission runtime ADR/spec
feat: mission contracts and durable ledger
feat: distributed lease/fencing
feat: swarm durability bridge
feat: recovery/checkpoint runtime
feat: mission resource/artifact integration
test: crash/distributed recovery matrix
fix: adversarial review findings
```

Open an independent PR against `master`.

PR must document:

- execution-time master SHA;
- architecture before/after;
- reused existing subsystems;
- why this is not another workflow engine;
- durability model;
- lease/fencing semantics;
- crash recovery model;
- artifact ownership;
- resource model;
- test evidence;
- known baseline failures;
- independent review findings;
- rollback strategy.

DO NOT merge the PR.
DO NOT enable auto-merge.
DO NOT wait for online CI.

---

# DEFINITION OF DONE

This goal is not complete because classes exist.

It is complete only when a GIS Mission can:

```text
start
→ execute multiple GIS steps
→ delegate to specialists
→ persist receipts/artifacts
→ survive runtime/process restart
→ recover exact frontier
→ resume only unfinished work
→ reject stale worker writes
→ preserve resource accounting
→ continue across later user turns
→ complete with durable evidence
```

without rebuilding Pi, Workflow Runtime, ExecutionGraph, Swarm or Artifact Registry.