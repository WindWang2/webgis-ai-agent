# Data Control Plane V4 + GeoCompute V5 + Durable Workspace — Data Plane Guide

ADR-0104 (`docs/adr/0104-data-control-geocompute-platform-v5.md`) is the
governing design document. This guide maps what now exists to code, states the
operational contracts, and lists every new env var / route / tool. Predecessor
guides: `geocompute-v4.md` (V4 execution + query plane) and
`v3-foundation/architecture.md` (data identity foundation).

## 架构总览（what now exists）

```
                        ┌── REST / tools（全部在 auth / SEC-08 之后）──
                        │
  upload ──► ingest seam ──► V3 IngestPipeline ──► catalog/profile/quality
  (content_sha256 幂等)      (app/services/data_ingest/)
                        │
  geocompute plan ──► executor（ready-set 调度，V4 继承）
        │                    │  governor L1 量规准入（per-run ledger，finally 全额归还）
        │                    ├─ durable 节点 → AnalysisTask → {profile}_queue（六队列）
        │                    ├─ 跨进程复用索引 geocompute_node_results（纯缓存）
        │                    └─ run 证据 geocompute_run_evidence（≤16KB）
        │
  session ref ──► promotion ──► project Artifact ──► artifact_revisions（append-only 账本）
                        │                │                 │
                        └──► BlobStore（唯一耐久内容后端；payload-digest 主键）
                                 ▲                            │ refcount GC
                                 │ materialize/read-back      ▼
                        workspace snapshots（manifest=指针集合；tier="workspace" interlock）
```

### Durable artifact flow（W1）

```
session ref payload
   │ canonical JSON 序列化（或 raster binary + sidecar）
   ▼
sha256 ──► BlobStore.put_blob(key=digest)          # put-if-absent CAS，tmp+os.replace
   │              FilesystemBlobStore：content_store_root()/<key[:4]>/<key>
   ▼
record_revision(artifact_id, content_sha256, …)    # (artifact_id, sha) 唯一 → 幂等
   │
   ├─ pin_artifact()      → GC 永不删
   ├─ clone_artifact()    → 新指针、零拷贝、不计费
   └─ refcount sweeper    → referencing_counts(location, sha) == 0 且过宽限期才删
```

- 接口：`app/services/durable_blob_store.py`（`BlobStore` / `FilesystemBlobStore`
  / `get_filesystem_blob_store()`）。调用方永远经抽象，不触碰路径。
- 账本：`app/services/artifact_revisions.py`（`record_revision` / `head_revision`
  / `pin_artifact` / `clone_artifact` / `referencing_counts` / `project_quota_usage`）。
- 复活纪律：`register_artifact` 对终态记录先 `probe_ref`，命中才 valid，
  未命中标 `expired` + `revival_probe="miss"`（`app/services/artifact_registry.py`）。
- 晋升库 GC：`app/services/artifact_lifecycle.py`（`PROMOTION_STORE_GC_GRACE_HOURS`，
  默认 168h）。

### Workspace V4 flow（W2）

```
save_snapshot(session, project, materialize="none"|"all", max_materialize_bytes)
   │ ① 捕获五家族 + layout chartRef/tableRef
   │ ② materialize="all"：无指针的声明 ref → materialize_ref_payload → BlobStore
   │ ③ 全部声明 ref 盖 persistence_tier="workspace" 章（_GC_PROTECTED_TIERS 已含）
   │ ④ 项目域保留上限 50/项目（_MAX_SNAPSHOTS_PER_PROJECT）
   ▼
restore_snapshot(snapshot)
   │ 账本重注册（rebind，mode="register"）→ 逐 ref verify_durable_pointer
   │ 四态：verified | digest_mismatch | pointer_missing | no_pointer
   │ 有指针 → read_back_payload（digest 校验）→ write_back_session_payload
   │ 失败项 → degraded 列表披露；死 ref 保持 expired，绝不伪装 valid
   ▼
verify_snapshot / list / clone / delete（REST + tier-2 tools，SEC-08 守卫）
```

- 通道：`app/services/workspace/durability.py`（`materialize_ref_payload` /
  `read_back_payload` / `verify_durable_pointer` / `write_back_session_payload`，
  内存后端 overwrite 为 replace-only 时如实返回 `"alias"` 模式）。
- 编排：`app/services/workspace/snapshot.py`。

### Ingest seam（W3）

生产上传路径附加 V3 管线 lane：字节落盘 → `content_sha256` → 同会话幂等探测
（命中返回既有 upload_id，`deduplicated=true`）→ 解析（utf-8 失败回退
gb18030，链尾 `ParseError`）→ CRS 诚实披露（`rfc7946_default`/assumed，绝不
静默 4326）→ raster profile（nodata/overviews）→ 质量报告 → plan-only 修复
提案（REMEDIATION_OPS 词表）。`ingest_dataset` tool 走同一管线。物化步骤
先落盘后置 ok 的回滚洞已关闭。代码：`app/api/routes/upload.py`、
`app/tools/ingest_tools.py`、`app/services/data_parser.py`。

### Arrow dual-lane（W5）

```
dict lane（普适车道，永远可用）        Arrow lane（pyarrow 在场时的加速车道）
streaming.py 批缝  ◄──同语义──►  geoparquet_adapter fast lane（execution_lane 标注）
        │                              │ arrow_ops（pa.compute filter/project/aggregate）
        └──────────► query/accumulators.AggregateDriver ◄──────────┘
                     （统一聚合语义：count 口径、stddev 样本 n-1、
                      distinct_count 10k 上限置 approximate、
                      emit_empty_global_row 旗标保空输入契约）
```

pyarrow 缺席 → typed unavailable（`VectorCarrierSchemaDriftError` 冻结 schema
漂移；carrier probe 失败即 typed 错误），dict lane 兜底全部语义。batch 边界
是取消协作点。GeoParquet 产物 lane 复用 raster-ref 先例（opt-in
materialize_geoparquet）。代码：`app/services/data_fabric/{vector_carrier,
arrow_ops,streaming}.py`、`query/accumulators.py`、`adapters/geoparquet_adapter.py`。

### Chunk runtime（W6）

`app/lib/geo_raster/chunk.py`：`RasterChunkDescriptor` + `iter_chunk_descriptors`
（chunk_id = 网格身份 digest）+ per-chunk hooks（逐 chunk 完成、取消后已完成的
chunk 保留、resume 跳过重算）。Opt-in chunk cache 挂在 artifact_cache 底盘上
（`app/lib/artifact_cache.py`，默认 1 GiB）：仅 window_safe 且非全局统计
profile 的操作可缓存；键 = chunk fingerprint；写入走流式 digest。COG：
`app/lib/geo_raster/cog.py`（`to_cog`/`ensure_cog`）。Zarr：
`app/lib/geo_raster/zarr.py`（typed `ZarrUnavailable`，不加依赖；
`zarr_chunk_descriptors` / `write_zarr_cube`）。

### Scheduler queues（W7）

```
queue_for_node(node)  # 确定性纯函数（app/services/geocompute/durable.py）
  1. locality_hint 命中 profile 词表        → 直接钉队列
  2. 类别（raster/heavy_cpu/network/external_io）
  3. ResourceClass：memory≥4→high_memory、io≥4→external_io、cpu≥4→heavy_cpu
  4. 轻量向量类别                           → light_cpu_queue
  5. 其余                                   → celery（默认）
```

六条 profile 队列：`light_cpu_queue` `heavy_cpu_queue` `high_memory_queue`
`raster_queue` `network_queue` `external_io_queue`（`app/services/task_queue.py`
声明 `task_queues`/`task_routes`；docker-compose 的单 worker 以
`-Q celery,…全部队列` 启动，行为与 V4 一致；按 profile 拆 worker 时收窄 `-Q`
子集即可）。WORKER_LOSS 重派走同一 `queue_for_node` → retry affinity 免新状态机。
取消：`POST /api/v1/geocompute/plans/runs/{run_id}/cancel` + `cancel_execution_run`
tool。重启后 `get_run` 经 `geocompute_run_evidence` 快照回放（owner 校验）。

### Governor semantics（W8）

- **量规而非计数器**：`_RunChargeLedger` 记录 run 的预留估计 + 各节点实充，
  `execute_plan` 的 `finally` 沿链全额归还 global/tenant/project/session
  作用域（`lease_reclaimed` 跳过二次释放；钳零兜底）。回归测试钉死
  "run 结束后用量回 baseline"。
- **L2 advisory 跨进程计数器**（`resource_counter.py`）：仅
  `WEBGIS_CROSS_PROCESS_GOVERNOR=1` 且 REDIS_URL 可解析时才有 Redis 交互；
  键 TTL 1h + stale-sweep；故障 30s 退避重探测；**fail-open——自身故障时
  多放行，绝不多拒绝**，因此只能建议、不能替代 L1 准入。
- **加权槽位**：`slot_units_for`（memory≥4 或 cpu≥5 → 2 单位）经祖先作用域
  并发预算产生跨 run 背压；EXECUTION 自身上界 2×max_workers 防自锁。
- **slot-lease watchdog**：deadline+grace 仍不释放的节点槽位被回收
  （`DEFAULT_SLOT_LEASE_GRACE_S`）。
- `ScopeKind.NODE` 已删除。

### Cache invalidation inventory（W10）

| 缓存 | 键构成 | 失效路径 |
|---|---|---|
| tool_cache（v2 键） | sha256(owner_domain \| tool \| args) | TTL；owner 域变更即全量 miss |
| describe cache（metadata_cache） | parts + `source:id\|org:o\|owner:u` scope（必填） | TTL + fingerprint |
| spatial index runtime | fingerprint | 单飞重建（singleflight） |
| ref payload cache | ref/revision | `ref_lifecycle` 失效 + 单飞 |
| raster tile cache | raster_path | 字节上限 256MiB（`RASTER_TILE_CACHE_MAX_BYTES`）+ `ref_lifecycle` additive hook（`invalidate_raster_ref`，best-effort） |
| band-stats cache | raster_path | TTL 600s（`RASTER_STATS_CACHE_TTL_S`）+ 同上 hook |
| 跨进程广播 | — | `cache_broadcast`（Redis pub/sub，仅 id+reason；重连循环已抽出可测） |

权威失效真值仍是 `ref_lifecycle`；广播只是通知，正确性从不依赖收到广播。

### Lineage chain（W11）

run 完成 → `build_execution_bundle`（verdict：reproducible /
conditionally_reproducible / stale / source_unavailable / non_deterministic +
digest + payload-free lineage 投影；run 读取含快照回退）→ workflow run 的
manifest outcome 带分类（fingerprint 之外）。**写时脱敏**：lineage
parameters 与 `execution_trace[].args` 落库前过 redactor（敏感 key →
`[REDACTED]`，超限值 → digest，整包 4096 字符预算）。`runtime_env`
（python/GEOS/PROJ/GDAL/shapely/pyproj/numpy）折叠进 runtime manifest 指纹
（v3）。修复执行证据 `repair_evidence`（digest-only，有界）落在
`ArtifactLineage`；lineage 继续只存链接与有界事实。代码：
`app/services/geocompute/{reproducibility,run_evidence}.py`、
`app/services/provenance/manifest.py`、`app/lib/gis/runtime_manifest.py`、
`app/services/data_quality/repair_{plan,execution}.py`。

### Quota / Retention / GC（W12）

- 配额：`app/services/data_lifecycle/quota.py` `ProjectQuotaPolicy.from_env()`
  （bytes / artifact count / per-artifact revision bytes；0 = unlimited）。
  超限 → 晋升行幸存 metadata-only，`content_status="quota_exceeded"` +
  有界 details（`app/services/project_artifact_promotion.py`）。
- Retention：`RetentionPolicy`（`WEBGIS_RETENTION_MAX_AGE_DAYS`，默认 0 =
  keep-forever；宽限复用晋升 GC 168h 纪律）；plan/execute 共享同一 W1 保护
  谓词（pin / workspace tier / lineage root），parity 矩阵测试锁定。
- 运维入口：`GET /api/v1/projects/{project_id}/data-usage`、
  `POST .../data-gc/plan`、`POST .../data-gc/execute`（confirm 门控 + 认证必需；
  round-1 review SEC CRITICAL-2：per-project 端点只做**项目域**保留 + 孤儿
  修订清理 —— plan 的可删 blob 只给 sha 前缀 + 字节，绝不返回全局
  key/location；GLOBAL promotion-store GC 只属于周期 sweep）、孤儿修订清理
  随 GC 走。

## 环境变量参考（本批次新增）

| 变量 | 默认 | 语义 |
|---|---|---|
| `WEBGIS_REF_CONTENT_HASH` | 关 | 会话 `RefDescriptor.content_hash`（canonical sha256）opt-in 开关 |
| `PROMOTION_STORE_GC_GRACE_HOURS` | 168 | 晋升库 refcount GC 宽限期（小时） |
| `WEBGIS_CHUNK_CACHE_BYTES` | 1073741824 (1 GiB) | raster chunk cache 字节上限（设值即启用） |
| `WEBGIS_CROSS_PROCESS_GOVERNOR` | 关（须 `=1`） | opt-in 跨进程 governor 计数器（advisory/fail-open） |
| `WEBGIS_PROJECT_ARTIFACT_MAX_BYTES` | 0 = unlimited | per-project 耐久产物字节配额 |
| `WEBGIS_PROJECT_ARTIFACT_MAX_COUNT` | 0 = unlimited | per-project artifact 数量配额 |
| `WEBGIS_PROJECT_ARTIFACT_MAX_REVISION_BYTES` | 0 = unlimited | 单 artifact 修订字节配额 |
| `WEBGIS_RETENTION_MAX_AGE_DAYS` | 0 = keep forever | 未 pin 修订的年龄保留策略 |
| `WEBGIS_RETENTION_GRACE_HOURS` | 168 | 保留宽限（复用晋升 GC 纪律） |
| `RASTER_TILE_CACHE_MAX_BYTES` | 268435456 (256 MiB) | raster tile 缓存字节上限 |
| `RASTER_STATS_CACHE_TTL_S` | 600 | band-stats 缓存 TTL（秒） |

队列（非 env，代码声明）：`celery`（默认）+ `{light_cpu,heavy_cpu,high_memory,
raster,network,external_io}_queue`。

## REST / tool 表面新增

REST（前缀按既有挂载；`{pid}` = project id）：

| 路由 | 说明 |
|---|---|
| `POST /api/v1/geocompute/plans/runs/{run_id}/cancel` | 取消运行（协作取消，非强杀） |
| `GET /api/v1/geocompute/runs/{run_id}` / `.../summary` | run 读取（含证据快照回退） |
| `POST /api/v1/projects/{pid}/workspace/snapshots` | 保存快照（`materialize=`） |
| `GET /api/v1/projects/{pid}/workspace/snapshots` / `/{snapshot_id}` | 列表 / 详情（integrity 四态） |
| `POST /api/v1/projects/{pid}/workspace/snapshots/{snapshot_id}/restore` | 恢复（degraded 披露） |
| `POST /api/v1/projects/{pid}/workspace/snapshots/{snapshot_id}/clone` | 克隆 |
| `DELETE /api/v1/projects/{pid}/workspace/snapshots/{snapshot_id}` | 删除 |
| `GET /api/v1/projects/{pid}/workspace` | workspace 总览 |
| `GET /api/v1/projects/{pid}/data-usage` | 配额使用量 |
| `POST /api/v1/projects/{pid}/data-gc/plan` / `.../execute` | GC 计划 / 确认执行 |
| `POST /api/v1/projects/{pid}/repair` | 质量修复（plan-only 默认） |
| `POST /api/v1/upload`（增强） | dedup + profile + 质量提案 lane |

新 tier-2 tools（经 `app/tools/__init__.py` 注册）：`ingest_dataset`、
`describe_workspace`、`cancel_execution_run`、`query_federated_chain`、
`convert_raster_to_cog`；workspace 快照工具
`save_workspace_snapshot` / `list_workspace_snapshots` /
`restore_workspace_snapshot`（`app/tools/workspace_tools.py`）；质量审计与
修复工具 `audit_spatial_quality` / `repair_spatial_dataset`
（`app/tools/project_tools.py`）。

## 已知限制（如实清单）

- **内存后端 workspace restore 是 alias 模式**：session store 为内存后端时
  overwrite 是 replace-only，写回经 `store()` + 别名恢复读取语义并如实返回
  `"alias"`；字节耐用性只在 BlobStore 侧，内存后端本身仍随进程。
- **Eager Celery 模式塌缩 durable 语义**：无 Redis 时 `task_always_eager`，
  队列/跨进程复用/retry affinity 全部退化为进程内；`backend_variant=
  "in_process_eager"` 诚实标注但差异真实存在。
- **Governor 跨进程计数是 advisory/fail-open**：每 pod L1 才是权威；L2 已知
  漂移（崩溃窗口虚高 ≤1h TTL、DECRBY 钳零竞态、故障期增量丢失、并发预留
  竞态）方向都是多放行。
- **tool_cache 的 session-free 工具仍是 anonymous 域**：键 owner 域派生自注入
  `session_id`；不带会话且未显式传 `owner_scope` 的工具跨用户共享条目。
- **describe-cache scope 依赖 fabric 路由**：tenant/owner scope 由路由从
  source 行派生；绕过路由直连 describe 的调用方必须自带 scope
  （`metadata_cache` 文档化约束）。
- **内容指纹的 descriptor 弱点**：晋升内容已由 payload-digest 键覆盖，但会话
  `RefDescriptor.content_hash` 仍是 opt-in（`WEBGIS_REF_CONTENT_HASH`，默认关）
  ——关闭时热路径无内容摘要，死 ref 检测靠 probe 而非内容比对。
- **链式联邦 tool 已暴露，remote-worker placement 仍进程内**：队列路由就位，
  执行仍在本进程 bounded executor，跨 worker data-locality 调度未做。
- **Zarr 诚实不可用**：不加依赖；typed `ZarrUnavailable`，pyarrow 同理
  （dict lane 普适兜底）。
- **Terrain 全量读预算化而非消除**：2× float64 字节守卫拒绝超预算输入；
  STAC DEM 已分条流式（逐位一致）。
- **DNS-rebinding 微窗口**：SSRF pre-flight + 逐跳重验已就位，re-resolve 与
  connect 之间仍有微窗口（connect-time IP pinning 为 ADR-0053 follow-up）。

## 测试指针

| 领域 | 测试 |
|---|---|
| 修订账本 / BlobStore / 晋升 GC / 复活 | `tests/data/test_artifact_revisions.py`、`test_durable_blob_store.py`、`test_wave1_promotion_gc.py`、`test_wave1_revival_content_hash.py` |
| Workspace V4 | `tests/data/test_workspace_v4.py` |
| Ingest / 上传幂等 | `tests/data/test_ingest_seam_v4.py`、`test_upload_identity_v4.py` |
| RepairPlan | `tests/data/test_repair_plan_v4.py` |
| Arrow 双车道 / 聚合统一 / fast lane | `tests/unit/test_data_plane_arrow_v5.py`、`test_arrow_ops_v5.py`、`test_geoparquet_fast_lane_v5.py` |
| Chunk / Zarr | `tests/unit/lib/test_geo_raster_chunks_v6.py`、`test_geo_raster_zarr_v6.py` |
| 调度队列 / 复用索引 / 取消 | `tests/unit/test_geocompute_v5_scheduler.py` |
| Governor 量规 / 跨进程 / watchdog | `tests/unit/test_geocompute_governance_wave8.py` |
| Optimizer（AND-split / semi-join / 投影派生） | `tests/unit/test_data_fabric_optimizer_v5.py` |
| Cache chaos / 单飞 / 失效 | `tests/unit/test_cache_chaos_v5.py` |
| Lineage 接线 / 脱敏 / runtime_env | `tests/unit/test_lineage_v5_wiring.py` |
| 配额 / 保留 / GC parity | `tests/data/test_quota_retention_v5.py` |
