# G18 BASELINE — 2026-11-10

## 环境/基线事实

- 主仓库 `WindWang2/webgis-ai-agent`，`origin/master = 816705218a5aa6202bf15d745cb31cddb3437f44`（与研究快照一致，fetch 后无前进）。
- 默认分支 `master`（`gh repo view` 确认）。
- Worktree：`../webgis-worktrees/goal-g18-dataset-delivery-20261110T131127Z`，分支 `zcode/goal-g18-dataset-delivery-20261110T131127Z`。
- 开发机 Python：Anaconda base 3.13.9；`pyogrio 0.12.1`（repo 声明 `>=0.13.0`，`Parquet` 驱动不存在——GeoParquet 写走 pyarrow 21.0.0 的 vector_carrier 通道，无需 GDAL Parquet 驱动）；驱动可用性（pyogrio.list_drivers 实测）：`GPKG rw`、`GeoJSON rw`、`CSV rw`、`ESRI Shapefile rw`、`FlatGeobuf rw`。pyproj 3.7.2、geopandas 1.1.3、shapely 可用。

## 既有失败（基线证据，非本任务引入）

- `tests/unit/test_geoparquet_fast_lane_v5.py::test_materialize_opt_in_geoparquet_and_default_unchanged` 在干净 master 本机失败（`KeyError: 'path'`，tests/unit/test_geoparquet_fast_lane_v5.py:308；`_materialize_geoparquet_result` 返回字典缺 `path` 键，10 passed/1 failed）。本任务在 WP05 顺带以最小 additive 修复（返回体补 `path`/`bytes`/`content_sha256`），属同一调用链上的兼容修复，不改老语义。

## 相关 PR / issue 状态（2026-11-10 重查）

- 已合并：#2011–#2017（执行编译/Pi runtime/数据资格/情境工作台/制图组合器/能力生态/可靠性实验室）、#2033–#2036（命令清单/Studio 单源/提交撤销/Pi-only）。
- 开放且相关：#2032（UI 重设计——共享前端接线，避免全局布局改动）、#2022（G10 数据刷新——改 `project-assets-section.tsx`/`project.json`，本任务不触碰该文件）、#2021（G11 简报）。无任何在途 data delivery PR。
- 相关开放 issue（不接管）：#2070（晋升 GC 误删 blob，G10/平台域）、#2066/#2065（V6 执行器截断族）。本设计发布路径只新建自己的 delivery 目录并按 DataObject manifest 引用计数，不改动 GC。

## 关键复用锚点（源码证据）

| 能力 | 位置 |
|---|---|
| 认证 FC 导出（客户端整包上传、50MiB 帽） | app/api/routes/map.py `export_geojson` / `download_map_export` |
| ref:fabric-parquet 磁盘工件 + 台账 + DataObject | app/services/data_fabric/materialization_service.py `materialize_geoparquet`（原子写、sha256、register_artifact、BlobStore CAS 预算发布） |
| ref 路径派生单点 | app/services/artifact_registry.py `fabric_parquet_path`（charset 白名单） |
| NDJSON 流（行帽/取消/lease/_eof 诚实终结） | app/api/routes/data_fabric.py `stream_catalog_item_features_http` |
| FC↔Arrow 载体（features_to_arrow/table_to_geoparquet/geo metadata） | app/services/data_fabric/vector_carrier.py |
| session FC ref 存储/读取 | app/services/session_data.py `session_data_manager.store/get/get_ref_descriptor` |
| 项目产物 durable 晋升 + 修订账本 | app/services/project_artifact_promotion.py；app/models/project.py `ArtifactRevision`（content_sha256=身份） |
| DataObject CAS 多文件发布（流式量尺→manifest→blob） | app/services/lakehouse/data_object.py `publish_data_object` |
| durable job 表 + 取消 + 幂等键 | app/models/db_model.py `AnalysisTask`；app/services/jobs/{store,lifecycle,cancellation}.py；DELETE /api/v1/jobs/{id}（两段式取消） |
| typed 谓词/bbox/字段投影 | app/services/feature_pages.py `evaluate_where/parse_bbox_param/parse_fields_param` |
| 选择截断契约（ids_truncated/matched_count/omitted 服务端派生） | app/services/gis_harness/interaction_ir/contracts.py `_clip` |
| 工具注册 | app/tools/__init__.py `_TOOL_MODULES`；@tool 装饰器含 execution_policy/side_effect/produced_refs/failure_modes |
| 权限下载先例（owner/sig/404 不泄漏存在性） | map.py `download_map_export`（fail-closed） |
| 前端 API client / i18n 模式 | frontend/lib/api/*.ts（apiFetch + fastGet）；frontend/lib/i18n（namespace 注册于 messages.ts） |
