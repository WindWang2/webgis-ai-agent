# WebGIS AI Agent v2 设计方案

**日期**: 2026-04-05
**定位**: 遥感/GIS 研究教学工具，单用户，本地部署
**模型对接**: OpenAI 兼容 Function Calling（不使用 LangChain）

## 1. 架构总览

```
用户对话 → LLM (Function Calling) → 工具调度 → 结果可视化
                ↓
    ┌───────────┼───────────┐
    ↓           ↓           ↓
  OSM/天地图  空间分析    遥感数据
  (Overpass) (GeoPandas) (Sentinel/NASA)
    ↓           ↓           ↓
  地图渲染   报告生成    图层管理
```

前端三面板布局保持不变：对话(左) + 地图(中) + 结果(右)。

## 2. 开放地理资源层

| 数据源 | 用途 | 协议 | 认证 |
|--------|------|------|------|
| OSM 瓦片 | 底图 | XYZ Tiles | 无需 key |
| 天地图 WMTS | 国内底图 | WMTS | 免费 key |
| Overpass API | 矢量数据(路网/建筑/POI/行政区) | REST | 无需 key |
| Nominatim | 地名搜索 / geocoding | REST | 无需 key |
| Sentinel Hub | 遥感影像(哨兵2号等) | API | API Key |
| NASA EarthData | 遥感/DEM | STAC/API | 免费注册 |

## 3. 核心模块（6个）

### M1: 对话引擎 (Chat Engine)
- OpenAI 兼容客户端，支持任意本地/云端模型切换
- Function Calling 工具注册与调度
- 对话历史管理（SQLite 存储）
- 流式响应（SSE）

### M2: OSM 数据服务 (OSM Data Service)
- Overpass API 查询封装：POI / 路网 / 建筑 / 行政区边界
- Nominatim geocoding：地名 → 坐标 / 坐标 → 地名
- 查询结果自动转 GeoJSON 供地图渲染
- 结果缓存（本地文件，TTL 可配）

### M3: 遥感数据服务 (Remote Sensing Service)
- Sentinel Hub 集成：多光谱影像获取、NDVI/NDWI 等指数计算
- NASA EarthData / STAC：DEM(SRTM)、Landsat 等
- 影像裁剪/重采样为 GeoTIFF
- 影像统计信息提取

### M4: 空间分析引擎 (Spatial Analysis)
- 保留现有 5 个算子：缓冲区、叠加、最近邻、统计、网络
- 新增：kriging 插值、热力图、voronoi
- 输入：GeoJSON / GeoDataFrame
- 输出：GeoJSON + 统计摘要

### M5: 地图服务 (Map Service)
- 后端生成 GeoJSON 图层数据
- 前端 MapLibre 渲染：双底图切换(OSM + 天地图)
- 图层管理：显隐、透明度、排序
- 地图截图导出

### M6: 报告生成 (Report)
- 保留现有 ReportService
- 对接分析结果，自动生成图文报告
- 导出：HTML / PDF / Markdown
- 报告模板可选

## 4. Function Calling 工具定义

```python
TOOLS = [
    # 地理编码
    "geocode",           # 地名 → 坐标
    "reverse_geocode",   # 坐标 → 地名

    # OSM 数据
    "query_osm_poi",     # 查询POI
    "query_osm_roads",   # 查询路网
    "query_osm_buildings",  # 查询建筑
    "query_osm_boundary",   # 查询行政区

    # 遥感数据
    "fetch_sentinel",    # Sentinel 影像
    "fetch_dem",         # DEM 高程数据
    "compute_ndvi",      # NDVI 计算

    # 空间分析
    "buffer_analysis",   # 缓冲区
    "overlay_analysis",  # 叠加分析
    "nearest_neighbor",  # 最近邻
    "spatial_stats",     # 空间统计
    "heatmap",           # 热力图

    # 输出
    "generate_report",   # 生成报告
    "export_geojson",    # 导出 GeoJSON
]
```

## 5. 技术栈

### 后端
- FastAPI + Uvicorn
- OpenAI Python SDK（兼容接口）
- GeoPandas / Shapely / Rasterio
- overpy（OSM Overpass）
- sentinelhub / pystac-client
- SQLite（对话历史 + 图层元数据）
- aiohttp（异步 HTTP 客户端）

### 前端
- Next.js 14 + React 18 + TypeScript
- MapLibre GL JS（地图渲染）
- Tailwind CSS
- SSE（流式对话）

### 部署
- Docker Compose（后端 + 前端）
- 可选 GPU 支持（遥感数据处理）

## 6. 删除的模块

以下模块从代码库中移除（与核心 WebGIS 功能无关）：
- `app/services/pr_checker/` — PR 检查
- `app/services/pr_workflow/` — PR 工作流
- `app/services/pr_check_flow.py`
- `app/services/issue_*` — Issue 工作流系列
- `app/services/feishu_*` — 飞书通知
- `app/services/celery_*` — Celery 任务队列
- `app/services/orchestration/` — 旧 Agent 编排
- `app/services/task_queue.py`
- `app/api/routes/webhook.py` / `issue_webhook.py`
- `app/core/auth.py` — JWT 认证（单用户不需要）
- 相关测试文件

## 7. 数据库

从 PostgreSQL + SQLAlchemy 切换到 SQLite：
- 对话历史
- 图层元数据
- 分析结果缓存

使用 SQLAlchemy 2.0（方便以后切回 PostgreSQL）。

## 8. 项目结构（目标）

```
├── app/
│   ├── main.py                 # FastAPI 入口
│   ├── core/
│   │   ├── config.py           # 配置（模型、API keys、数据目录）
│   │   ├── database.py         # SQLite 连接
│   │   └── exception.py        # 全局异常
│   ├── api/
│   │   └── routes/
│   │       ├── chat.py         # 对话 API (SSE)
│   │       ├── map.py          # 地图/图层 API
│   │       └── health.py       # 健康检查
│   ├── services/
│   │   ├── chat_engine.py      # 对话引擎 + FC 调度
│   │   ├── osm_service.py      # OSM 数据服务
│   │   ├── rs_service.py       # 遥感数据服务
│   │   ├── spatial_service.py  # 空间分析
│   │   ├── report_service.py   # 报告生成
│   │   └── map_service.py      # 地图/图层
│   ├── models/
│   │   ├── schemas.py          # Pydantic 模型
│   │   └── database.py         # SQLAlchemy 模型
│   └── tools/                  # FC 工具定义
│       ├── __init__.py
│       ├── geocoding.py
│       ├── osm.py
│       ├── remote_sensing.py
│       ├── spatial.py
│       └── report.py
├── frontend/
│   ├── app/
│   ├── components/
│   └── lib/
├── tests/
├── data/                       # 数据存储（.gitignore）
├── main.py
├── requirements.txt
├── Dockerfile
└── docker-compose.yml
```

## 9. 实施策略

分 4 个阶段，每阶段产出可运行的增量：

**Phase A: 清理 + 基础对话链路**
- 删除无关模块
- 重构对话引擎（FC）
- 前后端对话打通（SSE）
- 交付：能对话，模型能调用工具

**Phase B: OSM + 地图**
- OSM 底图 + 天地图底图切换
- Overpass 查询工具
- 前端地图渲染 GeoJSON
- 交付：对话查 POI，地图上显示

**Phase C: 空间分析**
- 空间分析工具接入 FC
- 分析结果地图渲染
- 报告生成对接
- 交付：完整对话→分析→可视化链路

**Phase D: 遥感数据**
- Sentinel Hub / NASA 集成
- NDVI 等指数计算
- 影像地图叠加
- 交付：遥感影像查询和基础分析
