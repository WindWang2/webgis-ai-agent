# ADR-0118: Spatial Data Lakehouse & Cube V6

- status: Accepted
- date: 2026-09-09
- relates-to: ADR-0104 (Data Control Plane V5 / GeoCompute V5), ADR-0101 (GeoCompute Data Fabric V4), ADR-0103 (Data Artifact Workspace Foundation V3), ADR-0096 (raster runtime non-goals)

## Context

Data Control Plane V4/V5 建立了 session ref / promotion / revision 台账与
BlobStore（FS-only）内容库，但数据对象层仍有结构性缺口：

1. `ref:fabric-parquet/<id>` 是 **write-only 悬空引用**（无 resolver、无
   台账、GC 盲区，materialization_service 内 TODO(fabric-artifact-ledger)）；
2. BlobStore 只有文件系统实现（接口 docstring 早已预留 S3 seam）；
3. session 层内容身份（`RefDescriptor.content_hash`）是 opt-in 且默认关；
4. Zarr 只是 typed stub（零 service 调用方），无多维 cube；
5. GeoParquet 写入无 row-group/statistics 控制，无 lazy 窗口读；
6. `raster_store.save_png` 是全仓唯一非原子持久写。

## Decision

**不建第二事实源**。V6 是组合层（`app/services/lakehouse/`），全部新能力
挂在既有主链路上：

1. **DataObject 身份**：manifest = canonical JSON → BlobStore CAS。
   id = manifest sha256（内容寻址，无 opt-in）；manifest 确定性（无
   wall-clock/随机）；owner scope 参与身份（跨 owner 同内容 = 不同逻辑
   对象，字节 blob 共享仍安全）；`input_fingerprint` 复用键走
   `app/lib/data.fingerprints.compute_reuse_fingerprint`（首个真实消费者）；
   manifest 携带 environment_fingerprint（有界版本事实）—— 环境参与
   可复现性身份。
2. **对象存储**：`S3BlobStore` 实现既有 `BlobStore` 接口（staging→copy
   原子发布；sidecar digest + 全读验证的 put-if-absent；endpoint 过既有
   SSRF 门且先于 optional boto3 导入）；env 驱动选择器，默认 filesystem
   零行为变化。
3. **Vector lakehouse**：GeoParquet 写入带 row-group bbox 地图（真实几何
   计算，schema metadata `webgis:row_groups`）；窗口扫描只读相交 row
   groups（无元数据退化为有界顺序读，正确性不变）；`ref:fabric-parquet`
   接入台账 disk-cursor（probe/GC/restore 全通道），悬空 TODO 闭合。
4. **Raster lakehouse**：COG → DataObject（grid identity + 有界 chunk
   checksums）；`save_png` 原子写。
5. **Zarr cube**：group cube（每 band 一个 (time,y,x) 数组，foundation
   attrs 契约），consolidated metadata，chunk 粒度窗口读；**不可变修订**
   = 硬链接 CoW fork（旧 store 逐字节不动，旧修订持续可验证）+ 内容寻址
   manifest。零 DB migration。
6. **Durability/DR**：workspace 快照支持 fabric-parquet（binary lane）与
   cube（manifest lane）；DR 提供工作副本校验/修复/备份/孤儿扫描；
   破坏性清扫仍归既有 GC 闸。
7. **REST 面**：`/api/v1/lakehouse/*` 五端点，全部 `require_owned_session`
   （SEC-08）+ owner 校验（越权 = 404，不泄漏存在性）。

## Consequences

- 内容寻址默认参与身份（DataObject 层无开关；session 层
  `WEBGIS_REF_CONTENT_HASH` 默认 ON，≤1MiB cap 不变，显式关回历史行为）。
- environment fingerprint 使跨运行时的同内容产物身份分离 —— 重跑可复现性
  判定因此可比；字节级 blob 去重不受影响。
- cube 的 fork 修订事件带 uuid 戳 —— 两次同内容 fork 是两个不同修订事件
  （不可变修订所需）；"同内容同 id" 语义保留给非 fork 发布。
- Lazy 读的结构性证明（row-group 剪枝计数、chunk 触达计数）进入测试与
  nightly perf harness（结构性证据优先，墙钟只做宽比带）。

## Non-goals / Deferred

- xarray 依赖与 n-D 通用 labeled cube（当前 time/y/x/band 以 zarr 多数组
  表达；xarray 适配为 follow-up）。
- DB schema 变更（manifest 走 BlobStore、revision 走既有
  `artifact_revisions`；Alembic head 零冲突）。
- catalog DB GIN 搜索（ADR-0103 deferred）、ETag 远端同步（ADR-0101
  deferred）、S3 multipart 大对象流式上传（当前预算内整备，超预算诚实
  manifest-only）。
