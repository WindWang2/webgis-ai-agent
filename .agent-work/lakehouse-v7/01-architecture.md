# Lakehouse V7 — Architecture (Phase B)

状态：冻结于实现前；Subagent-A 只读挑战后修订（见 05-review-findings.md R0 节）。

## 0. 总原则

**不建第二事实源**（V6 红线延续）：字节真相仍只有 BlobStore；身份仍只有
manifest；revision 仍归 `artifact_revisions`；会话台账仍归
`artifact_registry`。V7 全部新能力是**组合层 + additive 契约扩展**。

## 1. n-D Labeled Cube（Scope A/B）

### 1.1 存储形态（zarr group，xarray 兼容约定）

```text
<store>/                      # zarr group（v3）
  zarr.json                   # group attrs: dims/bands/times/crs/transform/nodata/
                              #   cube_schema_version=2, labeled=true
  <var>/zarr.json             # 每个数据变量一个数组，attrs._ARRAY_DIMENSIONS=["time","band","y","x"] 等
  time/zarr.json …            # 坐标数组（time/band/polarization/vertical/y/x）
```

- **数据变量**：任意 n-D（time,band,y,x / time,polarization,y,x /
  time,vertical,y,x / 纯 y,x …）；维度顺序即数组 shape 顺序。
- **坐标数组**：一维，与维度同名；`time` 用 ISO-8601 字符串（UTC，与 V6
  attrs 同形）；`y`/`x` 存网格中心坐标（由 crs+transform 推导）；CRS 以
  group attrs 承载（对齐 V6 与 foundation，不引入 rioxarray 依赖）。
- **V6 向后兼容**：`open_labeled_cube` 对 V6 cube（`labeled` 缺席、
  per-band 数组 + attrs.times）合成 dims/coords（band 维度由组 attrs
  `bands` 投影）；V7 新写一律 schema v2。
- **身份**：manifest payload 携带 labeled 投影（dims/变量/坐标/shape/
  chunks/dtype/nodata/crs）—— 身份含标签语义（同字节不同标签 = 不同
  payload ⇒ 不同 DataObject id，诚实）。kind 仍是 `zarr_cube`，
  `cube_schema_version: 2`（新增值，读取侧按版本分派，v1 语义不变）。

### 1.2 坐标/CRS 校验（typed，绝不静默重采样）

`cube_schema.py`（新模块，纯函数）：
- 维度名白名单 `{time,band,polarization,vertical,y,x}` + 顺序契约（y/x
  必须存在且在最后两轴；time 可缺席=单时刻）；
- 坐标单调性（y 降序 / x 升序 / time 可任意但唯一）、长度 == shape；
- CRS 字符串经 `rasterio.crs.CRS.from_user_input` 校验（可选依赖缺席时
  降级为 `EPSG:` 前缀正则，诚实披露 `crs_checked: "regex"`）；
- 网格一致性：多源对齐 = y/x 坐标数组逐元素相等（容差 0）——
  不相等 = `CubeSchemaError`（对齐是调用方职责，rechunk 不改变坐标）。

### 1.3 xarray 适配（`xarray_adapter.py`，probe-gated）

- `dataset_to_cube_store(ds, out_store)`：xarray.Dataset → 上述 store
  （写坐标数组 + `_ARRAY_DIMENSIONS` + group attrs；chunk 化经
  chunk planner 的 target chunks）。
- `open_cube_to_xarray(store)` → `xr.open_zarr` 等价物（直接
  `xr.open_zarr`，consolidated metadata 受益）；V6 cube 先合成 v2 投影
  再交给 xarray（临时 group 或 Dataset 构造，二选一见实现——选
  Dataset 构造，零落盘）。
- xarray 缺席 → `XarrayUnavailable`（typed，同 `ZarrUnavailable` 族）。

## 2. Chunk Planner（Scope C，`chunk_planner.py` 纯函数）

- 输入：`array_shape/chunks/dtype/itemsize`、workload
  (`read|write|compute`)、请求窗口（各轴 slice）、`max_chunk_bytes`、
  `target_aspect`（空间倾向）。
- 输出（deterministic，同输入同计划）：
  `{chunk_indices: [(idx…)], chunk_bytes, touched_bytes, windows,
  estimated_io_calls, rechunk: Optional[plan]}`。
- read：交集块集合（selection 的执行单元）；write：受影响块 + 部分块
  read-modify-write 标注；compute：按时间片优先的空间块序列。
- rechunk 策略：`plan_rechunk(shape, chunks, max_chunk_bytes,
  target_aspect)` → 新 chunk 元组（坐标轴 2D 权重合成，时间轴=1 优先），
  纯元数据（数据搬运是调用方职责）。
- 全部有界：块数上限闸（`MAX_PLAN_CHUNKS = 50_000`，超出 typed 拒绝）。

## 3. 遥感 Cube（Scope B，`rs_cube.py`）

- `build_rs_cube(session_id, sources, spec)`：
  - `sources`: `[{time, source, band|polarization|quality, role}]`，role
    ∈ {`optical`,`sar`,`mask`,`quality`}；
  - 对齐：全部源过 labeled schema 的 y/x 坐标恒等校验（§1.2）——网格
    不一致 typed 拒绝（不重采样）；
  - 变量组装：`reflectance`(time,band,y,x) / `sigma0`(time,polarization,
    y,x) / `quality_mask`(time,y,x) / `cloud_mask`(time,y,x)；
  - quality/cloud mask 跟随源指纹入 lineage；
  - 时序选择（`temporal_subset`）、空间窗口（`spatial_window`，经
    chunk planner 只触相交块）、band/polarization 选择 = **view 对象**
    （virtual DataObject，§5），绝不复制字节。
- 复用 `cube_service` 的源解析/指纹通道；新增 producer capability
  `lakehouse.rs_cube_build`。

## 4. S3 生产化（Scope D，扩展 `s3_blob_store.py`）

- **流式 put**：`put_blob_stream(key, chunks: Iterator[bytes],
  content_type, *, part_size=8MiB, max_parts=10_000)`：
  - 小于 1 part → 退回单 put（同既有原子发布路径）；
  - multipart：`create_multipart_upload` → 逐 part
    `upload_part`（流式累计 sha256 与字节数，内存 O(part)）→
    `complete_multipart_upload`；ETag 捕获入 sidecar meta；
  - 任一 part 重试 N 次（指数退避+jitter 上限）；终败 →
    `abort_multipart_upload`（无残留）→ typed 错误；
  - part 序号有界；总字节超预算 typed 拒绝（写前已知大小时）。
- **流式 get**：`get_blob_stream(key, expected_sha256=None,
  chunk_size=1MiB)` → Iterator[bytes]（digest 校验流式累计，尾部判定；
  FS 实现同语义读文件）。BlobStore 基类加默认实现（分块读 bytes 路径
  复用），FS/S3 覆写。
- **ETag/条件写**：sidecar meta 增 `etag`；`head` 暴露 ETag；
  `verify_remote_etag(key)`（DR 用）；put-if-absent 语义保持 head-based
  （MinIO/S3 兼容面一致），ETag 不作身份（digest 仍是身份）。
- **重试**：client 层薄包装 `_with_retries(fn, attempts=3,
  backoff 0.2s*2^n + jitter)`，仅对幂等读 + staging 写；final
  copy/complete 失败不盲目重试（part 状态未知 → abort 后整体重试）。
- **owner namespace**：不变 —— S3 键内容寻址，隔离在 manifest 层
  （V6 决策，评审已定）；prefix 即部署命名空间。
- **SSRF**：不变 —— endpoint 过 `DataFabricSecurity.validate_url`。

## 5. Virtual DataObject（Scope E，`virtual_object.py`）

- manifest `kind="virtual"`（kind 白名单 additive 扩展；
  `schema_version` 仍 1 —— manifest 结构无破坏性变化）。
- manifest 结构：`payload.virtual = {children: [data_object_id…],
  selection: {…bounded descriptor…}, materialization: "lazy"|"inline",
  inline_max_bytes}`；`content_blobs` = 空（**零字节复制**）；
  `source_refs = ["data-object:<child_id>"…]`（lineage 边）。
- 语义：
  - `resolve_virtual_object` → 递归解析 children（深度闸
    `MAX_VIRTUAL_DEPTH=8`、宽度闸复用 MAX_MANIFEST_BLOBS），检测
    missing/corrupt/foreign-owner child（typed 四态报告）；
  - `materialize`：lazy = 仅物化 children 的字节引用清单（不复制）；
    inline ≤ inline_max_bytes 时真实物化并升级为普通对象（诚实披露）；
  - view/query-derived 对象（时序选择/空间窗口/查询结果）都是 virtual；
  - **invalid child**（子对象被 GC/损坏）→ 状态可见 + 修复策略 =
    重新物化来源或报错，绝不静默跳过。
- 跨 owner：children 必须同 owner scope（发布通道是唯一跨域机制）。

## 6. Catalog（Scope F，DB-backed + additive migration）

- 新表 `lakehouse_catalog_items`（新模型文件 `app/models/lakehouse_catalog.py`）：
  - `id`(data_object_id 或 ref id, PK), `owner_type`(session|project),
    `owner_id`, `kind`, `title`, `producer_capability`, `producer_tool`,
    `workflow_run_id`, `tags_json`, `descriptor_json`(有界投影),
    `minx/miny/maxx/maxy`(NULLable float), `time_start/time_end`(NULLable
    DateTime), `content_sha256`, `byte_size`, `status`
    (`active|revoked`), `created_at`, `updated_at`；
  - 索引：`(owner_type, owner_id, created_at)`、`(kind)`、`(time_start)`,
    `(time_end)`、bbox 用 `(minx, maxx)`/`(miny, maxy)` 复合（范围谓词
    走索引）；tags GIN **仅 PG**（`postgresql_using="gin"` 条件索引，
    SQLite 跳过 —— 测试按方言断言，不强依赖 GIN）；
- 服务 `catalog_service.py`：`upsert_entry`（发布/注册时同步投影），
  `search(owner, filters{kind,bbox,time,tags,producer,q}, limit≤200,
  offset)` → `{items, total_bounded, next_offset}`（强制分页，total 用
  有界计数 —— 超 `MAX_TOTAL_SCAN=10_000` 报 `>=` 下界，诚实）；
- 一致性：catalog 行是 **projection**（事实源 = manifest + 台账）；
  upsert 幂等（同 id 重投影），GC 删除时同步删行/标 revoked。
- Migration `0034_lakehouse_catalog`（down_revision=
  `0033_geocompute_v6_cluster`，单 head 保持）。

## 7. STAC 投影（`catalog_stac.py`，纯函数）

- `entry_to_stac_item(entry)` / `entries_to_stac_collection(entries)`
  （STAC 1.0.0 固定版本串）；
- Item：bbox → geometry(Polygon)；`properties.datetime` =
  time_start==time_end；`start/end_datetime` 否则；assets =
  `{"data": {href: content_location, "webgis:data_object_id", roles}}`；
- 分页 JSON 响应（links next/prev 有界）；投影只读，不落库。

## 8. Project Publishing（Scope H，`project_publish.py`）

- `publish_to_project(session_id, project_id, refs|data_object_ids, actor, db)`：
  1. owner 链校验：session→user（verify_session_owner）与
     project.owner_id==actor（跨 owner = 404，不泄漏存在性）；
  2. 对象解析 + owner 校验（virtual children 同 owner）；
  3. **不可变发布修订**：blobs 已在 CAS（零复制）；写 catalog 行
     (owner_type=project, status=active) + `artifact_revisions` 行
     （经 `record_revision`，content_location=manifest location）；
     幂等：同 (project, content_sha256) 重发布 = 已存在命中；
  4. 撤销 `revoke(project_id, ids)`：catalog status=revoked（tombstone；
     已发布对象对既有引用仍可解析，但目录检索默认不可见）；
  5. 无跨项目未授权内容：发布读取经 owner 校验，写只进目标 project。
- REST：`POST /lakehouse/publish`、`POST /lakehouse/revoke`、
  `GET /lakehouse/catalog`（project/session 域检索）+ STAC 面。

## 9. Lifecycle / GC（Scope G，`lakehouse_gc.py`）

- **可达性**：roots → manifests → {content blobs, children(virtual)}；
  只读元数据，绝无字节物化。
- **protected roots**（可枚举、有界）：
  1. catalog `status=active` 的行（含 project published）；
  2. `artifact_registry`/`artifact_revisions` 指向的
     `data_object_id`/manifest location；
  3. workspace 快照引用（snapshot 内容位置投影）；
  4. 宽限期（`GRACE_HOURS`，mtime 新于阈值的 manifest 不回收）。
- **plan（dry-run）**：`gc_plan()` →
  `{candidates:[{id, reason_evidence, byte_size}], scanned, protected_n,
  deletable_bytes}`（确定性排序：id 升序）；execute(plan) 仅接受本函数
  签发的 plan（plan token = 候选集 digest —— 状态漂移即拒绝，CAS 纪律）。
- **multipart/staging 孤儿**：S3 `list_multipart_uploads`（超龄 abort）+
  staging prefix 枚举（超龄清除）；FS staging 目录同理。
- **interrupted → resume**：plan token 重验（候选子集已消失=跳过并记录；
  新增受保护对象=从计划剔除）—— 幂等可续。
- 证据：删除报告 `{deleted_ids, deleted_blobs, reclaimed_bytes,
  skipped_protected}`，全部排序稳定。

## 10. DR（Scope I，扩展 `dr.py`）

- `scrub_object(id, *, mode="sample"|"full", sample_k=8, etag_check=False)`：
  - manifest checksum（恒查）→ 逐 chunk digest；sample 模式按
    id 派生确定性子样（`sha256(id+i)` 选 K 个，可复现）；
  - `etag_check`：store 有 ETag 事实（meta）时 head 比对 →
    `etag_mismatch[]`；
  - 报告 `{state, chunks_checked, missing, corrupt, etag_mismatch,
    checked_bytes_estimate}`；
- 修复策略（safe only）：工作副本可用 → `backup_cube_chunks` 重灌；
  BlobStore 是真相 → 报告 + 人工/上游处置；绝不伪造修复。
- `restore`：既有 `materialize_data_object` 通道（owner 校验保留）。

## 11. 资源包络（全部新路径）

| 路径 | 上界 |
|---|---|
| plan chunks | 50,000 块 typed 拒绝 |
| multipart part | 10,000 parts；part 8MiB；总字节预算参数 |
| catalog search | limit≤200/页；total 扫描 ≤10,000 行（超出报下界） |
| virtual 解析 | 深度 ≤8；宽度 ≤MAX_MANIFEST_BLOBS |
| GC plan | 候选枚举元数据级；单批 ≤10,000 条 |
| scrub sample | K≤64 chunk 全量读；full 模式显式 |
| REST 响应 | 一律分页/预算（复用 V6 cell/row 闸） |

## 12. 兼容与回滚

- 全部 additive：新模块、新 kind 值、新 schema_version 值、新表、新端点；
  既有端点/manifest v1/FS 行为零变化；
- migration 可 down（drop 新表）；env 无新必填配置（S3 行为增强仅在
  backend=s3 时生效）；
- 测试 oracle：V6 全套保持绿（453 passed 基线）；V7 每个 wave 正/负/
  边界测试；completion-proof e2e（optical+SAR 全链路）为验收锚。

## 13. 性能预算（结构性证据）

- 窗口读触达块数 == 相交块数（counting store 断言，∝ 窗口而非 cube）；
- multipart 峰值内存 ≤ O(part_size×2)（tracemalloc）；
- catalog 查询走索引（分页行数有界；无全表扫描的结构证明 =
  LIMIT 查询返回行数 ≤ limit）;
- GC 触达字节数 == 元数据字节（不读 chunk 内容 —— 计数器证明）；
- 10k+ chunks 的 cube manifest/publish/scrub(sample) 在有界时间/内存
  （合成小 chunk，块数大 —— 结构性）。
