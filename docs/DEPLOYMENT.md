# WebGIS AI Agent 部署与运行指南

从本地开发到 Kubernetes 的部署形态选型、各形态步骤、CI/CD 流水线、监控与运维手册；事实以仓库内 compose 清单、`Dockerfile*`、`deploy/` 与 `.github/workflows/production.yml` 为准。

> **版本**: v0.1.3 · **状态**: 活文档 · **最后更新**: 2026-08-17

## 目录

- [部署形态选型](#部署形态选型)
- [前置要求](#前置要求)
- [形态一：开发栈 docker-compose.yml](#形态一开发栈-docker-composeyml)
- [形态二：本地手工开发流](#形态二本地手工开发流)
- [形态三：标准生产 docker-compose.prod.yml](#形态三标准生产-docker-composeprodyml)
- [形态四：加固生产 docker-compose.prod.secure.yml](#形态四加固生产-docker-composeprodsecureyml)
- [形态五：Kubernetes（deploy/k8s）](#形态五kubernetesdeployk8s)
- [镜像构建](#镜像构建)
- [环境变量与凭证管理](#环境变量与凭证管理)
- [CI/CD 流水线](#cicd-流水线)
- [监控](#监控)
- [运维手册](#运维手册)

## 部署形态选型

| 形态 | 入口文件 | 服务数 | 凭证文件 | 适用场景 |
|------|---------|-------|---------|---------|
| 开发栈 | `docker-compose.yml` | 4（db / redis / api / celery-worker） | `.env`（模板 `.env.example`） | 本地一键起全套；端口全部绑定 127.0.0.1 |
| 本地手工 | 无（进程直跑） | 3（redis + uvicorn + celery） | `.env` | 后端热重载开发调试 |
| 标准生产 | `docker-compose.prod.yml` | 10（+nginx / prometheus / 3×exporter / grafana） | `.env.prod`（模板 `.env.prod.example`） | 单机生产、需要 Grafana 面板 |
| 加固生产 | `docker-compose.prod.secure.yml` | 9（无 grafana） | `.env.Priv`（模板 `.env.Priv.example`） | 公网服务器；**CI preview / deploy-prod / rollback 实际使用的栈** |
| Kubernetes | `deploy/k8s/`（kustomize） | —（api×2 + celery + ingress + HPA） | `webgis-secret` | 集群化、自动伸缩 |

加固栈与标准栈的关键差异：db/redis **不暴露任何端口**（仅 `webgis-internal` 内网可达）；api 仅发布 `127.0.0.1:${API_PORT:-8000}`；nginx 80/443 是唯一公网入口，其配置与自签证书以 compose `configs:` **内联分发**（不依赖部署机上的文件，真实证书用 override 文件替换）；api/celery 优先使用 `image: ${WEBGIS_IMAGE}`（CI 写入 `ghcr.io/<repo>:<sha>`），无该变量时回落本地 `build:`。

## 前置要求

- Docker 与 Docker Compose（生产首选路径）。
- Python **3.12+**（`pyproject.toml` 锁定 `requires-python >= 3.12`；CI 与镜像均用 3.12）。
- Node.js 18+（CI/镜像固定 22）。
- PostgreSQL 14+ 需带 PostGIS 3+（镜像 `postgis/postgis:15-3.4`）。
- Redis 6+（镜像 `redis:7-alpine`）。

## 形态一：开发栈 docker-compose.yml

自动拉起 PostGIS、Redis 与隔离的 Celery worker：

```bash
# 复制配置模板并填入真实密钥（DB_PASSWORD / REDIS_PASSWORD 必填，
# compose 使用 ${VAR:?...} 语法，缺失时拒绝启动）
cp .env.example .env

# 拉起整套平台
docker-compose up -d --build

# 查看日志
docker-compose logs -f api
docker-compose logs -f celery-worker
```

端口与卷：

| 服务 | 宿主端口 | 卷 |
|------|---------|-----|
| db（postgis:15-3.4） | 127.0.0.1:15432 → 5432 | `pg_data` |
| redis（7-alpine，allkeys-lru，appendonly） | 127.0.0.1:16379 → 6379 | `redis_data` |
| api（backend-builder 阶段构建） | 127.0.0.1:18000 → 8000 | `uploads:/app/data`（与 worker 共享） |
| celery-worker | — | `uploads:/app/data` |

说明：api 与 worker 必须挂同一数据卷（celery 按本地路径打开上传的 raster/shapefile，分卷即 "No such file"）。可选设置 `WEBGIS_DEV_MOUNT=./app` 把源码 bind-mount 进容器实现热重载（默认关闭，避免覆盖镜像代码）。

`.env.example` 的 `DATABASE_URL=sqlite:///./data/webgis.db` 只给本机 pytest / `manage.py` 用。compose 的 api/celery **忽略**该 sqlite 值，把 `DATABASE_URL` 钉成 `postgresql://postgres:${DB_PASSWORD}@db:5432/webgis`（`app/core/config.py` 只读 `DATABASE_URL`，没有 `DB_HOST`）。本机进程要连 compose 里的 PostGIS 时，另设 `DATABASE_URL=postgresql://postgres:<DB_PASSWORD>@localhost:15432/webgis`。

## 形态二：本地手工开发流

按计算隔离原则，后端分三个必须组件（默认 `.env.example` 的 Redis 指向 `redis://localhost:16379/0`，即开发栈暴露的端口）：

```bash
# 1. 确保 Redis 可达（可复用开发栈的 redis 容器）

# 2. 启动 Celery Worker（不启动则空间工单挂起，前端等不到回调）
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
celery -A app.services.task_queue worker --loglevel=info

# 3. 启动 FastAPI
python -m uvicorn app.main:app --env-file .env --reload --host 0.0.0.0 --port 8000

# 4. 启动前端
cd frontend
npm install
npm run dev
# 浏览器打开 http://localhost:3000
```

## 形态三：标准生产 docker-compose.prod.yml

```bash
cp .env.prod.example .env.prod   # 填入真实凭证（gitignored）
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d
```

10 个服务：db（127.0.0.1:15432）、redis（127.0.0.1:16379，**noeviction**）、api（127.0.0.1:18000→8000 + 127.0.0.1:13000→3000）、celery-worker、nginx（`${NGINX_PORT:-80}` / `${HTTPS_PORT:-443}`，bind-mount `deploy/nginx/nginx.conf` 与 `deploy/nginx/ssl/`——证书目录不入库，缺失 nginx 启动即 crash）、prometheus（127.0.0.1:19090）、postgres-exporter、redis-exporter、node-exporter、grafana（127.0.0.1:13001，admin 密码取 `GRAFANA_PWD`，provisioning 挂载 `deploy/grafana/provisioning`）。

api 与 worker 挂共享命名卷 `webgis_data:/app/data`；api 的 compose healthcheck 与镜像 HEALTHCHECK 一致为双探测（uvicorn `/api/v1/health/live` + 前端 :3000）。

## 形态四：加固生产 docker-compose.prod.secure.yml

CI 的 deploy-prod / rollback / preview 走此栈；也可手动：

```bash
cp .env.Priv.example .env.Priv   # 填入 DB_PWD / REDIS_PASSWORD / JWT_SECRET_KEY / LLM_API_KEY / CORS_ORIGINS
docker compose --env-file .env.Priv -f docker-compose.prod.secure.yml up -d
```

要点：

- **`--env-file .env.Priv` 必须显式传**：db/redis 依赖 `${DB_PWD:?...}` 等插值，compose 默认只读 `.env`。
- redis 经 `deploy/redis.conf` 启动（`maxmemory 256mb` + `noeviction`，理由见 [Redis 驱逐策略](#redis-驱逐策略)）；`requirepass` 不写配置文件，由 `deploy/redis-entrypoint.sh` 从 `REDIS_PASSWORD` 环境变量注入（三种部署路径行为一致）。这两个文件必须随 compose 一起 scp 到部署机（bind-mount）。
- prometheus 挂载 `deploy/prometheus.yml` 与 `deploy/alerts-rules.json`（同为 scp 清单成员；缺失会被 Docker 创建为空目录导致 crash-loop）。
- nginx 的 TLS 证书是内联自签 scaffold（仅保证能以 TLS 启动，浏览器会告警）。换真实证书：复制本文件为 override，把 `webgis_ssl_scaffold_cert/key` 两个 config 换成 `file: ./deploy/nginx/ssl/server.crt` 形式的源，再 `-f <override>` 启动。
- 健康验证：`curl -k https://localhost/health`（nginx 自身）与 `curl -k https://localhost/api/v1/health/live`（经反代到 api）。

### Redis 驱逐策略

加固栈的 Redis 同时承担 broker / result backend / 会话缓存，`deploy/redis.conf` 固定 `maxmemory-policy noeviction`（与标准 prod compose 的 CLI 参数及 k8s 可选 Redis 一致）：broker/result 键无 TTL，任何 `allkeys-lru` 类策略都会在内存压力下静默丢任务。会话与工具缓存键自带 TTL，内存耗尽时写操作显式报错（`Redis_Memory_High` 告警此时 actionable），这是有意为之的响亮失败。开发栈 Redis 用 `allkeys-lru`（纯缓存用途）。

## 形态五：Kubernetes（deploy/k8s）

清单经 kustomize 组织（namespace `webgis-prod`）：`00-namespace` / `01-configmap` / `02-api-deployment` / `03-celery-deployment`（含 50Gi PVC） / `04-ingress`（TLS） / `06-hpa-pdb-rbac`（API ServiceAccount + PDB） / `07-hpa`。`05-deps-optional.yaml`（内部 postgres/redis）**默认不在资源列表中**，生产建议用外部托管服务。

```bash
cd deploy/k8s
# CI 只推 sha 标签到 ghcr.io/windwang2/webgis-ai-agent —— 部署必须显式钉 tag
kustomize edit set image ghcr.io/windwang2/webgis-ai-agent=ghcr.io/windwang2/webgis-ai-agent:<ci-pushed-sha>
kubectl apply -k .
```

裸 `kubectl apply -k` 不钉 tag 会拉取不存在的占位标签。清单内建行为：

- api `replicas: 2` + `sessionAffinity: ClientIP` + ingress nginx cookie（SSE turn-resume 缓冲是进程内的，重连需落回同一 pod）。
- initContainer 在 Pod Ready 前执行镜像内的 `docker-entrypoint.sh true`（k8s `command:` 覆盖镜像 ENTRYPOINT，所以必须显式调用同一脚本：对无 `alembic_version` 的存量 create_all 库先 `alembic stamp head`，再 `upgrade head`；幂等）。默认镜像 tag 是 CI 的 branch tag `master`；生产必须 `kustomize edit set image …:<ci-sha>`。
- api pod 注解 `prometheus.io/scrape: "true"`、`prometheus.io/path: "/metrics"`、`prometheus.io/port: "8000"`。
- 非 root（runAsUser 1001）、readOnlyRootFilesystem（可写面仅为挂载点）、专用 ServiceAccount、topologySpread 跨节点分散。
- HPA `minReplicas: 2 / maxReplicas: 10`。
- api/celery 挂共享 RWX PVC 于 `/app/data`。
- 04-ingress 需 `webgis-tls` secret 与真实域名；`proxy-body-size: 100m` 对齐上传限额。

### Secret 管理（审计 I4/I6）

`01-configmap.yaml` 不内嵌 Secret——明文凭证不进 git。部署前创建：

```bash
kubectl create secret generic webgis-secret --namespace=webgis-prod \
  --from-literal=DATABASE_URL='postgresql://USER:PWD@postgres:5432/DB' \
  --from-literal=JWT_SECRET_KEY="$(openssl rand -hex 32)" \
  --from-literal=REDIS_URL='redis://:PWD@redis:6379/0' \
  --from-literal=CELERY_BROKER_URL='redis://:PWD@redis:6379/0' \
  --from-literal=CELERY_RESULT_BACKEND='redis://:PWD@redis:6379/1'
# 启用 05-deps-optional.yaml（可选内部 postgres/redis）时，还需与 URL 内嵌
# 值一致的组件键：
#   --from-literal=DB_USER='USER' \
#   --from-literal=DB_PASSWORD='PWD' \
#   --from-literal=DB_NAME='DB' \
#   --from-literal=REDIS_PASSWORD='PWD'
```

更安全：SealedSecrets / External Secrets / Vault。

## 镜像构建

| 文件 | 阶段 | 用途 |
|------|-----|------|
| `Dockerfile` | 5 阶段：frontend-deps → frontend-builder → backend-deps → backend-builder → runner | dev/CI；开发栈 api 服务用 `target: backend-builder` |
| `Dockerfile.prod` | 4 阶段：frontend-deps → frontend-builder → backend-deps → runner | 生产镜像（CI build job 构建，`target: runner`） |

`Dockerfile.prod` runner 关键行为：

- `tini --` 作 PID 1（信号收割），`ENTRYPOINT` 先执行 `deploy/docker-entrypoint.sh` 再 exec CMD。
- entrypoint 启动前跑 `alembic upgrade head`（幂等；对 create_all 引导的存量库先 `alembic stamp head` 收编；`SKIP_DB_MIGRATIONS=true` 跳过——celery-worker 用，避免与 api 并发竞争 `alembic_version`；`DB_MIGRATION_RETRIES` 默认 5 次、间隔 3s）。迁移失败则容器退出，拒绝带旧 schema 起服务。
- `HEALTHCHECK` 双探测：`/api/v1/health/live`（uvicorn:8000）+ `/`（node:3000）。
- 双进程 CMD：`uvicorn app.main:app --port 8000` + `node frontend/.next/standalone/server.js -p 3000`；非 root 用户 `appuser`；`EXPOSE 3000 8000`。

## 环境变量与凭证管理

### 通用变量（`app/core/config.py`）

| 变量 | 说明 | 必须 |
|------|------|------|
| `DATABASE_URL` | 本机 pytest / `manage.py` 默认 `sqlite:///./data/webgis.db`；dev compose api/celery 钉到 PostGIS `db` 服务；生产必须 `postgresql://` | 是 |
| `REDIS_URL` | 数据枢纽（会话态 + Celery）；dev 默认 `redis://localhost:16379/0` | 是 |
| `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` | broker db0 / result db1 | 是 |
| `JWT_SECRET_KEY` | JWT 签名密钥（留空则每次启动随机生成，重启后失效） | 是 |
| `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` | LLM 密钥与模型（默认 provider 阶跃 Step Plan，模型 `step-3.7-flash`）；生产模式缺失即启动失败 | 是 |
| `CORS_ORIGINS` | JSON 数组格式（如 `["https://your-domain.com"]`），生产禁止 `["*"]` | 生产必须 |
| `USE_REDIS` | true 时会话态走 Redis 后端，否则进程内存 | 否 |
| `DATA_DIR` | 数据根目录（uploads、analysis_results、monitoring_reports、exports、会话数据）。compose 生产栈统一 `/app/data`（api 与 worker 共享卷 `webgis_data`）；k8s 同路径共享 RWX PVC；默认 `./data` 相对 WORKDIR，跨容器不可见 | 生产建议显式设置 |
| `ENV` / `DEBUG` | `ENV=production` 时关闭 `/docs`、`/redoc` 并收紧错误响应 | 生产必须 |

**从旧 `uploads` 卷迁移（compose）**：2026-08 前上传落在独立 `uploads` 卷挂 `/app/uploads`；现在全部产物统一在 `webgis_data:/app/data` 下。保留旧数据：

```bash
docker run --rm -v <old_uploads_vol>:/from -v webgis_data:/to alpine \
  sh -c 'mkdir -p /to/uploads && cp -a /from/. /to/uploads/'
```

### 凭证文件对应关系

| Compose 文件 | 凭证文件 | 说明 |
|--------------|----------|------|
| `docker-compose.yml` | `.env`（从 `.env.example` 复制） | 本地开发 |
| `docker-compose.prod.yml` | `.env.prod`（从 `.env.prod.example` 复制，gitignored；`--env-file .env.prod` 供插值） | 标准生产 |
| `docker-compose.prod.secure.yml` | `.env.Priv`（从 `.env.Priv.example` 复制，gitignored） | 加固生产 / CI |

关键凭证（`DB_PWD` / `DB_PASSWORD`、`REDIS_PASSWORD`、`JWT_SECRET_KEY`、`GRAFANA_PWD`）在各 compose 中用 `${VAR:?...}` 强制校验，缺失即拒绝启动。CI 的 deploy-prod / rollback 用 `deploy/ci-generate-env-priv.sh` 从 GitHub secrets 生成 `.env.Priv`（任一缺失写空行，`up` 时快速失败），并追加 `WEBGIS_IMAGE=ghcr.io/<repo>:<sha>` 供 `image: ${WEBGIS_IMAGE:-...}` 插值。

## CI/CD 流水线

`.github/workflows/production.yml`（name: `CI/CD Pipeline`）。触发：PR、push（master 与 `release/**`）、nightly（cron `0 2 * * *`）、手动 workflow_dispatch。镜像仓库 `ghcr.io/<repo>`（小写化），tag 为 commit sha。

### PR 门禁 → release-gate → build → deploy

9 项 release-blocking 检查聚合于 `release-gate` job（needs 全绿才进 build）：

| Job | 内容 |
|-----|------|
| `lint` | ruff（app/ tests/ main.py manage.py）+ ESLint 9（`--max-warnings 0`） |
| `test-backend` | PostGIS + Redis service 容器上跑 pytest，`--cov-fail-under=75` |
| `test-frontend` | Vitest + `tsc --noEmit` + `next build` |
| `security` | bandit `-r app/ -ll -ii`（MEDIUM+ 阻断） |
| `test-perf` | 性能回归 harness（无 coverage 干扰，warn 区间即失败） |
| `cartography-smoke` | 制图 / Harness 闭环确定性 smoke（release-blocking） |
| `deploy-config` | 一次性自签证书 + `nginx -t` 校验 nginx 语法（SSE location、keepalive map） |
| `db-migrations` | 真实 PostGIS 上 `alembic upgrade head` + 模型↔迁移漂移检查（`tests/test_deploy_migration_wiring.py`） |
| `real-services-smoke` | 真实 PostGIS/Redis/Celery worker 投递 smoke（broker db6 / result db7 隔离） |

另有 `dependency-audit`（pip-audit / npm audit）为 informational、非阻塞。

### nightly（02:00 UTC）

- `nightly-matrix`：全量 `pytest -m "cartography or perf"`（含并发与故障注入矩阵）。
- `runtime-validator`：Playwright + Chromium 的真实 MapLibre 渲染门（`REQUIRE_BROWSER=1` 硬失败；依赖外网，不进 PR 路径）。

### build → ghcr

`Dockerfile.prod` + submodules（vendor/pi）构建，`load: true` 后显式 `docker push`，并 `docker save` 为 artifact（供 preview / SSH 部署 / 回滚）。

### preview（Pull Request）

下载镜像 artifact → `docker load` → 复制 `.env.Priv.example` 为 `.env.Priv` 并注入随机强口令 + `WEBGIS_IMAGE=<sha>` → `docker compose --env-file .env.Priv -f docker-compose.prod.secure.yml up -d`（一次性 runner 上自签证书）→ 探测 `https://localhost/health` 与 `https://localhost/api/v1/health/live` → PR 评论结果。无公开访问地址（runner 的 localhost 不指向读者本机）。

### deploy-prod（master push）

前置：repo 配置 `SSH_HOST` var 与 `SSH_PRIVATE_KEY` / `DB_PWD` / `REDIS_PASSWORD` / `JWT_SECRET_KEY` / `LLM_API_KEY` / `CORS_ORIGINS` secrets。流程：`deploy/ci-generate-env-priv.sh` 生成 `.env.Priv` → scp compose 文件、`deploy/redis.conf`、`deploy/redis-entrypoint.sh`、`deploy/prometheus.yml`、`deploy/alerts-rules.json`、`.env.Priv`、镜像 tar 到主机 → SSH `docker load` + `compose up -d` → `curl http://localhost:8000/api/v1/health/live` 验证 → Feishu webhook 通知（`FEISHU_WEBHOOK_URL` var 配置时）。未配置 `SSH_HOST` 时仅校验镜像在 registry 可达（`docker manifest inspect`），供手动 pull。

### rollback（手动 workflow_dispatch）

找上一个稳定 commit（`git log --merges --skip=1`，兜底 `HEAD^`）→ 优先 `docker pull` registry 中该 sha 的不可变镜像 tag（打为 `:rollback`；registry 无此 tag 才从 pinned 源码重建）→ 与 deploy-prod 相同的 SSH 分发 + `up -d`，`WEBGIS_IMAGE` 显式指向 `:rollback` → 健康验证。

## 监控

- **Prometheus**（prod 127.0.0.1:19090 / secure 127.0.0.1:9090）：`deploy/prometheus.yml` 抓取 `webgis-api`（`api:8000`，30s）、`postgres-exporter:9187`、`redis-exporter`、`node-exporter`；告警规则 `deploy/alerts-rules.json`（Database_Connection_Failure、Redis_Memory_High、Celery_Task_Backlog、Disk_Space_Low、High_Error_Rate、Slow_Response_P95/P99、High_API_CPU/Memory_Usage、Auth_JWT_Errors、WebGIS_API_Down）。
- **exporters**：postgres-exporter（pg_up）、redis-exporter（`check-keys=celery` 导出默认队列长度）、node-exporter（宿主 /proc /sys 只读挂载）。
- **Grafana**（仅标准 prod 栈，127.0.0.1:13001）：`deploy/grafana/provisioning/dashboards/provider.yml`（file provider）使 `dashboard.json` 随启动自动加载；datasource provisioning 已含 Prometheus。
- **应用指标**：`/metrics`（无 /api/v1 前缀，prometheus-fastapi-instrumentator 暴露 `http_requests_total` / `http_request_duration_seconds` 等）；需网络层隔离（NetworkPolicy / IP 白名单 / 同 namespace ClusterIP），端点本身无鉴权。
- secure 栈的 prometheus 配置与告警文件由 CI 部署任务随 scp 一并传输。

## 运维手册

### 健康检查端点

| 端点 | 用途 |
|------|------|
| `GET /api/v1/health` | 基本信息（含版本号） |
| `GET /api/v1/health/live` | liveness：进程可响应即 200；Docker HEALTHCHECK / k8s livenessProbe 用 |
| `GET /api/v1/ready` | readiness：DB + LLM + Redis + Celery 全通 200 `{"ready": true}`，任一挂 503 `{"ready": false}`（细节只在服务端日志） |

### 数据库迁移（Alembic）

部署路径默认自动迁移（镜像 entrypoint / k8s initContainer）。手动操作：

```bash
# 首次 / 常规升级（在容器内或本地 export DATABASE_URL 后）
alembic upgrade head

# 已存在 init_db() 建过 schema 的存量库：先收编再升级
alembic stamp head
alembic upgrade head

# 生成新 revision（改完 app/models/ 后）
DATABASE_URL=sqlite:///tmp.db alembic revision --autogenerate -m "add foo column"
```

迁移链与漂移守护见 `docs/database-design.md`；CI 的 `db-migrations` job 在每次 PR 上对真实 PostGIS 验证链路可执行且与模型一致。

### 备份要点

- 数据库：备份 compose 卷 `pg_data` / `pg_prod_data` / `pg_data_secure`（或 `pg_dump`，PostGIS 库需包含扩展与空间索引）。
- 文件产物：`webgis_data` 卷（uploads / analysis_results / monitoring_reports / exports）与 k8s 的 `/app/data` PVC（50Gi）。
- Redis：AOF 开启（`appendonly yes`）；broker/result 键随任务消费，无需长期备份，但**不可用 allkeys-lru 淘汰**（见上文驱逐策略）。

### 回滚

- CI 路径：Actions 页手动触发 `Rollback Deployment`（workflow_dispatch），自动找上一个稳定 commit 并以 registry 不可变镜像回滚（见上文 rollback 流程）。
- 手动路径：`docker compose --env-file .env.Priv -f docker-compose.prod.secure.yml up -d` 前把 `.env.Priv` 的 `WEBGIS_IMAGE` 改回上一版本 tag；数据库迁移通常向前兼容（additive），回滚镜像+新 schema 组合需人工确认。

### 初始 admin 账号

公开注册默认关闭（审计 S28）。运维通过 CLI 创建：

```bash
python manage.py create-admin <username> <email> <password>
```

然后用 `POST /api/v1/auth/login` 获取 JWT。

### 排障雷达

- **前端白屏/图层不显示**：F12 查网络。若 `/api/v1/layers/data/{ref_id}?session_id=xxx` 报 404，大概率是 Redis 未启动、不可达，或该 ref 已被 LRU/TTL 逐出（重新执行分析生成新 ref）。
- **对话框没反应**：查 `celery-worker` 是否在跑——大模型把计算扔给后台后一直 Pending，通常是 worker 未启动或 broker 连接失败（看 worker 日志与 `/api/v1/ready`）。
- **secure 栈 prometheus crash-loop**：检查 `deploy/prometheus.yml` / `deploy/alerts-rules.json` 是否真的被 scp 到主机对应路径（缺失时 Docker 会把 bind-mount 源创建为空目录）。
- **nginx 启动即 crash（找不到证书）**：标准 prod 栈需把 `server.crt` / `server.key` 放到 `./deploy/nginx/ssl/`；加固栈检查内联 configs 或 override 文件。
