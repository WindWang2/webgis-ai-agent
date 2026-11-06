# GOAL-02：OGC 开放服务、COG 与远程数据生产消费底座

## 目标
补足 OGC API Tiles/Coverages、COG 远程资产可控物化与 GIS Harness 数据服务到 MapSpec 的可验证消费链。复用既有 Data Fabric（SSRF 安全、tile 服务纪律、CAS DataObject、STAC 搜索）与 #1868 数据准备工作台；不重写任何工作台核心，不碰 03 时间轴/04 符号律/05 恢复内核。

## master 起点 / 终点
- 基线：`6c5e71c2c`（origin/master，2026-10-31，PR #1875 合并后；PR 创建前会再 fetch 校验）
- 分支：`zcode/goal-ogc-cog-data-services-v1`（独立 worktree `/home/kevin/projects/webgis/webgis-wt-goal-ogc-cog`）

## 真实差异（新增为主，共享文件纯追加）
**新模块（本方向独占）**
- `app/services/data_fabric/ogc_capability.py` —— 三态能力评估（supported/unsupported/unknown）+ 封闭 reason code + WebMercatorQuad 族 TMS/CRS 注册表
- `app/services/data_fabric/adapters/ogc_tiles_adapter.py` —— OGC API Tiles 发现（read-only；conformance/collections/tilesets/TMS 同源跟随；版本化 descriptor `ogc-tiles-descriptor/1`）+ 受控 `fetch_tile`
- `app/services/data_fabric/adapters/ogc_coverages_adapter.py` —— OGC API Coverages 发现（domainset/rangetype/格式声明；`ogc-coverages-descriptor/1`）
- `app/services/data_fabric/ogc_tiles_proxy.py` —— 受控瓦片代理（DfTileCache `ogc-tiles:` 前缀 + 内容寻址 ETag + single-flight + `build_mapspec_source`）
- `app/services/data_fabric/coverage_read.py` —— subset→bounded raster→COG ref（像元预算前置门/轴协商/COVJSON+GeoTIFF 封闭解析）
- `app/services/data_fabric/cog_materialization.py` —— STAC 发现→预检→预算窗口读→CAS 发布（幂等/取消/签名 URL 脱敏）
- `app/services/data_fabric/raster_materialization.py` + `artifact_registry.fabric_cog_path` —— `ref:fabric-cog/` 工件纪律
- `app/services/data_fabric/service_qualification.py` —— G01 双轨资格（tile→display_only；coverage/COG→numeric_raster）
- `app/tools/ogc_service_tools.py` —— 4 工具（inspect_data_service / read_coverage_subset / materialize_remote_raster_asset / add_tile_service_layer）

**共享文件追加（additive 小 diff）**：registry.py（AdapterSpec×2）、source_registry/probing/capabilities（协议接线）、routes/data_fabric.py（1 条会话鉴权瓦片路由）、mapspec_source.py（vector dict passthrough 分支）、tools/__init__.py + categories.py、fake_server.py（make_response content 参数）、frontend（SOURCE_TYPES+2、descriptor 弹窗三态展示、i18n）、openapi/drift 快照（生成物）。

## 复用 vs 新增
- 复用：`DataFabricSecurity`（validate_url/make_safe_session/bounded_get/redact_url/ensure_same_origin_url）、`validate_remote_href`、`RemoteReadSession`（请求/字节预算+重试+取消）、`write_cog`、`publish_cog_data_object`（CAS）、`DfTileCache/tile_identity/TileBuildCoalescer`、`STACAdapter.search`、`spatial_catalog`、`mapspec_store.layer_upsert`、`raster_store.save_png`、#1868 全部接口（仅消费契约）。
- 新增：上列 9 个模块 + 1 条路由 + MapSpec vector passthrough（schema/前端能力 v1.0 起已存在，只补写入分类）。

## 里程碑
M0 勘察（PROTOCOL_CAPABILITY_MATRIX.md，实测矩阵）→ M1 三态发现 → M2 Tiles 消费链（代理/路由/MapSpec/渲染）→ M3 Coverage 受控读取 → M4 COG 物化 → M5 工具+G01 双轨 → M6 前端最小 → M7 离线模拟源（Tiles/Coverages/STAC-COG + 401/429/5xx/重定向 loopback/超大瓦片/错误 TMS）→ M8 预算账本 → M9 三场景 E2E + 双轮独立 review。

## 测试证据（全部离线、确定性、无密钥）
- 本方向 9 个测试文件 **81 passed**（双跑两次一致，见 EVIDENCE.md）：发现三态 16、代理/MapSpec/路由 31、coverage 真值 9、COG 物化 10（真实 loopback HTTP Range 服务器）、工具面 11、E2E 三场景 4。
- 真值对照：coverage 3×3 逐点（含 nodata/transform/north-up/subset 真子集）、COG AOI 采样点逐点、幂等（同参同 content_sha256/同 data_object_id+deduped）。
- fail-closed：默认 SSRF 拦 loopback（专门用例）、401/403/429/5xx typed、压缩瓦片帽、预算拒绝带协商事实、取消令牌。
- 回归：adapter/registry/mapspec/artifact/pmtiles 路由等 8 文件 99 passed/1 skipped；前端 vitest 38 passed（add-source-form + runtime 含 OGC 两形态）。
- 契约门：openapi 快照再生成后 `test_api_compatibility` 12 passed；`tests/quality` 失败集合与 origin/master 基线**完全一致**（10 个既有红灯零新增 —— 临时基线 worktree 对照证据在 LOOP_STATE Round 8）。
- 未跑项：无（前端 typecheck 有 3 个 master 既有红灯在 map-components/*.test.tsx，本分支变更集不含这些文件；本分支前端文件零错误）。

## 性能/科学正确性
- 预算账本进回执：materialize 的 ledger（requests≥1/bytes_read/window/out_shape）+ coverage 的 budget 字段 + 路由 X-Tile-Cache hit/miss。
- AOI 窗口语义实证：600×600 源上 1° AOI 读 150×150 窗口（≪ 整幅，绝不整幅 GET）。
- known artifact：GDAL /vsicurl 对 Python http.server 的 .msk 首访探测在本机恒 ~5s×2（curl CLI 亚毫秒、服务端即时 —— GDAL 内部行为）；已按 GDAL 官方建议对远程只读栅格设 `GDAL_PAM_ENABLED=NO` 消除 PAM 族探测（-25s/open），残余 ~10s/open 如实记录于 RISK_REGISTER R2，仅影响测试耗时（≈94s/模块）不影响生产语义。

## review-fix-review
- R1 独立对抗 review（未参与实现的 agent，逐文件）→ findings 与清偿映射见 `planning/goal-ogc-cog/INDEPENDENT_REVIEW.md`；P0/P1 全部修复后 R2 复审确认。

## 风险与诚实边界
- 不宣称完整 OGC 协议支持：Tiles 显示路径封闭于 WebMercatorQuad 族（其余 TMS typed 拒绝 + reason）；Coverage 封闭于 COVJSON/GeoTIFF 声明格式；WMS/WMTS 维持 metadata-only。
- `?session_id=` 瓦片鉴权与既有 MVT layer 路由同语义（浏览器可携）。
- master 既有红灯（quality 门 10 个、前端 typecheck 3 个）未在本 PR 处理（非本方向引入，属 S 系列审计跟踪）。

## issue 重叠排除
无认领重叠：open issues 全部为 audit 系列（#1799–#1866）。PR #1868–#1875 已 merged 且与本方向文件无冲突（交叠矩阵见 `planning/goal-ogc-cog/PR_OVERLAP_MATRIX.md`；routes/data_fabric.py 与 mapspec_source.py 为纯追加 diff）。

## 手工验证
1. `python -m pytest -q tests/unit/test_ogc_tiles_discovery_v1.py ... test_goal_e2e_scenarios_v1.py --no-cov`（9 文件 81 tests）
2. `cd frontend && pnpm exec vitest run components/sidebar/data-sources/add-source-form.test.tsx lib/mapspec-runtime/runtime.test.ts --maxWorkers=2`
3. UI：Data Sources → Register New Source → 协议下拉出现 OGC API Tiles / Coverages；注册后 dataset descriptor 弹窗显示三态能力与 reasons。
4. Agent 语义：「接入省级 OGC API Tiles 行政底图并叠加本地小学」→ inspect_data_service → add_tile_service_layer（见场景①测试）；「从 STAC 搜索真实 COG 影像并计算区域指标」（场景②）；「Coverage 没有能力声明」（场景③ typed 拒绝）。
