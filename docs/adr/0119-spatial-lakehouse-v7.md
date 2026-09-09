# ADR-0119: Spatial Lakehouse V7 — Cloud-Native N-D Geospatial Lakehouse

- status: Accepted
- date: 2026-09-09
- relates-to: ADR-0118 (Spatial Data Lakehouse & Cube V6), ADR-0096 (raster runtime non-goals), ADR-0101 (GeoCompute Data Fabric V4), ADR-0103 (Data Artifact Workspace V3)

## Context

V6 建立了 DataObject 身份（manifest CAS）、BlobStore FS/S3 双后端、Zarr
cube（time,y,x per-band）、CoW 修订与 DR 基线，但四项能力被显式 deferred：
xarray/n-D labeled cube、catalog 检索、ETag 远端同步、S3 multipart 流式。
V7 将湖仓从"2D/3D 数据对象"升级为云原生时空湖仓，同时不建第二事实源。

## Decision

**全部 additive，字节真相仍只有 BlobStore，身份仍只有 manifest。**

1. **N-D labeled cube（schema v2）**：zarr group，数组以 v3
   `dimension_names` 绑定维度（R0-1：`_ARRAY_DIMENSIONS` attr 仅镜像）；
   维度白名单 `{time,band,polarization,vertical,y,x}`，y/x 恒为末两轴；
   坐标显式（y 降序 / x 升序 / 标签唯一）；网格对齐 = 几何恒等
   （逐元素相等），dtype 逐变量（uint8 mask 与 float32 影像同 cube）；
   **绝不静默重采样**。manifest payload 只带 `coords_summary`
   （完整坐标在 store —— 守 64KiB 身份闸）。V6 cube（v1）经合成投影
   永远可读；V6 读/fork 入口对 v2 typed 拒绝（版本闸 —— R0-2）。
2. **Chunk planner / labeled selection**：确定性纯函数（同输入同计划）；
   触达块 ∝ 窗口；rechunk 纯元数据（时间轴 chunk=1 为 CoW 契约推广）；
   计划块 cap 50k、单元预算 8M cells。
3. **遥感 cube**：optical/sar/mask 角色化多源组装（通用角色，无项目
   硬编码）；网格恒等 = 纯几何（width/height/crs/transform —— mask 与
   影像 dtype 合法共存）；完整时间轴 + (time,variable,label) 唯一性
   typed 强制；组装期 64M cells 预算。
4. **S3 生产化**：multipart 流式 put（part 8MiB / ≤10k parts / 流式
   digest / 终败 abort 无残留 / part 级重试退避）；文件路径发布
   digest 进 `create_multipart_upload` Metadata（关闭 sidecar 时窗 ——
   R0-13）；ETag 记录 sidecar（**非身份**，digest 才是身份 —— R0-14）；
   `iter_objects` 分页枚举；超龄 multipart/staging 清扫（有界 cap）；
   ETag head 比对属 scrub 证据，不参与 put-if-absent 判定。
5. **Virtual DataObject**：kind=`virtual`，children 引用身份
   （content root = 有序 ids canonical sha256），零字节复制；深度 ≤8、
   全局节点 ≤10k（菱形 DAG 不指数 —— R0-28）；verify 递归
   （children 缺失 ≠ verified —— R0-4）；物化 lazy/inline(预算)；
   **分片组合（`publish_sharded_object`）是 10k+ chunks 大元数据的
   生产路径**（单 manifest 64KiB 闸是诚实边界）。
6. **Catalog**：`lakehouse_catalog_items` 表 = 可检索**投影**
   （事实源 = manifest + 台账，可重建）；代理 PK +
   `unique(owner_type,owner_id,content_sha256)`（多 owner 发布 —— R0-7）；
   bbox 复合索引 + tags GIN 仅 PG（JSONB —— R0-25）；强制分页
   （limit ≤200 / 有界计数下界披露）；`reconcile` 以 manifest 可解析性
   收敛投影漂移（R0-10）。STAC 1.0.0 Item/Collection 是纯投影
   （必填字段缺失 typed 拒绝，不可投影条目 skipped 披露）。
7. **Project publishing**：session → project **零字节发布** =
   find-or-create Artifact（storage_ref=数据对象）→ `record_revision`
   → catalog 行（R0-8）；owner 链 = `verify_session_owner` +
   `project.owner_id`（查无/他人 → 404 族，不泄漏存在性）；幂等 =
   revision/catalog 双唯一键；撤销 = tombstone（内容仍可解析）。
8. **GC**：dereference-based，元数据级；blob 保护集 = **全部活
   manifest 引用并集**（R0-17：fork 链共享 blob 不得误删）；可达性从
   根**向下**沿引用边定点传播；根 = catalog active + artifact_revisions
   + running runs 引用（R0-18）+ 宽限（默认 72h ≥ registry TTL ——
   R0-19）；plan token（候选集 digest + watermark）+ execute 逐引用
   重验（R0-12：计划期新发布 → token 漂移拒绝/引用重验剔除）。
9. **DR scrub**：确定性采样（sha256 派生序取 K）或全量 digest 校验；
   ETag 只做记录值 vs head 比对；virtual 对象走深度状态；修复策略
   仅 safe-only（工作副本重灌），绝不伪造修复。

## Consequences

- zarr/xarray 仍是 optional 依赖（ADR-0096 纪律）：probe-gated typed
  降级；测试 importorskip；venv 需显式安装以启用。
- 单 manifest 64KiB 闸不放松：大元数据对象必须走分片组合
  （shard+virtual），身份由组合体承担。
- working-copy 的 in-place chunk 写（RMW）必须先断硬链接
  （`st_nlink>1` → 拷贝）；v2 labeled store 的 fork 是后续能力
  （当前 typed 拒绝）。
- DR backup chunk 是 GC 可回收的派生数据（建议 GC 后重备份；
  CAS 重灌廉价）。
- 部署前提：对象存储 prefix 下不得配置 lifecycle 自动删除规则
  （带外删除由 scrub etag_check 探测）。
- 迁移 `0034_lakehouse_catalog` additive（单 head 保持；up/down/up
  SQLite 实测）。

## Non-goals / Deferred

- v2 labeled cube 的 CoW fork（当前 typed 拒绝；需要 v3 chunk 级
  引用计数或 manifest-only fork）。
- catalog 全文/GIN 查询的 SQLite 对等实现（行集内存过滤已够本 scope）。
- xarray 写路径的 dask 分布式分块（单进程写预算 64M cells）。
- 对象存储 bucket lifecycle 策略的自动检测。
