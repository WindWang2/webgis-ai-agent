# WebGIS AI Agent

智能地图分析与处理服务 - 基于大语言模型的 WebGIS 数据分析与制图系统

## 技术栈

### 前端
- **框架**: Next.js 14 + React 18 + TypeScript
- **样式**: Tailwind CSS + shadcn/ui
- **地图引擎**: MapLibre GL JS + react-map-gl

### 后端
- **框架**: FastAPI (Python)
- **数据库**: PostgreSQL + SQLAlchemy
- **GIS**: GeoPandas, Shapely, Rasterio
- **任务队列**: Celery + Redis

### 部署
- Docker + Docker Compose

## 项目结构

```
webgis-ai-agent/
├── frontend/                 # Next.js 前端
│   ├── app/                  # App Router
│   ├── components/           # React 组件
│   ├── lib/                  # 工具函数
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

## 开发团队

- **Frontend**: frontend-dev
- **Backend**: backend-dev
- **Testing**: tester
- **Experiment**: experimenter

## License

MIT
