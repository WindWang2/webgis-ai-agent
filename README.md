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

## 环境配置

项目使用环境变量进行配置。支持两种数据库配置方式：

### 方式1：完整 DATABASE_URL（推荐）
```bash
# .env 文件中
DATABASE_URL=postgresql://username:password@hostname:5432/database_name
```

### 方式2：分解的环境变量
```bash
# .env 文件中，可分别设置
DB_HOST=localhost
DB_PORT=5432
DB_USER=postgres
DB_PASSWORD=your_secure_password  # ⚠️ 必须设置强密码!
DB_NAME=webgis
```

#### 数据库环境变量列表
| 变量名 | 默认值 | 说明 |
|---------|---------|------|
| `DATABASE_URL` | - | 完整连接 URL（最优先） |
| `DB_HOST` | localhost | 数据库主机 |
| `DB_PORT` | 5432 | 数据库端口 |
| `DB_USER` | postgres | 数据库用户名 |
| `DB_PASSWORD` | postgres | ⚠️ 数据库密码，生产环境请设强密码 |
| `DB_NAME` | webgis | 数据库名称 |

### Redis 配置
| 变量名 | 默认值 | 说明 |
|---------|---------|------|
| `REDIS_HOST` | localhost | Redis 主机 |
| `REDIS_PORT` | 6379 | Redis 端口 |

### 启用默认密码警告
由于安全原因，当检测到默认密码时会输出警告。若需要禁用（如正式环境测试后），可在 `.env` 中设置：
```bash
DISABLE_DB_PASSWORD_WAR=true
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
