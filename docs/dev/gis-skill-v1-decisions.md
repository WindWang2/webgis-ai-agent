# GIS Skill Library V1 — Decisions（设计决定记录）

配套：`docs/adr/0182-gis-skill-procedure-library-v1.md`、
`docs/dev/gis-skill-v1-recon.md`、`docs/dev/gis-skill-v1-ledger.md`

## D0. Skill 是否真的有必要？——**有必要，但只作为薄薄一层**

诚实回答：现有对象已覆盖「选什么方法、什么制图、什么义务、怎么降级」
（recipe/workflow/family/composite/ontology/capability）。没有任何对象承载：

1. **可重放的作业过程 IR**——领域声明的有序步骤与显式决策点
   （"先查重、再判尺度、再选表达"，机器可对照 plan/evidence 验证覆盖）；
2. **统计/地理/时间语义**——count vs rate vs density vs percentage 不可互换、
   scope/analysis unit/aggregation unit/display unit 分离、时序可比性义务；
3. **作业阶段级组合**——composite 组合的是 recipe 制图层；
   Skill 组合的是「分析 → 制图 → 统计 → 交付」的作业阶段；
4. **面向 Pi 的渐进披露目录**——SkillCard（有界）→ 按需 full procedure，
   今天方法知识工具没有"技能"粒度，也没有字节预算的分披露；
5. **Skill 选择证据与过程重放**——selection reason 可审计、
   procedure obligations 可被验证"进入了 plan/evidence"。

因此 Skill = **reusable domain procedure（领域作业方法知识）**，
以引用方式消费 recipe/capability/ontology/template，不替代任何既有选择。

## D1. 概念边界（对账 goal §39/S32）

| 对象 | 本仓语义 | Skill 与它的关系 |
|------|----------|------------------|
| Recipe | 制图/算法选择策略（intent→capability/制图+降级链） | Skill.procedure 的"表达选择"步骤引用 recipe_id；选择裁决权仍在 planner |
| Template | 视觉组件组合 | Skill 不声明组件，只声明 product_requirements（组件期望引用既有词表） |
| Skill | 完整 GIS 作业过程（步骤+决策点+语义义务+证据） | 本体层 |
| Product | 用户最终交付 | Skill 输出 product_requirements 投影，不直接写 MapSpec paint |
| Capability | 能力事实源 | Skill.capability_requirements 只存 capability id，校验走 CapabilityRegistry |
| WorkflowProfile | recipe 挂载的数据角色/义务/完成契约 | Skill 的语义词汇与之同源（DATA_ROLES 等直接 import），不另造词表 |

## D2. 落点与形态

- 代码：`app/services/gis_harness/skills/`（harness 子包，与 recipes/workflow 同居；
  不建顶层 `app/services/skill_library`，避免暗示第二套服务）。
- 技能数据：**版本化 YAML 资产** `app/services/gis_harness/skills/library/*.yaml`
  （pydantic 契约 + 严格 loader + 启动 fail-loud；技能是审定资产，修改走 code
  review —— S23；与 `app/skills/*.md` 的 prompt 文档明确切割）。
- 与 `app/skills/` 的关系：V1 不动旧 md/py skill（chat 层资产）；在
  UBIQUITOUS_LANGUAGE 与模块 docstring 声明：`app/skills` = chat prompt 技能
  （遗留），`gis_harness/skills` = 结构化 GIS 领域技能（本线）。

## D3. 松耦合协议（对未合并分支）

- `SituationLike`（typing.Protocol，structural）：#1275 合并后 `GISSituation`
  天然满足，零改动；本线内 fixture 驱动（S29）。
- `CapabilityRequirement`：只存 id + purpose + criticality；校验谓词注入
  （默认 CapabilityRegistry.has）；#1274/#1275 合并后无需改 Skill 契约（S28/S30）。
- ExecutionGraph：Skill IR 描述"应该做什么"，运行期仍是 SessionPlan/PlanGraph；
  replay 只做投影比对，不做调度（S31）。

## D4. Resolver 设计（deterministic-first）

- 输入：`SkillSelectionQuery`（goal 文本、可选 intent 投影、可选
  SelectionFacts：geometry/data roles/measure semantics/scope/purpose/temporal）。
- 信号（全部确定性打分，零 LLM）：本体任务匹配（复用 gis_ontology 关键词面）、
  intent_patterns 关键词、task_types 对齐、几何/数据资格硬门槛（ineligible
  显式拒绝+原因码+fallback 建议）、输出目的对齐、pack/domain 过滤。
- 输出：ranked（score+tie→id）、confidence 分档、matched signals（可审计）、
  rejected（id→reason）、clarification_needed（歧义信号）。
- 与 planner 的关系：V1 经只读工具面向 Pi 暴露（知识咨询），planner 接线
  留接口（`resolve_skills` 纯函数），不在本 PR 改 candidate_planner 热路径。

## D5. 组合设计（S5）

- `SkillComposition`：有序成员（skill_id + role∈{primary,supporting,presentation}
  + 显式 depends_on）；planner 输出确定性拓扑序（Kahn，tie→声明序→id）。
- 约束：depth ≤4、无环、显式依赖、悬空引用 fatal；禁止任意递归组合。
- 与 CompositeRecipe 划界：composite 选择制图层（plan 内），SkillComposition
  编排作业阶段（跨 plan 阶段的作业知识）；二者可嵌套引用但不互替。

## D6. 语义词汇（S10-S12）

- 统计：`measure_semantics` 词表 {count, rate, density, percentage, index,
  mean, area_share}；obligation：rate/density/percentage 结论必须有分母证据
  （引用既有 DENOMINATOR_FIELD_HINTS 事实面，不重复实现）。
- 地理：`scope_unit` / `analysis_unit` / `aggregation_unit` / `display_unit`
  四分离（成都例：scope=city, aggregation=district, display=point+district）。
- 时间：`temporal_mode` {snapshot, comparison, trend, seasonal, before_after}
  + 可比性义务（same extent/compatible classes/comparable source/time labeling）。

## D7. 证据与重放（S21/S25）

- `SkillEvidenceRecord`：selected/skill_version/selection_reason/step 状态/
  skipped(带披露)/fallback/completion；有界投影；不记录模型 CoT。
- `replay_procedure(skill, plan_facts, evidence)`：纯函数；每个 required step
  → covered/missing/unknown/skipped_declared；obligation 有 evidence 键覆盖 →
  satisfied。**验证义务进入 plan/evidence，不判断"看起来差不多"**。

## D8. 防 Prompt 化（S26）

技能 YAML 必须是结构化契约（procedure 节点、语义义务、资格规则）；
`guidance` 字段只允许 ≤240 字的有界模型提示；纯 markdown 技能（无 procedure）
在 validator 报 fatal。旧 `app/skills/*.md` 不属于本库、不受此约束（遗留，
ADR 声明迁移方向）。

## D9. 明确不做

- 不做 UI（S27：现有 skills-hub 展示旧 md 技能，保持兼容，不改）
- 不做在线自修改（S23）：技能是版本化资产，运行 feedback 只能出 stats（V1 不建）
- 不做几十个 pack（S15）：core 一个 pack + 领域子目录，pack registry 结构就绪
- 不等线上 CI、不自动 merge
