# Spatial Data Lakehouse & Cube V6 (ADR-0118)

V6 在 Data Control Plane V5 之上建立 durable spatial lakehouse：统一数据
身份、chunk、revision、lazy materialization、object storage 与项目级
durability。**组合层设计**：字节真相只有 BlobStore 一份，台账归
artifact_registry，revision 归 artifact_revisions —— 无第二事实源。

## 核心契约

### DataObject 身份
- id = manifest 的 canonical sha256（内容寻址，无 opt-in）。
- manifest 确定性：同 (kind, owner, 内容, 参数) ⇒ 同 id ⇒ CAS 免费去重。
- `owner_scope`（session/project 恰一）参与身份；访问/物化/复用判定全部
  owner-checked，越权拒绝且不泄漏存在性。
- `input_fingerprint`：`compute_reuse_fingerprint` 口径的复用键
  （inputs+params+env），derived artifact dependency hash。
- `environment_fingerprint`：python/geo 栈版本的有界投影；跨运行时的同
  内容产物身份分离（可复现性语义）。
- 尺寸闸：manifest ≤64KiB canonical、blob 数 ≤65536、对象总字节有预算
  （写前拒绝）。

### Ref 词表（会话 disk-cursor）
| ref | 形态 | 路径 | 台账 |
|---|---|---|---|
| `ref:raster/<id>` | PNG 文件 | `<DATA_DIR>/<sid>/raster/` | V4（既有） |
| `ref:fabric-parquet/<id>` | GeoParquet 文件 | `<DATA_DIR>/<sid>/fabric-geoparquet/` | **V6**（probe/GC/restore 全通道） |
| `ref:cube/<id>` | zarr store 目录 | `<DATA_DIR>/<sid>/lakehouse-cubes/` | **V6** |

### 对象存储
`WEBGIS_OBJECT_STORE_BACKEND=filesystem|s3`（默认 filesystem 零行为变化）。
s3 需 `WEBGIS_S3_ENDPOINT_URL/BUCKET/REGION/ACCESS_KEY_ID/SECRET_ACCESS_KEY/PREFIX`；
endpoint 过 `DataFabricSecurity.validate_url` SSRF 门（先于 boto3 导入）；
boto3 为 optional 依赖，缺失 typed `S3StoreUnavailable`。

## 生产路径

1. **GeoParquet**：`POST /data-fabric/materialize`（`output_format=geoparquet`）
   → 台账注册 + 内容身份 + durable 发布（256MiB 预算内）；
   `POST /lakehouse/vector/scan` 窗口扫描（row-group 剪枝，证据在响应）。
2. **COG**：`convert_raster_to_cog` 工具结果附 DataObject 身份。
3. **Cube**：`POST /lakehouse/cubes`（时间片栅格，网格一致强制）→
   `ref:cube/<id>` + durable 身份；`POST /lakehouse/cubes/window` chunk
   粒度窗口读；修订 = `fork_cube_revision`（硬链接 CoW）。
4. **快照**：workspace snapshot（`materialize="claimed"`）物化
   fabric-parquet/cube 指针；restore 从 BlobStore 验真复原（会话死亡后
   reopen）。
5. **DR**：`verify_cube_store`/`repair_cube_store`/`backup_cube_chunks`/
   `scan_orphan_manifests`（只读扫描；破坏性清扫归既有 GC 闸）。

## Lazy materialization 纪律

vector 读 = row-group 剪枝（`webgis:row_groups` 元数据，真实几何计算；
缺席退化为有界顺序读）；raster 读 = `RasterReader.read_window`（既有唯一
窗口权威）；cube 读 = zarr 原生 chunk 粒度。结构性证明：剪枝计数、chunk
触达计数（counting store）、tracemalloc 峰值护栏。

## 已知限制（诚实清单）

- cube/parquet 的 durable 发布有预算（256MiB/128MiB），超预算为
  `oversized`/`manifest_only` 诚实降级（不冒充持久）；S3 multipart 流式
  上传未实现。
- 快照 cube 指针 verify 只回答"manifest 可读"（深层 chunk 校验归 DR 显式
  调用）。
- 跨会话 cube 复原要求 blob 已进 BlobStore（manifest_only 指针无法物化，
  restore 如实 False → degraded 披露）。
- zarr consolidated metadata 尚未进 zarr format-3 规范（写入但容忍缺席）。
- 内存后端 session store 的 alias-mode restore 行为不变（V5 已披露）。
- 时间片网格不一致时 cube 构建拒绝（无静默重采样）—— 对齐是上游职责。

## 环境变量

`WEBGIS_REF_CONTENT_HASH`（V6 默认 ON；0/false/no/off 关回历史）、
`WEBGIS_OBJECT_STORE_BACKEND`、`WEBGIS_S3_*`（见 `.env.example`）。
