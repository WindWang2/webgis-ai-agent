# ADR-0097: Explicit Analysis Graph as a derived projection

status: Accepted
date: 2026-09-04
relates-to: ADR-0076 (SessionPlan truth), ADR-0085 (goal→product graph),
ADR-0087/0088 (action intent), ADR-0096 (plane boundary)

## Context

The runtime already derives PlanGraph (capability DAG), ProductGraph (goal→
facets), facet completion, and a unified next-action — but they exist only as
in-memory projections and ≤10-line text blocks inside the turn prompt. The
user and the frontend cannot inspect "what analysis is this session running,
what is blocked, what does the agent owe me next". The workbench requires a
first-class, serializable graph — without creating a second planning truth.

## Decision

`app/services/gis_harness/analysis_graph.py` builds an **explicit Analysis
Graph** as a pure derived projection (zero persistence, zero new truth):

```
GET /api/v1/sessions/{session_id}/analysis-graph
→ { goal, nodes[goal|requirement|analysis|product], counts, next_action, notes }
```

- **goal node** — SessionPlan user_goal + recipe/plan ids + methodology
  warnings (with stable codes from ADR-0098) + superseded/replaced flags.
- **execution nodes** — PlanGraph capability DAG: purpose, resolved
  algorithm/tool, depends_on (artifact-type inference), status incl.
  blocked_by/fallback_to, bound refs (cursors, never payloads),
  `recompute_impact: downstream` (topological fact).
- **product nodes** — facet completion: facet kind/label/status/required,
  capability/artifact/layer/component bindings, render evidence (only when
  the observation revision matches), and `recompute_dims` — the five-dimension
  diff semantics projected per facet (legend = style-only; statistics =
  data/algorithm/parameter; map_layer = all five).
- **next_action** — the unified GISActionIntent (execution debt → product
  debt → observation debt → finalization debt) with mode/class.

Bounds (§20 performance): execution ≤ 96 nodes, product ≤ 64, warnings ≤ 12;
every payload travels as ref. Loading failures degrade to an honest empty
graph with notes — the graph is an inspection surface, never a gate.

## Rejected alternatives

- **Persisting the graph** (new table/cache as truth): violates ADR-0076/0085;
  row statuses in the SessionPlan chapter remain the only mutable state.
- **A second graph engine with its own scheduler**: the graph never schedules
  execution; Pi + harness + `apply_tool_result` advance the underlying rows.
- **Exposing raw MapProductPlan JSON**: not a graph; no dependency/status/
  evidence semantics; unusable for a workbench inspector.

## Consequences

- The frontend Agent Workspace panel consumes this endpoint directly (M7).
- Determinism is test-enforced (same chapter+spec ⇒ identical graph).
- When facets grow (new families), only the facet contract and the dims map
  need extension — the projection stays a pure function of existing truths.
