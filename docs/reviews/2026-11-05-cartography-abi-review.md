# Cartography ABI issue verification — 2026-11-05

Baseline e0e97b462, same isolated all-issues worktree. Every finding independently inspected; no aggregate blanket closure.

| Issue | Finding | Status | Evidence |
|---|---|---|---|
| #1966 | S14-001 | fixed | Reproduced13 invalid native seed defaults. ABI enums derive seed choices; opacity numeric; topN400; ABI selfcheck validates native defaults. |
| #1966 | S14-002 | fixed | Declared schema keys always validated; >32 props rejected. Forty padding keys cannot hide invalid variant. |
| #1966 | S14-003 | fixed | Rehome and overflow enforce exclusive-zone capacity; regression retains title center. |
| #1966 | S14-004 | fixed | Jenks sampled interior breaks retain full minimum/maximum. Updated old sample-only endpoint test expectations. |
| #1966 | S14-005 | fixed | Read-only design_candidates excluded from export parity; export_layout exempt only for interactive target. |
| #1967 | S14-006 | fixed | Classifier strips nonfinite observations; entirely invalid sample returns empty. |
| #1967 | S14-007 | fixed | ABI numeric props explicitly reject NaN/infinity before range checks. |
| #1967 | S14-008 | already_fixed | Current validate_props checks dict before required key traversal. Existing required regression passes. |
| #1967 | S14-009 | fixed | Category membership uses exact type plus equality; materialized property and legend keys both strings. |
| #1967 | S14-010 | fixed | Single-character candidates match only exact queries/tokens, avoiding 北 in 北京人口密度. |
| #1967 | S14-011 | fixed | Template validation exceptions produce explicit issue, matching fail-closed registry semantics. |
| #1967 | S14-012 | fixed | Table/disclosure taxonomy output targets match interactive/png/pdf/svg matrix; generated docs wording updated. |
| #1967 | S14-013 | fixed | Priority normalization handles nonnumeric/nonfinite metadata with deterministic0 fallback. |
| #1967 | S14-014 | fixed | elevation_profile descriptor binding follows profileRef/points; layer binding not required. |

Validation:94 focused Python tests passed (ABI/category/conformance/catalog/diagnostic/SVG filter);11 new S14 regressions passed;3 new SVG label/coordinate/expression tests passed. Catalog regenerated from authoritative backend registry. Broader final tests remain parent integration responsibility.
