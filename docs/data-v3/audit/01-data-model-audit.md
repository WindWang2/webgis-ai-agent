# 01 — Data Model Audit（Agent A）

## 1. 既有「数据对象」抽象清单

### A. 会话 ref 体系（事实上的 artifact 载荷持有者）
| 抽象 | 位置 | 职责 | 后端 |
|---|---|---|---|
| `ref:` cursor + payload | `app/services/session_data.py` (`MemorySessionStore.store`, :77, `ref:{prefix}-{uuid4hex16}` :86)；Redis 孪生 `session_data_redis.py` | 大载荷（GeoJSON/heatmap/chart）游标化；LRU 200/会话 + 字节上限 | 内存 LRU 或 Redis（DATA_TTL≈4h） |
| `RefDescriptor` | `app/schemas/ref_descriptor.py:13` | store 时 O(1) 元数据：feature_count/geometry_types/bbox/mvt、raster-capable、`content_hash`（保留位，恒 None :28-33）、`content_revision` 计数（:56）、field_schema | 随 ref 存储 |
| ref 生命周期契约 | `app/services/ref_lifecycle.py:25-92`；`ref_payload_cache.py` | 唯一失效权威（OVERWRITE/DELETE/EVICT/EXPIRE/ROLLBACK/REPLACE）+ 跨 pod 广播 | 派生缓存 |
| 会话栅格 PNG | `app/services/raster_store.py:38-49` `ref:raster/<id>` → `<DATA_DIR>/.webgis-agent/<sid>/raster/<id>.png`；路径解析 `artifact_registry.py:290-315` | 栅格产物不透明游标 | 磁盘（会话目录，随会话 GC） |
| MapSpec 修订/检查点 | `app/services/mapspec/store.py`（`BASE_STORAGE_DIR` :38、原子写 :85、保留 20 修订） | 期望地图状态 + 全量 spec 快照 | 磁盘 JSON + Redis map_state |

### B. 「Artifact」——四套并存体系
| 抽象 | 位置 | 身份/后端 |
|---|---|---|
| `Artifact` ORM 行 | `app/models/project.py:200`（表 `artifacts`） | artifact_type/format/crs/`storage_ref`（指向会话 ref！）/upload_record_id/layer_id/`content_fingerprint`/metadata_json；PK uuid4 |
| `ArtifactRecord` + `ArtifactGraph` | `app/services/artifact_registry.py:79,120`（ADR-0082） | 每会话记录层：producer capability/tool/node、inputs（血缘边）、`replaces`/revision、状态机 `valid→superseded→stale/expired/failed`（:43-48）、有界 128 条；artifact_id = ref 字符串本身 |
| `ArtifactDescriptor` + `ArtifactTypeRegistry` | `app/lib/gis/artifacts.py:232,158`（17 个种子语义类型 :35-155） | 机器可读语义类型（metadata+`data_ref`，lineage ≤16）；AlgorithmRegistry/MapModelRegistry 消费 |
| 晋升内容库 | `app/services/project_artifact_promotion.py`（content_store_root :62，内容寻址 `<fp[:4]>/<fp>.json` :84-90，`materialize_blob` :157，状态词表 :51-57） | 会话 TTL 后持久化：按内容指纹落盘 `DATA_DIR/project_artifacts/`；DB 行记 `content_status`/`content_payload_sha256` |

（另）`artifact_cache`（`app/lib/artifact_cache.py`，ADR-0048）：确定性栅格算子计算缓存，key=sha256(path+mtime+size, op, params, `ARTIFACT_VERSION_NS`)，磁盘 LRU 5GiB——**是缓存不是身份体系**。

### C. 「Dataset」——三层目录 + 两个 profile 形状
| 抽象 | 位置 | 后端 |
|---|---|---|
| `ProjectDataset` ORM | `app/models/project.py:42` | `source_type`（upload/layer/external/vector/raster）、`source_ref`（**三重过载**：UploadRecord id 或 Layer id 或 URL，`app/schemas/project_schema.py:36-41`）、schema_profile、`version_fingerprint`、`detached_at` 墓碑 |
| `DatasetDescriptor` | `app/schemas/data_fabric_schema.py:72`（ADR-0094） | fabric 数据集元数据契约 |
| `DataFabricDataset` ORM | `app/models/data_fabric.py:40`（表 `spatial_catalog_items`） | 每 DataSource 的持久目录索引；`fingerprint`/`availability` |
| `DataSource` ORM | `app/models/data_fabric.py:12` | 远端连接注册（endpoint_url/connection_profile/capabilities） |
| `SpatialCatalogService` | `app/services/data_fabric/spatial_catalog.py:28` | 进程内 Agent 目录（内存 dict，owner-scoped） |
| `DatasetProfile` | `app/lib/gis/dataset_profile.py:43` | **既有和解契约**：统一 RefDescriptor/Spatial Meta Profile/ArtifactRecord 为有界 profile |
| `RasterSource` 族 | `app/lib/geo_raster/source.py:40` | 栅格身份描述：Local/SessionRef/ProjectArtifact（`artifact://<project>/<id>` :64）/Remote(/vsi, http)/COG；惰性身份 |

## 2. 重复/边界混淆
1. **四套 artifact 体系、两种身份**（DB uuid vs 会话 ref 字符串 vs data_ref 字符串 vs 内容指纹）。ADR-0082 定调 registry 是 ref 的「记录投影」，V3 必须统一身份而非加第五套。
2. **三套 dataset 体系**：ProjectDataset（项目域）vs DatasetDescriptor/spatial_catalog_items（fabric 域）vs 内存 SpatialCatalogService；同名不同义。
3. **两套指纹口径**：`data_fabric/fingerprint.py:17`（含 id，重注册不稳定）vs `provenance/fingerprint.py:63`（身份证据，不含名称）。
4. **`source_ref` 三重过载**，无类型化引用。
5. **Layer/file**：`Layer.source_url` 原始串 vs MapSpec source（inline ≤5000 / `ref:` / `imageRef` / fabric）vs runtime map_state —— 三「层」真相靠 ref 串维系。
6. `RefDescriptor.content_hash` 恒 None（保留位）。

## 3. ID 稳定性
- `ref:` id：每次 store 随机 uuid4，重试/重算即新 id；TTL 绑定；靠 `replaces` 链追替代。
- DB `Artifact.id`/`ProjectDataset.id`：每行 uuid4，重导入不复用；`Layer.id`/`UploadRecord.id` 自增。
- 稳定者：`version_fingerprint`、`content_fingerprint`（描述符派生）、`graph_fingerprint`、`run_fingerprint`、晋升 `content_payload_sha256`（真字节摘要）。
- 重导入无任何复用：仅指纹可检测重复。

## 4. 载荷 vs 引用
大结果在 dispatch 接缝自动 ref 化（`tool_dispatch_service.py:580-700`），结果带 ref+有界描述符；inline 闸门 5000 要素（`mapspec_source.py:20-22`）；`app/lib/runtime/result_contract.py:146` 是只读结果适配器。注意 `Message.tool_result`（`app/models/db_model.py:236`）仍可能把结果 envelope 落库。

## 5. 「层/项目引用什么数据」的真相源
- 会话：`ref:` 串为规范指针；真相分治（SessionPlan `bound_ref` 计划真相 / MapSpec sources 地图真相 / map_state 运行时观测），ArtifactRegistry 为记录投影，`load_records_for_plan`（`artifact_registry.py:564`）算活跃引用集。
- 项目：`artifacts.storage_ref`（会话 TTL 后悬挂，除非晋升）+ `project_datasets.source_ref` + `layers.source_url`；**无统一持久内容目录**。

## 6. V3 扩展点（演进不重建）
1. 契约类型 → `app/lib/gis/dataset_profile.py` + `app/lib/gis/artifacts.py`
2. 指纹 → `app/services/provenance/fingerprint.py`（补真内容哈希；与 data_fabric 口径和解）
3. 注册/生命周期/血缘 → `app/services/artifact_registry.py` + `lineage_service.py` + `app/models/project.py`
4. 持久内容 → 推广 `project_artifact_promotion.py` 内容寻址库
5. 目录 → `data_fabric/spatial_catalog.py` 与 ProjectDataset 经类型化 ref 统一
6. **遵循 ADR-0082「记录不驱动；无第二真相」不变量；下一 ADR 号 0103**
