# 05-review-findings — 两轮 Review 的 findings 与修复记录

## Review Round 1（Subagent-A，只读 + 实测运行）— verdict: PASS-WITH-CHANGES

R1 确认架构挑战的 7 项强制修订全部落实且带结构锁；quick lane 实测全绿。

### BLOCKER / CRITICAL（全部修复）
- **C-1 RELEASE_READINESS 字节闸红态 + 无强制点 + 自指**：
  (1) `--check` 对 `git_commit_generation_time` 归一比对（内容性状态锁定，
  commit 身份剥离出闸）；(2) detail 剥离 elapsed 等易变字段（同 commit
  字节确定）；(3) `--check` 挂进 quick lane + platform gate 结构锁
  （test_readiness_and_frontend_behavior_wired_into_quick_lane）；
  (4) tip 上重生成产物 + 账本收敛（gen→update×2 循环，check 双跑绿）。

### MAJOR（全部修复）
- **M-1 merge_sim 盲轴**：新增 `allocator_collision` blocking 轴（ADR 等
  allocator 面 NNNN/编号撞号的合并前检出——创始事故同类）+
  `coordinated_cochange` advisory 轴（依赖/配置/main.py 双改信号）；
  ownership 暴露 `paths_with_policy` 谓词。
- **M-2 PG lane 代码性坏死**：`_pin_env(url)` 未 `__enter__` 就被当
  monkeypatch 用（real lane 一武装即崩）→ 改为 monkeypatch fixture 传递；
  删除死代码 `_pin_env`。残留披露：本地无真实 PG，TEST_POSTGRES_URL
  路径待 real 环境实测（real lane opt-in）。
- **M-3 BENCHMARK_MANIFEST inputs 漏记 algorithm packs** → 补
  `app/lib/gis/algorithms` + 账本刷新（backend_variants 类变更不再对
  staleness 闸失明）。

### MINOR（全部修复）
- m-1 stuck gauge None 时不再伪造 0（诚实缺席 + staleness 讲真话）
- m-2 degraded 死词表 → 延迟阈值判据（llm/worker 1500ms、db/redis 500ms）
- m-3 object_store 非 MinIO 健康路径 404 → degraded（不再假阳性 ok）
- m-4 chaos 注册表保真度 → 纯编排 factory 补 fired 记录、CAS attack 文本
  对齐实际（确定性交错）、测试 docstring 对齐
- m-5 impact lane title 显式披露 tests/** 不在选择面
- m-6 V2 映射单一来源化（护栏测试从 impact.py import _V2_DOMAIN_MAP）

### NIT（全部修复）
- 删除无消费方的 BLOCKING_AXES/ADVISORY_AXES 常量
- main.py trace 中间件 import 改 fail-fast（结构闸已锁存在性）
- as_log_dict 补 span_id
- traceparent flags=ff 保留值拒绝 + 测试
- chaos_v3 死代码 storm() 删除
- （保留）sre_export_duration 暂无生产调用点——注释披露，export 管线
  接入时启用（follow-up）

## Review Round 2（Subagent-B）

（待 R2 完成后补全）
