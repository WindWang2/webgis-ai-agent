# WebGIS AI Agent

基于 Next.js + FastAPI 的智能地图分析与处理全栈项目。

## 功能特性

- 🗺️ 交互式地图（缩放、平移）
- 📂 图层管理（上传、列表、加载、属性查询）
- 📐 空间分析（任务提交、进度展示、结果加载）
- 🔐 JWT认证 + RBAC权限控制
- 🛡️ 组织级数据隔离

## 项目结构

```
webgis-ai-agent/
├── frontend/              # 前端项目
│   ├── app/
│   │   ├── components/     # 组件（Sidebar, Header, MapView）
│   │   ├── layout.tsx      # 全局布局
│   │   └── page.tsx        # 首页
│   ├── package.json
│   └── README.md
├── app/                    # 后端应用代码
│   ├── api/               # API 路由
│   │   └── routes/        # 路由模块
│   ├── core/              # 核心配置
│   ├── models/            # 数据模型
│   └── services/          # 业务服务
├── docs/                  # 文档
├── tests/                 # 测试代码
├── Dockerfile             # Docker 镜像
├── requirements.txt       # Python 依赖
└── README.md             # 项目说明
```

## 技术栈

### 前端
- Next.js 16 (App Router)
- MapLibre GL JS
- TypeScript

### 后端
- **框架**: FastAPI
- **数据库**: PostgreSQL + SQLAlchemy
- **GIS**: GeoPandas, Shapely, Rasterio
- **认证**: JWT + BCrypt
- **任务队列**: Celery + Redis
- **部署**: Docker

## 快速开始

### 前端开发

#### 环境要求
- Node.js 18+
- npm 9+

#### 启动
```bash
cd frontend
npm install
npm run dev
```
访问 http://localhost:3000

### 后端开发

#### 环境要求
- Python 3.10+
- PostgreSQL 14+
- Redis 6+

#### 启动
```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env 文件配置数据库等参数

# 3. 启动服务
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

#### API文档
启动服务后访问：
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

### Docker 部署

```bash
# 构建镜像
docker build -t webgis-ai-agent .

# 运行容器
docker run -p 8000:8000 --env-file .env webgis-ai-agent
```
