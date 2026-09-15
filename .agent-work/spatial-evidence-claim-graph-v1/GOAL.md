# GROK BOT AUTONOMOUS DEVELOPMENT GOAL
# Direction 03 — Spatial Evidence / Claim / Provenance Graph

Repository:
https://github.com/WindWang2/webgis-ai-agent

Goal:

> Build a unified spatial evidence and claim layer that makes every important analytical, statistical, cartographic and narrative conclusion traceable to authoritative GIS evidence.

This project already contains many evidence/provenance mechanisms.

You MUST NOT build a second Artifact Registry or duplicate existing lineage.

Instead, discover and unify the current evidence backbone.

---

# 0. AUTONOMOUS WORKFLOW

Fully autonomous.

At execution time:

```text
git fetch --all --prune
read latest origin/master
inspect open PRs
inspect reviews
inspect issues
inspect recently merged work
```

At prompt-writing time StoryMap hardening #1318 was open.

Re-check.

If it is still open:
- do not modify its touched files unless absolutely necessary;
- expose clean adapters/hooks instead.

If it has merged:
- rebase first;
- integrate only where conflict-free.

Do not ask the user for ordinary architecture decisions.

---

# 1. WORKTREE

Create an independent worktree.

Suggested branch:

`harness/spatial-evidence-claim-graph-v1`

Suggested worktree:

`../webgis-ai-agent-wt-evidence-graph`

Base on latest `origin/master`.

Never work directly on master.

---

# 2. PROBLEM STATEMENT

The repository already has evidence in many places:

```text
Dataset descriptors
Data Fabric provenance
dataset versions
tool receipts
Workflow/Execution outputs
Artifact Registry
Artifact Graph
product lineage
MapSpec revisions
mutation ledger
Goal Satisfaction evidence
ReplayTrace
methodology evidence
uncertainty
VLM visual evaluation
simulation output
StoryMap trace input
```

But these are not yet one semantic answer to:

> “Why does the Agent claim this?”

The new layer must provide that answer.

---

# 3. ARCHITECTURAL RULE

Do NOT create:

```text
SecondArtifactRegistry
SecondProvenanceDatabase
SecondReplaySystem
SecondProductLineage
```

Instead:

```text
existing authoritative refs
          ↓
Evidence projection/index
          ↓
Claim graph
```

Prefer a graph/projection over authoritative stores.

Persist only what is necessary for durable claim identity and relationships.

---

# 4. PHASE 0 — EVIDENCE CENSUS

Deep-read:

- provenance service
- artifact registry
- artifact graph
- product lineage
- MapProduct graph/runtime
- data fabric/data lifecycle
- dataset descriptors
- execution receipts
- workflow runtime
- SessionPlan evidence
- GoalSatisfactionEvaluator
- anti-claim/evaluation code
- methodology obligations
- uncertainty contracts
- ReplayTrace
- GIS memory provenance
- MapSpec revision/mutation ledger
- VLM/visual evaluator
- StoryMap compiler
- simulation outputs
- What-if reports

Produce a source-of-truth matrix:

```text
fact/evidence kind
authoritative store
identity
version
freshness
producer
consumers
current lineage edges
gaps
```

Do this BEFORE creating a new schema.

---

# 5. EVIDENCE NODE MODEL

Design a versioned bounded evidence contract.

Possible categories:

```text
dataset
dataset_version
spatial_subset
transformation
analysis
statistic
spatial_relation
artifact
map_layer
map_view
chart
simulation
observation
user_assertion
external_source
```

Each EvidenceNode should preferably reference authoritative objects:

```text
evidence_id
kind
ref
producer
version
revision
scope
time
geometry_scope?
method?
uncertainty?
freshness
provenance
```

Avoid storing heavy payloads.

---

# 6. CLAIM MODEL

Introduce typed spatial/analytical claims.

Example:

```text
Claim:
  成都武侯区学校密度最高
```

should not be stored only as free text.

Possible structure:

```text
claim_id
claim_type
subject
predicate
value
unit
comparator
reference_scope
temporal_scope
spatial_scope
method
supporting_evidence_refs
contradicting_evidence_refs
confidence
uncertainty
status
```

Claim status may include something like:

```text
supported
partially_supported
unsupported
contradicted
stale
unknown
```

Use existing repository vocabulary if available.

---

# 7. CLAIM GENERATION DISCIPLINE

Prefer deterministic typed claim production from actual analysis outputs.

Examples:

```text
count
sum
mean
median
density
rate
percentage
rank
maximum
minimum
change
trend
difference
spatial relation
coverage
```

LLM-generated prose may describe claims.

LLM prose MUST NOT be the authoritative numeric source.

Numerical values must resolve to analysis/statistic evidence.

---

# 8. RELATION GRAPH

Support typed edges such as:

```text
derived_from
computed_by
aggregated_from
filtered_from
supports
contradicts
visualized_as
summarized_by
supersedes
invalidates
depends_on
```

Keep the relation vocabulary bounded.

Graph traversal must be bounded and cycle-safe.

---

# 9. POSITIVE PROOF

One core design principle:

> absence of a detected problem is not evidence of completion.

Build positive proof requirements.

Example:

Bad:

```text
uncertainty not blocked
=> uncertainty disclosed
```

Correct:

```text
uncertainty evidence exists
+
output disclosure ref exists
+
disclosure is connected to claim/product
=> disclosed
```

Apply the same principle where useful to:
- data provenance,
- map rendering,
- methodology,
- goal completion,
- narrative conclusions.

---

# 10. CLAIM VERIFICATION

Implement deterministic verification.

Concept:

```text
Claim
 ↓
resolve evidence refs
 ↓
verify liveness
 ↓
verify data/version freshness
 ↓
verify semantic compatibility
 ↓
verify method/units/scope
 ↓
verify uncertainty obligations
 ↓
verdict
```

Never promote missing evidence to PASS.

---

# 11. STATISTICAL SEMANTICS

Treat the following as distinct:

```text
count
density
rate
ratio
share
percentage
mean
median
change
growth
risk
score
```

Do not allow narrative drift such as:

```text
学校数量最高
```

becoming:

```text
教育资源最好
```

without additional supporting evidence.

Build semantic compatibility checks.

---

# 12. CARTOGRAPHIC CLAIM BINDING

Connect map appearance to data evidence.

Example:

User asks:

> 为什么这个区域是红色？

Harness should be able to traverse:

```text
rendered layer
→ style/class
→ classification break
→ metric value
→ analysis/statistic
→ source dataset version
```

Return bounded explainability—not chain-of-thought.

Store/resolve only technical evidence.

---

# 13. PRODUCT / MAP LINEAGE INTEGRATION

Reuse existing Map Product lineage.

Link:

```text
Claim
 ↔ statistic
 ↔ chart
 ↔ map layer
 ↔ MapProduct facet
 ↔ MapSpec
```

Do not replace current Product Graph.

The Evidence/Claim Graph sits across it.

---

# 14. NARRATIVE GROUNDING

StoryMap/report/final-response integration should use Claim references.

Ideal flow:

```text
Evidence
  ↓
Claim
  ↓
Narrative sentence
```

Narrative generation may be flexible.

Claims and numbers remain grounded.

If #1318 remains open, avoid editing its files.

Build an adapter such as:

```text
ClaimNarrativeProjection
```

that StoryMap can consume after integration.

---

# 15. CONTRADICTION DETECTION

Example:

Evidence A:

```text
武侯区 rank = 1
```

Evidence B:

```text
锦江区 rank = 1
```

Do not silently narrate both.

Create a contradiction result and inspect:

```text
dataset version
time
filter
AOI
metric definition
method
```

Sometimes both claims are valid under different scopes.

The graph should make that distinction explicit.

---

# 16. FRESHNESS / INVALIDATION

When upstream evidence changes:

```text
Dataset v12
→ Dataset v13
```

find affected downstream:

```text
analysis
statistics
claims
charts
MapProducts
narrative sections
```

Do not eagerly recompute everything here.

Instead expose an affected-descendants result that the existing replanning/execution system can consume.

---

# 17. EVIDENCE CONFLICTS AND STALE STATE

Handle:

```text
missing ref
deleted artifact
superseded dataset
changed MapSpec revision
stale observation
changed filter
changed AOI
changed method
```

A stale claim must stop being presented as fresh truth.

---

# 18. QUERY API

Provide bounded technical queries such as:

```text
why_claim(claim_id)
why_map_feature(...)
evidence_for(...)
claims_for_artifact(...)
affected_claims(ref)
contradictions(...)
```

Avoid unrestricted graph dumping.

Large provenance graphs should return summaries + refs.

---

# 19. FINAL ANSWER SUPPORT

Expose a bounded grounding projection suitable for Pi:

```text
Claim:
...
Status:
supported

Evidence:
- statistic ref
- dataset version
- analysis method
- map layer

Uncertainty:
...

Freshness:
...
```

No hidden reasoning/CoT.

---

# 20. OWNERSHIP BOUNDARY FOR PARALLEL WORK

This branch OWNS:

- Evidence projection/index
- Claim model
- evidence↔claim relations
- claim verification
- contradiction handling
- freshness/invalidation projection
- evidence query/explainability APIs
- bounded grounding projection

This branch MUST NOT implement:

- Durable Mission Runtime
- SkillPolicy
- another Artifact Registry
- another ExecutionGraph
- another StoryMap engine
- new simulation engines
- new cartographic algorithms

Coordinate through refs/interfaces only.

---

# 21. TEST MATRIX

At minimum test:

### Evidence
- live ref
- missing ref
- superseded ref
- stale version
- cross-tenant ref rejected

### Claims
- supported numeric claim
- unsupported claim
- partial evidence
- contradiction
- stale claim

### Semantics
- count vs density
- percentage vs raw count
- mismatched temporal scope
- mismatched AOI
- mismatched unit

### Map
- map style class traces back to statistic
- changed breaks invalidate old styling explanation

### Product
- Product facet links to claims/evidence
- dead artifact marks dependent claim stale

### Narrative
- narrative can reference claim
- free-text claim without evidence cannot become verified

### Invalidation
- dataset version update returns correct affected descendants

### Determinism
same evidence graph → same claim verdict.

---

# 22. PERFORMANCE

Graph operations must be bounded.

Test representative cases:

```text
100 nodes
1k nodes
10k lightweight relation records
```

Avoid loading heavy GIS payloads.

Graph traversal requires:
- max depth,
- max node count,
- cycle guard.

---

# 23. LOCAL VALIDATION

No waiting for online CI/CD.

Use targeted local tests first.

Resource discipline:

- backend test parallelism max `-n 2`;
- one heavy test/build at a time;
- no huge datasets;
- no real LLM calls in unit tests;
- use refs/synthetic fixtures;
- broad regression only after convergence.

Differentiate pre-existing master failures from branch regressions.

---

# 24. INDEPENDENT ADVERSARIAL REVIEW

Review specifically for:

```text
false evidence
cross-tenant leakage
stale claim presented as fresh
claim scope mismatch
unit mismatch
unsupported narrative claim
graph cycles
unbounded traversal
artifact duplication
provenance divergence
false PASS
```

Create:

`review/SPATIAL-EVIDENCE-CLAIM-GRAPH-REVIEW.md`

For P0/P1:
reproduce → red test → fix → green.

---

# 25. FINAL REBASE

Before PR:

```text
fetch latest master
inspect new PRs
rebase/update
```

Pay special attention to StoryMap hardening or any new provenance-related PR.

Resolve semantic conflicts rather than blindly taking ours/theirs.

---

# 26. PR

Use meaningful milestone commits.

Suggested shape:

```text
docs: evidence/claim graph ADR and source census
feat: evidence projection contracts
feat: typed spatial claim model
feat: claim verification and contradictions
feat: provenance/freshness invalidation
feat: map/product grounding adapters
feat: bounded query/explainability API
test: evidence and anti-claim matrix
fix: adversarial review findings
```

PR body must include:

- master SHA;
- evidence census;
- authoritative stores reused;
- why this is not another Artifact Registry;
- claim semantics;
- positive-proof model;
- freshness model;
- contradiction model;
- map/product grounding;
- performance;
- tests;
- review findings;
- known limits;
- rollback.

DO NOT merge.
DO NOT enable auto-merge.
DO NOT wait for online CI.

---

# DEFINITION OF DONE

The direction is complete when the system can answer technical questions like:

```text
“为什么武侯区被判断为学校密度最高？”
```

through:

```text
Claim
→ statistic
→ method
→ source dataset/version
→ spatial/temporal scope
→ uncertainty
→ map/chart representation
```

and can automatically mark that claim stale or contradicted when its supporting evidence changes.

The result must strengthen the existing Pi + GIS Harness architecture, not replace it.