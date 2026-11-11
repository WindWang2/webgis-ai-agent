# G18 DEDUP_MATRIX — 对照最新 master + 开放 PR

| 能力项 | 状态 | 证据 / 说明 |
|---|---|---|
| 认证 GeoJSON 导出（客户端上传整包→文件） | present（保留，不动） | map.py `export_geojson`；本任务不迁移、不破坏该端点 |
| 导出文件下载 + owner/sig 校验 | present（地图产物专用） | map.py `download_map_export`；数据交付需要独立路由（不同存储/授权域），复用同款 fail-closed 纪律 |
| ref 驱动服务端读取导出 | **missing** | master 无任何「服务器按 ref/revision 读数据并转格式交付」端点；搜索 `/export/geojson` 仅 schema/契约/测试 |
| GeoParquet 物化（disk artifact + 台账 + DataObject） | present（复用为源与 fast-path 目标） | materialization_service.materialize_geoparquet；缺用户交付合同（格式选择/损失/下载） |
| GeoPackage/Shapefile/FlatGeobuf/CSV 矢量交付 | **missing** | 全仓无 GPKG/SHP/FGB 用户导出路径 |
| 交付预览（保真/损失/完整性/确认） | **missing** | 无对应 schema/服务 |
| 交付任务（durable job/取消/进度/幂等） | partial（平台设施 present，交付域 missing） | AnalysisTask/DurableJobStore/cancellation 可复用；无 delivery 任务类型与执行体 |
| 发布/登记/受控下载闭环（多文件清单/校验和/重开可取） | **missing**（项目产物中心明确无下载端点） | artifact-center.tsx 头注释；本任务补交付域下载 |
| 标准 Pi 工具（预览→提交→查询） | **missing** | app/tools 无 data_delivery 面 |
| 表格 artifact → CSV 交付 | **missing** | rows 型 payload（feature_aggregate 等）无 CSV 出口 |
| 选择完整性（ids 截断披露/typed predicate 重放） | partial（契约 present，交付侧 missing） | interaction_ir contracts ids_truncated 派生；交付规格需绑定并复核 |
| #2022 G10 数据刷新 | owned-by-other | 不触碰外部重抓/增量更新；导出已有快照 |
| #2032 UI 重设计 | owned-by-other | 仅新增独立组件 + 薄入口，不动主题/布局/面板骨架 |
| #2021 G11 简报 / G12–G17、G19–G21 | owned-by-other | 不依赖其未合入 API；缺其增强时走基线兼容路径 |
| #2004 选择截断治理 / ADR-0287 | present（吸收为规格约束） | selected_ids≤50 + matched_count 复核纳入 scope 合同 |
| G09 参数化批分析 | owned-by-other/前批 | 不做计算编排 |
