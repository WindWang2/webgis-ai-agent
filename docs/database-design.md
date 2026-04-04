# WebGIS AI Agent 数据库设计文档
## T008 PostGIS 数据库设计
### 核心表结构
#### organizations - 组织/租户表
| 列名 | 类型 | 说明 |
|------|-----|-------|
| id | INTEGER | PK 自增 |
| name | VARCHAR(255) | 组织名称 |
| slug | VARCHAR(100) | 唯一标识(slug) |
| created_at | DATETIME | 创建时间 |
#### users - 用户表
| 列名 | 类型 | 说明 |
|------|-----|-------|
| id | INTEGER | PK 自增 |
| org_id | INTEGER | FK->organizations.id |
| username | VARCHAR(100) | 唯一用户名 |
| email | VARCHAR(255) | 唯一邮箱 |
| password_hash | VARCHAR(255) | BCrypt哈希 |
| role | VARCHAR(20) | admin/editor/viewer |
| is_active | BOOLEAN | 是否激活 |
| created_at | DATETIME | 创建时间 |

索引: idx_user_org(org_id)
#### layers - 图层表(核心)
| 列名 | 类型 | 说明 |
|------|-----|-------|
| id | BIGINT | PK自增 |
| org_id | INTEGER | FK->organizations.id |
| creator_id | INTEGER | FK->users.id |
| name | VARCHAR(255) | 图层名称(index) |
| description | TEXT | 描述 |
| category | VARCHAR(50) | 分类(index) |
| layer_type | VARCHAR(20) | vector/raster/tile |
| geometry_type | VARCHAR(50) | Point/LineString/Polygon |
| source_format | VARCHAR(50) | geojson/shapefile/tiff |
| source_url | VARCHAR(1000) | 数据源URL |
| crs | VARCHAR(100) | 坐标参考系(EPSG:4326) |
| bounds | JSONB | 空间范围{xmin,ymin,xmax,ymax} |
| feature_count | BIGINT | 要素数量 |
| properties_def | JSONB | 属性字段定义 |
| style_config | JSONB | 符号化配置 |
| visibility | VARCHAR(20) | public/org/private |
| is_basemap | BOOLEAN | 是否底图 |
| status | VARCHAR(20) | pending/processing/ready/error |
| created_at | DATETIME | 创建时间 |
| updated_at | DATETIME | 更新时间 |

约束:
- UniqueConstraint(org__id, name)
- CheckConstraint(layer_type IN ('vector','raster','tile'))

索引:
- idx_layer_status(status)
- idx_layer_created(created_at)

#### analysis_tasks - 空间分析任务表
| 列名 | 类型 | 说明 |
|------|-----|-------|
| id | BIGINT | PK自增 |
| org_id | INTEGER | FK->organizations.id |
| creator_id | INTEGER | FK->users.id |
| layer_id | BIGINT | FK->layers.id |
| result_layer_id | BIGINT | FK->layers.id 结果图层 |
| task_type | VARCHAR(50) | 分析类型(buffer/clip...) |
| parameters | JSONB | 分析参数 |
| celery_task_id | VARCHAR(100) | Celery任务ID(unique) |
| status | VARCHAR(20) | 任务状态(index) |
| progress | INTEGER | 0-100 进度 |
| progress_message | VARCHAR(255) | 进度描述 |
| result_summary | JSONB | 结果统计 |
| error_trace | TEXT | 错误堆栈 |
| retry_count | INTEGER | 重试次数 |
| max_retries | INTEGER | 最大重试次数3 |
| created_at | DATETIME | 创建时间 |
| updated_at | DATETIME | 更新时间 |

索引:
- idx_task_status(status)
- idx_task_celery(celery_task_id)

#### layer_permissions - 图层权限表
| 列名 | 类型 | 说明 |
|------|-----|-------|
| id | INTEGER | PK自增 |
| layer_id | BIGINT | FK->layers.id |
| user_id | INTEGER | FK->users.id |
| permission | VARCHAR(20) | view/edit/admin |
| granted_by | INTEGER | FK->users.id 授权者 |
| granted_at | DATETIME | 授权时间 |
| expires_At | DATETIME | 过期时间 |

约束: UniqueConstraint(layer_id, user_id)
### PostGIS 扩展配置
```sql
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS postgis_topology;
CREATE EXTENSION IF NOT EXISTS fuzzystrmatch;
```
### 空间索引优化
- GiST 索引: 对于矢量图层geom列建立GiST索引加速空间查询
- BRIN 索引: 对于大型静态表可用BRIN块级索引
- 定期VACUUM ANALYZE保持统计信息新鲜

### SQLAlchemy 模型
位于: app/models/db_models.py
```python
from app.models.db_model import (
    Base, Organization, User, Layer,
    AnalysisTask, LayerPermission
)
```

### 数据库连接配置
- DATABASE_URL: postgresql+psycopg2://user:pass@host:5432/webgis
- 生产环境: Docker Compose 管理，自动初始化