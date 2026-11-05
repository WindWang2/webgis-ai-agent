# PR — GIS 能力图、真实空间算法与跨工具分析编排 vNext（ADR-0282）

## 范围

在 goal-02 AnalysisWorkflowContract v1（#1869）与 SpatialSemanticContract（#1876/ADR-0279）之上，为自然语言空间分析补齐科学可信的多步执行闭环（Track B 所有权：`app/tools/`、`app/lib/gis/`、`gis_harness/analysis_workflows/`、`geocompute` 相邻 lib、science_oracles/fixtures）：

1. **Typed 分析产物封套 v2**：阶段产物携带实测事实（geometry_types/extent/result_semantics/raster{nodata,valid_pixel_coverage}/aggregation_basis/owner），有界采集（≤2000 特征），缺省=未知绝不虚填；typed 衔接门（semantic_contract 执行期核验 + qualifier 画像 plan 期裁决，新码 AWF_INPUT_SEMANTIC_MISMATCH/AWF_ROLE_GEOMETRY_MISMATCH/AWF_ROLE_KIND_MISMATCH）。
2. **三条混合方法链端到端**（local fixture + 真实 dispatch + MapSpec-ready ref + lineage + 方法学确定性门控）：
   - 链① 行政统计强化：per-capita 分母字段从 role:denominator qualifier.field 解析（缺列 plan 期 typed skipped，非运行期 failed）；grid_rate 改显式分母真率（人口/设施负荷，不再是改名计数）。
   - 链② `raster_zonal_summary` 新族：raster 角色 ref 直通（不水合）+ zonal 逐 zone valid_pixel_coverage 披露 + optional resample 剔除后确定性回接（修复栅格链不入编排的类型断点）。
   - 链③ `network_proximity` 新族：真 Dijkstra 最近设施→区均网络成本（Σtotal_cost/count 均值语义，库层反伪装率标签）；service_accessibility 族默认剔除脱离路网设施的 5m 占位面（计数进 stats）。
3. **语义守门与已知答案 oracle**：±inf 分子剔除（修复 inf 毒化和的真缺陷）；边界双计守恒/零负分母 rate=None/bowtie strict 拒绝+修复披露/两组件断网诚实 unmatched/跨 180 线性行为锚（count=0 精确钉扎）。
4. **局部重算与状态闭环**：FINGERPRINT_SCHEMA=2（语义合同进指纹，旧态一次性诚实失效）；stale 态获得赋值路径（recompute 指纹漂移步骤）；science_validation（G02 终局门投影）进状态机并经 status 工具透出；**产品最终门**（partial ≠ final：webgis_map_product 拒绝非 satisfied/science-blocked 工作流 ref 作为成品层；`GIS_AWF_PRODUCT_GATE=0` bit 级回退）。
5. **B4 可解释执行表面**：plan 附输入预算估计（超限 typed 指引分块/降采样）+ blocked_next_steps（封闭 action 词表）；`awf.discovery.v1` 机读族目录（前置/单位/可制图性/资源画像）。
6. **B5 五类 NL 黄金任务闭环**：F29 场景族（高风险区设施统计/栅格指数行政区汇总/最近医院网络距离——前两类此前完全无任务级场景）+ 2 个 perf workload（envelope 有界扫描契约、plan 热路径）。

## 与已有 PR 区别

- 唯一 open PR #1965（scene3d/terrain/observation）零文件交集；本 PR 不含其改动。
- 相对 #1869（goal-02 底座）：不重做五族/执行器/receipt——全部 additive 扩展（envelope 新字段/两新族/新码/新模块 envelope_facts·semantic_gate·product_gate·discovery）。
- 相对 Review-R2 issues（#1909 S96 network 等）：不抢修任何 issue 指认的行为；网络拓扑正确性依赖上游（族 risk_notes 披露）。

## 实现与验证证据

| 命令 | 结果 |
|---|---|
| `pytest tests/unit/gis_harness/analysis_workflows -q --no-cov` | **141 passed, 1 skipped**（基线 83P/1S + 新增 59 例） |
| `pytest tests/unit/gis_harness -q --no-cov -m "not heavy"` | **3242 passed, 4 skipped** |
| `pytest tests/acceptance/gis_agent -m cartography -q --no-cov` | **45 passed**（72 场景含 F29×3；pin 再生） |
| `python scripts/g05_task_acceptance.py run --scenario f29-0{1,2,3}-*` | 三场景 OK（pass=4/3/3） |
| `pytest tests/benchmarks/test_perf_harness.py -m perf -q` | **17 passed**（含 2 新 workload；既有 15 项基线原样保留） |
| `ruff check app/ tests/` | All checks passed |
| 亲证复现 | F29-02 开发中发现并修复真缺陷：栅格工件相对路径被 validate_data_path 二次拼接 data_dir |

## 独立 review

R1 对抗性评审（只读 subagent，亲跑测试）：**0 P0 / 0 P1 / 8 P2 / 10 P3 → FIX-FIRST**。
R1 清偿（commit 2e4ef860b）：F-2 分母字段劫持（科学缺陷，评审实测复现）+ F-1 trim 桩几何（P2 代码修复）；F-3/F-4/F-7/F-8 补齐缺失测试；F-5 ADR/IC 对齐 + semantic_contract 接线 6 步；F-6 status 透出；P3×10 全清（含 baselines 阈值被动放宽回退）。
R2 复核结论：**APPROVE**（2026-11-06）。8×P2 与 P3 批量全部 CLEARED（评审者独立重放其复现路径 + 实测族路径执行期门激活：polygon subject 在 run 期被 AWF_INPUT_SEMANTIC_MISMATCH 拦截）；无新增 P0/P1/P2；残留 3 项 P3（披露打磨）已在最终 commit 清偿（trim 桩丢弃计数进 envelope 警告 / 文档例数 / ADR D6 措辞对齐交付物）。评审者亲跑：analysis_workflows 141P/1S、cartography lane 45P、邻接 107P。

## 未运行项（如实披露）

- 远端 CI/部署（仓库约定不等待 CI）。
- real_services lane（需 REAL_SERVICES=1 + PostGIS/Redis；本机 Redis 16379 未运行，内存实现语义已覆盖）。
- 前端 vitest/tsc（零前端源码改动）。

## 已知边界与技术债

- `layer_ids` 引入的已挂载图层不在产品门管辖（门覆盖显式 ref 参数面——ADR-0282 D4 披露）。
- 跨 180° 环绕聚合（RFC 7946 §3.1.9 拆分约定）未实现——行为锚钉扎当前线性行为。
- zonal 掩膜=逐 zone 窗口读隐式完成（无独立 mask 步骤，族披露）。
- network 拓扑交叉口打断依赖上游（#1909 归 Review-R2 线）。

## 回退开关与影响面

- `GIS_AWF_PRODUCT_GATE=0` 回退产品门（AST 契约测试锁定守卫）。
- `exclude_unreachable` 工具默认 False=master 行为（族模板默认 True 是新面）。
- `FINGERPRINT_SCHEMA=2` 唯一既有行为变化：旧持久态一次性重算（ADR D5 披露）。
- 其余全部 additive：envelope/step/binding 新字段默认值；7 工具面新增参数默认保持旧行为。
