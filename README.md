# WebGIS AI Agent

智能地图分析与处理服务 - 基于大语言模型的 WebGIS 数据分析与制图系统

## 📌 项目状态
- **当前阶段**: Beta 测试阶段（开发完成度 95%）
- **最新版本**: v1.0.0-beta
- **更新时间**: 2026-04-03
- **已完成里程碑**: 
  - ✅ 核心功能开发（对话界面/地图集成/Agent编排/数据获取）
  - ✅ 全量Bug修复与安全加固
  - ✅ 大模型API全场景验证通过
  - ✅ 全链路集成测试100%通过
  - 🔄 报告生成预览功能开发中

## 技术栈

### 前端
- **框架**: Next.js 14 + React 18 + TypeScript
- **样式**: Tailwind CSS + shadcn/ui
- **地图引擎**: MapLibre GL JS + react-map-gl

### 后端
- **框架**: FastAPI (Python)
- **数据库**: PostgreSQL + SQLAlchemy 2.0
- **GIS**: GeoPandas 0.14+, Shapely 2.0+, Rasterio 1.3+
- **Agent**: LangChain + 字节跳动豆大模型API
- **任务队列**: Celery + Redis

### 部署
- Docker + Docker Compose + Kubernetes 支持
- 支持灰度发布与快速回滚
- **数据库**: PostgreSQL + SQLAlchemy
- **GIS**: GeoPandas, Shapely, Rasterio
- **任务队列**: Celery + Redis

### 部署
- Docker + Docker Compose

## 项目结构

```
webgis-ai-agent/
├── frontend/                 # Next.js 前端
│   ├── app/                  # App Router 页面
│   ├── components/           # React 组件库
│   ├── lib/                  # 工具函数与配置
│   ├── public/               # 静态资源
│   ├── package.json
│   └── ...
├── app/                      # FastAPI 后端
│   ├── api/                  # API 路由定义
│   ├── core/                 # 核心配置（认证/日志/中间件）
│   ├── models/               # 数据库模型与Pydantic schema
│   ├── services/             # 业务逻辑服务
│   ├── agents/               # LLM Agent 编排与工具
│   ├── spatial/              # 空间分析引擎
│   └── utils/                # 工具函数
├── docs/                     # 项目文档
│   ├── task-board.md         # 任务看板
│   ├── 技术方案说明书.md      # 详细技术设计
│   └── ...
├── tests/                    # 测试用例
├── main.py                   # 后端入口文件
├── requirements.txt          # Python 依赖
├── Dockerfile                # 多阶段构建镜像
│   ├── app/                  # App Router
│   ├── components/           # React 组件
│   ├── lib/                  # 工具函数
│   ├── package.json
│   └── ...
├── backend/                  # FastAPI 后端
│   ├── app/
│   │   ├── api/              # API 路由
│   │   ├── core/             # 核心配置
│   │   ├── models/           # 数据模型
│   │   └── services/         # 业务服务
│   └── ...
├── docs/                     # 项目文档
├── Dockerfile                # 多阶段构建
└── README.md
```

## 快速开始

### 前端开发

```bash
cd frontend
npm install
npm run dev
# 访问 http://localhost:3000
```

### 后端开发

```bash
# 安装依赖
pip install -r requirements.txt
# 启动服务
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
# 访问 Swagger 文档: http://localhost:8000/docs
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
# 访问 http://localhost:8000/docs
```

### Docker 部署

```bash
docker build -t webgis-ai-agent .
docker run -p 3000:3000 -p 8000:8000 --env-file .env webgis-ai-agent
```

## ✅ 已实现功能

### 前端功能
1. **AI 对话面板**（左侧）✅
   - 自然语言指令输入（支持中文/英文）
   - Shapefile/GeoTIFF/CSV 地理数据文件上传
   - Markdown 富文本渲染
   - 工具调用实时进度显示
   - 历史对话记录管理

2. **地图面板**（中间）✅
   - MapLibre GL JS 交互式地图（支持多底图切换）
   - 图层管理器（显隐控制/透明度调整/排序）
   - 空间量测工具（距离/面积/坐标拾取）
   - 实时图层动态加载与渲染
   - 地图缩放/平移/旋转操作

3. **结果面板**（右侧）✅
   - 空间分析结果可视化展示
   - 分析报告实时预览
   - 多格式导出（GeoJSON/CSV/PNG/PDF）

### 后端功能 ✅
- RESTful API 网关与统一路由
- LLM Agent 智能编排层（支持多工具调用）
- 多源地理数据获取工具（本地文件/在线服务/数据库）
- 空间分析引擎（缓冲区/叠加分析/网络分析/统计分析）
- 数据可视化与报告生成服务
- JWT 身份认证与权限控制

## 🔄 开发中功能
- **报告生成预览功能**（预计 2026-04-04 完成）
  - 自定义报告模板
  - 一键生成分析报告
  - 在线预览与编辑
  - Word/PDF 格式导出

## 🚀 规划中功能
- 多用户协作与共享
- 历史分析任务管理
- 自定义分析模型上传
- 移动端适配
## 功能模块

### 前端功能
1. **AI 对话面板**（左侧）
   - 自然语言指令输入
   - 文件上传支持
   - Markdown 渲染
   - 工具调用进度显示

2. **地图面板**（中间）
   - MapLibre GL JS 交互式地图
   - 图层管理
   - 空间量测工具
   - 实时图层加载

3. **结果面板**（右侧）
   - 分析结果展示
   - 报告预览
   - 多格式导出（HTML/PDF/Word）

### 后端功能
- API 网关与路由
- Agent 编排层
- 数据获取工具
- 空间分析引擎

## 开发规范

### Git 工作流

1. 从 `develop` 分支创建功能分支
2. 提交使用约定式提交（Conventional Commits）
3. 创建 Pull Request 到 `develop` 分支

### 提交规范

- `feat:` 新功能
- `fix:` 修复 bug
- `docs:` 文档更新
- `style:` 代码格式
- `refactor:` 重构
- `test:` 测试
- `chore:` 构建/工具

## 任务看板

查看 [docs/task-board.md](docs/task-board.md) 了解当前任务状态。

## 技术文档

- [技术方案说明书](docs/技术方案说明书.md)

## 🔒 安全特性
- JWT Token 身份认证
- 接口权限细粒度控制
- SQL注入防护
- XSS攻击防护
- 文件上传安全校验
- 敏感信息加密存储
- 所有接口请求日志审计

## 开发团队

项目由 Kevin 主导开发，AI 辅助编程实现。

### 贡献者
- Kevin (全栈开发/架构设计)
## 开发团队

- **Frontend**: frontend-dev
- **Backend**: backend-dev
- **Testing**: tester
- **Experiment**: experimenter

## License

MIT
