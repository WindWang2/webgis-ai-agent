# WebGIS AI Agent 后端 API 接口文档 (V2.1)

## T001 后端基础架构
### FastAPI 入口与承载流
```python
from app.main import app
# 默认服务端口: 8000
# 运行环境拦截: Uvicorn Asyncio
```

### 基础健康与探针端点
| 方法 | 路径 | 说明 |
|------|-----|-----|
| GET | / | 根路径，网关在线状态 |
| GET | /api/v1/health | 详细健康状态与底层依赖 (Redis/PostGIS) |
| GET | /docs | Swagger OpenAPI 动态文档 |

---

## T002 核心 AI 编排层 (SSE 流式)
V2.0 在聊天网关注入了抵抗 `ERR_CONNECTION_RESET` 的长连接保活架构。

### 会话与记忆管理
| 方法 | 路径 | 说明 |
|------|-----|-----|
| GET | /api/v1/chat/sessions | 获取长短期记忆会话列表 |
| GET | /api/v1/chat/sessions/{session_id} | 恢复上下文及工具调用帧 |
| DELETE| /api/v1/chat/sessions/{session_id} | 删除会话与级联缓存数据 |

### 流式大模型网关 (关键交互)
| 方法 | 路径 | 说明 |
|------|-----|-----|
| POST | /api/v1/chat/stream | 发送分析请求 (SSE 协议分发) |

#### [重要] SSE 事件类型扩展 (HUD 指令型)
V2.1 引入了更加细粒度的任务跟踪事件，用于驱动前端 HUD (Task Timeline & Dynamic Island)：

| 事件名 | 载荷数据 (Data) | 描述 |
|--------|----------------|------|
| `task_start` | `{"task_id": "..."}` | 任务总线开启 |
| `step_start` | `{"step_id": "...", "tool": "..."}` | 具体算子开始执行 |
| `step_result`| `{"result": {...}, "has_geojson": bool}` | 算子执行成功，包含部分脱敏结果 |
| `step_error` | `{"error": "..."}` | 算子执行失败，反馈给 AI 进行自愈 |
| `task_complete` | `{"summary": "..."}` | 整个任务链条完成 |

#### [注意] SSE 流 Heartbeat 规范
当 Agent 在后台调用 Celery 进行分钟级别的地理测算时，此时 LLM 端静默不输出文本。为防止云防火墙断开闲置连接，SSE 网关每 15 秒将推送一条隐式心跳：
```text
: keep-alive

```
前端解析流时需自动跳过此类占位符，保证长链接稳固不断。

---

## T003 地图控制协议 (Map Interaction Protocol)
AI Agent 通过在流式回复中嵌入特定的 JSON 指令，直接驱动前端 MapLibre 实例。

### 核心指令集
| 指令 (Command) | 参数 (Params) | 说明 |
|---------------|--------------|------|
| `BASE_LAYER_CHANGE` | `{"name": "高德影像"}` | 切换底图样式 |
| `LAYER_VISIBILITY_UPDATE` | `{"layer_id": "...", "visible": true, "opacity": 0.5}` | 修改图层状态 |
| `FLY_TO` | `{"center": [lng, lat], "zoom": 12}` | 视场平滑飞越 |
| `LAYER_STYLE_UPDATE` | `{"layer_id": "...", "color": "#ff0000"}` | 实时修改图层渲染色 |

#### 回复格式要求
AI 会在 Markdown 回复的末尾或逻辑断点处插入以下原生 JSON 块，前端 `MapActionHandler` 将捕获并执行：
```json
{
  "command": "BASE_LAYER_CHANGE",
  "params": { "name": "ESRI 影像" }
}
```

---

## T004 零拷贝提取层 (Fetch-on-Demand) 
为了保障大模型的流式文本绝不被庞杂的地理坐标卡死，大模型输出的 Layer 结果只包含 `ref_id`。实际数据拉取依靠以下接口：

| 方法 | 路径 | 说明 |
|------|-----|-----|
| GET | `/api/v1/layer/{ref_id}/data` | 获取原始空间 Payload (从 Redis 中解压提件) |

#### 提件返回格
- **Content-Type**: `application/json` (GeoJSON FeatureCollection)
- **404 状态**: 表示 Redis 中的缓存令牌已到期 (TTL失效)，前端需提示用户重试。

---

## T004 传统图层管控API (重装存储)
用户主动上传的基建底座。

### 文件载入
```
POST /api/v1/layers/upload
Content-Type: multipart/form-data

入参:
- file: (GeoJSON/Shapefile/KML 支持多重压缩)
- name: 图层标识名

机制: 强制切网格进 PostGIS 建空间索引
```

### 空间元数据探测
| 方法 | 路径 | 说明 |
|------|-----|-----|
| GET | /api/v1/layers | PostGIS 图层目录清单 |
| GET | /api/v1/layers/{id} | 图层元属性与几何中心 |
| DELETE | /api/v1/layers/{id} | 层级销毁 |

---

## T005 异步任务队列监控 (Celery Cluster)
被下发的空间切割重度任务监管。

| 方法 | 路径 | 说明 |
|------|-----|-----|
| GET | /api/v1/tasks/{id} | 读取 Celery 后台挂载状态 |
| POST| /api/v1/tasks/{id}/cancel | 终止后台进程（释放 CPU） |

### 核心计算列队
- **Queue: `default`**: API 快速搬运
- **Queue: `spatial_heavy`**: 大于十万网格叠加运算、多点缓冲区裁剪
- **Backend Cache**: Redis `localhost:6379`

---

## T006 统一响应格盾与异常
```json
{
  "code": "SUCCESS",         // 操作结果锁 (SUCCESS/ERROR)
  "success": true,           // BOOLEAN 快判
  "message": "执行通过",      // 异常阻挡或告警通报
  "data": {...}              // Payload
}
```

### 错误捕获池 (Exception Throwings)
| 错误码 | 机制与应对层 |
|--------|-----|
| `VALIDATE_ERROR` | FastAPI Pydantic 参数校验溃败，需拦截。 |
| `SPATIAL_TOPOLOGY_ERR` | GIS自交或环路错误，需通过 "Exception As Thought" 抛回给大模型启动清理指令。 |
| `CELERY_TIMEOUT` | 分布式节点队列拥堵，要求延后补录。 |
| `CACHE_MISS` | Fetch-On-Demand 提货码超期被清理，下发指令要求大模型从零原算。 |