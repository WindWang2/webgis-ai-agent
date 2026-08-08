# ADR-0049: Project Workspace, Persistent Workflow, Spatial Data Quality & Lineage Platform

## Status
Accepted

## Context
webgis-ai-agent 原先是一个强 Session/Conversation 驱动的系统。虽然能通过聊天触发 Tool 进行分析和制图，但存在以下痛点：
1. **Session 为唯一根**：无法跨 Session 组织长期分析任务；SessionStore/ref_id 是 transient 运行时机制而非持久化业务模型。
2. **UploadRecord 为 catch-all 表**：既包含上传文件，又充当分析结果产物。
3. **Plan Mode 不可重复运行 (Re-run)**：Plan 是一次性 agent execution proposal，无法持久化保存为可复用的 workflow recipe 并替换 AOI/数据集/参数重新运行。
4. **缺少显性 Spatial Data Quality Engine**：几何与拓扑校验散落，无法在入库与分析前发现 invalid geometry, self-intersection, missing CRS 等问题并给予修复建议。
5. **缺少可执行的 Lineage / Provenance 追溯**：分析产物无法回答“该结果是如何生成的”以及“如何使用新数据自动重跑”。

## Decision

1. **引入以 Project 为根的领域模型 (Project Domain)**
   - `Project`：持久化项目边界，绑定数据源、工作流、分析产物、地图与报告。
   - `ProjectDataset`：对底层数据（UploadRecord / Layer）的逻辑引用与规范分析（Schema/CRS/Quality）。
   - `Workflow`：基于 DAG 拓扑结构的持久化流程定义，支持从成功 Plan 保存。
   - `WorkflowRun`：不可变的（Immutable）工作流执行实例与 Log/Metrics。
   - `Artifact` & `ArtifactLineage`：统一逻辑产物抽象与有向无环图溯源依赖（Parents -> Artifact -> Consumers）。

2. **建立深层空间数据质量引擎 (Spatial Quality Engine)**
   - 涵盖 Geometry (invalid, self-intersection, empty, duplicate, sliver)、Topology (overlap, gap, duplicate features, near-duplicate vertices)、CRS (missing, suspicious, unit mismatch)、Attributes (null ratio, schema mismatch, duplicate IDs, type drift)、Spatial Sanity (Null Island, extent mismatch) 等维度。
   - 输出结构化 `SpatialQualityReport` (分级为 `info`, `warning`, `error`, `blocking`)。

3. **创建 Safe Repair Pipeline**
   - 默认“不改变原始 Source”，自动修复后创建新的 `Derived Dataset / Artifact`。
   - 支持 `make_valid`, `remove_empty`, `normalize_geometry_type`, `deduplicate`, `snap_within_tolerance`, `crs_transform`, `attribute_type_normalization`。

4. **Workflow Execution & Re-run Engine**
   - 支持替换 AOI/数据集/参数全量或单步（From step）重新运行。
   - 再次运行必须通过 `ToolRegistry` 的统一安全策略（Tool Execution Policy）重新鉴权。

5. **全量向下兼容 (Backward Compatibility)**
   - 不修改匿名 Mode / Session 交互模式。
   - 现有的 UploadRecord, Layer, AnalysisTask, MapSpec Checkpoint 保持现有 API 及逻辑不变。`project_id` 作为可选拓展关联。

## Consequences
- 支持面向大型工程与长期专业的 GIS 工作空间。
- 自动化测试与 CI 需覆盖完整 Project Workspace, Quality, Workflow, Lineage 及租户隔离逻辑。
