# EVIDENCE — 运行证据（只记真实执行结果）

## 2026-10-29

- 网络：git fetch / gh graphql / curl REST / WebFetch 全部 TLS EOF（github + pypi 均断，本机网络故障）。后台监控 `/tmp/net-retry.log`。
- 最后成功 fetch：origin/master = `4b3629c90ee977e287f7b1fb4fd827b0fad7428c`（= 任务锚点）。
- `git branch -r --no-merged origin/master` → 空（锚点时点无在途分支）。
- worktree 建立：`git worktree add -b zcode/gis-data-onboarding-semantic-vnext ../webgis-worktrees/gis-data-onboarding origin/master` → OK。
- 测试环境：主检出 `.venv`（3.13.17）跑 `pytest tests/unit/dataset_semantics/test_preflight_v1.py -q --no-cov` → **15 passed in 7.96s**。
- 勘察：3 subagents（后端链路 61 tools / 前端+工具链 68 tools / 测试基建 65 tools），结论归档 BASELINE.md。

## 待补（网络恢复后）

- [ ] `gh pr list --state open` 二次去重（push 前 + PR 创建前各一次）
- [ ] issues #1842–#1866 通读，与 R1/R2 关联裁决
- [ ] push + gh pr create 实际 URL
