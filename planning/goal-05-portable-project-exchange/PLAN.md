# PLAN — 05｜GIS 工程包、跨系统数据交换与可移植复现 Harness（goal-05-portable-project-exchange）

- 基准：`origin/master = f34cb69dda6150ee4135e6d2a476708149d9eef7`（2026-11-28 实测 fetch，与任务书快照一致）
- 分支：`zcode/goal-portable-project-exchange-20261127`
- worktree：`/home/kevin/projects/webgis/webgis-portable-project-exchange-20261127`
- vendor/pi pin：v1.6.1（`ef4a1cfc`）——本线不动 vendor/pi（Pi pin/host 变更归 01 线独占）
- 使命：在既有 Project/ProjectDataset/Workflow/Artifact/ArtifactLineage、MapSpec、ref/DataObject、durable jobs、会话文件面板与 Pi 宿主之上，补齐**项目级工程包**（manifest + 依赖闭包 + 标准 GIS 数据物化 + 安全导入 + 复现证据）与 **Pi/UI 双入口**；不建平行项目库、平行任务中心、第二制图引擎。

## 0. 层级定义（任务书 §4）

- **层级 A（核心验收）**：本系统原生可重开工程包——export → 新隔离环境 import → 重开 MapSpec/数据集/工作流 → 显式重算一致。
- **层级 B（核心验收=标准数据可读）**：标准 GIS 软件可读的分层交换子集——GeoPackage/GeoJSON/COG/CSV-Parquet，经独立读写库（ogr2ogr/GDAL、geopandas/rasterio）验证；样式按实际能力分级披露，不承诺 QGIS/ArcGIS 工程全保真。

## 1. 需求矩阵（D0 判定；依据 DEDUP_MATRIX.md，全部经本 worktree HEAD 核对）

（探查代理返回后回填本表）

## 2. 交付范围

（待 DEDUP_MATRIX 定稿后回填所有权白名单/避让清单）

## 3. 增量工作包（WBS）

- **WP1 依赖图与 manifest 合同**：`project_exchange` 新模块——manifest schema v1、依赖闭包、分级（embedded/external_reference/redacted/missing/unsupported；完整/部分 × 可重算/仅可查看）、pack plan/inspect（零写入）与 materialize（durable job）。
- **WP2 标准 GIS 数据物化**：复用既有读写器物化 GeoPackage（多图层矢量）、GeoJSON（RFC 7946）、COG/GeoTIFF、CSV/Parquet；fidelity receipt（字段/类型/CRS/几何/nodata/计数/范围，转换前后独立核对）；流式打包、去重、配额、取消、失败恢复。
- **WP3 安全解析与事务式导入**：不可信包防护（manifest bounded 读、ZIP slip/bomb/绝对路径/符号链接/重复条目/hash 校验）；import inspect/plan；默认新项目隔离 staging；拓扑序导入、ID 重映射、失败回滚/不可见 staging、幂等键；导入后校验报告。
- **WP4 复现校验与互操作实证**：复现证据报告（exact/semantic_equivalent/partial/not_evaluated）；原生包导出→全新隔离环境→导入→重开→显式重算；跨平台路径/中文编码；标准格式独立库读回；外部不可复制资源诚实披露。
- **WP5 双入口**：REST API（plan/inspect/export/import）+ 项目工作台 UI（导出向导/导入预检/恢复报告）+ Pi typed 工具（inspect/export_plan/export/import_inspect/import_plan 等，经真实 registry/dispatch）；产物进既有会话文件目录与下载链。
- **WP6 资源/安全/审查/PR**：故障注入、恶意包矩阵、双 owner 负例、性能对比；R1/R2 独立审查；PR。

## 4. 阶段计划

R0 事实清点（本文件 + BASELINE/DEDUP_MATRIX）→ R1 基线测试复跑 → R2 契约定案（manifest schema + DECISIONS + ADR）→ R3 WP1+WP2 最小纵切（合同+最简单格式物化+测试）→ R4 WP3 导入链 → R5 WP4 复现验证 → R6 WP5 双入口 → R7 WP6 加固与性能 → R8 R1 审查 → R9 清偿复审 → R10 R2 + PR。

## 5. 验收判据（对齐任务书必过旅程）

1. 原生工程包旅程：矢量+栅格+MapSpec+参数化工作流+真实成果；隔离导入后 IDs/refs 重映射、重开与显式重算可比事实一致。✅判据：E2E 测试绿
2. 标准交换子集：ogr2ogr/geopandas/rasterio 独立读回 CRS/字段/几何/nodata；不支持面真实披露。✅判据：独立读回测试绿
3. 恶意/越权/缺失矩阵：预检/导入正确拒绝或合法部分恢复；取消无半成品。✅判据：负例测试族绿
4. Pi + UI/API 双入口：对话旅程真实跑通；产物进会话文件目录；导入计划/恢复报告可见。✅判据：双入口测试绿
