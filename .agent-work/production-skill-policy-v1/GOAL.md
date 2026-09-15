# GROK BOT AUTONOMOUS DEVELOPMENT GOAL
# Direction 02 — Production GIS Skill Policy & Self-Evolving Procedure Runtime

Repository:
https://github.com/WindWang2/webgis-ai-agent

Develop this direction in parallel with other worktrees.

Goal:

> Move the existing GIS Skill system from a primarily queryable procedure library into a safe production planning policy layer used by the Pi-hosted GIS Harness.

The repository already has substantial skill infrastructure:
- SkillContract
- SkillProcedure IR
- SkillResolver
- SkillLibrary
- Skill Packs
- skill replay validation
- Situation-compatible selection facts
- trajectory → induced skill engine
- sandbox validation
- induced skill isolation
- ReplayTrace
- GoalSatisfactionEvaluator
- Capability Graph
- Execution Graph

DO NOT rebuild any of these.

The problem to solve is:

> Skills exist and can be learned, but when and how should production planning trust and use them?

---

# 0. AUTONOMOUS EXECUTION

Fully autonomous.

Before coding:

```text
git fetch --all --prune
inspect latest origin/master
inspect open PRs
inspect merged PRs
inspect issues
inspect reviews
inspect ADRs
inspect production call paths
```

Repository truth at runtime overrides this prompt.

Do not ask the user to choose architecture.

---

# 1. WORKTREE

Create an independent worktree.

Suggested branch:

`harness/production-skill-policy-v1`

Suggested worktree:

`../webgis-ai-agent-wt-skill-policy`

Base it on latest `origin/master`.

Do not develop in master.
Do not merge your own PR.

---

# 2. CORE ARCHITECTURE

Desired production chain:

```text
User Goal
   ↓
Pi
   ↓
GIS Situation
   ↓
Skill Policy              <-- THIS WORK
   ↓
SkillResolver
   ↓
Skill / Composition
   ↓
Capability requirements
   ↓
existing planning / Execution Graph
   ↓
tools/providers
```

Important:

Skill selection is guidance, not a mandatory gate for all GIS tasks.

Correct fallback:

```text
high-confidence eligible skill
      ↓
skill-guided planning

no suitable skill
      ↓
existing capability/planning path
```

There must always be a clean fallback to existing Harness planning.

---

# 3. PHASE 0 — RECON

Deep-read:

- GIS Skill loader
- resolver
- catalog
- contracts
- procedure IR
- composition
- replay
- skill evidence
- Skill Packs
- `app/tools/skill_library_tools.py`
- GIS Situation
- intent
- SessionPlan
- Capability Graph
- planner
- Execution Graph
- ReplayTrace
- Goal Satisfaction
- skill induction engine
- dynamic capability provider
- Pi tool surface
- Pi bridge
- current prompt/context assembly

Determine exactly:

```text
where skills are selected today
whether automatic production selection already exists
whether procedure steps influence SessionPlan
whether skills influence capability planning
whether selection evidence reaches replay/evaluation
how induced skills are loaded
why induced skills are excluded by default
```

Do not rely on task wording if code has moved.

Write a recon document first.

---

# 4. SKILL POLICY

Introduce a policy layer only if an equivalent production policy does not already exist.

Conceptual contract:

```text
SkillPolicyDecision

selected_skill?
skill_version
trust_tier
mode
confidence
eligibility
situation_signature
reasons
fallback_reason
shadow_candidate?
```

Possible modes:

```text
none
guide
execute_guided
shadow
blocked
fallback
```

Adjust vocabulary to existing repository conventions.

---

# 5. TRUST / LIFECYCLE MODEL

A useful lifecycle may include:

```text
core
candidate
experimental
quarantined
deprecated
```

Do not blindly introduce new enums if existing Skill Pack semantics are enough.

Critical rule:

> runtime MUST NOT automatically modify the reviewed core skill library.

Respect the existing induced-skill safety boundary.

Generated skills remain data assets.

No runtime code generation or arbitrary execution.

---

# 6. PRODUCTION HOT-PATH INTEGRATION

Integrate SkillPolicy at the smallest authoritative seam.

Do NOT scatter calls throughout the codebase.

Preferred conceptual location:

```text
Situation / resolved intent
          ↓
SkillPolicy.resolve(...)
          ↓
planning input
```

The result should influence:

- selected procedure semantics;
- expected capability set;
- quality obligations;
- methodology obligations;
- required evidence;
- fallback procedure;
- completion validation.

Do NOT let a Skill directly bypass:
- capability eligibility,
- security,
- spatial guardrails,
- resource governor,
- tool schema validation,
- mutation transactions.

Skills describe HOW to perform a GIS task.

They do not grant authority.

---

# 7. SKILL → PLAN COMPILATION

Build or reuse a deterministic adapter:

```text
SkillProcedure
    ↓
SkillPlanningProjection
    ↓
existing SessionPlan / planner / ExecutionGraph
```

Do not create another DAG.

Preserve existing Execution Graph as execution truth.

The projection should be bounded and contain things such as:

```text
required capabilities
optional capabilities
step ordering constraints
preconditions
quality obligations
methodology obligations
evidence requirements
fallback hints
```

Large skill bodies must not be dumped into Pi context.

Use progressive disclosure.

---

# 8. SITUATION-AWARE SKILL PERFORMANCE

Track performance conditioned on GIS situation.

Do not reduce skill quality to one global score.

At minimum consider signals such as:

```text
task family
geometry kind
data scale
AOI scale
CRS class
online/offline
desired product
analysis family
provider availability
```

Create a bounded:

```text
SkillPerformanceProfile
```

Metrics can include:

```text
attempts
success
goal satisfaction
partial completion
fallback frequency
repair count
latency
resource cost
cartography quality
methodology failures
```

Do not create a second general telemetry platform; reuse existing evaluation/replay infrastructure.

---

# 9. SHADOW MODE FOR INDUCED SKILLS

This is a major requirement.

Induced skills MUST NOT immediately control production execution.

Implement shadow evaluation:

```text
production planner
       │
       ├── actual plan
       │
       └── candidate induced skill → shadow projection
```

Compare:

```text
procedure topology
capability choice
eligibility
expected cost
goal requirements
methodological obligations
actual outcome after completion
```

A shadow skill must not mutate production state.

---

# 10. PROMOTION EVIDENCE

Build automatic promotion PROPOSALS, not automatic core modification.

Concept:

```text
induced skill
   ↓
N valid shadow/evaluation runs
   ↓
promotion evidence
   ↓
PromotionCandidateReport
```

Report should include:

```text
skill id/version
support count
domain diversity
parameter diversity
success rate
goal satisfaction distribution
failure cases
counterexamples
resource cost
fallback rate
quality result
security/sandbox status
recommended disposition
```

Disposition might be:

```text
keep_shadow
candidate
quarantine
reject
```

Core promotion remains code/review controlled.

---

# 11. ANTI-OVERFITTING

A successful trajectory is not sufficient evidence for a universal GIS procedure.

Add holdout/counterexample evaluation.

Examples:

```text
成都学校分布
≠
all POI distributions

point choropleth invalid
polygon choropleth valid

projected metric operation
≠
safe in geographic CRS
```

Use:
- support diversity,
- parameter diversity,
- geometry diversity,
- geographic diversity where available,
- negative examples,
- ReplayTrace holdout.

Fail closed when evidence is insufficient.

---

# 12. SKILL VERSION LINEAGE

Track:

```text
skill v1
  ↓
observed failures
  ↓
candidate v2
```

Preserve:

```text
parent version
reason for evolution
evidence refs
counterexamples
performance comparison
```

Do not overwrite historical evidence.

Rollback to previous trusted version must remain possible.

---

# 13. SKILL COMPOSITION LEARNING

The repository already supports skill composition concepts.

Improve production composition where code evidence supports it.

Examples:

```text
data_discovery
→ spatial_analysis
→ statistical_summary
→ thematic_cartography
→ delivery
```

Selection must account for:
- prerequisite compatibility;
- output→input semantic compatibility;
- CRS requirements;
- resource constraints;
- evidence obligations.

Do not create another generic workflow language.

---

# 14. FAILURE / FALLBACK

Required behavior:

```text
skill unavailable
→ normal planner

skill ineligible
→ normal planner

skill confidence weak
→ normal planner

skill procedure step unavailable
→ capability fallback / plan repair

skill violates guardrail
→ block that action, not the guardrail

induced skill suspicious
→ quarantine/shadow
```

Never force a skill just because one matches textually.

---

# 15. USER/Pi CONTEXT

Pi context should receive only bounded decision information such as:

```text
Selected GIS Procedure:
id
version
purpose
critical obligations
next recommended step
fallback
```

Do not inject the complete library.

The existing progressive disclosure principle must remain.

---

# 16. OBSERVABILITY

Add bounded diagnostics:

```text
skill considered
skill selected
trust tier
confidence
eligibility failures
shadow candidate
fallback
procedure progress
quality outcome
```

Use existing event/evidence vocabularies where possible.

---

# 17. OWNERSHIP BOUNDARY FOR PARALLEL DEVELOPMENT

This branch OWNS:

- SkillPolicy
- production skill selection seam
- skill-to-plan projection
- skill trust lifecycle
- induced-skill shadow mode
- skill performance profiles
- promotion proposal generation
- skill version lineage

This branch MUST NOT implement:

- durable Mission Runtime
- distributed job ownership
- Evidence/Claim Graph
- new MapSpec renderer
- StoryMap changes
- new spatial algorithms
- second ExecutionGraph
- second Agent framework

---

# 18. TEST MATRIX

Test at minimum:

### Selection
- high-confidence eligible skill
- low confidence
- no match
- multiple competing skills
- geometry mismatch
- CRS mismatch
- unavailable capability

### Hot-path
- skill influences planning projection
- no-skill path preserves existing behavior
- skill cannot bypass security/tool/resource guards

### Induced
- induced excluded from trusted path
- shadow evaluation works
- shadow cannot mutate state
- unsafe skill quarantined
- malformed dynamic asset rejected

### Evolution
- promotion candidate requires sufficient evidence
- one success does not promote
- counterexample blocks promotion
- v2 lineage preserves v1

### Composition
- compatible chain
- incompatible outputs
- missing prerequisite
- fallback route

### Determinism
same inputs produce same policy decision.

---

# 19. LOCAL TEST / RESOURCE DISCIPLINE

No waiting for online CI.

Use local tests.

Resource rules:

- targeted tests first;
- max backend pytest parallelism `-n 2`;
- one heavyweight suite at a time;
- no backend full suite while Next build is running;
- use small deterministic fixtures;
- do not invoke real LLM/network for unit tests;
- synthetic GIS fixtures only.

Record baseline failures separately.

---

# 20. ADVERSARIAL REVIEW

After implementation perform an independent review covering:

```text
planner bypass
skill overreach
induced-skill privilege escalation
cross-tenant state
overfitting
stale skill version
false promotion
failure fallback
context bloat
resource regressions
non-determinism
```

Create:

`review/PRODUCTION-SKILL-POLICY-REVIEW.md`

Confirmed P0/P1 findings:
reproduce → failing test → fix → rerun.

---

# 21. FINAL REBASE AND PR

Before PR:

```text
fetch master
inspect new PRs
rebase/update
resolve semantic conflicts
rerun critical tests
```

Open a standalone PR.

PR should explain:

- current skill architecture discovered;
- production hot-path before/after;
- why no second planner was created;
- skill trust policy;
- shadow mode;
- promotion evidence;
- fallback behavior;
- test evidence;
- review findings;
- rollback.

DO NOT merge.
DO NOT auto-merge.
DO NOT wait for CI.

---

# DEFINITION OF DONE

The line is complete when a normal GIS user request can flow:

```text
Goal
→ Situation
→ SkillPolicy
→ SkillResolver
→ trusted procedure
→ existing planning/execution
→ evidence
→ goal evaluation
→ performance update
```

while:

```text
no suitable skill
→ existing planner unchanged
```

and:

```text
induced skill
→ shadow/evaluation
→ promotion proposal
```

without runtime self-modification of reviewed core assets.