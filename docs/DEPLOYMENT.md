# WebGIS AI Agent 部署与运行指南

从本地开发到 Kubernetes 的部署形态选型、各形态步骤、CI/CD 流水线、监控与运维手册；事实以仓库内 compose 清单、`Dockerfile*`、`deploy/` 与 `.github/workflows/production.yml` 为准。

> **版本**: v0.1.4 · **状态**: 活文档 · **最后更新**: 2026-11-06

### 0102 凭据迁移：必须维护切换

`0102_owner_token_hashes` 将匿名会话/任务的明文 token 转为单向校验值，
旧应用不能读取新格式，新应用也不能接受旧进程继续写入的明文。存在凭据时，
迁移默认拒绝，不能直接滚动升级。不要在长期服务环境中设置确认开关。

Compose 升级前停止所有外部 DB 写入者，再执行：

```bash
sh deploy/maintenance-upgrade.sh .env.Priv docker-compose.prod.secure.yml
# 标准生产栈使用 .env.prod / docker-compose.prod.yml；开发栈使用 .env / docker-compose.yml。
```

脚本停止本 Compose 项目的 API/Celery，确认旧容器停止，等待 DB/Redis 就绪，
以私有权限在 `backups/` 写入 `pg_dump -Fc` 备份；仅一次性迁移容器获得
`OWNER_TOKEN_MIGRATION_QUIESCED=1`，迁移成功后才启动服务。任一步失败即停止，
保留维护状态和备份。跨主机、独立调度器/worker 不在脚本控制范围内，必须先停止。
自动 SSH 生产升级也使用此脚本。首次空库没有凭据，无需确认即可迁移。

Kubernetes 跨越 0102 时，先保存并停用 API HPA（包括可能的 KEDA/外部扩缩容），
将 `webgis-api`、`webgis-celery` 及所有其他写入者缩至 0，等待旧 Pod 全部退出，
完成并验证数据库备份。使用新镜像及同一 Secret/ConfigMap 创建一次性迁移 Job，
仅该 Job 设置 `OWNER_TOKEN_MIGRATION_QUIESCED=1` 并执行
`/usr/local/bin/docker-entrypoint.sh true`。成功后更新全部应用镜像、恢复副本和
扩缩容配置。普通 initContainer 不携带确认开关，因此误用滚动升级会拒绝迁移。

本地 SQLite 启动会在写锁内执行完整 Alembic 链，而非仅 `create_all`。
已有版本库从记录的 revision 升级；含旧凭据的库同样须停止所有写入者、备份后，
在一次性命令中设置上述开关运行 `alembic upgrade head`。无版本号的存量库拒绝
自动收编：备份、核对实际 schema 与一个已知 revision 完全一致后，仅 stamp 该
已验证 revision，再升级；不能 stamp head 来跳过迁移。迁移失败会阻止 API 启动。

0102 降级无法恢复明文，会撤销匿名凭据。跨越该边界回滚旧镜像，必须再次停止
全部写入者，选择恢复切换前备份（会丢失备份后的写入），或显式降级并重新签发
匿名会话；仅切回旧镜像不兼容。0100 降级遇到跨组织重复 event_id、同 watch/event
多个 action、超过 64 字符的 session_id 时，会在该 revision 的 DDL 前拒绝，
保留数据，需先备份并由操作者明确处理不兼容行。

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
| 标准生产 | `docker-compose.prod.yml` | 11（+nginx / prometheus / 3×exporter / alertmanager / grafana） | `.env.prod`（模板 `.env.prod.example`） | 单机生产、需要 Grafana 面板 |
| 加固生产 | `docker-compose.prod.secure.yml` | 10 个常驻服务 + 1 个 TLS 初始化服务（无 grafana） | `.env.Priv`（模板 `.env.Priv.example`） | 公网服务器；**CI preview / deploy-prod / rollback 实际使用的栈** |
| Kubernetes | `deploy/k8s/`（kustomize） | —（api×2 + celery + ingress + HPA） | `webgis-secret` | 集群化、自动伸缩 |

加固栈与标准栈的关键差异：db/redis **不暴露任何端口**（仅 `webgis-internal` 内网可达）；api 仅发布 `127.0.0.1:${API_PORT:-8000}`；nginx 80/443 是唯一公网入口，nginx 配置以 compose `configs:` **内联分发**，自签证书由 `tls-init` 在私有命名卷内生成（真实证书用 override 文件只读挂载）；api/celery 优先使用 `image: ${WEBGIS_IMAGE}`（CI 写入 `ghcr.io/<repo>:<sha>`），无该变量时回落本地 `build:`。

## 前置要求

- Docker 与 Docker Compose（生产首选路径）。
- Python **3.12+**（`pyproject.toml` 锁定 `requires-python >= 3.12`；CI 与镜像均用 3.12）。
- Node.js ≥22.19.0（vendored Pi 的最低版本；CI 使用 22，镜像使用 26）。
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

说明：api 与 worker 必须挂同一数据卷（celery 按本地路径打开上传的 raster/shapefile，分卷即 "No such file"）。开发时运行 `docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d` 可把源码 bind-mount 进容器；`WEBGIS_DEV_MOUNT` 可覆盖源码目录（默认 `./app`）。普通启动不挂载源码。

`.env.example` 的 `DATABASE_URL=sqlite:///./data/webgis.db` 只给本机 pytest / `manage.py` 用。compose 的 api/celery **忽略**该 sqlite 值，把 `DATABASE_URL` 钉成 `postgresql://postgres:${DB_PASSWORD}@db:5432/webgis`（`app/core/config.py` 只读 `DATABASE_URL`，没有 `DB_HOST`）。本机进程要连 compose 里的 PostGIS 时，另设 `DATABASE_URL=postgresql://postgres:<DB_PASSWORD>@localhost:15432/webgis`。

## 形态二：本地手工开发流

按计算隔离原则，后端分三个必须组件（默认 `.env.example` 的 Redis 指向 `redis://localhost:16379/0`，即开发栈暴露的端口）：

```bash
# 0. 构建内嵌 Pi RPC runtime（ADR-0282；USE_NEW_AGENT=true 必需，幂等）
#    产物：vendor/pi/packages/coding-agent/dist/bundle/rpc-entry.js（另需锁定的 jiti）
git submodule update --init vendor/pi
bash scripts/build-vendor-pi.sh
python scripts/pi_doctor.py   # 分域诊断：vendor/build/extension/surface/rpc/env

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
pnpm install --frozen-lockfile
pnpm run dev
# 浏览器打开 http://localhost:3000
```

## 形态三：标准生产 docker-compose.prod.yml

```bash
cp .env.prod.example .env.prod   # 填入真实凭证（gitignored）
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d
```

11 个服务：db（127.0.0.1:15432）、redis（127.0.0.1:16379，**noeviction**）、api（127.0.0.1:18000→8000 + 127.0.0.1:13000→3000）、celery-worker、nginx（`${NGINX_PORT:-80}` / `${HTTPS_PORT:-443}`，bind-mount `deploy/nginx/nginx.conf` 与 `deploy/nginx/ssl/`——证书目录不入库，缺失 nginx 启动即 crash）、prometheus（127.0.0.1:19090）、postgres-exporter、redis-exporter、node-exporter、alertmanager（127.0.0.1:9093）、grafana（127.0.0.1:13001，admin 密码取 `GRAFANA_PWD`，provisioning 挂载 `deploy/grafana/provisioning`）。

api 与 worker 挂共享命名卷 `webgis_data:/app/data`；api 的 compose healthcheck 与镜像 HEALTHCHECK 一致为双探测（uvicorn `/api/v1/health/live` + 前端 :3000）。

### RAG embedding 模型缓存预置（#662）

生产镜像与两个 prod compose 显式设置 `RAG_EMBEDDING_OFFLINE=true`：RAG
embedding 模型（`paraphrase-multilingual-MiniLM-L12-v2`）只从本地 HF 缓存
加载，未缓存时首次使用**秒级失败**（RAG 检索走文档化降级，日志有明确指路
信息），而不是无超时的 HuggingFace 下载把 worker 线程挂死、连累进程优雅
关停。预置缓存的三个候选（按推荐排序）：

1. **预热 volume（推荐）**：一次性跑 `docker run --rm -v webgis_hf_cache:/root/.cache/huggingface <镜像> python -c "from sentence_transformers import SentenceTransformer as S; S('paraphrase-multilingual-MiniLM-L12-v2')"`，再把该卷以 `HF_HOME` 挂给 api 与 celery-worker（需要构建机能出网一次）。
2. **构建期下载**：在 `Dockerfile.prod` 的 backend-deps 阶段加一步下载（镜像增重 ~120MB，构建机需 HF 出网；换来零运行时依赖）。
3. **不预置**：接受首次 RAG 检索降级（空证据 + error 日志），事后按 1 补缓存。

本地开发不受影响：`RAG_EMBEDDING_OFFLINE` 默认 false，首用自动下载。

## 形态四：加固生产 docker-compose.prod.secure.yml

CI 的 deploy-prod / rollback / preview 走此栈；也可手动：

```bash
cp .env.Priv.example .env.Priv   # 填入 DB_PWD / REDIS_PASSWORD / JWT_SECRET_KEY / LLM_API_KEY / CORS_ORIGINS
# Alertmanager 需先渲染并挂载，见下方要点；仓库模板不能原样用于启动。
docker compose --env-file .env.Priv -f docker-compose.prod.secure.yml up -d
```

要点：

- **`--env-file .env.Priv` 必须显式传**：db/redis 依赖 `${DB_PWD:?...}` 等插值，compose 默认只读 `.env`。
- redis 经 `deploy/redis.conf` 启动（`maxmemory 256mb` + `noeviction`，理由见 [Redis 驱逐策略](#redis-驱逐策略)）；`requirepass` 不写配置文件，由 `deploy/redis-entrypoint.sh` 从 `REDIS_PASSWORD` 环境变量注入（三种部署路径行为一致）。**#1676（SEC-04）起注入方式是「`/dev/shm` tmpfs 0400 运行时 conf + config include」，口令不再进容器 argv** —— `docker inspect` 的 entrypoint、`docker top`、宿主机 `/proc/<pid>/cmdline` 均不可见（dev 栈 `docker-compose.yml` 与标准 prod 栈 `docker-compose.prod.yml` 同规则；k8s 可选 redis 用等价的 sh -c 写法）。redis-exporter 的抓取口令同样从 env 改为 `REDIS_PASSWORD_FILE`（config 文件挂载）。不变式由 `tests/test_deploy_secrets_hygiene.py` 钉住。这两个文件必须随 compose 一起 scp 到部署机（bind-mount）。
- prometheus 挂载 `deploy/prometheus.yml` 与 `deploy/alerts-rules.json`（同为 scp 清单成员；缺失会被 Docker 创建为空目录导致 crash-loop）。
- Alertmanager 不展开环境变量。手动部署先通过 `sh deploy/render-alertmanager-config.sh /tmp/alertmanager.yml` 渲染，再用 Compose override 将该结果只读挂到 alertmanager 的 `/etc/alertmanager/alertmanager.yml`；保留仓库模板占位符。通过环境变量 `ALERT_WEBHOOK_URL` 指定真实通知目标，未配置则使用 `.invalid`，不会收到通知。CI 部署会自动渲染及传输。
- `tls-init` 复用应用镜像中的 Python / cryptography，离线生成自签 TLS scaffold 到私有 `nginx_tls_secure` 卷；nginx 只读消费，初始化成功才启动。`deploy/generate_tls_scaffold.py` 以只读 config 挂载，须随 Compose 一起传输且保持 0644，不能依赖旧回滚镜像包含新增文件。证书按卷复用（730 天有效，不自动续签），仅供启动验证，浏览器仍会告警。正式上线请用 override 为 nginx 的 `/etc/nginx/ssl` 只读绑定运维证书目录（包含 `server.crt` / `server.key`）；初始化服务不触碰这些外部证书。
- PostGIS 自带的真实 extension-owned 表不视为业务 schema。无 `alembic_version` 的业务表仍默认拒绝自动收编；只有操作者确认 schema 与迁移 head 一致后，才可显式使用 `ADOPT_LEGACY_SCHEMA=true`。
- 健康验证：`curl -fk https://localhost/health`（经 nginx 到 API readiness）、`curl -fk https://localhost/api/v1/health/live`（API 进程存活）以及 `curl -f http://localhost:9093/-/ready`（Alertmanager）。预览会先渲染 Alertmanager 配置，强制使用 `.invalid` 通知目标，不向真实 webhook 发测试告警。

### Redis 驱逐策略

加固栈的 Redis 同时承担 broker / result backend / 会话缓存，`deploy/redis.conf` 固定 `maxmemory-policy noeviction`（与标准 prod compose 的 CLI 参数及 k8s 可选 Redis 一致）：broker/result 键无 TTL，任何 `allkeys-lru` 类策略都会在内存压力下静默丢任务。会话与工具缓存键自带 TTL，内存耗尽时写操作显式报错（`Redis_Memory_High` 告警此时 actionable），这是有意为之的响亮失败。开发栈 Redis 用 `allkeys-lru`（纯缓存用途）。

## 形态五：Kubernetes（deploy/k8s）

清单经 kustomize 组织（namespace `webgis-prod`）：`00-namespace` / `01-configmap` / `02-api-deployment` / `03-celery-deployment`（含 50Gi PVC） / `04-ingress`（TLS） / `06-hpa-pdb-rbac`（API ServiceAccount + PDB） / `07-hpa`。`05-deps-optional.yaml`（内部 postgres/redis）**默认不在资源列表中**，生产建议用外部托管服务。

**#1688（TLS 链路）**：`04b-clusterissuer.yaml`（cert-manager `ClusterIssuer letsencrypt-prod`，ACME HTTP-01）是 cluster-scoped 资源，**故意不在 `kustomization.yaml` 的 resources 里** —— 该文件有 `namespace: webgis-prod`，kustomize 的 namespace 转换器会给资源树里每个资源注入 `metadata.namespace`，apiserver 会以 "not allowed when resource is not namespaced" 拒绝整个 `kubectl apply -k .`。因此签发者需在装好 cert-manager CRD 后单独应用；`webgis-tls` 证书由 cert-manager 依据该签发者自动创建并轮换，**不要手写该 Secret**：

```bash
cd deploy/k8s
# 使用 CI build 输出的 manifest digest；脚本先验证仓库可读再更新清单
./pin-image.sh sha256:<ci-published-manifest-digest>
# #1688: Ingress 对外域名只改这一处（同步 CORS_ORIGINS），cert-manager 按它签证书
#   （部署前还要把 04b-clusterissuer.yaml 的 spec.acme.email 占位值换成运维邮箱）
sed -i 's|INGRESS_HOST: .*|INGRESS_HOST: "webgis.<真实域名>"|' 01-configmap.yaml
kubectl apply -f 04b-clusterissuer.yaml
kubectl apply -k .
```

默认清单使用不可解析的全零摘要；未配置镜像的 raw apply 不会运行浮动版本。`pin-image.sh` 只接受非零 SHA-256 manifest digest，拒绝 master、latest 和其他可变标签。清单内建行为：

- api `replicas: 2` + `sessionAffinity: ClientIP` + ingress nginx cookie（SSE turn-resume 缓冲是进程内的，重连需落回同一 pod）。
- initContainer 在 Pod Ready 前执行镜像内的 `docker-entrypoint.sh true`（k8s `command:` 覆盖镜像 ENTRYPOINT，所以必须显式调用同一脚本）。已有版本库执行 `alembic upgrade head`；无 `alembic_version` 的存量业务库默认拒绝，只有操作者验证 schema 与 head 等价并显式设置 `ADOPT_LEGACY_SCHEMA=true` 后才执行 stamp。跨越 0102 必须先按上文维护切换执行一次性迁移 Job。迁移、种子、API、worker 统一由镜像转换器钉到 CI 发布摘要。
- API 与前端仍共用容器；Python supervisor 监督两个独立进程组，任一退出都会停止另一服务并以失败状态退出，收到 SIGTERM 时转发并在 10 秒后强制结束未退出的子进程。
- api pod 注解 `prometheus.io/scrape: "true"`、`prometheus.io/path: "/metrics"`、`prometheus.io/port: "8000"`。
- 非 root（runAsUser 1001）、readOnlyRootFilesystem（可写面仅为挂载点）、专用 ServiceAccount、topologySpread 跨节点分散。
- HPA `minReplicas: 2 / maxReplicas: 10`。
- Celery 默认单副本且 PDB `minAvailable: 1`，明确优先保留在途任务，因此会阻止自愿驱逐。节点维护前先将 worker 扩为至少两个可用副本；若维护窗口允许任务中断，由运维显式临时调整 PDB。不要将被 PDB 阻止的 drain 当作应用死锁，也不要默认强制驱逐。
- api/celery 挂共享 RWX PVC 于 `/app/data`。
- #1688: 04-ingress 的 host 来自 `01-configmap.yaml` 的 `INGRESS_HOST`（kustomize build 时注入），证书由 cert-manager + `04b-clusterissuer.yaml` 签发进 `webgis-tls`；`proxy-body-size: 100m` 对齐上传限额。

### Secret 管理（审计 I4/I6）

`01-configmap.yaml` 不内嵌 Secret——明文凭证不进 git。部署前创建：

```bash
kubectl create secret generic webgis-secret --namespace=webgis-prod \
  --from-literal=DATABASE_URL='postgresql://USER:PWD@postgres:5432/DB' \
  --from-literal=JWT_SECRET_KEY="$(openssl rand -hex 32)" \
  --from-literal=REDIS_URL='redis://:PWD@redis:6379/0' \
  --from-literal=CELERY_BROKER_URL='redis://:PWD@redis:6379/0' \
  --from-literal=CELERY_RESULT_BACKEND='redis://:PWD@redis:6379/1' \
  --from-literal=METRICS_TOKEN="$(python3 -c 'import secrets;print(secrets.token_urlsafe(24))')"
# #1689：/metrics 的 METRICS_TOKEN Bearer 门禁 fail-closed（app/main.py
# _require_metrics_token，未配置即 401）；api pod 注解 prometheus.io/scrape
# 邀请集群内 Prometheus 抓取，secret 缺此键则全部 401、监控静默失明
# （compose 栈用 ${METRICS_TOKEN:?} 强制提供，k8s 必须在此补等价来源）。
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
| `Dockerfile` | 7 阶段：frontend-deps → frontend-builder → backend-deps → backend-builder → node-runtime → pi-builder → runner | dev/CI；开发栈 api 服务用 `target: backend-builder` |
| `Dockerfile.prod` | 6 阶段：frontend-deps → frontend-builder → node-runtime → pi-builder → backend-deps → runner | 生产镜像（CI build job 构建，`target: runner`） |

`pi-builder` 阶段（ADR-0282/#400 根因清偿）：在镜像内构建 vendored Pi 的自包含
RPC bundle（`dist/bundle/rpc-entry.js`，≈9.2MiB）及锁定的 `jiti@2.7.0` 扩展加载器并复制进
runner —— 此前镜像只带 vendor 源码而从未构建，`USE_NEW_AGENT=true` 时 bridge 因
找不到入口而**静默回退旧引擎**。`/api/v1/health` 的 `pi_vendor` 字段为 None 表示未构建或身份探测失败；
本地等价命令 `bash scripts/build-vendor-pi.sh` + `python
scripts/pi_doctor.py`。

`Dockerfile.prod` runner 关键行为：

- `tini --` 作 PID 1（信号收割），`ENTRYPOINT` 先执行 `deploy/docker-entrypoint.sh` 再 exec CMD。
- entrypoint 启动前跑 `alembic upgrade head`（幂等）；无版本号的存量业务库默认拒绝自动收编，验证 schema 与 head 等价后才能显式设置 `ADOPT_LEGACY_SCHEMA=true` 执行 stamp。`SKIP_DB_MIGRATIONS=true` 供 celery-worker 等非迁移所有者跳过迁移；`DB_MIGRATION_RETRIES` 默认 5 次、间隔 3s。迁移失败则容器退出；跨越 0102 使用上文维护切换流程。
- `HEALTHCHECK` 双探测：`/api/v1/health/live`（uvicorn:8000）+ `/`（node:3000）。
- CMD：`python -m app.lib.runtime.service_supervisor`，监督 uvicorn 与 Next.js 两个进程组；非 root 用户 `appuser`；`EXPOSE 3000 8000`。

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

关键凭证（`DB_PWD` / `DB_PASSWORD`、`REDIS_PASSWORD`、`JWT_SECRET_KEY`、`GRAFANA_PWD`、`METRICS_TOKEN`）在各 compose 中用 `${VAR:?...}` 强制校验，缺失即拒绝启动。CI 的 deploy-prod / rollback 用 `deploy/ci-generate-env-priv.sh` 从 GitHub secrets（含 `METRICS_TOKEN`）生成 `.env.Priv`（任一缺失写空行，`up` 时快速失败），并追加 `WEBGIS_IMAGE=ghcr.io/<repo>:<sha>` 供 `image: ${WEBGIS_IMAGE:-...}` 插值。

## CI/CD 流水线

`.github/workflows/production.yml`（name: `CI/CD Pipeline`）。触发：PR、push（master 与 `release/**`）、nightly（cron `0 2 * * *`）、手动 workflow_dispatch。镜像仓库 `ghcr.io/<repo>`（小写化），tag 为 commit sha。

### PR 门禁 → release-gate → build → deploy

12 项 release-blocking 检查聚合于 `release-gate` job（needs 全绿才进 build）：

| Job | 内容 |
|-----|------|
| `lint` | ruff（app/ tests/ scripts/ main.py manage.py）、mypy 白名单、债务/依赖边界/import 边界、skills lock、source registry + ESLint 9（`--max-warnings 0`） |
| `compose-config` | 渲染开发 Compose 配置及可选源码挂载配置 |
| `test-backend-gate` | SQLite / fakeredis / eager-Celery 上分片跑 pytest，合并覆盖率后执行 75% 阈值；真实服务由专属门禁验证 |
| `test-frontend` | Vitest + `tsc --noEmit` + `next build` |
| `security` | bandit `-r app/ -ll -ii`（MEDIUM+ 阻断） |
| `test-perf` | 性能回归 harness（无 coverage 干扰，warn 区间即失败） |
| `cartography-smoke` | 制图 / Harness 闭环确定性 smoke（release-blocking） |
| `deploy-config` | 一次性自签证书 + `nginx -t` 校验 nginx 语法（SSE location、keepalive map） |
| `db-migrations` | 真实 PostGIS 上 `alembic upgrade head` + 模型↔迁移漂移检查（`tests/test_deploy_migration_wiring.py`） |
| `real-services-smoke` | 真实 PostGIS/Redis/Celery worker 投递 smoke（broker db6 / result db7 隔离） |
| `contract-gate` | TEST-01：API 契约（OpenAPI 快照 / field contract / api-docs drift / schemathesis）—— 与 `contract.yml` 同命令 |
| `perf-budget-gate` | TEST-01：`scripts/perf/run_budget.py` 四条预算线（--self-test 自证 + 20 迭代）—— 与 `quality-e2e.yml` 同命令 |

另有 `dependency-audit`（pip-audit / npm audit）为 informational、非阻塞。

### nightly（02:00 UTC）

- `nightly-matrix`：全量 `pytest -m "cartography or perf"`（含并发与故障注入矩阵）。
- `runtime-validator`：Playwright + Chromium 的真实 MapLibre 渲染门（`REQUIRE_BROWSER=1` 硬失败；依赖外网，不进 PR 路径）。

### build → ghcr

`Dockerfile.prod` + submodules（vendor/pi）构建，`load: true` 后显式 `docker push`，并 `docker save` 为 artifact（供 preview / SSH 部署 / 回滚）。

### preview（Pull Request）

下载镜像 artifact → `docker load` → 复制 `.env.Priv.example` 为 `.env.Priv` 并注入随机强口令 + `WEBGIS_IMAGE=<sha>` → `docker compose --env-file .env.Priv -f docker-compose.prod.secure.yml up -d`（一次性 runner 上自签证书）→ 探测 `https://localhost/health` 与 `https://localhost/api/v1/health/live` → PR 评论结果。无公开访问地址（runner 的 localhost 不指向读者本机）。

### deploy-prod（master push）

前置：repo 配置 `SSH_HOST` var 与 `SSH_PRIVATE_KEY` / `DB_PWD` / `REDIS_PASSWORD` / `JWT_SECRET_KEY` / `LLM_API_KEY` / `CORS_ORIGINS` / `METRICS_TOKEN` secrets（告警投递另见 `ALERT_WEBHOOK_URL`，可选）。流程：`deploy/ci-generate-env-priv.sh` 生成 `.env.Priv` → scp compose 文件、维护脚本、`deploy/redis.conf`、`deploy/redis-entrypoint.sh`、`deploy/prometheus.yml`、`deploy/alerts-rules.json`、渲染后的 `deploy/alertmanager.yml`、`.env.Priv`、镜像 tar 到主机 → SSH `docker load` + `sh deploy/maintenance-upgrade.sh .env.Priv docker-compose.prod.secure.yml`（停止写入者、备份、一次性迁移、启动）→ `curl http://localhost:8000/api/v1/ready` 验证（503-aware，依赖未就绪即失败）→ Feishu webhook 通知（`FEISHU_WEBHOOK_URL` var 配置时）。未配置 `SSH_HOST` 时仅校验镜像在 registry 可达（`docker manifest inspect`），供手动 pull。

### rollback（手动 workflow_dispatch）

找上一个稳定 commit（`git log --merges --skip=1`，兜底 `HEAD^`）→ 优先 `docker pull` registry 中该 sha 的不可变镜像 tag（打为 `:rollback`；registry 无此 tag 才从 pinned 源码重建）→ 与 deploy-prod 相同的 SSH 分发 + `up -d`，`WEBGIS_IMAGE` 显式指向 `:rollback` → 健康验证。

## 监控

- **Prometheus**（prod 127.0.0.1:19090 / secure 127.0.0.1:9090）：`deploy/prometheus.yml` 抓取 `webgis-api`（`api:8000`，30s）、`postgres-exporter:9187`、`redis-exporter`、`node-exporter`；告警规则 `deploy/alerts-rules.json`（Database_Connection_Failure、Redis_Memory_High、Celery_Task_Backlog、Disk_Space_Low、High_Error_Rate、Slow_Response_P95/P99、High_API_CPU/Memory_Usage、Auth_JWT_Errors、WebGIS_API_Down）。
- **Alertmanager**（#1687；prod / secure 两栈均已部署，127.0.0.1:9093）：`prometheus.yml` 的 `alertmanagers` target `alertmanager:9093` 由本服务提供（修复前全仓库无该服务，主机名解析失败、16 条规则全部无投递通道）。receiver 配置 `deploy/alertmanager.yml`：webhook 投递，critical（WebGIS_API_Down / Database_Connection_Failure / SRE_Component_Down）单独通道 + 1h repeat，其余按 alertname+severity 分组、4h repeat，均 `send_resolved: true`。alertmanager 配置不做 env 展开 —— webhook URL 由部署流程把占位符 `__ALERT_WEBHOOK_URL__` 渲染为 `ALERT_WEBHOOK_URL`（repo secret 或 var，deploy-prod / rollback 的 `deploy/render-alertmanager-config.sh` 步骤）后随 scp 送达主机；**未配置时写 `.invalid` 占位（RFC 2606 永不解析），投递在 alertmanager UI / 日志响亮失败而非静默丢弃**（#1636 同款诚实降级），配置该 secret/var 后下次部署即生效。验证：`docker compose -f docker-compose.prod.secure.yml exec alertmanager wget -qO- http://localhost:9093/api/v2/status`。不变式由 `tests/test_alerts_metrics_consistency.py` 钉住：target 主机名必须命中已部署 compose 服务。
- **应用日志**（#1686）：`app/main.py` 在 import 期给 root logger 挂 stream handler（stderr）。修复前全仓库零处配置 logging —— `app.*` 的 INFO 记录被 `logging.lastResort`（WARNING 级）整体丢弃，生产排障只剩 uvicorn access log，`LOG_LEVEL` / `LOG_FORMAT` 全是死配置。现在：每条记录带关联字段 `[req=… sess=… turn=… run=… proj=…]`（`RuntimeCorrelationFilter`，未绑定为 `-`，可按 turn_id/session_id 直接 grep）；`LOG_LEVEL` 生效（默认 `INFO`，无法解析的级别名回落 INFO 而非启动失败；compose prod/secure、k8s ConfigMap 均已设 `INFO`）；`LOG_FORMAT=json` 切 `JsonFormatter`（结构化 JSON 行：关联字段显式 null、异常带 exc_info、extra 标量经拒绝词表脱敏），未设则人类可读文本 —— **是否在生产启用 json 由运维决定**（设一个环境变量即可，聚合场景推荐）；`LOG_DIR`（默认 `/tmp/webgis-logs`）只被 `get_logger()` 显式创建的文件 handler 使用，目录不可写（只读 rootfs）时自动降级为纯控制台，容器/k8s 以 stdout/stderr 流为 collect 主通道。k8s `readOnlyRootFilesystem: true` 下 import 不再触碰 `./logs`（原先 import 期 mkdir 直接 Errno 30 CrashLoop）。不变式由 `tests/unit/test_root_logging_installed.py` 钉住（含「uvicorn dictConfig 后 import app.main，app.* INFO 必须到达 stderr」的子进程端到端证明）。
- **exporters**：postgres-exporter（pg_up）、redis-exporter（`check-keys=celery` 导出默认队列长度）、node-exporter（宿主 /proc /sys 只读挂载）。
- **Grafana**（仅标准 prod 栈，127.0.0.1:13001）：`deploy/grafana/provisioning/dashboards/provider.yml`（file provider）使 `dashboard.json` 随启动自动加载；datasource provisioning 已含 Prometheus。
- **应用指标**：`/metrics`（无 /api/v1 前缀，prometheus-fastapi-instrumentator 暴露 `http_requests_total` / `http_request_duration_seconds` 等）；由 `METRICS_TOKEN` Bearer 门禁 fail-closed（audit ISSUE-067，未配置即 401）。prometheus 经 `configs:` 挂载的 `credentials_file`（`/etc/prometheus/secrets/metrics_token`）携带同一 token，两个生产栈（prod + secure）都必须提供 `METRICS_TOKEN`。
- prometheus 配置与告警文件由 CI 部署任务随 scp 一并传输。

## 运维手册

### 健康检查端点

| 端点 | 用途 |
|------|------|
| `GET /api/v1/health` | 基本信息（含版本号） |
| `GET /api/v1/health/live` | liveness：进程可响应即 200；Docker HEALTHCHECK / k8s livenessProbe 用 |
| `GET /api/v1/ready` | readiness：DB + Redis 就绪时 200 `{"ready": true}`，任一不可用时 503 `{"ready": false}`；LLM/Celery 不在该端点的阻断条件内（细节只在服务端日志） |

### 数据库迁移（Alembic）

部署路径默认自动迁移（镜像 entrypoint / k8s initContainer）。手动操作：

```bash
# 首次 / 常规升级（在容器内或本地 export DATABASE_URL 后）
alembic upgrade head

# 已存在 init_db()/create_all 建过 schema 的存量库（无 alembic_version）：
# 容器 entrypoint 默认**拒绝**自动收编（PLT-04：stamp head 会静默跳过全部
# 全部 revision，落后的库从此升级全是 no-op）。确认库 schema 与该 head
# 等价后，显式 opt-in：设置环境变量 ADOPT_LEGACY_SCHEMA=true（deploy 的
# compose/k8s env 或手动 export），entrypoint 才会 `alembic stamp head` 再
# upgrade。手动等价操作：
ADOPT_LEGACY_SCHEMA=true  # entrypoint 开关；手动路径自己确认后再执行
alembic stamp head
alembic upgrade head

# 生成新 revision（改完 app/models/ 后）
DATABASE_URL=sqlite:///tmp.db alembic revision --autogenerate -m "add foo column"
```

> 注意：`alembic stamp head` 只写版本号、不执行任何 DDL。仅当存量库确实
> 等价于当前 head（历史上由 create_all 跟进模型）时才可收编；不确定时应
> 先 `alembic upgrade` 到能对上的 revision 或人工补列。

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
python manage.py create-admin <username> <email>
# 输入隐藏的密码；自动化可使用 --password-stdin 从标准输入提供密码。
```

然后用 `POST /api/v1/auth/login` 获取 JWT。

### 排障雷达

- **前端白屏/图层不显示**：F12 查网络。若 `/api/v1/layers/data/{ref_id}?session_id=xxx` 报 404，大概率是 Redis 未启动、不可达，或该 ref 已被 LRU/TTL 逐出（重新执行分析生成新 ref）。
- **对话框没反应**：查 `celery-worker` 是否在跑——大模型把计算扔给后台后一直 Pending，通常是 worker 未启动或 broker 连接失败（看 worker 日志与 `/api/v1/ready`）。
- **secure 栈 prometheus crash-loop**：检查 `deploy/prometheus.yml` / `deploy/alerts-rules.json` 是否真的被 scp 到主机对应路径（缺失时 Docker 会把 bind-mount 源创建为空目录）。
- **nginx 启动失败（找不到证书）**：标准 prod 栈需把 `server.crt` / `server.key` 放到 `./deploy/nginx/ssl/`；加固栈先查 `docker compose --env-file .env.Priv -f docker-compose.prod.secure.yml logs tls-init`，检查辅助脚本传输、私有 TLS 卷及真实证书 override。初始化遇到不完整、符号链接或不匹配的证书对会拒绝覆盖；先由操作者检查并修复既有材料，不要自动删卷或覆盖私钥。


## Runtime administration

Production LLM configuration and Python skills are deployed consistently through
environment/secret updates and reviewed images. Process-local LLM changes and skill
upload/refresh endpoints return403 in production; a load-balanced admin request must
not update just one worker. Development uploads require
`ALLOW_RUNTIME_SKILL_UPLOAD=true`, an admin, a writable integrity lock and a new skill
filename. Python skill execution is trusted code, not a sandbox.

Kubernetes ingress isolation expects controller namespace `ingress-nginx` and metrics
namespace `monitoring`; customize selectors for clusters using other names. Egress
remains configurable for private geodata endpoints and is separately checked by the
application's deployment profile. Optional Redis disables server reconfiguration and
whole-database destructive commands; administration uses deployment/configuration.


Remote HTTP COG access uses Rasterio's public Python opener interface with the
connection-validated fsspec client, bounded HTTP timeouts and redirects disabled.
GeoTIFF content is accepted; remote referencing formats or compound archives must
be materialized locally first. This preserves range/window reads while preventing
native GDAL/libcurl from re-resolving an attacker-controlled hostname after the
Python preflight check. Dataset teardown closes the HTTP session. See the
[Rasterio opener documentation](https://rasterio.readthedocs.io/en/stable/topics/vsi.html#python-file-and-filesystem-openers).
