# Lakehouse V7 — PR Summary（草稿；Round 2 后定稿）

## Problem / Motivation

V6 湖仓只有 time,y,x 隐式 cube、无检索目录、S3 后端无流式/重试/ETag、
无零字节组合对象、无对象级 GC/发布。V7 按 Epic 04 Must-have（Scope
A-I）补齐为云原生时空湖仓，全部 additive，不建第二事实源。

## Current-state audit

见 `00-baseline.md`（V6 入口 file:line 清单、事实源判定、缺口 G1-G8、
资源复杂度、并发冲突面）。

## Architecture

见 `01-architecture.md`（经 Subagent-A R0 挑战修订）。要点：
- labeled cube v2（zarr v3 dimension_names + attr 镜像；V6 合成投影）；
- 确定性 chunk planner / labeled selection（触达块 ∝ 窗口）；
- RS cube 角色化组装（几何恒等对齐、绝不重采样）；
- S3 multipart/流式/ETag/重试/枚举/清扫；
- Virtual DataObject（零字节复制 + 分片组合 = 大元数据生产路径）；
- catalog 投影（migration 0034 additive；多 owner 唯一键；STAC 纯投影）；
- 零字节 project 发布（Artifact find-or-create + record_revision + catalog）；
- dereference GC（union blob 保护；向下可达性；plan token + watermark +
  execute 重验；running-runs 根；72h 宽限）；
- DR scrub（确定性采样 / 全量 / ETag 记录值比对）。

## Implementation waves

23 waves 全部完成（见 `03-progress.md`；commits 0ad93bb1..HEAD）。

## Key code paths

- `app/services/lakehouse/`：cube_schema / chunk_planner / labeled_selection
  / rs_cube / virtual_object / catalog_service / catalog_stac /
  project_publish / lakehouse_gc / lineage /（扩展）cube_store /
  cube_service / data_object / dr / xarray_adapter
- `app/services/s3_blob_store.py`、`durable_blob_store.py`（流式/枚举 parity）
- `app/models/lakehouse_catalog.py`、`migrations/versions/0034_lakehouse_catalog.py`
- REST：`app/api/routes/lakehouse.py`（8 个新端点）+ schemas

## Data/persistence changes

- 新表 `lakehouse_catalog_items`（投影；可重建）；迁移 0034 additive、
  单 head、SQLite up/down/up 实测、模型↔迁移漂移守卫通过。
- manifest payload 新增可选 `labeled` / `virtual` / `durable` 投影
  （additive；v1 消费者不受影响）。

## API/contract changes

- 新 REST 端点（全部所有权门禁；GC 端点 admin 门禁）：POST cubes/rs、
  cubes/labeled/window、publish、revoke、objects/{id}/scrub、gc/plan、
  gc/execute；GET catalog、catalog/stac、projects/{pid}/objects/{oid}、
  objects/{id}/lineage。
- manifest kind 新增 `virtual`（DATA_OBJECT_KINDS 单点常量）；
  cube_schema_version 新增 v2（读取侧按版本分派，v1 语义不变）。

## Security implications

- GC 全局破坏性面 → admin 角色门禁（R1-2）；publish/revoke/catalog
  双域 fail-closed 404（不泄漏存在性）；virtual 跨 owner child
  owner_mismatch 一票否决；RS 源路径数据目录闸 + charset 闸；
  producer redact 通道防秘密入身份；multipart digest 键守卫。

## Performance evidence（结构性）

- 窗口读触达块数 ∝ 窗口（小窗口 1 块 vs 全量 24 块断言）；
- multipart/流式 get 峰值内存 O(chunk)（tracemalloc 断言）；
- 10k chunks 分片组合发布（25 shard + virtual）峰值内存有界 + 组合
  身份确定性；scrub 采样触达 ∝ K；
- GC plan 元数据级（计数器证明：1 manifest = 2 次 get，0 chunk 读）；
- catalog 分页 limit ≤200 + 有界计数（超 10k 报下界）。

## Local test matrix（精确结果）

- `tests/data`：**534 passed, 6 skipped**（zarr 3.3.0 + xarray 2026.7.0
  本机已装；importorskip 项在无依赖环境自跳过）
- `tests/benchmarks/test_lakehouse_v7.py -m perf`：4 passed（V6 harness
  同跑 4 passed 2 skipped）
- `tests/test_ci_perf_coverage_contract.py`：8 passed（NIGHTLY_ONLY 注册
  校验）
- `tests/test_deploy_migration_wiring.py`：21 passed（模型↔迁移漂移守卫）
- `tests/data/test_lakehouse_migration_v7.py`：2 passed（单 head + up/down/up）
- 邻接回归：test_s3_truthfulness / test_data_fabric_catalog_sync /
  real_services smoke：26 passed
- ruff（app/ tests/ main.py manage.py 全仓口径）：0 findings

## Review Round 1 findings/fixes

28 项 R0（架构挑战）+ 23 项 R1（代码审查）全部处置：
CRITICAL×5（GC/S3 datetime、GC admin 门禁、坐标平移、virtual owner
死代码、幻影 revision location）+ gate MAJOR×3（xarray 验收、verify
分派、项目域 GET）已修并带回归；MINOR 14/16/17/18/19/21/22 已修；
13/15/20 记录为 follow-up。详见 `05-review-findings.md`。

## Review Round 2 findings/fixes

（Round 2 完成后填写）

## Rebase/integration verification

（push 前填写：origin/master 位置 + 复测结果）

## Backward compatibility

- V6 cube/端点/manifest 行为零变化（全套 V6 测试保持绿）；v2 store 对
  V6 读/fork 入口 typed 拒绝（不静默读错轴）。
- `artifact_revisions`/`artifacts` 既有消费者不受影响（发布行带
  `metadata.lakehouse` 标记）。

## Known limitations

- v2 labeled cube 的 CoW fork 未实现（typed 拒绝）。
- GC 与删除竞态存在有界残余窗口（watermark + 重验已收敛至最小）。
- ref:cube 形态的 catalog 行不参与 reconcile 对账（活性归 probe 通道）。
- STAC assets.data.href 为合成形态（content_location 未入目录投影）。

## Follow-up candidates

- v2 CoW fork（chunk 级引用计数 / manifest-only fork）
- catalog 行 reconcile 覆盖 ref 形态 + STAC href 真实化
- dask/分布式 labeled cube 写路径（当前单进程 64M cells 预算）
- DR backup blob 的 GC 依赖声明（当前 GC 可回收派生备份）
