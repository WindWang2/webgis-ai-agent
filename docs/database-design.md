# WebGIS AI Agent 数据库设计文档
## T008 数据库设计
### 核心表结构

系统目前包含以下核心表，位于 `app/models/db_models.py` 中，采用 SQLAlchemy 定义。

#### users - 用户表
| 列名 | 类型 | 说明 |
|------|-----|-------|
| id | VARCHAR(100) | PK |
| name | VARCHAR(100) | 用户名 |
| email | VARCHAR(200) | 邮箱 |
| role | VARCHAR(50) | 角色 |
| created_at | DATETIME | 创建时间 |

#### user_roles - 用户角色表
| 列名 | 类型 | 说明 |
|------|-----|-------|
| id | INTEGER | PK 自增 |
| user_id | VARCHAR(100) | FK->users.id |
| role | VARCHAR(50) | 角色名 |

#### conversations - 聊天会话表
| 列名 | 类型 | 说明 |
|------|-----|-------|
| id | VARCHAR | PK UUID |
| title | VARCHAR(200) | 会话标题 |
| created_at | DATETIME | 创建时间 |
| updated_at | DATETIME | 更新时间 |

#### messages - 聊天消息表
| 列名 | 类型 | 说明 |
|------|-----|-------|
| id | INTEGER | PK 自增 |
| conversation_id | VARCHAR | FK->conversations.id |
| role | VARCHAR(20) | user/assistant/tool |
| content | TEXT | 消息内容 |
| tool_calls | JSON | FC Tool Calls |
| tool_call_id | VARCHAR | Tool ID |
| tool_result | JSON | Tool Result |
| created_at | DATETIME | 创建时间 |

#### layers - 图层表(核心)
| 列名 | 类型 | 说明 |
|------|-----|-------|
| id | INTEGER | PK自增 |
| name | VARCHAR(200) | 图层名称 |
| type | VARCHAR(50) | geojson/raster/vector |
| source | VARCHAR(100) | osm/sentinel/upload |
| data_path | TEXT | 文件路径或 GeoJSON |
| style | JSON | MapLibre 样式配置 |
| visible | BOOLEAN | 是否可见 |
| opacity | FLOAT | 透明度 |
| created_at | DATETIME | 创建时间 |

#### analysis_tasks - 空间分析任务表
| 列名 | 类型 | 说明 |
|------|-----|-------|
| id | INTEGER | PK自增 |
| name | VARCHAR(200) | 任务名称 |
| type | VARCHAR(50) | 分析类型 |
| status | VARCHAR(50) | 任务状态 (pending/success/error...) |
| result | JSON | 任务结果 |
| created_at | DATETIME | 创建时间 |
| updated_at | DATETIME | 更新时间 |

### PostGIS 扩展配置
(当前使用SQLite或标准关系库的话不强制，如果是PostgreSQL则需要以下扩展)
```sql
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS postgis_topology;
CREATE EXTENSION IF NOT EXISTS fuzzystrmatch;
```

### SQLAlchemy 模型
位于: `app/models/db_models.py`
```python
from app.models.db_models import (
    User, UserRole, Conversation, Message, Layer, AnalysisTask
)
```

### 数据库连接配置
- 配置文件中 `DATABASE_URL`，支持 SQLite 用于开发，PostgreSQL 用于生产。
- 模型在启动时通过 Alembic 或 SQLAlchemy 的 `Base.metadata.create_all()` 自动创建。