# CURRENT_ARCHITECTURE — 极限规模渲染相关真实架构（读码核实，file:line 可查）

## 0. 总览（文字架构图）

```
Agent 执行链（后端）
  tool 结果(大 GeoJSON/栅格) ──store──▶ SessionStore（ref 提货券, ref:prefix-<hex16>）
                                        ├─ MemorySessionStore (session_data.py, 容量200/SESSION_STORE_MAX_BYTES=50MB, 逐出前 RefSpillStore 落盘)
                                        └─ RedisSessionStore (session_data_redis.py, R4b per-(session,ref) GET singleflight)
                                        ├─ RefPayloadCache (TTL 5s / 256 条 / 128MB, 进程内已解析 payload)
                                        ├─ Descriptor（store 时算好: feature_count/bbox/mvt_capable/…）
                                        └─ ref_lifecycle.py = 唯一失效权威（epoch bump + 投影清除 + observer hook）

数据面 API（app/api/routes/layer.py, 全部 require_owned_session + X-Session-Token）
  GET /layers/data/{ref_id}                          整包 GeoJSON（gzip, 无 ETag/304）
  GET /layers/data/{ref_id}/feature/{feature_id}     单要素（spatial_index 常驻时零 Redis）
  GET /layers/descriptor/{ref_id}                    V3 预算描述符（metadata-only auth）
  GET /layers/data/{ref_id}/tiles/{z}/{x}/{y}.mvt    MVT 瓦片（tile LRU + single-flight + ETag/304）
  GET /layers/data/{ref_id}/raster-tiles/…png        栅格 XYZ PNG（同构性能路径 + path TTL 缓存）

前端（Next.js + MapLibre GL v5）
  SSE step_result (use-sse-stream.ts) ──addLayer(_refId,_tileUrl,_descriptor)──▶ useHudStore layers
     ├─ descriptor.feature_count > 5000 且 mvt_capable → MapSpec source {type:"vector", tiles:[MVT URL]}（MapLibre 原生逐瓦片拉取, transformRequest 注入凭据）
     └─ 否则 apiFetch 整包 GeoJSON → inlineData source → addGeoJsonSource（viewport bbox 过滤 + 5000 预算抽稀 + setData）
  hudStateToMapSpec (adapter.ts) → MapSpec → MapSpecRuntime.reconcileAsync
     （diffSpecs 在 Web Worker; patch 应用到 map.addSource/addLayer/setData）
```

## 1. MVT 服务（app/services/mvt.py, 1804 行）

三个模块级单例（mvt.py:1801-1804）：

| 原语 | 位置 | 容量/键 | 说明 |
|---|---|---|---|
| `spatial_index_cache: SpatialIndexCache` | mvt.py:1802, 类 mvt.py:1400 | per-(session_id, ref_id)；**max_refs=256 / max_bytes=256MB**（mvt.py:1418） | STRtree 索引条目 LRU；composite epoch（global_gen + per-key, mvt.py:1475/1488）防 invalidate-during-build 幽灵复活；构建经 per-key `SingleFlight(max_inflight=128)`（mvt.py:1440, 1481） |
| `tile_lru_cache: TileLRUCache` | mvt.py:1803, 类 mvt.py:1575 | per-(session, ref, z, x, y[, cmap, bands])；**max_tiles=4096 / max_bytes=256MB / max_entry_bytes=4MB**（mvt.py:1582-1590） | 最终 gzip 字节缓存；`get_epoch`/`put_if_current`（mvt.py:1621/1632）epoch 条件写入；MVT 与 PNG 共用同一缓存实例 |
| `single_flight: SingleFlightManager` | mvt.py:1804, 类 mvt.py:1703 | max_inflight=512 / wait_timeout=30s（mvt.py:1718） | asyncio.Future 去重；leader 崩溃/超时 → 等待者诚实降级自行计算（mvt.py:1756-1777）；线程版在 app/services/singleflight.py |

- `SpatialIndexEntry`（mvt.py:1207-1240）：`features`（dict 列表）+ `geoms`（z0 世界像素 shapely）+ `geoms_lonlat` + `crosses_am` + STRtree + `estimated_bytes`。查询 `query_tile`/`query_candidates`（mvt.py:1253/1261）= tile bbox → STRtree query → 插入序恢复。**一个索引服务所有 zoom**（投影 z 齐次，mvt.py:157-175）。
- `build_spatial_index_entry`（mvt.py:1302）：重解析 ref payload；内存估算用真实 JSON 字节 + 顶点数×64B 等（mvt.py:1354-1381, Issue #395）。
- 编码：`encode_tile_from_index`（mvt.py:1136）复用缓存几何；z<14 半像素容差简化（`_SIMPLIFY_MAX_ZOOM=14`, mvt.py:69）；antimeridian 切分（mvt.py:259-470）；layer 名固定 `"data"`、extent 4096（mvt.py:45-46）。
- 线程模型：编码在 `asyncio.to_thread` 中执行（见 layer.py 路由），缓存全部 `threading.Lock`。

## 2. MVT / 栅格 / 整包三条数据面路由（app/api/routes/layer.py, 689 行）

- **租户校验**：`require_owned_session` Depends + `X-Session-Token` header（layer.py:76-77, 283-284）；本模块 `_verify_session_owner` 是 back-compat wrapper（layer.py:42-49）。
- **会话级预算**：`_layer_data_budget`（layer.py:52-69）——每 session 600 req/min Redis 限流，超限 429，**限流器缺席 fail-open**。
- **整包 GeoJSON** `GET /layers/data/{ref_id}`（layer.py:72-103）：`serialize_geojson` to_thread 分块序列化 + 端点级 gzip（#590/#880）。**无 ETag / If-None-Match / 304**——重复拉取恒为全量 200。
- **MVT 瓦片** `GET /layers/data/{ref_id}/tiles/{z}/{x}/{y}.mvt`（layer.py:276-332）：
  1. tile LRU 命中 → 直接 `_tile_response`（layer.py:305-307）；
  2. miss → `single_flight.run(cache_key, _compute)`（layer.py:331）；`_compute` 内冷 ref 的授权拉取再经 `single_flight.run(("ref_fetch", session, ref))` 收口一次（layer.py:314-323, R4c）；
  3. `_encode_tile_cached`（layer.py:222-247）在 to_thread 中：先取 `tile_epoch` → `spatial_index_cache.get_or_build` → `encode_tile_from_index` → gzip(mtime=0) → `put_if_current`（epoch 不符 raise RefDataUnavailableError → 路由重拉重试一次, layer.py:326-329）；
  4. 响应：ETag = sha256(gzip)[:16] + `If-None-Match` 304 + `Cache-Control: private, max-age=30`（layer.py:250-273, 269 的短 TTL 是 #1112 同 ref 内容变异缓解）；
  5. 坐标校验 z∈[0,20]（layer.py:298）。
- **descriptor** `GET /layers/descriptor/{ref_id}`（layer.py:453-525）：V3 存储期 descriptor 直读（metadata-only auth, 不 hydrate payload）；pre-V3 走 `_compute_descriptor_fallback`（layer.py:335-450，bbox helper cap 5000, layer.py:364）。`is_mvt_capable`（ref_descriptor.py:263-274）= 有非 GeometryCollection 几何即 true——**阈值判断不在后端**。
- **栅格瓦片** `GET /layers/data/{ref_id}/raster-tiles/{z}/{x}/{y}.png`（layer.py:531-583）：与 MVT 同构（LRU → single-flight → to_thread → ETag/304）；缓存键含 `cmap`/`bands`（layer.py:567, C5）；`(session,ref)→safe_path` 进程缓存 TTL 5s / 256 条（layer.py:591-593）；`validate_data_path` 防越权路径（layer.py:623-630, SEC-08）。
- **app/services/raster_tile_service.py**（535 行）：`render_raster_tile`（raster_tile_service.py:307）窗口读 + 重投影；PNG 缓存 TTL-free 字节上限 LRU（env `RASTER_TILE_CACHE_MAX_BYTES`, 默认 256MB）、band-stats 缓存 TTL 10min（`RASTER_STATS_CACHE_TTL_S`）；`register_raster_ref`（:242）+ ref_lifecycle observer hook（:279-304）按路径主动清除。

## 3. ref 提货券机制（签发/兑换/过期）

- **签发**：`SessionStore.store()` 造 `ref:{prefix}-{uuid4.hex[:16]}`（session_data.py:322, 332）——ref_id+session_id 即能力令牌（64bit 熵）。store 时同步算 descriptor（session_data.py:390-397）。
- **会话存储预算**：容量 200 条 + `SESSION_STORE_MAX_BYTES`（env, 默认 50MB）（session_data.py:334-363）；逐出前 `ref_spill_store.spill` 落盘（session_data.py:364-374, ADR-0104 #6f）。
- **兑换**：`get_ref_data(session_id, ref_id, owner_token=…)`（路由层 `session_data_manager.get_ref_data`，layer.py:85/215/609）——PermissionDenied→403 / 不可用→404。
- **失效/过期**：`ref_lifecycle.py` 是**唯一失效权威**（`RefInvalidationReason`: OVERWRITE/DELETE/EVICT/EXPIRE/ROLLBACK/REPLACE, ref_lifecycle.py:26-33）；`overwrite` bump revision + invalidate 派生缓存（session_data.py:434-437）；派生投影 = ref_payload_cache（TTL 5s/256 条/128MB, ref_payload_cache.py:21-24）+ spatial_index_cache + tile_lru_cache + descriptor + L1。observer hook best-effort（raster 按路径清除）。
- **content revision → 前端**：descriptor 携带 `content_revision`，前端把它拼进 MVT URL `?v=`（tile-url.ts:5-12, 22-25）实现同 ref 内容变异的 cache-busting。
- **Redis 路径**：`session_data_redis.py` R4b——miss 的 GET+parse 经 per-(session,ref) `SingleFlightManager(max_inflight=128)`（session_data_redis.py:59-61, 586, 622-623，复用 mvt 的实现）。

## 4. 前端地图数据流与 setData 调用点清单

**大数据 source 如何装载（两条通道）**：
1. **SSE live**（use-sse-stream.ts，1347 行）：`step_result`（:642）→ 有 `geojson_ref` 即 `addLayer`，source 先挂空 FC + `_refId`/`_tileUrl`/`_descriptor`（:727-756；`_tileUrl` 由 `buildMvtTileUrl(ref, sessionId, descriptor.content_revision)` 生成, :744-746）。随后 `shouldFetchFullFC = !descriptor || !mvt_capable || feature_count <= 5000`（:797-801）——大层**整包下载直接跳过**，走 MVT；小层才 `apiFetch /layers/data/{ref_id}`（:803-829）回填 `updateLayer(ref, {source: geojson})`。
2. **会话恢复**：`lib/session/map-state-restore.ts` 同样用 `buildMvtTileUrl`（map-state-restore.ts:166, 290）。
3. **committed MapSpec ref-only 源**：`lib/mapspec/ref-source-resolver.ts` 兜底拉取整包（`FETCH_FEATURE_CAP = 20000`, :28；进程内 LRU `MAX_CACHE_ENTRIES = 24`, :36；失败墓碑 TTL 30s, :41）。

**MVT 判定**：`adapter.ts` `VECTOR_TILE_THRESHOLD = 5000`（adapter.ts:27）+ `isVectorTileLayer`（adapter.ts:66-83：`_descriptor.mvt_capable && feature_count > 5000` fast path，legacy 数 features）；source 形如 `{type:"vector", tiles:[_tileUrl], minzoom:1, maxzoom:16}`（adapter.ts:177）。tile 请求凭据由 `buildTileTransformRequest`（tile-auth.ts:36-51）注入（first-party URL 才带 Bearer/X-Session-Token）。

**setData 调用点清单（全部核实）**：
| 调用点 | file:line | 触发条件 |
|---|---|---|
| `addGeoJsonSource` 更新路径 | renderer.ts:184 | 引用变化且（≤2000 要素时）`diffFeatureCollection` 非 unchanged（renderer.ts:155, 171-182, V11 W3.5/ADR-0163） |
| `refreshGeoJsonSourcesByViewport` | renderer.ts:240 | map-panel `handleMove` 100ms debounce 结算后（map-panel.tsx:1144-1149），对注册过的内联 source 重新 bbox 过滤 + 5000 预算抽稀（`VIEWPORT_RENDER_BUDGET=5000`, renderer.ts:37；helper: lib/utils/geo.ts:131/170）；MVT source 跳过（double-crop guard, renderer.ts:74-94, 232） |
| annotation 覆盖层 | lib/map-commands/annotationHelpers.ts:110 | 标注 FC 更新 |
| runtime `applySource`→`addGeoJsonSource` | runtime.ts:864-870（转 renderer.ts:157） | MapSpec patch 增/改 source 时；传当前 viewport |
| F31 引用缓存跳过 | renderer.ts:22, 164 | 同引用不 setData |

**视口感知现状（问题①的回答）**：有**渲染面**视口感知（bbox 过滤 + 网格抽稀 + `publishViewportContext` 300ms 二道 debounce, map-panel.tsx:1150-1155 → lib/selection/viewport-context.ts:74），但**数据面无 viewport 语义**：MVT 的"视口感知"是 MapLibre 原生 XYZ 瓦片调度（不可控、无优先级/预算钩子）；整包 GeoJSON 一旦 ≤5000 就全量进内存并在每次视口结算时重新过滤；`minzoom:1/maxzoom:16` 是唯一 scale 语义（adapter.ts:177）。**服务端没有任何按 bbox/视口的要素查询端点**——查询粒度只有 z/x/y 瓦片或整包。

**小变更是否触发整包 setData（问题④的回答）**：是——但有两层守卫：F31 同引用跳过（renderer.ts:164）与 ≤2000 要素的 `diffFeatureCollection` unchanged 跳过（renderer.ts:171-182）。**真正的内容变更**（哪怕 1 个要素）在 >2000 要素的 source 上仍是整包 `source.setData(effective)`（renderer.ts:184）——即全量重解析上传；没有 partial patch / `updateData` 路径。`source-diff.ts` 已产出 diff（add/remove/update 分类）但当前只用于判定 unchanged，**不用于增量应用**（增量消费是本方向的直接挂点）。

**Worker 现状**：唯一 Web Worker = `frontend/lib/mapspec-compiler/reconciler.worker.ts`（diffSpecs 离线程，worker-bridge.ts:341-345 创建；超时 30s `DIFF_WORKER_TIMEOUT_MS`, worker-bridge.ts:43；闲置保温 10s, :54）。**没有几何处理/数据解码 worker**——bbox 过滤、抽稀、diff 全在主线程（idle callback / rAF 预算内）。

**帧预算原语（可复用）**：`RenderDebouncer`（render-debouncer.ts）rAF 队列 + `frameBudgetMs`（默认 10ms, :23）+ 帧统计回调——渲染退化/预算超限的现成挂点。`symbol-law.ts` 提供 f(zoom, featureCount) 渲染符号律（AC-06/ADR-0155）。

## 5. 现有 perf script / 测试约定（问题②的服务端缓存/预算原语汇总见 §6）

- `pytest.ini`：markers = `heavy` / `perf` / `cartography` / `real_services`；`timeout=60`（thread）；perf 基线假定隔离执行（未过滤全量跑自动 skip, #664）。
- `tests/benchmarks/_baseline_policy.py`：baseline gate——missing baseline 默认 `pytest.fail`，`PERF_UPDATE_BASELINES=1` 记录 / `ALLOW_MISSING_PERF_BASELINE=1` 放行；中位数写 `tests/benchmarks/baselines.json`。
- `tests/perf/test_mvt_cache_pressure_benchmark.py`：10 个确定性场景 + `BenchmarkInstrumenter`（对象计数/缓存字节/构建次数/逐出计数）——合成规模 benchmark 的直接模板。
- `scripts/perf/run_budget.py` + `measurements.py`：CI 预算门（perf/budgets.json，含 `mvt_tile_p95_ms`）；`--self-test` 自证可红。
- 前端：`vitest run`，`*.test.ts` 与源码同目录（map-kit 39 个测试文件）；`typecheck = tsc --noEmit && tsc -p tsconfig.test.json --noEmit`。

## 6. 服务端可复用缓存/预算原语（问题②的回答）

| 原语 | file:line | 本方向复用方式 |
|---|---|---|
| 双界 LRU（条目+字节）+ epoch 防复活 | mvt.py:1400/1575, ref_payload_cache.py | 新 LOD 缓存/patch 缓存直接套该模式（不要发明第三种 LRU） |
| SingleFlightManager（asyncio, 有界等待诚实降级） | mvt.py:1703-1798 | 渐进加载请求去重；Redis 侧已有先例（session_data_redis.py:59-61） |
| epoch 捕获-条件写入（get_epoch/put_if_current） | mvt.py:1621-1660 | 任何异步构建回写缓存的标准姿势 |
| ref_lifecycle 统一失效 + observer hook | ref_lifecycle.py:26-40, raster_tile_service.py:279-304 | 新派生投影（LOD 金字塔/patch 基线）接 `register_ref_invalidation_hook` |
| `_layer_data_budget` 会话级限流（600/min, fail-open） | layer.py:52-69 | 网络/请求预算的现有执行点，渐进加载配额在此之上收紧 |
| data_tiers 三档 + `select_data_strategy`/`effective_inline_budget` | app/lib/cartography/data_tiers.py:27/37/63/94（前端镜像 frontend/lib/data-tiers.ts:10-17） | 任务书预算函数的**单一事实源**——分层 LOD 阈值必须 import 它而非新造字面量（ADR-0163 纪律，grep 断言禁同义字面量） |
| descriptor（feature_count/bbox/mvt_capable/estimated_bytes/content_hash/content_revision） | ref_descriptor.py:51/263/436；layer.py:453-525 | 渐进加载决策的元数据输入，免整包扫描 |
| ETag/304 + Cache-Control max-age=30 | layer.py:250-273/647-669 | 整包端点目前缺失（gap）；新端点/增量端点必须对齐 |
| SESSION_STORE 字节预算 + spill | session_data.py:334-374 | 内存预算的后端对应物 |
