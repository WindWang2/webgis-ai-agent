# Lakehouse V6 — 00 Baseline Audit

- 日期: 2026-09-08
- 分支: `feat/data-v6-spatial-lakehouse-cube`
- Baseline commit: `445ad30`（origin/master，PR #1162 merge）
- Worktree: `/home/kevin/projects/webgis/lakehouse-v6`
- 并行 Epic: geocompute-v6 / harness-v5 / query-v6 / science-v4（同 baseline 分叉，注意共享文件冲突）

## A. 事实图（file:line 级证据）

### 已实现（真实，可复用）
| 模块 | 职责 | 证据 |
|---|---|---|
| `app/services/durable_blob_store.py` | BlobStore 接口（put/get/exists/size/delete/put_json）+ FilesystemBlobStore；content-addressed 64-hex sha256 key；tmp+os.replace 原子发布；put-if-absent CAS；docstring 预留 S3 seam | durable_blob_store.py:71-112,169-199 |
| `app/services/artifact_registry.py` | session artifact 台账；disk-cursor 模式（`is_raster_ref`/`raster_png_path`/`raster_ref_exists`/`probe_ref`） raster PNG 磁盘游标先例；GC 保护 tier workspace/persistent | artifact_registry.py:289-324,827 |
| `app/services/artifact_revisions.py` | durable revision 台账：record_revision/head_revision/referencing_counts/pinned_content_sha256s/pin_artifact/clone_artifact(零拷贝)/project_quota_usage | artifact_revisions.py |
| `app/services/data_fabric/materialization_service.py` | GeoParquet 写通道（opt-in，`ref:fabric-parquet/`前缀:44）；session_id 白名单:48 | materialization_service.py:44,135-139,224-284 |
| `app/services/data_fabric/vector_carrier.py` | GeoArrow/Arrow carrier；`table_to_geoparquet`(zstd):501-504；schema geo metadata bbox/geometry_types/CRS；schema-freeze batches | vector_carrier.py:71-149,501-504 |
| `app/lib/geo_raster/cog.py` | COG 真实写/校验（GDAL COG driver，blocksize 512，overview ladder 单一定义） | cog.py |
| `app/lib/geo_raster/chunk.py` | RasterChunkDescriptor（chunk_id=grid-identity digest）、opt-in chunk cache、raster_runtime_capabilities | chunk.py:332,444 |
| `app/lib/geo_raster/reader.py` | RasterReader window 读、`_budgeted_read`、cog_structure_ok | reader.py |
| `app/lib/geo_raster/zarr.py` | Zarr typed stub：lazy import、ZarrUnavailable、write_zarr_cube(time,y,x)、zarr_chunk_descriptors；**无 service 调用方** | zarr.py:1-14 |
| `app/lib/data/fingerprints.py` | canonical_dumps/sha256_hex/canonical_fingerprint/compute_reuse_fingerprint（**零消费者**） | fingerprints.py |
| `app/lib/data/artifact_contract.py` | typed ArtifactContract 投影 | artifact_contract.py |
| `app/services/workspace/snapshot.py` | Snapshot V4 manifest-of-pointers，≤128，project-domain home | snapshot.py:44-52 |
| `app/services/workspace/durability.py` | materialize_ref_payload/verify_durable_pointer(4态)/read_back_payload(digest-verify)/write_back_session_payload(**alias mode**:308-343) | durability.py |
| `app/services/data_lifecycle/quota.py` | ProjectQuotaPolicy/RetentionPolicy/orphan sweep/`promotion_blob_protection` 单一保护谓词 | quota.py |
| `app/services/artifact_lifecycle.py` | promotion refcount GC（168h grace）/sweep_aged_artifacts | artifact_lifecycle.py:279-443 |
| `app/services/lineage_service.py` | DB lineage（INV-LIN1-4）、写时 redaction、tenant-scoped 遍历 | lineage_service.py |
| `app/services/provenance/` | RunManifestBuilder/compute_run_fingerprint/redact_provenance_args | provenance/manifest.py |
| `app/services/data_fabric/security.py` | SSRF：validate_url 支持 s3/minio/postgres schemes:117、per-hop 重校验、bounded_get | security.py:95-151,319-353 |
| `app/services/ref_lifecycle.py` | 权威 invalidation hooks | ref_lifecycle.py |
| `app/services/data_catalog/` | session catalog + lineage_query | catalog.py |

### 部分实现 / stub / 悬空
| 项 | 状态 | 证据 |
|---|---|---|
| `ref:fabric-parquet/<id>` | **write-only 悬空引用**：无任何 resolver；TODO(fabric-artifact-ledger) 未接台账；id=random `secrets.token_hex(8)` 非 content-addressed | materialization_service.py:241-243,263；全仓 grep 仅测试断言前缀 |
| GeoParquet 产物 GC/台账可见性 | 不可见（无 ledger row→所有 GC 盲区） | 同上 |
| S3 object storage | `s3_storage_seam.py` 仅 listing/probe 合成 fixture，无 GET；无 boto3/s3fs/fsspec 依赖 | s3_storage_seam.py:37-56 |
| Zarr | typed stub，依赖未声明，零调用方 | zarr.py,chunk.py:444 |
| Cube/xarray | 完全缺失（无 xarray/cftime） | 全仓 grep |
| Session content hash | opt-in 默认 OFF，≤1MiB cap | schemas/ref_descriptor.py:283-313 |
| GeoParquet 写入质量 | 无 row-group sizing/statistics/spatial sort/bbox column；单文件 | vector_carrier.py:501-504 |
| `raster_store.save_png` | 非 `open(...,"wb")` 非原子写（repo 唯一例外） | raster_store.py:37-40 |
| workspace restore | memory backend 走 alias mode（进程内存指针） | durability.py:308-343 |
| compute_reuse_fingerprint | 已实现但零消费者 | docs/data-v3/audit/00-audit-index.md |

## B. 关键约定（ gates/conventions ）
- pytest: timeout=60 thread; markers heavy/perf/cartography/real_services; conftest `_ENV_BASELINE` 新 env 必须加 + `.env.example`（双 parity 锁）
- Byte gates: QUALITY_MANIFEST / CONTRACT_DRIFT / OpenAPI snapshot（additive 也红）/ structural baselines / security manifest(no orphan rows)
- Perf baseline fail-closed；优先 structural ratio bands（模板 `tests/benchmarks/test_data_control_plane_v5.py`）；perf 文件注册三处同步（PR_LANE_PERF_FILES/ci-local.sh/production.yml）
- 新 route/schema → `API_SNAPSHOT_UPDATE=1 pytest tests/quality/test_api_compatibility.py`
- Alembic head: `0031_revision_indexes`；下一个 `0032_<slug>.py`，additive-only + existence guard；`tests/test_deploy_migration_wiring.py` drift 锁
- coverage ratchet 75%；ruff E4/E7/E9/F on app/
- ADR 下一个号码 0118；CHANGELOG 新 dated `[Unreleased]` 段
- 测试 style: tests/data/ 纯 python synthetic fixtures，root docstring 声明覆盖面

## C. 本 Epic scope 冻结

### 必须解决（P0）
1. `ref:fabric-parquet` 悬空引用 → 接入 artifact_registry disk-cursor + resolve 路径 + GC 可见性
2. Durable DataObject identity：content-addressed revision + logical alias + owner scope
3. Object storage S3-compatible backend（实现既有 BlobStore 接口，optional dep + honest degrade）
4. Session/promotion 内容身份：content hash 默认参与 DataObject identity（lakehouse tier 无 opt-in）

### 必须解决（P1，本 Epic 范围内）
5. GeoParquet lakehouse：row-group/statistics/bbox column/window 扫描 + schema evolution 最小闭环
6. Zarr 真实读写（V6）：group/array metadata、consolidated metadata、chunk read/write、partial-update→新 immutable revision
7. Spatiotemporal cube：time/y/x/band 维度、chunk 选择、quality mask、provenance
8. Lazy materialization：ref-only + window/chunk 读，无隐式全量加载（测试证明）
9. Dedup/cache identity：compute_reuse_fingerprint 接入第一个真实消费者
10. Workspace durability：lakehouse refs 的 verify/restore/pin 通道 + 非原子写修复
11. Lineage hooks：DataObject source→derived 边 + 环境指纹 + redaction
12. Retention/GC：新 artifact class 纳入既有 GC 闸 + DR（backup/verify/orphan/corruption）
13. Security：owner check、SSRF（复用）、path traversal、metadata size cap、token redaction
14. Benchmarks：structural ratio 风格（window vs full、chunk reuse、parquet scan、zarr time slice、memory peak）

### 明确不做
- GeoCompute 调度、Query Optimizer 逻辑计划、科学算法（其他 Epic 所有）
- 不新建第二 registry/store/manifest；不引入 xarray 重依赖（cube 基于 zarr + numpy 自建最小维度索引）
- catalog DB GIN 搜索（ADR-0103 已 deferred）
- ETag-based remote sync（ADR-0101 deferred → follow-up）
