# WebGIS AI Agent (V2.0 深度智能版)

基于大语言模型 (LLM) 的通用空间智能先知与 GIS 处理引擎。不再仅仅是制图工具，而是能够执行极智场景计算、主动检索和预测性地理模拟的私人数据科学家。

## 🌟 核心技术架构 (V2.0)

| 层 | 核心技术选型 | 架构亮点 |
|---|---|---|
| **前端脑** | Next.js 14 + React 18 + MapLibre GL JS | 彻底抛弃低效后端生图。基于原生 GPU Shader 实现超百万点位聚合、随动补偿热力网格与 60fps 平滑视场追踪。 |
| **神经流** | FastAPI + Server-Sent Events (SSE) | 加入主动的心跳保活长链接技术，攻克深层 LLM 推理带来的连接超时熔断症。引入独特的 **Fetch-on-Demand (按需分片拖引)**，彻底消灭大型图层数据撑爆浏览器缓存的隐患。 |
| **后超算** | Docker + Celery + Redis + PostGIS | 实施**严格的计算隔离**。沉重的空间交集切割转入专用 Worker 无缝处理，主网关全时非阻塞。 |
| **AI底座** | Claude 3.5+ + Tool Use + 混合记忆 | 抛弃了僵硬固化的 LangChain 单链，直连原生工具钩子，拥有 "Exception As Thought" 失败闭环自救功能。 |

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
- **自愈式智能空间计算**：不是死板的机械图层堆叠。系统会捕捉投影坐标失效等异常，在极速静默模式中投递给 LLM 修复，主动调参重试直至画制完美图幅。
- **动态数字孪生叙事**：在交互框内直出带交互折线图的报告板。未来将直通 StoryMap (全视角互动演说地图)。
- **隔离式后台高并运单**：通过分布式 Celery 工头派发极度消耗内存的缓冲层叠推演，全城几百万节点的计算不再影响前台喝茶聊天。

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
