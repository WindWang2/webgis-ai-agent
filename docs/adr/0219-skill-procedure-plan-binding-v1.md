# ADR-0219: Skill Procedure → SessionPlan/PlanGraph 约束与证据绑定 V1

- 状态: Proposed
- 日期: 2026-10-16
- 线: harness/skill-procedure-plan-binding-v1
- 关联: ADR-0182（GIS Skill / Procedure Library）、ADR-0076（SessionPlan
  truth）、ADR-0085/0087（PlanGraph / action intent）、ADR-0180（Harness
  Kernel）、ADR-0183（Goal Satisfaction）、ADR-0210（Pi ownership）、
  ADR-0204（Capability ABI bind）

## 1. 背景与问题

ADR-0182 建立了 Skill Library：`SkillContract` 携带声明式 `SkillProcedure`
（步骤/决策/证据/回退），`SkillResolver`/`SkillPolicy` 在 plan seam
（`bind_skill_guidance_at_plan_seam`）选出技能并产出
`SkillPlanningProjection`。但 procedure 到此为止只是 **planning guidance**：

- `SkillPlanningProjection.step_ordering` 只有步骤 id 序；没有任何东西把
  `step_id` 映射到 PlanGraph 节点（capability）或 kernel `PlanStep`；
- `SkillPolicyMode=execute_guided` 已能被决策，但没有下游消费者 ——
  计划缺 required 步骤/证据契约时依然"看起来完整"；
- `SkillEvidenceRecorder.record_step/record_skip` 生产路径零调用：
  Agent 可以选中正确技能却跳过 required 步骤、乱序执行、不产出
  required evidence、fallback 不按声明走，而 completion 只看最终结果；
- `skills/replay.py` 只接受 caller 自喂的投影事实，与 runtime 证据不连通；
- `skill_guidance` 只活在 tool result 与进程内 turn ctx，不随
  `SessionPlan.gis_chapter` 持久化 —— 重启/回放后技能约束面消失。

## 2. 决策

在 ADR-0182 之上新增一层**派生投影 + 读时覆盖评估**，不新增执行器：

```
SkillPolicyDecision + SkillContract + MapProductPlan
  → ProcedureBinding（纯派生、持久于 gis_chapter["procedure_binding"]）
  → enforce（plan-time 确定性违例/重规划提示，披露不阻断）
  → 运行期：SessionPlan 行状态 + kernel StepEvidence + 决策结构
  → ProcedureCoverage（读时派生：covered/missing/unknown/
     skipped_declared/blocked/stale + drift）
  → 有界 Pi 卡片 / SessionPlan 投影行 / 终验 procedure_coverage 块
```

### 2.1 ProcedureBinding（派生投影，C1/C2）

`app/services/gis_harness/skills/procedure_binding.py`，
`schema="procedure_binding.v1"`，纯函数构造（零 IO/零 LLM/零时间戳 ——
指纹确定性）。每 step 绑定包含：skill id/version/fingerprint、step_id、
kind、required/skip_policy、depends_on、capability_ids、
**plan_node_ids**（capability_refs ∩ 计划 capability 集，按计划声明序）、
**session_step_refs**（kernel 物化规则 `step-{cap[:48]}`）、evidence_kinds、
conditional_on（decision node ids）、status（bound /
bound_obligation_only / capability_absent / optional_unplanned）+
reason_codes。

映射规则（稳定、确定性）：

- PlanGraph `node_id == capability`（既有事实），kernel step id 规则
  同 runtime `_materialize_steps` —— 绑定不发明第二套 id；
- 多 step → 同 capability 允许（many-to-one）；capability 缺失不虚构
  节点（capability_absent + fallback 语义交还既有 resolver）；
- 无 `capability_refs` 的步骤（inspect/validate/decide 类）=
  `bound_obligation_only`：只按 evidence kind 对账，不绑节点。

Decision 分支（C2 红线）：**只能由结构化事实裁决**。绑定期以计划内容做
结构裁决 —— 选项 `then_step_ids` 的 capability 在/不在计划 →
selected_option（resolved_by=plan_content）；无选项命中 → default
（resolved_by=default，显式披露）；多选项同时命中 → ambiguous（不裁决）。
LLM 文本无权直接选分支。

### 2.2 持久化与零漂移（C2）

绑定块在 intent 缝写进工具结果的 `plan` payload（additive 键
`procedure_binding`）→ 经既有 `apply_tool_result("webgis_map_intent")`
落入 `gis_chapter`，**不改 session_plan 持久层**。旧章节无该键 =
零漂移；`GIS_SKILL_POLICY=0` 或技能不可绑定（none/fallback/blocked/
硬能力缺口）→ 无绑定块，planner 原路回落并在 guidance 披露原因。

enforcement check 不持久：`binding × plan` 确定性重算（防第二真相）。

### 2.3 Plan-time enforcement（C3）

`enforce_procedure_binding(binding, plan)` 纯函数 →
`ProcedurePlanCheck`（violations + replan_hints）：

- `required_step_unplanned`：required 且非条件缺省分支的步骤无计划踪迹；
- `order_conflict`：显式 `step.depends_on` 与 PlanGraph 依赖边矛盾；
- `required_capability_unresolved`：required 步骤 capability 不在计划。

`mode=execute_guided` 时违例进 intent 结果 guidance/`procedure_check` 键
（⚠ 行 + 建议补的能力）；`guide` 模式同规但 advisory。**披露不阻断**：
技能对 planner 无 veto 权（user/planner-wins）；不改变 security/tool
availability。

### 2.4 运行期证据绑定与 ProcedureCoverage（C4/C5/C6）

`procedure_coverage.py::compute_procedure_coverage(chapter, envelope,
library)` —— **读时派生**（不建第二状态库）：

- 输入：chapter 的 procedure_binding + data_requirements/analysis_steps
  行状态（complete/voided/failed/unavailable/pending）+ kernel
  `PlanStep.evidence`（tool/tool_call_id/ref，按 capability 关联，
  same-capability 重复以 tool_call_id 区分，exactly-once 去重）+
  决策结构 + map_product/goal_satisfaction 块（只读）；
- 步骤状态机：planned caps 全 complete→covered（附 matched evidence
  refs）；failed→blocked（可重试）；unavailable→blocked（带 fallback
  披露）；voided→stale（supersede generation 不计入新 procedure）；
  pending/running→unknown；条件步骤不在计划且 decision=default→
  skipped_declared；required 无踪迹→missing；evidence-only 步骤无独立
  运行期事实→unknown（honest-unknown，不循环论证"能力完成=证据已产出"）；
- 义务：evidence_kind 有独立运行期事实才 satisfied，否则 unknown/missing
  （分母义务专项保留 replay.py 语义）；
- drift（C6）：绑定 skill_version/fingerprint 与当前 library 对账 →
  skill_version_drift / skill_fingerprint_drift / skill_deprecated /
  skill_missing 标志；deprecated 技能的绑定只降级不删除（历史可解释）；
- `complete` = 无 missing（unknown 不阻断，与 replay.py 一致）；skill
  coverage ≠ goal satisfaction —— 后者继续消费 science/cartography/
  final_map 事实，procedure_coverage 仅 additive。

### 2.5 消费面（C7）

- `format_session_plan_projection`：additive 单行
  `Procedure: skill@ver bound=x/y missing=[..] next=.. drift=..`
  （fail-open，无绑定零漂移）；
- `completion/pipeline`：终验块 additive 键 `procedure_coverage`
  （仿 goal_satisfaction/product_completeness 先例）；
- `procedure_pi_card`：≤1 有界卡（当前 skill、phase（第几步/共几步）、
  missing obligations ≤3、next expected step）；进 hotpath pi context；
  **禁止整份 YAML procedure 注入**。

### 2.6 红线（继承 + 新增）

- 不造 SkillExecutor / 第二 DAG / 第二状态库；
- 不绕过 Capability/Tool/Permission/Governor/MapSpec 体系；
- 任何 LLM 输出不产生绑定/不裁决分支/不充当证据；
- 原始 CoT 不进 evidence；
- User-wins：用户显式锁定不被绑定/覆盖自动改写；
- fail-closed for authority（绑定只披露不授权），fail-open only for
  optional telemetry（recorder/卡片投影失败不阻断主链路）。

## 3. 后果

- Agent 选中技能后，计划与运行期第一次有了"是否按方法完成"的确定性
  对账面：Goal/Completion 可见"方法步骤缺失"；
- SessionPlan 增加一个 additive 派生块（KB 有界：步数≤32、列表全 slice）；
- 技能库演进（version/fingerprint 变化）通过 drift 标志显式披露而非
  静默漂移；
- 测试语料：`tests/unit/gis_harness/procedure_binding_corpus.py`
  覆盖 §D 全族 × 场景（deterministic generated）。

## 4. 非目标

SkillExecutor、skill YAML 运行期自修改、大规模新 skill 内容、把
procedure 变 prompt、procedure 直接驱动工具调度。
