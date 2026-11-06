<!-- 来历声明：本文件由同 goal 的孪生 ZCode 会话于 2026-11-06 01:37 写入（其两只只读勘察
     subagent 的独立事实基线 + 亲读 contracts/families/executor/state/recompute/orchestrator/
     evidence/tools 全文复核）。孪生会话已让渡本 worktree 写入权（见 .goal-loop-ledger.md 尾部
     协调声明）；本文件内容与 SOURCE_INVENTORY.md 互补，属主会话可自由采用/忽略/改写。 -->

# RECON — 02_GIS_ANALYSIS vNext（R0 事实基线）

- 基准：origin/master `b6ac970eb`（2026-11-05 23:10 +0800，PR #1970 merge）
- worktree：`/home/kevin/projects/webgis/webgis-wt-goal-gis-analytical-vnext`
- branch：`zcode/goal-gis-analytical-foundation-vnext-20261105`
- 快照时间：2026-11-06

## 1. 避让矩阵（PR-overlap）

| 对象 | 状态 | 处置 |
|---|---|---|
| PR #1965（open） | scene3d/terrain/cartography-quality/frontend map-panel/terrain_ingest/upload routes | 全部排除，零文件交集 |
| Issues #1900–#1967（Review-R2 open） | 维护线认领（如 #1909 graph_builder 交叉口、#1946/#1947 spatial_decision、#1949 spatial_quality_gate） | 只读风险参考；不修不重开；测试绕开其已知缺陷 |
| #1876 SpatialSemanticContract | 已合并 | 底座，通过契约消费 |
| #1879 harness recovery | 已合并 | 复用 RecoveryPolicy/GoalVerification 语义 |
| #1880 multiscale | 已合并 | 不碰 ScaleRepresentationProfile |
| #1869 AnalysisWorkflowContract | 已合并（含 R1/R2 清偿 669246c/9c4a3b） | 本任务主要扩展对象 |

## 2. 地基事实（探索者盘点 + PR #1869 正文核实）

### AnalysisWorkflowContract v1（app/services/gis_harness/analysis_workflows/）
- contracts.py：AnalysisStepSpec L85-103（role_inputs `role:`/`step:` L88-92）；DataRoleBinding 三态 L70-82；ScienceContext L57-67（metric_crs_policy=auto_utm / denominator_policy=explicit_only）；fail-closed 校验 L224-306；contract_fingerprint L176-185（不含绑定 ref）
- families.py 五族 L37-43：admin_facility_distribution / service_accessibility / hazard_exposure / temporal_change / statistical_zones（步骤蓝图 L71-182）
- executor.py：波次执行 L445-504（Kahn _waves L507-528）；每步经共享 ToolDispatchService 真实分发；幂等复用 L329-332；预算门（>10 万行 typed blocked）；step: 输入靠进程内 `_step_payloads` 内存直传（L106,199-224），崩溃 resume 靠 envelope.ref 回补（L207-215）
- recompute.py：step_fingerprint L50-65 = sha256(schema+tool+params+units+science+input_identities)；ref@content_revision 内容身份（session_data.py:874-883）；style_only_invalidation 恒空 L119-123（已钉扎）；downstream_closure L68-90
- evidence.py：StepReceipt L38-55（MethodUseContract 摘要 fail-open；未执行步骤不得出现）
- state.py：单键 alias `analysis_workflow.v1:<id>` L35-36；RUNNING_STALE_SECONDS=300；run_log 环形 ≤24；verdict_of L140-154
- 工具面 app/tools/analysis_workflow_tools.py：plan/run/status/recompute 四工具；分布式锁单写者；recompute 显式变更集+漂移合并 L556-589

### 已锁定端到端链（真实 dispatch，全部矢量）
- admin 分布三步链（test_executor_e2e.py:105）
- service_accessibility 真路网链 isochrone_network→overlay→aggregate（test_families_e2e.py:128）
- temporal 双期差分（:161）；H3 网格（:187）；hazard buffer→spatial_join（test_executor_e2e.py:231）

### workflow_compiler.py 是另一条线
15 阶段确定性编译管线（query→intent→recipe→产品计划），与 AnalysisWorkflowContract 执行无直接连接 —— 不混淆、不合并。

## 3. 核实缺口（本任务实施面）

1. **vector↔raster 衔接缺失**：五族零栅格步骤；executor 只透传 ref:raster（executor.py:67-73）不铸栅格、无 raster→zonal/polygonize 步骤；statistical_zones 用 H3 矢量网格替代栅格（families.py:157-162）
2. **network 结果→行政汇总未打通**：service_accessibility 聚合的是 population 点层（families.py:113-118）；无任何族连接 network→admin aggregation
3. **envelope 类型化不足**：AnalysisArtifactEnvelope L138-160 有 units/feature_count/stats/lineage_inputs/fingerprint/output_roles/crs（crs 取 result.get("crs") 多为空，executor.py:406）；缺输出几何类型/字段 schema/时间有效性/栅格元数据（分辨率/波段/nodata）/聚合 basis（仅字符串标签）/空间范围；无 typed schema 严格门
4. **partial/stale 不外传**：stale 词表存在（contracts.py:126）但从未赋值；run success 仅 status=="satisfied"（analysis_workflow_tools.py:393）；partial 不进恢复面
5. **MapSpec handoff 断**：run 只返回 layers refs + visualization_hints（L401-405）；无代码把 envelope/output_roles 桥到 MapSpecLayer —— MapSpec 侧已有承接面 MapSpecLayerDataBinding（mapspec_schema.py:553-563）+ source_ref(:1010)，无需改 MapSpec
6. 无跨会话持久 job 队列 / 按指纹磁盘产物缓存（复用仅限本 workflow 状态内）——超出本 PR 范围，记录为技术债
7. plan 期 tool_exists=lambda n:True（L207）——注册面检查推迟到 run

## 4. 已知关联缺陷（只读规避，不修）

- #1909（High）：graph_builder._process_intersections 浮点交点 split 失效 → 路网拓扑错误。网络链测试用自建 fixture 图（直线/正交，避开交叉口 split 路径），不依赖 graph_builder 修复
- #1949（Medium）：spatial_quality_gate/repair 管道 outlier bounds 丢失 —— 不在 ownership，不碰
- #1946/#1947：spatial_decision 方向表/规则适用性 —— 不碰

## 5. 测试/工具环境

- worktree 复用主仓 `.venv`（Python 3.13.17）：`cd worktree && /home/kevin/projects/webgis/webgis-ai-agent/.venv/bin/python -m pytest ...`
- 快速道：`-o addopts= -q --no-cov`；既有套件基线：tests/unit/gis_harness/analysis_workflows 83 passed（#1869 时点）
- markers：heavy（geopandas/numpy/rasterio）/ perf / cartography / real_services
- oracle 目录已存在：tests/science_oracles/（crs_units/edge_cases/geodetector/geostat/network）
