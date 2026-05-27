# 工具层性能基础设施 — 设计文档

**日期**: 2026-05-27
**状态**: 已批准，待实现
**主题**: 给所有 FC 工具加上结果缓存、自动计时、响应裁剪三件套——一次基础设施投入，受益面覆盖 31 个工具

## 背景与动机

`/retro` 显示上周 64 次提交里有一条 `perf(session): coalesce Redis round-trips`——
说明性能问题已经开始浮现，但目前的修法是**逐点抢救**，没有共享的基础设施。
现状：

- `app/tools/` 下 31 个工具，重量级模块（`spatial_stats.py` 23KB、
  `osm.py` 20KB、`advanced_spatial.py` 22KB、`spatial.py` 16KB）每次调用都从零
  开始算
- **零缓存**——`grep` 整个 `app/tools/` 找不到任何 `lru_cache` / `@cache` /
  Redis 缓存调用。同一组参数的 heatmap、h3_binning、kde_contours 重复调用
  时全额付出 CPU 成本
- **零计时**——`time.perf_counter` 在 `app/` 里只出现在 `health.py` 和
  `viewport_naming.py` 两个地方，工具层完全没有时延数据。「哪个工具最慢」
  目前靠猜
- Celery 已经接好（`app/services/task_queue.py`），但 `_generate_heatmap`
  注释里写着 "without Celery"——说明背景任务路径存在但用得很少
- 响应载荷未裁剪。重 GeoJSON 直接出网线，序列化 / 网络 / 浏览器解析三段
  累计延迟肉眼可感

**症状（用户为先）**: heatmap、h3_binning、kde_contours、buffer 这一类工具
在用户眼里"要等几秒"，且服务端 CPU 在这些调用期间打满。

**本 spec 范围**: 单用户时延场景下的系统性基础设施。**不**改 Celery 路由、
**不**改单工具算法、**不**做输入采样——那是后续基于本 spec 收集到的数据再
做的工作。

## 设计原则

**三层独立、各自可选。** 缓存（per-tool 显式接入）、计时（自动、全工具）、
裁剪（工具作者主动调用的纯函数）。三层互不依赖；任意子集可用。

**Trim 是函数，不是装饰器。** 工具作者在 `return` 前显式调用
`trim_features(result)`——裁剪策略留在返回点（语义所在地），同时避开
「缓存到底缓存裁剪前还是裁剪后」的争论（答案：缓存的就是函数返回值，
即裁剪后）。

**TTL only，不做主动失效。** Cache invalidation 是著名难题；本 spec 不解决。
工具作者根据数据新鲜度需求选 TTL；需要更短失效窗口的工具自己降低 TTL 或
通过 `skip_if` 跳过缓存。

**结构化日志，不上 OpenTelemetry。** 一行 JSONL per call，加进程级 in-memory
聚合器。一行 `grep` 就能定位最慢的工具。需要分布式追踪时再单写 spec。

## 架构总览

```
┌──────────────────────────────────────────────────────────────────┐
│ chat_engine 派发工具调用                                          │
│      │                                                           │
│      ▼                                                           │
│ app/tools/registry.py                                            │
│      │  (自动在每次 dispatch 外面套 timing wrapper)              │
│      ▼                                                           │
│ ┌──────────────────────────────────────────────┐                 │
│ │  @cached_tool 装饰器  (per-tool 显式接入)    │                 │
│ │    1. make_cache_key(tool_name, args)        │                 │
│ │    2. redis.get → 命中? 返回缓存结果          │                 │
│ │    3. 未命中? 调用内层函数                    │                 │
│ │    4. redis.setex(ttl)                       │                 │
│ │    5. 返回结果                                │                 │
│ └──────────────────────────────────────────────┘                 │
│      │                                                           │
│      ▼                                                           │
│ 工具函数体（e.g. _generate_heatmap）                              │
│      │                                                           │
│      │  返回前: result = trim_features(result)                   │
│      │                          (opt-in helper)                  │
│      ▼                                                           │
│ registry 记录 duration_ms + arg_bytes + result_bytes              │
│      → tool_metrics.record_tool_call(...)                        │
│      → 每 100 次 / 进程退出时输出 digest 行                       │
└──────────────────────────────────────────────────────────────────┘
```

装饰器顺序（从外到内）: `@tool(...)` (registry 注册) → `@cached_tool(...)`
(缓存检查) → 函数体。registry 的 timing wrapper 在装饰器栈**之外**，所以
缓存命中和未命中都会被计时。

`@cached_tool` 通过 `inspect.iscoroutinefunction(func)` 判断内层函数是 async
还是 sync，分别生成 `async def` 或 `def` 包装层——支持两种形态共存，无需
工具作者关心。

## 组件

| 文件 | 状态 | 职责 |
|------|------|------|
| `app/lib/tool_cache.py` | **新增** | 缓存原语: `make_cache_key`、`get_cached`、`set_cached`。复用 `app/services/session_data_redis.py` 现有的 Redis 连接。 |
| `app/services/tool_metrics.py` | **新增** | 计时原语: `record_tool_call`、进程级聚合器（top-N 累计 / top-N p99）、`emit_digest()`。 |
| `app/tools/_utils.py` | 修改 | 导出 `@cached_tool(ttl=..., skip_if=...)` 装饰器 和 `trim_features(fc, max_features=5000, precision=6)` 帮助函数。 |
| `app/tools/registry.py` | 修改 | 每次 dispatch 外层加 timing wrapper，调用 `tool_metrics.record_tool_call`。通过 `contextvars` 检测当前调用是否命中缓存。 |
| `app/main.py` | 修改 | FastAPI lifespan shutdown 时调用 `tool_metrics.emit_digest()`，保证关机时落一份 top-N 总结。 |
| `tests/test_tool_cache.py` | **新增** | 缓存键规范化 + ref:xxx 跳过 + Redis 失败降级。 |
| `tests/test_tool_metrics.py` | **新增** | record + digest + 聚合器一致性。 |
| `tests/test_tool_trim.py` | **新增** | trim_features 各类边界。 |
| `tests/test_registry_timing.py` | **新增** | registry → metrics 整链。 |
| `tests/test_heatmap_caching.py` | **新增** | 端到端：相同参数第二次 <50ms 返回 + `_trim` 包络出现。 |
| `logs/tool_metrics.jsonl` | 运行时产物 | 10MB 轮转的 JSONL 时延日志，append-only。 |

## 数据流

```
chat_engine
  → registry.dispatch(tool_name, args)
    → [registry start_timer + 设 ctx cache_hit=False]
    → @cached_tool wrapper
      → make_cache_key(name, args)
      → 命中? 设 ctx cache_hit=True，直接返回 cached
      → 未命中: 调用内层函数
        → 函数体内: result = do_work(...); result = trim_features(result)
      → redis.setex(key, ttl, result)
      → 返回 result
    → [registry stop_timer]
    → tool_metrics.record_tool_call(name, arg_bytes, result_bytes,
                                     duration_ms, cache_hit, error)
    → 返回到 chat_engine
```

## 缓存键策略

```python
def make_cache_key(tool_name: str, args: dict) -> str:
    canonical = json.dumps(args, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(f"{tool_name}::{canonical}".encode()).hexdigest()[:16]
    return f"tool_cache:v1:{digest}"
```

- **sorted_keys + 无空格** 保证 `{a:1, b:2}` 和 `{b:2, a:1}` 算出相同 key。
- **sha256 前 16 字符**: 即使到百万级条目，碰撞概率 < 10^-9。Redis 内存占用低。
- **`v1` 命名空间** 让格式升级时一条 `SCAN | DEL` 即可整体失效。
- **跳过规则**（不缓存、每次都跑）:
  1. `args` 任意叶子值是以 `ref:` 开头的字符串——这种引用指向会话内可变
     数据，同一 `ref:` 不同时刻解析结果不同。
  2. 工具自带的 `@cached_tool(skip_if=...)` 谓词返回真值，例如
     `@cached_tool(skip_if=lambda a: a.get("realtime"))`。

## TTL 策略

- **默认 3600 秒（1 小时）**——长到能吸收会话内 LLM 重试和换种问法、短到
  陈旧数据最终会过期。
- **per-tool 覆盖** 通过 `@cached_tool(ttl=...)`。推荐预设:
  - 纯几何运算（buffer、simplify、intersection）: `ttl=86400`（1 天），
    确定性、无外部状态。
  - 外部服务工具（OSM、Nominatim、geocoding）: `ttl=3600`。
  - 读取用户可编辑 PostGIS 图层的工具: `ttl=600`（10 分钟），底下数据可能变。

## `trim_features` 契约

```python
def trim_features(fc: dict, max_features: int = 5000, precision: int = 6) -> dict:
    """
    返回一个 FeatureCollection:
      - features 最多 max_features 条（保留前 N 条——多数工具的输出顺序已经有意义）
      - 所有几何坐标四舍五入到 precision 位小数
      - 顶层多一个 "_trim" 键描述发生了什么
    输入不是 FC 时原样返回 + warning log。
    """
```

`_trim` 包络（仅在实际发生裁剪时出现）:

```json
{
  "type": "FeatureCollection",
  "features": [...],
  "_trim": {
    "original_count": 23000,
    "kept_count": 5000,
    "precision": 6,
    "reason": "max_features"
  }
}
```

- **precision 6** ≈ 赤道 10cm。任何实际地图缩放级别下视觉无差异。典型
  lat/lng 字符串体积砍 30-50%。
- **保留前 N 条而非随机采样。** 工具如果想要「最重要的 N 条」，自己在
  调用 trim 前先 sort。
- **顶层键，非嵌套。** GeoJSON 规范允许额外顶层键。前端可选择无视或
  渲染「显示 5000 / 23000」。

## 计时日志格式

每次工具调用一行 JSONL，写到 `logs/tool_metrics.jsonl`，10MB 轮转
（`logging.handlers.RotatingFileHandler`）:

```json
{"ts":"2026-05-27T14:23:01.412Z","tool":"heatmap_data","session_id":"abc123","arg_bytes":1234,"result_bytes":56789,"duration_ms":312,"cache_hit":false,"error":null}
```

工具名是 registry 注册时的裸名（例如 `heatmap_data`、`buffer_analysis`），
不带模块前缀——和 `@tool(registry, name="heatmap_data", ...)` 一致。

- **`arg_bytes` / `result_bytes`**: `len(json.dumps(...))`，便宜、足够精确。
- **`session_id`**: 来自 chat_engine 时存在，便于「这个会话很慢」型分析。
- **`error`**: 失败时记录异常类名，例如 `"TimeoutError"`，否则 `null`。失败
  也写一行——失败也会进 digest。

## 进程内 digest

`tool_metrics` 维护进程级聚合器:
`{tool_name → (count, total_ms, max_ms, hit_count, error_count)}`。
两个触发点输出 digest 行:

1. **每 100 次调用** 一次——长进程自我汇报。
2. **FastAPI lifespan shutdown** 时——最终快照。

digest 行格式:

```
TOOL_METRICS_DIGEST n=312 top_cumulative=[("heatmap_data",12480,38,5),("osm_fetch",6210,12,0)] top_p99=[("spatial_stats",4231),("kde_contours",3120)] errors=[("osm_fetch","TimeoutError",2)]
```

元组语义: `(tool_name, total_ms_or_max_ms, call_count, hit_count)`。可从
`journalctl` 或 FastAPI 日志文件 grep 出来。**不**新增 endpoint、**不**做
dashboard。

## 错误处理

| 失败场景 | 行为 |
|---------|------|
| Redis GET 失败（宕机、超时） | 限流地 log warning（模块级 last-warning 时间戳，每分钟最多一次），绕过缓存，调用内层函数返回结果。**不**尝试 SET。 |
| Redis SET 失败 | 限流 log warning，照常返回结果。用户请求**不能**因缓存写失败而失败。 |
| Cache key 生成抛异常（参数不可序列化） | log warning，本次跳过缓存，落到内层函数。 |
| 工具函数抛异常 | timing 行写入 `error=<exception-class>`，聚合器 error_count++，**重新抛出**。缓存层**不**吞异常。 |
| `trim_features` 收到非 FC 输入 | log warning，原样返回。防御式——绝不因为 trim 把工具弄崩。 |
| `tool_metrics` 写盘失败（磁盘满等） | stderr 落一行，丢掉这条记录。**不**阻塞工具调用。 |

## 并发

- 缓存 GET/SET 复用 `session_data_redis.py` 里的同步 `redis.Redis` 客户端。
  工具几乎都是同步的、跑在 `run_in_executor` 默认 ThreadPoolExecutor 里，
  所以 Redis 调用也跟着在 executor 线程内同步发出——和 `RedisSessionDataManager`
  现行模式一致，**不**新增 async Redis 依赖。
- 聚合器更新用 `threading.Lock`（无竞争时极便宜，但 executor 线程需要它）。
- **不**做缓存击穿防护（cache stampede）。10 个并发相同未命中都跑——前提
  是「重复调用常见、并发完全相同的调用不常见」。如果 digest 显示并发重复
  高，v2 加 SETNX 锁。YAGNI。

## 测试策略

| 层 | 测试文件 | 覆盖点 |
|----|---------|-------|
| 缓存键 | `tests/test_tool_cache.py` | 不同 key 序的同参数 → 同 key；`ref:xxx` 在任意位置 → 跳过；非 JSON 类型（datetime、set）落到 `str()` 路径仍确定性；`v1` 前缀存在。 |
| 缓存装饰器 | `tests/test_tool_cache.py` | 第一次 compute + store；第二次相同参数从 Redis 读，内层函数不再调用（mock call count 断言）；`skip_if` 谓词真时双向旁路；Redis down 时工具仍返回正确结果，warning 限流。 |
| trim 帮助函数 | `tests/test_tool_trim.py` | 空 FC 不变、无 `_trim`；恰好 `max_features` 不变；`max_features + 1` 裁剪到 `max_features` + 出现 `_trim`；精度: `121.123456789` @ p6 → `121.123457`；Polygon / Point 混合几何都四舍五入；非 FC 输入原样返回 + warning。 |
| timing record | `tests/test_tool_metrics.py` | `record_tool_call` 写出符合 schema 的 JSONL 行；error 路径正确记录异常类名；聚合器状态在 N 次合成调用后 count / total_ms / max_ms / hit_count / error_count 全对；digest 精确在 N=100 和 `emit_digest()` 时输出。 |
| registry 整合 | `tests/test_registry_timing.py` | 通过 registry 派发假工具 → 日志文件落一行；派发带 `@cached_tool` 的假工具两次 → 第二行 `cache_hit: true`。 |
| 端到端 | `tests/test_heatmap_caching.py` | 通过 registry 两次相同 `spatial.heatmap_data` 调用 → 第二次 <50ms（缓存命中）；输入 >5000 features 时响应里有 `_trim` 包络。 |
| smoke | 已有 `tests/test_chat_engine_planning.py` 风格 | 跑现有 chat-engine 集成测试 → 验证非装饰工具行为无变化、timing 日志不被污染。 |

**覆盖率门**: 每个新模块上线时 line coverage > 80%。修改过的旧文件不允许
覆盖率回退。

## 分阶段上线

三个小阶段、各自独立可发版。每阶段一个 PR。

### Phase 1 — 基础设施落地，行为零变化（1-2 天）

- 新文件: `app/lib/tool_cache.py`、`app/services/tool_metrics.py`
- 修改 `app/tools/_utils.py` 导出 `cached_tool`、`trim_features`
- 修改 `app/tools/registry.py` 每次 dispatch 自动记录 timing
- 修改 `app/main.py` lifespan 在 shutdown 时调用 `tool_metrics.emit_digest()`
- 测试: 上文所有 cache + metrics + trim 单测
- **上线门**: timing 日志在**所有**现有工具上出现 `cache_hit=false`；零工具
  接入缓存；零工具接入 trim；行为与今天完全一致。

### Phase 2 — 最重的工具接入（1-2 天）

- 给以下 4 个工具加 `@cached_tool(ttl=86400)`: `buffer_analysis`、
  `kde_contours`、`h3_binning`、`heatmap_data`。
- 在这 4 个工具返回前调用 `trim_features(result)`。
- 根据各工具的数据新鲜度需求调整 per-tool TTL。
- `heatmap_data` 端到端测试。
- **上线门**: 测试会话中 digest 日志出现命中累加；前端 heatmap / h3 / kde /
  buffer 在裁剪后载荷下仍正确渲染。

### Phase 3 — 由 digest 数据驱动第二批接入（运行一周后）

- 拉一份 prod（或 staging）连续 5-7 天的 digest。
- 选出尚未接入缓存的 top 3-5 个 `total_ms` 最大工具。
- 给这些工具加 `@cached_tool` + `trim_features`。
- TODOS.md 记录任何「这工具需要算法重写、不是缓存能救」的发现，留作未来
  per-tool 性能冲刺。

## 显式不做（YAGNI）

- **Celery 路由调整**。Celery 存在且工作中；本 spec 不改任何工具的 sync /
  deferred 决策。Phase 3 拿到真实数据后再议。
- **per-tool 输入采样帮助函数** `downsample_points()` / `simplify_geometry()`：
  有用但改变了工具语义（用户问的是 100k 点，工具用了 5k）。等首调延迟成为
  digest 头号问题时单写 spec。
- **主动缓存失效**。仅 TTL。需要「编辑图层后立即清缓存」时单写 spec。
- **Admin 性能仪表盘**。grep digest 行先用着。超出 grep 能力时单写 spec。
- **OpenTelemetry / 分布式追踪**。单进程结构化日志够用。跨进程同一 trace
  的需求出现时单写 spec。
- **Cache stampede 防护（SETNX 锁）**。前提是重复多、完全并发重复少。
  digest 显示反之时再加。
- **缓存大小限制 / 淘汰策略**。Redis 已有 `maxmemory-policy`，复用现有配置。
- **`trim_features` 的非「保留前 N」策略**。需要其他策略的工具自己在
  trim 前 sort。

## 成功标准

本 spec 算成功上线当：

1. Phase 1 PR 合并后，`tool_metrics.jsonl` 按 schema 每次工具派发落一行。
2. Phase 2 PR 合并后，4 个接入工具运行一周后缓存命中率 ≥ 30%（从 digest 量）。
3. `heatmap_data`、`h3_binning`、`kde_contours` 三个工具的 median
   `result_bytes` 下降 ≥ 30%（trim 效果）。
4. digest 给出可排序的工具总耗时清单——能直接指着 top-1 工具说「下个性能
   冲刺就攻它」，而不是猜。

本 spec **不**追求让任何特定工具在大输入下首调变快。那是 per-tool 算法
工作，使用本 spec 收集到的数据作为决策依据。

## 风险与未决

- **`trim_features` 改变响应载荷形状**——前端必须能容忍 `_trim` 顶层键
  存在。Phase 2 上线前需要在测试环境跑一遍现有前端集成测试。
- **缓存键碰撞**——16 字符 sha256 前缀在 10^9 条目下碰撞概率仍 < 10^-9，
  但万一发生会返回错误工具的结果。如果 prod 出现奇怪的"缓存返回了不相关
  数据"现象，第一步是把前缀加到 32 字符。
- **Redis 内存预算**——按平均工具响应 50KB、缓存条目 10000 算，约
  500MB。需要确认 Redis 的 `maxmemory` 配置容得下，否则会驱逐 session 数据。
  Phase 1 上线前查一次。
- **JSONL 写入磁盘 I/O**——每次工具调用一次 fwrite。10MB 轮转下不会爆盘，
  但 SSD 写入次数会显著上升。如果 I/O 成为新的瓶颈，改用 buffered handler。
