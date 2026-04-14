# WebGIS AI Agent 数据库设计说明书 (V2.1)

本系统采用 **Hybrid 混合持久化架构**，关系与空间核心依托 `PostGIS / SQLite`，大容量临时吞吐则依赖 `Redis` 内存沙盒。

## 1. Redis 内存沙盒 (V2.0 新增枢纽)
为了支撑按需拉取 (Fetch-on-Demand) 架构，系统的热点数据交换不再入库，而是经过 Redis。

| Key 格式 | 类型 | 说明 | TTL (过期期) |
|------|-----|-------|----|
| `webgis:session:{uuid}:cache:{layer_id}` | String/JSON | 用于存放后端执行工具抓取的超大型 GeoJSON 聚合包，免除 SSE 推流负担 | 3600s |
| `webgis:task:{task_id}:result` | JSON | Celery 处理任务后的结果挂载点 | 86400s |
| `webgis:chat:{session_id}:status` | Hash | 会话互斥锁与 SSE 心跳控制字典 | 会话结束清除 |

---

## 2. 核心关系表结构 (SQLAlchemy)

位于 `app/models/db_models.py`，支持 SQLite 用于开发，PostgreSQL 用于生产。

### 2.1 会话记忆库

#### `conversations` - 聊天会话表
持久化用户的长线分析历史。
| 列名 | 类型 | 说明 |
|------|-----|-------|
| id | VARCHAR | PK UUID |
| title | VARCHAR(200) | 会话标题 |
| created_at | DATETIME | 创建时间 |
| updated_at | DATETIME | 更新时间 |

#### `messages` - 聊天消息表 (Tool Use 追踪器)
完整留痕大模型调用 Tool 的签名与报错 Exception（实现自愈闭环）。
| 列名 | 类型 | 说明 |
|------|-----|-------|
| id | INTEGER | PK 自增 |
| conversation_id | VARCHAR | FK->conversations.id |
| role | VARCHAR(20) | user/assistant/tool |
| content | TEXT | 消息内容 |
| tool_calls | JSON | 框架级 Function Call |
| tool_call_id | VARCHAR | Agent 取样关联码 |
| tool_result | JSON | 此处不再存放超大 GeoJSON，而是存放 `ref_id` |
| created_at | DATETIME | - |

### 2.2 核心底座物料库

#### `layers` - 持久图层表
当用户手动上传高价值基建图纸或持久化分析成果时入表。
| 列名 | 类型 | 说明 |
|------|-----|-------|
| id | INTEGER | PK自增 |
| name | VARCHAR(200) | 图层名称 |
| type | VARCHAR(50) | geojson / raster / vector / heatmap |
| source | VARCHAR(100) | osm / sentinel / user_upload |
| data_path | TEXT | 真实服务器路径 / S3 路径 |
| style | JSON | MapLibre 原生 Shader 参数快照 |
| visible | BOOLEAN | 是否可见 |
| opacity | FLOAT | - |

#### `analysis_tasks` - 空间算子工单表
Celery 长耗时队列的状态机。
| 列名 | 类型 | 说明 |
|------|-----|-------|
| id | INTEGER | PK自增 |
| name | VARCHAR(200) | 任务名称 |
| type | VARCHAR(50) | intersect / buffer_geo / st_join |
| status | VARCHAR(50) | pending / success / failed / retrying |
| result | JSON | 任务结果摘要或取件 `ref_id` |

---

## 3. PostGIS 生产环境拓展 (Production)

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