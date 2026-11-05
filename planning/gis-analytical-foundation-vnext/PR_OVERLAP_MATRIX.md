# PR Overlap Matrix — gis-analytical-foundation-vnext

基准：origin/master `b6ac970ebcee4abc7b933d9693a23d00542a6d18`（2026-11-05 15:10 UTC，与任务书快照一致）。

## Open PR 冲突检查（2026-11-06 核实）

仓库唯一 open PR：**#1965**（`zcode/review-g3dv2-fixes`，scene3d/terrain/observation/UI review-fix）。

其全部 32 个改动文件与本任务所有权面（`app/tools/`、`app/lib/gis/`、`app/services/gis_harness/analysis_workflows/`、`workflow_compiler.py`、`capability_*`、`app/services/spatial_*`、`app/services/geocompute/`、`app/services/network/`、`tests/science_oracles/`）**零交集**。

潜在摩擦点（均非文件级冲突，注意即可）：
- `app/schemas/chat_schema.py`、`app/api/routes/layer.py`（#1965 改动）——本任务如需动 chat 证据转录或 layer 路由，必须最小化并单独 commit；默认不动。
- `docs/quality/generated-artifacts.json`（#1965 改动）——若本任务新增生成物账本条目需追加，保持 additive。

## 近期已合并底座（不得重做，只在其上增量）

| PR | 内容 | 对本任务的意义 |
|---|---|---|
| #1869 | AnalysisWorkflowContract 薄编排层（五族/真实 dispatch/选择性重算/receipt 证据链） | B1 typed intermediate layer 的宿主 |
| #1876 | SpatialSemanticContract v1（units/vocabulary/preflight/tool_gate） | B2 语义守门底座；ADR-0279 |
| #1760 | 方法学守门三段 + MethodUseContract + 守门 oracle | B2/B4 方法学证据底座 |
| #1761 | GIS 工具能力 8 面对账引擎 | B0/B4 能力目录对账底座 |
| #1762 | G05 任务级验收 21 场景 + 19 族真实 dispatch 缝语料 | B5 黄金例子宿主 |
| #1879 | GISRecoveryDiagnostic + RecoveryPolicy + GoalVerification（ADR-0281） | B3 恢复语义底座 |
| #1880 | 多尺度 ScaleRepresentationProfile（ADR-0280） | 比例尺语义边界 |
| #1878 | OGC/COG 数据服务 | 栅格链数据来源之一 |
| #1961 | network 只重建请求路径（perf） | B3 网络链路优化既有事实 |
| #1958 | 结果大小契约 + 错误展示限制 | B4 有界证据的既有约束 |

## Review-R2 issues 避让（只读风险依据，不抢修）

与本所有权面相交的 open issues（不修复、不重复修复，设计与实现时绕开或兼容）：
- #1909 / #1939 / #1940（S96 系）：`app/services/network/` graph_builder 交叉口打断、network/engine/accessibility/facility/allocation —— **B3 网络链设计必须把「断边/交叉口拓扑是否正确」当作外部不确定项**，不依赖其修复，也不改同一行为。
- #1946（S102）：simulation/* + spatial_analyzer + spatial_decision 前段。
- #1947（S103）：app/services/spatial_decision（22 文件）。
- #1949（S105）：spatial_guardrails / spatial_meta_profiler / spatial_quality_*。
- #1844：data_quality 环截断（>512 顶点多边形判未闭合）——oracle 数据制作时规避该路径或标注依赖。
- #1845/#1846：simulation 串行推进/幂等键 —— 不触碰 simulation。
