# Evidence Source-of-Truth Census (Phase 0)

Base master: `e21314a5`. Direction 02 PR #1327 open — SkillPolicy files out of scope.

Pattern: **existing authoritative refs → Evidence projection/index → Claim graph**.
No SecondArtifactRegistry / SecondProvenanceDatabase / SecondReplaySystem / SecondProductLineage.

## Source-of-truth matrix

| fact/evidence kind | authoritative store | identity | version | freshness | producer | consumers | current lineage edges | gaps for claim graph |
|---|---|---|---|---|---|---|---|---|
| tool artifact / GIS result | `ArtifactRegistry` (`artifact_registry.py`) | `artifact_id` = ref string (`ref:…`) | `revision` | status: valid/stale/superseded/expired/failed | capability/tool/node | SessionPlan, MapSpec source, ProductLineage | `inputs[]`, `replaces` | no typed claim; numbers live in payloads not indexed |
| artifact graph (derived) | `ArtifactGraph` (same module) | derived from records | n/a | projected | pure fn | dependency queries | producer/consumer/lineage/replacement | projection only — do not persist |
| map product facets | `product_graph.py` ProductGraph | `node_id` / facet key | via MapSpec / row | status projected from rows/components | SessionPlan + MapSpec | completeness, action_intent | facet inputs | free-text `MapProductSpec.claims[]` not typed |
| facet↔artifact lineage | `product_lineage.py` | facet_id + LineageRef | via ArtifactRecord | liveness: alive/expired/stale/superseded/failed/unknown | ProductGraph + registry snapshot | P4 recompute | facet→artifact refs | unknown ≠ pass; no claim layer |
| product runtime/spec | `product_runtime.py` / `product_spec.py` | `MapProductSpec` | `spec_digest` | merge_spec_with_replay | plan compiler | tools SSE | view bindings | claims are `List[str]` prose — must not be authoritative |
| goal satisfaction evidence | `goal_satisfaction/` | `GoalEvidence.id` | `revision` (bound_ref / fingerprint) | PRESENT/ABSENT/STALE/FAILED | chapter/map_product/review | evaluator | requirement↔evidence_ids | task completion, not spatial claim semantics |
| harness eval evidence | `app/lib/harness/evidence.py` | correlation ids | mapspec revision / checkpoint | NOT_EVALUATED ≠ success | tool results | cartography loop | validity ladder | MapSpec/carto validity, not stats claims |
| replay packing | `app/lib/harness/replay/schema.py` ReplayTrace | session/turn/run | schema_version=1 | truncated flag | GisTraceChain + TurnEvidence | offline replay | artifacts/mutations dicts | packs refs — not claim graph |
| session plan truth | `session_plan.py` | plan/chapter/row | plan revision | row status | planner | ProductGraph, registry | bound_ref pointers | plan is canonical; we only read |
| StoryMap narrative | `story_compiler.py` | StoryMapSpec chapters | n/a | from trace stages | GisTraceChain / messages | export | refs extracted from text | needs ClaimNarrativeProjection adapter |
| dataset version pin | `data_fabric/versioning_gate.py` | dataset_key + pin | version_token / content_fingerprint / schema_fp | drift alerts | acquisition | query replay | SnapshotRecord | claim must ref pin/version_token |
| data fabric facts | `data_fabric/facts.py` | fact_id | wave | budget/ratchet | acquisition | cost governance | source_id | cost facts ≠ analysis claims |
| provenance fingerprints | `app/services/provenance/` | content/dataset/graph/run fingerprints | INV-FP/REV/MAN | pure deterministic | tools/workflows | manifests | fingerprint edges | hash SoT — claim stores refs only |
| MapSpec mutation ledger | `mapspec_mutations` + store | mutation_id / revision | expected_revision | revision monotonic | user/agent patches | runtime | layer style/presentation | style→metric binding missing for “why red?” |
| scientific uncertainty | `app/lib/gis/uncertainty.py` | typed measures | n/a | disclosure obligation | analysis algos | evidence blocks | uncertainty_type vocab | positive proof: disclosure must exist + link to claim |
| spatial decision uncertainty | `spatial_decision/uncertainty.py` | decision uncertainty | n/a | — | decision stack | reports | — | project via ref when present |
| VLM / visual eval | `visual_evaluator.py` + harness visual keys | visual evidence slots | — | visual class only | L5 judge | goal_satisfaction | — | never sole PASS (align EvidenceClass.VISUAL) |
| GIS world-state provenance | `gis_world_state/provenance.py` | world-state events | — | — | mutation | world state | — | optional projector if session exposes |
| skill evidence (Dir 02) | `skills/evidence.py` on #1327 | SkillEvidenceRecord | — | recorder | skill runtime | policy | — | **DO NOT touch**; coordinate via skill_id refs only |

## Authoritative stores we REUSE (adapters only)

1. ArtifactRegistry / ArtifactRecord / ArtifactGraph
2. ProductGraph / ProductLineage / MapProductSpec bindings
3. GoalSatisfaction GoalEvidence (classification vocabulary alignment)
4. ReplayTrace artifact/mutation summaries (refs)
5. Data fabric SnapshotRecord / version_token / fingerprints
6. Provenance fingerprint helpers (identity, not duplication)
7. MapSpec layer style + revision (for carto binding)
8. Uncertainty typed measures (obligation checks)
9. StoryMap compiler input shape (ClaimNarrativeProjection out)

## What we persist (durable-light)

Only:
- Claim identity + typed fields (subject/predicate/value/unit/scopes/method)
- EvidenceNode projection stubs (kind + ref + version + freshness metadata) — **refs, not payloads**
- Typed relation edges (bounded vocabulary)
- Verification verdicts / contradiction records / freshness invalidation marks

## Why not another Artifact Registry

ArtifactRegistry already owns session artifact identity, status, and input lineage.
ProductLineage already answers facet→artifact liveness.
Claim graph answers a different question: **typed analytical conclusions and their proof obligations**, indexed over those refs.
