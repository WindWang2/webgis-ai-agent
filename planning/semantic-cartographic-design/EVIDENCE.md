# EVIDENCE — 运行证据（随里程碑追加）

## M0（2026-10-30）
- master 基线：4b3629c90（git rev-parse origin/master 实测）
- worktree 建立：git worktree add -b zcode/cartography-design-choices-vnext ../webgis-worktrees/gis-carto-design origin/master ✅
- 新 worktree pytest 冒烟：tests/cartography/test_plan_ir_v1.py → 9 passed in 6.91s ✅
- 开放 PR 审计：仅 #1868，文件面零重叠（gh pr view --json files 实测）✅
