# WebGIS AI Agent

基于大语言模型的智能地图分析与处理服务。

## 技术栈

| 层 | 技术 |
|---|------|
| 前端 | Next.js 14 + React 18 + TypeScript + Tailwind CSS + MapLibre GL JS |
| 后端 | FastAPI + SQLAlchemy 2.0 + Celery + Redis |
| GIS | GeoPandas / Shapely / Rasterio |
| AI | LangChain + 豆包 API / 本地模型 |
| 部署 | Docker Compose |

## 项目结构

```
├── app/                    # FastAPI 后端
│   ├── api/routes/         # API 路由 (chat, layer, report, orchestration, ...)
│   ├── core/               # 配置、认证、异常处理
│   ├── models/             # 数据库模型 & Pydantic schemas
│   ├── services/           # 业务逻辑 (orchestration, data_fetcher, report, ...)
│   └── main.py             # FastAPI 应用入口
├── frontend/               # Next.js 前端
│   ├── app/                # App Router 页面
│   ├── components/         # React 组件 (chat, map, panel, report)
│   └── lib/                # 工具函数 & API 客户端
├── tests/                  # 测试用例
├── docs/                   # 文档
├── main.py                 # 启动入口
├── Dockerfile
└── docker-compose.yml
```

## 功能

- **AI 对话**：自然语言 GIS 查询，支持文件上传、流式响应
- **地图交互**：MapLibre 地图，图层管理，空间量测
- **空间分析**：缓冲区分析、叠加分析、网络分析、统计分析
- **报告生成**：分析结果导出为 HTML/PDF/Markdown
- **Agent 编排**：多 Agent 协同处理复杂空间任务
- **数据获取**：支持本地文件、OSS、PostGIS、第三方 API

## 快速开始

```bash
# 后端
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8000

# 前端
cd frontend && npm install && npm run dev
# 访问 http://localhost:3000

# Docker
docker-compose up -d
```

## 文档

- [技术方案说明书](docs/技术方案说明书.md)
- [部署指南](docs/DEPLOYMENT.md)
- [API 文档](docs/api-docs.md)
- [数据库设计](docs/database-design.md)

## License

MIT
