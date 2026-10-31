# EVIDENCE — 运行证据（只记真实执行结果）

## 2026-10-29

- 网络：git fetch / gh graphql / curl REST / WebFetch 全部 TLS EOF（github + pypi 均断，本机网络故障）。后台监控 `/tmp/net-retry.log`。
- 最后成功 fetch：origin/master = `4b3629c90ee977e287f7b1fb4fd827b0fad7428c`（= 任务锚点）。
- `git branch -r --no-merged origin/master` → 空（锚点时点无在途分支）。
- worktree 建立：`git worktree add -b zcode/gis-data-onboarding-semantic-vnext ../webgis-worktrees/gis-data-onboarding origin/master` → OK。
- 测试环境：主检出 `.venv`（3.13.17）跑 `pytest tests/unit/dataset_semantics/test_preflight_v1.py -q --no-cov` → **15 passed in 7.96s**。
- 勘察：3 subagents（后端链路 61 tools / 前端+工具链 68 tools / 测试基建 65 tools），结论归档 BASELINE.md。

## 2026-10-29 23:34+（网络恢复后）

- 网络恢复（/tmp/net-retry.log: NET_RECOVERED 23:34:29）。
- `git fetch origin master` → 仍 4b3629c90（未前移，无需 rebase）。
- `gh pr list --state open` → **(none)**；`gh issue list` → 86 open（#1842–#1866
  为 2026-10 大审查系既有发现；本任务未引入同类跨租户面，裁决见
  PR_OVERLAP_MATRIX.md）。
- R1 独立评审（subagent，未参与实现）：FIX-FIRST —— 3 P1 + 4 P2 + 5 P3，
  全部于 commit ada8e26f3 清偿；关键复现（幂等碰撞/auto 谎报/状态回退/
  并发双收据）均有清偿后回归锁定。
- 清偿后全量回归：后端 227 passed（14 文件面）；前端 sidebar 270 passed
  （45 文件）+ tsc 0 错；ruff/import-boundaries/scope-matrix 绿。
- R2 复审：**APPROVE**（12/12 CLEARED、4 复现不可复现；新发现 6 项全 P3，
  R2-1/3/5 当场修复、R2-2/4/6 记 RISKS 残差）。
- push：zcode/gis-data-onboarding-semantic-vnext → origin（新分支）。
- **PR #1868 已创建**：https://github.com/WindWang2/webgis-ai-agent/pull/1868
  （未合并、未等线上 CI；PR body 存档 docs/dev/sdo-pr-body.md）。
