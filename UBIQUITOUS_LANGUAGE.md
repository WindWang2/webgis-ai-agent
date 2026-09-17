# Ubiquitous Language

Domain terminology for the WebGIS AI agent's cartographic evaluation loop, extracted from the
#653–#657 fail-closed evaluation work (see ADR-0061~0064, `CONTEXT.md`). This file is the
opinionated glossary; where it disagrees with older docs, this file wins.

## MapSpec lifecycle

| Term | Definition | Aliases to avoid |
| ---- | ---------- | ---------------- |
| **MapSpec** | The declarative, content-fingerprintable specification of the intended map (sources, layers, paint, intent). | spec JSON, style, map config |
| **MapSpec generation** | One revision lineage of a session's MapSpec; a verdict or observation belongs to exactly one generation. | revision (weaker: a generation may span several revisions), turn |
| **MapSpec fingerprint** | The content hash identifying a MapSpec generation; the join key between intent, observation, and verdict. | hash, style hash |
| **Superseded** | The state of a verdict/observation whose generation a newer intent has replaced; it must never be acted on. | stale, outdated |
| **MapSpec lifecycle review** | The desired-state review embedded in a mutation tool's `content` (`stage: desired_state`). | mutation review, compile review |

## Validity ladder

| Term | Definition | Aliases to avoid |
| ---- | ---------- | ---------------- |
| **Validity ladder** | The ordered rungs a MapSpec can prove: `NOT_EVALUATED` → `MUTATION_REJECTED` → `MUTATION_ACCEPTED` → `SEMANTIC_VALID` (ceiling). #694：对齐 `app/lib/runtime/evidence.py` 的实际枚举（旧 SYNTACTIC/SCHEMA/CARTO_SPEC 三级不存在于任何代码）。 | validation stages, levels |
| **L5** | An explicit unevaluated rung above the ladder with no visual/goal oracle; never inherits L4/cartographic pass. | visual validation, level 5 |

## Evaluation & evidence

| Term | Definition | Aliases to avoid |
| ---- | ---------- | ---------------- |
| **Observed Map** | The live frontend `_cartographic_observation` of what is actually rendered for a generation; the only production runtime oracle. | runtime state, live map, ACK |
| **Headless runtime** | The Playwright `webgis_runtime_validate` canvas check; record-only evidence, can never pass the map or override the Observed Map. | headless validation, runtime validator (as oracle) |
| **CartographicQuality** | The production fail-closed gate: the Observed Map for the current generation converged with intent. | the 5-float AND, overall score, eval score |
| **Cartography Verdict** | The bounded three-value projection of the current-generation review: `pass` \| `fail` \| `not_evaluated` (`passed_with_warnings` maps to `pass`). | verdict block, harness badge, gate flag |
| **not_evaluated** | The honest state when required evidence is missing, stale, or unverifiable; never counts as pass. | unknown, skipped, N/A |
| **Fail-closed** | The policy that missing evidence fails safe: `not_evaluated`, `overall_passed: False`, no exemption. | strict mode, deny-by-default |
| **overall_passed** | The gate flag on the stored review meaning CartographicQuality only; deliberately absent from the inject. | passed, success flag |
| **EvaluationEvidence** | The harness-collected record (checks, repair attempts, counters) a verdict is rendered from. | evidence dict, harness dump |

## Delivery channels

| Term | Definition | Aliases to avoid |
| ---- | ---------- | ---------------- |
| **Inject** | The automatic next-turn user-message attachment of the Cartography Verdict; requires a fingerprint match with the current generation. | push, auto-inject, prompt injection |
| **Pull** | The agent explicitly querying `webgis_cartography_status`, which returns the full stored review (including pass details). | status query, fetch |
| **Same-turn content** | The mutation tool's `content`, which carries the MapSpec lifecycle review — never the harness Cartography Verdict. | tool result, payload |
| **No-activity** | A session with no cartographic work (`no_session_harness` / `no_mapspec_mutation`); injects nothing. | idle, quiet |

## Quality loop

| Term | Definition | Aliases to avoid |
| ---- | ---------- | ---------------- |
| **Quality loop** | The evaluate → AUTO_SAFE repair → re-evaluate cycle around the cartographic gate. | repair loop, self-heal |
| **AUTO_SAFE repair** | The bounded automatic repair class the loop may attempt without user confirmation. | auto-fix, remediation |

## Test lanes

| Term | Definition | Aliases to avoid |
| ---- | ---------- | ---------------- |
| **Self-skip** | A test's own guard that skips when its services/flags are absent; never masks "unreachable" as "passed". | conditional skip, auto-skip |
| **Real-services lane** | The CI smoke subset (PostGIS + Redis + real Celery worker) armed only by explicit `REAL_SERVICES=1`. | smoke tests, integration lane |
| **Perf lane** | The isolated `pytest -m perf` baseline run; unfiltered full-suite runs self-skip perf items. | benchmarks, perf harness |

## Lakehouse (V6)

| Term | Definition | Aliases to avoid |
| ---- | ---------- | ---------------- |
| **DataObject** | A durable, immutable lakehouse data object whose id is the sha256 of its canonical manifest (content-addressed, no opt-in). Kinds: `vector_parquet`, `cog_raster`, `zarr_cube`. | file, dataset (weaker), blob |
| **DataObject manifest** | The deterministic canonical JSON record (kind, owner_scope, content_blobs, payload, input_fingerprint, environment_fingerprint); no wall-clock — same inputs+params ⇒ same id. | metadata, descriptor |
| **Logical alias** | A readable pointer (session alias / Artifact name) to a DataObject revision; carries no content and never changes identity. | handle (vague) |
| **content root** | The sha256 over the sorted (path, digest, size) blob list of an object — the merkle identity of its bytes. | checksum (weaker) |
| **Owner scope** | Exactly one of `session_id`/`project_id` participating in identity and enforced on every use; byte blobs may dedupe across owners, judgments never do. | tenant (reserved for cross-tenant isolation) |
| **Row-group bbox map** | Per-row-group bboxes computed from real geometry at GeoParquet write time (`webgis:row_groups` schema metadata); the pruning evidence for window scans. | spatial index |
| **Cube revision** | An immutable cube state identified by a manifest; produced either by publication or a hardlink copy-on-write fork (the source store stays byte-identical). | version (weaker), snapshot (reserved) |
| **Lazy materialization** | ref-only until a bounded window/chunk read; proven structurally (row-group prune counts, chunk-touch counts), never by wall-clock alone. | streaming |

## GIS Skill / Procedure Library (ADR-0182)

| Term | Definition | Aliases to avoid |
| ---- | ---------- | ---------------- |
| **GIS Skill** | A versioned, machine-validated contract for a reusable **domain procedure** (e.g. `point_distribution_analysis`) — ordered steps, decision points, statistical/geographic/temporal semantics, quality obligations, fallbacks, completion evidence. Lives in `app/services/gis_harness/skills/library/`. | prompt skill, agent skill, tool |
| **Chat prompt skill (legacy)** | The `.md`/`.py` assets under `app/skills/` injected into chat prompts or loaded as executable tools. NOT a GIS Skill; unstructured, unversioned. | skill (ambiguous), runtime skill |
| **Developer skill** | An agent-workflow SKILL.md (tdd, review, gstack) under `agent/skills/` or `.agents/skills/`. Coding-process knowledge, never consumed by the product runtime. | skill |
| **SkillCard** | The bounded first-layer projection of a GIS Skill (id/description/when_to_use/requirements) shown before selection; the full procedure is read on demand. | full skill dump (forbidden) |
| **SkillResolver** | The deterministic-first selector over GIS Skills (ranked + confidence + reasons + rejected-with-fallback + clarification). Zero LLM. | recommender, LLM picker |
| **SkillComposition** | An explicit, acyclic, bounded-depth (≤4) ordering of GIS Skills across job phases (analysis → cartography → delivery). Distinct from **CompositeRecipe**, which composes cartographic layers within a plan. | recipe composite |
| **Procedure replay** | Deterministic verification that a selected skill's required steps and obligations are covered by plan/evidence projections (covered / missing / skipped_declared / unknown). | LLM "looks fine" check |

## Relationships

- A **GIS Skill** *references* capabilities (ids validated against `CapabilityRegistry`), recipes (`RecipeRegistry`), ontology tasks (`gis_ontology`), and data roles (`workflow_schema.DATA_ROLES`) — it never duplicates their vocabularies.
- A **GIS Skill** sits between the **user goal** and capability/execution planning: `Goal → Skill procedure → capability requirements → tools`; it is not a second planner and not an agent loop.
- **Recipe** owns cartographic method choice; **Template** owns visual composition; **MapSpec** owns the desired map state; the **GIS Skill** owns the whole-job procedure and its obligations.

## Relationships

- A **Cartography Verdict** belongs to exactly one **MapSpec generation**, joined by **MapSpec fingerprint**.
- A **generation** has at most one current **Cartography Verdict**; a newer intent **supersedes** it.
- **CartographicQuality** produces the stored review; the **Cartography Verdict** is its bounded projection; **overall_passed** stays on the review and never enters the **inject**.
- The **Observed Map** is the only runtime oracle for **CartographicQuality**; the **headless runtime** is record-only.
- **Inject** and **pull** read the same stored review; **same-turn content** never carries it.

## Example dialogue

> **Dev:** "The layer upsert compiled fine and the tool returned success — can I tell the user the map is correct?"
> **Domain expert:** "No. Tool success is not the **Cartography Verdict**. The mutation's **same-turn content** only carries the **MapSpec lifecycle review** — desired state. Wait for the next-turn **inject** or **pull** `webgis_cartography_status`."
> **Dev:** "The headless validator passed, though. Doesn't that prove the runtime?"
> **Domain expert:** "The **headless runtime** is record-only. Production pass requires the **Observed Map** for the current **generation** — same **fingerprint**. Without it the verdict is **not_evaluated**, which is **fail-closed**: `overall_passed` stays false."
> **Dev:** "And if the verdict block never shows up in the next turn?"
> **Domain expert:** "Then there was **no-activity**, the generation was **superseded**, or the fingerprint didn't match. Since #657, silence is never pass — a passing current generation injects a tiny `pass` token."

## Map Review (ADR-0201)

| Term | Definition | Aliases to avoid |
| ---- | ---------- | ---------------- |
| **Map Review（空间审查/会签）** | The governance workflow over MapSpec changes: ReviewProposal → anchored comments → ReviewDecision → governed merge via the existing mutation transaction. A **governance** review — not the lifecycle review, not the harness review. | bare "review", code review |
| **ReviewProposal** | A bounded set of MapSpec mutation intents (the 14-body union) against a `base_revision`, authored by a user or an agent, moving through draft/submitted/changes_requested/approved/rejected/merged/superseded/withdrawn. | PR, change request |
| **AnchoredComment** | A review comment bound to a map structure (layer/component/feature) or evidence surface (claim/artifact); anchors evaluate to ok/stale/unverified — unresolvable is **unverified, never stale**. | inline comment |
| **ReviewDecision** | Append-only approve/request_changes/reject record with `base_revision_at_decision`; approvals expire when the proposal rebases. | vote |
| **Approval policy** | The fail-closed v1 policy: agent decisions never accepted; agent-authored proposals need a distinct human approval; high-risk needs a distinct editor+ reviewer; anonymous sessions cannot approve high-risk. | role-based access |
| **MergeEvidence** | Structured merge record (checkpoint id, applied steps with revisions, rollback/interleave flags, policy snapshot); has no chain-of-thought fields by contract. | merge log |

## Flagged ambiguities

- **"review"** was used for both the **MapSpec lifecycle review** (`stage: desired_state`, in mutation
  `content`) and the harness's stored `_cartographic_review` (`stage: actual_runtime`). These are
  distinct objects; say "lifecycle review" vs "harness review" when context doesn't disambiguate.
- **"pass"** was used for the verdict token, raw status `passed`/`passed_with_warnings`, and the
  `overall_passed` gate flag. Canonical: the **Cartography Verdict** token is `pass`;
  `overall_passed` is the gate flag and never enters the inject (#657).
- **"skill"** meant three different things: chat prompt skills (`app/skills/`), executable skill
  scripts (`app/skills/*.py`), and developer skills (`agent/skills/`). Canonical (ADR-0182): the
  structured domain procedure is the **GIS Skill**; say "prompt skill" or "developer skill" for the
  other two; never use bare "skill" when context could span them.
- **"runtime"** was used for both the **headless runtime** (Playwright, record-only) and the live
  **Observed Map** runtime. Only the latter is the production oracle (ADR-0061).
- **"silence"** (no inject) used to be ambiguous between pass / no-activity / superseded; after
  #657 it means only **no-activity** or **superseded** — never pass.
- **"skip"** is overloaded between injection policy (verdict skipped) and pytest **self-skip**;
  both are deliberate non-events, but one is a delivery decision and the other a test-lane guard.
- **Map Review vs lifecycle review vs harness review**: three different "review"s. Say **Map Review**（审查/会签）for the governance workflow (ADR-0201), **lifecycle review** for the desired-state stage, **harness review** for the stored evaluation.
