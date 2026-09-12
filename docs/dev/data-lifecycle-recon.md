# 数据基础与生命周期治理 V9 — P0 勘察报告

> 线：`foundation/data-lifecycle-v9` · 基线：origin/master @ `8b5b8375` · 日期：2026-09-11
> 方法：只读勘察（S1 Explore subagent 全景扫描 + 主 agent 复核）。本文为 P0 交付物，行号以基线为准。

## 0. 摘要

五套过期/回收机制（lakehouse retention、fabric 物化/session spill、artifact 磁盘 LRU、COG 会话目录、geocompute worker_cache）**各自为政确认**：TTL 语义、淘汰触发、观测面互不相通，且 **COG 输出无任何清理机制（真实缺口）**。data-gc 已有同步 dry-run/execute 端点但无审批/回滚闭环；quality-audit/repair 均为同步进程内路径，无 durable job 对接。Alembic 两轮撞号（0034×4、0035×3）靠 merge revision 收敛，流程性根因（无领号机制）确认未解决；`tests/test_alembic_metadata.py` 目前仅断言 `target_metadata` 非空，无编号唯一性、无单头断言。`app/db/` 确认为零引用死包；`app/skills/` 实为文件系统数据目录（非 Python 包）；`app/tasks/explorer/task_chain.py` 有活跃消费方（explorer orchestrator + celery include），保留并文档化。

## 1. 五套过期/回收机制全景

### 1.1 Lakehouse / Durable Artifact Retention

- **键空间**：`Artifact`/`ArtifactRevision`（`Artifact.project_id` 作用域）+ 内容寻址 BlobStore 物理 blob + 孤儿修订行。
- **TTL 语义**：`WEBGIS_RETENTION_MAX_AGE_DAYS`（0=永久保留，默认）、`WEBGIS_RETENTION_GRACE_HOURS`（默认 168h）→ `RetentionPolicy.from_env`（`app/services/data_lifecycle/quota.py:349-373`）。Lakehouse 元数据级：`app/services/lakehouse/dataset_retention.py`（`DEFAULT_MAX_VERSIONS=64`、`DEFAULT_MIN_AGE_HOURS=72`、branch/tag 指针保护、plan/execute token 防陈旧）。
- **触发**：仅显式 API（无后台 sweep）：`POST /projects/{id}/data-gc/plan`（`app/api/routes/project.py:1376`，dry-run）、`POST .../data-gc/execute`（:1462，`confirm=true` 强制）。全局 promotion GC 属周期 sweep `artifact_lifecycle.sweep_aged_artifacts`（项目路由明确禁止触发，:1386-1388 注释）。
- **保护面（单一保护路径不变式）**：`_retention_revision_protection`（quota.py:376-403，plan/execute 双侧同函数）；blob 级 `promotion_blob_protection`（引用计数+head 指针+pin+宽限期+快照 manifest 指针）；快照指针扫描上限 `SNAPSHOT_POINTER_SCAN_CAP=5000`，截断 → fail-closed 跳过 blob 删除（quota.py:585-595、713-721）。
- **观测**：plan dict（`protected_counts/candidate_*`）、execute 结果（`deleted_*/bytes_freed/skipped_protected/failed`）；无 prometheus 指标。
- **字节删除唯一入口**：`lakehouse_gc.execute_gc`（`app/services/lakehouse/lakehouse_gc.py:322`，`DEFAULT_GRACE_HOURS=72`）。REST：`/lakehouse/datasets/{id}/retention/plan|execute`（`app/api/routes/lakehouse_datasets.py`）。

### 1.2 Data Fabric 物化 + 工具/ref 缓存

- `materialization_service.py`：本身无 TTL。GeoParquet lane 落盘 `<DATA_DIR>/<session>/fabric-geoparquet/`，预算 256MiB；TTL 落在 session_data：ref spill `GIS_REF_SPILL_TTL_S`（默认 86400s，惰性过期，`app/services/session_data.py:165-175`）、`cleanup_idle_sessions` LRU（max_sessions=100）。
- `app/lib/tool_cache.py`：Redis `tool_cache:v2:<sha256[:16]>`，per-tool TTL 1h–24h（默认 1h），TTL-only 自然过期、无主动失效通道；`ref:` 参数绝不缓存；singleflight SET NX。
- `app/services/ref_payload_cache.py`：进程内 LRU，`PAYLOAD_TTL_SECONDS=5.0`、MAX_ENTRIES=256、MAX_BYTES=128MiB；失效主 = content revision epoch。

### 1.3 Artifact 磁盘 LRU（`app/lib/artifact_cache.py`，702 行）

- 键：16-hex sha256（source path|mtime_ns|size + op + `ARTIFACT_VERSION_NS`），条目 `data/artifacts/<key>.tif` + `.meta`；chunk 变体独立目录/预算。
- 预算：`WEBGIS_ARTIFACT_CACHE_BYTES` 默认 5GiB；chunk 默认 1GiB。逐出：LRU by `.meta` mtime，advisory 字节计数器仅在超预算时全目录扫描。
- 清扫：`sweep_orphan_disk_artifacts`（:622，temp 遗留/配对孤儿/超龄 `ARTIFACT_DISK_RETENTION_DAYS=30`），调用方 = 周期 `sweep_aged_artifacts`。观测仅 logger。

### 1.4 COG 会话目录（实际位置与任务书不同）

- **`app/services/raster/cog_service.py` 不存在**。实际：`app/lib/geo_raster/cog.py`（`write_cog`/`to_cog`/`ensure_cog`，原子发布）+ 工具入口 `app/tools/raster_tools_cog.py`（默认 `out_dir="data/cog"`，`effective_out_dir = out_dir/session_id`，GIS-08 隔离）。
- **清理触发/TTL：不存在**。唯一间接视野 = 有 session 上下文时的 `register_artifact` 证据登记；无 session 时诚实跳过 → **清理缺口确认为真**。P3 适配器按 `data/cog/<session_id>/` 目录扫描实现，行为保持 = 默认不删、仅登记观测。

### 1.5 GeoCompute worker_cache

- 进程内本体 `PayloadCache`（`app/services/geocompute/cluster/object_cache.py:37`）：键 `(session_id, ref_id, owner_scope)`，MAX_ENTRIES=32 / MAX_BYTES=256MiB，插入侧 LRU。
- DB 表 `geocompute_worker_cache`（`app/models/db_model.py:515-535`）= **位置声明注册表**（PK `(worker_id, cache_key)`）；coordinator tick `purge_older_than`（`locality.py:253-277`，下限 60s，批删 limit=64）；worker prune 级联 `drop_worker`。观测：`worker_cached_bytes`（geocompute.py:685 暴露）、`worker_cache_hit` 事件。

## 2. data-gc / quality 端点契约现状（project.py，prefix /api/v1/projects）

| 路由 | 行 | 鉴权 | 执行模型 |
|---|---|---|---|
| `POST /{project_id}/quality-audit` | :1032 | optional | 同步，`SpatialQualityEngine.audit_dataset`，无 job、无落库报告实体 |
| `POST /{project_id}/repair` | :1053 | 强制 + owner_token | 同步 `execute_repair`（新 ref 语义 + lineage 接线），session 所有权守卫 |
| `GET /{project_id}/data-usage` | :1322 | optional | 同步只读（usage/limits/quota/retention 披露） |
| `POST /{project_id}/data-gc/plan` | :1376 | optional | 同步 dry-run；脱敏投影（SEC MAJOR-F3）；`_bounded_items` ≤64 |
| `POST /{project_id}/data-gc/execute` | :1462 | 强制 | 同步；`confirm=true` 必须；现场重跑 plan→execute（绝不盲执行缓存计划）+ 孤儿修订清理 |

**结论：全部同步 def（threadpool），零 durable job 对接；审批/回滚状态机不存在。**

**Durable job 运行时（P1/P5 对接口）**：`submit_durable_job(*, celery_task, task_type, display_name, params, task_args=(), task_kwargs=None, kind, session_id=None, idempotent=True, max_retries=3, queue=None)`（`app/services/jobs/submit.py:43-56`）；状态表 `analysis_tasks`（`app/models/db_model.py:91-180`，status CHECK pending/queued/running/cancelling/completed/failed/cancelled/stale；`result_ref`/`dispatch_spec`/`heartbeat_at`/`idempotency_key` unique）；store `app/services/jobs/store.py`，worker `app/services/jobs/worker.py`。先例：geocompute `durable.py:190-199`。

**指标约定**：prometheus_client Counter 注册默认 REGISTRY（先例 `app/core/auth_metrics.py:20-25`，无 label 防 cardinality；告警一致性测试钉死）；另有内存聚合器 `/api/v1/metrics/digest` 与 instrumentator `/metrics`（main.py:489-509）。

## 3. templates 服务与 Cartography 组件注册表耦合面

- `app/services/templates/intent_resolver.py`（118 行）：模板注册表（`app/schemas/template_registry.py`，O(1)）上的薄封装；调用方 `app/tools/templates.py:192-338`（apply_template 按 kind 分发 → mapspec_store）、`app/services/gis_harness/template_catalog.py:134`。
- 组件注册表 `app/lib/cartography/component_registry.py`：`MapComponentDescriptor`（`schema_version: int = 1`，:67）+ 注册表级单调 `registry_version()`（:661）。`component_graph.py`（510 行）= 运行时投影（`Template = Component Graph + Constraint Set + Style Tokens`），单一事实仍是 MapSpec `layout.components` 扁平列表；纯函数无第二存储。
- DB：`cartography_templates`（迁移 `c1d2e3f4a5b6`）：id String(255) PK、org_id FK nullable、kind CHECK、payload JSON、`version Integer default 1`、is_builtin；ORM `app/models/db_model.py:~249`。REST：GET/POST/DELETE `/templates`（`app/api/routes/templates.py:149/275/310/351`）。
- **P4 对齐点**：模板 payload 引用组件 id 时可校验组件存在性与 `schema_version` 兼容；版本化表 `template_version` 挂 `template_id → cartography_templates.id`。

## 4. 测试清单与缺口

- `data_lifecycle` 引用 8 个测试文件（tests/data/test_quota_retention_v5.py、test_gc.py、test_lifecycle_service.py、test_wave1_promotion_gc.py、test_review_fixes.py、test_reliability_corpus.py、test_catalog.py、tests/unit/tools/test_behavioral_data_discovery.py）——与 lakehouse（18+ 文件）相比明显偏薄，验收目标 ≥25。
- `data_quality`：test_repair_plan_v4.py 等；`data_profile`：test_profiler_service.py 等；`artifact_cache`：test_artifact_cache.py + chaos 系列；`worker_cache`：geocompute v7/v8 测试；**COG 清理：零测试**。
- 缺口：data-gc HTTP 路由层无专门 API 测试；质量报告无实体落库测试；生命周期统一注册表/策略/GC 闭环全缺（本线主体）。
- **约定**：pytest.ini（asyncio_mode=auto、timeout 60s、markers heavy/perf/cartography/real_services）；conftest env 基线（sqlite 文件库、memory broker、USE_REDIS=false）；API 路由测试样板 `tests/unit/test_project_api.py`（模块级 `TestClient(app)` + autouse `setup_db` create_all + User 种子 + `create_access_token` 鉴权头）。

## 5. Alembic 撞号历史复盘

**第一轮 0034（四路并行，全部 down_revision=`0033_geocompute_v6_cluster`）**：

| 文件 | revision |
|---|---|
| 0034_data_fabric_v7_facts_feedback.py | `0034_data_fabric_v7_facts_feedback` |
| 0034_geocompute_v7_dataflow.py | `0034_geocompute_v7_dataflow` |
| 0034_lakehouse_catalog.py | `0034_lakehouse_catalog` |
| 0034_workflow_v5_runtime.py | `0034_workflow_v5_runtime` |

→ merge `c0d8322aa2cb`（四头合一）。

**第二轮 0035（三路并行，全部 down_revision=`c0d8322aa2cb`）**：`0035_geocompute_v8_fabric` / `0035_lakehouse_dataset_versions` / `0035_workflow_v6_durable` → merge `c1e2f3a4b5c6`（当前 master 单头）。注：`0034_workflow_v5_runtime.py:27` 注释引用的合并 id 已过期（实际为 c1e2f3a4b5c6）。

**根因**：多线并发无领号机制；revision id 权威来自文件内 `revision =` 字段，文件名 NNNN 前缀纯装饰，`test_alembic_metadata.py` 仅查 `target_metadata` 非空——撞号在 CI 不可见，直到 `alembic heads` 多头才暴露。**P6 对策**：`scripts/alloc_migration.py` + `migrations/.alloc.json` 预登记 + 编号唯一性断言 + 单头强校验 + 协议文档。

**本线段位**：0046–0055（B 线 0036–0045 禁用）；ADR-0140（watermark 现为 0137；0138 属 A 线）。迁移惯例：新 model 显式 import 进 `migrations/env.py`（漏 import → autogenerate drop_table）、create_all-coexistence guard 可重入 DDL、downgrade 反序。

## 6. 结构债确认

- `app/db/`：`__init__.py` 仅 27 字节 docstring，**全仓零 import** → 死包，P7 删除（ADR 附录记录）。
- `app/tasks/explorer/task_chain.py`（229 行）：explorer Celery 链，消费方 `app/services/explorer/orchestrator.py:294-315` + celery include `app/services/task_queue.py:18` → **保留并文档化**（非死代码）。
- `app/skills/`：仅 6 个 .md（无 `__init__.py`），消费方 config.py:213/246、skill_creator.py、tools/skills.py → **文件系统数据目录而非空包**，P7 只补 README 说明，不动文件。
- `app/services/rs/spectral_engine.py:88-97`：`legend_spec` 已计算但丢弃（`# noqa: F841`）；接通路径 = `RasterAnalysisResult`（`app/services/rs/band_math.py:12-24`）加字段 → `tools/remote_sensing.py` 带出；下游消费面已就绪（tool_dispatch_service.py:999/1252、chat.py:129 白名单）。

## 7. 对实现顺序的影响

1. **P1 质量规则引擎**：quality-audit 端点保留不动（A 线禁改签名）；新 evaluate API 走**新路由文件**（§8 可改清单），报告实体落库 migration 0046。
2. **P3 适配器行为保持**：五套机制默认策略必须等价现状——lakehouse retention 与 artifact sweep 已有 parity 测试钉死形状（test_quota_retention_v5.py、test_gc.py），适配器只登记不接管删除路径；COG 为唯一新增清理能力的对象类，默认策略 = 仅观测。
3. **P5 gc 闭环**：审批状态机新建 `gc_plan` 表（0047），执行穿 durable job（`submit_durable_job`），staging 二段式删除只对本线新统一 GC 生效，不改既有 data-gc/execute 语义。
4. **org_id 兼容**：B 线未合入，artifacts/revisions 无 org_id（租户经 project 间接推导）——本线新表带 `org_id` 占位列（nullable，无 FK 或 nullable FK），查询兼容读取（列缺失时不失败）。
5. **API 形状预告（F 线协调）**：新路由前缀 `/api/v1/data-quality`、`/api/v1/data-lifecycle`；信封遵循 master 现状（dict 直返 + typed 错误映射 404/409/400，分页 `Page[T]` limit/offset）。
