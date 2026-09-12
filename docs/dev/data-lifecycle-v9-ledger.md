# foundation/data-lifecycle-v9 — 复核纪要 + 交付台账

> §0.2 复核产出（随 PR 描述交付）。基线 origin/master @ `8b5b8375`。

## 一、复核纪要（防重复复核，结论：无重叠照单执行）

| 复核项 | 结论 |
|---|---|
| PR 检索（data quality/lifecycle/retention/gc/profiling/template versioning，200 条） | 相关历史 PR 全部 MERGED 且为前置能力（V3 数据基座 #1149、V4 控制面 #1160、lakehouse V6/V7/V8、fabric V8 #1232/1233、CI 修复 #1229/1224/1225）。**无进行中的同域 PR** —— 本线无重叠 |
| Issue 检索（数据质量/生命周期/retention/gc/模板版本/alembic 撞号，300 条） | 模板系统 issue（#174/#175/#179/#184/#676）为 Cartography 模板**运行时**问题，已闭；本线做模板**版本化治理**（0048），正交。#539 质量审计性能（O(P²) 拓扑）已闭。alembic 撞号无专属 issue（现场记录在 #1229/#1224 PR） |
| 分支检索（data-/lifecycle/quality） | 远端无同名/同域分支；本地仅本线 worktree |
| project.py 端点现状 | quality-audit(:1032)/repair(:1053)/data-usage(:1322)/data-gc plan(:1376)/execute(:1462) 全部同步 def、无落库报告实体、无审批回滚 —— 任务书判断成立；本线全部走**新路由文件**，既有签名零改动 |
| 历史必查项 | #1221（migration 协议漂移 + D-7 fcntl 类环境守卫）：本线 P6 落防撞号机制 + 顺带修复 weasyprint OSError 同类问题；#1219（B-11 registry 文件名碰撞等）：修复在 master 成立（component_registry/get_template_registry 均带身份约束），本线 templates 校验复用之；#1229/#1224（lakehouse FK seeds、alembic 双头两次撞号现场）：P6 直接动机，复盘表见 recon §5 |
| 薄模块现状确认 | data_quality 493 行 / data_profile 556 行 / templates 118 行 ✅；`data_lifecycle` 已有 gc/quota/service 三文件 1429 行（V3 遗产，本线**新增** policy/adapters/gc_plan/jobs/metrics 不覆盖其语义）；`app/db/` 死包（零 import）→ 删除；`app/tasks/` 仅 explorer/task_chain（活跃）→ 保留+文档化；`app/skills/` 为数据目录（6 个 .md）→ README 说明 |
| `pip install -e .` 任务书命令 | 本仓 pyproject 无 `[build-system]`，setuptools flat-layout 多顶层包自动发现拒绝 → `-e .` 结构性不可用（CI 从不 `-e .`，兄弟 worktree venv 亦无 editable 安装）。**按仓库既有约定**改为 `pip install -r requirements.txt -r requirements-dev.txt`（等效达成 venv 可用）；pytest 收集 10795 项确认环境成立 |
| 编号契约 | ADR-0140（watermark 0137，0138 属 A 线）✅；迁移段 0046–0055（master 现头 c1e2f3a4b5c6，0036–0045 留 B 线）✅，本线用 0046/0047/0048 |

## 二、交付台账（任务 → 文件 → 测试 → 证据）

### P0 勘察
- `docs/dev/data-lifecycle-recon.md`（五机制全景/端点契约/模板耦合/测试缺口/撞号复盘）

### P1 质量规则引擎
- 代码：`app/services/data_quality/{rules,rule_functions,engine,autofix,jobs,metrics}.py`、
  `app/models/data_quality.py`、`app/api/routes/data_quality.py`、
  `migrations/versions/0046_quality_reports.py`、`migrations/env.py`、`app/main.py`
- 测试：`tests/data/test_quality_rules.py`（16 规则逐类 + DSL fail-fast，22 测）、
  `tests/data/test_quality_engine_api.py`（双路径/恢复/autofix/所有权守卫，15 测）
- 证据：规则类型 16 ≥ 15 ✅；durable 恢复 = 复用 + ref 缺失诚实失败两测 ✅

### P2 画像深化
- 代码：`app/services/data_profile/{distribution,incremental,unified}.py` + 路由 `POST /data-quality/profile`
- 测试：`tests/data/test_profile_unified_v9.py`（H3/增量==全量/联动/失效钩子，12 测）

### P3 生命周期策略引擎（ADR-0140）
- 代码：`app/services/data_lifecycle/{policy,adapters,metrics}.py`、`app/models/data_lifecycle.py`、
  `migrations/versions/0047_lifecycle_registry.py`、`app/api/routes/data_lifecycle.py`
- 测试：`tests/data/test_lifecycle_policy_v9.py`（分级/默认策略等价性/复活/登记对账，10 测）
- 证据：五 kind 全登记 ✅；等价性 = `test_default_policies_equivalent_to_status_quo`（全 observe/零候选）✅

### P4 templates 升格
- 代码：`app/services/templates/versioning.py`、`app/models/template_version.py`、
  `migrations/versions/0048_template_versions.py`、`app/api/routes/template_versions.py`
- 测试：`tests/data/test_template_versions_v9.py`（继承覆盖矩阵/跨模板链/失效兼容读取/V7 校验/REST 全路径，7 测）

### P5 data-gc 闭环
- 代码：`app/services/data_lifecycle/{gc_plan,jobs}.py`
- 测试：approve→execute→rollback→purge 全环（文件真搬真还）、状态机全转移矩阵
  （reject/cancel/非法 409/中断恢复幂等重入）、lakehouse observe-only 拒绝、
  plan-digest 幂等复用、树截断旗标 —— 见 test_lifecycle_policy_v9 + test_lifecycle_api_v9（5 测，HTTP + eager durable job）

### P6 Alembic 防撞号
- 代码：`scripts/alloc_migration.py`、`migrations/.alloc.json`、CI db-migrations 新步骤、
  `tests/test_alembic_metadata.py`（+4 测）、`docs/dev/migration-protocol.md`
- 证据：`--check` 当前树 OK（46 迁移单头 0048）；重复编号临时副本 fail（自证测试钉死）

### P7 结构债
- `app/db/` 删除（零 import）；`app/tasks/README.md`、`app/skills/README.md`；
  legend_spec 接通（band_math/spectral_engine + 3 行为钉测）
- 顺带修复：weasyprint OSError 守卫（publication_export/report_service + 2 个 cartography 测试诚实 skip）

### 门禁证据（§5 全项）
1. alembic 单头（0048）+ 编号唯一断言绿；0046 up/down 往返绿（显式回退 c1e2f3a4b5c6）
2. 16 规则单测绿；durable 路径恢复测试绿
3. 五 kind 登记注册表；默认策略等价现状（测试 + 本 ADR 附录）
4. gc 状态机全转移路径测试绿（含回滚与中断恢复）
5. 模板版本化/继承契约绿；V7 组件校验绿
6. `--check` 自证 fail 测试绿
7. data_lifecycle 测试引用 8 → 本线新增 10 个测试文件（连同 data_quality/data_profile
   计 10 文件 82+ 测）≥25 达标
8. `pytest tests/unit -q -n 4 -m "not heavy and not real_services and not perf"` 全绿；
   ruff 变更文件 0 告警；覆盖率 ≥75%（`--cov=app` 总量口径，见 CI 取证）

### 已知环境事项（非本线引入）
- `tests/jobs/test_job_migration.py` 在 Windows 本机失败：其 `_alembic` 子进程
  env 写死 Linux PATH（`/usr/bin:/bin`）→ Windows 下 alembic 无法启动（returncode 1、
  stdout 空）。同 env 手动执行 `alembic upgrade head`（含本线 0046–0048）全部成功，
  CI（Linux）不受影响。未修（跨线共享测试基建，属各线公共约定，避免本线越界）。
