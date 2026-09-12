# 安全与多租户 V9 勘察报告（P0，只读）

- 基线：origin/master @ 8b5b8375（2026-09-11）
- 范围：org 生态 / 认证面 / V8 表全景与查询入口销项表 / metrics-health 暴露面 / extensions 计量面
- 用途：P1（org_id 落库 0036–0039）与 P2（scoped_query 改造）的直接输入
- 勘察方式：S1 Explore 只读扫描 + 主 agent 抽查复核；行号为基线提交时点行号

---

## 1. org 生态全景

### 1.1 模型层 org_id 现状

| 位置 | 类（表） | 定义 |
|---|---|---|
| `app/models/db_model.py:29` | `User`（users） | `org_id` nullable FK → organizations.id, ondelete CASCADE |
| `app/models/db_model.py:44` | `User` | `organization = relationship(..., lazy="selectin")`；**全仓无代码读该关系对象**，租户判定一律走标量列 |
| `app/models/db_model.py:55` | `Layer`（layers） | org_id **NOT NULL**；`uq_layer_org_name` / `idx_layer_org_status` / `idx_layer_org_category_status` |
| `app/models/db_model.py:109` | `AnalysisTask`（analysis_tasks，durable job 统一表） | org_id nullable FK；`idx_task_org_status` / `idx_task_org_type_status`；同表 session_id/owner_token/creator_id |
| `app/models/db_model.py:254` | `CartographyTemplate` | org_id nullable FK；`idx_template_org_kind` |
| `app/models/db_model.py:379` | `GeoComputeClusterRun`（geocompute_runs） | **V8 唯一已有 org_id**：`String(255) nullable`，无 FK 存字符串原文（376 行注释：跨库可移植纪律） |
| `app/models/data_fabric.py:17` | DataSource（data_fabric） | org_id nullable FK；`idx_datasource_org_type` / `uq_datasource_org_name` |
| `app/models/knowledge_base.py:27` | 知识库文档 | org_id nullable FK；`idx_document_org` |
| `app/models/project.py:19` | Project | org_id nullable FK；`idx_project_org_id` |

Organization 模型：`app/models/db_model.py:12-22`，表 `organizations`（id/name/slug unique/description/is_active/created_at/updated_at）。
**无默认组织**——P1 迁移需 ensure slug=`default` 的默认组织（匿名与无主回填桶）。

### 1.2 org 的统一取值入口

- `app/core/auth.py:104` `actor_ids()` → `(user_id, org_id)`：全部路由取 org 的统一入口；匿名哨兵折叠 None。
- `app/core/auth.py:338/374`：`get_current_user(_optional)` 从 JWT claim 读 org_id（claim 可缺省，`app/api/routes/auth.py:105-106` 仅在 user.org_id 非空时写入）。
- `app/core/auth.py:466`：`get_current_user_with_version` 从 DB User 行读 org_id（权威值）。
- `AUTH_DISABLED` bypass 身份 `test-admin` org_id=None（auth.py:42-47）——P2 需定义其 effective org。

### 1.3 代表性 org 消费点（改造成相关）

- `app/api/routes/geocompute.py:291/344`：cluster submit 写 creator_id/org_id/tenant_raw 进 geocompute_runs。
- `app/api/routes/project.py`：约 50 处 `actor_ids(user)`。
- `app/api/routes/templates.py:61-80`：`_template_scope_clause` org 匹配 SQL 谓词。
- `app/services/project_service.py:23-60/309-310`：org 相等性判定 + 列表过滤。
- `app/services/geocompute/api.py:162-172/209-215`：`_caller_org_id` + governor TENANT 作用域（org sha1 前 12 位）。
- `app/services/jobs/store.py:142/165-166/201-202`：analysis_tasks 创建/retry 传播 org_id。
- `app/services/layer_service.py:15/23`：建图层必填 org。

## 2. 认证面全景（app/core/auth.py，546 行）

### 2.1 依赖项清单

| 函数 | 行 | 说明 |
|---|---|---|
| `auth_bypass_enabled` | 50 | AUTH_DISABLED 测试开关 |
| `actor_ids` | 104 | 归一化 `(user_id, org_id)` |
| `authorize_session_write` | 107 | 会话写归属（SEC-08 fail-closed，hmac.compare_digest） |
| `hash_password` / `verify_password` | 172/189 | **scrypt**（N=2^14,r=8,p=1,32B），常量时间校验 |
| `create_access_token` | 214 | 30min access JWT，HS256 |
| `create_refresh_token` | 239 | 7d refresh JWT（jti 32-hex） |
| `verify_token` | 264 | 仅签名+exp；失败计数 `auth_jwt_validation_errors_total` |
| `get_current_user` | 284 | **不查库不校验 ver**；拒 refresh type |
| `get_current_user_optional` | 342 | 匿名返回 `{"user_id":"anonymous","role":"anonymous"}` |
| `get_current_user_with_version` | 382 | PK 查库 + `ver == User.token_version` + is_active（推荐依赖） |
| `require_admin` | 471 | role 实时取 DB，非 admin 403 |
| `get_owner_token` | 496 | `X-Session-Token` 头提取 |
| `verify_session_owner` / `require_owned_session` | 503/529 | 匿名会话守卫（无权 404） |

### 2.2 require_admin 使用统计（26 处）

geocompute.py 9（含 import）；config.py 9（含 import+注释）；lakehouse.py 3（含 import）；metrics.py 2；chat.py 2；auth.py 1（注释）。

### 2.3 JWT claim 结构

- access：`{sub, username, role, org_id?, exp, iat, type:"access", ver}`（签发 `routes/auth.py:102-122` → auth.py:227-236）。
- refresh：base + `type:"refresh"` + `jti`（auth.py:251-261）。
- **无 scopes claim**；role 仅 viewer/editor/admin（users 表 CHECK）。
- 兼容：无 type 视为 access；无 ver 视为 0。

### 2.4 匿名 owner_token

- 发放：`app/services/history_service_async.py:393`（匿名会话 `secrets.token_urlsafe(32)`）。
- 存储：`Conversation.owner_token`（db_model.py:220）；请求头 `X-Session-Token`；镜像列 `AnalysisTask.owner_token`。
- 校验：`authorize_session_write` + `verify_session_owner`。

### 2.5 登录端点与既有防线（app/api/routes/auth.py）

- `/auth/register`（125）：默认 503，`ALLOW_PUBLIC_REGISTER` 才开；5/h/IP。
- `/auth/login`（184）：失败 5 次/5min/IP + 尝试 30/5min；dummy-hash 防时序。
- `/auth/refresh`（233）：**refresh token 已存在**，type+ver 校验 + 30/5min/user 限速；**soft rotation（无 refresh_tokens 表、无家族/重用检测）**。
- `/auth/logout`（296）：bump token_version。
- 密码策略：**仅 min_length=8**（routes/auth.py:66），无复杂度/常见密码校验。

## 3. V8 表全景与查询入口销项表（P2 改造直接输入）

### 3.1 租户数据面（org_id NOT NULL + 索引 + scoped 查询）

| 表 | 写入点 | 查询点 | 现有作用域 |
|---|---|---|---|
| geocompute_runs | cluster/store.py:99 create_run（org_id 已写 178）；CAS 写 222-578/919-1018；purge_terminal 701 | get_run 189 / get_run_owned 196 / list_runs 203（owner 过滤）/ get_run_internal 612（coordinator 专用） | owner_scope 哈希 + creator_id + org_id(nullable) + project_id |
| geocompute_run_events | cluster/events.py:97 append（级联删 store.py:732；TTL events.py:275） | exists 151 / window 169 / count_kind 190 / sum_bytes 217 / progress_projection 238 | **仅 run_id——隔离靠上游 run 归属校验** |
| geocompute_node_results | reuse_index.py:51 record_result（LRU 剪枝 166） | find_result 100 / delete_result 133 | owner_scope 哈希 |
| geocompute_run_evidence | run_evidence.py:168 save_snapshot | list_snapshots 209（owner 过滤）/ load_snapshot 248 / load_snapshot_extras 277 | owner_scope 哈希；load 由调用方校验（executor.py:702-723） |
| geocompute_artifacts | cluster/exchange.py:95 _register / _touch 126 / cleanup_expired 236 | _has_row 182 | owner_scope(nullable) + run_id |
| workflow_packages | registry.py:78 register / publish 130 | resolve 160（owner 过滤）/ get_by_fingerprint 198 / list_packages 214 / list_versions 226 | owner_scope；唯一键 (owner_scope, package_id, version) |
| workflow_instances | store.py:150 create_instance / update_instance 441 / acquire_run_lease 483 / release_run_lease 520 | get_instance 209（**owner=None 跨 owner 读**）/ list_recoverable 746（**全局**）/ list_session_instances 784 / list_owner_instances 801 / count_active_subworkflows 814 / instance_content_fingerprint 836 | owner_scope + session_id + project_id |
| workflow_instance_nodes | store.py:195/252-437 transition（CAS 412）/ heartbeat 592 / cancel 624-691 | get_node_states 228 / get_nodes 237 / get_node 243 / find_orphan_running_nodes 541 / cancel_flags 667 / count_running_nodes 777 | **仅 instance_id——依赖调用方先解析 instance** |
| workflow_events | store.py:424/695-711 / cancel 656 | get_events 713（by instance_id） | **仅 instance_id** |
| workflow_node_reuse | reuse.py:89-124 record（_prune_owner 258） | find 133 / invalidate 181 | owner_scope + session_scope |
| lakehouse_datasets | dataset_registry.py:349 create_dataset | get_by_descriptor_id 411 / get_dataset 421 / list_datasets 443（owner 强制 456）/ dataset_owner_allows 431（fail-closed） | owner_type/owner_id（CHECK session/project）；owner 参与描述符身份（registry.py:186-195） |
| lakehouse_dataset_versions | registry.py:728 commit_version（savepoint 802） | get_version_row 468 / list_versions 481 / version_lineage 867 / resolve_version 900（前置 owner 校验 905）/ rollback_branch 831 | 经 dataset_row_id FK 间接继承 |
| lakehouse_dataset_refs | registry.py:549 create_branch / 598 create_tag / 631 _move_branch_ref（CAS 668） | get_ref 506 / list_refs 524 / branch_head 544 | 同上 |
| lakehouse_catalog_items | catalog_service.py:127 upsert（project_publish.py:209 调用）/ revoke 297 / reconcile 326 / GC 撤销 lakehouse_gc.py:322 | search_catalog 182（owner 强制，无全局目录）/ GC 保护引用 lakehouse_gc.py:136（**全表扫**） | owner_type/owner_id；唯一键 (owner_type, owner_id, content_sha256) |

REST 面：`app/api/routes/lakehouse_datasets.py`（每端点先 verify_session_owner + dataset_owner_allows）；`app/api/routes/lakehouse.py`（`_reject_project_scope` 88——project 域 REST 显式拒绝）；workflow_runtime 路由 owner 域统一 `owner_scope_for`（routes/workflow_runtime.py:98 → service.py:53-58 → executor.py:166-186）。

### 3.2 控制面 / 基础设施（信任域；org_id 落列但 NULL=服务全 org，查询不 scoped）

| 表 | 写入点 | 查询点 | 说明 |
|---|---|---|---|
| geocompute_workers | store.py:742 upsert / heartbeat 776 / leadership 1022-1117 / prune 795-837 | live_workers 839 | worker 服务全租户，行不随 org 分裂 |
| geocompute_worker_cache | cluster/locality.py:108 record_put / 187 record_hit / purge 252 / drop 281 | worker_holds 206 / cached_bytes 228 / entries 239 | worker_id 维度；cache_key 已嵌 owner 哈希 |
| geocompute_resource_usage | store.py:1181 set_scope_limits / ensure_scopes 1235 / reserve 1287 / release 1392 | ledger_snapshot 1121 / _locate_reject_dim 1357 | scope_key=global/t:<sha1>/p:<sha1>（不可逆）；新行可在调用方持真实 org 时打标 |
| geocompute_task_quarantine | cluster/quarantine.py:92 record_failure | is_quarantined 71 / snapshot 168 | owner_scope 是 PK 分量；快照仅 admin 面 |
| workflow_workers | cluster.py:115 register / 155 heartbeat / 183 retire / 198 sweep | list_active 223 / total_active_slots 261 | 同 geocompute_workers |

控制面 coordinator 路径（scan_dispatchable/scan_running/reclaim/counters 等 store.py:638-738/883-918）本就是信任域（`get_run_internal` 注释 613-617：绝不进 REST 投影），P2 不 scoped、在 ADR 立此存照。

### 3.3 回填映射（P1 迁移 SQL 顺序）

1. 有 `project_id` → `Project.org_id`；2. 有 `session_id`/owner 可解析 → `Conversation.user_id → User.org_id`；3. 有 `creator_id` → `User.org_id`；4. 其余（哈希 owner_scope-only）→ 默认组织；5. 控制面 → NULL。所有回填 fallback 默认组织（slug=`default`，迁移内 ensure）。

## 4. /metrics /health /version 暴露面

| 端点 | 位置 | 认证 |
|---|---|---|
| `/metrics` | main.py:507 `Instrumentator().instrument(app).expose(...)`（try/except ImportError 503-509） | **无认证**；SEC-11 网络隔离注释块 main.py:493-502 |
| `/api/v1/health` | routes/health.py:92 | 无；version + agent_runtime + pi_workers_alive |
| `/api/v1/health/live` | health.py:128 | 纯存活 |
| `/api/v1/ready` | health.py:138 | **无认证**；DB+LLM+Redis+Celery 四检查，503 时 body 极简（SEC-11 注释 145-149） |
| `/api/v1/status/detailed` | health.py:329 | JWT（331）；组件词表 203；TTL 10s；503 |
| `/api/v1/metrics/digest` | routes/metrics.py:17 | require_admin |
| `/api/v1/version` | routes/version.py:19 | 公开极简（防侦察注释 1-6） |
| `/healthz`（根级） | **不存在** | 限流豁免前缀 `/api/v1/health`（main.py:546-550） |

SEC-11 文档点：docs/api-docs.md:489、docs/adr/0131:120（D7）、docs/platform-v4/CROSS_CUTTING_AUDIT.md:91。

## 5. Extensions 计量面

- `app/extensions_platform/metrics.py`（ADR-0131 D5 唯一计量模块）：`record_activation`:57 / `record_quarantine`:64 / `record_worker_crash`:70 / `InvocationTimer`:81；序列 `extension_activation_total{result}` 等；**无 session/user/extension_id 标签**（docstring 17-19，防高基数），per-extension 诊断走 `host.status_report()`。
- `ledger.py` 是投影回滚账本，**非 usage 计量**；extensions_platform 内 `quota|usage` 零命中。
- 真正的配额/账本现状在 geocompute 侧：`budgets.py`（ResourceGovernor，ScopeKind TENANT/PROJECT/SESSION）、`resource_counter.py`（CrossProcessCounter）、`ClusterLedger`（store.py:1146，三级 scope 账本）——org 配额（P5）与其合流的挂点。

## 6. ErrorCategory 与 429 现状

- `app/core/errors.py:37-55` `ErrorCategory(str, Enum)` 封闭词表 14 值：VALIDATION / PERMISSION / DATA_UNAVAILABLE / CRS / RESOURCE_EXHAUSTED / TIMEOUT / CANCELLATION / RETRYABLE / PERMANENT / DEPENDENCY_FAILURE / WORKER_LOST / MODEL_FAILURE / RENDER_FAILURE / STORAGE_CORRUPTION。
- `CATEGORY_DEFAULTS`（71-85）：429 用 `RESOURCE_EXHAUSTED`（retryable=True）。**P5 扩 `QUOTA` 枚举需 append-only 并在 PR 声明供 A 线对齐**。
- 既有 429 三来源：全局限流中间件直发（main.py:577，未走 ErrorCategory）；auth 限速（routes/auth.py:147/201/207/287）；geocompute ClusterBackpressureError→429+Retry-After（routes/geocompute.py:404-406）。

## 7. P2 改造销项结论（实施后勾销，v9 分支）

- **租户数据面 14 表**：org_id NOT NULL + 索引 ✓（迁移 0036–0038）；
  REST 读面 org 谓词 ✓（cluster runs 读 / workflow get_instance·lists
  / lakehouse 全部既有 owner 精确面）；派生表写入 org 锚定 run/owner
  真相惰性解析 ✓（events.append / reuse.record_result / run_evidence
  .save_snapshot / exchange._register / workflow journal & reuse.record）；
  create_run / create_instance / registry.register / lakehouse
  create_dataset·commit_version·branch·tag·catalog upsert 全部打标 ✓。
- **控制面 5 表**：org_id nullable 落列 ✓；查询不 scoped（信任域，
  ADR-0139 §D1 立此存照）✓；admin 面（cluster metrics/workers/
  quarantine snapshot）require_admin ✓。
- **owner 哈希精确读**（复用/证据/包表 find/list）：owner 为
  per-principal 事实、严格窄于 org → org 谓词等价冗余，未叠加
  （设计决策，ADR-0139 §D2）✓。
- **矩阵测试**：tests/integration/test_cross_tenant_matrix.py
  （双 org × geocompute/workflow/lakehouse/jobs/project/upload，404
  断言 + 响应体泄漏扫描）✓。

## 8. 对任务书前提的核对结论

1. 「org_id 只在 legacy 表」——**基本成立，一处偏差**：geocompute_runs 已有 nullable org_id（0033 引入）。P1 对该表只做回填+NOT NULL+索引，不重复加列。
2. 「无 OAuth scope」——成立（JWT 无 scopes claim，粒度仅 require_admin 26 处）。
3. 「/metrics 无认证」——成立（main.py:507）。根级 /healthz 不存在，/ready 无认证。
4. 「无组织级配额」——成立；合流挂点为 ClusterLedger/ResourceGovernor + extensions host 级计量。
5. 「refresh token 轮换/密码策略缺失」——refresh 已有 soft rotation（无家族/重用检测）；密码仅长度≥8。
6. #1221 各安全小项修复在 master 成立（hmac.compare_digest 于 auth.py:132/209）；跨租户隔离测试仅 `tests/integration/test_cross_tenant_isolation.py`（RAG）+ 零散单测，全量矩阵缺失。
7. README Phase 6（README.md:302）🚧「用户认证增强与动态栅格图层(规划中)」——本线兑现其认证增强半边；动态栅格图层不在本线范围，勾销文案需注明。
