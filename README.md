# WebGIS AI Agent

智能地图分析与处理服务

## 项目结构

```
webgis-ai-agent/
├── app/                    # 应用代码
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

## 快速开始

### 本地开发

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env 文件配置数据库等参数

# 3. 启动服务
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Docker 部署

```bash
# 构建镜像
docker build -t webgis-ai-agent .

# 运行容器
docker run -p 8000:8000 --env-file .env webgis-ai-agent
```

## API 文档

启动服务后访问：
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## 技术栈

- **框架**: FastAPI
- **数据库**: PostgreSQL + SQLAlchemy
- **GIS**: GeoPandas, Shapely, Rasterio
- **任务队列**: Celery + Redis
- **部署**: Docker
