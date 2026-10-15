# skill-procedure-plan-binding v1 — 开发 ledger

Base: `origin/master` = fd1acd7c（2026-10-16）。分支：
`zcode/skill-procedure-plan-binding`。ADR-0219。

## Round 1 — recon（已完成）

- master 无前进（fd1acd7c）；open PR ×5（#1600/#1592/#1589/#1588/#1587），
  overlap matrix 见 `PR_OVERLAP_MATRIX.md`（工作区文件，不随 PR 提交）。
  结论：无语义重叠；#1589 只动 skill 代码沙箱（app/tools/skills.py）。
- 架构 recon（subagent A，只读）：skills 包 / SessionPlan / PlanGraph /
  kernel / evidence / hotpath / goal_satisfaction 全链路 + 接线点确认。
- **关键事实**：`ProcedureStep.capability_refs` 字段存在但库内 0 使用；
  `SkillEvidenceRecorder.record_step/record_skip` 生产零调用；
  `skill_guidance` 不持久化 —— 本方向缺口确认为真。

## Round 2 — 设计与实现（已完成）

决策要点（详见 ADR-0219）：

1. **双层绑定**：skill 级（capability_requirements × 计划行；全部技能
   可用）+ step 级（capability_refs 精确映射；库内
   spatial_autocorrelation_analysis 已最小增强 3 步 + 测试 twin 生成器）。
2. 持久化：`procedure_binding` 块随 intent 工具结果的 plan payload 落
   `gis_chapter`（零改 session_plan 持久层）；enforcement 不持久
   （binding × plan 确定性重算）。
3. 决策分支结构裁决（plan_content / default / ambiguous 三态；无 LLM）。
4. coverage 读时派生：chapter 行 + envelope progress（voided+ref →
   stale）+ kernel steps evidence（exactly-once by tool_call_id）。
5. complete 强语义：required 全 covered/skipped_declared + 无 missing。

修正记录（实现期发现）：

- `schema` 字段遮蔽 BaseModel 属性 → 改 `schema_version`；
- order_conflict reachable 方向反了 → 修正为「应先执行者反居下游」；
- enforcement/coverage 必须对照**当前**计划行/progress（绑定快照会分叉）；
- 无 bound_ref 的 voided progress 行不冒充 stale（没损失证据）；
- 同 goal 相同计划 replace → intent_diff 正确 carried（covered 是对的，
  stale 需语义签名变化触发）。

## Round 3 — 测试与 gates（已完成）

- 新测试 5 文件（binding/coverage/corpus/seam + corpus 生成器）全绿：
  - `test_procedure_binding_v1.py`（C1/C2/C3 + 对抗：伪造块 fail-closed、
    手改状态不产生覆盖）；
  - `test_procedure_coverage_v1.py`（C4/C5/C6/C7 + drift + 分母）；
  - `test_procedure_corpus_v1.py`（§D 族×场景矩阵 + 确定性）；
  - `test_procedure_seam_v1.py`（真实 SessionPlan 路径：selection →
    binding 落 chapter → 工具推进 → coverage → 投影行；supersede/replace
    stale 语义；无绑定零漂移）；
  - `procedure_binding_corpus.py`（语料生成器）。
- 回归（改动面）全绿：skill 库 7 文件 119 例、session_plan/kernel/
  hotpath/planner 71 例、goal_satisfaction 57 例。
- ruff 全绿。

## 基线失败

无（本轮所有定向 gates 在本分支全绿；未发现 pre-existing 失败）。

## 遗留 / 后续

- 库内其余 37 技能的 capability_refs 渐进充实（内容工作，另行 PR）；
- evidence-kind 运行期生产者（如 tool result 携带 evidence_kind 面）
  未建 —— obligation-only 步骤保持 honest-unknown（ADR §2.4）；
- workflow_compiler 编译路径的绑定接线（当前只在权威 intent 缝）；
- goal_satisfaction evaluator 内嵌消费（当前以 pipeline 块 + 投影行
  消费，evaluator 本体未动）。


## Round 4 — 独立 review 清偿（已完成）

Reviewer 判定 APPROVE-WITH-FIXES（2×P1 + 10×P2，全部处置）：

- P1-1 denominator 恒 missing → 三态接线（结构接地 / map_product
  显式事实 True/False / 无事实 unknown）；
- P1-2 complete 强语义使信号退化为常量 → 双旗标（complete=replay 兼容
  无 missing；fully_covered=强语义），基线测试 dead assertion 修复；
- P2-3/4 决策分支：plan_content 选中其他分支 → 专属步骤/能力
  skipped_declared（enforcement 同步豁免）；无结构信号不裁 default；
- P2-6/7 指纹在有界投影上计算（round-trip 稳定）；步骤列表元素切片
  与 capability 级一致 + guard 测试；
- P2-8 生产 capability_exists 显式接 bridges 默认谓词；
- P2-10 corpus 改用真实 replace 词表（chapter pending + progress
  voided+ref）；P2-11 seam 测试 kernel evidence 注释/断言修正；
  P2-12 ledger 计数修正；P2-9 ADR 对齐（§2.2/§2.4）。
- 新增 review guard 测试 3 例（分支未走/无信号不裁/长内容指纹）。
