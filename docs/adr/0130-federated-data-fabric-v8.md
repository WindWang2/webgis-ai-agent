# ADR-0130: Data Fabric V8 — Adaptive Federated Spatial Data Plane

## Status
Accepted（Epic 03，2026-09-10；前身 ADR-0120 Federated Data Fabric V7）

## Context
V7 交付了联邦数据面治理层（ConnectionRegistry / CapabilityProbeService /
SourceFactsService / 分布式反馈 / 结果缓存 / counters），但在 ADR-0120 中
如实披露了结构性缺口：**治理面对生产流量不可见** —— 工具层 11 处调用点仍
走 legacy `connection_manager`，REST/worker 每请求绕过 registry 工厂重建；
probing 与 source_facts 是零消费者的孤儿库；feedback 衰减修正因子无
costing 消费者；V6→V5 回退无进程级熔断（R2-Mi-4，持续故障每请求付双执行
成本）；结果缓存无 stampede 保护、无分布式二线。目标流水线

`Data Fabric → ConnectionRegistry → Capability Profile → Cost Model →
Query Planner → Execution → Cache/Artifact`

中段四环（Capability/Cost/Planner/Cache）实际处于"捕获完整、回流断开"的
半闭环状态。

## Decision
V8 **不是新引擎** —— 是把 V7 治理面接成生产单一解析路径，并闭合自适应
回环。`engine` 参数语义、adapter 工厂链（`AdapterRegistry` 单一真相）、
V5 回退路径全部不变。

### 1. FabricRuntime：单一生产解析路径（`fabric/runtime.py`）
- 三级解析链：**registry 优先**（作用域/revision/健康/secret 分离生效）→
  **legacy 会话回退**（首次命中即注册进 registry —— 治理视图统一，单构建
  复用，不做双份真相的新写入方；沿用条目原属域 —— 全局连接不按会话复制）。
  DB 注册源的治理路径在 `DataFabricManager._governed_adapter`：行内重建
  profile（零额外 DB 查询）→ 工厂 seam 单次构建 → `attach_prebuilt` 幂等
  登记（此前 DB 源完全绕过 registry）。
- 接线面：工具层 11 处调用点收敛到 `_resolve_source_adapter`；
  `DataFabricManager._governed_adapter` 覆盖 query/async/materialize/sync/
  explain 五条 REST/worker 路径；registry 任何故障 fail-open 回退既有工厂
  构建（行为契约逐字节保留）。
- connect 即治理：`connect_data_source` 后把**同一 adapter 实例**注册进
  registry（`attach(prebuilt_adapter=…)`，legacy 存储原样保留）。

### 2. Registry V8 增强（`fabric/connection_registry.py` additive）
- `ConnectionRecord.redacted_profile`：**V7 缺陷修复** —— 此前 rehydrate 仅
  四字段，options 形态源（PostGIS host/port、本地文件 path）record 重建必
  失败；现在 attach 时保存无凭证全量视图（构造上不含 secret）。
- `ensure_adapter(record)`：LRU 驱逐后按 redacted profile 忠实重建并回填
  （secret 仅工厂构建瞬间注回的契约不变）。

### 3. 自适应闭环（Phase C+D：孤儿库激活）
- `federation.enrich_request_from_runtime`：规划前把治理数据拉平为**纯数据
  提示**（IO 收敛在 runtime，planner 保持纯函数）：
  - `ChainSource.source_type` ← registry record —— 激活静态能力注入（此前
    工具路径恒 None = 保守不下推，聚合下推证明资格从未启用）；
  - `ChainSourceStats.caps`（additive）← 探测覆盖（**仅 probed basis 注入**；
    default/stale 绝不注入 —— 绝不把未验证能力当真）；planner 优先消费于
    静态矩阵；
  - `estimated_rows` 缺省时 ← SourceFacts 行数事实 × feedback 衰减修正因子
    （仅 ok 观测、半衰加权、样本≥3、夹界 [0.1,10]）；**显式提示永不覆盖**，
    偏差以 `hint_feedback_drift` 如实披露；
  - `column_ndv` ← 事实 NDV（测量来源；已有提示不覆盖）。
- feedback 修正因子终于有了 costing 消费者（行数估计 → 扫描传输成本），
  ADR-0120 遗留的"捕获/持久/披露回路完整但无消费者"收口。

### 4. EXPLAIN 诚实披露（Phase E）
`EnumerationContext.estimate_basis` 透传 → `explain_v6_lines` 渲染
`estimate_basis:` 段（rows basis：`request_hint | source_facts:<basis>
[×feedback:<factor>(samples=N)]`、caps basis、hint 偏差）。无富集时该段
不渲染 —— 输出形状与 V7 逐位一致。

### 5. 引擎回退进程级熔断（Phase G，收口 R2-Mi-4）
`fabric/engine_breaker.py`：连续 V6 崩溃 ≥3（可配）→ OPEN，cool_down 60s
内 engine=v6 请求直达 V5（V6 规划+执行栈零进入）；窗口后半开单 trial 探测
恢复；成功归零。trial 经 `finally` 无条件释放（review P1-1：负缓存/typed
错误等不记账路径退出时名额不泄漏 —— 否则 HALF_OPEN 卡死，V6 被禁用到进程
重启）。守卫位于缓存命中检查之后（review P2-6：熔断剥夺的是 V6 执行，不
剥夺有效缓存结果的服务）。披露：回退结果 additive `engine_breaker` 段 +
warnings。熔断只影响引擎选择，结果契约不变。

### 6. 结果缓存强化（Phase F）
- **stampede 保护**：miss 后的规划+执行收进闭包，经 per-key `SingleFlight`
  执行 —— 并发同键请求在界内（10s，可配）共享首问结果（披露
  `basis=singleflight`）；owner 失败/等待超时 → 调用者自行执行（保护是
  延迟优化，绝非可用性单点）。
- **可选分布式二线**：`ResultCacheBackend` seam + Redis 实现（惰性连接、
  双界超时、故障 fail-open 计数）。进程内 LRU 恒为 L1，redis 写穿为 L2；
  键已含 per-source fingerprints —— 二线命中即一致；命中披露
  `basis=ttl+fingerprint+distributed`，age 诚实为 None。

### 7. 旁路收敛（Phase H）
- 单一路径落地后，联邦查询/目录查询/物化/explain 的 adapter 解析**全部**
  经治理链；`geocompute/ops` 与 `extensions_platform/fabric_bridge` 经
  `DataFabricManager` 同步受益。
- 遗留旁路如实登记（迁移顺序，本 PR 不动）：
  1. `app/services/rs/stac_client.py`（pystac_client 直连外部 catalog，绕过
     STACAdapter/SSRF/熔断）—— 最高优先迁移对象；
  2. `app/lib/geo_raster/remote.py` / `reader.py`（rasterio 直读任意 URI，
     仅可选挂钩熔断）；
  3. `app/tools/local_admin.py` / `local_stats.py`（httpx 直连高德 REST，
     legacy provider 工具形态）；
  4. 本地矢量（上传/GeoPackage/Shapefile 走 session ref 机制，fabric 无
     local-vector adapter）—— 需要新增 adapter 或 ref-backed source type，
     属独立 Epic。
- 跨 CRS join 正确性由既有测试锁定（V6 混合 CRS 变换记录/多跳子树 CRS
  传播/V7 交付账本/坏 CRS 对拒绝）；V8 未触碰 CRS 语义（富集只影响估计与
  能力，坐标变换决策输入不变）。

## Consequences
- 工具契约 additive：`query_federated_chain` 结果的 `explain_v6` 可能新增
  `estimate_basis:` 行；`fabric`/`result_cache`/`engine_breaker` 披露段
  只增不改。规划输入在治理数据可用时**可能改变 join 序选择**（这正是目标
  —— 历史成本影响 planner），无治理数据时行为与 V7 逐位一致。
- 安全：凭证分离面扩大（connect 即入 SecretStore，record/diagnostics/
  explain 全链无明文）；`DataSourceModel` 明文 JSON 列维持不变（at-rest
  加密仍是 hookable provider follow-up，本 ADR 继续披露）。
- 性能：registry 命中省去每请求 adapter 重建；熔断消除持续故障的双执行；
  单飞消除同键并发重复取数。新增成本：解析链一次进程内 dict 查找；探测
  走 TTL 缓存（scoped，TTL 300s）。
- 已知限制 / Deferred：
  - 工具路径无 DB 会话 —— DB 注册源在工具面仍需先经 REST connect 或在
    REST 路径使用（与 V7 一致，未引入新行为）；
  - 探测原语只覆盖 arcgis/ogc_api/stac（其余源类型不发无意义缓存条目）；
  - Redis 二线为可选 seam，单实例部署默认 memory（ADR-0120 决策延续）；
  - 遗留旁路清单（§7）按序迁移，每项独立 PR；
  - durable secret provider（at-rest 加密）。

## Migration
无 schema 变更（V7 迁移 0034 的表继续服务）。新增 Settings 键
`DATA_FABRIC_V8_*`（engine breaker 阈值/冷却、singleflight 等待、cache
backend/redis url）—— 默认值即可运行，不加 `.env.example`
（conftest 卫生测试约束不变）。回滚 = revert 本分支（全部 additive，
无数据迁移）。
