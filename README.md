# WebGIS AI Agent (V2.0 深度智能版)

基于大语言模型 (LLM) 的通用空间智能先知与 GIS 处理引擎。不再仅仅是制图工具，而是能够执行极智场景计算、主动检索和预测性地理模拟的私人数据科学家。

## 🌟 核心技术架构 (V2.0)

| 层 | 核心技术选型 | 架构亮点 |
|---|---|---|
| **前端脑** | Next.js 14 + MapLibre GL JS + HUD 2.0 | **极智交互体验**：自研全沉浸式 HUD 概念，包含 Dynamic Island 任务指挥舱。基于原生 GPU 实现 60fps 平滑渲染与超百万点位动态聚合。 |
| **神经流** | FastAPI + SSE + Map State Sync | **感知级同步**：引入实时地图状态回传机制，AI 实时感知视野与底图状态。配合 Fetch-on-Demand (按需提货) 机制，彻底攻克大数据量阻塞难题。 |
| **后超算** | Docker + Celery + Redis + PostGIS | **严格计算隔离**：空间算子被隔离至专用 Worker。具备 "Exception As Thought" 自愈回路，能在计算失败时主动反思并重调参数修复。 |

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
├── docs/                   # V2.0 规划书与架构深潜
├── tests/                  # 智能边界突围测试、防死锁断言
├── Dockerfile              # 分阶段企业级部署映像
└── docker-compose.yml      # 一键拉起 Redis+Celery+DB 战斗群
```

## 🚀 进阶与愿景级功能群
- **感知级实时地图同步**：AI 不再“盲目”操作。每一轮工具执行后，系统自动同步当前地图中心、缩放等级及已选底图至 AI 上下文，确保决策逻辑的连续性和稳定性。
- **自愈式智能空间计算**：具备 "Exception As Thought" 失败逻辑自重构反射。捕捉投影、坐标或拓扑异常，由 AI 在极速静默模式中主动重试修复。
- **全沉浸式 HUD 指挥舱**：采用 Dynamic Island、RagInsightCard 与任务时装化 Timeline，提供工业级专业地理分析的可视化交互流程。

## ⚡ 极速点火部署

建议使用 Docker 体系一键拉起附带超算隔离矩阵 (Redis + Celery) 的完整生态。

```bash
# 构建镜像并带起全套总成
docker-compose up -d --build

# 或者开发模式下独立启动
# 后端 (需预先安装并启动 Redis)
pip install -r requirements.txt
celery -A main.celery_app worker --loglevel=info &
uvicorn main:app --reload --host 0.0.0.0 --port 8000

# 前端
cd frontend && npm install && npm run dev
# 浏览器访问 http://localhost:3000
```

## 📚 开发极客指导

全案项目架构图与防崩坏代码纪律，强烈建议所有共建者入职前通读：
- 📈 [技术方案说明书 V2.0](docs/技术方案说明书.md) (宏观顶层)
- ⚙️ [整体架构深潜](docs/architecture.md) (数据流与 Celery 拓扑)
- 📡 [API 数据流与心跳规范](docs/api-docs.md) (流式连接底线)
- 🗃️ [分片拉取与取件流](docs/data-fetcher.md) (Fetch-On-Demand 机制)
- 🛂 [代码纪律与安全准线](CODE_REVIEW.md) (如何不把这个庞然大物写崩溃)

## License
MIT
