# DECISIONS — 05｜工程包交换

| # | 决策 | 理由 | 备选与否决 |
|---|---|---|---|
| D1 | 域名 `project_exchange`（app/services/project_exchange/、app/api/routes/project_exchange.py、app/tools/project_exchange_tools.py） | 与既有 data_delivery / model_authoring 同构；避开旧轮次 `g05_` 前缀（scripts/g05_task_acceptance.py 属旧 G05） | `project_package`（与 delivery job task_type 冲突面模糊） |
| D2 | 包容器 = ZIP（manifest.json 在根；format 门 `{"format":"webgis-project-package","format_version":1}`）；下载名 `<name>.wgproject.zip` | ZIP 中央目录可先读 manifest、条目级 bounded 校验（大小/计数/压缩比/路径）天然可实施、跨平台通用；"避免无依据定制通用格式" | 自定义 tar 变体（无收益）；单 JSON bundle（无法承载二进制 GIS 数据） |
| D3 | manifest 全引用用包内稳定逻辑 ID（`ds-<slug>`/`wf-<slug>`/`art-<slug>`/`map-<slug>`/`file-<n>`），导入时重映射为新系统 ID；禁止绝对路径/凭据/签名 URL/owner_token 入包 | 任务书硬性要求；导入幂等与跨部署可移植的前提 | 直接携带 DB 主键（跨部署必撞） |
| D4 | 依赖分类五态：`embedded` / `external_reference` / `redacted` / `missing` / `unsupported`；整体复现分级：`full_offline_recompute` / `view_only` / `partial` | 任务书 §WP1；严格区分 supported/consumed/not_evaluated 的项目纪律 | 二态 embed/不 embed（无法表达许可受限/缺失） |
| D5 | 物化复用 data_delivery writers（GeoJSON/GPKG/GeoParquet/CSV/SHP）与 modelops/cog 的 GTiff 写出；包内矢量组装为**单多图层 `layers.gpkg`**（layer 名=逻辑 ID），栅格 `rasters/<id>.tif`，表格 `tables/<id>.csv|parquet` | 不写第二套读写器（任务书硬性）；单 gpkg 多图层是 QGIS 等 GIS 软件最优读入形态 | 每数据集一个 .gpkg（文件数膨胀，去重差） |
| D6 | 导出 = durable job（task_type `project_package_export`），staging 目录构建 → os.replace 原子发布到 `DATA_DIR/exports/wgproject-<pkgid>.zip` + owner 侧车；manifest/receipt 副本存 `DATA_DIR/project-packages/<pkg_id>/` | 复用 jobs 生命周期（进度/取消/幂等/重试）与 exports 下载链（签名 URL+owner 校验+自动进 #2289 面板） | 新建顶级目录+新下载端点（重复安全审计面） |
| D7 | 导入 = 不可信 ZIP：先读中央目录 bounded 校验（条目数/总展开大小/压缩比/单文件上限/路径规范化拒绝绝对路径与 `..`/拒绝非常规条目/嵌套深度≤1/SHA-256 对账），仅流式解包 whitelist 条目到 `DATA_DIR/project-packages/imports/<job>/staging/`；默认导入为**新项目**（isolated staging → 注册成功后可见），不提供覆盖现有项目路径 | 任务书 §WP3 硬性；"原项目不受影响" | 原地覆盖导入（否决）；zipfile extractall（否决，无单条目管控） |
| D8 | 导入注册顺序 = 拓扑序：datasets → workflows（含 revisions）→ artifacts/lineage → map products（MapProductVersion lineage_kind="import"）；任一步失败：已注册对象保留在隐藏 staging 项目（status=deleted 池外）或全部回滚删除，原项目零接触；幂等键防重 | 复用 ProjectService/CAS；失败终态可见 | 部分静默成功（否决） |
| D9 | 导入后**不自动重算**任何工作流；显式重走现有 workflow run 入口且须用户授权；历史产物作为历史产物导入（lineage 保留，不伪造 run） | 任务书 §WP3/§WP4；科学诚实 | 导入即重算（否决） |
| D10 | Pi 工具族：`project_exchange_inspect` / `project_exchange_export_plan` / `project_exchange_export`（side_effect=state_mutation）/ `project_exchange_import_inspect` / `project_exchange_import_plan` / `project_exchange_import`（state_mutation）；走 `@tool` + `_TOOL_MODULES` 一条注册（deferred 超集），不动 NATIVE_TOOL_NAMES 前门与 index.mjs；输入仅接受 project_id / upload_id / job_id，不接受磁盘路径 | 最小接线；G18 data_delivery_tools.py 同款范式 | 进前门 7 工具（需动 index.mjs+surface，超边界） |
| D11 | UI：artifact-center 增加「工程包」区（导出：范围选择→依赖/许可/分级预览→提交任务→下载；导入：上传→manifest 摘要/冲突/许可→导入计划→执行→恢复报告），复用 DataDeliveryDialog 组件骨架与 downloadWithAuth | artifact-center.tsx:358 既有挂载范式 | 新独立页面（违背"不新建第二种工作台"） |
| D12 | 包内样式面：保留原生 MapSpec 文档（`maps/<id>.mapspec.json`）+ MapLibre style 合法子集 `styles/<id>.style.json`（经现有编译器投影）；桌面 GIS 样式（SLD/qml）**不声明支持**，在 README-dislosure 披露 | 真实可验证的才声明 | 伪造 .qgz/SLD（否决） |
| D13 | 不新增 DB 表、无迁移：包/导入台账 = 磁盘 manifest/receipt（与 data_delivery 同构）；导入产物落既有表（Project/ProjectDataset/Workflow/WorkflowRevision/Artifact/ArtifactLineage/MapProductVersion） | 最小变更；receipt 文件已是仓内成熟模式 | 新表（需要迁移+GC 面，无必要） |
| D14 | 包内不携带底图/外部服务凭证/云模型；外部依赖记 `external_reference`（含 URI 与义务说明）或 `redacted`（许可不允许）；离线恢复不联网 | 任务书 §WP4 | 自动拉取上游（否决） |
| D15 | fidelity receipt 字段：fields(名/类型/可空)、feature_count、geometry_types、bbox、crs(+axis order)、nodata/band/scale(栅格)、时间列时区；物化前后独立双读（写前读源、写后用独立库重读）比对 | 任务书 §WP2；假保真=伪成功 | 仅记文件名/大小（否决） |
