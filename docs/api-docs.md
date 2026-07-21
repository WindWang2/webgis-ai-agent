# WebGIS AI Agent 后端 API 接口文档 (v0.1.2)

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
| GET | /api/v1/health/live | 存活探针 (K8s liveness) |
| GET | /api/v1/health/ready | 就绪探针 (K8s readiness, 检查依赖可达) |
| GET | /docs | Swagger OpenAPI 动态文档 |

---

## T002 认证 API (JWT + token_version)

| 方法 | 路径 | 说明 |
|------|-----|-----|
| POST | /api/v1/auth/register | 注册 (默认关闭，需运维创建) |
| POST | /api/v1/auth/login | 登录，返回 access token (30min) + refresh token (7d) |
| POST | /api/v1/auth/refresh | 用 refresh token 换取新的 access token |
| POST | /api/v1/auth/logout | Bump token_version，使所有旧 token 失效 |
| GET | /api/v1/auth/me | 获取当前用户信息 |

- Access token 携带 `ver` claim，与 `User.token_version` 绑定。
- Logout 时 bump `token_version`，旧 token 立即 401。
- Refresh token 用于换取新 access token，不能直接访问受保护资源。

---

## T003 核心 AI 编排层 (SSE 流式)

### 会话与记忆管理
| 方法 | 路径 | 认证 | 说明 |
|------|-----|------|-----|
| GET | /api/v1/chat/sessions | optional | 获取当前用户的历史会话列表 (匿名返回空) |
| GET | /api/v1/chat/sessions/{session_id} | optional | 恢复上下文及工具调用帧 (校验所有权) |
| DELETE | /api/v1/chat/sessions/{session_id} | optional | 删除会话与级联缓存数据 (校验所有权) |
| GET | /api/v1/chat/sessions/{session_id}/map-state | optional | 获取会话持久化地图状态 |
| POST | /api/v1/chat/sessions/{session_id}/map-state | optional | 推送地图状态 (viewport/layers/base_layer) |

### 流式大模型网关 (关键交互)
| 方法 | 路径 | 认证 | 说明 |
|------|-----|------|-----|
| POST | /api/v1/chat/stream | optional | 发送分析请求 (SSE 协议分发) |
| POST | /api/v1/chat/completions | optional | 非流式对话接口 |

### SSE 事件类型

| 事件名 | 载荷数据 (Data) | 描述 |
|--------|----------------|-----|
| `token` | `{"content": "...", "is_reasoning": false, "session_id": "..."}` | 流式文本 token |
| `task_start` | `{"task_id": "..."}` | 任务总线开启 |
| `step_start` | `{"step_id": "...", "tool": "..."}` | 具体算子开始执行 |
| `step_result` | `{"result": {...}, "has_geojson": bool}` | 算子执行成功 |
| `step_error` | `{"error": "..."}` | 算子执行失败 |
| `task_complete` | `{"summary": "..."}` | 任务链条完成 |
| `task_cancelled` | `{"task_id": "..."}` | 任务被取消 |
| `task_error` | `{"task_id": "...", "error": "..."}` | 任务异常 |
| `tool_call` | `{"name": "...", "arguments": {...}}` | Agent 发起工具调用 |
| `plan_ready` | `{session_id, task_id, intent, domains, steps}` | Plan-First 模式生成计划 |
| `plan_step_done` | `{session_id, task_id, step_n}` | 计划步骤完成 |
| `plan_finalized` | `{session_id, task_id, skipped}` | 计划终态 |

#### SSE 流 Heartbeat 规范
当 Agent 在后台调用 Celery 进行长耗时计算时，SSE 网关每 **5 秒**推送一条隐式心跳：
```text
: keep-alive

```
前端解析流时需自动跳过此类占位符。

---

## T004 零拷贝提取层 (Fetch-on-Demand)

| 方法 | 路径 | 认证 | 说明 |
|------|-----|------|-----|
| GET | /api/v1/layers/data/{ref_id} | optional | 获取原始空间 Payload (校验 session 所有权) |

---

## T005 异步任务队列监控 (Celery Cluster)

| 方法 | 路径 | 认证 | 说明 |
|------|-----|------|-----|
| GET | /api/v1/tasks/{task_id} | required | 读取任务状态 (校验所有权) |
| GET | /api/v1/tasks | required | 按 session_id 列任务 (必填参数) |
| DELETE | /api/v1/tasks/{task_id} | required | 取消任务 (校验所有权) |
| GET | /api/v1/tasks/status/{task_id} | required | Celery 原生任务状态 |
| DELETE | /api/v1/tasks/status/{task_id} | required | 撤销 Celery 任务 |

### 核心计算队列
- **Queue: `default`**: API 快速搬运
- **Queue: `spatial_heavy`**: 大于十万网格叠加运算
- **Backend Cache**: Redis

---

## T006 统一响应格式

```json
{
  "success": true,
  "message": "执行通过",
  "data": {...}
}
```

---

## T007 专题制图与高清导出 (Cartography Export)

| 方法 | 路径 | 认证 | 说明 |
|------|-----|------|-----|
| POST | /api/v1/export | required | 接收前端 Canvas PNG 并持久化 |
| POST | /api/v1/export/pdf | required | 合成为 A4 横向 PDF |
| POST | /api/v1/export/geojson | required | 持久化 GeoJSON 为可下载文件 |
| GET | /api/v1/export/download/{filename} | required | 下载 (校验文件所有权) |

### AI 工具：`export_thematic_map`
| 参数 | 类型 | 默认 | 说明 |
|-----|------|------|-----|
| `title` | string | 必填 | 制图主标题 |
| `subtitle` | string | `""` | 制图副标题 |
| `include_legend` | bool | `true` | 是否叠加图例 |
| `include_compass` | bool | `true` | 是否叠加指北针 |
| `include_scale` | bool | `true` | 是否叠加比例尺 |
| `dark_mode` | bool | `true` | 暗色模式底纹 |
| `format` | string | `"png"` | 导出格式: `png` 或 `pdf` |

---

## T008 遥感分析资产管理 (Analysis Assets)

| 方法 | 路径 | 认证 | 说明 |
|------|-----|------|-----|
| GET | /api/v1/uploads | required | 获取分析产物清单 |
| GET | /api/v1/static/analysis_results/{file} | required | 静态资源访问 |

### 遥感分析算子 (Agent Tools)
| 工具名 | 描述 |
|--------|-----|
| `analyze_vegetation_index` | 计算归一化植被指数 |
| `list_analysis_assets` | 检索历史分析产物 |
| `manage_analysis_asset` | 重命名或删除 |

---

## T009 实时感知 WebSocket (Bidirectional Perception)

| 方法 | 路径 | 认证 | 说明 |
|------|-----|------|-----|
| WS | /ws/{session_id} | optional | 建立双向实时连接 (空 token 被拒绝) |

### 感知事件类型 (Client → Server)
| 事件名 | 数据 | 描述 |
|--------|------|------|
| `viewport_change` | `{center, zoom, bearing, pitch}` | 用户拖拽/缩放 |
| `layer_toggled` | `{layer_id, visible}` | 图层显隐 |
| `layer_opacity_changed` | `{layer_id, opacity}` | 透明度 |
| `layer_removed` | `{layer_id}` | 移除图层 |
| `base_layer_changed` | `{name}` | 底图切换 |
| `layers_changed` | `{layers: [...]}` | 图层列表更新 |
| `layers_reordered` | `{order: [...]}` | 图层排序 |
| `state_snapshot` | `{...full state}` | 完整状态快照 |
| `upload_completed` | `{original_name, feature_count, ...}` | 上传完成 |
| `ping` | `{}` | 心跳 (服务端回复 pong) |

---

## T010 系统配置接口 (Agent Mainframe)

| 方法 | 路径 | 认证 | 说明 |
|------|-----|------|-----|
| GET | /api/v1/config/llm | admin | 获取 LLM 配置 |
| POST | /api/v1/config/llm | admin | 更新 LLM 配置 |
| GET | /api/v1/config/skills | admin | 列出已加载技能 |
| POST | /api/v1/config/skills/upload | admin | 上传技能脚本 (AST 校验) |
| POST | /api/v1/config/skills/refresh | admin | 热重载技能 |

---

## T011 扩展工具清单 (部分)

### 空间统计分析
| 工具名 | 描述 |
|--------|-----|
| `spatial_cluster` | 空间聚类 (DBSCAN / K-Means) |
| `moran_i` | 空间自相关检验 |
| `hotspot_analysis` | 热点分析 (Getis-Ord Gi*) |
| `kde_surface` | 核密度估计 |
| `idw_interpolation` | 反距离加权插值 |
| `kriging_interpolation` | 普通克里金插值 |
| `calculate_sde` | 标准差椭圆 |
| `calculate_nearest` | 最近邻分析 |
| `cluster_narrated` | 聚类叙事分析 |
| `h3_lisa` | H3 六边形局部空间自相关 |

### 地图交互工具
| 工具名 | 描述 |
|--------|-----|
| `measure_distance` | 测量大圆线距离 |
| `measure_area` | 计算投影面积 |
| `add_marker` | 投放标记图钉 |
| `clear_annotations` | 清除测量图形 |
| `fly_to_location` | 地名飞越定位 |
| `zoom_to_bbox` | 缩放至包围盒 |
| `zoom_to_layer` | 缩放至图层范围 |
| `reset_map_view` | 重置视口 |
| `set_map_view` | 精确设定视口 |
| `reorder_layer` | 图层排序 |
| `remove_layer` | 移除图层 |
| `display_layer` | 显示隐藏图层 |
