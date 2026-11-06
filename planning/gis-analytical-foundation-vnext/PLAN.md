# PLAN — GIS 能力图、真实空间算法与跨工具分析编排 vNext（B 轨）

> 基准 origin/master `b6ac970eb`（2026-11-05）。worktree `/home/kevin/projects/webgis/webgis-wt-goal-gis-analytical-vnext`，branch `zcode/goal-gis-analytical-foundation-vnext-20261105`。
> 底座：#1869 AnalysisWorkflowContract v1（五族/dispatch/receipt/recompute）、#1876 SpatialSemanticContract v1（ADR-0279）、#1760 MethodUseContract、#1761 能力对账、#1879 RecoveryPolicy（ADR-0281）、#1880 多尺度（ADR-0280）。**只做 additive 增量，不重做底座。**

## 需求状态表（B0 盘点驱动；随 SOURCE_INVENTORY.md 细化更新）

| 需求 | 状态 | 依据 |
|---|---|---|
| AnalysisWorkflowContract 薄编排层 | implemented（#1869） | contracts.py/executor.py/recompute.py/state.py |
| typed 阶段产物（geometry/vertical datum/time/aggregation basis/extent/nodata/ownership/provenance） | partial（envelope 仅 crs/units/feature_count/stats/lineage/fingerprint） | INTERFACE_CONTRACTS.md §2 |
| 三条端到端方法链（行政统计/栅格 zonal/网络可达→汇总） | partial（五族 E2E 存在，但栅格链与网络→行政汇总尾部、MapSpec-ready 分级产物待核） | 待 explorer A 报告 |
| ref 衔接 typed 严格门 | partial（unit_contract 执行前校验；跨步产物类型/CRS/nodata 门待核） | 待核 |
| 已知答案 oracle（地理/投影/边界/nodata/时间/极值） | partial（science_oracles 14 数据集，覆盖面待 explorer B 报告） | 待核 |
| 局部重算/缓存失效 | implemented（#1869 recompute.py；style-only 零重算已有测试） | test_recompute.py |
| 状态机 cancelled/failed/partial/stale/validated/not_evaluated | partial（step/verdict 态存在；not_evaluated 产物态与 partial≠final 断言待核） | 待核 |
| 预算估计/预检/进度 receipt/重试语义 | partial（AWF_BUDGET_EXCEEDED 执行期门；预估/进度 receipt 待核） | 待核 |
| discovery metadata（方法学前置/资源预算/可制图性/推荐下一步） | partial（execution_catalog 待核） | 待核 |
| 五类 NL 黄金任务 | partial（G05 F28 场景族存在；栅格指数汇总/最近医院网络距离两条待核） | 待核 |

## 里程碑（薄切片 TDD，每片含正例/负例/边界/恢复/并发取消/资源边界）

- **M0 ✅** R0 事实检查 + worktree + 基线（analysis_workflows 83P/1S）
- **M1** B0 能力盘点定稿：SOURCE_INVENTORY.md（capability→tool→types→守门→证据矩阵 + 5-8 断点）
- **M2** ADR-0284 + 契约冻结：typed AnalysisArtifactEnvelope v2（additive 字段 + versioned schema gate + 三链 role/类型合同）——接口先冻结后实现（先例 INTERFACE_CONTRACTS.md）
- **M3** 纵切 1：链① 点位→行政 join→计数/密度（真实分母）→分级→MapSpec-ready（typed 门 + fixture + 局部重算）
- **M4** 纵切 2：链② 栅格重采样/掩膜→zonal→矢量→图（nodata/extent/vertical datum 合同）
- **M5** 纵切 3：链③ 网络→可达性/覆盖→行政汇总（无路网 typed blocked；汇总尾部 role 衔接）
- **M6** B2 oracle 扩充：跨 CRS/边界触碰/经度180/nodata/时间/极值已知答案集 + 错误数据不产图断言
- **M7** B3/B4：预检预算估计/进度 receipt/重试语义补全 + not_evaluated/partial 状态闭环 + discovery metadata + 结构化 next-step
- **M8** B5：五类 NL 黄金任务 journey + perf baseline/after（-m perf 既有机制）
- **M9** 独立 review gate（第三 subagent 只读）→ 修复 → 复核至 P0/P1=0
- **M10** PR 提交 + 最末报告

## 验收映射（任务书四条终点 → 证据形态）

1. 三类真实混合分析端到端（local fixture + dispatch + MapSpec-ready ref + lineage + 方法学确定性门控）→ M3-M5 各链 E2E 测试 + G05 场景
2. 已知答案 oracle 覆盖地理/投影/边界/nodata/时间/极值；错误数据不产"成功地图" → M6 oracle 测试
3. 兼容既有 AnalysisWorkflowContract/ToolRegistry；局部重算/缓存失效测试 → 既有 83 基线测试保持绿 + 新增失效传播测试
4. 资源预算/性能 baseline/after 对比 + 完整度/失败态用户可见证据 → M8 perf + 状态投影测试

## 红线（每片自查）

- 不建第二 workflow engine / 第二 ToolRegistry / 第二 MapSpec 真相；执行一律 ToolDispatchService（#1869 已收敛的共享缝）
- 不改 Pi bridge / ToolDispatchService 核心；交叉点文件默认只读
- 全部 additive 字段 + 默认值；schema_version 进指纹
- 缺数据 → not_evaluated/missing_data，禁止模拟统计结果
- 不抢修 Review-R2 issues（S96 network/S103/S105 等）；不重复合并底座
- 本地 -j2；不等待远端 CI
