# WebGIS AI Agent 数据库设计

存储分层、关系表结构（SQLAlchemy 模型）、Redis 会话态键布局与 Alembic 迁移链的参考；列名以 `app/models/` 为单一事实源。

> **版本**: v0.1.3 · **状态**: 活文档 · **最后更新**: 2026-08-17

## 目录

- [存储分层](#存储分层)
- [表结构总览（22 张）](#表结构总览22-张)
- [身份与消息域](#身份与消息域)
- [图层与权限域](#图层与权限域)
- [统一任务表 analysis_tasks（ADR-0052）](#统一任务表-analysis_tasksadr-0052)
- [项目工作区域](#项目工作区域)
- [数据织网域](#数据织网域)
- [上传、报告、模板](#上传报告模板)
- [知识库域](#知识库域)
- [Redis 键布局](#redis-键布局)
- [迁移链概览与漂移守护](#迁移链概览与漂移守护)

## 存储分层

| 层 | 载体 | 内容 | 生命周期 |
|----|------|------|---------|
| 关系 + 空间 | **PostGIS 15-3.4**（生产）/ **SQLite**（开发默认 `sqlite:///./data/webgis.db`） | 用户、会话、任务、项目、数据源编目等全部持久业务数据（22 张表） | 持久，Alembic 管理 |
| 会话态 | **Redis**（`USE_REDIS=true` 时 `RedisSessionDataManager`，否则进程内 `MemorySessionStore` 兜底） | 大 GeoJSON ref 数据、地图状态、感知事件、地图动作 ACK | TTL 4 小时 + 每 session LRU |
| 队列 | Redis db0（Celery broker）/ db1（result backend） | Celery 任务消息与结果 | 由 Celery 管理，无 TTL，见下文驱逐策略说明 |

开发默认 `.env.example` 指向 `redis://localhost:16379/0`（result backend `/1`），即 dev compose 暴露的 Redis 端口。

**PostGIS 驱逐策略注意**：生产栈（`docker-compose.prod.yml` 与 `deploy/redis.conf`）固定 `maxmemory-policy noeviction` —— broker/result 键无 TTL，任何 `allkeys-lru` 类策略都会在内存压力下静默丢任务；会话键自带 TTL，内存耗尽时写操作显式报错（`Redis_Memory_High` 告警 actionable），这是有意为之。开发 compose 的 Redis 用 `allkeys-lru`（仅缓存用途）。

## 表结构总览（22 张）

按模型文件清点：`db_model.py` 8 张、`project.py` 7 张、`data_fabric.py` 3 张、`upload.py` / `report.py` 各 1 张、`knowledge_base.py` 2 张。

## 身份与消息域

### `organizations` — 组织（租户根）

| 列 | 类型 | 说明 |
|----|------|------|
| id | Integer PK | |
| name | String(255) NOT NULL | |
| slug | String(100) UNIQUE NOT NULL | |
| description | Text | |
| is_active | Boolean 默认 true | |
| created_at / updated_at | DateTime | |

### `users` — 用户

| 列 | 类型 | 说明 |
|----|------|------|
| id | String(255) PK UUID | |
| org_id | Integer FK→organizations ON DELETE CASCADE | |
| username | String(100) UNIQUE NOT NULL | |
| email | String(255) UNIQUE NOT NULL | |
| password_hash | String(255) | scrypt |
| full_name / avatar_url | String | |
| role | String(20) 默认 viewer，CHECK `viewer/editor/admin` | |
| is_active / email_verified | Boolean | |
| token_version | Integer NOT NULL 默认 0 | logout/改密时 bump，使旧 JWT 失效（`ver` claim） |
| last_login / login_count | DateTime / Integer | |
| created_at / updated_at | DateTime | |

### `conversations` — 会话

| 列 | 类型 | 说明 |
|----|------|------|
| id | String(255) PK | 即 session_id |
| user_id | String(255) FK→users，nullable | 匿名会话为 NULL |
| title | String(200) 默认 "新对话" | |
| owner_token | String(64) nullable | SEC-08：新建匿名会话的归属令牌（`X-Session-Token`）；NULL 为历史匿名会话；认证会话不依赖此列 |
| created_at / updated_at | DateTime | |

### `messages` — 消息（Tool Use 留痕）

| 列 | 类型 | 说明 |
|----|------|------|
| id | Integer PK 自增 | |
| conversation_id | String(255) FK→conversations ON DELETE CASCADE | |
| role | String(20) CHECK `user/assistant/tool` | |
| content | Text | |
| reasoning_content | Text | 思维链（可选） |
| tool_calls | JSON | 框架级 Function Call |
| tool_call_id | String(255) | Agent 取样关联码 |
| tool_result | JSON | 工具结果（大结果存 ref_id） |
| created_at | DateTime | 索引 `idx_message_conversation_created(conversation_id, created_at)` |

## 图层与权限域

### `layers` — 持久图层

Agent 上传/持久化的空间数据登记（图层 CRUD API 已移除，由 Agent 工具链自动管理）。

| 列 | 类型 | 说明 |
|----|------|------|
| id | BigInteger PK 自增 | |
| org_id | Integer FK→organizations NOT NULL | 唯一约束 `(org_id, name)` |
| creator_id | String(255) FK→users ON DELETE SET NULL | |
| name / description / category | String / Text / String(50) | name、category 有索引 |
| layer_type | String(20) NOT NULL CHECK `vector/raster/tile` | |
| geometry_type / source_format / source_url | String | |
| crs | String(100) 默认 EPSG:4326 | |
| bounds | JSON | [w, s, e, n] |
| feature_count | BigInteger | |
| style_config | JSON | 当前套用的 template_id 指针或 MapLibre 样式快照 |
| visibility | String(20) 默认 org CHECK `org/public/private` | |
| is_basemap | Boolean | |
| status | String(20) 默认 pending CHECK `pending/processing/ready/error` | |
| error_message / processing_progress | Text / Integer | |
| created_at / updated_at | DateTime | |

（`properties_def` 列已被迁移 `d3e4f5a6b7c8` 删除。）

### `layer_permissions` — 图层权限

| 列 | 类型 | 说明 |
|----|------|------|
| id | Integer PK | |
| layer_id | BigInteger FK→layers ON DELETE CASCADE | 唯一约束 `(layer_id, user_id)` |
| user_id | String(255) FK→users ON DELETE CASCADE | |
| permission | String(20) CHECK `read/write/admin` | |
| granted_by | String(255) FK→users ON DELETE SET NULL | |
| granted_at / expires_at | DateTime | |

## 统一任务表 analysis_tasks（ADR-0052）

原「空间分析任务表」，经迁移 `0013_unified_durable_job_runtime` 演进为 **Agent task / 空间分析 / Celery job 的统一 durable 事实源**。归属由 `creator_id + owner_token + session_id` 三元组证明；状态机含租约（heartbeat）与取消（cancel_requested_at）。

原有列：`id`（BigInteger→SQLite 变体 Integer 自增）、`org_id`、`creator_id`、`layer_id`、`result_layer_id`、`task_type`（String(50) NOT NULL）、`parameters`（JSON NOT NULL，脱敏展示摘要）、`celery_task_id`（String(100) UNIQUE）、`status`、`progress`（0–100 CHECK）、`progress_message`、`result_summary`（JSON）、`error_trace`（Text）、`retry_count` / `max_retries`、`queued_at` / `started_at` / `completed_at` / `created_at` / `updated_at`。

`status` CHECK：`pending / queued / running / cancelling / completed / failed / cancelled / stale`。

ADR-0052 新增列：

| 列 | 类型 | 说明 |
|----|------|------|
| job_kind | String(20) 默认 analysis | 执行域：`agent / analysis / workflow / explorer` |
| display_name | String(200) | 任务中心展示名（不含用户原文） |
| session_id | String(255) | 会话归属（匿名链：session_id → Conversation.owner_token） |
| owner_token | String(64) | 匿名归属令牌（镜像 SEC-08 模式），索引 `idx_task_owner_token` |
| project_id | String(255) | 项目关联 |
| run_id / turn_id / tool_call_id / agent_task_id / agent_step_id | String | Agent Turn → Step → Job 关联链（`idx_task_agent_task`） |
| idempotency_key | String(128) UNIQUE | SSE 重连/双击幂等 |
| attempt | Integer 默认 1 | retry 创建新 attempt，不覆盖失败证据 |
| worker_id | String(128) | 执行者标识（hostname:pid / worker 名），stale 归因 |
| cancel_requested_at | DateTime | 取消请求的持久事实源 |
| heartbeat_at | DateTime | running 且心跳超时 → stale（索引 `idx_task_status_heartbeat`） |
| result_ref | String(512) | 结果指针（artifact id / 存储路径），巨型结果不入 result_summary |
| dispatch_spec | JSON | 重跑描述符 `{task, args, kwargs}`（写入前脱敏；绝不通过 API 返回） |

任务中心主查询索引：`idx_task_session_created(session_id, created_at)`、`idx_task_creator_created(creator_id, created_at)`、`idx_task_owner_token`。

## 项目工作区域

`app/models/project.py`，7 张表。

### `projects` — 项目

`id`(String PK UUID)、`org_id`(FK CASCADE)、`owner_id`(FK SET NULL)、`name` NOT NULL、`description`、`status` CHECK `active/archived/deleted` 默认 active、`metadata_json`(JSON)、时间戳。索引：org_id / owner_id / status。

### `project_datasets` — 项目数据集

`id`(PK UUID)、`project_id`(FK CASCADE NOT NULL)、`name` NOT NULL、`source_type` NOT NULL、`source_ref`、`schema_profile`(JSON)、`crs` 默认 EPSG:4326、`quality_status` CHECK `unchecked/valid/invalid/warning/unknown/pending/verified`、`version_fingerprint`(String(64)，确定性内容指纹)、`created_at`、`detached_at`（软摘除墓碑：保留历史血缘可解析，列表过滤 NULL）。复合索引含 `(project_id, detached_at)`。

### `workflows` — 工作流定义

`id`(PK UUID)、`project_id`(FK CASCADE NOT NULL)、`name` NOT NULL、`description`、`version` ≥1、`graph_spec`(JSON)、`inputs_schema`(JSON)、`created_from_session`、`current_revision_id`（指向最新不可变修订的普通指针列，无 FK 约束避免循环）、时间戳。

### `workflow_revisions` — 不可变修订快照

`id`(PK UUID)、`workflow_id`(FK CASCADE NOT NULL)、`revision_no`（唯一索引 `(workflow_id, revision_no)`）、`graph_spec`(JSON NOT NULL)、`inputs_schema`、`graph_fingerprint`(String(64) NOT NULL，graph_spec 的 sha256——同图收敛同一修订身份)、`created_by`、`created_at`。

### `workflow_runs` — 运行实例

`id`(PK UUID)、`workflow_id`(FK CASCADE NOT NULL)、`workflow_version`、`project_id`（反规范化租户列，免 join 列表/对比）、`workflow_revision_id`（执行的精确修订）、`graph_snapshot`(JSON，自描述)、`input_bindings`(JSON)、`input_dataset_fingerprints`(JSON，resume 时比对数据集过期)、`status` CHECK `pending/running/completed/failed/cancelled`、`started_at` / `completed_at`、`execution_trace`(JSON)、`outputs`(JSON)、`error_message`、`cost_perf_summary`(JSON)、`completed_steps`(JSON，部分成功步骤)、`run_manifest`(JSON) + `run_fingerprint`(String(64))、`durable_job_id`(BigInteger，可选挂接 ADR-0052 运行时)、`created_at`。

### `artifacts` — 产物

`id`(PK UUID)、`project_id`(FK CASCADE NOT NULL)、`name` NOT NULL、`artifact_type` NOT NULL、`format`、`crs`（未知 CRS 存 NULL，不伪造默认值）、`storage_ref`(String(500))、`upload_record_id`(FK→uploads SET NULL)、`layer_id`(FK→layers SET NULL)、`metadata_json`、`content_fingerprint`(String(64)， truthful 指纹)、`created_at`。索引含 layer_id / upload_record_id / content_fingerprint。

### `artifact_lineages` — 血缘边

`id`(PK UUID)、`artifact_id`(FK CASCADE NOT NULL)、`parent_artifact_id`(FK CASCADE nullable)、`producing_tool`、`tool_version`、`workflow_run_id`(FK SET NULL)、`parameters`(JSON)、`source_dataset_id` + `source_dataset_fingerprint`（根产物的输入数据集出处）、`content_fingerprint`（子产物反规范化指纹）、`created_at`。

## 数据织网域

`app/models/data_fabric.py`，3 张表。

### `data_sources` — 数据源注册

`id`(String PK)、`org_id`(FK CASCADE nullable)、`owner_id`(FK SET NULL)、`name` NOT NULL（唯一约束 `(org_id, name)`）、`source_type` NOT NULL（索引）、`endpoint_url`(Text NOT NULL)、`connection_profile`(JSON)、`capabilities_json`(JSON)、`status` 默认 active（索引）、`last_health_check`、时间戳。复合索引 `(org_id, source_type)`。

### `spatial_catalog_items` — 空间目录轻量索引

`id`(String PK)、`source_id`(FK→data_sources CASCADE NOT NULL)、`name` NOT NULL、`title`、`description`、`geometry_type`、`feature_type` 默认 vector、`crs` 默认 EPSG:4326、`bbox_json`(JSON)、`tags_json`(JSON)、`descriptor_json`(JSON)、`meta_profile_json`(JSON)、`fingerprint`(String(255))、时间戳。复合索引 `(source_id, name)`、`(geometry_type, feature_type)`。

### `materializations` — 物化审计与出处

`id`(String PK)、`dataset_id` NOT NULL、`source_id`(FK SET NULL)、`ref_id` NOT NULL、`query_spec_json`(JSON)、`fingerprint`、`record_count`、`materialized_at`。复合索引 `(dataset_id, ref_id)`。

## 上传、报告、模板

### `uploads` — 上传记录（`app/models/upload.py`）

`id`(Integer PK 自增)、`filename`（存储名 `upload_id/original.*`）、`original_name` NOT NULL、`file_type` CHECK `vector/raster`、`format` CHECK `geojson/shapefile/geotiff/csv/gpkg/kml`、`crs`、`geometry_type`、`feature_count`(BigInteger)、`bbox`(JSON)、`file_size`(BigInteger NOT NULL)、`upload_time`、`session_id`(nullable)。索引 `ix_uploads_session_time(session_id, upload_time)`（列表热路径）。

### `reports` — 分析报告（`app/models/report.py`）

`id`(String(36) PK)、`session_id` NOT NULL（索引）、`title`、`format` CHECK `pdf/html/markdown` 默认 pdf、`status` CHECK `pending/processing/completed/failed`（索引）、`file_path`(Text)、`file_size`、`share_code`(String(32) UNIQUE 索引)、`share_expires_at`、`error_message`、`created_at`。

### `cartography_templates` — 制图模板（`app/models/db_model.py`）

`id`(String PK)、`org_id`(FK CASCADE nullable)、`creator_id`(FK SET NULL)、`kind` CHECK `basemap/symbology/layout/thematic`、`name` NOT NULL、`category`、`keywords`(JSON)、`description`、`payload`(JSON NOT NULL)、`is_builtin`（索引 `(is_builtin, kind)`）、`version` ≥1 默认 1、时间戳。

## 知识库域

`app/models/knowledge_base.py`（向量本体由 FAISS 管理，表仅元数据与分块文本）。

### `knowledge_documents` — 文档

`id`(String(36) PK UUID)、`title` NOT NULL、`content`(Text 摘要)、`file_type`、`file_path`、`chunk_count`、`status` 默认 pending（pending/indexing/completed/failed；索引）、`error_message`、`org_id`(FK CASCADE)、`creator_id`(FK SET NULL)、`created_at`、`indexed_at`。

### `knowledge_chunks` — 分块

`id`(String(36) PK UUID)、`document_id`(FK→knowledge_documents CASCADE NOT NULL，索引)、`content`(Text NOT NULL)、`chunk_index` NOT NULL、`start_char` / `end_char`。

## Redis 键布局

实现：`app/services/session_data_redis.py`（`RedisSessionStore`）；内存兜底 `app/services/session_data.py`（`MemorySessionStore`，协议对齐）。TTL 常量：`SESSION_TTL = DATA_TTL = STATE_TTL = EVENTS_TTL = 4h`；每 session ref 容量 200（LRU，`refs_order` ZSet）；事件日志上限 20；地图动作 ACK 上限 200。另有进程内 L1 读缓存（TTL 2s，容量 512 session）。

| 键 | 类型 | 说明 | TTL |
|----|------|------|-----|
| `session:{sid}:data:{ref_id}` | String(JSON) | ref 数据本体（GeoJSON / 栅格指针等），读时刷新 | 4h |
| `session:{sid}:meta:{ref_id}` | String(JSON) | store 时预计算的 descriptor（bbox/计数/几何类型），O(1) 读取 | 4h |
| `session:{sid}:refs_order` | ZSet（score=时间戳） | 每 session LRU 淘汰序，容量 200 | 会话级刷新 |
| `session:{sid}:index` | Set | 该 session 全部 ref_id | 会话级刷新 |
| `session:{sid}:aliases` | Hash | 别名 → ref_id | 会话级刷新 |
| `session:{sid}:refs` | Hash | ref_id → 别名（反向映射） | 会话级刷新 |
| `session:{sid}:state` | Hash | 地图状态（viewport / layers / base_layer / `_started_at` / 各 key 的 `_seq`、`_updated_at` / `owner_token_digest`） | 4h |
| `session:{sid}:events` | List | 感知事件日志（rpush + ltrim 至 20 条） | 4h |
| `session:{sid}:map_actions` | Hash | 地图动作终态 ACK（field=action_id，首达获胜），上限 200 | 4h |
| `session:{sid}:map_actions_order` | ZSet（score=到达时间戳） | ACK 插入序（hash 无序，靠它读回） | 4h |
| `sessions:active` | Set | 活跃 session id 集合 | — |
| `sessions:activity` | ZSet（score=时间戳） | session 最近活动时间（idle 清扫依据） | — |

写入要点：大 payload 的 JSON 序列化在事件循环外线程执行；ref 数据读取走 best-effort TTL 刷新（失败不阻断读）；`overwrite` 写回同一 ref_id 并删除过期 descriptor；LRU 淘汰同时联动失效 MVT 空间索引与瓦片缓存。Celery 使用同实例 db0（broker）/ db1（result），无前缀、由 Celery 自管。

## 迁移链概览与漂移守护

Alembic 单链 18 个 revision（`migrations/versions/`），base `85e4939d7e07` → head `0018_dedupe_duplicate_indexes`：

| # | Revision | 内容 |
|---|----------|------|
| 1 | `85e4939d7e07` | initial schema（base） |
| 2 | `6c68ec475cfa` | users.token_version |
| 3 | `e46935cd5dd1` | FK ON DELETE + CHECK 约束 |
| 4 | `a1b2c3d4e5f6` | analysis_tasks 进度 CHECK |
| 5 | `f123456789ab` | 复合索引 |
| 6 | `6ef479051297` | conversations.owner_token（SEC-08） |
| 7 | `b2c3d4e5f6a7` | messages(conversation_id, created_at) 索引 |
| 8 | `c1d2e3f4a5b6` | cartography_templates 建表 + 种子 |
| 9 | `d3e4f5a6b7c8` | 删除 layers.properties_def |
| 10 | `0010_project_workspace_workflow` | 项目/工作流域建表 |
| 11 | `0011_enterprise_geospatial_data_fabric` | 数据织网域建表（PostGIS 专属操作） |
| 12 | `0012_add_composite_indexes_pd_wr` | project_datasets / workflow_runs 复合索引 |
| 13 | `0013_unified_durable_job_runtime` | analysis_tasks 演进为统一 durable job 表（additive） |
| 14 | `0014_workflow_provenance_revisions` | workflow_revisions、指纹与血缘列 |
| 15 | `0015_uploads_session_upload_index` | uploads(session_id, upload_time) |
| 16 | `0016_knowledge_documents_owner` | knowledge_documents 归属列 |
| 17 | `0017_close_model_migration_drift` | 关闭模型↔迁移漂移 |
| 18 | `0018_dedupe_duplicate_indexes` | 去重索引（head） |

- SQLite 兼容：迁移使用 `render_as_batch=True`（SQLite 无 ALTER 列能力时批量重建表）。
- **漂移守护**：CI 的 `db-migrations` job（`.github/workflows/production.yml`）先在真实 PostGIS 15-3.4 service 上 `alembic upgrade head`，再跑 `tests/test_deploy_migration_wiring.py::test_migrated_schema_matches_models`——迁移建出的 schema 必须覆盖模型声明的表/列/索引，漂移即红。
- 部署侧自动执行：Dockerfile.prod entrypoint 启动前 `alembic upgrade head`（legacy create_all 库自动 `stamp head` 收编；`SKIP_DB_MIGRATIONS=true` 供 celery-worker 跳过并发竞争）；k8s 由 initContainer 执行。
- 生成新 revision：改 `app/models/` 后 `DATABASE_URL=sqlite:///tmp.db alembic revision --autogenerate -m "..."`，复查生成的脚本再提交。
