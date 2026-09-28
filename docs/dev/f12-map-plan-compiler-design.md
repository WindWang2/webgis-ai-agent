# F12 — Map Plan Compiler — Design

- Date: 2026-09-26（实现） / 2026-09-27（本文为合并后补记的 design，G06 文档债补齐）
- ADR: `docs/adr/0214-map-plan-compiler.md`
- Recon: `docs/dev/f12-map-plan-compiler-recon.md`；Decisions: `docs/dev/f12-map-plan-compiler-decisions.md`；Review: `docs/dev/f12-map-plan-compiler-review.md`
- 实现基线: `origin/master @ 9e1ad229`；Merge: PR #1497（`dc86346d`）
- 状态: 已合并（merged）。本文全部内容以合并后代码为事实源，可 rg 核对。

## 目标与红线

LLM/planner 的地图意图到 MapSpec mutation 之间没有统一中间表示：各工具自行拼
Intent，无最小化/排序/diff 层，无 compile receipt，无可回放的编译审计。F12 补
**versioned MapPlanIR → 确定性编译器 → 有序最小 mutation 序列 → CompileReceipt →
finalization 对账** 这一条下游编译层。红线：不造第二 planner（消费
`MapProductPlanner` 的 `MapProductPlan`，任务流程仍归 Pi）；不造第二写路径
（提交只经 `MapSpecLifecycleEngine.apply_mutation` 的锁+CAS+事务+checkpoint+幂等
通道）；不重推断语义（grammar/measurement 一律引用 fingerprint）。

## Module map

| 模块 | 状态 | 内容 |
|---|---|---|
| `app/lib/cartography/plan_ir.py` | **new** | versioned/有界 IR 本体：`MapPlanIR`（:300）、`LayerBlueprint`（:180，表达面 token 双闸：键数 ≤24 / 8KB）、`UserLockSnapshot`、`spec_doc_of`（会话态归一化：`state["mapspec"]` 嵌套/扁平双兼容）；全部 refs-only（ref_id+fingerprint+schema_version），payload 走私构造期拒绝 |
| `app/services/map_plan_compiler/projector.py` | **new** | `project_plan_ir`（:67，MapProductPlan+GrammarDecision+measurement profile+锁快照 → IR 纯投影）；`amend_plan_ir`（:293，消费 typed `PlanAmendment`，锁目标/未知目标 fail-closed） |
| `app/services/map_plan_compiler/plan_amendment.py` | **new** | amendment 契约：`PlanAmendment`（封闭词表 8 种） |
| `app/services/map_plan_compiler/obligations.py` | **new** | 编译期六闸 fail-closed：`check_obligations`（:119）；`PLAN_LOCK_CONFLICT`（workbench 锁提前拦截，双保险于引擎 `guard_locked_partitions`）、`DATA_REF_UNRESOLVED`（新建层 ref 必须可解析，杜绝空 source 层）、`COMPONENT_UNKNOWN_TYPE`；组件词表剔除 `_ENGINE_UNSUPPORTED_COMPONENTS = {"basemap"}`（:51） |
| `app/services/map_plan_compiler/compiler.py` | **new** | `compile_plan`（:230，纯函数 diff → 有序最小 mutations，同输入字节级同输出）：paint 键级 delta→patch_layer_style、非 paint 键最小合并 upsert、双变化合并单笔；确定性 `client_mutation_id = f"pmc.{ir.ir_id}.{i:02d}.{m.intent}"`（:402）；相位序 present(0)→layer patch(1)→component(2)→removal(3)；`DisplayExpectation`（:79）/`PlanCompilation`（含 `display_expectations`，:88）随编译下行 |
| `app/services/map_plan_compiler/apply.py` | **new** | `mutation_to_engine_intent`（:46，与 `intent_codec.body_to_intent`+`mutate_component` 语义一致）；`apply_plan`（:84）：逐步 CAS（step i 期望 `base_revision + i - 1`，:116），任何 superseded 立即中止（陈旧计划绝不静默续跑）；`mutation_id = f"c:{client_mutation_id}"`（:131）命中引擎幂等去重，同编译重放 = duplicate no-op |
| `app/services/map_plan_compiler/receipt.py` | **new** | `CompileReceipt`（:62，身份=(ir, base_revision, base_fingerprint, compile_digest, plan_revision)，created_at/applied 不入身份）；applier 终态写入 map_state `PLAN_RECEIPTS_KEY = "_plan_receipts"`（:39，≤8 FIFO 环，零新存储真相）；存储失败只告警，重试走幂等去重 |
| `app/services/map_plan_compiler/finalization.py` | **new** | `check_final_display`（:70，期望显示面 vs 实际 spec + `display_confirmation` ACK 纯函数对账） |
| `app/services/map_plan_compiler/service.py` | **new** | `MapPlanCompilerService`（:35，编排面）：`lock_snapshot_for`（:39，读引擎 `locked_layer_ids_of`/`locked_component_ids_of`，review P1-1 修复 —— 生产工具路径必须在 project 前喂锁，否则前置闸空转、引擎中途拒产生部分提交）；`spec_doc_of` 消费见 :49 |
| `app/lib/runtime/decision_record.py` | extended | additive kind：`DECISION_KIND_PLAN_COMPILE = "plan_compile"`（:36）—— compile receipt 复用内容寻址 decision 形态 |
| `app/tools/map_plan_tools.py` | **new** | 工具面（`app/tools/__init__.py` 加法 import 注册）：`layer_bindings` 参数让 LLM 做绑定选择、编译器验活性（review P1-2 修复） |

## 规范语义（normative）

1. **IR 全 refs-only**：数据/语义不入 IR；`LayerBlueprint` 表达面有界（键数 ≤24 /
   8KB）；amendment 封闭词表，锁目标/未知目标构造期抛 `ValueError`。
2. **最小 mutation 语义**：paint 只带变化键（整包 overwrite 被 mutant 测试锁定为
   红）；legend/label/threshold 等非 paint 键变化走 current+delta 最小合并 upsert；
   可见性读语义 = schema `visible is False` ∨ `layout.visibility=="none"` 双形态
   （upsert 写前者、presentation patch 写后者）。
3. **确定性**：`compile_plan` 同输入字节级同输出（含 current dict 键插入序不变性，
   review P2-6 补测）；`compile_id = pmcc-<digest[:12]>`（compiler.py:467）。
4. **可回放/可 stale**：`pmc.` 幂等键同编译重放 = duplicate no-op；逐步 CAS 链上
   任何 superseded 立即中止并采信引擎锁内一致读回执（review P2-3）。
5. **单一提交通道**：全链唯一提交经 `apply_mutation`；receipt/decision 只做审计
   投影，绝不成为第二真相。

## 测试计划（F12 专属 9 文件，78 passed）

| 文件 | 覆盖 |
|---|---|
| `tests/cartography/test_plan_ir_v1.py` | IR 契约/有界性/refs-only |
| `tests/cartography/test_plan_projector_v1.py` | plan→IR 投影、amendment 词表、锁 fail-closed |
| `tests/cartography/test_plan_obligations_v1.py` | 六闸（锁阻塞/DATA_REF_UNRESOLVED/词表） |
| `tests/cartography/test_plan_compiler_v1.py` | 最小 delta/相位序/确定性（含键插入序） |
| `tests/cartography/test_plan_apply_v1.py` | 逐步 CAS/superseded 中止/幂等重放 |
| `tests/cartography/test_plan_receipt_v1.py` | receipt 身份稳定性、`_plan_receipts` 环 |
| `tests/cartography/test_plan_finalization_v1.py` | 显示期望对账 |
| `tests/cartography/test_map_plan_tools_v1.py` | 工具全链（含锁 ⇒ blocked 零提交、无空 source 层落盘） |
| `tests/cartography/test_plan_compiler_corpus_v1.py` | 端到端 corpus |

兼容性邻域：manifest/binding conformance 19 passed；decision_provenance/replay
roundtrip 46 passed；lifecycle 引擎回归 29 passed。review 终裁 READY-AFTER-FIXES
→ 2×P1 + 7×P2 全部清偿（详见 review 文档）。
