# WebGIS AI Agent: 具身空间智能引擎 (Embodied Spatial Intelligence) - V3.2 / V2 UI

不再仅仅是一个 GIS 展示工具，而是一个拥有**中枢神经系统 (Agent CNS)** 的具身智能代理。通过实时感官同步与全称异步计算矩阵，它能像专业数据科学家一样感知地图、决策逻辑并执行复杂的地理推演。

## 🎨 V2 UI 重新设计 (最新!)

基于 **WebGIS AI Agent v2.html** 设计规范完全重构的新一代用户界面：

| 特性 | 说明 |
|------|------|
| **玻璃拟态设计** | 半透明背景 + 毛玻璃模糊效果 |
| **双主题支持** | 亮色 / 暗色主题无缝切换 |
| **Agentic HUD 2.0** | 全息座舱式感知界面，状态灯语联动 |
| **多标签侧边栏** | 聊天 / 图层 / 操作日志 / 导出 四合一 |
| **动态光效** | 思考时扫描线动画，感知环扩散效果 |

## 🌟 核心技术架构 (Agent CNS)

| 层级 | 核心技术选型 | 具身智能特性 |
|------|--------------|-------------|
| **具身感官 (Sensory)** | Next.js 14 + MapLibre + HUD 2.0 | **Agentic HUD 2.0**：全沉浸式座舱设计，V2 UI 玻璃拟态主题 |
| **中枢神经 (CNS)** | FastAPI + SSE + MCP Protocol | **主动感知与破网检索**：感官同步协议，支持地图状态全感知 |
| **执行肌肉 (Execution)** | Celery + Redis + PostGIS | **计算隔离与自愈**：空间算子在隔离区运行，"Exception As Thought" 自愈回路 |

## 🏗️ 项目核心目录

```
├── app/                    # 流式网关与 AI 大脑 (FastAPI)
│   ├── api/routes/         # 极速非阻塞 API 路由层
│   ├── core/               # 设置、SSE 异常自控、身份核验
│   ├── models/             # 数据库 ORM (PostGIS 与 SQLite 兜底)
│   ├── services/           # 任务流发派、Redis 中间件转盘、Orchestrator
│   ├── tools/              # LLM 函数武库 (空间分析、矢量爬提、渲染)
│   └── main.py             # Server Entry
├── frontend/               # V2 渲染引擎与操控主控台 (Next.js 14)
│   ├── app/                # App Router 后端式页面渲染
│   ├── components/         # 玻璃拟态 UI / Agentic HUD / MapPanel
│   └── lib/                # Zustand Store / Theme System / Fetch-on-Demand
├── docs/                   # 规划书与架构深潜
├── tests/                  # 智能边界突围测试、防死锁断言
├── Dockerfile              # 分阶段企业级部署映像
└── docker-compose.yml      # 一键拉起 Redis+Celery+DB 战斗群
```

## 🚀 进阶与旗舰级功能群

### V3.2 (当前)
- **AI 专题制图与高清合成 (AI Cartographer)**：Canvas 2D 合成标准专题底图，自动指北针、比例尺、图例，导出 PNG/PDF
- **自然资源遥感智能 (Nature Resource AI)**：rasterio 集成，NDVI 指数秒级计算
- **Agent 主控中枢 (The Mainframe)**：全屏设置面板，LLM 热切换、MCP 配置
- **能力自我进化 (Skill Creator)**：自主编写并部署 Python 技能脚本
- **空间分析 MCP 服务器**：独立解耦的 spatial-analysis 服务

### V2 UI (最新重新设计)
- **玻璃拟态界面**：全系统半透明毛玻璃风格
- **双主题系统**：Light / Dark 完整主题支持
- **状态可视化**：思考/执行/完成/错误的动态视觉反馈
- **多标签布局**：侧边栏集成聊天、图层管理、操作日志、导出
- **演示模式**：无需后端即可体验完整流程

## ⚡ 极速点火部署

### Docker 快速启动（推荐）

```bash
# 构建镜像并带起全套总成
docker-compose up -d --build
```

### 开发模式独立启动

```bash
# 后端 (需预先安装并启动 Redis)
pip install -r requirements.txt
celery -A main.celery_app worker --loglevel=info &
uvicorn main:app --reload --host 0.0.0.0 --port 8001

# 前端
cd frontend && npm install && npm run dev
# 浏览器访问 http://localhost:3000
```

## 🎮 快速体验

### 演示模式（无需后端）

1. 启动前端：`cd frontend && npm run dev`
2. 访问 http://localhost:3000
3. 点击左下角 **"Try Demo"** 按钮
4. 发送消息如 "分析北京学校分布" 即可体验完整模拟流程

### 完整功能

需要启动后端服务 + Redis + Celery Worker。

## 📚 开发极客指导

全案项目架构图与防崩坏代码纪律，强烈建议所有共建者入职前通读：
- 📈 [技术方案说明书 V3.2](docs/技术方案说明书.md) (宏观顶层)
- ⚙️ [整体架构深潜](docs/architecture.md) (数据流与 Celery 拓扑)
- 📡 [API 数据流与心跳规范](docs/api-docs.md) (流式连接底线)
- 🗃️ [分片拉取与取件流](docs/data-fetcher.md) (Fetch-On-Demand 机制)
- 🛂 [代码纪律与安全准线](CODE_REVIEW.md) (如何不把这个庞然大物写崩溃)
- 🎨 [前端 V2 设计文档](frontend/README.md) (新 UI 组件架构)

## 🚀 里程碑速览

- ✅ **Phase 1**: 创生、连通与深层筑底
- ✅ **Phase 2**: 具身智化与 CNS 架构融合
- ✅ **Phase 3**: 专业制图与遥感分析增强 (V3.0)
- ✅ **Phase 4**: 主控中枢与算子 MCP 化 (V3.2) - [查看 Release Notes](./docs/release-notes-v3.2.md)
- ✅ **Phase 4+**: V2 UI 重新设计，玻璃拟态体验
- 🌌 **Phase 5**: "超我"演进与星辰大海 (终极谋划期)


## License

MIT
