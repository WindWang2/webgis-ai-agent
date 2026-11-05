# EVIDENCE — 验证记录（gis-analytical-foundation-vnext）

环境：worktree `/home/kevin/projects/webgis/webgis-wt-goal-gis-analytical-vnext` @ 基线 b6ac970eb；
venv `/home/kevin/projects/webgis/webgis-ai-agent/.venv`（py3.13.17）；本机多 agent 并存 → 全程串行测试。

## E0 M0（R0 事实检查）
- master=b6ac970eb 无漂移（PR 创建前 2026-11-06 02:5x 复核仍一致）；open PR 仅 #1965，与本任务所有权面零文件交集。
- 基线：`pytest tests/unit/gis_harness/analysis_workflows -q --no-cov` → **83 passed, 1 skipped**（26s）。

## E1 M3 envelope v2（commit a22dd6f14）
- `test_envelope_v2.py` 19 例：v2 字段缺省=v1 round-trip 兼容；有界扫描（2000 特征/20000 坐标界，第 2001 个点不进 extent——确定性钉扎）；口径采集自 ADR-0279 证据块；几何门 Multi* 折叠/network→line 归一；plan 期画像门 typed blocked（AWF_ROLE_GEOMETRY_MISMATCH）；执行期合同门（AWF_INPUT_SEMANTIC_MISMATCH）；repeated 同指纹孪生步骤复用。
- 套件 102P/1S；邻接 spatial_semantics+workflow_guards 108P；ruff 绿。

## E2 M4-M5 链①②（commit b6141938d）
- 分母字段：qualifier.field 注入（E2E pop_2020 端到端 satisfied）；fields 清单缺列 → plan 期 typed skipped AWF_DENOMINATOR_MISSING（不再运行期 failed）。
- 链②：合成 2×2 栅格已知答案（nw: mean=1.0/coverage=0.5；ne: mean=2.0/coverage=1.0——nodata 区与全有效区产物可区分）；raster role_kind 直通（vector 误绑 typed blocked）；optional resample 剔除后 zonal 回接 role:subject。
- 套件 147P/1S（含 skill library 36 例——六族过程包全词表校验）。

## E3 M6 链③（commit d21240489）
- network_proximity E2E：真 Dijkstra 路线 → 区均网络成本（numerator=Σtotal_cost/denominator=count 全在产物可回读）。
- exclude_unreachable：同形状结果验证过滤面（剔除+计数+警告；默认 False 保 master 行为对照）；族默认 True。
- 预算估计：descriptor 画像 12 万行 → runnable=False + AWF_BUDGET_EXCEEDED + 分块指引（run 期硬门不变）。
- grid_rate 真率：denominator_kind=field/count（人口/设施负荷——不再是改名计数）。
- gis_harness 全量 **3218P/4S**（203s）。

## E4 M7 状态闭环（commit 5fccf8eb2）
- science_validation：satisfied run 终态写入状态机并 round-trip 保真。
- stale：collect_stale_steps 指纹漂移（同 ref 原地 overwrite）→ recompute 落 state=stale。
- product_gate：partial/blocked run 的 envelope ref 不合格；satisfied/非工作流 ref 合格；去重/空 ref。
- 已知答案 oracle：边界双计 3+3；零/负分母 rate=None；NaN+inf 分子剔除（**发现并修复真缺陷：inf 此前毒化和**）；bowtie strict 拒绝/修复披露（平坦栅格修复后 mean=5.0）；两组件路网最近设施 unmatched 显性（routes=0，无欧氏替代）；跨 180 线性行为锚。
- gis_harness **3230P/4S**；G05 45 绿；ruff 全绿。

## E5 M8 场景 + perf（commit 45dc86bc1）
- F29 三场景（G05 CLI 实跑）：f29-01 OK(pass=4)；f29-02 OK(pass=3)——**过程中发现并修复真缺陷**（栅格工件相对路径被 validate_data_path 二次拼接 data_dir → executor 层规范化为绝对路径）；f29-03 OK(pass=3)。
- pin 再生 72 场景；`pytest tests/acceptance/gis_agent -q` → **45 passed**（全量 lane，含既有 69 场景零回归）。
- perf：`PERF_UPDATE_BASELINES=1 pytest tests/benchmarks/test_perf_harness.py -m perf`（17 skipped=录制）→ 复跑 **17 passed**。新 workload：envelope_facts_bounded_50k（50k 特征采集=有界扫描）、awf_plan_typed_gates（plan 热路径毫秒级）。
- gis_harness **3233P/4S**（205s）；analysis_workflows **132P/1S**；ruff `app/ tests/` 全绿。

## 未运行项（如实披露）
- 远端 CI/部署管线（任务书 §二.9 明确不等待）。
- real_services lane（需 REAL_SERVICES=1 + PostGIS/Redis——本机 Redis 16379 未运行，内存实现语义已全覆盖）。
- 前端 vitest/tsc（本任务零前端源码改动；analysisWorkflowCommands.ts 未动——catalogue 不变量不受影响）。
