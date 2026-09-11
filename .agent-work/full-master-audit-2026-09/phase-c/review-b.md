# Review B — performance / resource-safety / security（88d4ecde V8 + 修复批次复核）

Reviewer: Review B agent（只读复核）。对象：Harness V8 新增（capability_graph.py /
qualification_v8.py / candidate_planner_v8.py / modelops 域包 / registry_validation 图闸）
+ 本会话修复批次中性能/安全相关改动（D-15/D-13/D-9/D-14/B-7/B-19/B-18/A-2/C-1..C-3）。

测量环境：本机（win32），warm import，10 条 modelops 种子记录。数字为量级证据，非基准。

---

## A. Harness V8（commit 88d4ecde）

### [RB-1] MINOR — V8 模块当前零生产调用方（诚实披露：零热路径成本 = 零生产收益）

- 证据（全仓 grep `plan_candidates_v8|qualify_node|get_capability_graph|estimate_for_node`）：
  生产代码内唯一 import 链是 `registry_validation.py:79-85 → validate_graph()`（见 RB-2，本身 test-only）；
  `candidate_planner_v8.py` / `qualification_v8.py` 的调用方只有
  `tests/unit/gis_harness/test_capability_graph_v8.py`。
- `app/services/chat/tool_surface_v3.py:512 select()` 的检索热路径只走
  capability_registry / algorithm_registry / `rank_tools`（capability_descriptors），
  不触碰 V8 图。**tool_surface_v3 select 上当前为零 V8 成本**——这是任务书要求
  明确披露的事实：V8 尚未接入任何请求路径（ADR-0136 的 planner/qualification
  能力目前是"已建成未通电"状态）。
- modelops 域包（capabilities/modelops.py +8、algorithms/modelops.py +8）对检索
  热路径的影响：仅使 V7 索引（`get_capability_index_cached`，进程级缓存，
  capability_descriptors.py:459）多 16 个描述符；`rank_tools` 是 O(描述符 × 查询词)
  的有界词法匹配，约 +5% 条目，进程缓存后一次性构建。**可忽略**。
- 建议：接入前保持现状即可；若长期不接，在 ADR/文档标注"未接线"，避免下轮
  audit 再次把"零调用"当 gap 重新发现。

### [RB-2] MINOR — 图闸未接入任何 preflight/startup；kill switch 是死代码

- `registry_validation.py:74-85` 把 `validate_graph()` 并入 `validate_gis_library()`，
  但 `validate_gis_library` 全仓唯一调用方是测试（8 个测试文件）；
  `app/main.py` lifespan 走的是 `validate_runtime_manifest_strict`
  （app/main.py:186-190），其内部**不**调用 validate_gis_library
  （runtime_manifest.py:529-548 只消费 manifest 自身 issues）。
  scripts/check_integration_preflight.py 也不调用。
- **对 preflight 时延的影响：当前为零**（preflight 根本不跑这道闸）。commit message
  "wired as a machine gate (preflight PASS)" 的实际语义是"单测套件闸"。
- 若将来接入 startup：冷路径成本实测 —— `validate_graph() → get_capability_graph()`
  首次 build 0.66s + source_fingerprints 冷 6.45s（其中 runtime_manifest 首次编译
  6.34s；lifespan 已先编译并缓存 singleton，故接入 startup 的净增约 0.7s）；
  缓存命中路径 8.3ms。无重建风暴：`_graph_lock` 串行化 + 指纹比对在锁内，
  并发调用方至多串行等待一次重建。
- `v8_capability_graph_enabled()`（capability_graph.py:32-34）全仓无调用方 ——
  `GIS_CAPABILITY_GRAPH_V8=0` 不会跳过图闸，注释"=0 回退 V7 行为"不成立。
- 建议：要么把 kill switch 接进 `validate_gis_library` 的 V8 段，要么删掉死函数；
  并决定图闸是否真的上 startup（0.7s 一次性成本可接受）。

### [RB-3] MINOR — get_capability_graph 锁内做 modelops 全量磁盘扫描；重建用平行 ToolRegistry（陈旧面不可见）

- `capability_graph.py:536-549`：`_graph_lock`（模块级 RLock）内先算
  `source_fingerprints()`。其中 modelops 段（:283-293）**每次** new 一个
  `ModelRegistryStore()` 并 `load()` —— `_loaded=False` → 全量 glob +
  逐文档 json 解析（registry.py:228-268）。缓存命中路径实测 8.3ms（10 模型），
  但成本 O(模型数) 文件 IO / 每次调用，且并发调用方在锁上串行。
- 重建路径 `build_capability_graph()` 内部（:494）再算一次 `source_fingerprints()`
  —— 重建时指纹算两遍（双倍 IO）。
- `_lazy_tool_registry()`（:300-311）每次重建 new `ToolRegistry() + init_tools()`
  （实测 ~0.65s），**不复用** lifespan 注入的活注册表：extension 注册的工具对图
  不可见；且 runtime_manifest 指纹在进程内只编译一次，工具面运行期变化不改变
  指纹 → 不触发重建（modelops 注册变化**会**触发 —— 失效语义不一致：模型面
  按内容失效、工具面按启动快照冻结）。
- 当前零生产调用方 → 无实际影响；接入热路径前必须先解决（复用模块级
  ModelRegistryStore 单例做指纹、复用 app registry、缓存 tool 段投影）。

### [RB-4] MINOR — MAX_EDGES 截断静默，违背本文件自己声明的"不静默"纪律

- `capability_graph.py:77-78` docstring："超过即截断并在 issues 披露（不静默）"。
  节点段兑现了（`_add` :320-326 记 `node_budget_exceeded`）；边段 `_edge`
  （:335-339）到 `MAX_EDGES` 直接 return，**无任何 issue 披露**。20000 条边
  被截断时 validate_graph 与下游完全无感。
- 建议：`_edge` 首次触顶时补一条 `edge_budget_exceeded` issue（与节点段对称）。

### [RB-5] NIT — modelops 投影段单 try 包整段循环：一条坏记录抹掉整段

- `capability_graph.py:429-471`：循环体内 `int(desc.input_bands)` 等 typed 取值
  一旦抛错，整段落 `modelops_registry unavailable` —— 投影损失披露了但粒度粗
  （分不清"1 条坏记录"与"registry 不可用"）。注册期校验
  （DescriptorError/RegistryParityError）使其概率很低。建议 per-record try。

### V8 安全面检查（结论：未发现注入面）

- corpus 构建：纯 registry 字符串拼接 + 截断（id 128 / label 96 / corpus 400，
  `GraphNode.__init__` :101-106）；无 eval/SQL/模板渲染；`to_dict` extras 截前 8 项。
- `qualification_v8` / `estimate_for_node`：纯函数、零 IO、零 LLM（符合声明）。
- `reliability_penalty_v8`（candidate_planner_v8.py:81-104）：ledger snapshot
  有界 32 条；前缀匹配 `entity_key + "|"` 对 `"{entity}||{failure_class}"` 键
  语义正确（entity 自身含 ":" 不含 "|"）；`min(1, fails/4)` 有界。异常 → 0.0
  中性（fail-open，与"反馈缺席中性"注释一致）。

---

## B. 修复批次复核

### [RB-6] MINOR — D-15 budget 实现正确，但计请求数不计字节：残余带宽放大面

- 实现（`app/api/routes/layer.py:51-68`）：`layer_data:{session_id}`，600 次/60s，
  三个数据面端点全接入（:82/:147/:294）。**顺序正确**：`require_owned_session`
  是 Depends，先于 handler 体（即先鉴权后扣预算）→ key 基数 = 真实归属会话，
  无匿名刷 key 面。
- fail-open 语义核实：`RedisRateLimiter.is_allowed`（rate_limiter.py:80-101）
  Redis 异常 → True + warning；socket_connect_timeout=2 / socket_timeout=1
  （C-F10）→ 半开连接最多阻塞 1s，无可用性灾害。Redis 缺席回退
  MemoryRateLimiter（per-pod，_MAX_KEYS=10000 有界，超界丢最旧 key = 预算重置，
  仅 dev/单 pod 语义漂移，已文档化）。与全局限流同语义 —— 设计自洽。
- 残余：`/layers/data/{ref_id}` 单响应可达 ~50MB（#590 分块序列化路径），
  600 次/分 × 50MB ≈ 30GB/分/会话 的理论带宽上限 —— 请求计数 budget 降低了
  但没有 bound 字节。会话需通过鉴权 + 数据须真实存在，攻击价值有限，但若
  关心出口带宽，后续可加 per-session 字节配额或下调计数上限。
- NIT：MVT 瓦片端点预算在 tile LRU 命中检查**之前**扣（:292）—— 重度平移
  （全缓存命中）也耗预算；docstring 自述覆盖"3-4 层全瓦片视口恢复突发"，
  属有意取舍，建议在注释中明示缓存命中也计费。

### [RB-7] D-13 HKDF 域分离 — 实现正确；旧签名失效影响面 ≈ 零（诚实披露）

- `app/core/signing.py:24-28`：`prk = HMAC(info, IKM)`；`key = HMAC(prk, info||0x01)`
  —— 单块 expand 与 RFC 5869 的 T(1)（L=32, SHA-256）逐字一致；counter 字节
  正确。以 info 兼任 extract salt 非规范 HKDF 但密码学上稳健（双 HMAC 域分离，
  派生键 ≠ 原始 JWT 键，域间由 info 区分）。`verify_signature` 常量时间比较
  （hmac.compare_digest）；`f"{path}|{exp}"` 无跨协议碰撞（exp 必须 int-parse）。
- `_secret()` 每次 verify 重算 2 个 HMAC —— 微秒级，无 DoS 面。
- **旧签名失效影响面**：全仓唯一生产调用方是 `static.py:98 verify_signature`；
  `sign_path`/`make_signature` 没有任何生产 producer（只有 tests）。即当前
  仓库内**没有**签发签名 URL 的线上流 —— 迁移只使外部手工签发的旧 URL 失效
  （TTL≤1h，窗口自愈）。无破坏。

### [RB-8] D-9 static ver 复核 — 成本与 DoS 面均合格

- `static.py:83-97`：仅 `role==admin` 的 Bearer 通道触发一次
  `select(User.token_version).where(User.id==...)` —— 索引 PK 点查；
  `token_version` 列 `nullable=False default 0`（db_model.py:40）→ `int(row[0])`
  无 TypeError。公共/签名路径零额外查询（符合注释承诺）。
- DoS 面：持 admin token 者本可打任意 DB-backed 路由，新增每请求一次 PK 点查
  不构成有意义的新放大。语义正确：登出（token_version bump）→ is_admin 降级 →
  404。

### [RB-9] D-14 session_id 必填 — 调用方覆盖完整，无破坏

- `upload.py:166-175`：缺 session_id → 400（前置拒绝，不再制造 #1109 下
  不可见不可删的孤儿记录 —— 语义自洽）。
- 调用方覆盖（全仓唯一客户端链）：`frontend/lib/api/upload.ts:60-70`
  `uploadFile(file, sessionId?)` 有 sessionId 才 append；其唯一 UI 调用方
  `frontend/components/upload/upload-zone.tsx` 本身未挂载到任何页面（仅测试
  引用）→ **无生产 UI 因该 400 破坏**；后端无内部 POST /upload 调用方。
- NIT：UploadZone 的 `sessionId` 仍是可选 prop，缺省时错误只在 400 响应后
  浮现 —— 可在组件层前置校验/禁用，属 UX 打磨。

### [RB-10] B-7 RasterReader with 包装 — 异常路径句柄完整，无泄漏残留

- `reader.py:104-115`：open 失败（含 rasterio import 失败）时显式
  `env_cm.__exit__` —— 持有的 rasterio_env CM 不泄漏；`close()`（:121-130）
  幂等 + env teardown best-effort。
- 全仓非 with 用法核查：`engine.py` 全部 with / try-finally（含 :825 的
  `reader_b` 在内层 finally 关闭；:1354/:1619/:1659 为 try/finally 等价形式）；
  `source.py:159/169/214/249` 是工厂方法，由基类 `__enter__/__exit__`
  （source.py:150-152）消费；`fingerprint.py:98`、`zarr.py:420` 均 try/finally。
  **未发现异常路径泄漏点**。

### [RB-11] B-19 LRU 驱逐循环 — 修复正确（str-Enum 已验证）；一个相邻 invalidate 竞态

- `executor.py:561-575`：驱逐只挑 `status != "running"`；`ExecutionRunStatus`
  是 `str, Enum` 且成员值 "running"（plan.py:320-325），实测
  `S.RUNNING != "running"` → False —— **不会被驱逐**。循环在只剩在飞 run 时
  break（临时超界由终态回调收敛，文档化）。最坏复杂度：每次驱逐一轮 O(N) 扫描，
  超界 K 条 → O(K·N)（N ≤ 128 + 并发在飞），锁内微秒级 —— 无最坏情况风险。
  `_run_extras` 的 `popitem(last=False)`（:670-671）无运行保护是安全的：extras
  仅在终态后写入。`_run_outputs/_run_tokens` 在终态显式清理（:684-687）。
- MINOR（相邻发现，loaded_cache.py:183-192）：`invalidate()` 在 pop 与 poisoned
  回插两个临界区之间，同 key 的并发 load 完成（:160）会插入 fresh 条目，随后
  被 invalidate 的回插**覆盖** —— fresh model 的 (model, unload_fn) 泄漏（无人
  再能 unload）。窄竞态、泄漏单个模型句柄直到进程重启。建议：回插前检查
  `key not in self._entries` 或把 pop+poison 合并进单一临界区。
- NIT：`_drop_locked`（loaded_cache.py:227-237）在持锁状态同步执行 unload_cb ——
  GPU teardown 慢时阻塞所有缓存操作（既有模式，非本次引入）。

### [RB-12] B-18 FAISS 渐进扩取 — 有界正确；空租户全扫描残余

- `faiss_store.py:456-495`：fetch 从 `min(4k, ntotal)` 起、不足翻倍至 ntotal
  —— 追加 search 次数 ≤ ceil(log2(ntotal/4k))+1，如注释所声。索引是
  `IndexFlatIP`（:258）精确搜索：最坏总工作量 ≈ 等比级数 ≈ 2 次全扫 —— 有界。
- MINOR 残余：某租户在共享索引中**零命中**时每次查询都扩满 ntotal（重复全扫）。
  大索引 + 恶意/空租户 = 每查询 O(ntotal·d)。当前索引规模可接受；若增长，
  考虑按 owner 前缀分区索引或对空租户短路（metadata 预统计）。
- 边界核查：ntotal=0 / top_k=0 / fetch≥ntotal 均正确终止，无死循环路径。

### [RB-13] A-2 别名折叠 — 正确且零热路径成本

- `registry.py:1056-1059`：dispatch 入口 `_TOOL_NAME_ALIASES.get(name, name)`
  —— O(1) dict 查找/dispatch，与 `_dispatch_impl` 同一映射（import 自
  argument_normalization.py:105-158 单一事实源）。指标（tool_metrics.record_
  tool_call :1175）与 recent_failure_hints 因此收到 canonical 名。
- `ToolRegistry.resolve_name`（:891-894）公开只读；`aliases_for`（:896-898）
  O(50) 扫描仅在 `descriptor()` 构建期（`_descriptor_cache` 缓存）。
  `no_progress.py:61-63` 同源折叠。无回归面。

### [RB-14] C-1/C-2/C-3 — 修复到位；对 React 渲染无影响（新闭包均在命令/库层，非组件层）

- **React 影响核查（任务书问点）**：三处修复全部位于 map-commands 处理器与
  `lib/mapspec/user-mutation.ts` 库函数 —— 不触碰任何 hook/组件/依赖数组，
  无新渲染闭包；durability 均为 fire-and-forget（`void (async …)()`），不阻塞
  ack 队列与渲染。`enqueueUserMutation` 串行链（user-mutation.ts:105-112）
  链尾自愈（`.catch(() => undefined)`）。
- C-1（layerCommands.ts:352-355 + :432-454）：durability 目标在删除前捕获；
  会话守卫（enqueuedSessionId 入队/执行双读比对）；`unsynced` 保留 pending
  压制 reconcile 复活。NIT：IIFE 内动态 import 与 cursor 读取在 try/catch
  之外 —— import 失败会成为 unhandledrejection（dev 噪音，无状态损坏）；
  建议整段包 try/catch。
- C-3（layerCommands.ts:942-966）：store 重排 + `commitMapSpecMutation(
  intent:'reorder_layers')` fire-and-forget（会话守卫 + try/catch + devOnly.warn）；
  ack 维持 store_updated 诚实语义。同款 NIT（import 在 try 外）。
- C-2（user-mutation.ts:153-156/:239-240/:337/:380/:443）：所有 mutation 通道
  在 await 之后做会话复核，superseded 分支同样复核 —— 一致覆盖。
- 测试：`frontend/lib/map-commands/audit3-frontend-fixes.test.ts`（#1200/#1202
  专测：断言 POST 实际发出 + intent 正确）。

---

## 计数

| severity | 数量 |
|---|---|
| BLOCKER | 0 |
| MAJOR | 0 |
| MINOR | 7（RB-1, RB-2, RB-3, RB-4, RB-6, RB-11-竞态, RB-12） |
| NIT | 5（RB-5, RB-6-缓存命中计费, RB-9, RB-11-unload 持锁, RB-14-IIFE） |

## 任务书问点的直接回答

1. **capability_graph build 最坏成本**：冷路径 fingerprint ~6.5s（dominated by
   runtime_manifest 首编译 6.3s；lifespan 已编译则 ~0）+ build 0.66s（dominated
   by _lazy_tool_registry 重建 ToolRegistry）。缓存命中 8ms/次但含锁内 modelops
   全量磁盘扫描（O(模型数)）。重建触发者：任何 get_capability_graph 调用方且
   指纹变化（模型注册/反注册会变；工具面运行期变化**不会**）。重建风暴不可能
   （全局锁 + 锁内指纹比对）——但当前**零生产调用方**（RB-1）。
2. **qualification/candidate planner 在 tool_surface_v3 select 上被调用吗**：
   没有。零热路径成本（诚实披露：也即零生产收益）。
3. **registry_validation 图闸对 preflight 时延**：零 —— validate_gis_library
   本身 test-only，startup/preflight 脚本均不经过（RB-2）。
4. **安全**：V8 无注入面（corpus 有界拼接、extras 截断、纯函数）；
   D-13 HKDF 语义正确（单块 expand 与 RFC 5869 T(1) 一致），旧签名失效影响
   ≈ 零（无生产 producer）。
5. **前端 effects**：C-1/C-2/C-3 均在命令/库层，不产生新的渲染闭包或依赖
   数组变化（RB-14）。
