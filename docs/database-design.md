# WebGIS AI Agent 数据库设计说明书 (v0.1.2)

本系统采用 **Hybrid 混合持久化架构**，关系与空间核心依托 `PostGIS / SQLite`，大容量临时吞吐则依赖 `Redis` 内存沙盒。

## 1. Redis 内存沙盒 (Fetch-on-Demand 枢纽)

为了支撑按需拉取 (Fetch-on-Demand) 架构，系统的热点数据交换不直接入库，而是经过 Redis（可选 RedisSessionDataManager 后端，默认 in-memory）。

| Key 格式 | 类型 | 说明 | TTL |
|------|-----|-------|-----|
| `webgis:session:{session_id}:map_state` | Hash | 前端推送的 viewport / layers / base_layer | 会话级 |
| `webgis:session:{session_id}:events` | List | 感知事件日志 (layer_toggled, base_layer_changed 等) | 会话级 |
| `webgis:ref:{session_id}:{ref_id}` | String/JSON | 工具执行产生的巨型 GeoJSON / 栅格数据，通过 ref_id 提货 | 3600s |
| `webgis:task:{task_id}` | Hash | TaskTracker 任务状态 (pending → running → completed/failed) | 86400s |

> **注**: 实际 Redis 后端实现在 `app/services/session_data_redis.py`，默认后端为 `app/services/session_data.py` (in-memory LRU)。

---

## 2. 核心关系表结构 (SQLAlchemy)

位于 `app/models/db_model.py`，支持 SQLite 用于开发，PostgreSQL (PostGIS) 用于生产。

### 2.1 多租户与用户

#### `organizations` - 组织表
根租户隔离单元。
| 列名 | 类型 | 说明 |
|------|-----|-------|
| id | VARCHAR(255) | PK UUID |
| name | VARCHAR(200) | 组织名称 |
| created_at | DATETIME | 创建时间 |

#### `users` - 用户表
| 列名 | 类型 | 说明 |
|------|-----|-------|
| id | VARCHAR(255) | PK UUID |
| org_id | VARCHAR(255) | FK→organizations.id, ON DELETE CASCADE |
| username | VARCHAR(100) | 唯一 |
| email | VARCHAR(200) | 唯一 |
| role | VARCHAR(20) | CHECK: viewer / editor / admin |
| is_active | BOOLEAN | 账号是否启用 |
| email_verified | BOOLEAN | 邮箱是否验证 |
| token_version | INTEGER | 退出/改密时 bump，使旧 JWT 失效 |
| last_login | DATETIME | 最后登录时间 |
| login_count | INTEGER | 登录次数 |
| created_at | DATETIME | 注册时间 |

### 2.2 会话记忆库

#### `conversations` - 聊天会话表
持久化用户的长线分析历史。
| 列名 | 类型 | 说明 |
|------|-----|-------|
| id | VARCHAR(255) | PK UUID |
| user_id | VARCHAR(255) | FK→users.id, nullable (匿名会话为 NULL), ON DELETE CASCADE |
| title | VARCHAR(200) | 会话标题 |
| created_at | DATETIME | 创建时间 |
| updated_at | DATETIME | 更新时间 |

#### `messages` - 聊天消息表 (Tool Use 追踪器)
完整留痕大模型调用 Tool 的签名与报错 Exception。
| 列名 | 类型 | 说明 |
|------|-----|-------|
| id | INTEGER | PK 自增 |
| conversation_id | VARCHAR(255) | FK→conversations.id, ON DELETE CASCADE |
| role | VARCHAR(20) | CHECK: user / assistant / tool |
| content | TEXT | 消息内容 |
| reasoning_content | TEXT | 思考链 (可选) |
| tool_calls | JSON | 框架级 Function Call |
| tool_call_id | VARCHAR | Agent 取样关联码 |
| tool_result | JSON | 工具执行结果（大结果存 ref_id） |
| created_at | DATETIME | 创建时间 |

### 2.3 图层与权限

#### `layers` - 持久图层表
用户上传或 Agent 持久化的空间数据。
| 列名 | 类型 | 说明 |
|------|-----|-------|
| id | INTEGER | PK 自增 (BigInteger) |
| org_id | VARCHAR(255) | FK→organizations.id, ON DELETE CASCADE |
| creator_id | VARCHAR(255) | FK→users.id, ON DELETE SET NULL |
| name | VARCHAR(200) | 图层名称 |
| layer_type | VARCHAR(20) | CHECK: vector / raster / tile |
| visibility | VARCHAR(20) | CHECK: org / public / private |
| status | VARCHAR(20) | CHECK: pending / processing / ready / error |
| description | TEXT | 图层描述 |
| category | VARCHAR(100) | 分类 |
| geometry_type | VARCHAR(50) | 几何类型 |
| source_format | VARCHAR(50) | 源数据格式 |
| source_url | TEXT | 数据源 URL |
| crs | VARCHAR(50) | 坐标系 (默认 EPSG:4326) |
| bounds | JSON | 空间范围 [w, s, e, n] |
| feature_count | INTEGER | 要素数量 |
| properties_def | JSON | 属性字段定义 |
| style_config | JSON | MapLibre 样式快照 |
| is_basemap | BOOLEAN | 是否底图 |
| is_active | BOOLEAN | 是否启用 |
| error_message | TEXT | 错误信息 |
| processing_progress | INTEGER | 处理进度 0-100 |
| created_at | DATETIME | 创建时间 |
| updated_at | DATETIME | 更新时间 |

#### `layer_permissions` - 图层权限表
| 列名 | 类型 | 说明 |
|------|-----|-------|
| id | INTEGER | PK 自增 |
| layer_id | INTEGER | FK→layers.id, ON DELETE CASCADE |
| user_id | VARCHAR(255) | FK→users.id, ON DELETE CASCADE |
| granted_by | VARCHAR(255) | FK→users.id, ON DELETE SET NULL |
| permission | VARCHAR(20) | CHECK: read / write / admin |
| created_at | DATETIME | 授权时间 |

### 2.4 异步任务

#### `analysis_tasks` - 空间算子工单表
Celery 长耗时队列的状态机。
| 列名 | 类型 | 说明 |
|------|-----|-------|
| id | INTEGER | PK 自增 (BigInteger) |
| org_id | VARCHAR(255) | FK→organizations.id, ON DELETE CASCADE |
| creator_id | VARCHAR(255) | FK→users.id, ON DELETE SET NULL |
| layer_id | INTEGER | FK→layers.id, ON DELETE SET NULL |
| result_layer_id | INTEGER | FK→layers.id, ON DELETE SET NULL |
| task_type | VARCHAR(100) | 任务类型 |
| status | VARCHAR(20) | CHECK: pending / queued / running / completed / failed / cancelled |
| progress | INTEGER | 进度 0-100 |
| progress_message | TEXT | 进度描述 |
| parameters | JSON | 任务参数 |
| result_summary | TEXT | 结果摘要 |
| result_data | JSON | 结果数据 (大结果存 ref_id) |
| error_trace | TEXT | 错误堆栈 |
| celery_task_id | VARCHAR(36) | 唯一, Celery 任务 ID |
| retry_count | INTEGER | 已重试次数 |
| max_retries | INTEGER | 最大重试次数 |
| queued_at | DATETIME | 入队时间 |
| started_at | DATETIME | 开始时间 |
| completed_at | DATETIME | 完成时间 |
| created_at | DATETIME | 创建时间 |

---

## 3. PostGIS 生产环境拓展

当通过 Docker 配置 `DATABASE_URL=postgresql://` 接入时，系统启用高级空间扩展能力：

```sql
-- 启用空间引擎
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS postgis_topology;

-- 对用户上传的极简点位图自动构建 GIST 树索引
CREATE INDEX idx_layer_geom_fast ON "layers" USING GIST(geom_column);

-- [规划中] pgvector：用于搭建 RAG 向量知识库
CREATE EXTENSION IF NOT EXISTS vector;
```
