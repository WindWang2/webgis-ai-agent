# WebGIS AI Agent: 具身空间智能引擎 (Embodied Spatial Intelligence) - v0.1.2 / V2 UI

不再仅仅是一个 GIS 展示工具，而是一个拥有**中枢神经系统 (Agent CNS)** 的具身智能代理。通过实时感官同步与全称异步计算矩阵，它能像专业数据科学家一样感知地图、决策逻辑并执行复杂的地理推演。

## 🎨 V2 UI 重新设计

> 注：V2 玻璃拟态后续已演进为 **Visual System V4（专业 GIS 工作台）**，语义 token 体系 + 明暗双主题硬约束，详见 [frontend/README.md](frontend/README.md)。

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
| **中枢神经 (CNS)** | FastAPI + SSE + 分层 ToolCatalog | **主动感知与破网检索**：感官同步协议，支持地图状态全感知 |
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
│   ├── app/                # App Router 页面 (含 StoryMap)
│   ├── components/         # 玻璃拟态 UI / Agentic HUD / MapPanel / 制图饰件
│   │   ├── chat/           # 对话组件 (SuggestedPrompts, TaskProgress)
│   │   ├── map/            # MapPanel, map-action-handler, legends
│   │   ├── hud/            # Agentic HUD 2.0 (Dynamic Island)
│   │   ├── sidebar/        # 多标签侧边栏
│   │   ├── drawers/        # 历史记录 / 设置抽屉
│   │   ├── explorer/       # 空间探索器
│   │   └── report/         # 报告生成器
│   ├── lib/                # Zustand Store / Theme System / Fetch-on-Demand
│   │   ├── hooks/          # 自定义 React Hooks
│   │   ├── store/          # Zustand 状态管理
│   │   ├── providers/      # Context Providers
│   │   ├── utils/          # 工具函数
│   │   ├── types/          # TypeScript 类型定义
│   │   └── map-kit/        # 地图工具包
│   └── public/             # 静态资源
├── docs/                   # 规划书与架构深潜
├── tests/                  # 智能边界突围测试、防死锁断言
├── Dockerfile              # 分阶段企业级部署映像
└── docker-compose.yml      # 一键拉起 Redis+Celery+DB 战斗群
```

## 🛡️ 质量加固波次 (2026-08 · 52 项 Issue 全清零)

2026-08-16/17 对当时全部 52 个开放 Issue（#514–#565）做了逐根因修复，分 11 批独立 PR（#566–#576）全部合并，每个修复附回归测试与独立代码评审：

| 主题 | 关键修复 |
|------|----------|
| **契约与鉴权** | MVT 瓦片/导出下载鉴权链路打通（MapLibre `transformRequest` 凭据注入、认证下载传输层）；遗留别名表不再遮蔽 `remove_layer`/`zoom_to_layer`；`to_llm_response` 家族（~30 处）分析结果恢复自动上图；`explorer_progress` 端到端可见（登录走属主校验独立流，匿名走会话隔离聊天流桥接） |
| **会话与安全** | 匿名会话按 `owner_token` 分桶，跨用户驱逐删除消除；所有权守卫不再全量加载消息；`{"error": ...}` 工具返回统一识别为失败（计划不再跨过失败推进）；UploadRecord 工具查询按会话作用域；工具事件行注入统一转义 |
| **GIS 数值正确性** | `raster_difference`/`temporal_raster` 尊重声明 nodata（含场景级 AOI 默认与 "unknown" 趋势诚实化）；`buffer_smart` 英尺投影单位换算方向修正（曾小 ~10.8×）；EVI/NDVI 零像素不再计为有效；DANGLING_ENDPOINT 容差复活；Amap/Baidu 提供商响应契约修正 |
| **性能** | 质量审计拓扑检查有界化（400 环 4116ms→286ms，截断显式上报）；VRP 2-opt O(n³)→O(1) delta（320 站点 51.1s→572ms）；closest_facility 按 top-K 惰性建 Route（9.3×）；屏障索引化；会话存储序列化移出事件循环 |
| **部署矩阵** | `DATA_DIR` 全矩阵统一 + api/celery 共享存储（跨容器 404 消除）；kustomize 镜像契约；secure 栈 Redis 改 `noeviction`（broker 键不再被驱逐）；k8s Secret 键与文档对齐；Grafana 仪表板 provisioning 落地 |
| **CI 与测试门禁** | 回滚/预览固定 `WEBGIS_IMAGE`；Prometheus 配置随部署传输；real-services lane 跑生产 Celery；Playwright 运行时校验器进入 nightly 门禁；后端覆盖率闸 10→75（实测 ~82%）、前端阈值落地；**DB Migration Gate 修复**（`alembic_version` 列宽 + PostGIS tiger 扩展排除） |

## 🚀 进阶与旗舰级功能群

### V0.1.2+ 加固版 (当前)
- **MVT 瓦片与下载全量可用**：>5000 要素图层瓦片在登录/匿名会话下均可加载；导出文件、报告、聊天内嵌图片经认证传输层下载（不再 401）
- **深度探索进度端到端可见**：Explorer 进度/结果实时推送至 UI，任务可关闭、有上限、随会话切换清理
- **会话管理闭环**：历史抽屉支持两步确认删除会话；新建会话在工作区非空时需确认；项目上下文随聊天请求注入
- **设置面板去伪存真**：底图卡片直绑 TILE_PROVIDERS 目录；假控件（Tweaks/技能开关/RAG 滑块）按"接线或移除"逐一落实
- **GIS 数值真值保障**：nodata/NaN/单位换算/拓扑容差全链路数值断言测试覆盖

### V0.1.2
- **专业化地图导出面板 (Professional Map Export)**：支持所见即所得的 WYSIWYG 遮罩预览，配置 A4/屏幕画幅，最高支持 300 DPI 矢量重采样的高清图件导出（包含动态适配的指北针、比例尺、图例及自定义标题水印），一键导出 PNG / PDF。
- **AI 专题制图与高清合成 (AI Cartographer)**：Canvas 2D 合成标准专题底图，结合 LLM 动态空间分析结果。
- **Agent-Map Bridge 稳定性加固**：重构 SSE 解析与会话状态同步，彻底消除生命周期竞态条件，实现无损长文本传输流与毫秒级地图感知回传。
- **自然资源遥感智能 (Nature Resource AI)**：rasterio 集成，NDVI 指数秒级计算
- **Agent 主控中枢 (The Mainframe)**：全屏设置面板，LLM 热切换、技能管理
- **能力自我进化 (Skill Creator)**：自主编写并部署 Python 技能脚本

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
celery -A app.services.task_queue worker --loglevel=info &
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

> **注意**: 运行前需确保 `.env` 文件已配置。以下变量为必填项：
> - `JWT_SECRET_KEY`：JWT 签名密钥（生产模式必填；开发模式自动生成随机密钥）
> - `LLM_API_KEY`：LLM API 密钥（占位符 `your-api-key-here` 会被检测并警告）
> - `DATABASE_URL`：数据库连接 URL（默认 SQLite）
> - `REDIS_PASSWORD`：Redis 密码（docker-compose 必填）
> - `DB_PASSWORD`：PostgreSQL 密码（docker-compose 必填）

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
- 📈 [技术方案说明书](docs/技术方案说明书.md) (宏观顶层)
- ⚙️ [整体架构深潜](docs/architecture.md) (数据流与 Celery 拓扑)
- 📡 [API 数据流与心跳规范](docs/api-docs.md) (流式连接底线)
- 🗃️ [分片拉取与取件流](docs/data-fetcher.md) (Fetch-On-Demand 机制)
- 🛂 [代码纪律与安全准线](CODE_REVIEW.md) (如何不把这个庞然大物写崩溃)
- 🎨 [前端 V2 设计文档](frontend/README.md) (新 UI 组件架构)
- 📜 [CHANGELOG](CHANGELOG.md) (逐波次工程变更记录)

### 质量门禁（本地与 CI 同构）

```bash
# 后端：单元 + 集成（CI 在 PostGIS+Redis service container 上跑同一套）
pytest -q tests/unit tests/integration
ruff check app tests

# 前端：测试 + 双份 typecheck + lint + 构建（构建是 Next.js 页面导出检查的唯一真实门禁）
cd frontend && npx vitest run && npm run typecheck && npm run lint && npx next build
```

- 后端覆盖率闸 `--cov-fail-under=75`（ratchet，实测 ~82%）；前端 vitest thresholds 75/70/75/60
- PR 门禁：Backend Tests / Frontend Tests / Code Quality / Security Scan 全绿才可合并
- 性能回归：PR 跑确定性冒烟子集，墙钟套件在 nightly perf lane（基线锚定，回归硬失败）
- DB Migration Gate：全量 alembic 链 base→head 在 PostGIS 上验证 + 模型↔迁移漂移比对

## 🚀 里程碑速览

- ✅ **Phase 1**: 创生、连通与深层筑底
- ✅ **Phase 2**: 具身智化与 CNS 架构融合
- ✅ **Phase 3**: 专业制图与遥感分析增强
- ✅ **Phase 4**: 主控中枢与工具体系增强
- ✅ **Phase 4+**: V2 UI 重新设计，玻璃拟态体验
- ✅ **Phase 5**: 安全硬化、多租户隔离、流式稳定性加固
- ✅ **Phase 5+**: 质量加固波次（2026-08）—— 52 项 Issue 逐根因清零，契约/安全/GIS 数值/性能/部署/CI 六线并进
- 🌌 **Phase 6**: 用户认证与动态栅格图层 (规划中)


## License

MIT
