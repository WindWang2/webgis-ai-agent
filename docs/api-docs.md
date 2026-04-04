# WebGIS AI Agent 后端接口文档
## T001 后端基础架构
### FastAPI 入口
```python
from app.main import app
# 服务端口: 8000
```
### 基础端点
| 方法 | 路径 | 说明 |
|------|-----|-----|
| GET | / | 根路径，健康检查 |
| GET | /api/v1/health | 详细健康状态 |
| GET | /docs | Swagger 文档 |

### 中间件
- CORS: 允许跨域
- GZIP: 响应压缩
- 日志: 结构化日志输出
- 异常处理: 统一错误响应

## T002 图层管理API
### 文件上传
```
POST /api/v1/layers/upload
Content-Type: multipart/form-data

参数:
- file: 文件 (GeoJSON/Shapefile/TIFF)
- name: 图层名称
- layer_type: vector|raster|tile
- description: 描述(可选)

返回: {layer_id, feature_count, status}
```

### CRUD 操作
| 方法 | 路径 | 说明 |
|------|-----|-----|
| GET | /api/v1/layers | 图层列表(分页) |
| GET | /api/v1/layers/{id} | 图层详情 |
| PUT | /api/v1/layers/{id} | 更新图层 |
| DELETE | /api/v1/layers/{id} | 删除图层 |

### 空间查询
| 方法 | 路径 | 说明 |
|------|-----|-----|
| GET | /api/v1/layers/{id}/bounds | 获取边界 |
| GET | /api/v1/layers/{id}/geojson | 导出GeoJSON |
| POST | /api/v1/layers/spatial-query | 空间关系查询 |

### 空间查询操作符
- `contains`: 包含
- `intersects`: 相交
- `within`: 范围内
- `touches`: 接壤
- `crosses`: 穿过
- `distance`: 距离查询

## T003 空间分析任务队列
### 任务管理
| 方法 | 路径 | 说明 |
|------|-----|-----|
| POST | /api/v1/tasks/submit | 提交分析任务 |
| GET | /api/v1/tasks/{id} | 任务详情 |
| GET | /api/v1/tasks/{id}/progress | 进度查询(SSE) |
| POST | /api/v1/tasks/{id}/cancel | 取消任务 |
| POST | /api/v1/tasks/{id}/retry | 重试失败任务 |

### 分析类型
- buffer: 缓冲区分析
- clip: 裁剪分析
- intersect: 相交分析
- dissolve: 融合分析
- union: 联合分析
- spatial_join: 空间连接
- statistics: 统计分析

### Celery 配置
- Broker: Redis localhost:6379/0
- Backend: Redis localhost:6379/1
- 队列: default, high_priority, spatial_analysis

## 统一响应格式
```json
{
  "code": "SUCCESS",
  "success": true,
  "message": "操作成功",
  "data": {...}
}
```

## 错误码
| 错误码 | 说明 |
|--------|-----|
| VALIDATE_ERROR | 参数校验失败 |
| NOT_FOUND | 资源不存在 |
| PERMISSION_DENIED | 权限不足 |
| PARSE_ERROR | 文件解析失败 |
| SERVER_ERROR | 服务器内部错误 |
| TASK_FAILED | 任务执行失败 |