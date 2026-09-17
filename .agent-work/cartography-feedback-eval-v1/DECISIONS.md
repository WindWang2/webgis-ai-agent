# DECISIONS — Cartography Harness Feedback Evaluation v1

**Branch:** `harness/cartography-feedback-eval-v1`
**Baseline:** `origin/master` @ `c8c7a902` (seed tip `c78b4cdb`)
**Date:** 2026-09-15 (Asia/Shanghai)

## D1 — Extend existing seams; do not invent a second harness

**Decision:** Implement unified feedback inside `app/lib/harness/` + `verdict_summary.py`, wired from `PiAgentHarness.evaluate_with_evidence` after `attach_visual_judgement`.

**Rejected:** A parallel agent host / second evaluator loop / Mission Runtime overlap (`harness/durable-gis-mission-runtime-v1`).

**Why:** GOAL and Pi research docs (`pi-harness-verdict-injection.md`, `pi-harness-eval-honesty.md`) require one inject path (`[CARTOGRAPHY_VERDICT]`) and one session review blob.

## D2 — Three scored axes

| Axis | Source | Honest empty |
|---|---|---|
| `visual` | `visual_evidence` (`source=visual_judge`) | `not_evaluated`, `score=null` |
| `template_codegen` | new `template_codegen_evaluator` | missing MapSpec / incomplete sub-checks → `not_evaluated` |
| `gis_semantics` | `desired_status` / desired_review checks | `not_evaluated` when desired not run |

**Overall:** any axis `fail` → overall `fail`; all `pass` → `pass`; otherwise `not_evaluated` (incomplete evidence ≠ pass).

## D3 — Template/codegen sub-checks

1. **schema_validity** — authoritative `parse_mapspec`
2. **compile_readiness** — lifecycle `is_compiled` only when present; never invent `True`
3. **composition_fitness** — `validate_component_composition` + optional composition template id
4. **component_reuse** — `templateId` against `ComponentTemplateRegistry`; unknown id → fail; all-bare → `not_evaluated`

## D4 — Verdict injection always discloses scores when feedback exists

Even when the three-state token is `pass`, project a bounded `feedback.scores` / `feedback.axes` block so the agent sees **visual + template** scores (GOAL deliverable 3). Fail detail checks remain non-pass-only per ADR-0062 / #657.

## D5 — Record-only check rows

`TEMPLATE_CODEGEN_FITNESS` and `CARTOGRAPHY_FEEDBACK_AXES` append to `cartography.checks` for status-tool visibility. They do **not** by themselves rewrite L4 three-state (same discipline as visual judge record-only).

## D6 — Avoid Mission Runtime overlap

No edits under durable GIS mission runtime paths; feedback stays on cartography review + Pi verdict injection only.
