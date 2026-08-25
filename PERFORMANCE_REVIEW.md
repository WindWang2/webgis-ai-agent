# 性能审查报告

审查日期: 2026-08-24 · 审查人: Agent C（性能专项） · 仓库: `/home/kevin/projects/webgis/webgis-ai-agent`
方法: grep 模式定位 → 精读热路径 → `.venv` 微基准量化（脚本在 /tmp，未改动仓库）。

## 1. 已有性能资产（缓存/瓦片/single-flight 现状，防重复建设）

本轮审查确认以下资产已存在且实现良好，**新优化不得重复建设**：

| 资产 | 位置 | 要点 |
|---|---|---|
| MVT 瓦片管线 | `app/services/mvt.py:1372-1643`、`app/api/routes/layer.py:200-247` | STRtree 空间索引按 (session, ref) 常驻（256 refs / 256MB 双上限 LRU）；gzip 瓦片 LRU；single-flight 去重；ETag/304；索引未建才拉 ref 数据；编码走 `asyncio.to_thread` |
| 栅格瓦片 | `app/services/raster_tile_service.py` | 窗口读 + 按 destination 分辨率 decimate（#595，低 zoom 不再解码 600MB）；全图统计 stretch 缓存（#410）；PNG LRU(2048)；最多 3 波段 |
| 工具结果缓存 | `app/lib/tool_cache.py` | Redis `cached_tool`（geocode 3600s / 空间分析 86400s）+ SET NX singleflight 锁 + 错误形态不写缓存（#694）+ 预算化 ref 扫描（#677） |
| ref descriptor | `app/services/session_data.py:81-90`、`app/api/routes/layer.py:341-409` | 存储时一次性计算 descriptor（to_thread），descriptor 端点永不 hydrate 全量 payload |
| 会话元数据 L1 | `app/services/session_data_redis.py:31-32,123-168` | map_state / metadata 两类 2s TTL 进程内 L1，写失效；命中重解析而非 deepcopy（#795） |
| 大 JSON 分块序列化 | `app/lib/geojson_serializer.py` | 顶层数组分批 to_thread 编码，事件循环间隙毫秒级（#427/#590） |
| 上下文装配 | `app/services/chat/context_assembler.py` | 元数据单 pipeline 拉取（PERF-08）、token 预算截断、会话尾部长度上限（C-F12）、`_sessions` LRU |
| GIS 算法 | `app/lib/geo_analysis/*`、`app/services/spatial_quality_service.py` | 等时圈 cKDTree + 可达邻接扫描（#443，实测 7.2s→修复）；Gi* 全向量化（S40）；nearest_neighbor cKDTree（S40）；质量检查 STRtree + buffered query（#597）+ 逐要素预算；`cursors_by_call` 单遍分组（#796） |
| 前端渲染 | `frontend/lib/map-kit/renderer.ts`、`frontend/lib/mapspec-runtime/runtime.ts` | F31 setData 引用跳过；视口要素过滤（Phase 8）；MapSpecRuntime diff/patch 增量 reconcile；token rAF 批量（TokenBatcher）；viewport 写 100ms debounce；ThematicLegend/MapDecorations React.memo；结果登记簿 MAX_RESULTS=50 + 重载荷键剥离；diff 在 Web Worker（`frontend/lib/mapspec-compiler/worker-bridge.ts`）；job-center 有界轮询 |
| 性能门禁 | `tests/benchmarks/`（baselines.json、_baseline_policy.py、perf marker） | 回归有基线锁定 |

结论：三轮审计确实清掉了绝大多数经典问题（iterrows、O(n²) sjoin、循环内 to_crs、事件循环上大序列化、瓦片风暴等均已修）。以下为**仍然存在**的问题。

## 2. 发现的问题

### P-1 [P1] 会话 ref payload 无进程内读缓存：每次工具解引用全量 Redis GET + json.loads 多 MB 数据
- **问题描述**: `ref:` 提货券每次被工具参数引用时都执行一次完整的 payload 读取：Redis `GET`（11MB 级）+ WATCH/MULTI TTL 刷新 pipeline + `json.loads` 全量解析。进程内 L1 只覆盖 `map_state`/`metadata` 两类（`session_data_redis.py` 中 `_l1_get/_l1_put` 仅 4 处调用：624/655/966/1029），**不含 payload**。内存后端同病：每次 `get()` 做 `copy.deepcopy`（#799 已下线程但成本仍在）。
- **影响范围**: Agent 链式分析是最高频路径——"buffer(ref:A) → intersect(ref:B) → dissolve(ref:C)" 每步解引用都重读重解析。微基准（/tmp/bench_feature_click.py，本机 .venv）：50k Point 要素 ≈ 10.9MB，`json.loads` 171ms/次。一个 5 步工具链的 turn ≈ 0.9s 纯重复解析 CPU + ~55MB Redis 出流量；同一 ref 在同一 turn 被两个工具使用则翻倍。100k 要素翻一倍。
- **代码位置**: `app/tools/registry.py:1062`（`data = await session_data_manager.get(session_id, node)`，每次 dispatch 每个引用叶子）；`app/services/session_data_redis.py:448`（全量 GET）、`457-474`（每次读附带 WATCH/MULTI pipeline）、`486`（json.loads）；`app/services/session_data.py:181`（内存后端 deepcopy）
- **原因分析**: 解引用成本 O(payload bytes)，无 per-turn/per-session 失效协议的解析缓存。数据不可变性其实有保障：`overwrite`/`delete_ref`/LRU 淘汰已经会调 `spatial_index_cache.invalidate_ref` + `tile_lru_cache.invalidate_ref`（session_data.py:107-109、125-127、73-75），同样的钩子可以挂 payload 缓存。
- **优化方案**: 在 SessionStore 增加 per-(session, ref) 的**已解析对象**短 TTL LRU（复用 `_l1` 机制或对齐 `spatial_index_cache` 的字节上限策略），在同一批失效点（overwrite/delete_ref/evict/clear_session）一并失效；命中返回同一对象（工具侧约定不就地改 payload，`get()` 的 deepcopy 语义可由"写时复制"替代或保留浅引用+只读约定）。注意跨副本失效：TTL 取 2-5s 或以 store 时写入的 revision 号做键。
- **验证方式**: `pytest tests/benchmarks/ -k ref -x`；新增基准：同 turn 内对同一 ref 连续 `registry.dispatch` 两次，断言第二次 Redis payload GET 次数为 0（可用 fakeredis 统计命令数）；`pytest tests/test_buffer_caching.py tests/test_cap_eviction_522.py` 回归。

### P-2 [P2] POI 点击"单要素回填"在服务端全量拉取+解析整个图层，再 O(n) 扫描找一个要素
- **问题描述**: MVT 图层（≥5000 要素，`frontend/lib/store/layer-data.ts:15,30-37`）点击要素后，前端调 `/layers/data/{ref}/feature/{fid}`；服务端先 `get_ref_data` 把**整个 FeatureCollection**（Redis GET + json.loads）拉进来，然后 `_find_feature_by_id` 线性扫全部要素（每要素查 `feature.id` + 7 个属性键）返回 1 个要素。
- **影响范围**: 微基准（/tmp/bench_feature_click.py）：50k 要素 = 10.9MB → json.loads 171ms + 最坏线性扫 29ms ≈ **每次点击 ~200ms CPU + 11MB Redis 流量**。点击是高频交互；换一个要素再点一次全部重来（无缓存，同 P-1）。MVT 瓦片路径的 `spatial_index_cache` 里明明已驻留同一 ref 的 features 列表（含内存估算与失效联动），却没被该端点复用。
- **代码位置**: `app/api/routes/layer.py:115`（get_ref_data 全量）、`124`（to_thread 扫描）、`83-98`（`_find_feature_by_id` O(n)×8 键）；前端触发链 `frontend/lib/store/layer-data.ts:100-108`、`frontend/components/map/map-panel.tsx:717`
- **原因分析**: 查找无索引；且不复用瓦片路径已有的进程内索引。复杂度 O(features) CPU + O(payload) IO，仅为取 1 个要素。
- **优化方案**: ① 端点先查 `spatial_index_cache.get((session_id, ref_id))`，命中直接在其 `features` 列表上扫（省 Redis GET + json.loads）；② 在 `SpatialIndexEntry` 或 descriptor 侧建 `id→index` 字典（构建索引时一遍 `collect_filterable_fields` 已经扫过 id 键），查找 O(1)；③ 兜底才走全量 get（并受益于 P-1 的缓存）。
- **验证方式**: `pytest tests/ -k "feature and layer" -x`；curl 计时对比：`time curl -H "X-Session-Token: ..." "http://localhost:8000/api/v1/layers/data/<ref>/feature/<fid>?session_id=..."`，50k 要素层修复前后应从 ~200ms 降到 <10ms（缓存命中）。

### P-3 [P2] harness 评估对 map_actions 的逐 tool_call 线性过滤是 O(n²)（#796 只修了 cursors，同型问题残留），且评估在事件循环上按 observation/ACK 重跑
- **问题描述**: `evaluate_with_evidence` 的 per-tool-call 循环里，`map_actions=[a for a in self._map_action_evidence if a.tool_call_id == tcid]` 对每个 tool call 全量扫 evidence 列表。#796 已经把**完全相同形状**的 cursor 过滤改成了单遍 `cursors_by_call` 分组（755-758 行，注释实测 1000×1000 = 53ms），但 map_actions 这一处漏了。
- **影响范围**: 微基准（/tmp/bench_harness_loop.py）：1000 tool_calls × 1000 actions = **31.5ms 纯 Python 循环/次评估**，跑在事件循环上（`evaluate_cartographic_session` 由路由直接 await）。触发频率高：前端每次 MapSpec reconcile 后 POST cartographic-observation（`chat.py:1262`），每个 ACK 批次也触发（`chat.py:1371`）；而评估缓存 `_cartography_eval_cache` **只缓存终态结果**（`agent_pi_bridge.py:1113-1123`），非终态（repairing 中）每次都全量重跑，且 evidence hash 随每个新 ACK 变化必然 miss。
- **代码位置**: `app/lib/harness/pi_agent_harness.py:803-806`（嵌套过滤，外层循环 `:768`）；对照已修的 `:755-758`；FIFO 上限 `MAX_EVENTS=1000`（`:290`）使上界真实可达（长会话 1000 工具调用 × 每次 dispatch 记录的 issued 动作）
- **原因分析**: O(calls × actions) = 10^6 次属性比较；与 #796 修前完全同型。
- **优化方案**: 复制 cursors_by_call 模式——循环前一遍 `actions_by_call: Dict[str, list] = {}` 分组，循环内 `actions_by_call.get(tcid, [])`。一处改动，~10 行。顺手可把 `_interaction_section`（:944-953）保持不变。
- **验证方式**: `pytest tests/benchmarks/test_dispatch_stall_perf.py tests/perf -x -k harness`；新增 micro-test：构造 1000 calls + 1000 actions 断言 `evaluate_with_evidence` 阶段耗时 < 5ms（修复前 ~31ms）。复跑 `/tmp/bench_harness_loop.py` 验证分组版本。

### P-4 [P2] scenario_compare 串行 await 评估 N 个方案，每个方案含 geocode/RAG 证据链网络往返
- **问题描述**: 多方案对比对 scenarios 列表逐个 `await engine.evaluate_decision(...)`。单个方案内部含 `target_resolver.resolve`（地理编码网络调用）+ `build_evidence_chain`（向量 RAG 检索）+ 规则匹配 + SessionStore 写，各方案相互独立。
- **影响范围**: 用户工具（tier 3，wall-clock 直接计入 turn 时长）。3 个方案 × 每个约 1-4s（geocode RTT + RAG 检索 + 存储写）= 串行 3-12s；gather 后 ≈ max(单方案) ≈ 1-4s，节省 ~60-75%。
- **代码位置**: `app/tools/spatial_decision_tools.py:153-175`（`for item in scenarios: ... res = await engine.evaluate_decision(...)`）
- **原因分析**: 串行 await 链；无共享可变状态（每个 decision_id/scenario_id 独立，store 写按 session 并发安全——session store 各方法自带锁/WATCH）。
- **优化方案**: `results = await asyncio.gather(*[engine.evaluate_decision(...) for item in scenarios], return_exception=True)`，异常方案降级为错误项（与现语义一致——现在单方案异常会被外层 try 整个吞掉返回 error，gather 反而能提高鲁棒性）。注意加 `asyncio.Semaphore(4)` 防外部 API（geocode/RAG）突并发。
- **验证方式**: `pytest tests/ -k scenario_compare -x`；monkeypatch `evaluate_decision` 为 `asyncio.sleep(0.5)`，断言 3 方案总时长 < 1s（串行为 ~1.5s）。

### P-5 [P2] 栅格瓦片路由每瓦片都做 DB 鉴权 + Redis ref 拉取，PNG LRU 拦不住，且无 ETag/single-flight（MVT 路径全有）
- **问题描述**: MVT 路由的顺序是"tile LRU → single-flight → 仅索引缺失才拉 ref"；栅格路由对每个瓦片请求都先 `get_ref_data`（内部：metadata L1/deepcopy + owner 校验 + payload GET + WATCH/MULTI）+ `validate_data_path`，然后才进 `render_raster_tile` 的进程内 PNG LRU。PNG LRU 命中时这些上游成本照样付。
- **影响范围**: 一次平移/缩放触发 20-40 个瓦片请求 = 20-40 × (JWT + DB 会话查询 + metadata 读[2s L1 过期后为 4 命令 pipeline] + payload GET + WATCH/MULTI)。估算每瓦片 5-15ms 服务端开销，一屏 100-600ms 分散在事件循环/线程池上；多用户同时浏览栅格层时放大。无 ETag → max-age 过期后全量重传 PNG。
- **代码位置**: `app/api/routes/layer.py:431`（每瓦片 get_ref_data）、`449`（每瓦片 validate_data_path）、`454-462`（响应头无 ETag、无 single-flight）；对照 MVT 路径 `:228-246`
- **原因分析**: 栅格 ref payload 虽小（file_path dict），但读取协议昂贵（metadata + WATCH/MULTI pipeline）；路径校验纯 CPU 重复；且鉴权 DB 查询（`require_owned_session` → `get_session_meta`）每瓦片一次（MVT 同样有，但 MVT 有 LRU-first 且瓦片命中后无其它 IO）。
- **优化方案**: ① 路由开头先查 `_RASTER_TILE_CACHE`（把 `render_raster_tile` 的 key/GET 提为模块级 `get_cached_raster_tile(path,z,x,y)`），命中直接返回，跳过 ref 拉取与路径校验（鉴权保留）；② ref 拉取结果按 session 短 TTL 缓存或改用 `ref_exists + get_ref_descriptor_authorized` 拿 file_path；③ 响应加 ETag（PNG bytes sha256 前 16 位）+ If-None-Match 304，对齐 MVT 的 `_tile_response`。
- **验证方式**: `pytest tests/ -k raster_tile -x`；连续两次 curl 同一瓦片 URL，第二次应 <2ms 且服务端日志无 Redis/DB 行；`pytest tests/benchmarks/test_transport_perf.py`。

### P-6 [P3] cartographic observation/ACK 处理链单请求内 3 次 get_map_state，L1 命中仍全字段重解析，写后即失效
- **问题描述**: 一次 observation POST 内：`chat.py:1177` 先 `invalidate_local_cache`，`1178` 读 map_state（冷：HGETALL+解析，回填 L1）→ `:1251` `set_map_state(_cartographic_observation)` 写失效 L1 → `_hydrate_cartographic_harness`（`agent_pi_bridge.py:779`）再冷读一次 → `_evaluate_cartographic_session_unlocked`（`:1050`）第三次读（L1 命中但仍在线程里把所有字段重新 `json.loads`，`session_data_redis.py:632/658-` `_parse_state_fields_sync`）→ 结尾 `set_map_state(_cartographic_review)`（`:1136`）再失效。1MiB 级 mapspec 字段时每次冷读/重解析 10-30ms 线程 CPU + 2 次 HGETALL RTT。
- **影响范围**: 每次前端 reconcile 后的 observation + 每个 ACK 批次；制图活跃会话每 turn 数次到十数次。单独看是几十 ms 级，叠加 P-3 的循环构成该端点的总延迟。
- **代码位置**: `app/api/routes/chat.py:1177-1178,1251`；`app/agent_pi_bridge.py:779,1050,1054,1136`；`app/services/session_data_redis.py:624-656`
- **原因分析**: 三个函数各自独立读状态（hydrate/evaluate/persist），无请求级共享快照；L1 设计为"写即失效"导致写后自废。
- **优化方案**: observation 处理器在读锁范围内读一次 map_state 并作为参数传入 hydrate/evaluate（两者只读），评估结果与 observation 一次 pipeline 写回（合并两次 set_map_state）；或 evaluate 内部对 state 用 `#795` 的 raw-bytes 缓存共享（同一 raw_items 解析一次）。
- **验证方式**: `pytest tests/ -k cartographic -x`；fakeredis 统计一次 observation POST 的 HGETALL 次数，断言 ≤1（现值 2-3）。

### P-7 [P3] 数据面 GeoJSON 响应 pretty-print（indent=2）放大 ~1.8x，应用层无 gzip
- **问题描述**: `serialize_geojson` 契约固定 `json.dumps(..., ensure_ascii=False, indent=2)`（字节一致性有测试锁定）。对 50k 点要素层：compact 11MB → pretty 20MB（实测 1.8x，面/线要素比值更高）。`app` 未注册 `GZipMiddleware`（`app/main.py:344-382` 只有 RateLimit/CORS/Correlation），gzip 只存在于 prod nginx（`deploy/nginx/nginx.conf:66-71`）——dev / 直连 uvicorn / docker-compose.yml（无 nginx 服务）下多 MB JSON 裸传。前端 `apiFetch` 只做 `JSON.parse`，空白完全无用。
- **影响范围**: `/layers/data/{ref_id}` 全量 hydrate（MVT 层过滤/属性表/导出时触发，前端 `layer-data.ts:132-140`）：一次 20MB pretty 传输 + 双倍序列化 CPU；feature 端点同理。prod 有 nginx gzip 兜底体积，但序列化 CPU（indent 模式更慢）与 dev 体验仍在。
- **代码位置**: `app/lib/geojson_serializer.py:25`（`_dumps_pretty` indent=2）；`app/api/routes/layer.py:69,128`（Response 无 Content-Encoding）；`app/main.py`（无 GZipMiddleware）
- **原因分析**: 历史契约（tests/test_event_loop_offload_427.py 锁定 indent=2 字节形态）延续到性能敏感的数据面。
- **优化方案**: 数据面端点改 compact 序列化（`separators=(",", ":")`，分块逻辑不变），保留 pretty 仅用于人工调试端点；同时注册 `GZipMiddleware(minimum_size=1024)` 使无 nginx 部署也压缩。更新字节一致性测试为 compact 契约。
- **验证方式**: `pytest tests/test_event_loop_offload_427.py -x`（改契约后）；`curl -s -H 'Accept-Encoding: gzip' -o /dev/null -w '%{size_download}' http://localhost:8000/api/v1/layers/data/<ref>...` 前后对比（应 ~11MB→~11MB compact，gzip 后 <1MB，实测 pretty gzip 0.01x 说明 gzip 本身效果极好）。

### P-8 [P3] 分析结果构造普遍走 `json.loads(gdf.to_json())` 字符串往返，序列化成本 ~1.5x
- **问题描述**: 11 处把 GeoDataFrame 先 `to_json()`（C 序列化成字符串）再 `json.loads` 解析回 dict——多一次全量字符串物化 + 一次全量解析。50k 要素结果 to_json ≈ 0.5-1s，loads 再加 30-50%。
- **影响范围**: 所有空间分析工具的结果收尾（线程内、一次性），叠加在用户可感知的工具总时延上；与 P-1 的重复解析同族但发生在出参侧。
- **代码位置**: `app/services/spatial_analyzer.py:372`；`app/lib/geo_processor/geometry.py:89,155,209`；`app/lib/geo_processor/overlay.py:65`；`app/services/local_osm.py:249`；`app/services/local_poi.py:784`；`app/tools/local_admin.py:139` 等（grep `json.loads(.*to_json()` 共 11 处）
- **原因分析**: geopandas 旧版无直接 to-dict API 的历史习惯；shapely 2.x 下 `gdf.iterfeatures()` / `to_geo_dict()`（geopandas ≥1.0 / `gdf.__geo_interface__`）可直接产出 dict。
- **优化方案**: 统一 helper：`def gdf_to_fc(gdf): return {"type":"FeatureCollection","features":[dict(f) for f in gdf.iterfeatures()]}`（iterfeatures 已含 properties/geometry 映射，无字符串中间态）；逐点替换 11 处。
- **验证方式**: `pytest tests/cartography tests/test_spatial_analyzer*.py -x`（结果形状回归）；基准 `pytest tests/benchmarks/bench_gis_perf_539_540.py` 前后对比。

### P-9 [P3] 前端 map-panel 受控 viewState：地图移动期间 MapPanel 每帧（~60fps）整组件重渲染
- **问题描述**: `<Map {...viewState} onMove={handleMove}>` 是 react-map-gl 的"受控"用法——每次 move 事件 `setViewState` 触发 MapPanel 函数组件全量 re-render（~120 个 vdom 节点 + children 协调），移动全程 60fps 连续发生。`viewStateRef` 已经无成本跟踪最新值，受控回灌唯一的消费者就是 Map 组件本身（MapLibre 早已自己移动到位），属冗余反馈环。react-map-gl 官方对"无需程序化控制相机"的场景明确推荐非受控（initialViewState）模式。
- **影响范围**: 桌面端 ~0.5-1ms/帧、低端设备 2-4ms/帧 的 React 开销叠加在瓦片渲染关键期；children 大多已 memo（MapActionHandler、ThematicLegend、MapDecorations）所以症状温和，但 MapPanel 自身 JSX + PoiInfoPanel/Popup 条件分支每帧协调。
- **代码位置**: `frontend/components/map/map-panel.tsx:133`（useState viewState）、`942-944`（handleMove 内 setViewState 每事件）、`1084-1096`（`{...viewState}` 受控回灌）
- **原因分析**: 受控模式反馈环：onMove → setState → re-render → 相同 props 回 Map。MapLibre 实例自身持有真相，回灌是恒等更新。
- **优化方案**: 改非受控：`<Map initialViewState={DEFAULT_VIEW_STATE}>`，onMove 只写 `viewStateRef` + debounce 结算（现有逻辑不变）；程序化移动（focusLayer fitBounds、AI set_view）本就走 mapRef/命令通道，不受影响。需回归 basemap 切换/会话恢复时 initialViewState 的应用时机。
- **验证方式**: `cd frontend && npx vitest run test/map-render-work-count.test.tsx test/page.render-scope.test.tsx`；React Profiler 或 `test/map-render-work-count.test.tsx` 增加"move 事件不触发 MapPanel render"断言（模拟 10 次 onMove，断言渲染计数为 0/1）。

---
### 排序总览

| # | 级别 | 一句话 | 类型 |
|---|---|---|---|
| P-1 | P1 | ref payload 每次解引用全量拉取+解析（11MB/171ms/次，链式工具 ×N） | 后端·缓存覆盖 |
| P-2 | P2 | POI 点击单要素回填全量取图层再 O(n) 扫描（~200ms/次点击） | 后端·数据读取 |
| P-3 | P2 | harness map_actions O(n²) 过滤（31.5ms/次评估，#796 同型残留）+ 非终态无缓存 | 后端·算法 |
| P-4 | P2 | scenario_compare 串行 await（每方案 geocode+RAG，3 方案 3-12s） | 后端·并发 |
| P-5 | P2 | 栅格瓦片每请求鉴权+ref 拉取绕过 PNG LRU，无 ETag/single-flight | 后端·缓存覆盖 |
| P-6 | P3 | observation/ACK 链单请求 3× get_map_state、写后 L1 自废 | 后端·重复读取 |
| P-7 | P3 | 数据面 pretty JSON 1.8x + 应用层无 gzip（dev 裸传 20MB） | 后端·序列化 |
| P-8 | P3 | 11 处 `json.loads(gdf.to_json())` 往返（出参序列化 ~1.5x） | GIS·GeoJSON 往返 |
| P-9 | P3 | 受控 viewState 致移动期 60fps MapPanel 重渲染 | 前端·渲染 |

微基准脚本（只读验证，未改动仓库）: `/tmp/bench_harness_loop.py`、`/tmp/bench_feature_click.py`。
