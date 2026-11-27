# DEDUP_MATRIX — 05｜工程包交换与可移植复现

判定基准：本 worktree HEAD `f34cb69dd`（origin/master）。判定：present / partial / missing / covered-by-other / blocked。
证据格式：`path:line`（本 worktree 实测）。开放 PR #2290（01 线 Pi 运行策略）、#2291（03 线抽样）与本线无路径交集（Pi host/provider vs 抽样统计）。

## WP1 依赖图 / manifest / 复现分级

| 能力 | 判定 | 证据 | 剩余增量 |
|---|---|---|---|
| 项目域权威读取（Project/Dataset/Workflow/Artifact/Lineage） | present | app/services/project_service.py:120；app/models/project.py:14,42,82,242,288 | 复用，不改 |
| 项目聚合指纹 | present | project_service.py:290 get_project_fingerprint；provenance/fingerprint.py:62,83,144 | 包内引用复用三族指纹 |
| 会话/项目工作空间快照（manifest+物化+GC interlock） | partial | app/services/workspace/snapshot.py:421（materialize=none/claimed/all；磁盘 `<sid>/workspace-snapshots/`） | 快照是会话工作空间冻结，无标准 GIS 物化、无依赖闭包分类、无导入；工程包为其超集但独立实现（避免动快照 GC 契约） |
| 工作流编译包（semver 注册表） | partial | app/services/gis_harness/workflow_v4/package.py:48,116,170；app/models/db_model.py:787 workflow_packages | 仅工作流编译面；无数据/地图/许可；复用其 fingerprint/兼容检查思想 |
| 单模型导出/导入对偶（format 门范式） | partial | app/services/model_authoring/export.py + imports.py（"webgis-model"/"1"） | 复用 format/version 门范式；单文档无包容器 |
| **项目级 manifest（依赖闭包+分级分类+许可+逻辑 ID）** | **missing** | `project_bundle/project_package/project_import/workspace_bundle` 全仓零命中 | 本线 WP1 全量 |
| pack plan/inspect（零写入）+ materialize（durable job） | **missing**（数据交付有单数据集 plan，无项目闭包级） | app/services/data_delivery/service.py:246（单源交付） | 本线 WP1 |

## WP2 标准 GIS 数据物化

| 能力 | 判定 | 证据 | 剩余增量 |
|---|---|---|---|
| GeoJSON RFC7946 流式写出 | present | app/services/data_delivery/writers.py:104 GeoJSONWriter | 复用 |
| GeoPackage 写出 | present | writers.py:167 PyogrioWriter（GPKG 驱动） | 复用；包内多图层单 .gpkg 组装为本线新增薄层 |
| GeoParquet / CSV / SHP-ZIP / FlatGeobuf 写出 | present | writers.py:314,445,288 | 复用 |
| COG/GeoTIFF 写出 | present | app/services/data_fabric/cog_materialization.py:290；app/services/modelops/artifacts.py:31 write_raster_output | 复用 |
| 单交付 fidelity 校验（反读+receipt） | partial | data_delivery/service.py:898 _verify_output + _finalize_delivery:639 receipt | 包级跨数据集 fidelity matrix + manifest 对账为本线新增 |
| BlobStore 内容寻址去重 | present | app/services/durable_blob_store.py:128,223 | 包内 blob 引用复用 |
| 包级配额/流式打包/取消恢复 | **missing**（durable job 取消 present，包级配额与流式 hash 校验缺） | app/services/jobs/store.py:648 request_cancel | 本线 WP2 |
| MapLibre style 合法子集随包导出 + 不可表达披露 | **missing** | storymap bundle 内嵌 MapSpec 仅服务自身 HTML（app/lib/storymap/export_packager.py:93） | 本线 WP2（保留原生 MapSpec + 披露） |
| SLD/QGIS 样式兼容 | **missing → 不承诺** | 无任何 SLD 实现 | 仅在能真实验证时声明；本线默认不声明 |

## WP3 安全解析 / 导入 / 事务恢复

| 能力 | 判定 | 证据 | 剩余增量 |
|---|---|---|---|
| 会话数据导入（staging/preview/commit） | present（session 级数据文件，非工程包） | app/services/data_import/{service,staging,commit,preview}.py | 复用其 staged 纪律思想；工程包导入独立实现 |
| 上传路径防穿越 | present | app/api/routes/upload.py:106 + data_parser.py:640（DATA_DIR 内约束） | 包解压新增包内路径规范化 |
| **不可信 ZIP 防护（slip/bomb/绝对路径/符号链接/重复条目/压缩比/深度）** | **missing** | 仓内无 zip 包导入面 | 本线 WP3 全量 |
| **import inspect/plan（能力/许可/冲突/映射预览）** | **missing** | — | 本线 WP3 |
| 拓扑序项目注册 + ID 重映射 | **missing**（model_authoring imports.py 单文档；workspace snapshot clone 会话级） | — | 本线 WP3 |
| 失败回滚 / 不可见 staging / 幂等键 | partial（交付侧 staging→原子发布 present；导入侧 rollback 缺） | data_delivery/store.py + service.py:639；jobs/submit.py:33 幂等键 | 本线 WP3 |
| 导入后校验（依赖/计数/哈希/MapSpec 语义对账） | **missing** | — | 本线 WP3 |

## WP4 复现校验 / 互操作实证

| 能力 | 判定 | 证据 | 剩余增量 |
|---|---|---|---|
| geocompute 复现证据（单运行级） | partial | app/services/geocompute/reproducibility.py | 包级复现报告为本线新增 |
| 三族指纹对账 | present | provenance/fingerprint.py:62,83,144 | 导入对账复用 |
| 原生包 export→隔离 import→重开→显式重算 | **missing** | — | 本线 WP4 全量 |
| 独立库读回（geopandas/rasterio/ogr2ogr） | partial（交付侧自有反读 present；跨工具独立验证缺） | data_delivery/service.py:898 | 本线 WP4 |
| 不可复制资源披露 | **missing** | — | 本线 WP4 |

## WP5 双入口

| 能力 | 判定 | 证据 | 剩余增量 |
|---|---|---|---|
| REST 项目路由惯例 | present | app/api/routes/project.py:52 prefix；main.py:1196-1357 挂载 | 新增 project_exchange 路由 |
| durable job 任务中心/取消/重试 | present | app/services/jobs/{submit,store,lifecycle,worker}.py；app/api/routes/jobs.py:86,148,169,218 | 复用，注册新 task_type |
| #2289 会话文件面板自动纳入新产物 | present（派生机制） | app/services/session_generated_files.py:114（delivery job :36 / export ref :67 / report :96） | 导出走 register_artifact+export_storage 即自动出现 |
| 受保护下载（owner 侧车+签名 URL） | present | app/services/export_storage.py:31,62,82；app/api/routes/map.py:876 | 复用 |
| **工程包 REST（plan/inspect/export/import + 下载）** | **missing** | — | 本线 WP5 |
| **Pi typed 工具族** | **missing**（范式 present：app/tools/data_delivery_tools.py:34 四工具 + app/tools/__init__.py:105 注册） | — | 本线 WP5 |
| **UI 导出/导入入口** | **missing**（挂载范式 present：frontend/components/sidebar/project/artifact-center.tsx:358 + DataDeliveryDialog 范式） | — | 本线 WP5 |

## 与开放 PR / 其他线边界

- #2290（01 线）：Pi host/provider 健康路由——本线只消费现有 Pi 工具注册面，不改 vendor/pi、不改 NATIVE_TOOL_NAMES 前门（新工具走 deferred 超集）。
- #2291（03 线）：抽样/精度——无交集。
- 04 线（已合并 #2287/#2288）：图符/出版导出——本线包内样式面仅携带 MapSpec 原生文档与 MapLibre style 子集，不改出版导出链。
- #2289：会话文件面板——只消费（产物自动出现），不改面板代码。

## 结论

工程包交换的项目级闭环（闭包 manifest、标准 GIS 物化组装、不可信 ZIP 导入、复现证据、双入口）在 master 上 **missing**；所有底层构件（写引擎、durable job、BlobStore、指纹、权限、下载链、面板机制、Pi 注册范式）**present 且应复用**。本线增量=把这些构件组装成项目级交换闭环，不重写任何底层。
