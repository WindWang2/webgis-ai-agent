# Lakehouse V6 — 01 Architecture

## 0. 总原则（承接 ADR-0104:44-46 单一事实源红线）

不新建第二个 registry/store/manifest/state machine。V6 全部新能力以**组合层**落在现有主链路上：

```
                    ┌────────────────────────────────────────────────┐
 session refs ─────▶│ artifact_registry（台账，唯一会话真相）          │
 ref:raster/<id>    │   + V6: is_fabric_parquet_ref / fabric_parquet  │
 ref:fabric-parquet │     _path / fabric_parquet_ref_exists（disk-    │
 ref:cube/<id>  ───▶│     cursor 形状镜像 raster 先例 :289-324）       │
                    │   + V6: is_cube_ref / cube_store_path / …       │
                    │   + probe_ref 扩展（O(1) stat/is_dir）           │
                    │   + collect_orphan_refs unlink 覆盖（:909 镜像） │
                    └──────────────┬─────────────────────────────────┘
                                   │ promotion / workspace durability
                                   ▼
                    ┌────────────────────────────────────────────────┐
                    │ BlobStore（唯一内容持久后端，durable_blob_store）│
                    │   FilesystemBlobStore（既有）                   │
                    │   + V6: S3BlobStore（同一接口，lazy boto3，     │
                    │     staging→copy 原子发布，sidecar digest）      │
                    │   get_object_store() 选择器（env 驱动）          │
                    └──────────────┬─────────────────────────────────┘
                                   │ record_revision（既有 revision 台账）
                                   ▼
                    ┌────────────────────────────────────────────────┐
                    │ V6 组合层：app/services/lakehouse/              │
                    │  data_object.py  DataObject manifest（内容寻址  │
                    │  │                身份=manifest digest；同输入   │
                    │  │                同参数 ⇒ 同 digest ⇒ 免费去重）│
                    │  cube_store.py   Zarr V6 cube（group+consolid. │
                    │  │               metadata+chunk 读写+CoW 修订）  │
                    │  dr.py           verify/backup/restore/orphan  │
                    │  └── 复用：app/lib/data/fingerprints（唯一哈希    │
                    │       口径）、geo_raster/*、vector_carrier、     │
                    │       provenance fingerprint、ref_lifecycle     │
                    └────────────────────────────────────────────────┘
```

## 1. DataObject 身份（Wave 1 核心契约）

**DataObject manifest** = canonical JSON（`app/lib/data/fingerprints.canonical_dumps`）→
`sha256` → BlobStore `put_json`。**manifest digest 即 DataObject 的不可变修订身份**。

```json
{
  "schema_version": 1,
  "kind": "vector_parquet | cog_raster | zarr_cube",
  "owner_scope": {"session_id": "..."} | {"project_id": "..."},
  "storage": {"backend": "filesystem|s3", "location": "<blobstore-relative>"},
  "content_sha256": "<64hex>",           // 载荷字节摘要（文件/manifest 内 chunk 聚合）
  "byte_size": 123,
  "payload": { ... kind 专属投影：feature_count/bbox/crs/schema_fingerprint
               或 grid_identity/chunk_manifest/times/bands ... },
  "input_fingerprint": "<64hex>",        // 复用键：source digests + params（无时间戳！）
  "producer": {"capability": "...", "algorithm": "...", "env_fingerprint": "..."},
  "source_refs": ["ref:..."]             // lineage 输入边（redaction 后）
}
```

不变式：
- **确定性**：同 (kind, owner_scope, 内容, params) ⇒ 逐字节相同 manifest ⇒ 同 digest。
  manifest **绝不写 wall-clock**（时间戳只进台账/revision 行）。
- **内容寻址默认参与身份**：DataObject id 恒为 sha256，无 opt-in；session 层
  `RefDescriptor.content_hash` 默认翻 ON（≤1MiB cap 不变，env 可关）。
- **logical alias vs immutable revision**：alias 走 session_data.set_alias（会话级）/
  Artifact.name（项目级，既有）；revision 身份恒为 manifest digest，alias 只是其
  可读指针，绝不承载内容。
- **owner 隔离**：manifest 携带 owner_scope；解析/复用/缓存键都含 owner scope。
  BlobStore 字节级去重跨 owner 共享是安全的（访问永远经 owner-checked manifest 解析）。

## 2. 存储后端（Wave 2）

- `S3BlobStore(BlobStore)`：实现同一接口（durable_blob_store.py:71 docstring 预留）。
  - lazy `import boto3`；缺失 → `S3StoreUnavailable`（typed，ZarrUnavailable 同族纪律）。
  - **原子发布**：staging key（`staging/<uuid>`）→ put → CopyObject→final → delete staging；
    读侧只见完整对象或无对象。put-if-absent：HeadObject + sidecar digest 比对（sidecar
    `final.meta` JSON，FS sidecar 同语义）。
  - digest 校验读：读后 sha256 比对，不符 → None（绝不静默顶替，FS 同纪律）。
  - endpoint 经 `DataFabricSecurity.validate_url` SSRF 门（已支持 http/https/s3）。
- `get_object_store()`：env `WEBGIS_OBJECT_STORE_BACKEND=filesystem|s3` 选择；
  默认 filesystem（零行为变化）。测试经 fake boto3 client 注入（无网络）。

## 3. Vector lakehouse（Wave 3-4）

- 修复 `ref:fabric-parquet` 悬空：artifact_registry 增 disk-cursor 三件套
  （`is_fabric_parquet_ref`/`fabric_parquet_path`/`fabric_parquet_ref_exists`）+
  `probe_ref` 分支 + GC unlink 分支；materialization_service 写后 `register_artifact`
  （type=`fabric_geoparquet`，descriptor=bbox/count/crs + content digest）。
- GeoParquet 写入升级（vector_carrier.table_to_geoparquet）：row_group_size、
  statistics 启用、GeoParquet 1.1 `bbox` 列（加密列）、确定性写入。
- **window scan**：`scan_parquet_window(ref, bbox, columns, budget)` —— footer
  row-group bbox 统计剪枝 → 只读相交 row groups → Arrow → features；内存预算闸
  （复用 enforce_result_bounds 纪律）；绝不无谓全量读（测试用计数 row-group 证明）。

## 4. Raster lakehouse（Wave 5）

- `raster_store.save_png` 原子写修复（tmp+os.replace，全仓唯一非原子写）。
- COG 产物发布为 DataObject：manifest 携带 grid identity（chunk.py `_grid_identity_dict`
  同口径）+ chunk checksums（chunk_digest）+ overview ladder 事实；`convert_raster_to_cog`
  工具结果 additive 附 DataObject 身份（既有生产路径接线，无新工具/无 descriptor gate）。

## 5. Zarr V6 cube（Wave 6-7）

- `lakehouse/cube_store.py` 基于 geo_raster/zarr.py foundation（不重建）：
  - **group + consolidated metadata**（zarr 3 `zarr.consolidate_metadata`，zarr 2 fallback）；
  - compression（blosc/zstd，依赖可用性诚实探测）；
  - chunk 粒度读写（`read_window(t,y,x)` 只触所需 chunk）；
  - **partial update → 新 immutable revision**：CoW 发生在 manifest 层 —— 新 chunk 写入
    staging → chunk digest 计算 → 新 manifest（复用未变 chunk 的 digest）→ 新 DataObject id；
    旧 revision 原样保留（append-only 语义，零 DB 迁移）。
  - consolidated metadata **尺寸闸**（防 archive-bomb 式 oversized metadata）。
- `ref:cube/<id>` disk-cursor（目录形态 is_dir）；cube 由时间片 raster refs 构建
  （time/y/x/band 维度、nodata quality mask、chunk 选择走 iter_chunk_descriptors）。

## 6. Lazy materialization（验收红线）

任何 lakehouse 读路径 ref-only + 窗口/chunk 粒度；结构性证明：测试注入计数 wrapper
（row-group 计数 / zarr chunk 计数），断言**只触窗口内 chunk**。

## 7. Durability / lineage / GC / DR / security

- workspace durability `materialize_ref_payload` 扩展两 lane：`ref:fabric-parquet/*`
  （binary，同 raster PNG 模式）、`ref:cube/*`（manifest blob；restore 校验 chunk 在场性，
  缺 → pointer_missing 诚实四态）；alias mode 维持既有诚实披露 + 新增 durable pointer
  存在场时的 reopen 复原路径。
- lineage：manifest.source_refs + registry inputs 边 + provenance env_fingerprint +
  既有写时 redaction（lineage_service/provenance.manifest 复用）；无新 lineage store。
- GC：新 ref class 全部进台账 → 既有 sweep（statuses + protected tiers + probe）自动
  覆盖；unlink 分支镜像 raster :909-923。GC 保护验收：pinned revision / in-flight run /
  reachable（lineage 根）不删（既有测试语义 + 新 class 回归）。
- DR（lakehouse/dr.py）：`verify_object`（逐 chunk/字节 digest）、`backup_object`
  （cube chunk 文件 → BlobStore binary，内容寻址天然去重）、`restore_object`（manifest
  → 重建 store 目录）、`scan_missing/orphan`（BlobStore 枚举 vs 引用集）、corruption
  simulation 测试（篡改 chunk → verify 红 → restore 绿）。
- security：路由 owner check（verify_session_owner 既有纪律）；cube/parquet 路径全部
  过 charset 白名单 + validate_data_path；manifest/consolidated metadata 尺寸闸；
  日志/manifest 的 URL/token 经 redact_url/redact_provenance_args 既有工具。

## 8. 显式不做（与其他 Epic 边界）

- GeoCompute 调度 / Query Optimizer 逻辑计划 / 科学算法 —— 不触碰。
- 无 xarray 依赖（cube 基于 zarr+numpy 最小维度索引；xarray 适配列为 follow-up）。
- 无 DB migration（manifest 走 BlobStore；durable revision 走既有 artifact_revisions；
  会话台账既有 —— Alembic head 零冲突）。catalog DB GIN（ADR-0103 deferred）不做。
- ETag 远端同步（ADR-0101 deferred）→ follow-up。

## 9. 性能预算（结构性）

- window scan 读的 row-group 数 ≤ 相交数（非总组数）——结构性断言。
- cube 时间片读触发的 chunk 读数 ≤ 窗口覆盖 chunk 数。
- GeoParquet scan 内存峰值 ≤ 预算（bounded rows）；写路径 O(n) 单遍。
- manifest publish 额外开销 = 一次 sha256 + 一次 canonical dumps（O(payload)，off-loop）。
- bench（tests/benchmarks/test_lakehouse_v6.py，structural ratio 风格）：
  window/full 时间比带宽、chunk 命中计数、parquet 扫描 scaling 比、内存峰值护栏。
