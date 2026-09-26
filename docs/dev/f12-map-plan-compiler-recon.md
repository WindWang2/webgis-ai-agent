# F12 — Map Plan Compiler 勘察报告（Recon）

- 基线：`origin/master = 9e1ad22907e99cd7b4721153294448ce6496c717`（2026-09-24 05:00 +0800，merge #1494）
- Worktree：`../wt-webgis-f12-map-plan-compiler-20260926-9e1ad229`
- Branch：`zcode/f12-map-plan-compiler-20260926-9e1ad229`
- 勘察方式：只读（git fetch + gh pr/issue + 代码勘察 subagent，2026-09-26）

## 1. 上游状态

- **open PR**：仅 #1489（dependabot docker node 22→25），与本方向零交集。
- **最近 merged**：#1490–#1496 全部为 dependabot 依赖升级；最近功能性大波次为 **#1479–#1488**（2026-09-21 合入，ADR-0204..0213），全部已在基线内。
- **open issues**：#1436（i18n）、#1377（audit 延期跟踪）——与本方向无直接交集。
- **并行 worktree**（本机 15 路并发中的邻位，PR 期需去重说明）：F08 workflow-resource-scheduler、F09 trace-replay-oracle-v3、F11 template-component-composition、F13 mapspec-render-runtime-data-plane、F15 visual-observation-repair。
  - 避让纪律：F12 只新增 `app/services/map_plan_compiler/` + `app/lib/cartography/plan_ir.py`，不重排既有模块内部；工具注册走新模块 `app/tools/map_plan_tools.py` + `app/tools/__init__.py` 一行 import。

## 2. 权威契约盘点（全部仅存在于 origin/master，本地主 checkout 落后 187 commits，已通过从 origin/master 派生 worktree 解决）

### MapSpec 真相（必须绕开，只经其通道提交）
- Schema：`app/lib/cartography/mapspec_schema.py`（KNOWN_VERSIONS 1.0–1.4，`MapSpecDocument`:497，`MapSpecLayer`:292，`MapSpecComponent`:391）
- Mutation 契约：`app/schemas/mapspec_mutation_schema.py`（14 个 Body discriminated union:179，`MutationApplyResponse`:207，`client_mutation_id` 幂等键:24）
- Body→Intent 单一映射：`app/services/mapspec/intent_codec.py::body_to_intent`（ADR-0201）
- 引擎：`app/services/mapspec/lifecycle_engine.py`（25 个 Intent 类:350-595，`apply_mutation`:1445 = 锁+CAS+事务+checkpoint+幂等去重，`user_lock_pin_hit`:958，`guard_locked_partitions`:830）
- revision/ACK：`mutation_revision`（单调 CAS）+ 快照保留 20 代（store.py）；前端游标 session-cursor。
- 指纹：`app/lib/cartography/quality_loop.py::cartographic_fingerprint`（`carto-sha256:...`）

### Planner / plan 语义（可用作输入，不得替代）
- `app/services/gis_harness/planner.py::MapProductPlan`（:591，plan_id=(query,recipe_id) sha1 :642，manifest_fingerprint 判 STALE_PLAN :626）+ `MapProductPlanner`（纯函数，memo 化）
- `plan_candidates.py` / `plan_graph.py` / `plan_runtime.py::compute_plan_fingerprint`；`app/services/planning/models.py::CanonicalPlan`；SessionPlan（ADR-0076/0180 = plan 真相）

### Grammar（方向4 已完成，勿重复）
- `app/lib/cartography/grammar_solver.py`：`solve_grammar(GrammarRequest)→GrammarDecision`（fingerprint 进工件:192）；`GrammarDecision.component_obligations` / `layout_participants()` / `audit()`
- `grammar_types.py`（GRAMMAR_VERSION="1.0.0"）、`visual_variables.py`、`scale_rules.py`

### GIS Semantics（方向2 已完成，勿重复）
- `app/lib/gis/measurement.py`（DatasetMeasurementProfile/FieldSemantics）、`field_resolver.py`（parse_measure_phrase/resolve_measure_field 双语）、`scale_semantics.py::display_hints`

### Component graph（已有正式概念，必须复用）
- `app/lib/cartography/component_graph.py`（ComponentNode:62/ComponentLink:74，只读投影，"单一事实仍是 MapSpec"）
- `component_composer.py::required_components_for`（:357）；harness 侧 `gis_harness/components.py`（21 种 CartographyComponent）

### Verify/Repair（方向5 已完成）
- `unified_findings.py`、`repair_planner.py`、`completion/pipeline.py::run_map_finalization`、`visual_healer.py`（ApplyVisualHealPatchIntent 通道）
- **最终显示确认 seam 已有**：`app/services/gis_harness/display_confirmation.py`（FINAL_DISPLAY_ACK_KEY，auto/required 模式，`is_display_confirmed(render_seq)`）——F12 finalization check 直接消费

### Trace/Replay/Decision receipt（方向8 已完成）
- `app/lib/runtime/gis_trace.py`（Stage 1-18 含 MAP_MUTATIONS）、`app/lib/harness/replay/`（schema v1 + recorder/replayer/drift/roundtrip）
- `app/lib/runtime/decision_record.py`（内容寻址 decision_id，kind 封闭词表 additive）——compile receipt 复用此形态

### Export/Completeness（方向10 已完成）
- `app/services/export_lineage.py`（receipts ≤8 有界环）、`product_completeness.py`、`completion/contracts.py::derive_product_verdict`

## 3. Overlap / Already Done / Still Missing / Must Not Touch / Integration Seams

| 列 | 内容 |
|---|---|
| **Overlap**（命名/概念撞车，需避开混淆） | ① 4 个既有 "compiler"（前端 mapspec-compiler、mapspec/coordinator.py::CompileCoordinator、gis_harness/product_compiler、gis_situation/compiler）→ F12 落位 `app/services/map_plan_compiler/` 新目录 ② component graph（复用，不重建）③ fingerprint 家族（carto-sha256/manifest/GrammarDecision.fingerprint/decision_id——receipt 复用不新造）④ "plan" 三层已有概念（MapProductPlan/plan_graph/SessionPlan）→ F12 定位为**下游编译层** |
| **Already Done**（勿重复） | MapSpec schema+迁移；mutation 引擎（CAS/事务/幂等/锁守卫）；recipe 166 条；capability ABI v2；方向2 semantics；方向4 grammar；方向5 闭环 G1-G4；方向8 replay+DecisionRecord；方向10 export lineage+verdict；component graph V7；display confirmation seam；user 锁守卫 |
| **Still Missing**（=F12 空间） | ① versioned **MapPlanIR** 本体（LLM/planner 意图 → 类型化 refs-only IR，现无统一中间表示）② **确定性编译器**（IR → component graph diff → 有序最小 mutation 序列；现在各工具自行拼 Intent，无统一最小化/排序/diff 层）③ **compile receipt**（digest+decision_id+目标 revision，可 replay/可 stale，MapSpec ACK 回链）④ 多轮 patch 的 IR amendment 机制 ⑤ 编译期 obligations/conformance 单点 ⑥ 确定性 finalization check（期望可见性 vs 实际 spec/ACK 对账） |
| **Must Not Touch** | `lifecycle_engine.py`（只 additive，禁重构）；`mapspec/store.py`（存储顺序契约）；`agent_pi_bridge.py`（89 改动/2月）；`api/routes/chat.py`；`chat/execution_engine.py`；`tool_dispatch_service.py`（ADR-0068 唯一拥有者）；`semantic_checks.py`；MapSpec KNOWN_VERSIONS 语义；ADR 已冻结词表 |
| **Integration Seams** | ① 输入：`MapProductPlan` + `GrammarDecision` + `DatasetMeasurementProfile` + recipe/template + workbench user locks → projector ② 输出：编译 mutation → `MapSpecLifecycleEngine.apply_mutation`（client_mutation_id 幂等 + expected_revision CAS）③ 审计：`decision_record` 新增 `plan_compile` kind（additive）+ trace chain MAP_MUTATIONS 段 ④ 回链：map_state `_plan_receipts` 有界环（≤8，仿 export_lineage/`_final_display_ack` 先例）⑤ 工具面：新 `app/tools/map_plan_tools.py`（@tool 注册，additive import） |

## 4. master 已知状态

- 基线 checkout 干净（worktree 直接自 origin/master），无本地已知失败需隔离；本地测试纪律 `pytest -n 0`，分层跑（新契约 → 邻域 → 一次较宽回归）。

## 5. 用户锁/多轮路径事实

- 锁面在 workbench state（`lockedLayerIds`/`lockedComponentIds`），MapSpec 本体无 user_locked 字段；引擎 `guard_locked_partitions` 已强制 agent 绕锁 → compiler 在 obligations 层提前 fail-closed（PLAN_LOCK_CONFLICT），双保险。
- "改分类数 k" 目前无用户直提 mutation（仅 agent 工具/视觉自愈）——F12 以 IR amendment + patch_layer_style(paint 合并) + layer 级 legend_spec 合并通道承接，不新增用户端 intent（不扩 scope）。
