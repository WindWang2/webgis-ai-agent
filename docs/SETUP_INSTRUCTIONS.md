# 本地开发手册

面向贡献者的 WebGIS AI Agent 本地环境搭建与启动指南，覆盖配置、三种启动路径、命令参考与常见问题排查。

> **版本**: v0.1.3 · **状态**: 活文档 · **最后更新**: 2026-08-17

## 1. 前置要求

| 依赖 | 版本要求 | 说明 |
|------|----------|------|
| Python | >= 3.12 | 见 `pyproject.toml` 的 `requires-python` |
| Node.js | 22 | 与 CI/Dockerfile 一致（`node:22-alpine`，ESLint 9 等依赖要求 node >= 22） |
| Redis | 任意稳定版（容器用 redis:7-alpine） | Celery broker 与会话数据存储的硬依赖，`manage.py dev` 启动前强制检查 |
| Git | 任意 | 拉取代码 |
| Docker + Compose | 可选 | 仅在使用容器路径（路径 B/C）时需要 |

依赖管理：后端用 pip + `requirements.txt`（开发另有 `requirements-dev.txt`），前端用 npm（`frontend/package.json`）。

## 2. 获取代码与安装依赖

```bash
git clone https://github.com/WindWang2/webgis-ai-agent.git
cd webgis-ai-agent

# 后端依赖（建议在虚拟环境中）
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install -r requirements-dev.txt   # 测试/开发工具，可选

# 前端依赖
cd frontend && npm install && cd ..
```

## 3. 配置 .env

```bash
cp .env.example .env
```

以 `.env.example` 为准的核心变量：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DEBUG` | 代码默认 `false` | 本地开发请在 `.env` 显式设 `true`；缺省 `false` 避免泄漏堆栈/凭证 |
| `ENV` | `development` | 设为 `production` 会启用一组生产校验（见第 9 节） |
| `JWT_SECRET_KEY` | 空 | 留空时开发模式每次启动自动生成随机密钥并告警（重启后会话失效）；**生产必填**，缺失直接拒绝启动 |
| `DATABASE_URL` | `sqlite:///./data/webgis.db` | 本机 pytest / `manage.py` 默认 SQLite。dev compose 的 api/celery **不读**这个值，改用 PostGIS `db` 服务。生产强制 PostgreSQL/PostGIS（见第 7 节） |
| `LLM_BASE_URL` | `https://api.stepfun.com/step_plan/v1` | OpenAI 兼容接口，项目默认为阶跃 Step Plan |
| `LLM_API_KEY` | 占位符 | 生产模式校验会拒绝占位符值（`your-api-key-here`）；开发模式仅告警 |
| `LLM_MODEL` | `step-3.7-flash` | 默认推理模型（思维链在 `reasoning_content`，正文在 `content`） |
| `LLM_PLANNER_MODEL` | 空 | 规划阶段专用模型，留空回退 `LLM_MODEL` |
| `REDIS_URL` | `redis://localhost:16379/0` | 与 dev compose 的 redis 端口映射（16379→6379）对齐 |
| `CELERY_BROKER_URL` | `redis://localhost:16379/0` | Celery broker |
| `CELERY_RESULT_BACKEND` | `redis://localhost:16379/1` | Celery 结果后端（db1） |
| `HTTP_PROXY` / `HTTPS_PROXY` | 空 | 地理编码/OSM 请求走代理时配置 |
| `WEBGIS_DEV_MOUNT` | 空 | 设为 `./app` 时 dev compose 把源码 bind-mount 进 api/celery 容器实现热重载（默认不挂载） |

数据源凭证（按需填写，均为可选）：`TIANDITU_TOKEN`、`AMAP_API_KEY`、`AMAP_JS_KEY`、`AMAP_JS_SECURITY_KEY`、`BAIDU_MAP_AK`、`BAIDU_QIANFAN_TOKEN`（网络搜索）、`SENTINELHUB_CLIENT_ID`/`SENTINELHUB_CLIENT_SECRET`、`NASA_EARTHDATA_USERNAME`/`NASA_EARTHDATA_PASSWORD`、`OPENTOPOGRAPHY_API_KEY`。

容器路径额外必填（`docker-compose.yml` 用 `${VAR:?}` 强制）：

- `REDIS_PASSWORD`：dev compose 的 redis 以 `--requirepass` 启动，必须设置。此时本机直连的 `REDIS_URL` 需带密码：`redis://:<REDIS_PASSWORD>@localhost:16379/0`。
- `DB_PASSWORD`：PostgreSQL 容器密码。compose api/celery 用它拼 `DATABASE_URL=postgresql://postgres:${DB_PASSWORD}@db:5432/webgis`（应用不读 `DB_HOST`）。

## 4. 前端配置 frontend/.env.local

前端通过 `NEXT_PUBLIC_API_URL` 指向后端实际地址（`frontend/lib/api/config.ts`，未设置时回退 `http://localhost:8001`）。后端本地端口是 **18000**（`manage.py dev` / `manage.py server`），因此：

```bash
# frontend/.env.local
NEXT_PUBLIC_API_URL=http://localhost:18000
NEXT_PUBLIC_WS_URL=ws://localhost:18000
# 浏览器侧底图/地理编码 key（可选）
NEXT_PUBLIC_TIANDITU_TOKEN=
NEXT_PUBLIC_AMAP_JS_KEY=
NEXT_PUBLIC_AMAP_JS_SECURITY_KEY=
```

## 5. 三种启动路径

启动前建议先做基础设施诊断（Database / Redis / LLM API / Celery ping 四项，rich 表格输出）：

```bash
python manage.py check
```

### 路径 A：全本地（manage.py dev）

需要本机已运行 Redis（监听 `REDIS_URL`）。一条命令拉起后端（uvicorn `:18000` 热重载）+ Celery worker + 前端（next dev `:3000`）：

```bash
python manage.py dev
```

启动前强制 ping Redis，不可达则直接退出并提示先启动 Redis。Ctrl+C 一并停止全部子进程。

### 路径 B：dev compose 只起 db + redis，其余本地

用容器提供 PostGIS 与 Redis（最常用的混合方式）：

```bash
# .env 中需先设置 REDIS_PASSWORD 与 DB_PASSWORD
docker compose up -d db redis

# 之后照常本地启动（REDIS_URL 带密码，见第 3 节）
python manage.py dev
```

dev compose 的 db 为 `postgis/postgis:15-3.4`，端口映射 `127.0.0.1:15432→5432`；redis 为 `redis:7-alpine`，`127.0.0.1:16379→6379`。

### 路径 C：全 compose（后端也进容器）

dev compose 共 4 个服务：`db`、`redis`、`api`（target `backend-builder`，`127.0.0.1:18000→8000`，uvicorn）、`celery-worker`。**不含前端服务**——前端始终本地运行：

```bash
docker compose up -d --build
# api 就绪后（healthcheck 通过）再启动前端
cd frontend && npm run dev
```

容器内源码热重载：在 `.env` 设 `WEBGIS_DEV_MOUNT=./app` 后重建 api/celery 服务。

Windows 用户另有 `start_all.bat` 一键脚本（后端 `:8001` + 前端 `:3000`；此时 `NEXT_PUBLIC_API_URL` 应指向 8001）。

## 6. manage.py 命令参考

`python manage.py`（argparse，共 6 个子命令）：

| 命令 | 作用 |
|------|------|
| `python manage.py init-db` | 初始化数据库（创建所有表），幂等 |
| `python manage.py create-admin <user> <email> <password>` | 公开注册关闭后创建 admin 账号（密码 >= 8 位，scrypt 哈希；成功后提示用 `POST /api/v1/auth/login` 获取 JWT） |
| `python manage.py check` | 基础设施诊断：Database / Redis / LLM API 连通性 / Celery worker ping，rich 表格输出 |
| `python manage.py dev` | 一键拉起后端 uvicorn `:18000`（`--reload`）+ Celery worker + 前端 `npm run dev`；启动前强制检查 Redis |
| `python manage.py server` | 仅后端：`uvicorn app.main:app --host 0.0.0.0 --port 18000 --reload` |
| `python manage.py worker` | 仅 Celery worker：`celery -A app.services.task_queue.celery_app worker --loglevel=info` |

对应的手动等价命令（不想用 manage.py 时）：

```bash
# .env 由启动器/命令行加载：manage.py 与 python main.py 内置 load_dotenv；
# 裸 uvicorn/celery 请显式 --env-file / env 包装（#663：app 代码不再有
# import 期 env 副作用）。
celery -A app.services.task_queue.celery_app worker --loglevel=info &
uvicorn app.main:app --env-file .env --reload --host 0.0.0.0 --port 18000
cd frontend && npm run dev
```

## 7. 数据库与迁移

- 本机 pytest / `manage.py` 默认 SQLite（`sqlite:///./data/webgis.db`），零配置兜底。dev compose 的 api/celery 钉到 PostGIS `db` 服务（不读 `.env` 里的 sqlite URL）。生产必须 PostgreSQL/PostGIS（`ENV=production` 时启动校验强制 `postgresql://` 前缀）。
- 迁移用 alembic，单一迁移链 `migrations/versions/`（18 个 revision）：

```bash
alembic upgrade head
```

SQLite 下 `migrations/env.py` 自动启用 `render_as_batch` 以兼容 ALTER 限制。

切换到 dev compose 的 PostGIS：先 `docker compose up -d db`，然后在 `.env` 设置

```bash
DATABASE_URL=postgresql://postgres:<DB_PASSWORD>@localhost:15432/webgis
```

再执行 `python manage.py init-db` 或 `alembic upgrade head`。

## 8. 健康检查

| 端点 | 用途 |
|------|------|
| `GET /api/v1/health` | 基础信息（含版本号） |
| `GET /api/v1/health/live` | liveness：仅确认进程可响应，不查依赖 |
| `GET /api/v1/ready` | readiness：DB + LLM + Redis + Celery 四项连通性，任一不可达返回 503 |

```bash
curl http://localhost:18000/api/v1/health
curl -i http://localhost:18000/api/v1/ready
```

## 9. 生产模式校验（.env 排错参考）

`ENV=production` 时 `app/core/config.py` 的启动校验会拒绝以下配置：

- `JWT_SECRET_KEY` 为空；
- `LLM_API_KEY` 为空或等于占位符 `your-api-key-here`；
- `DATABASE_URL` 不是 `postgresql://` / `postgres://`；
- `CORS_ORIGINS` 含 `*`。

开发模式下这些问题只告警不阻断。

## 10. 测试与质量检查

推送前一条命令跑齐 CI PR lane 的本地可复现门禁（#671）：

```bash
scripts/ci-local.sh          # 全量
scripts/ci-local.sh --fast   # 只跑 ruff + eslint + typecheck + vitest
```

脚本与 `.github/workflows/production.yml` 逐字对齐，漂移由 `tests/test_ci_local_gate_contract.py` 断言守住。日常手动回路：

```bash
# 后端：日常快速集（单元 + 集成）
pytest -q tests/unit tests/integration

# 后端全量
pytest

# Lint（仓级，与 CI 同 —— 不要只 lint 改动文件）
ruff check app/ tests/ main.py manage.py

# 前端
cd frontend && npx vitest run && npm run typecheck && npm run lint
```

pytest markers：`heavy`（重依赖 geopandas/numpy/rasterio，含 Runtime 校验器 `runtime_validator`）、`perf`（性能回归基线）、`cartography`（制图闭环门禁）、`real_services`（需真实 Postgres/Redis/Celery，不可达自跳过）。本地跑单类：`pytest -m heavy` 等。perf 基线要求隔离运行：无 marker 过滤的全量跑会自动 skip perf 项（全量中段执行的时序抖动会超基线，#664），评测 perf 请用 `pytest -m perf --no-cov`。CI 后端覆盖率闸为 75%（`--cov-fail-under`，`pytest.ini` 默认开启 `--cov=app`）。

> **Runtime Scenario 单跑与排障**：新增/调试渲染场景请见 [Runtime Scenario 作者指南](runtime-scenario-guide.md) §5（含 `pytest -m heavy -k <name>` 单跑与 `runtime_dir` 产物 `map.png` / `trace.zip` / `report.json` 排障路径）。

前端脚本一览（`frontend/package.json`）：`npm run dev` / `build` / `start` / `test` / `test:coverage` / `lint` / `typecheck`（双 tsconfig：主工程 + `tsconfig.test.json`）。

## 11. 常见问题排查

**端口冲突**。本地路径占用端口：后端 18000、前端 3000、Redis 16379、Postgres 15432。排查：`lsof -i :18000`（或 `ss -ltnp | grep 18000`）。改动后端端口时记得同步 `frontend/.env.local` 的 `NEXT_PUBLIC_API_URL`。

**Redis 缺失/带密码**。`python manage.py dev` 启动即退并提示 Redis 未运行：本机装 redis-server 监听 16379，或 `docker compose up -d redis`。用 compose 的 redis 时它有 `--requirepass`，`REDIS_URL` 必须写成 `redis://:<REDIS_PASSWORD>@localhost:16379/0`，否则 ping 通不过、Celery 也连不上 broker。

**LLM key 占位符**。日志出现 `LLM_API_KEY is set to placeholder value ... LLM calls will fail`：在 `.env` 填入真实 key。占位符状态下后端能启动，但所有对话/工具调用会 401。默认 provider 是阶跃 Step Plan（`https://api.stepfun.com/step_plan/v1`），也可换任意 OpenAI 兼容端点（同时改 `LLM_BASE_URL`/`LLM_MODEL`）。

**JWT_SECRET_KEY 告警**。开发模式留空只告警，但每次重启密钥轮换、登录态全部失效；想保持会话持久就在 `.env` 固定一个值。

**数据库相关**。首次运行报 no such table：`python manage.py init-db` 或 `alembic upgrade head`。切 PostGIS 后异常先确认端口是 15432（映射端口）而非 5432。

**容器 worker 找不到上传文件**。dev compose 中 api 与 celery-worker 必须共享 `uploads` 卷（compose 已配置）；若自建 compose 请保持同样挂载，否则栅格分析在 worker 侧报 No such file。

**代理/SSL 报错**。地理编码或 OSM 请求 SSL 失败时配置 `HTTPS_PROXY`（见 `.env.example`）。

## 相关文档

- [技术方案说明书](技术方案说明书.md)：项目定位与核心架构
- [Fetch-on-Demand 机制](data-fetcher.md)：大数据引用与按需拉取
- [部署文档](DEPLOYMENT.md)：生产部署
