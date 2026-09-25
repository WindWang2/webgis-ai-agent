# F12 Map Plan Compiler — 设计决策（ADR-0214 配套）

基线：`origin/master = 9e1ad229`；Worktree：`wt-webgis-f12-map-plan-compiler-20260926-9e1ad229`。

## 架构总览

```
中文需求/多轮指令
   │ (LLM 只做意图与选择)
   ▼
resolve_intent_adaptive (规则快路径)  ──►  MapRequestIntent
   ▼
MapProductPlanner.plan_from_intent  ──►  MapProductPlan（权威 plan）
GrammarDecision (方向4)   DatasetMeasurementProfile (方向2)   workbench 锁快照
   └───────────────┬────────┴───────────────┬────────────────┘
                   ▼  project_plan_ir（纯投影，refs-only）
             ┌──────────────┐
             │   MapPlanIR   │  versioned / content-addressed / 有界
             └──────┬───────┘
                    │  amend_plan_ir（typed PlanAmendment × N → revision+1）
                    ▼
             check_obligations（六闸 fail-closed）
                    ▼
             compile_plan（纯函数 diff → 有序最小 mutations，同输入字节级同输出）
                    ▼
             apply_plan（逐步 CAS 经 MapSpecLifecycleEngine.apply_mutation）
                    ▼
             CompileReceipt（decision_record plan_compile + _plan_receipts 有界环）
                    ▼
             check_final_display（期望显示面 vs 实际 spec + render ACK）
```

## 关键决策记录

| # | 决策 | 理由 |
|---|---|---|
| D1 | IR 全部 refs-only（ref_id + fingerprint + schema_version），`LayerBlueprint` 表达面 token 双闸（键数 ≤24 / 8KB） | 数据/语义不入 IR；payload 走私构造期拒绝 |
| D2 | amendment 封闭词表 8 种；锁目标/未知目标 fail-closed 抛 ValueError | user-wins 与最小演进由构造保证 |
| D3 | 最小 mutation 语义：paint 键级 delta → patch_layer_style；legend/label/threshold 等非 paint 键变化 → current+delta 最小合并 upsert（引擎唯一 durable 通道）；双变化合并为单笔 | 最少 mutation 数；不整包 overwrite |
| D4 | 相位排序：present(0) → layer patch(1) → component(2) → removal(3)；同相位保 IR 序 | 删除最后 = 中途状态永不引用已删对象 |
| D5 | `client_mutation_id = pmc.<ir_id>.<step>.<intent>`；引擎 `c:<id>` 幂等去重 | 同编译重放 = duplicate no-op；replay 对齐键 |
| D6 | apply 逐步 CAS（expected = base_revision + step - 1），superseded 立即中止 | 陈旧计划绝不静默续跑（fail-closed） |
| D7 | 可见性读语义 = schema `visible is False` ∨ `layout.visibility=="none"` | upsert 写前者、presentation patch 写后者，双形态并存（引擎实测） |
| D8 | receipt 身份 = (ir, base_revision, base_fingerprint, compile_digest, plan_revision)，created_at/applied 不入身份 | 同编译同 receipt_id（重放可对齐） |
| D9 | receipt 落 map_state `_plan_receipts`（≤8 FIFO）+ decision_record additive `plan_compile` kind | 零新存储真相；复用 export_lineage/`_final_display_ack` 先例 |
| D10 | 会话态归一化 `spec_doc_of`：`state["mapspec"]` 嵌套或扁平 doc 双兼容 | 引擎真值嵌套；纯函数测试面扁平 |
| D11 | 编译期 `DisplayExpectation`（layer_id 落定）随 compilation/receipt 下行；finalization 纯函数对账 + display_confirmation ACK | DoD-7/DoD-4 的机械证明面 |

## 与既有机制的边界（单一真相）

- **不造第二 planner**：编译器消费 MapProductPlan，任务流程仍归 Pi。
- **不造第二写路径**：提交只经 `apply_mutation`（锁+CAS+事务+checkpoint+幂等）。
- **不重推断语义**：grammar/measurement 一律引用 fingerprint，不重算。
- **不动热区**：`lifecycle_engine.py` / `agent_pi_bridge.py` / `chat.py` / `execution_engine.py` 零改动；唯一既有文件改动 = `decision_record.py`（additive kind）与 `tools/__init__.py`（additive 注册行）。

## 已知边界 / 后续方向

- `classification`（k/method/palette）作为 blueprint 元数据随 legend_spec 合并提交；breaks 数值重算（classify 模块消费）留给调用方/后续接线，编译器不重推导。
- 用户端"改分类数"直提 mutation intent 未新增（Out of Scope，见 ADR）。
- 导出义务目前做到 obligations 闸 + receipt 披露；导出产物级 ACK 对账待方向10 receipt 面扩展。
