# Decision Log — Pi-native GIS Harness Kernel + SessionPlan

按时间顺序记录架构决策。格式：ID / 日期 / 决策 / 依据 / 备选与否定理由 / 回滚面。

## D-001 (2026-09-13) 契约载体：扩展现有 SessionPlan，不新建第五套 plan class

- **决策**：host-neutral SessionPlan 契约以 `app/services/session_plan.py` 的 `SessionPlan`（ADR-0076）为载体，additive 扩展；`CanonicalPlan`（`planning/models.py`）保持 legacy 域源真，经 adapter 投影进同一契约。
- **依据**：SessionPlan 已是 Pi 路径生产事实（slot/apply/SSE/GET/前端面板全链贯通，见 recon §3）；任务书 K0 明令「不得先写第五套 plan class」。
- **否定备选**：(a) 新建 `harness_kernel/plan.py` 独立模型 → 产生第二事实源，违反防重复；(b) 直接把 CanonicalPlan 升级为双 host 真相 → Pi 侧 capability-progress 语义（voided/supersede/goal_key）已深度绑定 SessionPlan，迁移成本高且破坏前端冻结契约。
- **回滚面**：新增字段全部有默认值；旧 envelope 反序列化零漂移。

## D-002 (2026-09-13) Runtime 职责边界：GIS 语义归 Kernel，agent loop 归 Pi

- **决策**：`GISSessionRuntime` 只拥有 hydrate → begin_turn → plan update → evidence attach → checkpoint → end_turn 的 GIS 会话生命周期与 SessionPlan 读写；不复制 Pi 的 tool loop/LLM loop/history loop；Pi-only 的 RPC client、dispatch cache、turn token HMAC 留在 bridge/pi 域。
- **依据**：任务书约束 6 + `docs/research/pi-host-seams.md` 的 Shared/Pi-only 分类。
- **回滚面**：runtime 是被调用的 service；wiring 点全部 try/except 降级（沿用 bridge 既有「增值披露绝不阻断」纪律）。

## D-003 (2026-09-13) Step 语义：单层 PlanStep，不建第二 DAG 引擎

- **决策**：SessionPlan 增加有界 `steps` 列表（host-neutral PlanStep：id/goal/tool/tool_binding/status/evidence/deps），由 plan 事件与工具结果驱动；依赖只做语义标注（depends_on 字段 + 就绪投影），完整 ExecutionGraph 调度留方向 5。
- **依据**：`gis_harness/plan_graph.py` 已有类型级 DAG 纯投影；`CanonicalStep` 语法（${stepId} 占位）已在 legacy 域。
- **否定备选**：复用 CanonicalStep 类型本身 → 会把 `planning/models.py`（legacy 域）变成 Pi 路径依赖，import 方向倒置风险；改为在 kernel 域定义最小 step 模型，legacy adapter 负责映射。

## D-004 (2026-09-13) 持久化：复用 session_data_manager + 别名机制；CAS 用 revision guard 模式

- **决策**：SessionPlan 的 revision CAS 与历史快照沿用 PlanStore 的 guard/alias 设计，但实现在既有 `save_session_plan` 路径上（读-校-写置于既有会话锁内），不引入第二个 store 类。
- **依据**：`planning/store.py` 的 revision guard 已验证该模式；session 锁（fail_on_degraded=True）已覆盖 envelope 写路径，锁内 CAS 天然串行。
- **回滚面**：CAS 拒绝写时 log + 返回持久化真相（与 PlanStore 同语义），不抛异常不阻断。

## D-005 (2026-09-13) 事件：保留 session_plan_* 三个冻结事件名，step 级增量用新 additive 事件名

- **决策**：`session_plan_updated/progress/superseded` payload 形态冻结不动；新增 step 级事件走新名字（`session_plan_step`），核心字段与 legacy `plan_step_done` 语义对齐（前端可在同一 reducer 链消费）；不建第二条 stream，全部经既有 rendezvous + TurnEventBuffer。
- **依据**：`use-sse-stream.ts:939-944` 分发链按事件名白名单转发；`session-plan-delta.ts` 是纯 reducer 易扩展。
- **否定备选**：直接把 `plan_ready/plan_step_done/plan_finalized` 发到 Pi 路径 → `events_to_sse` 有显式 ValueError 守卫禁止（ADR-0076 决策），绕过它会破坏「CanonicalPlan 事件名禁用于 Pi」的既有不变量。

## D-006 (2026-09-13) Legacy 适配：投影不反向

- **决策**：K4 adapter 方向为 CanonicalPlan → SessionPlan 单向投影（legacy 引擎在 plan 变更点同步写 SessionPlan）；Pi 路径永不读 CanonicalPlan；不立即删 CanonicalPlan（风险高），保留 thin projection 并在 ADR 标注 deprecated 边界。
- **依据**：任务书 K4「若立即删除风险高，则保留 thin projection 并标 deprecated」；parity test 以语义等价（capability/step 状态集）为准，不要求 LLM 文本一致。

## D-007 (2026-09-13) ADR/占号

- **决策**：新 ADR 占 **0180**（当前最高 0179；已核对 origin 全分支无更高在途占号）。Alembic：本任务不新增表（SessionPlan 存 session_data store），无迁移。
- **依据**：约束 9；`docs/adr/` 末位 0179。

## D-008 (2026-09-13) 恢复语义：幂等由「已确认 side effect 台账」承载

- **决策**：checkpoint/resume 不重放已 succeeded step 对应的 destructive 调用：恢复路径以 SessionPlan steps/evidence（含 bound_ref）为准读回证据；执行层既有 repeat-intercept（executed sets + ToolDispatchService）继续兜底；resume 只重建上下文投影，不自动重执行工具。
- **依据**：任务书 K7「恢复时不得重复执行已确认的 destructive side effect」+ ADR-0022 dispatch cache 语义。
