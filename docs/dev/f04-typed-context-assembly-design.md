# F04 — Typed Context Assembly & Budget Governor：设计与实现说明

基线 `9e1ad229`；ADR 依据：0208 D2（本方向是其 deferred seam）、0206（scope/sensitivity 单一实现）、0182（governor rg.v1）、0180/0183/0190（situation/gis_memory/proactive）、0212（decision record）。

## 1. 架构

```
chat.py (2 个 Pi 调用点，最小 hunks)
  │  只传结构化身份（context_org_id/project_id/user_id/query_text）+ env_block
  ▼
agent_pi_bridge.prompt()/stream_prompt()        ← 兼容 kwargs（additive）
  │  turn_id 由 bridge 铸造并下传
  ▼
pi_turn_context.bind_turn_prompt()              ← 兼容 adapter
  │  GIS_TYPED_CONTEXT_ASSEMBLY=1（默认）        │ =0 或 typed 异常
  ▼                                             ▼
context_assembly.assembly.assemble_turn_context   legacy_bind_turn_prompt（pre-F04 字节等价）
  1 fetch_shared_facts：plan/mapspec/map_state 各取一次 + build_turn_context 投影
  2 wave A：≤8 providers 有界并行（每 provider 延迟预算，超时诚实 skip）
  3 wave B：gis_context card（向 wave A 字符预算让位，include_reuse 去重联动）
  4 scope gate：ADR-0206 SensitivityClass 语义；caller-injected 项豁免（route 层已鉴权）
  5 fence+scrub：marker 中和 / wrap fence / secret 双层消毒
  6 dedupe：指纹精确去重 + authority stale 丢弃（确定性 winner 序）
  7 allocate：复用 context_budget.plan_budget 分池；floor 截断 / honest omission
  8 render：attach_turn_context 既有顺序（cartography 五块 inline join 字节兼容）
  9 receipt：digest settle-once + decision_record 发射 + governor plan/settle
```

## 2. 关键契约

- `ContextItem`（ctx.item.v1）：domain/scope/revision/freshness/sensitivity/est_tokens/priority/floor/evidence_ref/fingerprint；`extra="forbid"`；frozen；工厂 `bounded_item` 强制 per-domain char cap。
- `ContextDomain`（14 值封闭词表）→ Pool（复用 `context_budget.Category`，不新增类别）→ render rank（与 legacy attach 顺序一一对应）→ fence mode（internal/wrap/none）。
- `ContextProvider` 协议：`enabled()/collect()`，fail-open → `ProviderReport`（skipped_reason/error/latency）。全部 provider 只包装既有单一渲染源 builder（verdict_summary、context_assembler._build_project_*、gis_memory.queries、gis_context.hotpath、format_session_plan_projection、v6_context_blocks、context_policy.tombstone）——零渲染逻辑复制，零第二 store。
- `TurnContextRequest`（ctx.req.v1）：身份 + env_block（situation 有 advance 副作用，必须 caller-injected）+ legacy_cartography_block（显式输入，注入一次并抑制派生 provider）。

## 3. 预算语义（诚实优先）

- **observe-first**：窗口未知且未显式 `GIS_CONTEXT_BUDGET_ENFORCE=1` → 只记录 `pool_over:*`，不丢弃任何块（pre-F04 默认路径行为不变）；窗口已知或显式 enforce → 分池 cap 生效。
- floor 契约（verdict/plan/environment）：超池压力时截断到 floor（有界保底），绝不整块丢。
- global cap：yield 序 = priority ↓ → cost ↓ → item_id（全序，确定性）。
- estimate/actual：同一 CJK-aware `_estimate_tokens`；planned=allocator 结果，actual=最终渲染串估算；governor `admit_and_reserve`（observe-first，REJECT 只记录不阻断——context 不可延迟）+ `complete()` settle；有 reservation 时 actual 由 `ledger.release` 记账（complete 内部），无 reservation 走 R12 `record_context_tokens` —— 单一记账，无双计（有测试钉住）。

## 4. 安全边界

- 控制面 marker（ACTIVE_TOOLS/TURN_CONTEXT）在数据文本中一律中和（两族 marker，幂等）。
- wrap-fence（GIS_MEMORY）：escape-then-bound 整块包裹 `<untrusted_gis_memory>`；internal-fence 域由其单一渲染器负责值级栅栏（契约测试钉住两类行为）。
- secret 双层消毒：key-part 赋值模式 → 残余 token 形态（sk-/AKIA/ghp_/xox/bearer）；干净文本字节不变（有测试）；user message / 控制面永不消毒。
- user message 只做 marker 中和（legacy 同款），其余不改动。

## 5. 性能

- 每 turn I/O 身份：plan×1、mapspec×1、map_state×1（pre-F04 是 mapspec×2/map_state×2/plan×1）；HarnessTurnContext 用已加载 plan 直接 `build_turn_context`（第二读被消除）。
- provider 并行受 `GIS_CONTEXT_PROVIDER_PARALLELISM`（默认 6，上限 8）+ 每 provider `GIS_CONTEXT_PROVIDER_BUDGET_S`（默认 2.5s）约束；超时 → receipt skip reason，绝不阻塞 turn。
- token 估算 memo 化（既有 `_estimate_tokens` 缓存复用），receipt/trace 只携带指纹不携带内容。

## 6. 退役边界

- `legacy_bind_turn_prompt`（pi_turn_context.py）：pre-F04 路径保真保留；三个空会话字节等价测试钉住（无块/带 cartography 块/带 env 块）。等价范围 = 良性内容：敌意控制 marker 形态在两条路径上都被中和（typed 额外中和 TURN_CONTEXT 族），wrap-fence 域（GIS_MEMORY）是 typed 路径声明的加固，非字节等价项。
- `context_assembly/legacy_compat.py`：cartography 五块 join 的字节契约归此一处；Pi route 完全切换后删除本文件 + 三个等价测试即可退役。
- `chat._build_cartography_turn_context`：薄包装（委托 legacy_compat），保留给测试/外部调用。

## 7. Out of Scope

- legacy 引擎（ChatContextAssembler）的 provider 化改写（其自身有 measure-advise 预算体系；迁移是独立方向）。
- `gis_trace.Stage` 词表扩展（1-18 封闭且 F09 PR 占用中）；receipt 以 digest + decision_record（ADR-0212）进入 replay/trace，Stage 行后续追加。
- `app/lib/redaction.py`（F09 所有）；F09 落地后 `fence.scrub_secrets` 可切换为其实现（接口已收敛在 `scrub_item`）。
