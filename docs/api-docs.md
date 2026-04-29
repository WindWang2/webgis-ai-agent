# WebGIS AI Agent 后端 API 接口文档 (V3.2)

## T001 后端基础架构
### FastAPI 入口与承载流
```python
from app.main import app
# 默认服务端口: 8001
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
V2.1 在聊天网关注入了抵抗 `ERR_CONNECTION_RESET` 的长连接保活架构。


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
| GET | `/api/v1/layers/data/{ref_id}?session_id=xxx` | 获取原始空间 Payload (从 SessionDataManager 中提取) |

#### 提件返回格
- **Content-Type**: `application/json` (GeoJSON FeatureCollection)
- **404 状态**: 表示缓存令牌已失效或 session_id 不匹配，前端需提示用户重试。

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

---

## T007 专题制图与高清导出 (Cartography Export)
V3.0 引入了由 Agent 编排的高清 Canvas 合成导出链路；V3.3 新增标准化 PDF 制图输出与完整地图饰件（指北针、比例尺、图例）。

### 导出任务接口
| 方法 | 路径 | 说明 |
|------|-----|-----|
| POST | `/api/v1/export` | 接收前端合成的 Canvas PNG 图像并持久化 |
| POST | `/api/v1/export/pdf` | 将地图图像合成为标准 A4 横向专题底图 PDF |
| GET | `/api/v1/export/download/{filename}` | 下载生成的成果（PNG 支持内联预览，PDF 强制下载） |

#### PNG 导出（`POST /api/v1/export`）
前端通过 Canvas 2D 合成包含标题、指北针、比例尺、图例等制图饰件的 PNG，随后以 `multipart/form-data` 上传。

| 字段 | 类型 | 说明 |
|-----|------|------|
| `file` | File | PNG/JPG 图像（上限 50 MB） |
| `title` | string (可选) | 制图标题，用于后台日志记录 |

#### PDF 导出（`POST /api/v1/export/pdf`）
将前端生成的 PNG 嵌入标准 A4 横向 PDF，后端使用 matplotlib 添加页眉（标题 / 副标题）和页脚（日期 / 制图者 / 比例说明）。

| 字段 | 类型 | 说明 |
|-----|------|------|
| `file` | File | 基础地图 PNG（上限 50 MB） |
| `title` | string (可选) | 制图主标题 |
| `subtitle` | string (可选) | 制图副标题 |
| `author` | string (可选, 默认 `WebGIS AI Agent`) | 制图者 |
| `scale_text` | string (可选) | 比例尺文本，如 `1:50,000` |

**响应示例：**
```json
{
  "success": true,
  "filename": "map_export_1714396800_a1b2c3.pdf",
  "url": "/api/v1/export/download/map_export_1714396800_a1b2c3.pdf",
  "format": "pdf",
  "message": "专题底图 PDF 已成功生成"
}
```

### AI 工具：`export_thematic_map`
Agent 通过此工具触发前端制图排版流程，支持 PNG 和 PDF 两种输出格式。

| 参数 | 类型 | 默认 | 说明 |
|-----|------|------|------|
| `title` | string | 必填 | 制图主标题 |
| `subtitle` | string | `""` | 制图副标题 |
| `include_legend` | bool | `true` | 是否叠加图例（自动读取 choropleth 图层元数据） |
| `include_compass` | bool | `true` | 是否叠加指北针（旋转角随地图 bearing 同步） |
| `include_scale` | bool | `true` | 是否叠加比例尺（根据当前 zoom 与纬度动态计算） |
| `dark_mode` | bool | `true` | 是否使用暗色模式底纹 |
| `format` | string | `"png"` | 导出格式：`png` 或 `pdf`（PDF 在后端进一步排版） |

### 前端制图饰件渲染（Canvas 2D）
当 Agent 调用 `export_thematic_map` 时，前端 `MapActionHandler` 在地图 `render` 事件后执行以下叠加：
1. **地图截图** — `preserveDrawingBuffer: true` 保证 WebGL 画布可读
2. **标题区** — 顶部渐变底纹 + 主/副标题文字
3. **比例尺** — 4 段交替黑白刻度尺，标注 0 与实际距离（单位自适应 m/km）
4. **指北针** — 红/白双色箭头，随地图旋转角（bearing）同步偏转，附 "N" 标签
5. **图例** — 自动读取当前可见 choropleth 图层的 `metadata`，渲染圆角面板与色块 + 数值区间
6. **水印** — 右下角半透明 "Generated by WebGIS AI Agent"

### 关联指令 (Map Command)
| 指令 (Command) | 参数 (Params) | 说明 |
|---------------|--------------|------|
| `export_map` | `{"title": "...", "subtitle": "...", "dark_mode": bool}` | 驱动前端执行可视化合成提取 |

---

## T008 遥感分析资产管理 (Analysis Assets)
针对持久化分析成果（如 NDVI GeoTIFF）的管理接口。

### 资产管控
| 方法 | 路径 | 说明 |
|------|-----|-----|
| GET | `/api/v1/uploads` | 获取包含 `raster_analysis` 类型的分析产物清单 |
| GET | `/api/v1/static/analysis_results/{file}` | 静态资源访问链路（由 FastAPI 挂载） |

### 遥感分析算子 (Agent Tools)
| 工具名 | 描述 |
|--------|-----|
| `analyze_vegetation_index` | 计算归一化植被指数并启动持久化流程 |
| `list_analysis_assets` | 检索历史分析产物 |
| `manage_analysis_asset` | 执行重命名或逻辑/物理删除 |

---

## T009 实时感知 WebSocket (Bidirectional Perception)
WebSocket 通道用于实时双向感知，独立于 SSE 流式对话。

| 方法 | 路径 | 说明 |
|------|-----|------|
| WS | `/api/v1/ws/{session_id}` | 建立双向实时连接 |

### 感知事件类型 (Client → Server)
| 事件名 | 数据 | 描述 |
|--------|------|------|
| `viewport_change` | `{center, zoom, bearing, pitch}` | 用户拖拽/缩放地图 |
| `layer_toggled` | `{layer_id, visible}` | 图层显隐切换 |
| `layer_opacity_changed` | `{layer_id, opacity}` | 图层透明度调整 |
| `layer_removed` | `{layer_id}` | 图层移除 |
| `base_layer_changed` | `{name}` | 底图切换 |
| `layers_changed` | `{layers: [...]}` | 图层列表批量更新 |
| `layers_reordered` | `{order: [...]}` | 图层排序变更 |
| `state_snapshot` | `{...full state}` | 前端主动推送完整状态快照 |
| `upload_completed` | `{original_name, feature_count, ...}` | 文件上传完成通知 |

### 协议细节
- 客户端需定期发送 `ping` 事件，服务端回复 `pong`
- 感知数据写入 `SessionDataManager` 供 Agent 在下一轮推理时消费
- 连接断开后自动清理，不影响 SSE 对话通道

---

## T010 系统配置接口 (Agent Mainframe)
V3.2 新增的控制面板 API，支持运行时动态配置。

| 方法 | 路径 | 说明 |
|------|-----|------|
| GET | `/api/v1/config/llm` | 获取当前 LLM 配置 |
| POST | `/api/v1/config/llm` | 更新 LLM 配置 (base_url, model, api_key) |
| GET | `/api/v1/config/mcp` | 获取 MCP 服务器配置 |
| POST | `/api/v1/config/mcp` | 更新 MCP 配置并热重载 |
| GET | `/api/v1/config/skills` | 列出已加载的动态技能 |
| POST | `/api/v1/config/skills/upload` | 上传新技能脚本 |
| POST | `/api/v1/config/skills/refresh` | 热重载技能目录 |

---

## T011 扩展工具清单 (V3.0-V3.2 新增)

### 中文地图服务 (Chinese Map Services)
| 工具名 | 描述 |
|--------|-----|
| `search_poi` | 中文 POI 搜索（支持高德/百度/天地图三服务商） |
| `geocode_cn` | 中文地址转坐标 |
| `reverse_geocode_cn` | 坐标转中文地址 |
| `plan_route` | 路径规划（驾车/步行/骑行/公交） |
| `get_district` | 行政区划查询 |

### 空间统计分析 (Spatial Statistics)
| 工具名 | 描述 |
|--------|-----|
| `spatial_cluster` | 空间聚类（DBSCAN / K-Means） |
| `moran_i` | 空间自相关检验 (Moran's I) |
| `hotspot_analysis` | 热点分析 (Getis-Ord Gi*) |
| `kde_surface` | 核密度估计 |
| `idw_interpolation` | 反距离加权插值 |
| `kriging_interpolation` | 普通克里金插值 |
| `service_area` | 服务区分析 |
| `od_matrix` | 起讫点距离矩阵 |

### 地形分析 (Terrain Analysis)
| 工具名 | 描述 |
|--------|-----|
| `compute_terrain` | 地形分析（坡度/坡向/山体阴影），基于 Copernicus DEM 30m |
| `compute_vegetation_index` | 多源遥感指数（NDVI/NDWI/NBR/EVI） |

### 报告生成 (Report)
| 工具名 | 描述 |
|--------|-----|
| `generate_analysis_report` | 生成 PDF/HTML 分析报告 |

### 技能系统 (Dynamic Skills)
| 工具名 | 描述 |
|--------|-----|
| `execute_skill` | 执行动态加载的 Python 技能脚本 |