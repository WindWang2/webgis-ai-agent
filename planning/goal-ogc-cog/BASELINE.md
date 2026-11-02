# GOAL-02 OGC 开放服务 / COG / 远程数据底座 —— M0 基线勘察

- 基线：origin/master `6c5e71c2cb04afe31b40bf66ca96e6dd6480b4e7`（2026-10-31 18:54 +0800，PR #1875 合并后）
- worktree：`/home/kevin/projects/webgis/webgis-wt-goal-ogc-cog`（branch `zcode/goal-ogc-cog-data-services-v1`）
- 勘察方式：两个只读 Explore subagent（A：协议 adapter/安全/物化；B：消费链/工具/测试）+ 主 Agent 亲读关键文件交叉验证。所有结论带 file:line。

## 1. 协议 adapter 现状（子功能逐项）

| 子功能 | 已有实现 | 入口文件与函数 | 真实调用链 | 现有测试 | 缺口/证据 | 对应 PR/issue | 允许改动范围 |
|---|---|---|---|---|---|---|---|
| OGC API Features | collections/items 查询、CQL2 门控、游标分页 | `app/services/data_fabric/adapters/ogc_api_adapter.py:43` | connect_data_source→connection_manager→adapter.query→materialize | `tests/unit/test_ogc_adapters_753.py`、`test_ogc_wfs_arcgis_v2.py` | 无 /tiles /coverage 端点消费；CRS 协商硬编码 CRS84（:357-358） | #1758 系列演进 | 只读复用；不改 Features 行为 |
| WFS | bbox/FES/分页 | `adapters/wfs_adapter.py:100` | 同上 | test_ogc_wfs_arcgis_v2 | 无 raster 语义 | — | 只读 |
| WMS/WMTS | GetCapabilities 解析 + GetMap URL 模板（metadata-only） | `adapters/wms_wmts_adapter.py:186,412-441` | describe→metadata | test_ogc_adapters_753:354 | 不取瓦片字节；无 TileMatrixSet 解析 | — | 只读；不重复其能力 |
| STAC | POST /search、bbox/datetime/token 游标、cql2-json 下推 | `adapters/stac_adapter.py:168,544` | connect→search→features | test_cloud_geospatial_adapters 等 7 处 | **asset 不枚举、不读取、无物化**（registry.py:162 明示 separate 但无实现） | — | 扩展 asset 面（本任务 M4） |
| COG | metadata-only describe + ≤16×16 preview | `adapters/cog_adapter.py:60,163-167` | resolve→rasterio open | test_cog_tenant_isolation、test_ads_local_adapters:174 | **query 显式指向 materialization 但物化服务无 raster 输入（悬空）**；无远程 /vsicurl 测试 | ADR-0171 | 物化实现在新模块（M4），adapter 不动语义 |
| OGC API Tiles | **全仓零实现** | — | — | 0 测试 | grep 全仓无 /tiles tileset 消费 | — | 本任务独占新增 |
| OGC API Coverages | **全仓零实现** | — | — | 0 测试 | 唯一 "coverages" 命中是无关 skill 文件 | — | 本任务独占新增 |
| PMTiles→XYZ | read_tile_bytes + XYZ 服务 + ETag/缓存 | `pmtiles_tile_service.py:87`、routes/data_fabric.py:928 | catalog item→route→adapter | pmtiles 相关 4 处 | — | #1771(g3d01) | 复用纪律模式 |

## 2. 安全原语（只读复用，不重写）

- `DataFabricSecurity.validate_url`（`security.py:98-226`）：scheme 白名单 + 私网/IMDS/loopback 拦截 + getaddrinfo 全记录 DNS 检查 + IPv4-mapped IPv6 递归拆解；`allow_private` 供内网部署。
- `make_safe_session`（:452-486）：SSRFSafeHTTPAdapter 逐跳校验 + DNS 钉扎（TOCTOU）+ 可选限流。
- `bounded_get/safe_json_get`（:494-540）：Content-Length 预检 + 解压后逐块字节帽（下限 16MiB）。
- `ensure_same_origin_url`（:670）、`redact_url`（:252）、`resolve_safe_local_path`（:589）。
- `app/lib/geo_raster/env.py:31-58` `validate_remote_href`：RasterReader.open 前的 /vsicurl SSRF 门；GDAL_HTTP_MAX_RETRY=0/MAX_REDIRECT=0（#1416）。

## 3. 栅格读取预算原语（M4 复用）

- `RasterReader.open`（`app/lib/geo_raster/reader.py:89-115`）：SSRF 门 + 共享 GDAL env；`read_window/read_band/read_full` 走 `_budgeted_read` 512MiB 红线（:32）。
- `remote.py`：RemoteReadPolicy（重试 3 次/全抖动/瞬态分类）+ RemoteReadSession（2000 请求/512MiB 预算）+ 断路器接入 + 取消协作（:104-176）。
- `cog.py`：validate_cog（:110-144）、write_cog（:50-107）、range_read_probe（:147）。
- `windowed.py` execute_windowed：块式 + halo + 预算。

## 4. 物化与 ref 体系

- `materialization_service.py`：**全部矢量**（GeoJSON session ref / `ref:fabric-parquet/` + BlobStore CAS ≤256MiB + sha256 指纹）；raster 输入缺失（:252-331, :484-552）。
- `lakehouse/raster_object.py:81` `publish_cog_data_object`：COG 文件→DataObject（content_sha256 + chunk_checksums + grid identity + owner_scope）——M3/M4 直接复用。
- `session_data_manager.store(prefix=...)` → `ref:` 游标；artifact_registry 登记血缘。
- tile 服务纪律：`tile_cache.py`（LRU 2048 条/256MiB/单条 4MiB）、`tile_identity.py`（ETag=sha256(body+fingerprint)[:16]、tenant 键）、TileBuildCoalescer single-flight。

## 5. 消费链（3 个端到端场景追踪）

场景 S1「矢量目录图层上图」（既有，作对照）：
自然语言→chat→`ToolDispatchService.dispatch`（`tool_dispatch_service.py:308`）→`webgis_layer_upsert`（`cartography_tools.py:403`，ref 解引用 :460-494）→`mapspec_store.layer_upsert`（`mapspec_store.py:439`）→`mapspec/mutation_handlers._handle_upsertlayer`（:99）→`process_layer_ingestion`（`pipeline.py`）→`mapspec_source.store_data`（:30）分类 source→前端 `mapspec-runtime/runtime.ts applySource:1016-1103`（geojson/vector/raster-dem/inline/url 兜底五分支）→MapLibre。ref 注水 `ref-source-resolver.ts:163`。

场景 S2「PMTiles XYZ 底图」（既有，瓦片代理模式对照）：
catalog item→`GET /data-fabric/catalog/{id}/pmtiles-tiles/{z}/{x}/{y}`（routes:928）→`pmtiles_tile_service.serve_pmtiles_tile`（:105，鉴权后缓存/ETag/single-flight）→MapSpec raster url 模板→`addRasterTileSource`（renderer.ts:986）。

场景 S3「STAC 搜索→目录」（既有）：
connect_data_source(stac)→stac_adapter.search→features；**asset 侧断链**（本任务 M4 补）。

## 6. 关键消费点事实（设计输入）

- MapSpec source union（`app/lib/cartography/mapspec_schema.py:287-294`）：geojson / vector(`tiles[]`+min/maxzoom) / raster(静态图) / raster-dem / data_fabric(`catalog_item_id`/url) / model3d。
- `mapspec_source.store_data`（:30-93）：**dict `type:"vector"` 无 passthrough 分支**——跌落 geojson inline（M2 需最小兼容扩展）；`type in DATAFABRIC_SOURCE_TYPES` 或含 catalog_item_id → 全字段透传（data_fabric url 模板今日即可用）。
- 渲染兜底：`source.url || source.dataPath` 字符串→`addRasterTileSource`（runtime.ts:1094-1101）——栅格瓦片代理 URL 无需前端改动。
- 渲染契约 `render-protocol/contract-manifest.ts:56-67`：wms/wmts/pmtiles='none'、data_fabric='partial'——本任务不提升 wms/wmts（不属 Tiles 网关范围），新增能力经 vector/url 两条已 'full' 语义的既有路径。
- 工具挂载点：`app/tools/__init__.py _TOOL_MODULES` + `categories.py` 归类；命名查重：已有 `inspect_data_source`（data_fabric_tools）、`materialize_dataset`、`prepare_dataset`（data_preparation_tools）——新工具拟名 `inspect_data_service` / `materialize_remote_raster_asset` / `read_coverage_subset` 无冲突。
- #1868 prepare/bind：入参 `source_ref` 必须是 `ref:` 游标（`data_preparation_tools.py:113-115`）；bind 走 FieldBindingRequest（schema:139）。
- G01：`gis_harness/data_qualification.py:30-32` 五态资格 + `DataQualification`（:177）receipt 形状通用。
- 前端：`add-source-form.tsx:17-24` SOURCE_TYPES 白名单 + `dataFabricApi.createDataSource`。

## 7. 测试基建

- 离线 HTTP 模拟：`tests/fixtures/data_fabric/fake_server.py`（FakeFabricAdapter 挂 requests HTTPAdapter seam，逐跳 SSRF 仍生效）+ `tests/data/fabric_fixtures.py` FakeSourceServer（OGC Features/WFS/STAC/ArcGIS canned 最小真响应；`patched_safe_sessions` 替换全部 adapter 的 make_safe_session）。
- 缺口：无 Tiles/Coverages fixture；无远程 COG（HTTP）测试；WMS/WMTS 零瓦片取数测试；渲染端 fabric 源消费零测试。

## 8. GitHub PR/issue 排查结论

- #1868（数据准备）、#1869（分析工作流）、#1870（制图决策）、#1871（图册）、#1873（要素编辑）、#1874（地图操作事务）、#1875（联动视图）全部 MERGED；#1868 **无 GitHub review**（reviews 数组为空）。
- open issues 全部为 audit 汇总（S1-S7 系列）与 #1799-#1847 High/Medium 缺陷，与本任务无重叠认领；其中 #1841-#1847 安全类已有专人跟踪，不在本任务复制其信任边界（新代码全部走既有 SSRF/owner_scope 门）。
- g3d01（#1771，PMTiles→XYZ/DEM）与 g3dv2-a（#1835，DEM 供给）为 tile 纪律先例，无文件冲突（详见 PR_OVERLAP_MATRIX.md）。
