# REVIEW — G21 (independent review → fix → rereview)

## Reviewer
Independent reviewer subagent (no authorship of this branch's implementation), briefed with base SHA `cc2dc6900`, the full diff `cc2dc6900...HEAD`, the user scenarios, and the author's test evidence. Verdict: **NON-BLOCKING** — no P0/P1.

## Findings and disposition

| Sev | Location | Finding | Disposition |
|---|---|---|---|
| P2 | `app/services/flow_product/service.py` inspect sample | Sample echoed full row values into agent context (rows/keys bounded, values not) | **Fixed** (commit 5dfe567f9): string values truncated at 200 chars. |
| P2 | `app/services/flow_product/builder.py` net_available | Self-loop-only net build yields empty product instead of explicit error | **Not changed — conflicts with the locked contract**: `tests/unit/test_flow_product.py::test_self_loop_gross_once_nodes_in_out_once` asserts a self-loop-only net build succeeds with an empty edge list (self-loop nets to zero; node still renders). The explicit error remains for links with no computable pair. Attempting the reviewer's suggestion broke that test; reverted and documented. |
| P2 | `app/services/mapspec_to_svg.py` merge | G34/G40 explanatory comments dropped during the master merge (code intact) | **Fixed**: comments restored. |
| P2 | `app/services/flow_product/builder.py` link_stats | Recomputed ~3× per link | **Fixed**: computed once per link and reused. |

## Verified correct by the reviewer (sample)
- Direction semantics traced end-to-end (A→B=8/B→A=3): directed 8/3; gross 11; net magnitude 5 oriented A→B; unobserved reverse never fabricated as zero; equal reverse ⇒ zero net without arrows; net refused when no computable pair.
- Scope honesty: `gross.retained` = covered gross ÷ same-scope population gross (not net/gross); node totals from raw declared-scope directed vectors.
- Identity: sha256(scope+kind+typed id+dimensions) render ids; coordinate conflicts raise; permutation/scale invariance.
- Production wiring: tool registered; routes session-owned; apply atomic (family replace + source-revision guard + CAS); preview never touches revisions; no second agent loop / registry / MapSpec store.
- Frontend: click query includes flow layers; node-first ordering; stable object id; hover does not write MapSpec.
- Merge quality: linear 1.9→1.10→2.0→2.1 chain; registry body count is exact equality (20); parity golden adds a CAS-rejection case (strengthening); dead-code gate registers all 7 flow codes.
- Resource discipline: caps enforced; O(N log N); no O(nodes²).
- Test-claim verification: full `tests/cartography` 3677 passed / 5 failed reproduced exactly; all 5 failures re-verified failing on clean origin/master.

## Rereview status
Fixes landed in commit 5dfe567f9; OD suite 83 passed, E2E-flagged 20 passed (1 skipped — vendor RPC not built in this environment, as in the reviewer's run), frontend flow suites 74 passed, tsc clean, ruff clean. No blocking findings remain open.
