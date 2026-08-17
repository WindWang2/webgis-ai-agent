# WebGIS AI Agent - V3.2 部署与运行指南

## 前置环境要求 (Prerequisites)
- Docker & Docker Compose (生产首选)
- Python 3.12+ (`pyproject.toml` 锁定 `requires-python >= 3.12`，CI / Docker 均使用 3.12)
- Node.js 18+
- Redis 6+ (V3.2 强制依赖)
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
celery -A app.services.task_queue worker --loglevel=info &
```

### 3. 启动大模型流式总网关 (FastAPI)
```bash
# 开启非阻塞主 API
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
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
| `REDIS_URL` | 数据枢纽 (Celery 与缓存流使用) | 是 |
| `JWT_SECRET_KEY` | JWT 签名密钥（留空则自动生成，重启后失效） | 是 |
| `LLM_API_KEY` | 支持工具调用的 LLM 密钥 | 是 |
| `LLM_MODEL` | 默认模型（如 deepseek-v4-flash） | 否 |
| `DATA_DIR` | 数据根目录（上传/uploads、analysis_results、monitoring_reports、exports、会话数据）。compose 生产栈统一设 `/app/data`（api 与 celery-worker 挂同一共享命名卷 `webgis_data`），k8s 设 `/app/data`（共享 RWX PVC）；默认 `./data` 相对 WORKDIR 展开，跨容器不可见且只读 rootfs 崩溃（见 #519） | 生产建议显式设置 |

> **从旧 `uploads` 卷迁移（compose）**：2026-08 前 `uploads` 独立卷挂 `/app/uploads`。
> 升级后上传与全部产物统一在 `webgis_data:/app/data` 下。保留旧数据请在升级前执行：
> `docker run --rm -v <old_uploads_vol>:/from -v webgis_data:/to alpine sh -c 'mkdir -p /to/uploads && cp -a /from/. /to/uploads/'`

## ☸️ K8s 部署 (deploy/k8s)

清单经 kustomize 组织（`deploy/k8s/kustomization.yaml`）。镜像坐标已对齐 CI
（`ghcr.io/windwang2/webgis-ai-agent`，CI 只推 sha 标签），**部署时必须显式钉 tag**：

```bash
cd deploy/k8s
kustomize edit set image ghcr.io/windwang2/webgis-ai-agent=ghcr.io/windwang2/webgis-ai-agent:<ci-pushed-sha>
kubectl apply -k .
```

裸 `kubectl apply -k` 不指定 tag 会拉取不存在的标签（清单中的占位 tag 仅作锚点）。
api/celery 均挂共享 RWX PVC 于 `/app/data`（`readOnlyRootFilesystem` 保持 true，
可写面仅为挂载点）。可选内部 postgres/redis（`05-deps-optional.yaml`）默认不启用，
启用前按上文 Secret Management 一节补齐组件键。

### Redis 驱逐策略

secure 栈的 Redis 同时承担 broker / result backend / 会话缓存，`deploy/redis.conf`
固定 `maxmemory-policy noeviction`（与标准 prod compose CLI 及 k8s 可选 Redis 一致）：
broker/result 键无 TTL，任何 `allkeys-lru` 类策略都会在内存压力下静默丢任务。
会话与工具缓存键自带 TTL，内存耗尽时写操作会显式报错（`Redis_Memory_High` 告警
此时 actionable），这是有意为之的响亮失败。

### Grafana 可观测性

`deploy/grafana/provisioning/dashboards/provider.yml`（file provider）使
`dashboard.json` 随容器启动自动加载；datasource provisioning 已含 Prometheus。
secure 栈 Prometheus 所需的 `deploy/prometheus.yml` 与 `deploy/alerts-rules.json`
由 CI 部署任务随 scp 一并传输（#530，缺失曾导致空目录 crash-loop）。

## 排障雷达 (Troubleshooting)
- **前端白屏/不显示建筑物**：按下 F12 查看网络。如果 `/api/v1/layers/data/{ref_id}?session_id=xxx` 报 404，极大概率是您的 Redis 没有启动或容积超标。
- **对话框没反应**：去终端看看是不是 `celery worker` 压根没开，大模型把计算扔给后台后一直处于 Pending 等待中。

## 🔐 Secret Management (审计 I4 + I6)

`deploy/k8s/01-configmap.yaml` **不再** 内嵌 Secret 资源 —— 明文凭证不应
进 git。部署前必须用以下方式创建：

### K8s

```bash
kubectl create secret generic webgis-secret --namespace=webgis-prod \
  --from-literal=DATABASE_URL='postgresql://USER:PWD@postgres:5432/DB' \
  --from-literal=JWT_SECRET_KEY="$(openssl rand -hex 32)" \
  --from-literal=REDIS_URL='redis://:PWD@redis:6379/0' \
  --from-literal=CELERY_BROKER_URL='redis://:PWD@redis:6379/0' \
  --from-literal=CELERY_RESULT_BACKEND='redis://:PWD@redis:6379/1'
# #561: 启用 deploy/k8s/05-deps-optional.yaml（可选内部 postgres/redis）时，
# 还需组件键（值必须与上面 URL 内嵌的 user/password/db 一致）：
#   --from-literal=DB_USER='USER' \
#   --from-literal=DB_PASSWORD='PWD' \
#   --from-literal=DB_NAME='DB' \
#   --from-literal=REDIS_PASSWORD='PWD'
```

更安全：SealedSecrets / External Secrets / Vault。

### Docker Compose

凭证文件因 compose 变体而异（审计 D2 澄清）：

| Compose 文件 | 凭证文件 | 用途 |
|--------------|----------|------|
| `docker-compose.yml` | `.env`（从 `.env.example` 复制） | 本地开发 |
| `docker-compose.prod.yml` | `.env.prod`（从 `.env.prod.example` 复制，gitignored；`--env-file .env.prod` 供 `${VAR}` 插值） | 标准生产 |
| `docker-compose.prod.secure.yml` | `.env.Priv`（从 `.env.Priv.example` 复制，gitignored） | 加固生产（推荐） |

所有变体的 `${VAR:?...}` 语法会在关键凭证缺失时强制 fail，拒绝用弱默认值启动。

### 初始 admin 账号

公开注册默认关闭（审计 S28）。运维通过 CLI 创建初始 admin：

```bash
python manage.py create-admin <username> <email> <password>
```

然后用 `POST /api/v1/auth/login` 拿 JWT。

## 🗃️ Database Migration (审计 I6)

Alembic 现在可用 —— `migrations/env.py` 从 `DATABASE_URL` 读连接，
`migrations/versions/` 含 initial schema。

### 首次部署

```bash
# 在已运行的容器里跑（或本地 export DATABASE_URL=... 后跑）
alembic upgrade head
```

### 已存在 init_db() 建过 schema 的环境

`init_db()` 走 `Base.metadata.create_all`（只创建缺失的表），alembic
不知道这些表已存在。需要先 "stamp" 当前状态再 upgrade：

```bash
# 1. 把当前 schema 标记为 head（不执行任何 SQL）
alembic stamp head
# 2. 之后任何新 revision 都能正常 upgrade
```

### 生成新 revision

```bash
# 改完 app/models/db_model.py 后：
DATABASE_URL=sqlite:///tmp.db alembic revision --autogenerate -m "add foo column"
# 复查生成的 .py，然后 commit
```