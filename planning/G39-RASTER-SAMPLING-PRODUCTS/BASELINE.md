# G39 BASELINE — 数值栅格点集与沿线采样成果

- 执行时 base：`1276692737a8cf58d0604fb40ae0e70fc61779a2`（origin/master，2026-11-14 研究快照复核一致）
- 分支：`zcode/g39-raster-sampling-products-20261114T172106Z`；worktree：`/home/kevin/projects/webgis/webgis-worktrees/g39-raster-sampling-products-20261114T172106Z`
- 执行日：2026-11-14/15（北京时间）。开放 PR 执行时仅 #2117（G37 兄弟任务，会话模型运行配置，不触本域）。

## 已核对的复用基线（生产调用链）

| 接缝 | 位置（base SHA） | 事实 |
|---|---|---|
| G20 像元检视 | `app/services/raster_authoring/probe.py:1-213` | 源像元 1×1 窗口读，raw/scaled、mask、row/col、单位；明示不插值；点/波段帽 16；只检视不产成果 |
| G20 源解析 | `app/services/raster_authoring/source.py:79-470` | `resolve_source(session,ref,owner_token,trusted_dispatch)` → `RasterSource`（path/data_revision/content_fingerprint/descriptor/band_meta≤64/dataset_tags/bands_truncated）；`data_revision` 是有界指纹（mtime+size+首尾 256KB 或降采样指纹），**不是**全内容不可变证明 |
| 波段元数据 | `source.py:_read_band_meta` | 每波段 dtype/unit/scale/offset/role/vmin/vmax；role 封闭词表；`scale_declared/offset_declared` 区分声明与默认 |
| 领域预算 | `app/services/raster_authoring/budgets.py` | `MAX_RASTER_PROBE_POINTS/BANDS=16`、`MAX_BAND_COUNT_METADATA=64`、`MAX_DESCRIPTOR_BANDS=16`（band 17+ 需受控补读） |
| DEM 采样数学 | `app/lib/geo_analysis/measurements.py:190-260`、`app/lib/geo_analysis/terrain.py` | `_bilinear_sample`（任一角无效→NaN）、`_world_to_cell`、`_sample_polyline`（顶点即采样点、段边界 checkpoint）；MAX_PROFILE_SAMPLES=2M 预估先于分配 |
| terrain_profile 工具 | `app/tools/terrain_measurements.py:73-209` | 输入为栅格 CRS 折线；profile_chart ≤512 点抽稀保端点；保持不动 |
| RasterReader | `app/lib/geo_raster/reader.py:226-282` | `read_window((col,row,w,h), band, bands)` 全分辨率受预算读；多波段栈读有字节闸 |
| G23 durable runner | `app/services/data_operations/runner.py`、`compute_jobs.py` | `submit_async_job(task_type,kind,display_name,params,idempotency_key,...)` + `run_async_job`；原子认领、心跳、取消轮询、attempt fencing；收据短路→幂等建 job→join 活跃 job |
| JobKind | `app/services/jobs/lifecycle.py:46-62` | DB 列 String(20) 无 CHECK，新 kind 纯加性（`raster_sampling` 15 字符合法） |
| G18 交付 | `app/services/data_delivery/*` | `SourceSnapshot` 支持 session_ref FC/rows、fabric GeoParquet 工件（`_snapshot_from_parquet`）；`publish_data_object_for_delivery`；硬帽行 200k/输出 512MB |
| CAS DataObject | `app/services/lakehouse/data_object.py:288` | `publish_data_object(source_files,kind,owner_scope,payload,producer,source_refs,input_fingerprint)` → 全 CAS 不可变对象（blobs+manifest） |
| 计划/收据存储先例 | `app/services/data_preparation/field_compute/receipt.py` | per-session JSONL + 原子替换 + by_key 稳定键；`BASE_STORAGE_DIR=DATA_DIR/.webgis-agent` |
| 选择句柄 | `app/services/selection_handles`（G24） | scope(bbox|where|ids)+revision 快照→不透明 handle；materialize 成会话 ref |
| 会话 ref 存储 | `app/services/session_data.py` | `session_data_manager.store/get/get_ref_data(owner_token)` |
| 工具注册 | `app/tools/registry.py` `@tool(...)`；工具名前缀规范见 field_compute_tools | Pi 面：typed Args 模型、`_resolve_session_id` 注入 session、有界回执 |
| 路由 | `app/api/routes/*.py` + `app/main.py` include（prefix=/api/v1） | `require_owned_session` + X-Session-Token |
| 前端 | `frontend/components/sidebar/data-sources-tab.tsx:623`（prepare 子页挂面板）；`frontend/lib/api/*.ts`（apiFetch transport）；`frontend/messages/{zh-CN,en-US}/<ns>.json` | elevation-profile 组件只管高程（distance_m/elevation_m），不动 |
| 迁移 | `migrations/versions/`（alembic） | 本任务不加 DB 表（见 PLAN 决策） |

## 冲突记录（历史文档 vs 生产代码）

无 ChatEngine fallback 相关冲突需处理：G39 不触 chat 引擎。`sampling_tools.py`（Goal07 空间抽样布点）与本域「栅格取值采样」不同名不同义；本任务工具命名 `raster_sampling_*` 避免撞名。

## 旗舰旅程（WP01 选定）

1. **监测站点多波段提取**：真实点 FeatureCollection ref（≥3 站，稳定 feature id）+ 单源多波段数值栅格（值/单位不同）→ 完整样本长表 + 站位点成果 + receipt + G18 CSV 下载回读。
2. **双源沿线剖面**：LineString 路线（地理 CRS，测地距离）+ 两个不同 CRS/分辨率数值栅格 → 同站位各自网格采样、支撑差异披露、多波段曲线 + 断口显示 + 站位点层。
