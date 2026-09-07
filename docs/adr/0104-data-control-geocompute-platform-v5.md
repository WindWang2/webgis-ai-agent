# ADR-0104: Data Control Plane V4 + GeoCompute V5 + Durable Workspace

**Date:** 2026-09-07
**Status:** Accepted
**Extends:** ADR-0103 (GIS Data / Artifact / Workspace Foundation V3), ADR-0101 (GeoCompute & Data
Fabric V4), ADR-0096 (GeoCompute Data Plane V3), ADR-0052 (Durable Job Runtime)
**Branch:** `feat/data-control-geocompute-platform-v5`
**Audits:** `.agent-work/data-compute-v5/01..08`（八路只读审计）+ `09-migration-plan.md`（综合与排期）

## 背景与问题

V3/V4 交付了执行面骨架（ExecutionPlan、ready-set 调度、durable dispatch、有界联邦、
governor、cache 广播），但八路落地前审计发现平台存在三类系统性问题：

1. **惰性服务（inert services）**。Workspace 快照有能力无入口：无任何 REST/tool surface，
   `restore_snapshot` 只做元数据重绑、从不回读字节（audit 02 §5.1/§5.4）；V3
   `IngestPipeline` 仅 service-internal，生产上传仍走 legacy `data_parser`
   pre-V3 语义（audit 03 §1.1/§1.2）；`ResourceClass` 声明维度被解析但从未被任何
   调度/治理路径消费（audit 07 R7）。
2. **耐久性缺口（durability gaps）**。晋升内容只有 `Artifact.metadata_json.
   content_location` 单指针——覆写式、无历史、无引用计数（audit 01 §3）；快照随
   session 一起死（purge/TTL sweep 连坐）；GC 可以吃掉快照声明拥有的 payload；
   快照恢复进的 ledger 本身是 ephemeral 的（audit 02 §5.2–5.5）；上传路径没有任何
   内容身份，同一文件传两次 = 两条记录，无去重、无血缘键（audit 03 §4）。
3. **CRITICAL governor bug（audit 07 §6.1 R1）**。rows/bytes/nodes 是**终身累加
   计数器**而非并发在途量规（gauge）：plan 启动 `reserve(rows=total_rows)` 加上每节点
   `charge()` 永久累积在 global/tenant/project/session 作用域上，唯一的 `release`
   只释放 concurrency=1。在默认 global 5M rows / 2GiB caps 下，约 25 个
   estimate-heavy run 之后所有后续 plan 在 `global:root` 永久
   `BudgetExceededError`——一个缓慢、静默的自拒服务（self-DoS），直到进程重启。

其余按审计归组：Arrow 只在 GeoParquet 边界存在且立即被降级为 dict（audit 04）；
raster 无 chunk 级缓存/取消、无 COG ingest、Zarr 缺席、terrain 全量读未设防
（audit 05）；无 cancel REST、无跨进程 checkpoint 复用、推送下推是二元开关
（audit 06）；cache 无 owner/tenant 域、无单飞、raster tile 缓存无字节上限
（audit 07）；lineage 参数与 execution_trace args **明文落库**、无环境捕获、
无 RepairPlan 状态机（audit 08 §2.4/§4.3）。

## 决策

六个 commit 按 audit 09 的波次落地（W1–W12；W13 安全项并入 W2 路由前置条件，
W14 基准门未在本批次）。总原则沿用每路审计的底线：**接线并加固既有结构；
绝不新建第二个 registry/store/truth。** 每个波次都是对既有权威的
adapter/缝合，而不是平行机构——BlobStore 从晋升库机制中**抽出**接口而非另建内容库；
artifact_revisions 挂在既有 `Artifact` 身份之下；durable 复用索引只是
`analysis_tasks` 旁边的纯缓存。

- **D1（W1）耐久产物库**：`BlobStore` 后端接口（`durable_blob_store.py`，
  `FilesystemBlobStore` 与晋升内容库同布局 `<key[:4]>/<key>`、tmp+`os.replace`
  原子发布、put-if-absent CAS）带 binary lane（content-type + sidecar）；
  `artifact_revisions` 追加式修订表（0026）+ `pin_artifact`/`clone_artifact`
  （clone 即指针、不计费）+ 按 refcount 的晋升库 GC（替换 report-only pass）；
  `register_artifact` 终态复活改为 probe-before-valid（`revival_probe` 披露）；
  `RefDescriptor.content_hash` 为 opt-in（`WEBGIS_REF_CONTENT_HASH`，默认关）。
- **D2（W2）Workspace V4**：项目侧快照之家（项目域快照在 session purge/TTL
  sweep 后存活，保留上限默认 50/项目）；`save_snapshot(materialize=...)` 经
  Wave-1 BlobStore 把存活 payload 写通（`none` = 纯 manifest；
  `all` = 全部声明 ref 落字节）；restore 经 digest 校验 `read_content` 重物化
  ——降级显式 `degraded` 披露，死 ref 永不恢复为 valid；layout chartRef/tableRef
  捕获；所有声明 ref 盖 `persistence_tier="workspace"` 章——GC 保护词表零改动
  即生效（`_GC_PROTECTED_TIERS` 已含 `"workspace"`）；REST + tier-2 tools 全部
  在 SEC-08 会话所有权守卫之后（audit 07 R10 的前置关闭）。
- **D3（W3）Ingest 生产缝**：V3 `IngestPipeline` 经 upload 附加 lane +
  `ingest_dataset` tool 进入生产；`uploads.content_sha256`（0027）同会话幂等
  去重（跨会话不共享——全局唯一会把内容变成跨租户能力令牌）；
  utf-8 失败回退 gb18030、链尾 `ParseError`（400 + 修复建议）；CRS 诚实披露
  （`rfc7946_default`/assumed 标记，绝不静默 4326）；上传时 raster profile
  （nodata/overviews）；物化步骤先落盘后置 ok 的回滚洞关闭。
- **D4（W4）RepairPlan**：`RepairPlan`/`RepairStep` 实体委托到**唯一的**
  REMEDIATION_OPS 映射；`plan-only` 默认——提案永不自动执行；显式
  `execute_repair` 产生**新 ref**（源数据永不原地突变）；执行证据
  `repair_evidence`（digest-only、≤有界）落在既有 `ArtifactLineage` 新列（0028），
  血缘边继续只存链接与有界事实；`quality_status` 回写 ProjectDataset
  （CHECK 词表追加 `repairable`/`blocked`，0028）；spatial audit issues 经
  W3 映射标注 repairable/remediation_op。
- **D5（W5）Arrow 双车道**：batch-native 载体（schema freeze，漂移即
  `VectorCarrierSchemaDriftError`）；`arrow_ops` 用 `pa.compute` 实现
  filter/project/aggregate；**统一累加器**（`query/accumulators.py`
  `AggregateDriver`）让 dict lane、compute lane、Arrow lane 共用一套聚合语义，
  消灭双实现分叉；`on_batch`→CancellationToken 适配（batch 边界可取消）；
  GeoParquet fast lane 复用 raster-ref 先例，`execution_lane` 诚实标注
  arrow/dict；`governor.limits_for` 转公共 API；pyarrow 仍是 probe + typed
  unavailable，绝不进 requirements。
- **D6（W6）Raster chunk runtime**：`RasterChunkDescriptor` +
  `iter_chunk_descriptors` + per-chunk hooks；opt-in chunk cache
  （`WEBGIS_CHUNK_CACHE_BYTES`，默认 1 GiB，window_safe 门 + 全局统计 profile
  门，resume 跳过已完成 chunk）；`to_cog`/`ensure_cog` + `convert_raster_to_cog`
  tool；Zarr foundation（typed `ZarrUnavailable`，不加依赖）；terrain 全量读加
  2× float64 字节守卫；STAC DEM sentinel 窗口改分条流式（逐位一致）；
  `RasterArtifactDescriptor.times/stack_ref` 时间轴字段。
- **D7（W7）GeoCompute V5 调度**：六条能力队列
  （light_cpu/heavy_cpu/high_memory/raster/network/external_io → `{profile}_queue`），
  `queue_for_node` 为确定性纯函数（locality_hint > 类别 > ResourceClass >
  轻量向量 > 默认 celery），WORKER_LOSS 重派复用同一路径 → retry affinity
  免新状态机；单 worker 消费全部队列、语义不变；REST
  `POST /plans/runs/{id}/cancel` + `cancel_execution_run` tool；跨进程
  durable-node 复用索引（0029 `geocompute_node_results`，upstream-fp 校验 +
  live-probe + 每 owner LRU 64 条 + fail-open）；run 证据快照（0029
  `geocompute_run_evidence`，≤16KB）让重启后 `get_run` 不再 404；
  eager 模式 `backend_variant="in_process_eager"` 诚实标注。
- **D8（W8）Governor 量规化**：rows/bytes/nodes 变**并发在途 gauge**——
  per-run `_RunChargeLedger`，`execute_plan` 的 `finally` 全额归还长寿命祖先
  作用域（CRITICAL R1 修复），baseline-return 回归测试钉死；opt-in 跨进程
  计数器（`WEBGIS_CROSS_PROCESS_GOVERNOR=1` 才有任何 Redis 交互），
  **advisory + fail-open**：L1 进程内 governor 永远是权威，L2 只在确信超限
  时建议拒绝，故障退避后重探测、绝不多拒；`ResourceClass` 接线为加权槽位
  （memory≥4 或 cpu≥5 → 2 槽位单位，经祖先作用域并发预算生效）；
  slot-lease watchdog 回收过 deadline+grace 仍不合作的节点槽位
  （`lease_reclaimed` 披露，钳零兜底）；`ScopeKind.NODE` 删除（从未使用）。
- **D9（W9）Optimizer V5**：AND-split per-clause 下推——部分可推的过滤器
  不再拉全量数据集，按拆分实况诚实披露 `partial` 三族；golden-pinned
  bit-compat fallback（守卫路径下与旧行为逐位一致）；统计量接入
  ArcGIS/OGC-API/WFS/STAC（count 级、advisory）；`query_federated_chain` tool
  （N 源 2..4 链式联邦暴露给工具面）；`derive_projection` 默认开
  （`false` 显式退出复原输出形状）；semi-join 右表约减回移植到双源路径。
- **D10（W10）Cache V5**：tool_cache 键进 owner 域（v2 键形状，带会话身份的
  工具跨用户不再共享条目；无可派生身份 → `anonymous` 哨兵）；describe 缓存
  键含 tenant+owner scope（`source:<id>|org:<org>|owner:<owner>`）；spatial-index
  与 ref-payload 重建单飞；tile 路由冷 ref 拉取去重；raster tile 缓存字节上限
  （`RASTER_TILE_CACHE_MAX_BYTES`，默认 256MiB）+ band-stats TTL
  （`RASTER_STATS_CACHE_TTL_S`，默认 600s）+ 经 `ref_lifecycle` 的失效 hook
  （additive observer，best-effort 永不 raise）；广播重连循环抽出为可测单元；
  16 例 cache chaos/race 矩阵。
- **D11（W11）Lineage/Reproducibility 接线**：run 完成时装配
  `build_execution_bundle`（verdict + digest + payload-free lineage 投影，
  run 读取含快照回退）；workflow run 的 manifest outcome 携带可复现分类
  （fingerprint 之外）；**写时**脱敏（keys 保留、值 `[REDACTED]`/超限换
  digest，整包 4096 字符预算）；`runtime_env`（python/GEOS/PROJ/GDAL/shapely/
  pyproj/numpy）折叠进 runtime manifest 指纹（v3）；lineage_inputs 从可证明的
  参数同一性派生。
- **D12（W12）Quota/Retention/GC**：per-project 字节/数量/单 artifact 修订字节
  配额（env 门控，默认 0=unlimited 与 W12 之前行为一致）；超限 → 诚实
  `content_status="quota_exceeded"`（行幸存 metadata-only，不写字节）；
  retention 年龄策略 plan/execute 对共享 **同一个** W1 保护谓词
  （pinned/workspace/lineage-root 统一）；flagship parity 矩阵测试钉死
  所有 planner 保护同一保护集；`GET /{project_id}/data-usage`、
  `POST /{project_id}/data-gc/plan|execute`（confirm 门控）；孤儿修订清理；
  clone 不计费。

## 单一事实源不变量（全波次强制）

1. **BlobStore 是唯一的耐久内容后端**。workspace 物化、晋升、raster binary、
   修订内容全部经由同一接口/同一磁盘布局；无第二个 BlobStore 调用方在晋升
   路径之外。快照只是 manifest（指针集合），字节真相只有一份。
2. **`artifact_revisions` 是 append-only 账本**，挂在既有 `Artifact` 身份之下
   ——它记录历史与引用计数，不发明新身份；`(artifact_id, content_sha256)` 唯一，
   重晋升同内容幂等复用同一行。
3. **没有第二份 job 真相**。durable 节点仍经 `AnalysisTask` 运行时派发；
   `geocompute_node_results` 是纯缓存索引（upstream-fp 校验、live-probe），
   `geocompute_run_evidence` 是终态证据快照——两者都能删，job 状态机不受影响。
4. **Lineage 保持 link-only**。`repair_evidence` 是有界 digest 事实，不是
   第二个产物库；任何要素载荷不落血缘边。
5. **可选依赖保持可选**。pyarrow/zarr 永不进 requirements.txt；probe +
   typed Unavailable + 两条 lane 都有诚实测试。
6. **破坏性路径共享同一保护谓词**。GC/retention planner 与 executor 消费
   同一保护模块（tier/pin/workspace/lineage-root），plan/execute parity 有测试。
7. Migrations 全部 additive（create_all-coexistence guard，SQLite/Postgres
   双方言）；session ledger 仍在 session store 内，耐久事实只经晋升/修订持久化。

## 关键语义

- **payload-digest 是主内容键**。`content_sha256` = BlobStore 键（CAS：同键必
  同内容）。晋升内容（promoted content）由此获得内容寻址身份；注意**会话层**
  `RefDescriptor.content_hash` 仍是 opt-in（默认关，成本门）——见已知局限。
- **Governor 是 gauge 不是 counter**。用量语义 = 并发在途（run 结束归还到
  baseline），不是终身累计。若未来真要终身配额，必须是显式的第二维度
  （带 reset/TTL），不能搭在准入路径上。
- **`quota_exceeded` 诚实状态**。超配额不静默丢字节也不伪造成功：晋升行幸存
  （metadata-only），`content_status="quota_exceeded"` + 有界 typed details，
  字节未写、可事后重试。
- **Repair plan-only 默认**。质量提案永远不自动执行；执行是显式 seam，
  产出新 ref + 有界证据，原 ref/dataset 不被触碰。
- **AND-split 下推 + bit-compat fallback**。过滤器按子句拆分：可推子句推远端、
  余项本地求值，`partial` 诚实披露；守卫路径（无法证明等价时）走 golden-pinned
  的位兼容 fallback——宁可退回旧行为，不可静默改变结果。
- **Workspace snapshot materialize/restore**。save：`materialize="none"`（默认）
  = 纯 manifest；`materialize="all"` = 尚无指针的声明 ref 写通 BlobStore +
  全部盖 `persistence_tier="workspace"` 章（GC interlock）；restore：
  digest 校验读回（`verified | digest_mismatch | pointer_missing | no_pointer`
  四态），降级显式 `degraded` 列表披露，写回失败保持诚实 `expired`——
  死 ref 永不变成 valid。

## 被拒绝的替代方案

- **新建独立 CAS 存储**（新内容库/新身份 scheme）——拒绝：audit 01 的头号风险
  就是 second-registry；`FilesystemBlobStore` 直接绑定晋升库 `content_store_root()`
  （root provider 可调用），布局向后兼容，读序兼容旧 `<key>.json`。
- **分布式配额服务/权威跨进程 governor**——拒绝：跨进程计数器是 advisory
  fail-open 叠加层；权威准入留在每 pod L1。一个在自身故障时必须**多放行**
  的层没有资格做权威（自身残余漂移如实记录在 `resource_counter` 模块头）。
- **DatasetRevision 独立表 / 第二产物注册表**——拒绝：`artifact_revisions`
  刻意 keyed 到既有 `Artifact` 行（无 FK 的 `workflow_run_id` 除外），只记
  内容账本与引用计数；一份新身份 scheme 会重演 audit 01 列出的四套产物抽象问题。
- **必选 pyarrow（以及 zarr）**——拒绝：probe + typed Unavailable + dict lane
  全语义兜底（dict lane 是普适车道，Arrow lane 是加速车道）。
- **快照自带存储（快照级字节库）**——拒绝：快照字节去 BlobStore（同一内容库），
  快照行只是指针集合；GC interlock 靠 tier 章而非新删除逻辑。
- **把 repair 做成自动管道**——拒绝：科学数据原地突变不可逆；plan-only 默认
  + 显式 execute + 新 ref 是唯一可接受形态。

## 迁移与兼容

- 0026 `artifact_revisions`（新表+索引）、0027 `uploads.content_sha256`
  （可空列+探测索引，NULL 不参与去重）、0028 `artifact_lineages.repair_evidence`
  （可空 JSON）+ `project_datasets.quality_status` CHECK 词表追加
  `repairable`/`blocked`（仅当旧 CHECK 在场时受守卫替换）、0029
  `geocompute_node_results` + `geocompute_run_evidence`（两个纯缓存/证据新表）。
- 全部 create_all-coexistence guard 保护（repo convention），SQLite/Postgres
  双方言，无数据改写；downgrade 反序删除（0028 的 SQLite batch 表重建可反射
  回具名 CHECK）。默认部署（不设任何新 env）行为与升级前一致：
  配额 0=unlimited、retention 0=keep-forever、content_hash 关、跨进程
  governor 关、chunk cache 关。
- 既有 REST 路由、工具签名、plan JSON、artifact refs 全部继续工作；
  tool_cache 键形状 v2 变更靠 TTL 自然过期（全量 miss 一次，无迁移）。

## 已知局限（如实清单）

- **Eager 模式悬崖**：Redis 缺席时 Celery `task_always_eager`——durable 语义
  （异构队列、跨进程复用、retry affinity）静默塌缩为进程内执行。已通过
  `backend_variant="in_process_eager"` 诚实标注，但语义差是真实的。
- **Governor 跨进程计数是 advisory/fail-open**：L2 有已知残余漂移（崩溃窗口
  虚高 ≤1 TTL、DECRBY 钳零竞态、故障期增量丢失、并发预留竞态）——方向都是
  多放行；每 pod L1 才是权威。多 pod 聚合只有建议力。
- **tool_cache 的 session-free 工具仍是 anonymous 域**：键 owner 域派生自注入
  的 `session_id`；不带会话的工具（且调用方未显式传 `owner_scope`）跨用户共享
  条目——对纯函数工具是安全的，对读会话外状态的工具不是。
- **DNS-rebinding 微窗口**：SSRF pre-flight 校验所有已解析 A/AAAA 且逐跳
  重验，但 re-resolve 与 connect 之间仍存在微窗口；connect-time IP pinning
  仍是 ADR-0053 的 documented follow-up。
- **快照内存后端 restore 是 alias 模式**：session store 为内存后端时
  overwrite 是 replace-only，写回以 `store()` + 别名恢复同一 ref 的读取语义，
  并如实返回 `"alias"` 模式供调用方披露——字节耐用性仍只在 BlobStore 侧。
- **Terrain 全量读是预算化而非消除**：2× float64 字节守卫拒绝超预算输入，
  但守卫内的全量读仍是全量读；STAC DEM 路径已改分条流式（逐位一致）。
- **Zarr/pyarrow 诚实不可用**：无依赖时 typed `ZarrUnavailable` /
  `VECTOR_CARRIER_UNAVAILABLE`，dict lane 兜底；不会静默降级也不会假装支持。
- **describe-cache 对共享源的 scope 依赖调用方**：键含 tenant/owner scope，
  但 scope 由路由从 source 行派生——绕过 fabric 路由直连 describe 的调用方
  必须自带上 scope（metadata_cache 文档化约束）。
- **链式联邦 tool 已暴露，但 remote-worker placement 仍进程内**：队列路由
  就位， federation 执行仍在本进程 bounded executor 内，跨 worker 的
  data-locality 调度未做。
- **会话 `content_hash` 默认关**：晋升内容有 payload-digest 键，但会话层
  内容身份要显式 `WEBGIS_REF_CONTENT_HASH=1`——关闭时热路径无 digest，
  死 ref 检测靠 probe 而非内容比对。

## 值得记住的偏差（对审计计划的偏离及原因）

- **AGGREGATE 边角统一**：audit 04 只要求"单一累加器消灭双实现"；落地时发现
  两个既有驱动对**空输入**语义不同（stream 产零行 = 终结操作；compute 的
  无分组聚合按 SQL `SELECT count(*)` 惯例仍出一行）。统一不是选边而是
  `emit_empty_global_row` 旗标保双方对外契约；`stddev` 钉死样本口径
  （n-1，对齐 Postgres STDDEV_SAMP）；`distinct_count` 加 10k 容量上限
  （到顶置 `approximate`，绝不无界内存）。
- **Semi-join 边界回移植**：audit 06 把 semi-join 列为 N 源（≥3）联邦能力；
  落地时回移植到双源路径（右表键约减显著缩小哈希索引与候选数），并进
  explain 的 `semi_join_reduction` 披露。
- **Retention 默认 keep-forever**：审计建议年龄策略；落地默认 0 天 = 永久
  保留——默认部署不做任何保留删除，策略是显式 opt-in（`WEBGIS_RETENTION_MAX_AGE_DAYS`），
  宽限复用 `PROMOTION_STORE_GC_GRACE_HOURS` 纪律（默认 168h）。
- **`ScopeKind.NODE` 删除而非接线**：audit 建议接线未用枚举；该成员从未被
  任何路径消费，删除比伪造语义诚实。
- **晋升库 GC 从 report-only 换成 refcount 执行**：audit 01 §7.8 的
  原建议是"报告式"；修订账本落地后引用计数成为可计算事实，直接升格为
  执行式 GC（plan/execute parity 测试锁定保护集）。

## 执行面红线（继承并强化）

不变：无第二 job 表/workflow engine/lineage store；Data Plane 不 import
Agent/Product Plane 状态（AST-enforced）；统计/缓存是性能提示不是正确性真相；
有界枚举；预算内才允许全量栅格读；事件循环零阻塞 I/O。新增：gauge 语义
基线回归钉死；血缘/manifest 落库前必经写时脱敏；破坏性路径必须走统一保护
谓词且有 plan/execute parity 测试。
