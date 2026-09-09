# Lakehouse V7 — Baseline Audit (Phase A, 只读)

- date: 2026-09-09
- base SHA: `8a33e3a53f0fa53873cace853758445192722dca` (origin/master)
- worktree: `/home/kevin/projects/webgis/webgis-ai-agent-lakehouse-v7`
- branch: `feat/lakehouse-v7-cloud-spatial-cube`
- 最近 PR: #1172 (quality-v2), #1171 (extensions-v2), #1170 (cartography-v5), #1169 (workbench-v5), #1168 (workflow-v4), #1164 (**lakehouse-v6**)
- 同域 PR: #1164 (Lakehouse V6), #1163 (geocompute-v6), #1167 (query-v6)
- open issues / duplicate work: 无 lakehouse-v7 同域 open PR / branch（`git branch -a | grep lakehouse` 仅 v6 归档）。

## 1. V6 真实生产入口（file:line 证据）

| 能力 | 入口 | 证据 |
|---|---|---|
| DataObject 身份/发布/解析/物化 | `app/services/lakehouse/data_object.py` | `publish_data_object` :241, `resolve_data_object` :354, `verify_data_object` :375, `materialize_data_object` :400 |
| 字节真相（FS） | `app/services/durable_blob_store.py` | `FilesystemBlobStore` :112（原子写 + sidecar digest） |
| 字节真相（S3） | `app/services/s3_blob_store.py` | `S3BlobStore` :93（staging→copy 原子发布 :191；put-if-absent :156；env 选择器 `get_object_store` :366） |
| Cube 写/读/CoW 修订 | `app/services/lakehouse/cube_store.py` | `write_cube` :126, `read_cube_window` :239, `fork_cube_revision` :389, `publish_cube` :310 |
| Cube 会话生产 | `app/services/lakehouse/cube_service.py` | `build_session_cube` :96, `read_session_cube_window` :250, `revise_session_cube` :328 |
| Vector 窗口扫描 | `app/services/lakehouse/vector_scan.py` | `scan_fabric_parquet_ref`（row-group bbox 剪枝） |
| Raster COG 对象 | `app/services/lakehouse/raster_object.py` | COG → DataObject |
| DR | `app/services/lakehouse/dr.py` | `verify_cube_store` :41, `repair_cube_store` :87, `backup_cube_chunks` :109, `scan_orphan_manifests` :158 |
| REST | `app/api/routes/lakehouse.py` | 6 端点，全部 `verify_session_owner`（SEC-08）；project scope 显式拒绝 :80 |
| 台账 | `app/services/artifact_registry.py` | `register_artifact` :454, `ref:cube/<id>` cursor :383, GC :920/:942 |
| DB 修订 | `app/models/project.py` | `artifact_revisions` :332（CAS by (artifact, content_sha256) unique :367） |
| Promotion | `app/services/project_artifact_promotion.py` | `promote_run_artifacts` :370（raster binary lane :267；**cube 不在 promotion 内**） |
| Catalog | `app/services/data_catalog/catalog.py` | session 级内存过滤（CatalogEntry/DataCatalog :291）；DB `spatial_catalog_items` 是 source-sync 投影（`app/models/data_fabric.py` :40） |
| Workspace 快照/DR | `app/services/workspace/snapshot.py` :803-870, `durability.py` :177 | cube manifest lane |
| 可选依赖纪律 | `app/lib/geo_raster/zarr.py` | zarr 非 requirements 声明依赖；probe-gated + `ZarrUnavailable` typed 降级；ADR-0096 deferred |

## 2. 事实源判定

- 字节唯一事实源 = **BlobStore**（`durable_blob_store.BlobStore` 接口，FS/S3 两实现）；工作目录（zarr store / parquet）是 hot copy。
- 身份唯一事实源 = **manifest canonical JSON**（id = sha256）；revision 台账 = `artifact_revisions`；会话台账 = `artifact_registry`。
- 未发现 V6 引入的第二事实源；`ref:cube/*` 是 disk-cursor（可由 manifest 物化重建，非第二真相）。

## 3. V6 Deferred → V7 Must-have 对应

| V6 deferred（ADR-0118 Non-goals） | V7 scope |
|---|---|
| xarray 依赖与 n-D labeled cube | A/B |
| catalog DB GIN 搜索（ADR-0103 deferred） | F |
| ETag 远端同步（ADR-0101 deferred） | D/I |
| S3 multipart 大对象流式上传 | D |

## 4. 缺口清单（P0/P1/P2/P3）

### P1（V7 必须闭合）
- **G1 n-D labeled cube 缺席**：cube 硬编码 `dims=["time","y","x"]`（cube_store.py:198）、单 band 数组名 `"b1"`（cube_service.py:153）；无 dims/coords/band/polarization/vertical 契约，无坐标校验，无 xarray。
- **G2 chunk planner 缺席**：窗口/铺排仅由 `window_side_from_budget` 决定（geo_raster/chunk.py:296）；无 workload/read-write/max-chunk-bytes/成本/adaptive rechunk。
- **G3 S3 非生产级**：无 multipart/流式（`put_blob` 整体 bytes :156）；无 ETag 捕获/校验；无条件写仅 head-based；无重试退避；无 multipart/staging 孤儿清扫。
- **G4 Virtual DataObject 缺席**：manifest 只允许 kind ∈ {vector_parquet,cog_raster,zarr_cube}（data_object.py:167）；无法引用不可变子对象而不复制字节。
- **G5 Catalog 缺席**：无 owner/project 维度、bbox/time、tags、分页的 lakehouse 对象检索；现有 DataCatalog 是 session 内存过滤，不覆盖 lakehouse 对象。
- **G6 Project 发布缺席**：REST 显式拒绝 project scope（routes/lakehouse.py:80-86）；`promote_run_artifacts` 无 cube/lakehouse lane；无发布修订/撤销语义。
- **G7 Dereference-GC 缺席**：现有 GC 是 session 孤儿 ref（artifact_registry.py:942）+ promotion-store 引用计数（artifact_lifecycle.py:402）；无 DataObject 可达性（manifest→blob、virtual→children）GC，无 multipart 孤儿清理，无 dry-run 计划与确定性删除证据。
- **G8 DR 无 scrub/ETag**：`verify_data_object` 全量逐 blob 读（无 sampling 预算）；无远端 ETag 比对；scrub 报告缺失。

### P2（本 Epic 顺带闭合）
- `iter_blob_files` 仅 FS（dr.py:171 s3 抛错）→ S3 需有界 list 以支持孤儿扫描/GC。
- 窗口读 REST 全量 tolist()（routes/lakehouse.py:208）——已有 cell 预算（8M cells），可保留但补结构性证明。

### P3（记录不动）
- `save_png` V6 已原子化；`ref:fabric-parquet` TODO 已闭合；无新增发现。

## 5. 资源复杂度审计

- `verify_data_object`：O(blobs × bytes) 全量读 — 大 cube（10k+ chunks）成本高 → V7 scrub 需要 sampling 模式。
- `scan_orphan_manifests`：FS 全枚举 O(files) 元数据级，可接受；S3 需分页 list（有界）。
- `publish_data_object`：两遍（量尺+写入）流式，预算闸齐备。
- Catalog 必须 DB 索引 + 强制分页（no unbounded response）。
- GC 必须元数据级（manifest/head），绝不全量物化。

## 6. 测试真值审计

- tests/data/*_v6: 453 passed / 6 skipped（本机，zarr 3.3.0 已装入 venv）；测试用 `pytest.importorskip("zarr")` 自跳过 —— CI 无 zarr 的 lane 会跳过 cube 测试（真值依赖 nightly perf lane）。
- V7 测试策略：结构性证据优先（chunk 触达计数、剪枝比、分页行数、GC 触达 blob 数）；依赖 zarr/xarray 的测试 importorskip；S3 用 fake client（无网络，同 V6 `test_s3_truthfulness.py` 纪律）。

## 7. 与并发 Epic 的冲突面

| 共享文件 | 风险 | 对策 |
|---|---|---|
| `CHANGELOG.md` | 高（每 Epic 都追加） | 最小追加一个 subsection |
| `docs/adr/` | 中（编号竞争） | 用 0119（当前最高 0118；重复编号有先例，suffix 唯一） |
| `migrations/versions/` | 中 | down_revision=0033_geocompute_v6_cluster（唯一 head，已验证 `alembic heads`） |
| `app/models/project.py` | 中 | 尽量不改既有模型；catalog 建独立新模型文件 |
| `app/api/routes/lakehouse.py` + `app/schemas/lakehouse_schema.py` | 低（V6 专属） | additive 扩展 |
| `tests/test_ci_perf_coverage_contract.py` | 中 | NIGHTLY_ONLY_PERF_FILES 追加一行 |

## 8. 环境事实

- venv: `/home/kevin/projects/webgis/webgis-ai-agent/.venv`（python 3.13，worktree 共享）。
- 本机已装 zarr 3.3.0 / xarray 2026.7.0（本 Epic 为验证装入；**不**加入 requirements —— ADR-0096 可选依赖纪律不变，probe-gated typed 降级保留）。
- Alembic 单 head：`0033_geocompute_v6_cluster`。
