# ADR-0098: Semantic product families V2 — first-class decision products

status: Accepted
date: 2026-09-04
relates-to: ADR-0092 (Phase C semantic layer), ADR-0094 (Spatial Decision V3)

## Context

On master, `选址 / 适建 / 风险 / 公平` queries fall through the intent rules to
`fallback_distribution_default` (distribution overview). The analysis-pattern
library *knows* these semantics (patterns `site_selection`, `suitability`,
`risk_exposure`, `spatial_equity` exist) but they were advisory-only: the
planner never mapped them to a product path, and golden cases G27–G29 locked in
the generic fallback as "honest behavior". That was honest about *limits* but
dishonest about *capability*: the system has the capabilities (proximity,
service area, spatial join, zonal stats) and a full MCDA engine
(DecisionEngineV3: WSM/TOPSIS, Pareto, Monte Carlo, sensitivity) that the
planner could never ask for.

## Decision

1. **Four first-class intent tasks** (additive `TaskType` vocabulary):
   `spatial_equity`, `site_selection`, `suitability_assessment`,
   `risk_exposure`. Deterministic Chinese **and English** rules sit before the
   form-semantics rules (aggregation/proximity/accessibility) because decision
   semantics dominate form. Pure statistics queries are unaffected (G20 lock).
   All four join `_HINT_PROTECTED_TASKS` — an LLM hint cannot downgrade an
   evaluation question into a visual overview.

2. **Four dedicated recipes** (`spatial_equity`, `site_selection`,
   `suitability_assessment`, `risk_exposure`) with honest capability plans,
   validation rules (denominator/receptor/weight-provenance), declared
   fallbacks, priority 50 (task hit dominates; the raster-family geometry
   hard-filter still puts generic raster surfaces first for raster hints).

3. **New capability `mcda_evaluation`** + algorithm `decision.mcda.wsm` →
   tool `spatial_decision_v3`. MCDA becomes plannable, not tool-only. The
   recipe composes it with the overlay capabilities that produce criteria
   layers; artifact-type dependency inference wires the edges.

4. **Planning-time obligation disclosures with stable machine-readable
   warning codes** (extend the pattern projection):
   - `EQUITY_MISSING_DENOMINATOR` (role-gated, keyword-gated — unchanged
     trigger semantics from ADR-0092 C4)
   - `SITE_SELECTION_CRITERIA_UNDECLARED`, `SUITABILITY_WEIGHT_PROVENANCE`,
     `RISK_RECEPTORS_UNCONFIRMED` (**task-gated**: they fire whenever the
     first-class task fires, gated on `AnalysisPattern.first_class_task` so
     borrowed task aliases (proximity etc.) never cross-fire)
   Each planner warning now carries `code` + `warning_codes`; the prose may
   evolve, the code contract may not.

5. **Golden cases G27–G29 updated**: they now lock the *first-class* behavior
   (task + recipe + mcda capability + obligation warning + forbidden
   over-analysis), replacing the fallback lock-in.

## Rejected alternatives

- **Keyword-only pattern matching without tasks**: keeps the planner blind;
  recipes can't express decision products; MCDA stays unplannable.
- **A separate "decision planner"**: violates ADR-0076 single-plan-truth; the
  decision engine stays an *executor* behind a capability, not a planner.
- **MCDA as a map model/cartography**: MCDA output is scores + rankings +
  sensitivity, not a new visual encoding; it renders through existing models.

## Consequences

- Benchmark regression holds (33/33 offline, G1–G26/G30–G33 unchanged).
- Equity queries keep the G18 capability contract (poi_query +
  admin_aggregation) — the equity recipe supersedes the fallback recipe
  without losing the aggregation path.
- Frontend/UI rendering of the new warning codes arrives with the evidence
  workspace (workbench milestone); the codes are already machine-consumable.
