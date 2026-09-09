# Lakehouse V6 — 02 Wave Plan

每个 wave：契约/测试先行 → 最小闭环实现 → targeted tests → ruff → progress 记录 → 小步 commit。

| Wave | 内容 | 关键文件 | 测试 |
|---|---|---|---|
| W1 | DataObject manifest 契约 + publish/resolve + session content hash 默认 ON | `app/services/lakehouse/data_object.py`、`app/schemas/ref_descriptor.py`、`tests/conftest.py`、`.env.example` | `tests/data/test_lakehouse_data_object_v6.py`、revival 测试回归 |
| W2 | S3BlobStore（同 BlobStore 接口）+ get_object_store 选择器 | `app/services/s3_blob_store.py` | `tests/data/test_s3_blob_store_v6.py`（fake client 注入） |
| W3 | fabric-parquet 悬空 ref 修复：registry disk-cursor 三件套 + 注册 + probe + GC unlink | `artifact_registry.py`、`materialization_service.py` | `tests/data/test_fabric_parquet_ref_v6.py` |
| W4 | GeoParquet 写入升级（row-group/statistics/bbox 列）+ window scan（row-group 剪枝） | `vector_carrier.py`、`lakehouse/vector_scan.py` | `tests/data/test_geoparquet_lakehouse_v6.py`（计数 row-group） |
| W5 | raster save_png 原子写 + COG→DataObject（grid identity+chunk checksums） | `raster_store.py`、`raster_tools_cog.py`、`lakehouse/raster_object.py` | `tests/data/test_raster_lakehouse_v6.py`（window 不全量读） |
| W6 | Zarr V6 cube store：group+consolidated metadata+compression+CoW 修订 | `lakehouse/cube_store.py` | `tests/unit/lib/test_lakehouse_cube_store_v6.py`（round-trip） |
| W7 | 时空 cube 服务：raster refs → cube（time/y/x/band、quality mask）+ `ref:cube/` + lazy window | `lakehouse/cube_service.py`、`artifact_registry.py` | `tests/data/test_cube_service_v6.py`（chunk 计数） |
| W8 | dedup/cache identity：input_fingerprint 复用键 + 跨 owner 隔离 | `data_object.py` | W8 并入 W1 测试 + `test_lakehouse_dedup_v6.py` |
| W9 | workspace durability：fabric-parquet binary lane + cube manifest lane + reopen | `workspace/durability.py`、`workspace/snapshot.py`（verify 扩展） | `tests/data/test_workspace_lakehouse_v6.py`（进程重启 reopen） |
| W10 | lineage：manifest.source_refs + registry inputs 边 + env_fingerprint + redaction | `data_object.py`、`provenance/` 复用 | `tests/data/test_lakehouse_lineage_v6.py`（无 secret） |
| W11 | DR：verify/backup/restore/orphan/corruption + GC 保护验收 | `lakehouse/dr.py` | `tests/data/test_lakehouse_dr_v6.py` |
| W12 | security：owner check/路径闸/metadata 尺寸闸/token redaction | 各处 + `data_fabric/security.py` 复用 | `tests/data/test_lakehouse_security_v6.py` |
| W13 | REST：`/lakehouse/*` 路由（objects/cubes/verify/scan）+ OpenAPI snapshot regen | `app/api/routes/lakehouse.py`、`app/schemas/lakehouse_schema.py` | snapshot gate + route tests |
| W14 | benchmark（structural ratio）+ ADR 0118 + docs + UBIQUITOUS_LANGUAGE + CHANGELOG + 质量产物 regen | `tests/benchmarks/test_lakehouse_v6.py`、`docs/**` | perf lane + gates |

## 风险与闸门清单（实现时逐项核对）
- [ ] env 变更 → conftest `_ENV_BASELINE` + `.env.example`（+ prod template 若 Settings 键）
- [ ] 新 route/schema → `API_SNAPSHOT_UPDATE=1 pytest tests/quality/test_api_compatibility.py`
- [ ] registry 语义变化 → 质量产物 regen（quality manifest/drift/certifications）
- [ ] perf 文件注册三处（PR_LANE_PERF_FILES / ci-local.sh / production.yml）或放 nightly
- [ ] ruff E4/E7/E9/F；coverage ≥75%（新增 app 代码必须有测试覆盖）
- [ ] zarr 3.3 兼容（create_array/consolidate API 双版本 fallback）
- [ ] content hash 默认 ON → 全 session-store 相关 targeted 回归
