# 0306. 项目级工程包交换与可移植复现（webgis-project-package）

**Date:** 2026-11-28
**Status:** Proposed
**Branch:** `zcode/goal-portable-project-exchange-20261127`
**Baselines:** ADR-0211（export lineage 回执）· ADR-0249（workspace durability / BlobStore 单一事实源）· G18 data-delivery（staged 交付纪律）· model_authoring export/imports（format 门范式）· ADR-0236（MapProductVersion 快照）

## Context

master 已有会话工作空间快照（`app/services/workspace/snapshot.py`）、工作流编译包
（`workflow_v4/package.py`）、单模型导出/导入对偶（`model_authoring/export.py+
imports.py`）与单数据集多格式交付（G18 `data_delivery`），但**项目级工程包**——
依赖闭包 + 标准 GIS 物化 + 安全导入 + 复现证据的完整闭环——不存在：
`project_bundle/project_package/project_import/workspace_bundle` 全仓零命中。
用户新旅程「把本项目的数据、工作流和地图打包供另一台本地部署使用」无法满足。

## Decision

1. **包容器 = ZIP + manifest**：format 门 `{"format":
   "webgis-project-package", "format_version": 1}`（model_authoring 同款范式）；
   manifest ≤1MiB、条目 ≤2000、总展开 ≤2GiB（settings
   `PROJECT_EXCHANGE_MAX_PACKAGE_BYTES` 可调）、单条目 ≤512MiB、压缩比 ≤200x。
   下载名 `*.wgproject.zip`（诚实 ZIP 语义）。
2. **依赖闭包与五态分类**：manifest 引用一律用包内稳定逻辑 ID
   （`ds-/wf-/art-/map-<slug>`）；依赖分类 embedded / external_reference /
   redacted / missing / unsupported——引用绝不冒充已打包文件。整包复现分级
   full_offline_recompute / view_only / partial；`session_ref` 是计划期暂态，
   导出时刻解析为 embedded/missing（诚实两级）。
3. **不写第二套读写器**：矢量 → G18 `PyogrioWriter`（GDAL GPKG，多图层单
   `standard/layers.gpkg`）；表格 → `CSVWriter`；GeoJSON 产物 →
   `GeoJSONWriter`；栅格 → 字节保真拷贝（不重投影）。物化前后**独立读路径**
   双读核对（pyogrio/rasterio/pandas 读回），fidelity receipt 逐数据集给
   exact / semantic_equivalent / partial / not_evaluated。
4. **载荷三条权威通路**（不摸 Redis key、不走任意磁盘路径）：upload →
   UploadRecord 持久文件；会话 ref → `session_data_manager.get_shared`
   （raster ref 走 `artifact_registry` 磁盘真相单点）；产物 →
   ArtifactRevision head（`read_content` digest 校验 / binary BlobStore 直读）。
5. **导出 = 进程内 durable runner**（G18 同款，非 Celery 新类型）：staging →
   ZIP → `os.replace` 原子发布到 exports 目录 → owner 侧车 + 会话面板
   export ref。幂等键防重复；取消/失败丢弃 staging，无半成品。
6. **导入 = 不可信 ZIP**：中央目录先读（条目/声明大小/压缩比）→ 路径规范化
   （绝对/穿越/反斜杠/非 NFC/重复/符号链接/Windows 保留名全拒）→ 扩展名
   白名单（嵌套压缩包显式拒绝）→ 逐条目流式解压预算 → sha256 对账 manifest。
   注册在**全新隔离项目**内**单事务**完成（拓扑序：数据集→工作流→产物→地图）；
   任一步失败事务回滚 + 磁盘清理，原项目零接触。绝不自动执行工作流；
   历史产物作为历史产物导入（不伪造 run）。会话 ref 恢复是提交后的尽力而为
   （原 ref 原位写回目标会话），失败如实进恢复报告。
7. **双入口同一领域服务**：REST（`/projects/{pid}/exchange/*` +
   `/project-exchange/*`）与 Pi 工具族（`project_exchange_inspect/export_plan/
   export/import_inspect/import_plan/import`，deferred 超集注册，绝不进
   NATIVE_TOOL_NAMES）共用 `app/services/project_exchange`；输入只接受授权
   project_id / 受控上传 ID（专用接收面 `pkgnz-*` + owner sidecar），绝不接受
   磁盘路径。UI 入口挂在既有产物中心（不建第二种工作台）。
8. **Pi pin 不动**：本线只消费现有 Pi 运行能力；vendor/pi 保持 v1.6.1
   （`ef4a1cfc`）。上游 v1.7.0 存在，归 01 线裁决。

## Consequences

- 无新 DB 表、无迁移：包/导入台账 = 磁盘 manifest/receipt（与 data_delivery
  同构）；导入产物落既有表（Project/ProjectDataset/Workflow/WorkflowRevision/
  Artifact/ArtifactRevision/ArtifactLineage/MapProductVersion）。
- layer 类型数据集与外部云任务不物化（unsupported 披露）；PNG 数据集载荷不进
  UploadRecord（format 词表约束），由会话栅格恢复双轨承担——全部在披露清单可见。
- 保真语义：导入侧 verdict 上限是 semantic_equivalent（标准格式转换后字节形态
  变化），永称 exact 即伪证。
- 样式面：包内保留原生 MapSpec + MapLibre style 合法子集；SLD/.qgz 不声明、
  不伪造。
