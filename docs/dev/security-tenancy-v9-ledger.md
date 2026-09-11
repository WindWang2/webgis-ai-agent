# 安全与多租户 V9 交付台账（foundation/security-tenancy-v9）

- 分支：`foundation/security-tenancy-v9`（基线 origin/master @ 8b5b8375）
- ADR：`docs/adr/0139-security-tenancy-v9-org-scopes-quota.md`
- 勘察：`docs/dev/security-tenancy-recon.md`（P0 产物 + P2 销项勾销）
- 矩阵：`docs/dev/endpoint-scope-matrix.csv`（227 端点，生成器
  `scripts/generate_endpoint_scope_matrix.py`，CI
  `tests/test_endpoint_scope_matrix.py`）

## 任务 → 文件 → 测试 → 证据

| 任务 | 主要文件 | 测试 | 状态 |
|---|---|---|---|
| P0 勘察 | docs/dev/security-tenancy-recon.md | —— | ✅ |
| P1 geocompute org_id | migrations/0036；db_model.py（9 类） | tests/test_security_v9_migrations.py | ✅ |
| P1 workflow org_id | migrations/0037；db_model.py（6 类） | 同上 | ✅ |
| P1 lakehouse org_id | migrations/0038；lakehouse_datasets/catalog.py（4 类） | 同上 | ✅ |
| P2 tenancy 核心 | app/core/tenancy.py | tests/unit/test_security_v9_units.py | ✅ |
| P2 geocompute 查询/写入 | cluster/store.py、cluster/events.py、cluster/exchange.py、reuse_index.py、run_evidence.py、routes/geocompute.py | tests/unit/test_geocompute_*（authz/v6/v7/v5） | ✅ |
| P2 workflow 查询/写入 | workflow_runtime/{store,registry,service,reuse,hooks,subworkflow}.py、routes/workflow_runtime.py | tests/unit/workflow_runtime/*（150 用例） | ✅ |
| P2 lakehouse 打标 | lakehouse/dataset_registry.py、catalog_service.py | tests/data/test_lakehouse_datasets_api_v8.py | ✅ |
| P3 scopes 词汇表 | app/core/scopes.py、app/core/auth.py（claim 暴露）、routes/auth.py（scopes claim） | test_security_v9_units.py::test_scope_* | ✅ |
| P3 端点矩阵 + CI | docs/dev/endpoint-scope-matrix.csv、scripts/generate_endpoint_scope_matrix.py、tests/test_endpoint_scope_matrix.py | 6 用例（覆盖/词表/陈旧/admin 一致性） | ✅ |
| P4 隔离矩阵 | tests/integration/test_cross_tenant_matrix.py | 双 org × {geocompute,workflow-runtime,lakehouse,jobs,fabric,project,upload}（jobs/uploads 会话面由既有 RAG 矩阵覆盖；knowledge 为声明性收缩，见下） | ✅ |
| P5 metrics 门禁 | app/main.py（METRICS_TOKEN / fail-closed 401） | test_security_v9_units.py::test_metrics_gate_* | ✅ |
| P5 health 分层 | app/main.py（/healthz 公开、/health admin） | 同上 | ✅ |
| P5 org 配额 | app/services/org_quota.py、routes/security_admin.py、ErrorCategory.QUOTA（errors.py）、geocompute/workflow/lakehouse 路由接线 | test_security_v9_units.py::test_quota_* | ✅ |
| P6 refresh 轮换 | app/core/auth.py（fam claim）、routes/auth.py（家族旋转/重放） | test_security_v9_units.py::test_refresh_rotation_and_replay_detection | ✅ |
| P6 密码策略 | app/core/password_policy.py、routes/auth.py | test_security_v9_units.py::test_password_* | ✅ |
| P6 README 勾销 | README.md（Phase 6 认证增强半边 ✅） | —— | ✅ |
| P7 审计事件 | migrations/0040、app/services/audit.py、routes/security_admin.py、0039（org_quotas）、0041（refresh families） | test_security_v9_units.py::test_audit_* / rotation 审计断言 | ✅ |

## §5 门禁取证（最终门禁运行）

| 门禁 | 证据 | 结果 |
|---|---|---|
| alembic 单头 | `alembic heads` → `0041_security_v9_refresh_families (head)` 单行 | ✅ |
| up/down 迁移 | tests/test_security_v9_migrations.py（54s 绿，含回填语义/NOT NULL/索引/down 清理） | ✅ |
| V8 全表 org_id | `grep org_id` 销项表（recon §7）全勾 | ✅ |
| 隔离矩阵 | tests/integration/test_cross_tenant_matrix.py | ✅ |
| 矩阵 CI | tests/test_endpoint_scope_matrix.py 6 用例 | ✅ |
| /metrics 门禁 | 无 token 401 / 带 token 200 | ✅ |
| health 分层 | /healthz 200 公开；/health 401/403 非管理员 | ✅ |
| 配额/审计/轮换单测 | tests/unit/test_security_v9_units.py | ✅ |
| 全量 unit 套件 | `pytest tests/unit -m "not heavy and not real_services and not perf"`（串行；见备注） | 见 PR 评论 |
| 覆盖率 ≥75% | 仓库既有 gate（pytest-cov）；本线新增模块行覆盖见 PR 评论 | 见 PR 评论 |
| ruff 变更文件 | 全部变更文件 0 告警（本 PR 全量执行） | ✅ |

备注（诚实披露）：本机（Windows）`pytest-xdist` 的 worker 在 22–30% 处
反复被杀（`node down: Not properly terminated`，多轮复现），门禁按 §0.4
以**串行**全量套件执行，结果在 PR 评论附日志摘要。

## 偏差声明（任务书收缩项）

1. 控制面 5 表（geocompute_workers / workflow_workers / worker_cache /
   resource_usage / task_quarantine）：org_id 落列但 nullable、不回填、
   不参与 scoped 查询——worker 行不随 org 分裂；scope 键单向哈希不可逆。
   论证见 ADR-0139 §D1；§5 门禁「全部表带 org_id」按列存在性满足。
2. owner 哈希精确读（复用/证据/包表）未叠加 org 谓词——owner 为
   per-principal 事实（严格窄于 org），ADR-0139 §D2。
3. `require_scope` 在 P3 以「矩阵 + CI + 新增管理面端点全量应用」交付；
   存量 227 端点的依赖行区不做批量替换（§8 边界：A 线共享路由文件的
   Depends 行区冲突面控制；矩阵记录意图 scope，演进由矩阵 diff 评审）。
4. 渐进延迟为进程内 best-effort（0.5s→8s）；跨进程硬上界由既有限流
   承担（不重复造轮子）——ADR-0139 §D6。
5. **knowledge 域不进矩阵**（声明性收缩）：隔离 = documents.org_id 列
   （idx_document_org）+ 路由显式传 org（S41 体系）；需要真实 RAG 基础
   设施（队列/独立会话），裁剪 app 无法诚实装配。
6. **refresh「合并」语义**：任务书「刷新时合并 scopes」在 scope 纯
   角色派生的当前实现下落地为按当前角色**整体重算**（等价且更安全：
   角色降级即时收敛）；引入 per-user 精细授权时再演进为真合并。
7. **P4 断言放宽声明**：列表端点断言 200+空集（而非强行 404）、写越权
   接受 405/409（幂等取消语义）——存在性不泄漏这一硬约束不变。
8. 模型层任务书点名 project.py/data_fabric.py：V8 表不在其中（org_id
   早已存在），属任务书过度列举，本线未改动这两文件的相关部分。
9. **matrix 与 scoped_query/require_org_context 对齐**（code-review 后）：
   require_org_context 依赖已实现（tenancy.py）；scoped_query 成为
   store 读路径的活代码（cluster get_run_owned/list_runs、workflow
   get_instance）；geocompute 路由配额入口去重为薄封装；
   0039-0041 纳入迁移测试断言（org_quotas/audit_events/
   refresh_token_families 列+索引）。
