# 空间数据 Fetch-on-Demand(按需拉取)机制

描述工具产出的超大空间数据如何以引用(`ref:geojson-xxxx`)形式绕过 LLM 上下文与 SSE 通道,由前端按需拉取并直接交给 MapLibre 渲染;含 MVT 矢量瓦片显示路径的现状说明。

> **版本**: v0.1.3 · **状态**: 活文档 · **最后更新**: 2026-09-02

## 1. 机制动机:上下文防爆

一次城市级 POI 检索或空间叠加运算可能产出上万要素、数十 MB 的 GeoJSON FeatureCollection。如果这份负载直接进入工具返回值,会产生两类问题:

- **LLM 上下文爆炸**:工具结果会拼进下一轮对话的上下文,大负载触发截断、成本激增,多轮会话迅速劣化。
- **SSE 通道过载**:聊天流是逐 token 的 `text/event-stream`,把大 JSON 塞进流里会导致长连接卡死、网关超时。

Fetch-on-Demand 的解法是**引用剥离**:数据本体存进服务端会话存储,只把一个短引用 ID 和摘要元数据放进 LLM 上下文与 SSE 流;数据本体由前端在流外用独立 HTTP 请求按需拉取。相关决策见 ADR-0001(fetch-on-demand)。

需要说明:该机制保护的是 **LLM 上下文与聊天流**。浏览器端显示路径的进一步优化(超大图层不再整包下发 GeoJSON,改走 MVT 瓦片)已在 v0.1.3 落地,见第 5 节。

## 2. 端到端时序

| 阶段 | 位置 | 动作 |
|---|---|---|
| 1. 生成与落存 | 后端工具 / Celery 任务 | 地理工具抓取或计算得到大型 FeatureCollection 后,不把明文实体抛给函数外,而是调用 `session_data_manager.store(session_id, data, prefix="geojson")` |
| 2. 引用签发 | `app/services/session_data.py` | store 生成全局唯一引用 `ref:geojson-<16位hex>`(64 bit 熵,ref_id + session_id 共同构成难以枚举的能力令牌),并在落存时同步计算轻量描述符(descriptor) |
| 3. 轻量通信 | ChatEngine 执行回路 | 工具只向 LLM 返回摘要与引用壳(如 `{layer_id: "ref:geojson-...", count: 50000}`);LLM 据此组织回复 |
| 4. SSE 下发提货码 | `app/services/chat/execution_engine.py` | 工具结果的 SSE `tool_result` 事件携带 `geojson_ref` 与 `ref_descriptor`(要素数、bbox、几何类型、是否可 MVT 化等) |
| 5. 前端挂图层 | `frontend/lib/hooks/use-sse-stream.ts` | 识别 `geojson_ref` 后向 HUD store 注册图层(id 即 ref_id,携带 `_refId`/`_tileUrl`/`_descriptor`);小数据在此并行发起数据拉取 |
| 6. 数据提货 | `frontend/lib/api/`(apiFetch) | `GET /api/v1/layers/data/{ref_id}?session_id=...` 独立 HTTP 请求取回 GeoJSON 本体,匿名会话附带 `X-Session-Token`(owner_token) |
| 7. 原生渲染 | MapLibre GL JS | 数据交给 MapLibre 原生 source/layer 绘制(`frontend/components/map/map-action-handler.tsx` 执行地图命令目录);大数据图层由 `_tileUrl` 走 MVT 瓦片源,不整包进浏览器 |

数据本体全程不经过 LLM、不进聊天流;前端取件失败只影响该图层,不阻塞对话。

## 3. 会话数据生命周期

存储后端由工厂 `create_session_data_manager()` 决定(`app/services/session_data.py`):

- **默认 Redis 后端**(`USE_REDIS=true`,`RedisSessionDataManager`,连接 `REDIS_URL`)——支持多进程/重启存活;
- **内存兜底**(`MemorySessionStore`)——redis 库缺失或构造异常时回退,数据随进程重启丢失。

每个 session 的关键语义:

- **LRU 上限 200 条引用**:超出按最久未访问淘汰;淘汰/覆写时同步失效该 ref 的 MVT 空间索引与瓦片缓存(`app/services/mvt.py` 的 `spatial_index_cache`/`tile_lru_cache`)。
- **别名机制**:`set_alias`/`resolve_alias` 支持用语义名(如 `plan-current`)寻址 ref,LLM 与工具可以引用别名。
- **描述符预计算**:落存时算好 `feature_count / point_count / geometry_types / bbox / mvt_capable / estimated_bytes`(`app/schemas/ref_descriptor.py`),后续元数据读取 O(1),不再扫描 10 万要素。
- **空闲会话清理**:`cleanup_idle_sessions(max_sessions=100)` 按最后触达淘汰溢出会话;`clear_session` 同时回收该会话的 MapSpec 磁盘状态(revisions/checkpoints/栅格 PNG)。
- **隔离**:所有读写以 `session_id` 为命名空间;取数端点经 `require_owned_session` 校验会话归属,匿名会话还需匹配 owner_token(恒时比较)。

用户主动上传的文件不走这套会话缓存,而是落在数据目录(`DATA_DIR`,dev compose 中为 `uploads` 卷),以路径引用的方式被工具消费——存算分离,与会话层互不替代。

## 4. API 端点

全部位于 `app/api/routes/layer.py`,鉴权一致(`require_owned_session` + 可选 `X-Session-Token` 头):

| 端点 | 返回 | 说明 |
|------|------|------|
| `GET /api/v1/layers/data/{ref_id}?session_id=...` | `application/json` 原始 GeoJSON | 提货端点;支持 ref 或别名 |
| `GET /api/v1/layers/data/{ref_id}/tiles/{z}/{x}/{y}.mvt?session_id=...` | `application/vnd.mapbox-vector-tile`(gzip) | MVT 矢量瓦片,大图层显示路径(第 5 节) |
| `GET /api/v1/layers/data/{ref_id}/raster-tiles/{z}/{x}/{y}.png?session_id=...` | `image/png` | 栅格图层的 XYZ 瓦片(路径经 `validate_data_path` 校验,防跨会话读文件) |
| `GET /api/v1/layers/descriptor/{ref_id}?session_id=...` | JSON 元数据 | 轻量描述符,不触水负载本体 |

错误语义:权限不匹配返回 403;ref 已被 LRU 淘汰或不存在返回 404。前端对 404 的处理是保留占位图层并记录错误日志,由用户决定是否让 Agent 重新发起检索。

## 5. MVT 现状(已落地,非规划)

ADR-0047(Data Plane: MVT vector tiles)已实施,当前行为:

- **触发阈值**:前端 `use-sse-stream.ts` 中 `VECTOR_TILE_THRESHOLD = 5000`。有描述符、`mvt_capable` 且要素数 **> 5000** 的图层不再整包拉取 GeoJSON,改由 `_tileUrl` 模板让 MapLibre 按视口请求 MVT 瓦片;不满足条件(无描述符、不可瓦片化如纯栅格、或要素数 <= 5000)仍走内联 GeoJSON 全量拉取 + 视口裁剪。
- **几何支持**:`app/services/mvt.py` 为纯标准库 MVT 2.1 编码器(V2),支持 Point/MultiPoint/LineString/MultiLineString/Polygon/MultiPolygon(正确绕向与孔,z < 14 做简化);GeometryCollection 不支持。
- **服务端缓存**:瓦片 LRU 直接命中则零开销返回;未命中经 single-flight 去重并发请求后,在 `asyncio.to_thread` 中做空间索引查询 + 编码 + gzip;空间索引按 `(session_id, ref_id)` 常驻 LRU,同一份大数据只解析一次。
- **HTTP 语义**:响应 gzip 压缩,携带 ETag(sha256 前 16 位)并支持 `If-None-Match` 条件请求(304);`Cache-Control: private, max-age=30`（overwrite/rollback 可同 ref 覆写，短 TTL；ETag 保 304）。
- **凭据注入**:MapLibre 原生瓦片请求无法携带自定义 header,前端经 `transformRequest`(`frontend/lib/map-kit/tile-auth.ts`)实时注入会话凭据——匿名会话注入 `X-Session-Token: <owner_token>`,登录会话注入 Bearer token。
- **收益实测**(ADR-0047 记录):10 万 POI 城市视口,约 26 MB 原始 / 2.5 MB gzip 的 GeoJSON,替换为 4 张共 22 KiB 的 MVT 瓦片;浏览器端 `JSON.parse` 卡顿与 HUD store 大对象驻留同时消失。

## 6. 与普通小数据的区别

| | 小结果(如 50 个 POI) | 大结果(如 5 万要素) |
|---|---|---|
| LLM 上下文 | 直接内联(受工具结果的 slim 策略约束) | 只见引用壳 + 摘要计数 |
| SSE 事件 | 可内联完整数据 | 只携带 `geojson_ref` + `ref_descriptor` |
| 前端获取 | 一次 `GET /layers/data/{ref}` 全量拉取 | 要素数 <= 5000 同左;> 5000 走 MVT 瓦片按视口拉取 |
| 渲染 | GeoJSON source + 视口裁剪 | MapLibre vector tile source(`source-layer: "data"`) |
| 服务端成本 | 无特殊处理 | 描述符预计算、空间索引常驻、瓦片 LRU、single-flight |

写新工具时的纪律:任何可能产出大 FeatureCollection 的工具必须走 `session_data_manager.store()` 转存为引用,禁止把全量数据塞进工具返回值。

## 相关文档

- [ADR-0001 Fetch-on-Demand](adr/0001-fetch-on-demand.md) · [ADR-0047 Data Plane MVT](adr/0047-data-plane-mvt-tiles.md)
- [本地开发手册](SETUP_INSTRUCTIONS.md) · [技术方案说明书](技术方案说明书.md)
