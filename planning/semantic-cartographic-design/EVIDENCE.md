# EVIDENCE — 运行证据（随里程碑追加）

## M0（2026-10-30）
- master 基线：4b3629c90（git rev-parse origin/master 实测）
- worktree 建立：git worktree add -b zcode/cartography-design-choices-vnext ../webgis-worktrees/gis-carto-design origin/master ✅
- 新 worktree pytest 冒烟：tests/cartography/test_plan_ir_v1.py → 9 passed in 6.91s ✅
- 开放 PR 审计：仅 #1868，文件面零重叠（gh pr view --json files 实测）✅

## M1–M8（2026-10-30）
- M1 引擎：tests/cartography/test_design_candidates_v1.py 19 passed（含 R1 清偿回归 3 例）
- M2 接线：tests/unit/test_map_product_design_trace_v1.py 4 passed（真实 registry.dispatch；best_effort 降级不阻断；面板落 layout 断言）
- M3 选择：tests/unit/test_map_design_select_v1.py 6 passed（pin 往返/幂等/typed 拒绝/IR 桥回归）
- M4 检查：test_semantic_checks_design_v1.py 11 passed + 契约 8 passed（v3 48 码）；mutation parity golden 经 ZCODE_PARITY_GENERATE=1 重建
- M5 五族：test_design_families_e2e_v1.py 6 passed（含 R1 密度披露回归）
- M6 G05：FD 族 3 场景 OK；全 lane 45 passed；pin 64 场景（CLI --force）
- M7 前端：map-components 134 passed；catalog/TS 投影/catalog docs/golden×2 全部经生成器同步
- M8 R1 评审清偿后全量：tests/cartography/ 2961 passed + G05 45 + 前端 134 + ruff 全绿
