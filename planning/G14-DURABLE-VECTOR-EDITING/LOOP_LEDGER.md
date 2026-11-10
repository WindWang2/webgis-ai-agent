# LOOP_LEDGER — G14 iteration log

## Round 1 (2026-11-10)
- Read master state, PRs, feature_edit stack, sketch editor, FE/BE integration surfaces (2 Explore agents).
- Wrote BASELINE / DEDUP_MATRIX / PLAN / INTERFACES / TEST_MATRIX.
- Next: backend core (WP03/04/05/07): contracts → wire_types → journal → geometry_ops → plan → executor + tests.

## Decisions log
- D1–D8 frozen in PLAN.md.
- Created-feature WFIDs derive from plan_id (deterministic replay/redo) — see PLAN D1.
- Topology declared via envelope key `topology` (survives overwrite; set at provision time) — PLAN D4.
