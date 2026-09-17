# PARALLEL OWNERSHIP — overlap matrix vs open PRs #1335 / #1336

Evidence: `gh pr diff <n> --name-only` captured 2026-09-16 against baseline faa453a8.

## PR #1335 (fail-closed claim/tenant/mission fixes) — 11 files

| File | Overlap with eval-factory-v2 work |
|---|---|
| `app/services/gis_harness/completion/pipeline.py` | none directly; production consumer of evidence |
| `app/services/gis_harness/evidence_claim/census.py` | **HOT** — V2 evidence corpus will call `ClaimStore`/claim factories; do not edit |
| `app/services/gis_harness/evidence_claim/grounding.py` | **HOT** — grounding projection is a V2 assertion surface; do not edit |
| `app/services/gis_harness/evidence_claim/verify.py` | **HOT** — `verify_claim` semantics (unsupported/positive-proof/tenant) may shift; do not edit |
| `app/services/gis_harness/hotpath_convergence/claim_ingest.py` | **HOT** — settlement-time claim ingest; do not edit |
| `app/services/gis_harness/hotpath_convergence/pi_card.py` | HOT (Pi context card) |
| `app/services/gis_harness/hotpath_convergence/session_ctx.py` | HOT (`get_or_create_claim_store` — V2 may import this; treat signature as unstable) |
| `app/services/mission_runtime/service.py` | **HOT** — V2 mission evaluator drives this facade; expect behavior changes (fail-closed) |
| `app/services/mission_runtime/store.py` | **HOT** — store semantics/lease fail-closed; V2 must not fork or patch it |
| `tests/unit/gis_harness/test_evidence_claim_graph_v1.py` | owned by #1335 — V2 must NOT extend this file; write a separate corpus test |
| `tests/unit/gis_harness/test_hotpath_convergence_v1.py` | owned by #1335 — same rule |

**Rule for V2**: consume #1335-touched modules **read-only via imports**; pin expected behavior in V2-owned corpus tests (a behavior change then shows as a corpus diff, which is exactly the regression signal wanted). Never edit those files on this branch.

## PR #1336 (GeoAI promptable platform) — 95 files; relevant subset

| File/dir | Overlap with eval-factory-v2 work |
|---|---|
| `tests/conftest.py` | **SHARED ROOT FILE** — the only genuinely shared touchpoint. V2 must avoid editing root `tests/conftest.py`; put fixtures in a local conftest (`tests/quality/conftest.py` already exists and is not touched by #1336; a new dir-local conftest is safest) |
| `tests/unit/gis_harness/test_capability_graph_v8.py` | adjacent harness unit test — do not edit |
| `tests/unit/modelops/**`, `tests/integration/modelops/**` | disjoint |
| `app/lib/modelops/**`, `app/api/routes/geoai.py`, `app/lib/gis/{algorithms,capabilities}/modelops.py` | disjoint (new GeoAI platform; may later become an evaluation *target*, not a conflict) |
| `CHANGELOG.md` | append-only conflict risk at PR-merge time only — coordinate the changelog entry when landing |
| `.agent-work/geoai-promptable-foundation-platform-11/**` | planning docs only; our `.agent-work/eval-factory-v2/` is separate |
| `.env.example`, `.coverage.root.*` junk files | disjoint |

**#1336 does not touch `app/evaluation/**`, `tests/quality/**`, or `tests/harness_replay/**`.**

## Ownership matrix

| Area | Owner at 2026-09-16 | V2 (this branch) |
|---|---|---|
| `app/evaluation/**` | **free** (no open PR touches it) | primary write zone |
| `tests/quality/**` | **free** | primary write zone |
| `tests/harness_replay/**` | **free** | primary write zone |
| `scripts/replay_bench.py`, `scripts/ads_gen_*` | free | safe to extend |
| `app/services/mission_runtime/**` | #1335 | read-only import |
| `app/services/gis_harness/evidence_claim/**` | #1335 | read-only import |
| `app/services/gis_harness/hotpath_convergence/**` | #1335 | read-only import |
| `app/services/gis_harness/skills/policy.py` (+skills/**) | free (landed #1327; no open PR) | safe to import; prefer not to edit (newly landed, review-sensitive) |
| `app/services/gis_harness/completion/pipeline.py` | #1335 | read-only |
| `app/lib/harness/cartography_feedback.py`, `template_codegen_evaluator.py` | free (landed #1321) | import-only; extension via new modules |
| `tests/unit/gis_harness/test_evidence_claim_graph_v1.py`, `test_hotpath_convergence_v1.py` | #1335 | do not touch |
| `tests/unit/gis_harness/test_capability_graph_v8.py` | #1336 | do not touch |
| `tests/conftest.py` (root) | #1336 | do not touch |
| `app/lib/modelops/**` | #1336 | do not touch |
| `pytest.ini` | free but global | avoid unless additive marker needed (a new marker line is low-risk) |

## Safe zones (V2 can create/modify freely)

1. New corpus modules under `app/evaluation/` (e.g. `mission_corpus.py`, `skill_policy_corpus.py`, `evidence_corpus.py`, `security_corpus.py`, `cartography_axes_corpus.py`, `index.py`).
2. Additive edits to `app/evaluation/case.py` (optional fields / Literal extension — established additive pattern), `runner.py` (new opt-in tier), `report.py` (new aggregation functions).
3. New test files: `tests/quality/test_<x>_corpus.py`, `tests/harness_replay/test_<x>_*.py`, `tests/unit/gis_harness/test_<x>_v2.py` — all free dirs.
4. `.agent-work/eval-factory-v2/**` planning docs (this directory).
5. `scripts/` new CLI wrappers (new files preferred over editing existing).
