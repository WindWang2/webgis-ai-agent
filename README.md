# WebGIS AI Agent: 具身空间智能引擎 (Embodied Spatial Intelligence) - V3.0

不再仅仅是一个 GIS 展示工具，而是一个拥有**中枢神经系统 (Agent CNS)** 的具身智能代理。通过实时感官同步与全称异步计算矩阵，它能像专业数据科学家一样感知地图、决策逻辑并执行复杂的地理推演。

## 🌟 核心技术架构 (Agent CNS)

| 层级 | 核心技术选型 | 具身智能特性 |
|---|---|---|
| **具身感官 (Sensory)** | Next.js 14 + MapLibre + HUD 2.0 | **Agentic HUD 2.0 / StoryMap**：全沉浸式座舱设计与双向汇报路由，3D 数字底座引擎。 |
| **中枢神经 (CNS)** | FastAPI + SSE + MCP Protocol | **主动感知与破网检索**：除了自带 Tool 外，成功挂载 Model Context Protocol 接管公网搜索，内置盲区爬虫 Sub-Agent 探测器。 |
| **执行肌肉 (Execution)** | Celery + Redis + PostGIS | **计算隔离与自愈**：空间算子在隔离区运行。具备 "Exception As Thought" 自愈回路。 |

## 🏗️ 项目核心目录

```
├── app/                    # 流式网关与 AI 大脑 (FastAPI)
│   ├── api/routes/         # 极速非阻塞 API 路由层
│   ├── core/               # 设置、SSE 异常自控、身份核验
│   ├── models/             # 数据库 ORM (PostGIS 与 SQLite 兜底)
│   ├── services/           # 任务流发派、Redis 中间件转盘、Orchestrator
│   ├── tools/              # LLM 函数武库 (空间分析、矢量爬提、渲染)
│   └── main.py             # Server Entry
├── frontend/               # V2 渲染引擎与操控主控台 (Next.js)
│   ├── app/                # App Router 后端式页面渲染
│   ├── components/         # 罗盘风 UI / 会话板 / 原生 MapPanel
│   └── lib/                # Fetch-on-Demand 取件客户端
├── docs/                   # V2.1 规划书与架构深潜
├── tests/                  # 智能边界突围测试、防死锁断言
├── Dockerfile              # 分阶段企业级部署映像
└── docker-compose.yml      # 一键拉起 Redis+Celery+DB 战斗群
```

## 🚀 进阶与旗舰级功能群 (V3.0 New!)
- **AI 专题制图与高清合成 (AI Cartographer)**：Agent 现在是一名专业制图师。支持通过 Canvas 2D 重绘合成具有“玻璃质感”标题、动态水印及美学遮罩的高清专题图，并支持云端持久化下载链路。
- **自然资源遥感智能 (Nature Resource AI)**：深度集成 `rasterio`。支持 NDVI 指数秒级计算、多波段卫星影像 (Sentinel-2/Landsat) 智能识别。
- **分析资产库管理 (Asset Manager)**：分析结果不再随 Session 消失。支持对持久化 GeoTIFF 资产进行全生命周期管理（重命名、查看、删除）。
- **感知级实时地图同步与 3D 孪生**：AI 不再“盲目”操作。此外支持 AWS/Opentopography DEM 实时高程渲染与 3D 建筑挤压。

## ⚡ 极速点火部署

建议使用 Docker 体系一键拉起附带超算隔离矩阵 (Redis + Celery) 的完整生态。

```bash
# 构建镜像并带起全套总成
docker-compose up -d --build

# 或者开发模式下独立启动
# 后端 (需预先安装并启动 Redis)
pip install -r requirements.txt
celery -A main.celery_app worker --loglevel=info &
uvicorn main:app --reload --host 0.0.0.0 --port 8001

# 前端
cd frontend && npm install && npm run dev
# 浏览器访问 http://localhost:3000
```

## 📚 开发极客指导

全案项目架构图与防崩坏代码纪律，强烈建议所有共建者入职前通读：
- 📈 [技术方案说明书 V2.1](docs/技术方案说明书.md) (宏观顶层)
- ⚙️ [整体架构深潜](docs/architecture.md) (数据流与 Celery 拓扑)
- 📡 [API 数据流与心跳规范](docs/api-docs.md) (流式连接底线)
- 🗃️ [分片拉取与取件流](docs/data-fetcher.md) (Fetch-On-Demand 机制)
- 🛂 [代码纪律与安全准线](CODE_REVIEW.md) (如何不把这个庞然大物写崩溃)

## License
MIT

