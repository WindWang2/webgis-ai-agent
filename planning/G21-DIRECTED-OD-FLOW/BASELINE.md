# BASELINE — G21-DIRECTED-OD-FLOW

## Repository identity
- Remote: `https://github.com/WindWang2/webgis-ai-agent.git`
- GitHub `nameWithOwner`: `WindWang2/webgis-ai-agent`
- Default/base branch: `master`
- Latest fetched `origin/master`: `816705218a5aa6202bf15d745cb31cddb3437f44`
- Research snapshot SHA matched current remote at initial fetch: yes.
- Branch: `zcode/goal-g21-directed-od-flow-20261110T132034Z`
- Worktree: `/home/kevin/projects/webgis/webgis-worktrees/goal-g21-directed-od-flow-20261110T132034Z`
- Shared git common dir: `/home/kevin/projects/webgis/.bare`
- Heavy-stage lock: `/home/kevin/projects/webgis/.bare/webgis-heavy-stage.lock`

## Initial main-workspace state (left untouched)
- Branch `master`, HEAD `f4a6e86abd21208b148eb08c1c1797ab8604440b` before fetch.
- Modified: `vendor/pi` (pre-existing user change).
- Numerous pre-existing untracked `.claude/skills/*` entries and `_tmp_47_f754af1b0a09e630d4cf829599d2868a`.
- Many existing worktrees; a new unique G21 worktree/branch was created without reusing them.

## Initial remote state
- Open feature PRs observed: #2021 G11, #2022 G10, #2032 UI redesign.
- Other open PRs observed: dependabot #1979 and #2024–#2031.
- Recently merged relevant PRs: #2033 command manifest, #2034 Studio single source, #2035 Agent undo, #2036 Pi-only.
- No open G12–G20 implementation PR was present in the initial list; they remain owned-by-other and are not implemented here.

## Source/test verification ledger
Pending. Every snapshot assertion must be replaced or confirmed against this worktree with file:line and executed-test evidence.

## Pi baseline
Pending verification of `.gitmodules`, gitlink, tag/release and local RPC schemas. G21 will not change the Pi pin or bridge.

## Resource discipline
- `CMAKE_BUILD_PARALLEL_LEVEL=2`, `MAX_JOBS=2`; numeric tests with one BLAS/OpenMP thread.
- At most one heavy stage for this task, under the shared `flock`; no uncontrolled xdist or `-j$(nproc)`.
- Unique ports/temp dirs/database namespaces will be registered here before services start.
