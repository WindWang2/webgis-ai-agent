# PR_BODY（草稿）— 01｜Pi 自适应执行策略：可观测、可预算、可安全恢复

## 标题
feat(pi-gis): Pi 运行策略与 Provider 服务质量闭环——健康感知路由、失败观测、attempt 链与预算投影（GOAL-20261127-01）

## Baseline / Pi pin
- 基线：`origin/master` = `f34cb69dd`（启动时与 PR 前两次核验一致；无新合并、无开放 PR）
- vendor/pi：`ef4a1cfcca62…` = 上游最新正式 tag **v1.6.1**（ls-remote 实查无更新）→ **gitlink 不动**，协议面零变更
- 兼容矩阵增补：docs/PI_UPSTREAM_COMPAT_MATRIX.md §7（v1.6.1 既有能力的新增消费，无协议 floor 变化）

## 实际新能力（全部经真实入口）
1. **Pi inherit 路径接路由权威**（G1/B1）：`RouteRequest.candidate_models`
   显式授权候选池（= 管理员会话目录；descriptor ≠ 授权边界不逃逸）；
   `model_runtime/turn_strategy.py` 复用 ModelRouter 硬资格+健康护栏+理由码；
   用户显式选择零行为变化（撤销/不可用仍 typed 拒绝）；kill-switch
   `PI_MODEL_HEALTH_ROUTING=0` 回落原行为。新回执状态 `inherit_routed`
   （健康降级应用 ≠ 继承默认），理由码有界进 SSE `route` 键与账本。
2. **Provider 失败回写健康表**（G2/B2）：扩展 after_provider_response
   stderr 行升级为结构化 `[provider-obs]`（精确 status/retry_after，隐私
   纪律不变）→ `parse_provider_observation` 纯解析 → 注入式 sink → 桥回写
   `model_runtime/health`（与 ChatEngine observe_outcome 同一权威）；
   auto_retry_start/end 事件为结构化兜底通道（文本保守分类）+ attempt
   计数 + 重试恢复成功回写；turn settle completed → 成功回写。
3. **Attempt 链持久关联**（G3/B3）：`chat_turn_runtime` additive 三列
   （provider_retry_attempts / route_reason_codes / last_provider_failure_kind，
   迁移 g5201 单 head；不建新账本表），随既有 (turn_id, call_seq) usage
   明细与 JobOrigin 关联共聚一行。
4. **Governor 真实 usage 回流**（B4）：turn 结算把真实 output tokens
   回流 governor session 账本（`record_output_tokens`，全 scope 求和，
   与 context keep-latest 语义分叉有据）——governor 此前只收组装估算。
5. **Budget 投影契约修复**（B6）：`GET /sessions/{id}/usage` 补 `budget`
   块（cap/token_cap/used/remaining，与 budget_admission 同一权威）——
   前端 `usage.budget?.token_cap` 读取不再恒 undefined。
6. **运行质量聚合投影**（WP4）：`GET /chat/sessions/{id}/runtime`
   （owner-scoped）= 账本 turn（含 attempt 链）+ 路由健康表白名单投影 +
   vendor `get_session_stats` 对账块（不可达如实 available=false，D6；
   会话级聚合经 _isolate_vendor_session 保证无跨 owner 泄漏）。前端
   session-model-selector 渲染预算块 + 最近 turn 运行事实（双语）。

## 去重矩阵摘要
- 复用不重建：ModelRouter/health/provider 分类器/usage 账本/governor/
  pi_durable_cancel/event_resume/ TurnContextReceipt（typed indeterminate
  既有承载，不新增词表）。
- covered-by-other：#2283 决策辅助/schema 卫生（回归保护，未触碰语义）；
  02–05 线领域交付。详见 planning/GOAL-20261127-01-PI-ADAPTIVE-EXECUTION/
  DEDUP_MATRIX.md（B1–B10 缺口 × 处置 × path:line）。

## 测试与性能数据（本机实测，命令见 TEST_MATRIX.md）
- 真实 vendored RPC 故障矩阵（mock HTTP provider × v1.6.1 rpc-entry）：
  F1 429×2→恢复（obs/retry/usage/health 四链证据）、F2 持续 429 耗尽
  （诚实失败：stop=error、cost unknown 不冒充免费）、F3 5xx→
  provider_unavailable 进退避、F4 401 观测诚实（unknown 词表）、F5
  stream-cut 恰一次终态。5 passed。
- 单测：策略 11 / 观测 11 / attempt 链 7 / runtime 投影 2 / 安全负例 5
  全绿；G37 套件 49→60 passed（无回归）；vendor contract 18 passed；
  governor 全包 209 passed（含一处陈旧测试对齐，见下）。
- 压测：pool soak 2 workers / 4 sessions / 40 turns（随机断连/取消风暴）
  不变量 PASS——0 lease 泄漏、0 幽灵 turn、0 会话越序；wait p50=53.4ms
  p95=87.8ms。
- 前端：tsc --noEmit 干净、eslint 干净。
- pi_doctor 六域 PASS（native）；Ruff（本线触及文件）零诊断。

## 预存失败与基线遗留（诚实披露，非本线回归）
1. **master 预存 P1：app 启动即失败**（strict runtime manifest，14 个
   dangling capability_id：diagram_symbol/scenario_harness/paper_scale
   工具声明引用未注册能力；引入自 #2274/#2275/#2287 合并内容，#2288
   整合遗漏；相关模块与 origin/master 零 diff → master 同样失败）。
   本线**不修**（04/05 线能力语义域）；受影响的 opt-in journey 以文档化
   开关 `GIS_MANIFEST_STRICT=0`（runtime_manifest.py:645）降级运行；
   建议能力归属线尽快收口（启动门是 fail-fast 设计，默认环境当前无法
   完成 app 启动）。
2. G37 全栈 journey（PI_JOURNEY_E2E=1）：J1/J3 失败——归因运行中
   （临时 detached master worktree 对照；结果回填）。
3. test_pi_stub_llm_journeys：预存失败（#2283 REVIEW 已披露同失败）。
4. 前端全量 vitest 50 failed：干净主 worktree 同样失败（预存）。
5. `tests/governor/test_backpressure_fairness.py::test_cumulative_…`：
   基线预存红（测试未跟上 24655bf3a 的 keep-latest 语义变更）——本线
   对齐已文档化实现语义并补 output 求和断言（非弱化）。
6. 真实商用 provider 无凭据：真实 LLM 付费调用未实测（沿用 #2283 披露
   口径；mock HTTP + 真实 vendored RPC 覆盖）。

## 迁移 / 兼容 / 回退
- 迁移 g5201 additive（三列 server_default、CheckConstraint 重建含
  batch 兼容 sqlite/pg；单 head，alembic 校验过）。
- 协议 floor 不变（v1.5.1）；vendor 不动；ChatEngine 路径零行为变化
  （candidate_models 缺省 None 走原池派生）。
- 回退：`PI_MODEL_HEALTH_ROUTING=0` 关策略；观测/回流/投影均为增值面
  fail-open；kill-switch `GOVERNOR_TOOL_SURFACE`/`MODEL_ROUTER_ENABLED`
  语义未触碰。

## 冲突边界
- 触及面：model_runtime（routing/turn_strategy/session_model_service/
  usage_ledger）、agent_pi_bridge、pi_rpc_client、extensions/index.mjs、
  governor/session_budget、db_model+迁移、session_model_views+schema、
  前端 selector+双语、tests。
- 与 02–05 线无文件交集（PR 前已核 changed files）；不占 ADR 编号
  （沿用既有 ADR 0301 语境，新增文档只落 compat matrix §7 与 planning）。

## R1 / R2
- **R1 独立审查**（非作者子代理，逐行 + vendor 源码交叉）：1×P1 + 4×P2
  + 8×P3 → 全部 P0/P1/P2 修复（vendor stats 绑定校验+白名单投影、双通道
  健康惩罚去重、归属锚复位、恒真断言、非流式 attempt 链同构、governor
  回流幂等标志、route codes 整条取舍等）；3 项记录性披露（进程级健康
  快照跨租户计数等）。REVIEW_R1.md。
- **R2 复审**：R1 关闭证据逐项复核；关键门修复后重跑全绿（fixture 矩阵
  5 passed / G37 journey 35.51s PASS / affected 114 + bridge+governor
  281 passed）；基线冲突复核（master 未动、0 开放 PR、vendor pin 零 diff）。
  REVIEW_R2.md。
- 审查过程透明记录：R1 期间曾引入 `_bounded_route_codes` 缺 `self.` 缺陷，
  被既有 g37 settle 测试捕获 → 修复回绿（测试链价值实证）；早期 journey
  「失败」经三向对照定性为主 Agent 并发跑两个 journey 的共享
  `.pi/agent` 隔离违规，非代码回归（串行纪律已入台账）。
