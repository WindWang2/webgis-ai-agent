# 工程质量审查报告

审查人: Agent E (工程质量专项) · 日期: 2026-08-24 · 对象: `master@062ba31` (工作区干净)

## 1. 工程资产现状(门禁/测试/ADR 小结)

- **CI 门禁** (`.github/workflows/production.yml`): lint (ruff + ESLint `--max-warnings 0`)、Backend Tests (`--cov-fail-under=75`)、DB Migration Gate (PostGIS alembic upgrade + model/migration 漂移检查)、Real Services Smoke (PostGIS+Redis+Celery)、Performance Regression Gate 等多车道;本地同构门禁 `scripts/ci-local.sh` 与 CI 命令字面一致,并有 `tests/test_ci_local_gate_contract.py` 保护二者不漂移——这套设计本身是高成熟度实践。
- **ADR**: `docs/adr/` 实测 **70 篇** (ADR-0000~0069),决策记录持续演进到最近的 cartographic memory 与 dispatch-service 收口。
- **测试**: `tests/` 下 200+ 测试文件;本次抽样 `tests/unit` 全量运行 **2730 passed / 4 skipped / 0 failed (361s)**;pytest.ini 的 `--ignore` 仅剩两个 smoke 脚本(注释明确"是脚本不是 pytest 用例"),历史欠账基本清偿;heavy/perf/cartography/real_services 用 marker 分道,契约清晰。
- **静态检查**: ruff 配置仅 E4/E7/E9+F 并显式注明取舍;前端双 tsconfig,tsc 通过;ESLint 9 flat config。
- **总体印象**: 异常处理(工具 `{"error":...}` 契约由 `registry.py` + `tool_dispatch_service.py` 统一收口)、日志(request_id/session_id/turn_id 关联注入)、安全(无硬编码密钥、路径越界防护、生产 fail-fast 校验)整体质量高。以下问题是在高基线上仍然存在的真实缺陷。

## 2. 静态检查运行结果(真实输出摘要)

| 检查 | 命令 | 结果 |
|---|---|---|
| ruff | `.venv/bin/ruff check app/ tests/ main.py manage.py` | **Found 7 errors** (F401 x5 / E741 / F841,全部位于 `tests/`),与 CI lint job 同命令 |
| tsc | `npx tsc --noEmit -p tsconfig.json` (frontend/) | 通过,无输出 |
| ESLint | `npx eslint .` (frontend/) | **2 errors + 3 warnings** (`chart-theme.test.tsx` require-import x2 + unused x2;`map-spec-chrome.test.tsx` unused x1) |
| pytest | `.venv/bin/pytest -q tests/unit -x --timeout=120` | **2730 passed, 4 skipped**, 255 warnings, 360.90s;单元子集覆盖率 TOTAL 71% (CI 75% 门禁为全量口径) |

## 3. 发现的问题

### E-1 [P1] lint 双门禁红色状态: ruff 7 错 + ESLint 5 问题已合入 master,门禁被绕过

- **问题描述**: 当前 HEAD 在 CI 两条阻塞型 lint 门禁上均为失败状态。ruff: 7 个错误(F401 x5、E741、F841);ESLint: 2 个 error(require-import)+ 3 个 warning,而 CI 是 `--max-warnings 0`。引入这些错误的提交 `4316771` (08-23) 与 `190fac6` 已直接进入 master,证明最近提交未经过 CI 或本地 `ci-local.sh` 验证即合入。
- **影响范围**: `tests/test_pi_rpc_client_810.py:8`、`tests/test_session_l1_cache.py:206`、`tests/unit/lib/test_geo_analysis.py:133`、`tests/unit/test_data_fabric_services.py:177`、`tests/unit/test_explorer_stages.py:558`、`tests/unit/test_model_library_audit832_835.py:14`、`tests/unit/test_round2_audit846_853.py:60` (ruff);`frontend/components/chat/chart-theme.test.tsx:11,21,35,59`、`frontend/components/map/map-spec-chrome.test.tsx:260` (eslint)。
- **代码位置**: `.github/workflows/production.yml:58` (ruff gate)、`.github/workflows/production.yml:71` (eslint gate);对应门禁失效证据: `git log -1 4316771` 已在 master。
- **原因分析**: 近期提交节奏快(8 月单月 541 commit),审计修复类提交直接 push master,跳过了 PR 流程的 CI 拦截;`ci-local.sh` 存在但未被强制执行。
- **优化方案**: (1) 立即修复 7+5 处 lint 错误(5 个 F401 可 `ruff check --fix` 自动清除);(2) 对 master 启用分支保护,禁止直推;(3) 在 `ci-local.sh` 之外增加 pre-push hook 或在审计类批量提交脚本里内嵌 lint 快速道。
- **验证方式**: `cd /home/kevin/projects/webgis/webgis-ai-agent && .venv/bin/ruff check app/ tests/ main.py manage.py && cd frontend && npx eslint . --max-warnings 0 && echo CLEAN`

### E-2 [P2] 分层侵蚀: services/tools 层反向 import api 路由层(6 处)

- **问题描述**: 底层包通过函数内 deferred import 引用 `app.api.routes.*`,形成 api→services→api 的隐式环。services/tools 应当下沉为 api 的依赖,而非反过来借用路由模块里的对象。
- **影响范围**: 6 处反向依赖,涉及 5 个文件;路由层任何重构(如拆分 1603 行的 chat.py)都会静默破坏 services 层;services 单测被迫连带加载路由模块及其 FastAPI 依赖。
- **代码位置**:
  - `app/services/history_service_async.py:612` → `from app.api.routes import chat as chat_route`
  - `app/services/tool_dispatch_service.py:821` → `from app.api.routes.raster import lookup_session_owner_token`
  - `app/services/chat/pi_rpc_client.py:186` → `from app.api.routes.pi_tools import get_bridge_secret`
  - `app/tools/skills.py:247`、`app/tools/skills.py:328` → `from app.api.routes.chat import get_registry`
  - `app/tools/cartography_tools.py:446` → `from app.api.routes.raster import lookup_session_owner_token`
- **原因分析**: `get_registry`/`get_bridge_secret`/`lookup_session_owner_token` 这类单例访问器与所有权查询在路由文件里随手定义,后续底层需要时就近借用,deferred import 掩盖了循环导入报错。
- **优化方案**: 将 `get_registry`(改由 `app.tools.registry` 提供 app-level 单例)、`get_bridge_secret`(下沉到 `app/core` 或独立 secret 模块)、`lookup_session_owner_token`(下沉到 `app/services` session 归属服务)迁出路由层,api 层改为从新位置 re-export 保持兼容。
- **验证方式**: `grep -rn "from app.api" app/services/ app/tools/ app/lib/ --include="*.py"` 应为 0 条(迁移后)。

### E-3 [P2] lib 叶子层反向依赖 services/tools,且 tool_cache 跨模块访问 registry 私有成员

- **问题描述**: `app/lib/` 定位为底层工具库,却反向依赖上层:多个 geo_analysis 模块 import `app.services.jobs.*`;`app/lib/tool_cache.py` 直接 import `app.tools.registry` 的 4 个**下划线私有成员**(`_arg_size_hint_var`、`_estimate_json_bytes`、`_ESTIMATE_SIZE_LIMIT`、`_ESTIMATE_MAX_NODES`)。
- **影响范围**: 私有成员被外部消费后即成为事实公共契约,registry 内部重构(重命名/改签名,#677 估计器的"分工规矩"全靠注释维系)会在无告警的情况下破坏 tool cache 的正确性门(oversized 短路与 ref 证明);lib↔services 环导致 lib 无法独立复用。
- **代码位置**:
  - `app/lib/tool_cache.py:47`、`app/lib/tool_cache.py:60`、`app/lib/tool_cache.py:79` (私有成员 import)
  - `app/lib/geo_analysis/aggregation.py:11`、`app/lib/geo_analysis/interpolation.py:37`、`app/lib/geo_analysis/network.py:12`、`app/lib/geo_analysis/raster_math.py:14-15`、`app/lib/geo_analysis/geometry_ops.py:26` → `app.services.jobs.*`
  - `app/lib/cartography/thematic_spec.py:161` → `app.services.cartography_service`
- **原因分析**: `cancellable`/`checkpoint`/`atomic_output` 是作业基础设施,最初放在 services/jobs,lib 计算函数需要取消点时就近借用;大小估计器为共享 #677 优化直接引用了私有符号。
- **优化方案**: (1) 把 `cancellable/checkpoint/atomic_output` 提为 `app/lib/runtime` 或 `app/core` 级原语,services 与 lib 共同依赖它;(2) 将 registry 的 4 个私有成员升级为公开 API(去下划线,写入 `app/tools/_utils.py` 之类共享模块)并加公共契约测试。
- **验证方式**: `grep -rn "from app.services\|from app.tools" app/lib/ --include="*.py"` 清零;`grep -rn "_estimate_json_bytes\|_ESTIMATE_MAX_NODES" app/lib/` 清零。

### E-4 [P2] .env.example 与 app/core/config.py 键漂移: 30+ 个 Settings 键缺失于模板

- **问题描述**: `config.py` 中定义的键有 30+ 个未出现在 `.env.example`,使用者无法从模板发现这些配置。最典型: `efd6ddf` (08-23) 向 config 新增了 11 个 CARTO_* 可调阈值,而 `.env.example` 停留在 08-20 (4ab061c),新的"tunable thresholds"特性对模板不可见。
- **影响范围**: 缺失键包括:`CARTO_LOAD_WARN_RATIO` 等 11 个校准阈值 (`config.py:57-72`)、`MAPBOX_TOKEN`/`BING_MAP_KEY`/`TENCENT_MAP_KEY` (`config.py:105-107`,模板只列了天地图/高德/百度)、`CORS_ORIGINS` (`config.py:127`,**生产环境 fail-fast 校验会直接 RuntimeError**,用户升级生产必踩坑)、`LLM_PLANNER_MODEL` (`config.py:82`)、`NOMINATIM_URL`/`OVERPASS_API_URL` (`config.py:86-87`)、`DATA_FABRIC_*` 8 键、`HEATMAP_MIN_POINTS`、`DATA_DIR` 等。
- **代码位置**: `app/core/config.py:57-87,105-127,167` vs `.env.example` (全文件,无对应条目)。
- **原因分析**: 缺少"改 config 必须同步模板"的守恒测试;`.env.example` 依赖手工同步,特性提交时被遗漏。
- **优化方案**: 新增契约测试:解析 `config.py` 的 Settings 字段集合与 `.env.example`/`.env.prod.example` 键集合做差集,非白名单字段(如 compose 专用的 `DB_PASSWORD`)必须出现在模板中;并入 `tests/test_env_prod_template.py` 现有体系。
- **验证方式**: `python3 -c "import re; cfg=set(re.findall(r'^\s{4}([A-Z_0-9]+):', open('app/core/config.py').read(), re.M)); env=set(re.findall(r'^([A-Z_0-9]+)=', open('.env.example').read())); print(sorted(cfg-env))"` 应为空(或白名单)。

### E-5 [P2] .env.example 的 REDIS_URL/CELERY_URL 不带密码,与 compose 强制 requirepass 冲突,host 进程无法连 Redis

- **问题描述**: `docker-compose.yml:36` 对 dev Redis 强制 `--requirepass ${REDIS_PASSWORD:?}`,且 `.env.example:30` 明确要求填写 `REDIS_PASSWORD=change-me-dev-redis-password`;但同文件 `.env.example:65-68` 给出的 `REDIS_URL=redis://localhost:16379/0`、`CELERY_BROKER_URL`、`CELERY_RESULT_BACKEND` 均不含密码。容器内 api/celery 被 compose 注入的带密码 URL (74-76 行) 覆盖不受影响,而**宿主机进程**(`manage.py` 的 health 检查走 `settings.REDIS_URL`、本地脚本、直连调试)会得到 `NOAUTH Authentication required`。
- **影响范围**: 按官方模板 `cp .env.example .env && docker compose up` 后,`python manage.py status` 的 Redis 检查报错,用户第一反应是服务坏了;README 快速上手指令与模板互相矛盾。
- **代码位置**: `.env.example:65` (`REDIS_URL=redis://localhost:16379/0`) vs `docker-compose.yml:36` (requirepass)、`docker-compose.yml:74-76` (容器内带密码 URL);`manage.py:111` (`redis.from_url(settings.REDIS_URL)`)。
- **原因分析**: 8-23 的 `ab87001` 修复只处理了容器内 URL,宿主机路径的模板示例未同步补密码占位。
- **优化方案**: 模板改为 `REDIS_URL=redis://:${REDIS_PASSWORD}@localhost:16379/0`(pydantic-settings 不做变量插值,需写成 `redis://:${REDIS_PASSWORD}@...` 的说明或直接写 `:<REDIS_PASSWORD>@` 注释示例),三处 URL 同步;或 manage.py 在探测 localhost 时自动从 REDIS_PASSWORD 组装。
- **验证方式**: `docker compose up -d redis && redis-cli -p 16379 -a "$(grep ^REDIS_PASSWORD .env | cut -d= -f2)" ping` 成功而 `redis-cli -p 16379 ping` 返回 NOAUTH,即证明模板 URL 缺密码。

### E-6 [P2] job 失败日志只记异常类名,丢失 traceback 与错误消息

- **问题描述**: durable job 异常落库后,日志侧仅 `logger.warning("[jobs] failed job_id=%s error=%s", job_id, type(exc).__name__)`——只有类名(如 `RuntimeError`),既无 str(exc) 也无 `exc_info=True`。同文件 268/283 行的 cancel/heartbeat 失败都正确带了 `exc_info=True`,此处是明显遗漏。
- **影响范围**: 生产 job 失败时,日志流(及其聚合)无法回答"为什么失败";排障必须逐个查 DB `error` 字段,与"错误在源头可见比在下游可诊断便宜"的项目自身信条(见 `pi_tools.py:71` 注释)相悖。
- **代码位置**: `app/services/jobs/worker.py:440`。
- **原因分析**: `mark_failed_sync` 已把 `error=exc` 存库,写日志时误以为信息已持久化即可,忽略了日志侧的即时可观测性。
- **优化方案**: 改为 `logger.warning("[jobs] failed job_id=%s error=%s: %s", job_id, type(exc).__name__, exc, exc_info=True)`。
- **验证方式**: 构造失败 job 后 `grep "\[jobs\] failed" logs/app.log` 应包含堆栈。

### E-7 [P2] structlog 声明为直接依赖但全库零使用(死依赖)

- **问题描述**: `pyproject.toml:28` 与 `requirements.txt:38` 都声明 `structlog>=26.1.0`,理由沿用了"直接 import 的库必须直接声明"的口径;但 `app/` 全部 348 个文件中 **0 处** `import structlog`,日志栈实际是 stdlib logging + 自研 `RuntimeCorrelationFilter` (`app/core/logging_config.py`)。
- **影响范围**: 每个环境多安装一个永不加载的包(uv.lock:3574 锁定),并误导贡献者以为项目使用 structlog;同时暴露依赖声明与代码的双向核对缺失——项目规则 #618-35 只查"用了未声明",不查"声明了未用"。
- **代码位置**: `pyproject.toml:28`、`requirements.txt:38`、`uv.lock:3574`。
- **原因分析**: 日志方案曾计划迁移 structlog,最终落在自研 correlation filter 上,依赖残留。
- **优化方案**: 从 pyproject 与 requirements 删除 structlog 并 `uv lock` 刷新;若计划保留未来迁移,应在依赖旁注明"保留原因"。
- **验证方式**: `grep -rn structlog app/ tests/ main.py manage.py` 为空; `uv sync && .venv/bin/pip show structlog` 报 not found。

### E-8 [P3] 上帝文件: ChatExecutionEngine 2487 行 48 个方法;chat 路由 1603 行

- **问题描述**: `app/services/chat/execution_engine.py` 单类 48 个方法(306 行起至 2487 行),混杂会话锁、clear 生命周期、SSE 流编排、规划循环、失败分类 (`classify`, 2459 行)、恢复动作等至少 5 个职责;`app/api/routes/chat.py` 1603 行,其中 400+ 行是非路由逻辑(`_resume_generator_impl` 424-566、`_persist_pi_transcript` 843、`_cap_map_state_size` 566 等),是第二名路由文件的两倍以上。同级大文件: `lib/cartography/semantic_checks.py` 2352 行、`agent_pi_bridge.py` 2243 行、`lib/harness/pi_agent_harness.py` 2092 行(全仓 >1500 行文件 6 个)。
- **影响范围**: 定位与评审成本高;并发修改变冲突热点;`execution_engine` 的 clear/锁语义 (class 级共享 `_clearing`) 与流编排纠缠,回归面大。
- **代码位置**: `app/services/chat/execution_engine.py:306` (class 起)、`app/services/chat/execution_engine.py:2459` (恢复分类嵌在类尾)、`app/api/routes/chat.py:424-566`。
- **原因分析**: 聊天主循环持续按 issue 增量打补丁(注释可见 #407/#529/#791/#788 等),从未做机械切分。
- **优化方案**: 按既有深模块惯例拆出 `chat/session_clearing.py`(锁与 clear)、`chat/turn_recovery.py`(失败分类/恢复,已可平移 `HonestTurnFailure` 家族)、`api/routes/chat_resume.py`(resume 生成器);路由文件保留装配与依赖注入。
- **验证方式**: `wc -l app/services/chat/execution_engine.py app/api/routes/chat.py` 均降至 1000 行以下,`pytest tests/unit -q` 保持全绿。

### E-9 [P3] 27 处 os.getenv/os.environ 绕过 pydantic-settings,含安全相关开关

- **问题描述**: 配置中心 `app/core/config.py` 之外存在 27 处直接读环境变量,其中 `ALLOW_PUBLIC_REGISTER`(公开注册开关)属于安全面配置却绕开 Settings 的校验与可见性;`distributed_lock.py` 的 `REDIS_URL/USE_REDIS` 与 Settings 双轨;`execution_engine.py` 内 4 个会话行为参数用函数内 `_os.getenv` 读取,不可发现、不可在 config 层统一审计。
- **影响范围**: `app/api/routes/auth.py:53`、`app/services/distributed_lock.py:225-226`、`app/tools/registry.py:66` (`TOOL_TIMEOUT_S`)、`app/services/chat/execution_engine.py:367-401` (`SESSION_CACHE_SIZE`/`SESSION_MESSAGE_CAP`/`CLEAR_QUIESCE_TIMEOUT`/`CANCEL_WAIT_TIMEOUT`)、`app/tools/spatial_reasoning.py:163` 等;同时这些键天然不会出现在 .env.example(叠加 E-4)。
- **原因分析**: 部分 字段在模块加载期读取(settings 尚未就绪)有其历史原因,但运行期函数内读取的各键没有这个约束,属于就近取用惯性。
- **优化方案**: 运行期读取的键统一入 Settings(带默认值),模块加载期的保留原样并在 config 中登记文档;`ALLOW_PUBLIC_REGISTER` 必须入 Settings 并纳入 `_PROD_REQUIRED` 白名单审查(生产禁止 true)。
- **验证方式**: `grep -rn "os.getenv\|os.environ.get" app/ --include="*.py" | grep -v "os.environ\[" | wc -l` 收敛到白名单内(目标 ≤10,均注明加载期原因)。

### E-10 [P3] ApiResponse 泛型设计缺陷: TypeVar 定义未使用,data 为 Any(全库 427 处 Any 之根)

- **问题描述**: 统一响应契约 `app/models/api_response.py` 定义了 `T = TypeVar("T")` (第 6 行) 却从未使用,`data: Optional[Any]` (第 28 行) 使所有走该契约的响应放弃静态类型;全库 `: Any`/`-> Any` 达 427 处,api 层公共契约 (如 `app/api/routes/chat.py:135,139,184`) 亦直接以 Any 透传。frontend 有 0-error tsc 门禁,而后端等价约束(mypy/pyright)完全缺失。
- **影响范围**: 前端按 OpenAPI 生成的类型对 `data` 一律 unknown/any,双端类型闸门在后端一侧断开;修改返回结构无编译期保护。
- **代码位置**: `app/models/api_response.py:6,28`;热点: `app/tools/chart.py`、`app/tools/local_admin.py`、`app/api/routes/mapspec_mutations.py` 等。
- **原因分析**: 统一响应模型最初按弱类型快速铺开,TypeVar 是遗留的半成品泛型意图。
- **优化方案**: (1) `class ApiResponse(BaseModel, Generic[T]): data: Optional[T] = None`,保留非泛型别名兼容存量调用;(2) 在 pyproject 启用 mypy(pydantic plugin) 仅对 `app/models`、`app/schemas` 先行设闸,逐步扩面。
- **验证方式**: `.venv/bin/mypy app/models/api_response.py` 通过;`grep -c "data: Optional\[Any\]" app/models/api_response.py` 为 0。

### E-11 [P3] data_fabric 工具的 payload 上限保护在异常时静默失效

- **问题描述**: 两处"结果超过 40000 字符则裁剪"的上下文安全保护,`json.dumps` 抛错时被 `except Exception: pass` 吞掉——保护本身失效且无任何日志,超大 payload 将原样进入 LLM 上下文。
- **影响范围**: `app/tools/data_fabric_tools.py:163-164` (datasets 裁剪)、`app/tools/data_fabric_tools.py:211-212` (items 裁剪);一旦触发,后果是上下文膨胀/成本激增,而非报错,属静默降级。
- **原因分析**: 防御性裁剪写成了"尽力而为",未考虑检查器自身失败时的语义(应视为"可能超限"并保守裁剪,或至少记日志)。
- **优化方案**: 失败分支改为 `logger.warning(...)` 并直接执行保守裁剪 (`res["datasets"] = datasets[:10]`);两处重复逻辑提取为共享 helper。
- **验证方式**: 单测注入不可序列化对象,断言结果被裁剪且日志出现 warning。

### E-12 [P3] 根目录历史遗留物: 过时的 tracked 文档与一次性脚本未清理

- **问题描述**: (a) tracked 文档过时误导: `TODOS.md` (2026-05-21 的 `/review` 快照,大量条目已过时/带删除线)、`CODE_REVIEW.md` (2026-04-30,自称"All future PRs MUST be audited against these rules",与现行 CI/ADR 体系脱节)、`MEMORY.md`; (b) 一次性工具滞留: `start_all.bat` (Windows 批处理)、`bench_runner.py`+`bench_results_after.json` (8-16 的基准产物); (c) 磁盘未跟踪垃圾 (gitignore 已覆盖,无需入库但可本地清理): `dump.rdb`、`test_alembic.db`、`screenshot*.png` x4、`initial_snapshot.png`、`.git.backup-20260810-204852`、`.env-2`。
- **影响范围**: 新贡献者按 `CODE_REVIEW.md` V2/V3 不变式理解项目会与 ADR-0060~0069 的现行决策冲突;`TODOS.md` 的 P1 清单已部分失效却仍显示待办。
- **代码位置**: 仓库根: `TODOS.md`、`CODE_REVIEW.md`、`MEMORY.md`、`start_all.bat`、`bench_runner.py`、`bench_results_after.json`。
- **原因分析**: 治理文档随体系演进(→adr/、→CI 门禁)后旧载体未退役。
- **优化方案**: `CODE_REVIEW.md` 中已由 CI/ADR 承接的条款标注"superseded by ADR-xxxx/CI gate"后归档至 `docs/history/`;`TODOS.md` 未完成项迁 issue 后删除;`start_all.bat`、`bench_runner.py` 移入 `docs/history/` 或删除(bench 契约已由 `tests/benchmarks/` 覆盖)。
- **验证方式**: `git rm TODOS.md start_all.bat bench_runner.py && grep -rn "TODOS.md\|start_all" README.md docs/` 无引用残留。

---
**优先级统计**: P1 x1 (E-1) · P2 x6 (E-2~E-7) · P3 x5 (E-8~E-12)。E-1 建议当日修复(纯机械改动);E-4/E-5 建议同一 PR 处理(都是 env 模板守恒问题);E-2/E-3 合并为一次"分层收口"重构。
