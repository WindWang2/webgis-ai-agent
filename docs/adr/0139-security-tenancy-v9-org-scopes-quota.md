# ADR-0139: 安全与多租户基石 V9 —— org_id 落库、OAuth scopes、配额与认证硬化

- 状态：Accepted（foundation/security-tenancy-v9）
- 日期：2026-09-11
- 关联：SEC-11（网络隔离）、SEC-08（匿名会话）、#1221（HMAC 域分离）、
  ADR-0052（durable job）、README Phase 6「用户认证增强」兑现

## 背景

认证体系是两层（JWT `token_version` 撤销 + 匿名 `owner_token`，角色
viewer/editor/admin），错误分类学与限流成熟，但：

1. **多租户半成品**：org_id 只存在于 legacy 表；V8 三大子系统
   （geocompute 9 表、workflow runtime 6 表、lakehouse 4 表）全部
   session/owner-token 作用域，组织级隔离缺失；跨租户隔离测试仅 RAG。
2. **无 OAuth scope**：JWT 无 scope claim，权限粒度只有 `require_admin`；
   端点无 per-route 权限矩阵。
3. **`/metrics` 无认证**，仅靠网络隔离文档兜底；根级健康面无分层。
4. **无组织级配额**：存储 / 任务数 / 速率均无 org 维度。
5. refresh 轮换是 soft rotation（无家族/重用检测）；密码策略仅长度≥8。

## 决策

### D1. V8 全表 org_id 落库（迁移 0036–0038）

- 词表纪律：`org_id VARCHAR(255)` 存 `organizations.id` **字符串原文**、
  无 FK（与 0033 geocompute_runs 同款跨库可移植纪律）。
- **租户数据面**（runs/events/node_results/evidence/artifacts、
  workflow packages/instances/nodes/events/node_reuse、lakehouse
  datasets/versions/refs/catalog，共 14 表）：nullable 引入 → 回填 →
  **NOT NULL** → 索引 `(org_id, created_at)`（无 created_at 的表用其
  时间列）。
- **控制面信任域**（geocompute_workers / workflow_workers /
  worker_cache / resource_usage / task_quarantine，共 5 表）：仅加
  nullable 列（NULL = 服务全部租户），**不参与查询级隔离**——worker 行
  不随 org 分裂（一个 worker 承载多租户 run，强制 NOT NULL 会迫使
  per-org 复制 worker 行，破坏调度语义）；resource_usage/quarantine 的
  scope 键是单向哈希不可逆推。信任域路径（coordinator 调度、恢复扫描、
  GC 保护引用扫描）REST 不可达（admin 面除外），边界持续由
  `tests/unit/test_geocompute_authz.py` 类测试钉住。
- **回填映射**：project_id → Project.org；session/会话归属 →
  Conversation.user_id → User.org；creator → User.org；哈希-only 行 →
  default 组织；全部 fallback 到 default 桶（迁移内 ensure
  `organizations.slug='default'`）。已带 org 的行不覆盖。

### D2. 查询级隔离（`app/core/tenancy.py`）

- effective org：JWT 用户 = DB/claim org；**匿名 owner_token 会话与
  无 org 用户归 default 隔离桶**（桶内会话级隔离仍由既有 owner_scope /
  owner_token 过滤承担——org 谓词是叠加的硬边界，不是共享授权）。
- `scoped_query`：租户面 SELECT 叠加 `org_id == effective_org`
  （**additive，只收紧不放宽**）。无 org 列的模型传入即 TypeError
  （fail-loud，防止控制面静默加入租户过滤语义）。
- 读隔离的分层事实：
  - owner 哈希精确匹配的读（owner_scope 参与唯一键的复用/证据/包表）
    本就 per-principal，严格窄于 org——org 谓词在这些路径等价冗余，
    不强行叠加（避免逐点二连查）；
  - owner 可缺省/控制面的读（cluster runs 的 REST 投影、workflow
    `get_instance(None)`、恢复扫描）一律补 org 谓词或钉死信任域注释；
  - 派生表写入（events/evidence/artifacts/reuse）org **锚定 run/owner
    行真相惰性解析**（派生路径绝不自造租户事实），default 桶兜底。
- 解析失败方向：路由线程解析失败 → 空 org → 全部读 404/空集
  （**fail-closed**）；写路径派生解析失败走各域 fail-open 纪律丢弃。

### D3. OAuth scopes 与权限矩阵（P3）

- 封闭词汇表（`app/core/scopes.py`，append-only）：`public:read` …
  `extensions:write`（22 词）。
- 角色兼容映射：viewer ⊂ editor ⊂ admin 的默认集；**claim 缺席
  （存量 token）按角色默认集回退**——无需强制重登录的平滑迁移。
  claim 中未知词丢弃；claim 与角色基线取交集（角色降级即时收敛，
  宽词不复活）。`token_version` 不因此 bump（见风险 R3）。
- `require_scope("...")` 依赖工厂：词汇表外 scope 是编程错误（抛
  ValueError）；scope 缺失 → 403 `INSUFFICIENT_SCOPE`（401 留给未认证）。
- **端点 scope 矩阵**：`docs/dev/endpoint-scope-matrix.csv` +
  生成器（`scripts/generate_endpoint_scope_matrix.py`）+ CI 校验
  （`tests/test_endpoint_scope_matrix.py`）：app 中每个路由必须在
  矩阵、scope 必须在词汇表、矩阵无陈旧行、require_admin 面必标
  admin scope。矩阵 diff 即权限面变更的评审面。

### D4. `/metrics` 门禁与健康面分层（P5）

- `/metrics`：`METRICS_TOKEN` Bearer 门禁，**默认开启 fail-closed**
  （未配置 token → 一律 401 分类学错误体）；`METRICS_AUTH_DISABLED=true`
  显式回退「仅网络隔离」旧模式；常量时间比较。网络层隔离仍是推荐纵深
  （应用层门禁是第二道闸，不是替代）。E 线 ops 消费形状不变（exposition
  不变，仅加鉴权头）。
- 健康面：新增根级 `/healthz`（公开 liveness 极简）与根级 `/health`
  （require_admin，复用 `/api/v1/status/detailed` 的组件检查与 TTL 缓存
  ——单一实现两个鉴权面入口）；既有 `/api/v1/health`、`/health/live`、
  `/ready`、`/status/detailed` 全部保持原状（E 线形状稳定承诺）。

### D5. 组织级配额（P5）

- 三资源：存储字节（lakehouse 版本 + 目录 + gc artifacts 字节列按 org
  聚合）、并发任务（geocompute 在飞 + workflow running）、速率（org
  维度 Redis 滑窗，复用限流设施）。
- 配置：per-org 覆盖表 `org_quotas`（admin scope 端点维护）→ env
  默认兜底（`ORG_QUOTA_*`）；NULL = 跟随全局默认（非无限）。
- 裁决：越限 → `QuotaExceededError`（`ErrorCategory.QUOTA` →
  **429** 分类学信封；QUOTA 为 append-only 新枚举，与 A 线信封文档的
  协调点已在 PR 声明）+ 越限审计事件。检查面故障 **fail-open**
  （保护性限流不放大故障、不作可用性单点）。

### D6. 认证增强（P6，README Phase 6 兑现）

- **refresh 家族轮换**：登录/注册建 `refresh_token_families` 行，
  `fam` claim 关联；refresh 时 jti 必须等于家族 `current_jti`（严格
  rotation）；**旧 jti 重放 → `reuse_detected=True`，整个家族失效**
  + 审计事件（终结被盗令牌的互踢循环）。无 `fam` 的存量 token 走
  soft rotation back-compat（≤7d 自然过期，与既有 back-compat 纪律一致）。
- **密码策略**（仅注册/改密面；登录绝不校验存量密码）：长度 8–128；
  常见密码表（内置 top-128）拒绝；≥3/4 字符类 **或** 长度 ≥12
  （口令短语豁免）。
- **登录失败渐进延迟**：每标识符指数延迟 0.5s→8s（进程内 best-effort）；
  跨进程硬上界仍由既有限流承担（5/5min/IP + 30/5min/标识符）。

### D7. 组织审计事件（P7）

- `audit_events` 表：actor / org / action / target / detail /
  **trace_id（W3C traceparent trace-id 分量，与请求关联中间件同键）**。
- 词表前缀 `admin. / quota. / auth.`（append-only）；词表外 action
  归类 `uncategorized.*`（不丢事实、不漂移词表）。
- **fail-open**：审计写入失败仅结构化日志（含最小重建信息），绝不
  阻断主流程。
- 查询面：`GET /api/v1/admin/orgs/{org_id}/audit` 与跨 org
  `GET /api/v1/admin/audit`（admin:read + require_admin 双守卫）。

## 风险与回滚

- R1 迁移：0036–0041 全部有真实 downgrade（SQLite batch recreate；
  全链 up/down/up 测试锁定）。runs 表 org 回 0033 nullable 形态，
  其余派生/控制面列直接 DROP。
- R2 default 桶：匿名与无主数据同桶——桶内既有 owner/session 过滤
  保持会话级隔离；org 谓词只收紧不放宽，无新增可见性。
- R3 scope 兼容：旧 token 无 scopes claim → 角色默认集，**无需
  token_version bump**（零强制下线）；矩阵/词汇表演进走 append-only。
- R4 METRICS_TOKEN 缺省行为：默认 401（运维必须显式配置或显式
  DISABLED）——部署清单需同步；回滚 = 设 METRICS_AUTH_DISABLED=true。
- R5 quota 失败方向：检查面 fail-open（可用性优先）；越限裁决本身
  fail-closed（429 + Retry-After 语义由调用方 429 面承接）。
- R6 回滚面：scope 兼容映射（claim 缺席回退）+ refresh legacy 路径
  （无 fam token 仍可刷）+ METRICS_AUTH_DISABLED + 配额 env 默认
  置 0/负（关闭并发/存储裁决）= 各能力独立可退。

## 协调点（10 线并发契约 §8）

- `ErrorCategory` 扩 `QUOTA`（append-only）——A 线信封文档对齐；
- A 线共享路由文件只动 Depends/权限行区与路由体内一行（org/quota
  入口），签名/response_model 行未触碰；
- E 线 ops：`/metrics` exposition 形状不变 + METRICS_TOKEN 鉴权头；
  `/healthz`、`/health` 为新增根级端点，`/api/v1/health*` 形状不变。

## 证据

- 迁移：`tests/test_security_v9_migrations.py`（回填语义 + up/down/up）
- 隔离矩阵：`tests/integration/test_cross_tenant_matrix.py`
- 单元集：`tests/unit/test_security_v9_units.py`
- 矩阵 CI：`tests/test_endpoint_scope_matrix.py`
