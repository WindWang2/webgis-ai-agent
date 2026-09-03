# ADR-0094: Spatial Decision Intelligence V3

**Date:** 2026-09-03
**Status:** Accepted
**Supersedes:** none (evolves Spatial Decision Intelligence V2 into V3)

## Context

Spatial Decision Intelligence V2 introduced initial orchestration (`DecisionEngine`), scenario specifications (`ScenarioSpec`), baseline resolution (`BaselineResolver`), rule packs, and multi-scenario comparison (`ScenarioComparisonEngine`). While functional as an initial prototype, rigorous audit of V2 revealed key methodological debts:

1. **Unscaled Composite Scoring:** V2 summed percentage deltas (`delta_pct`) directly across heterogeneous units (e.g., housing price RMB/m², commute time min, green ratio %) without dimensionless normalization.
2. **Arbitrary Pareto Scoring Bonus:** V2 awarded an arbitrary `+10.0` points for Pareto frontier membership and multiplied scores by a synthetic confidence formula `(0.5 + 0.5 * confidence)`.
3. **Absence of First-Class Constraints:** Infeasible alternatives (violating ecological boundaries, minimum distance regulations, or capital budgets) were not eliminated from consideration and could win if other metrics were high.
4. **Hardcoded Causal Assumptions & Recommendation Strings:** Rigid keyword matching (`"subway"`, `"school"`, etc.) selected hardcoded recommendation templates with static radii (500m, 1500m) rather than deriving recommendations from evidence, rules, and quantitative thresholds.
5. **Lack of Uncertainty & Sensitivity Analysis:** Decision rankings assumed absolute certainty; no rank stability, weight sensitivity, or robustness metrics existed to detect when a recommendation flips under minor preference changes.
6. **Binary Winner Assumption:** Comparison engines always returned a single recommended scenario (`argmax`), failing to represent states such as `NO_CLEAR_WINNER`, `NO_FEASIBLE_ALTERNATIVE`, or `INSUFFICIENT_EVIDENCE`.

Spatial Decision Intelligence V3 addresses these debts by establishing a mathematically rigorous, evidence-grounded spatial multi-criteria decision analysis (MCDA) framework.

## Core Principle: No Fake Decisions

The system must never turn missing evidence into confident numerical recommendations.
All decision variables, baseline data, and scenario outcomes must be classified into one of four mutually exclusive truth states:
- **`OBSERVED`**: Empirically measured facts backed by verifiable spatial datasets, POI layers, or administrative records.
- **`DERIVED`**: Computed from observed baselines via deterministic, documented spatial or statistical models.
- **`ASSUMED`**: Explicitly declared user or policy assumptions, bounded and tagged as non-factual hypotheses.
- **`MISSING`**: Quantities with no evidence or assumption. Missing core quantities fail-closed to `INSUFFICIENT_EVIDENCE` and are never substituted with silent synthetic defaults.

## Decision Problem Domain Model

Spatial Decision V3 formalizes decisions via the `DecisionProblem` value object:

```text
DecisionProblem
  ├── id: str
  ├── goal: str
  ├── target_area: TargetAreaSpec
  ├── alternatives: List[Alternative]
  ├── criteria: List[Criterion]
  ├── constraints: List[Constraint]
  ├── preferences: PreferenceModel (weights & directions)
  ├── baseline: BaselineEvidenceContext
  ├── uncertainties: List[UncertainParameter]
  └── decision_horizon: Optional[str]
```

### 1. Alternatives (`Alternative`)
Represents candidate spatial choices (e.g., candidate hospital sites, transport alignments, school parcels). Each alternative has a stable identifier, spatial geometry (GeoJSON), declared attributes (cost, area, capacity), and associated scenario assumptions.

### 2. Criteria & Unit-Safe Normalization (`Criterion`)
Every evaluation criterion must explicitly declare:
- `id`, `name`, `unit`
- `direction`: `maximize` (benefit), `minimize` (cost), `target` (distance to optimal value), `range` (acceptable interval), or `unknown`.
- `normalization_strategy`:
  - `min_max_benefit`: $(x - x_{\min}) / (x_{\max} - x_{\min})$
  - `min_max_cost`: $(x_{\max} - x) / (x_{\max} - x_{\min})$
  - `target_distance`: $1 - \min(1, |x - x_{\text{target}}| / d_{\max})$
  - `bounded_utility`: piecewise linear / bounded mapping
- Robust handling of edge cases: zero range ($x_{\max} = x_{\min}$), negative values, and outliers.
- Unit safety: Incompatible raw metrics (RMB, min, people, dB) cannot be added or compared without passing through the normalization stage.

### 3. Preferences & Weight Normalization (`CriterionWeight`)
Weights indicate decision priorities:
- Sources: `user_declared`, `policy_defined`, `rule_pack`, `equal_default`.
- Weights are strictly non-negative, finite, and normalized deterministically such that $\sum w_i = 1.0$.
- When no user preference is provided, equal weighting is applied and explicitly labeled: *"Decision ranking assumes equal criterion importance."*

### 4. Constraints (`Constraint`)
Constraints are evaluated prior to recommendation:
- **Hard Constraints**: Any violation sets `feasible = false`. An infeasible alternative is strictly disqualified from being recommended, regardless of its composite score.
- **Soft Constraints**: Violations incur an explicit, transparent penalty function or conditional warning.
- Categories:
  - `numeric`: e.g., $\text{budget} \le 500\text{M}$
  - `spatial`: `within`, `outside` (e.g. protected ecological zones), `intersects`, `min_distance`, `max_distance`, `overlap_ratio`.
  - `categorical` & `logical`.
- **CRS Correctness**: All distance and area constraints must be evaluated using projected or geodesic geometry (`pyproj.Geod` or suitable UTM/local projection). Treating geographic degrees (`EPSG:4326`) as meters is strictly rejected.

### 5. Multi-Criteria Decision Analysis (MCDA) Engine
The engine implements two independent, mathematically proven MCDA methods:
- **Weighted Sum Model (WSM)**: $S_i = \sum_{j=1}^m w_j \cdot r_{ij}$
- **TOPSIS (Technique for Order Preference by Similarity to Ideal Solution)**:
  - Vector normalization: $n_{ij} = x_{ij} / \sqrt{\sum_k x_{kj}^2}$
  - Weighted matrix: $v_{ij} = w_j \cdot n_{ij}$
  - Positive ideal $A^+$ and negative ideal $A^-$ solutions
  - Separation distances $S_i^+$ and $S_i^-$
  - Relative closeness $C_i = S_i^- / (S_i^+ + S_i^-) \in [0, 1]$

### 6. Generalized Pareto Frontier
Pareto non-dominance is evaluated strictly according to multi-criterion dominance:
- Alternative $B$ dominates $A$ iff $B$ is strictly better than $A$ in at least one criterion and no worse in all others.
- Pareto status is independent evidence (`dominated` vs `non_dominated`). No arbitrary hidden score bonuses are added.

### 7. Uncertainty & Monte Carlo Simulation
When parameters exhibit stochastic variation:
- Parameter distributions: `fixed`, `interval`, `triangular`, `normal` (truncated), `empirical`.
- Bounded, reproducible Monte Carlo simulation:
  - Seed-controlled PRNG (`numpy.random.Generator`)
  - Bounded iteration counts (default 1,000; cancel-safe)
  - Evaluates summary percentiles: mean, median, std, p05, p25, p75, p95, and probability of constraint satisfaction.

### 8. Sensitivity & Robustness Analysis
- **Weight Sensitivity & Rank Stability**: Perturbs criterion weights across random or gridded simplex vectors to determine the stability percentage:
  $$\text{RankStability}(A) = \frac{\text{Count}(A \text{ ranks } \#1)}{\text{Total Perturbations}}$$
- **Critical Weight Flips**: Identifies the minimal weight change that alters the top-ranked alternative.
- **Minimax Regret**:
  $$\text{Regret}(A, s) = \max_{A'} U(A', s) - U(A, s), \quad \text{MaxRegret}(A) = \max_s \text{Regret}(A, s)$$
  Identifies the alternative that minimizes maximum regret.

### 9. Recommendation Admissibility & Structured Explanation
Instead of always returning a winner, the recommendation policy evaluates admissibility:
- `RECOMMENDED`: Feasible alternative clearly dominates with rank stability $\ge 70\%$.
- `CONDITIONALLY_RECOMMENDED`: Winner has soft constraint penalties or requires verification of assumptions.
- `NO_CLEAR_WINNER`: Top alternatives flip under small ($\le 15\%$) weight shifts.
- `NO_FEASIBLE_ALTERNATIVE`: All alternatives violate at least one hard constraint.
- `INSUFFICIENT_EVIDENCE`: Required baseline metrics or criteria lack empirical grounding.

Structured explanation objects (`why_selected`, `why_not_selected`, `binding_constraints`, `criterion_contributions`, `major_tradeoffs`, `sensitivity`, `uncertainty`, `evidence_gaps`) generate audit-ready rationale.

### 10. Rule Packs & Evidence Hardening
- Rules define temporal validity (`valid_from`, `valid_until`) and jurisdiction.
- Conflicting rules are detected and reported rather than arbitrarily applied.
- Evidence items track source strength and conflicting claims.
- Every run produces a deterministic `decision_fingerprint` for end-to-end reproducibility.

## Architectural Seams & Compatibility

1. **State Ownership**: Decision intelligence is a domain calculation engine. It does not own session state, MapSpec state, or workflow state.
2. **Backward Compatibility**: Existing V2 tools (`spatial_decision_v2`, `scenario_compare`) continue to function without breaking existing callers.
3. **MapSpec & Report Seams**: Results inject cleanly into existing MapSpec layers (differentiating feasible candidates, infeasible exclusions, constraint buffers, and recommended sites) and generate structured Markdown decision reports.
