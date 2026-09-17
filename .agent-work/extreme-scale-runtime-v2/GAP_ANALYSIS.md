# GAP_ANALYSIS — 任务书里程碑逐项 vs 现状

> 里程碑编号按任务书条目展开为 M1–M13（M1–M7 为任务书七项能力，M8–M13 为落地必备工程项）。每项给出：已存在（复用）/ 缺口（新建）/ 落点。

## M1 viewport-aware progressive loading（视口感知渐进加载）

- **已存在**：渲染面视口感知——`refreshGeoJsonSourcesByViewport`（renderer.ts:220-254）+ map-panel 100ms debounce（map-panel.tsx:1144-1149）+ `publishViewportContext` 300ms 二道 debounce（map-panel.tsx:1150-1155）；MVT 层由 MapLibre 原生 XYZ 调度"天然视口感知"（adapter.ts:177）；`filterFeaturesByBounds`/`thinFeaturesForViewport`（lib/utils/geo.ts:131/170）；视口缓存 per-source+viewport（renderer.ts:68, 100-111）。
- **缺口**：①服务端无 bbox/视口要素查询端点（数据面查询粒度只有整包 layer.py:72 或 z/x/y 瓦片 layer.py:276）；②首帧"低清全貌→高清细节"的渐进序列不存在——大层首帧只能等 MapLibre 逐瓦片（无低清整层概览）；③小-中层（≤5000）一次整包后无任何渐进。④渐进请求无优先级/取消语义（MapLibre 内部调度不可控）。
- **落点**：新服务端 view 端点（bbox+capped+generalized）+ 前端 progressive 编排器（新目录 map-kit/progressive/）。

## M2 分层 LOD（hierarchical LOD, 数据面）

- **已存在**：**样式面 LOD 已被 #1356 占据**（ADR-0154 `label.zoomBands`/`thresholds.maxFeatures`，其 PR body 明言落点）——本方向不碰。数据面已有：MVT 编码 z<14 半像素简化（mvt.py:69）；渲染预算 5000 抽稀（renderer.ts:37）；`select_data_strategy` 三档决策（data_tiers.py:94）+ `effective_inline_budget`（data_tiers.py:63，视口面积×复杂度连续插值）——**决策面已存在但没有服务端执行器**。
- **缺口**：服务端多级几何金字塔（同 ref 的 lod0/lod1/…预泛化版本）、descriptor 缺 LOD 元数据（levels/bytes/counts）、前端按 zoom 换 source 层级的编排。`SpatialIndexCache` 只存单份全精度几何（mvt.py:1207-1240），无派生 LOD 层。
- **落点**：mvt.py additive（per-(session,ref) LOD 层缓存，复用双界 LRU+epoch 模式）或新 `tile_pipeline.py`；descriptor additive 字段。

## M3 incremental source patch（增量源补丁）

- **已存在**：`source-diff.ts` 的 `diffFeatureCollection`（renderer.ts:4-5 import；≤2000 要素阈值 renderer.ts:155）——但**只用于 unchanged 判定**，diff 结果（add/remove/update）被丢弃；worker-bridge 已能离线程 diff（reconciler.worker.ts）。
- **缺口**：①diff → MapLibre 增量应用（`setData` 全量 vs GeoJSON source 无原生 partial update——需评估 updateData/坐标级 patch 或分桶 source）；②服务端到前端的增量协议（overwrite 后只发变更要素/版本号）；③>2000 要素 source 的任何小变更目前都是整包 setData（renderer.ts:184）——即全量重解析（~100ms/50k 要素, renderer.ts:19 注释）。
- **落点**：扩展 source-diff 消费侧（新 progressive/patch.ts）；SSE step_result/map_action 载荷 additive。

## M4 worker scheduling（前端 worker 调度）

- **已存在**：唯一 worker = mapspec-compiler reconciler（diffSpecs 专用；worker-bridge.ts:341-345；超时 30s :43、闲置保温 10s :54、失败诚实回退主线程）。`RenderDebouncer` 帧预算（render-debouncer.ts:23, 10ms/帧）是主线程调度原语。
- **缺口**：①无几何 worker（bbox 过滤/抽稀/diff 全在主线程 idle 回调, renderer.ts:248-253）；②无通用任务调度器（优先级/取消/预算分摊）；③worker 池（多核并行过滤/解码）不存在。
- **落点**：新 `progressive/scheduler.worker.ts` + `progressive/scheduler.ts`（复用 worker-bridge 的超时/降级/保温纪律）。

## M5 内存预算（内存）

- **已存在（服务端）**：全套字节预算原语——SpatialIndexCache 256MB/256 refs（mvt.py:1418）、TileLRUCache 256MB/4096 tiles/4MB 单条（mvt.py:1582-1590）、RefPayloadCache 128MB（ref_payload_cache.py:24）、SessionStore 50MB env（session_data.py:337）、raster 256MB env（raster_tile_service.py:8-11）。
- **已存在（前端）**：ref-source-resolver LRU 24 条（ref-source-resolver.ts:36）；WeakMap 引用缓存（renderer.ts:22, 67）。
- **缺口**：①前端**无跨 source 总内存预算**（N 个 5k 要素层 × 全量 raw + filtered 双份（renderer.ts:67-68 raw/filtered 各存一份）可无界增长）；②无预算超限的驱逐/降级动作；③服务端预算彼此独立，无会话级统一视图（`_layer_data_budget` 只限请求数, layer.py:52-69）。
- **落点**：前端 `progressive/memory-budget.ts`（以 descriptor.estimated_bytes 为输入）；服务端会话级预算聚合可选。

## M6 网络预算（网络）

- **已存在**：会话级 600 req/min 限流（layer.py:52-69）；ETag/304（瓦片 layer.py:250-273；**整包端点没有**）；gzip（layer.py:96-102）；`content_revision` cache-busting（tile-url.ts:22-25）；`Cache-Control: max-age=30`。
- **缺口**：①前端无并发/带宽预算器（MapLibre 瓦片并发不可控；`apiFetch` 整包 120s 超时, use-sse-stream.ts:813）；②整包端点无 ETag/304/Range——会话恢复/重挂全量重拉；③渐进加载的多请求优先级（概览 > 焦点瓦片）不存在。
- **落点**：新 `progressive/network-budget.ts`；整包端点补 ETag（低成本高收益，注意 #1112 同 ref 变异需配合 content_revision/ETag 失效）。

## M7 渲染退化（render degradation）

- **已存在**：抽稀兜底（thinFeaturesForViewport, renderer.ts:104-111）；符号律密度降级（symbol-law.ts, f(zoom, featureCount)）；`RenderDebouncer` 超预算统计（render-debouncer.ts FrameStats.budgetExceeded）；#1356 在**场景面**做了 3d→2.5d→2d 退化链（其 PR body）——样式/场景域，非本方向。
- **缺口**：数据面退化链（预算超限 → LOD 降档 → 抽稀加密 → 隐藏层 + 显式披露）不存在；无「当前处于退化态」的用户可见证据/遥测面。
- **落点**：`progressive/degrade.ts` + useHudStore/披露面 additive。

## M8 合成规模 benchmark

- **已存在**：`tests/perf/test_mvt_cache_pressure_benchmark.py`（10 场景 + BenchmarkInstrumenter：对象/字节/构建/逐出计数）——**直接模板**；baseline gate（tests/benchmarks/_baseline_policy.py：missing=fail, PERF_UPDATE_BASELINES=1）；CI 预算门 scripts/perf/run_budget.py + perf/budgets.json（已有 `mvt_tile_p95_ms`）；前端无 headless 地图 perf 惯例（vitest 只做功能）。
- **缺口**：①端到端渐进加载合成 benchmark（1M 要素 ref → 首帧/完整帧/内存峰值）；②前端编排器逻辑基准（vitest + 合成 FC + fake timers 可做纯逻辑面）；③budgets.json 新条目与本方向棘轮。
- **落点**：`tests/benchmarks/test_extreme_scale_*.py` + `scripts/perf/extreme_scale_measurements.py`（命名避让 #1352 的 gis_bench_v2）。

## M9 服务端视口查询原语

- **已存在**：`SpatialIndexEntry.query_candidates`（mvt.py:1261）已是 bbox→候选查询——**视口查询的引擎已就绪**，只暴露在瓦片路由内；`_feature_collection_bbox` cap 5000（layer.py:364）。
- **缺口**：把该查询暴露为「视口+上限+简化级别」的 API 形状（含续读/优先级游标）；descriptor.bbox 已有可先做视口相交短路。
- **落点**：layer.py append 路由 + tile_pipeline.py。

## M10 feature flag / kill-switch 约定

- **已存在约定**：环境变量大写下划线（`SESSION_STORE_MAX_BYTES` session_data.py:337、`RASTER_TILE_CACHE_MAX_BYTES`、`WEBGIS_REF_CONTENT_HASH` ref_descriptor.py 尾注）；MapSpec additive 字段 + identity upgrader（#1356 v1.4 先例）；SSE 载荷 additive（descriptor 字段透传 use-sse-stream.ts:725）。
- **缺口**：本方向 flag 未定（见 DECISIONS.md：`WEBGIS_EXTREME_SCALE_*` env + descriptor/MapSpec additive 字段双轨，默认关闭 = 现行为逐字节不变）。

## M11 可观测性 / 预算披露

- **已存在**：cartography_metrics_store、cartography_ratchet（app/services/）；perf 基准 instrumenter 惯例；前端 useHudStore 证据面（renderer.ts:82 读 layers）。
- **缺口**：渐进加载运行时指标（首帧时延/LOD 档位/patch 字节数/退化事件/预算水位）无承载面。
- **落点**：progressive 模块内 metrics + HUD/披露 additive；对齐 V5-G 有界事件纪律（ref_lifecycle.py:22）。

## M12 测试矩阵 / 回归棘轮

- **已存在**：后端 tests/unit（mvt 相关见 test_layer_api/test_layer_descriptor_api/test_layer_feature_endpoint/tests/perf 压力基准）、tests/benchmarks、tests/perf；marker 契约（pytest.ini）；前端 vitest 同目录测试（map-kit 39 文件）。并发/stale 语义测试有深厚先例（mvt epoch/ghost 测试族）。
- **缺口**：渐进加载专属矩阵（见 TEST_MATRIX.md）。
- **落点**：tests/unit + tests/benchmarks + frontend *.test.ts。

## M13 ADR + 文档

- **已存在**：ADR 惯例（docs/adr/211 个，最新 0197）；CHANGELOG/UBIQUITOUS_LANGUAGE append 纪律；CONTEXT.md 词汇（ref_id/SessionStore/Data Plane）。
- **缺口**：本方向 ADR（**0200 起**——0198=#1336、0199=#1356 已占）；UBIQUITOUS_LANGUAGE 需补 LOD/progressive/patch 词条。

## 汇总：复用率最高的五个既有资产

1. `SpatialIndexEntry.query_candidates`（mvt.py:1261）——视口查询引擎现成。
2. `data_tiers.select_data_strategy/effective_inline_budget`（data_tiers.py:94/63）——预算/档位决策现成（且是 ADR-0163 钦定单一事实源）。
3. 双界 LRU + epoch + singleflight 三件套（mvt.py:1400/1575/1703）——新缓存直接套模式。
4. `source-diff.ts`——增量 patch 的 diff 半边已存在，缺的只是应用半边。
5. `tests/perf/test_mvt_cache_pressure_benchmark.py` + `_baseline_policy.py`——合成规模 benchmark 的骨架与棘轮机制现成。
