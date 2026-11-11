# BASELINE — G17-IMPORT-WORKBENCH

- 基线 SHA：`816705218a5aa6202bf15d745cb31cddb3437f44`（= 2026-11-10 快照，fetch 后 origin/master 未再前进）
- Worktree：`C:/Users/wangj.KEVIN/projects/webgis-worktrees/goal-g17-import-workbench-20261110T131224Z`
- Branch：`zcode/goal-g17-import-workbench-20261110T131224Z`
- 本地 Python：conda 3.13.9，fastapi/geopandas/pyogrio/pandas/shapely/pytest/httpx/openpyxl/aiosqlite/ijson/pytest-asyncio 全部可用。

## 现有调用链（要点 + 证据）

1. **上传只处理第一个文件**：`app/api/routes/upload.py:157-170`，其余文件仅进 `ignored_files`（契约被 `tests/data/test_upload_identity_v4.py:384-407` 钉死：精确有序列表）。新工作台必须走**新端点**，不改旧响应。
2. **解析**：`app/services/data_parser.py` — `parse_vector(file_path, upload_dir, upload_id, *, crs=None)`（L299）产出单个 `original.geojson`；GPKG/KML 不传 layer（L329/331，默认第一层）；CSV `_parse_csv`（L441-560）按列名猜经纬度、未声明 CRS 披露 `assumed`；非 GeoJSON 缺 CRS 直接 ParseError（L369-372）。格式白名单 L18-24（无 xlsx）。内容守卫 L77-208（魔数/驱动白名单/zip 炸弹/路径穿越）。
3. **摄入**：`app/services/data_ingest/pipeline.py` — `IngestPipeline.ingest(session_id, fc, *, name, crs, source_type, prefix, license, attribution, cancel_event)`（L150-165）→ `IngestResult{ok/duplicate/ref_id/profile_summary/quality_summary/...}`。dedup 键 = canonical-JSON 载荷指纹（会话内）。取消经 `cancel_event`。ok 必须最后一步成功后置位。
4. **GPKG 枚举先例**：`app/services/data_fabric/adapters/geopackage_adapter.py` — `pyogrio.list_layers`（L102）、`read_info`（L145，零行读元数据）、`read_dataframe(max_features=)`（L173-208 有界预览）。
5. **准备计划先例**：`app/services/data_preparation/plan.py` — 状态机 + plan_hash 幂等 + per-session JSONL 持久化；HTTP 面 inspect/plan/preview/execute。
6. **纯表现状**：artifact 词表无 "table" 类型，但 `register_artifact`（artifact_registry.py:535）`artifact_type` 为自由字符串；`GET /chat/sessions/{sid}/table-artifacts/{ref}`（chat_session_views.py:141-173）前缀白名单含 `ref:table-` → 纯表用 `session_data_manager.store(session_id, payload, prefix="table")` 即可被既有端点取回。
7. **XLSX 依赖**：openpyxl/calamine 仅 dev（requirements-dev.txt:29-30）；生产承诺 XLSX 需把 openpyxl 加入 requirements.txt + 运行时能力探测。
8. **权限**：upload 侧 `_verify_session_owner`（auth_history_bridge.py:33-54，session→Conversation.user_id，fail-closed 404）+ DB 写边界 `authorize_session_write`（core/auth.py:146-176）。UploadRecord 无 user 列。
9. **工具**：`app/tools/__init__.py` `_TOOL_MODULES` 登记；`@tool(registry, name, ...)`；descriptor kwarg 白名单 registry.py:530-545（side_effect/produced_refs/latency_class/memory_class/scale_class/failure_modes/idempotent...）；session 归属用 `_resolve_session_id`（LLM 传参不可信）；typed failure：KeyError→NOT_FOUND，异常→`{"success":False,"code":...}`。同构先例：`webgis_map_operation`（preview/commit/回执）、`prepare_dataset`（检查→提案→准备+ref_offload）、`export_atlas`（durable job）。测试纪律：经 `registry.dispatch` 而非直调。
10. **取消/资源**：`app/lib/cancellation.py`（OperationCancelled/CancellationToken/checkpoint）；jobs `submit_durable_job`；governor `HarnessResourceGovernor`。
11. **前端**：`frontend/lib/api/upload.ts` XHR client（Bearer/X-Session-Token/X-Request-ID）；`dataset-manager.tsx:216-226` 单文件 input 是唯一上传 UI 入口；无 react-query（zustand + 自研 `useBoundedPoll`、`use-virtual-rows`）；i18n = `frontend/messages/{zh-CN,en-US}/<ns>.json` + `lib/i18n/messages.ts` 登记（camelCase ns 先例 dataPreparation.json）；vitest+RTL，FakeXHR 先例 `lib/api/upload.test.ts`，组件 mock 先例 `dataset-manager-upload.test.tsx`。
12. **路由挂载**：main.py:1102-1160 `include_router(..., prefix="/api/v1")`；i18n 错误目录 `app/locales/{zh_CN,en_US}.json` append-only。

## 基线小 fixture 行为（WP01，静态核对 + 实现期补运行矩阵）

- 单 GeoJSON：完整链（upload→parse→(register_ref)→ingest ref）。
- 三层 GPKG：`gpd.read_file` 默认第一层（L331），其余两层不可达。
- ZIP 双 Shapefile 集：`_validate_shapefile_zip` 只要求含 .shp+.dbf（L193-197），读取时不分组——`.zip` 走 `_read_shapefile` 读"第一个 .shp"（按 zip 条目顺序），另一套丢弃。
- CSV 自定义列名：`_detect_csv_columns` 候选集不含 Easting/Northing → ParseError。
- XLSX：不在白名单 → 400「不支持」。

## 开放 PR/issue 相关性

- 开放：#2032（UI 重设计——避开全局布局/主题，只做薄挂载）、#2022（G10 刷新——不做刷新）、#2021（G11 简报——无关）、dependabot×8（无关）。
- 已合并排重清单核实：#1973/#1873/#1875/#1975/#2011-#2017/#2033-#2036 均为其他领域，无 staged 导入工作台实现。
- 相关 issue：#2044（attach_dataset 上传归属校验缺陷）——本任务不修复，但新代码必须自带归属校验，不复现该模式。
