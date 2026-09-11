# ADR-0133: GeoCompute V8 — Distributed Spatial Compute Fabric

## 状态（Status）

Accepted（2026-09-11，与 feat/geocompute-v8-runtime 同枝落地）

## 背景（Context）

V6/V7（ADR-0125）交付了 run 级控制面（状态机/lease fencing/抢占/公平/
背压）与能力剖面/放置三层语义/局部性声明。V8 目标审计（对照
「Scheduler → Worker Registry → Queue → Reservation → Execution →
Artifact Exchange → Observation」全链）确认了七项缺口：

| # | 缺口 | 审计证据 |
|---|---|---|
| a | 能力缺磁盘/GPU 型号/卡级明细 | capability 只有 gpu_count 汇总 |
| b | 账本只有 rows/bytes/units 三维，`estimate.memory_mb` 无任何调度路径消费 | `_claims_for_row` |
| c | 空间分区零命中（plan/ops/executor/durable 无 partition/tile/halo） | grep 审计 |
| d | BlobStore 的 content-hash/流式原语存在但 geocompute 未接线；checkpoint 纯内存无 spill | durable_blob_store vs NodeResultStore |
| e | 无 speculative duplicate / poison quarantine | 仅 run 级 straggler 事件 |
| f | GPU 只是 envelope 等值 gating；无计数级预留/多卡路由/CUDA 回退 | 账本无 gpu 维 |
| g | 观测缺 transfer/spill/retry/utilization/resource-rejection/OOM-avoided | events.bytes 列几乎无写入方 |

结构性事实（避免误读现状）：cluster 的 run 执行体是 **coordinator 本地
线程**（只有 `durable_job` 节点穿 celery）；控制面原语（fencing/reclaim/
取消/背压）已被测试锁定 —— V8 一律**扩展而非替换**，表列全部 additive。

## 决策（Decision）

### D1 能力清单 V8（Phase B）

`WorkerCapabilityProfile` 增 `gpus[]`（型号+显存，nvidia-smi 单次探测）
与 `disk_free_mb`（`WEBGIS_BLOB_ROOT`/temp 的真实探测）。探测缺席 = 0 =
诚实未知，绝不虚构。`gpu_count/gpu_mem_mb` 仍是 gating 汇总真相（V7 语义
不变）；卡级清单服务多卡路由（advisory）。

### D2 账本五维 reservation（Phase C —— 「先预留后启动」）

`ResourceClaim`/`geocompute_resource_usage` 增 **mem_mb/gpu** 两维：

- **mem_mb**：run 的节点 `estimate.memory_mb` 之和（钳 2 TiB 防自 DoS）。
  enforcing 账本在 claim 同事务做条件 UPDATE —— 超限即留队，
  `waiting_resource[resource:mem_mb]` 事件 + `oom_avoided` 计数（被预防
  的「先启动再 OOM」直接可量化）；
- **gpu**：`ResourceRequest.gpu` 计数预留 —— 防 N 个 GPU run 超卖同一
  池卡（卡级独占分配仍归 ModelOps device plan；cluster 承诺的是池容量
  不超卖）；
- 预留值持久化在 `geocompute_runs.reserved_mem_mb/reserved_gpu`（与
  reserved_rows/bytes/units 同一「认领 reserve、终态/reclaim 同事务精确
  归还」纪律，账本守恒不变量延续）；
- **advisory 模式语义逐字节保留**：超限仍记账放行（只报维度不阻塞）；
  enforcing 与否仍是显式部署决策。

同租户准入顺序（`scarcity_rank_key` × `fair_pick.within_tenant_key`）：
GPU run > 高内存 run > 普通 run，同档合格 worker 少者优先 —— 槽位紧张
时稀缺资源 run 不再被同租户普通 run 饿死；跨租户公平环不动，缺省键 =
V6 的 `(-priority, id)` 逐字节不变。

### D3 空间感知分区（Phase D）

`ExecutionNode.partition: PartitionSpec`（**参与语义指纹** —— seam 语义
影响输出）。durable 节点声明分区后，executor fan-out：

- **raster_grid**：像素窗口网格 + halo（钳界）。tile job 在 halo 窗口上
  裁剪并计算（源路径经参数注入 —— 跨越「worker 无 in-process 上游可见
  性」的 V7 边界，路径字符串是既有 raster 载荷通货）；合并 = core 窗口
  写回全幅（tile 文件内偏移换算裁除 halo），core 并集精确覆盖全幅、互不
  重叠 → 确定性 mosaic，无接缝混合决策。CRS/transform 与源一致。
- **vector_grid**：bbox 网格 + halo_ratio。代表点（几何 bbox 中心）唯一
  分配（无 halo 时零复制零丢失）；halo 邻域复制，合并按内容指纹去重
  （诚实边界：源内真重复同被塌缩 —— 与「同内容要素」不可区分）。代表点
  不可判定的要素 fail-open 保留在分区 0，绝不静默丢失。空 cell 是合法
  分区（短路空输出，绝不伪装「缺失输入」打挂 tile）。
- tile job = 普通 durable job：独立幂等键（`_partition` 进参数）、独立
  重试（stale 可重派；failed 即该 tile 终局）、独立放置（profile 队列/
  worker 守卫全部复用）；取消/deadline 级联持久取消全部在飞 tile。
- 自适应 tile 数（内存预算上调、行数下界收缩）与 `detect_skew` 偏斜报告
  （证据不处置）。分区声明 × 类别的支持性走封闭词表
  （filter/vector_operation/raster_window_operation），越界
  `PARTITION_UNSUPPORTED` 类型化拒绝。
- graph 校验放宽：partition 声明的 raster_window_operation 允许 durable
  （tile 结果经 result_summary 回传，合并本地完成 —— V4 的「raster 不
  支持 durable 交接」限制对分区路径不成立）。

### D4 Artifact Exchange + spill（Phase E）

把既有 BlobStore（content-hash/put-if-absent/digest 校验）接进执行面：

- `ArtifactExchange`：键 = sha256(逻辑字节)（全局去重）；zlib 压缩（钳
  64MiB，超出 raw）；读回 digest/尺寸双校验 + 瞬态 IO 有界重试（确定性
  腐坏立即类型化失败 —— 重试同一份坏字节无意义）；
- `NodeResultStore` spill：超预算载荷落盘，LRU 只留 stub（按驻留字节小
  额记账，绝不自逐出），命中按需重hydration（锁外 IO；失败 = 复用 miss
  诚实重算）。`WEBGIS_EXCHANGE_ROOT` 未配 → 整体停用，V7 丢弃语义逐字
  节保留；
- `geocompute_artifacts` 元数据表（载荷字节真相仍在 BlobStore；行丢失 =
  孤儿字节由 TTL 清扫兜底，绝不是数据丢失）+ coordinator tick 有界批
  TTL 清扫。

### D5 运行时韧性（Phase F）

worker lease/死worker 恢复/取消/超时/背压/配额 V6 已交付，V8 补：

- **poison quarantine**：per-owner × 节点语义指纹的非瞬态终局失败计数 →
  冷却窗内快失败（`POISON_QUARANTINED`）—— 不再消耗派发/重试预算。
  WORKER_LOSS/瞬态不计（那是 reclaim 与重试的职责）；惰性解封；全链路
  fail-open（隔离是保护机制，绝不倒灌）；
- **speculative duplicate**（straggler 缓解）：`WEBGIS_SPECULATIVE_AFTER_S`
  （默认 0 = 显式 opt-in —— 副本有真实算力成本）超时未终态的 durable
  job 派独立幂等键副本，先到先得（primary 优先），败者请求持久取消
  （late success 由 jobs 状态机丢弃）。门控 `deterministic ∧ reuse
  ALLOW` —— 只对幂等节点双跑；
- work stealing 诚实声明：celery 共享队列天然跨 worker 抢占，cluster 层
  不再重复实现绑定式 stealing（与 V7 D1 同一诚实语义）。

### D6 GPU（Phase G）

CUDA 不可用回退：`ResourceRequest.fallback_cpu=True` 且等待窗内集群无任
何存活 GPU worker → coordinator 持久剥离 gpu 要求改派 CPU（CAS 限
dispatchable 态；`gpu_fallback` 事件 + 计数，诚实可见降级）。False =
V7 语义：无限期留队等待。计数级池预留见 D2；多卡路由/卡内分配是
ModelOps device plan 的职责（cluster 只承诺「有合格 GPU worker 才派发 +
池不超卖」，不越界重写推理 —— Goal 边界）。

### D7 观测（Phase H）

`/cluster/metrics` 快照补齐：`resource_rejections{dim}`、`oom_avoided`、
`gpu_fallbacks`、`spill{count,bytes,rehydrate_*}`、`transfer{bytes_total}`
（events.bytes 列首个规模化写入方 = worker node_output_ready）、
`cache{worker_cache_hits}`、`lineage{completed/reused/lost/partition/
speculative/quarantined}`（任务谱系：复用 vs 实算 vs 丢失 vs 分区 vs
投机 vs 隔离）、`utilization{reserved/capacity/ratio}`、`quarantine[]`
（伪名化）。全部封闭词表 + created_at 索引有界聚合，无 per-run/per-user
基数。

## 事实源边界（延续 V6/V7）

| 域 | 真相 | V8 变化 |
|---|---|---|
| run 生命周期 | `geocompute_runs` | +reserved_mem_mb/reserved_gpu（默认 0） |
| 账本 | `geocompute_resource_usage` | +mem_mb/gpu 两维（usage/limit） |
| 节点 job | `analysis_tasks` | 不变（tile/speculative 经 params/kwargs 穿透） |
| 终态证据 | `geocompute_run_evidence` | 不变 |
| 事件 trace | `geocompute_run_events` | 词表 +6（gpu_fallback/partition_planned/speculative_*/poison_quarantined/artifact_spilled）；bytes 列启用 |
| artifact 元数据 | `geocompute_artifacts`（新） | 字节真相在 BlobStore |
| 毒任务隔离 | `geocompute_task_quarantine`（新） | per-owner × 指纹复合主键 |
| 载荷 | session ref / raster_path / BlobStore | 不变（无第四种交接通道） |

迁移 `0035_geocompute_v8_fabric`（down = `c0d8322aa2cb`，与并行 epic 双头
共存，slug 不冲突）：全 additive、可重入 DDL、create_all-coexistence guard。

## 兼容与回滚

- 全部新列/新字段带缺省；旧 plan/旧行/旧 worker 行为逐字节兼容（回归
  357+ 全过）；advisory 账本/fair_pick 缺省键/exchange 停用/投机停用均
  是显式 opt-in 语义；
- 回滚 = 还原 feature 分支（无数据迁移不可逆项；新表可 drop，新列可
  drop，V8 代码路径均有 V7 等价退化分支）。

## 风险

- 分区合并的 raster 路径依赖「coordinator 与 worker 共享文件系统」——
  与既有 raster_path 载荷同一主机假设（V4 起不变），跨对象存储的 tile
  直传留给 Data Fabric 通道（Goal 边界外）；
- 投机副本有真实算力成本 —— 默认停用，运维显式开启；
- enforcing 账本依赖 plan 估计的诚实性（`confidence: assumption` 可见）；
  估计缺失 = 不预留该维（advisory 退化），绝不虚构。
