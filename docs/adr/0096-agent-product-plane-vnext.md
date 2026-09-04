# ADR-0096: Agent/Product Plane VNext boundary

status: Accepted
date: 2026-09-04
supersedes: — (extends ADR-0079/0080/0088/0090/0092 runtime line)

## Context

The workbench VNext effort spans many subsystems at once, and a parallel
development stream may be evolving the GeoCompute/Data Plane (Data Fabric).
Work delegated across agents needs one written boundary: which concepts this
effort owns, which it must only consume through stable public contracts, and
which truths must stay single (no second planner, no second map state, no
second workflow engine).

## Decision

This effort owns the **Agent/Product Plane**:

```
Pi (agent host, unforked) · GIS Harness · semantic GIS reasoning
Analysis Graph projection · SessionPlan integration
Map Product lifecycle · cartographic composition · completion/QA
human-agent interaction · frontend workbench · GIS behavior evaluation
```

It **consumes** (never reaches into private internals of) the Data Plane
contracts: `DatasetDescriptor`, `DatasetVersion`, `QuerySpec`, `ExecutionRequest`,
`ExecutionStatus`, `ArtifactRef`, `QueryEvidence`, `RuntimeManifest`. A shared
schema change, if unavoidable, must be additive, isolated in one commit,
documented, migration-safe, and covered by compatibility tests.

Single sources of truth (unchanged):

| Truth | Owner |
|---|---|
| Agent host | Pi (vendored, unforked) |
| Session planning | SessionPlan envelope (`app/services/session_plan.py`) |
| Desired map | MapSpec (+ lifecycle engine mutations) |
| Tool execution chokepoint | ToolRegistry / ToolDispatchService |
| Artifacts / lineage | artifact_registry + artifact/lineage services |
| Workflow execution | WorkflowEngine + WorkflowRun records |
| Map Product versions | `map_products` ledger (append-only evidence) |

Everything new built in VNext — the explicit Analysis Graph, product verdicts,
decision workspace state — is a **derived projection or an additive schema**,
never a persisted second truth. This is the same invariant ADR-0076/0085/0088
established for PlanGraph/ProductGraph; VNext extends it to every new surface.

## Consequences

- Analysis Graph (ADR-0097) is API/UI projection of SessionPlan chapter +
  MapSpec + artifacts + observation; zero new persisted state.
- Map Product lifecycle ops (ADR-0099) append to the ledger; restore/fork are
  new *rows* with lineage metadata, never mutations of history.
- Semantic V2 (ADR-0098) adds intent vocabulary + recipes + capabilities
  additively; existing tasks keep their resolution (benchmark regression).
- Pi runtime changes (ADR-0100) stay inside `app/agent_pi_bridge.py` +
  extension + cancellation seams; Pi vendor is never forked.
- Frontend remains a projection + interaction originator; MapSpec stays the
  only desired-map authority.
