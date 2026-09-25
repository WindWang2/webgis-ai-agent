# ADR-0214: Map Plan Compiler — MapPlanIR → 有序最小 MapSpec Mutations

- 状态：Accepted（direction F12）
- 关联：ADR-0058（mutation origin/CAS）、ADR-0076/0180（SessionPlan = plan 真相）、
  ADR-0183（mutation 事务/幂等）、ADR-0201（body_to_intent 单一映射）、
  ADR-0205（cartographic grammar）、ADR-0207（measurement semantics）、
  ADR-0212（trace/replay + DecisionRecord）、ADR-0211（export lineage/completeness）
- 勘察/规格：`docs/dev/f12-map-plan-compiler-recon.md`、`docs/dev/f12-map-plan-compiler-decisions.md`

## Context

理解（intent）、capability/recipe、grammar、components、MapSpec、completion 各环
已强（#1479–#1488），但 **planner/LLM 输出到 MapSpec mutation 之间没有确定性编
译层**：各工具（create_thematic_map / apply_template / map_view / visual_healer）
各自拼装引擎 Intent，低层拼装正确性（最小性、排序、一致性、幂等）由调用方分散
承担。缺口：

1. 无统一的中间计划表示 —— MapProductPlan（planner 产物）、GrammarDecision、
   DatasetMeasurementProfile 三个权威输出没有单一投影，多轮对话要"再加一张统计
   图 / 隐藏某层 / 换配色 / 改分类数 / 改标题 / 导出"时只能靠 LLM 重新拼工具调用。
2. 无"同输入同输出"的确定性编译保证 —— mutation 序列的排序、最小 diff、
   幂等键在各调用点口径不一。
3. 无编译回执（compile receipt）—— 编译产物与已提交 mutation、最终 MapSpec
   revision 之间无对账链，desired/current state 无法机械核对。
4. "期望图层/组件确实显示"没有确定性 finalization check（display_confirmation
   只有 render ACK seam，无期望面）。

## Decision

1. **D1 versioned MapPlanIR（refs-only）**：`app/lib/cartography/plan_ir.py`，
   `PLAN_IR_VERSION="1.0.0"`，Pydantic 有界模型族。所有输入一律**引用权威决策
   （ref_id + fingerprint + schema_version）**，禁止复制大 payload：requirements、
   datasets/fields、grammar/recipe/template/manifest authorities、analysis outputs、
   layer intents（含 bounded layer blueprint）、component intents、layout/export
   obligations、final-display obligations、user lock snapshot、evidence。内容寻址
   `ir_id` + `plan_ir_fingerprint`（canonical JSON sha256，复用
   `canonical_decision_json` 口径）。
2. **D2 投影而非推断**：`app/services/map_plan_compiler/projector.py` 从
   MapProductPlan + GrammarDecision + measurement profile + workbench lock 快照
   **投影** MapPlanIR，不重新推断 field semantics / grammar / 任务流程。多轮演进
   走 typed `PlanAmendment`（add chart / set visibility / restyle+reclassify /
   set title / add export / pin zone）→ `amend_plan_ir` 生成 `revision+1` 新 IR
   （`supersedes` 旧 ir_id），amendment 不触碰 user 锁与无关节点。
3. **D3 确定性编译器（唯一 mutation 排序/最小化点）**：
   `app/services/map_plan_compiler/compiler.py` 纯函数
   `compile_plan(ir, current_mapspec, base_revision) → PlanCompilation`：
   - 现状图：复用 `component_graph` 只读投影 + layers/sources 扁平面，**不建第二存储**；
   - diff desired vs current，逐目标产**最小 mutation**：paint 仅变化键合并
     （patch_layer_style）、非 paint 层键（legend_spec/thresholds）以 current+delta
     最小合并 upsert、组件缺失补/差异只 patch 变化字段（patch_component upsert）、
     显式 remove 才删除；
   - 排序 = 4 相位稳定序（新增层 0 → 层表达面修正 1 → 组件补齐/修正 2 →
     显式删除 3），相位内保持 IR 权威插入序（删除最后 ⇒ 中途状态永不引用
     已删对象）；
   - 每步铸确定 `client_mutation_id = pmc.<ir_id>.<step>.<intent>`（引擎
     `c:<id>` 幂等去重 → 重放同编译 = duplicate no-op）。
   同输入（IR fingerprint + base spec fingerprint + base revision）字节级同输出。
4. **D4 编译前 obligations fail-closed**：`obligations.py::check_obligations` 六闸：
   required components ∈ registry 词表、renderer/export 支持、data refs 活性
   （sources/analysis outputs 可解析；新建层无 ref 且目标不在场 = 阻断，
   拒绝产出空 source 数据层）、scale/CRS 提示一致性（advisory）、
   **user lock 冲突（PLAN_LOCK_CONFLICT，阻塞）**。生产路径由
   `lock_snapshot_for` 先读 workbench 锁面喂给本闸（引擎守卫仍是最后
   防线 —— 双保险杜绝"中途拒 → 部分提交"）。blocked 报告含结构化
   reason codes，不产出任何 mutation。
5. **D5 compile receipt + ACK 回链**：`receipt.py` 复用 DecisionRecord 形态
   （内容寻址 digest、有界投影、结构化 reason codes），新增 additive 决策种类
   `plan_compile`。`apply.py` 顺序经 `apply_mutation` 提交（expected_revision
   逐步 CAS，superseded 即中止剩余步骤 → receipt status=`superseded`
   且回执反映会话当前态；>64 步计划的幂等重放受引擎 dedup FIFO（64 条）
   约束，退化为安全 superseded 中止而非 no-op —— 有界披露），
   回执落 map_state `_plan_receipts`（有界环 ≤8，仿 export_lineage /
   `_final_display_ack` 先例）。`receipt_is_stale(receipt, current_fingerprint)`
   判 desired/current 漂移。
6. **D6 确定性 finalization check**：`finalization.py::check_final_display(ir,
   current_mapspec, render_seq, ack)`：期望面 = IR final-display obligations
   （逐层期望可见性 + 必需组件在场且 enabled），实际面 = 当前 MapSpec + 
   `display_confirmation.is_display_confirmed`。输出 confirmed / unconfirmed /
   blocked + expected_vs_actual 逐项对账，无 LLM、无自由文本结论。
7. **D7 生产接线（additive）**：新 `app/tools/map_plan_tools.py` 注册
   `webgis_compile_map_plan`（`map_mutations` 声明面齐全，走既有 dispatch/review
   gate；不绕过 ADR-0068 拥有者）；服务面公开
   `MapPlanCompilerService.project/check/compile/apply/finalize`。不新建第二
   planner runtime，不改既有工具的 mutation 路径。
8. **D8 边界**：Pi 决定任务流程（本编译器不排任务）；IR 不携带数据 payload；
   grammar/semantics 一律引用不重算；MapSpec 单一真相不变（编译器只经引擎
   提交）；user-wins：锁面冲突 fail-closed，amendment/编译不静默覆盖用户显式
   选择。

## Consequences

- LLM/planner 不再承担低层拼装正确性；同输入可 replay、可 stale、可对账。
- 最小 diff 由编译器单点保证并有测试锁定（多轮 amendment 未触碰节点零 mutation）。
- 既有工具路径不受影响（additive）；后续可逐工具迁移到"project → compile → apply"。
- receipt 落 map_state 有界环，容量 O(1) 增长，不引入新存储真相。

## Out of Scope

- 不做用户端新 mutation intent（如"改分类数"用户直提通道）。
- 不改前端；不迁移全部既有工具到编译器（提供公共 API，迁移按方向推进）。
- 不重写 planner / grammar / semantics。
