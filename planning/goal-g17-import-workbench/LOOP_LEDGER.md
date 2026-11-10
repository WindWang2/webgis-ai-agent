# LOOP_LEDGER.md — 迭代账本

## 轮次记录

### R0（2026-11-10）侦察 + 建树
- 基线 SHA 816705218；worktree/branch 建立；3 份侦察报告（后端链/工具面/前端面）。
- DEDUP_MATRIX：G17 核心 missing；开放 PR 无冲突；#2032 仅薄挂载点。
- 决策 D1-D8 落 PLAN.md。
- 下一步：WP02 schemas/models。

## 环境事实
- 测试解释器：`python`（conda 3.13.9，依赖齐全）。pytest 需在 worktree 根执行。
- 重型阶段资源纪律：默认串行（本任务无编译需求；pytest -x -q 定向子集）。

### R1（2026-11-10）后端纵向切片打通
- 新增域：app/services/data_import/{models,budgets,staging,discovery,interpretation,preview,commit,service}.py
- 接线：main.py 路由 + tools/_TOOL_MODULES + data_parser 公共守卫别名 + requirements(.lock) openpyxl
- API：app/api/routes/data_import.py（9 端点）；工具：app/tools/data_import_tools.py（5 工具，未测）
- 测试：tests/data_import/test_vertical_slice.py 3 passed（124s）
  - 覆盖场景 2（双 GeoJSON）、幂等（场景 14 机制）、不同解释→不同产物（场景 13 机制）、归属 NOT_FOUND、unsupported 诚实披露
- 修复记录：_load_owned async 化、shapely 2.x __geo_interface__、member container_ref 显式定位
- 下一步：容器 fixture 矩阵（GPKG 三层/ZIP 双套/CSV 变体/XLSX）+ 取消/恢复 + 工具经 dispatch 测试 + UI
