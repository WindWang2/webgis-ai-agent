# ADR-0217: 大数据数据平面 vNext — 目录瓦片服务化、窗口分页与断连真取消

- 状态: Proposed（W12 波次）
- 日期: 2026-09-29
- 关联: ADR-0047（data-plane MVT）、ADR-0214（render runtime & data plane，其
  "明确不做 MVT 重写" 的后续载体）、ADR-0094（Data Fabric）、issue #1545（已由
  PR #1563 完成所有权下沉的最小半步，本 ADR 是完整的所有权/分页/取消闭环）

## 背景

F13 建立了会话 ref 路径的 MVT 显示管线（STRtree/single-flight/字节 LRU/epoch
失效），但数据平面仍有五个结构性缺口：

1. **FC 端点 ETag/304 回归**：`tests/test_layer_fc_etag.py` 在 master 红 ——
   ETag 块在某次重构中丢失，且 gzip 分支缺 `mtime=0`（ETag 逐秒漂移，
   条件再验证在生产永不命中）。
2. **目录瓦片的 route 越权**：`data_fabric.py` 路由内直接
   `PostGISAdapter(conn_profile)` 构建，绕过 governed resolution
   （registry/健康/secret 分离门）；非 PostGIS 源 422 硬拒绝 —— 12 类
   adapter 的矢量目录项没有瓦片显示路径。
3. **目录瓦片缓存缺陷**：`DfTileCache` 字节记账用 `len((gz, fp))` —— 恒为 2，
   256MB 预算从未生效；无 single-flight（会话路径有，目录路径惊群）。
4. **浏览半边缺失**：ADR-0047 的显示路径（MVT）之外，属性/小批浏览只有
   「整包 FC」一条路 —— 2 万～10 万要素的 inline-only ref 被前端拒绝挂载，
   目录 keyset cursor 管线存在但零 REST 消费；`stream_catalog_item_features`
   （有界队列+背压+取消）零 REST 消费。
5. **断连不取消**：`cancel_token` 参数存在但无路由传参；handler task 被取消时
   manager-loop 上的协程白跑到 600s 超时。

## 决策

### D1: 目录瓦片服务化（capability 驱动双路径）

`app/services/data_fabric/tile_service.py` 是目录瓦片的唯一事实源；route 只做
鉴权（先于缓存，API-01）与 ETag/304/HTTP 映射。

- `serve_mvt_tile` 能力存在（PostGIS ST_AsMVT）→ server_mvt 路径，行为与
  旧路由逐字节一致；
- 其余矢量源 → python_fallback：视口瓦片 → 经纬度包络
  （`tile_identity.tile_bounds_lonlat`）→ governed adapter 的 bbox 有界查询
  （`TIER_SCAN_CAP_FEATURES`=20k 帽 + 本地再截断，不信任远端 limit）→
  `mvt.encode_tile` 纯 Python 编码。可选高性能实现（PostGIS）与纯 Python
  fallback 并存，与 ADR-0047「MVT 为 additive 显示路径」一致。

### D2: 统一缓存身份语义（tile_identity.py）

目录 MVT 键 = `(item_id, tenant_scope, data_fingerprint, z, x, y)`；ETag =
`sha256(gz + fingerprint)[:16]`（fingerprint 进摘要，版本参与身份）。四条数据
面身份方案（目录 MVT / 会话 MVT / FC / 栅格）的推导纪律集中文档化于
`tile_identity.py`：内容寻址、确定性 gzip（mtime=0）、鉴权先于缓存、数据版本
变化必须对外可见。缓存失效跟**数据版本**（fingerprint 变化 → 新键 + sync 主动
invalidate），与 route 生命周期无关。

### D3: 字节记账真源 + 构建收敛

`TileCacheEntry(NamedTuple)`：字节记账唯一真源 = gzip 载荷长度（裸 tuple 兼容
保留）；`max_entry_bytes=4MB` 单条上限（超限拒绝入缓存、诚实降级仍服务）；
`stats()` 暴露 hit/miss/eviction/oversize。`TileBuildCoalescer`（df-manager-loop
上的 asyncio single-flight）：同键并发合并为一次构建；失败逐份传播、失败不缓
存；在飞上限（512）与等待超时（30s）后诚实自建降级，绝不悬挂。瓦片响应携带
`X-Tile-Cache`（hit/miss/bypass）与 `X-Tile-Source`（cache/server_mvt/
python_fallback）观测头（无租户/凭据内容，向已认证调用者披露缓存状态）。

### D4: 浏览半边 —— 窗口/keyset 分页

- **会话 ref**：`GET /layers/data/{ref}/features`。稳定序 = FC 自然序（单个
  content_revision 内不可变，#1112 修复语义）；cursor = 不透明 b64 下标（服务
  端零翻页状态，断线零清理）；`v=<revision>` guard，不符 → 409 + 当前 revision
  （绝不静默跨版拼接页）；取数后重读 live revision（TOCTOU 收口）；bbox 前向
  扫描预算 10k/请求（有界 CPU、不漏要素）；fields 投影白名单 ≤64。
- **目录**：`GET /data-fabric/catalog/{item_id}/features`。复用 V2 pushdown
  管线的 CursorPage → adapter keyset（稳定序 = 排序键）；键集失效 → 400 typed
  （绝不静默错页）；源不支持 → `next_cursor=null/has_more=false` 诚实降级；
  响应携带 catalog fingerprint 供翻页中途数据改版检测。
- **流式**：`GET /data-fabric/catalog/{item_id}/features/stream`（NDJSON）。
  内存上界 = 泵有界队列（64）而非数据集总量；`limit` 封顶行数；终结诚实：
  `_eof` 尾行 + `limit_reached` 显式标注 + 源失败 `_error` 尾行；客户端断开
  静默终止（无 _eof = truncated）。

### D5: 断连真取消（取消链同步收敛）

- `_run_async_manager_cancellable`：handler task 被取消（客户端断开 → Starlette
  取消）经 `wrap_future` 传播为 `run_coroutine_threadsafe` future 的 cancel →
  manager-loop 协程在下一 await 点收到 CancelledError（阻塞线程受 adapter
  timeout 有界）；`db.close` 加 shield（二次取消不泄漏 session）。
- async generator 链全部**显式 aclose**：`async for` 在 GeneratorExit 时不会
  自动关闭内层 async generator，泵收尾会被拖到 GC —— 显式 aclose 让取消链同步
  收敛。
- 泵收尾顺序：stop → 有界等待泵线程真退出（`drain_done` 事件；`to_thread` 的
  task.cancel 只取消包装协程）→ close 上游迭代器。close 与 next() 并发的
  "generator already executing" 不再吞掉上游协作取消。

### D6: FC ETag/304 恢复

内容寻址 ETag（sha256[:16]）+ If-None-Match 304，gzip/plain 各表示可再验证，
gzip 分支 `mtime=0`（嵌入时间戳 → ETag 逐秒漂移 → 304 永不命中）。304 免整包
重传（会话重放/重复挂载/调度器条件再验证的传输预算）；内容变化 → ETag 失效为
完整 200，绝不吞真更新。

## 不变量（继承并钉死）

鉴权先于任何缓存/数据访问；tenant+fingerprint 必须入键；fingerprint 变化必须
失效/轮转；确定性 gzip；失效代际字节绝不回服务；fetch 失败 ≠ 空数据集（typed
502/422/413）；大 GeoJSON 不进 LLM/SSE agent 上下文；阈值锚点 5000/20000/50000
不动；事件循环卫生（sync ORM/编码/远端 I/O 全部 off-loop）；有界资源（队列/字
节帽/扫描预算/在飞上限）。

新增依赖方向守卫（AST 常驻门禁）：`app/services`、`app/extensions_platform`、
`app/lib` 绝不 import `app.api`（#1545 同族层倒置防线）。

## 后续方向（Out of Scope）

- UI 深度接线：属性表组件分页渲染（前端契约与客户端已交付：
  `frontend/lib/data-plane/paged-features.ts`）；
- PostGIS 目录瓦片的属性过滤下推（adapter 有意拒绝 pending plan-integrated
  tile filtering 设计）；
- 空瓦片（204）负缓存；
- MVT 编码的可选高性能实现（如 Rust/protobuf 库）—— 纯 Python 路径已满足
  当前预算（perf/budgets.json mvt_tile_p95 10ms 门禁持续监控）。
