# WebGIS AI Agent - V2.0 部署与运行指南

## 前置环境要求 (Prerequisites)
- Docker & Docker Compose (生产首选)
- Python 3.10+
- Node.js 18+
- Redis 6+ (V2.0 强制依赖)
- PostgreSQL 14+ (携带 PostGIS 3+)

## 🚀 方式一：Docker 一键挂载 (推荐)

此方式能自动拉起底层 Redis 以及隔离的 Celery 计算兵团。

```bash
# 复制配置模板并填入您的真实私钥
cp .env.example .env

# 拉起包含计算集群的整套 WebGIS 平台
docker-compose up -d --build

# 查看网关或异步 Worker 的日志
docker-compose logs -f api
docker-compose logs -f worker
```

## 💻 方式二：极客手工独立启动 (开发流)

由于 V2.0 实施了严格的**计算隔离与 Fetch-on-Demand** 原则，后端启动分为三个必须组件：

### 1. 启动基建与 Redis
确保本地 `localhost:6379` 可访问。这对于大尺寸 GeoJSON 缓存极为重要。

### 2. 启动重量级计算剥离列队 (Celery Worker)
如果不启动此服务，诸如道路网络切割等空间工单将被挂起，前端等不到回调信令。
```bash
# 进入后端环境
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 启动 Worker（注意必须配置正确的 REDIS_URL）
celery -A main.celery_app worker --loglevel=info &
```

### 3. 启动大模型流式总网关 (FastAPI)
```bash
# 开启非阻塞主 API
python -m uvicorn main:app --reload --host 0.0.0.0 --port 8001
```

### 4. 启动 GPU 绘图前端台 (Next.js)
```bash
cd frontend
npm install
npm run dev

# 浏览器开启 http://localhost:3000
```

## 核心环境变量解析 (.env)

| 变量键值 | 功能解释 | 必须 |
|----------|-------------|----------|
| `DATABASE_URL` | PostGIS 或 SQLite 地址 | 是 |
| `REDIS_URL` | V2.0 数据枢纽 (Celery与缓存流使用) | 是 |
| `CLAUDE_API_KEY` | 必须支持 Tool Use (工具装载) 的大模型密钥 | 是 |
| `MAPLIBRE_STYLE` | 矢量栅格叠加套壳风格 | 否 |

## 排障雷达 (Troubleshooting)
- **前端白屏/不显示建筑物**：按下 F12 查看网络。如果 `/api/v1/layer/xxxx/data` 报 404，极大概率是您的 Redis 没有启动或容积超标。
- **对话框没反应**：去终端看看是不是 `celery worker` 压根没开，大模型把计算扔给后台后一直处于 Pending 等待中。