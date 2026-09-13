# ADR-0182: GIS Skill / Procedure Library V1 —— 领域作业方法知识层

- 状态: Accepted
- 日期: 2026-09-14
- 线: harness/gis-skill-procedure-library-v1（与全部 Harness 主线并行的独立线）
- 关联: ADR-0097（Goal Graph）、ADR-0101（Workflow Recipe DSL V2）、ADR-0137
  （Capability Graph V8）、ADR-0150/0151（意图理解 / recipe 资格 V4）、
  ADR-0160~0169（AC-V11）、ADR-0170~0179（ads-v1）；
  未合并分支 ADR-0180（Situation，#1275）/ ADR-0181（Capability Graph v1）

## 1. 背景与问题

Harness 已有完整的"方法选择"知识链：intent → 本体任务 → recipe（资格/
能力/制图/降级链）→ capability/algorithm → plan → 产品。但它缺一层
**领域作业过程知识**（domain procedural knowledge）：

- 「分析成都小学分布」应触发的不是工具名，而是一套作业方法：解析行政
  范围 → 获取主体点数据 → 质检（重复/CRS/空几何）→ 按目标选表达
  （点分布/密度/行政聚合）→ spatial join → 分母/归一化核查 → 主图+统计
  图+图例+来源 → 产品完整性评估。
- 这些步骤、决策点、统计/地理/时间语义义务今天没有机器可查的载体：
  WorkflowProfile 有义务但无步骤与决策点 IR；knowledge tools 是"方法
  粒度"而非"作业过程粒度"；recipe 组合层（composite）编排的是制图层
  而非作业阶段。

## 2. 决策

### 2.1 Skill 的定义与定位

**Skill = reusable domain procedure**：Agent 完成空间分析/遥感分析/制图
目标时可调用、组合、约束、复用的领域作业方法知识。它

- 位于 goal 与 capability/execution planning 之间：
  `Goal → Skill(procedure) → capability requirements → tools/algorithms`；
- **引用不复制**：capability id 引用 CapabilityRegistry；recipe_id 引用
  RecipeRegistry；本体任务引用 gis_ontology；数据角色复用
  `workflow_schema.DATA_ROLES`；科学义务引用算法层 scientific
  preconditions id；降级分类复用 `DOWNGRADE_CLASSES` 语义；
- **不是**：第六套平行 registry、第二个 planner、第二个 agent loop、
  tool registry 复制品、prompt 文本集（防 Skill Prompt 化，§2.6）。

### 2.2 契约（S1）与过程 IR（S2）

`SkillContract`（pydantic，schema_version=1）：id/name/description/domain/
pack/intent_patterns/ontology_tasks/task_types/required_situation/
optional_situation/input_roles/output_roles/capability_requirements/
recipe_refs/**procedure**/statistical_semantics/geographic_semantics/
temporal_semantics/constraints/quality_obligations/fallback/
completion_evidence/examples/guidance(≤240 字)/version/deprecated/
when_to_use/when_not_to_use。

`SkillProcedure`（声明式 IR，不执行）：`ProcedureStep`（required 步骤 +
skip_policy + 步骤级证据要求）、`DecisionNode`（显式条件→分支）、
`RequirementNode` / `EvidenceNode` / `FallbackNode`；≤32 节点、唯一 id、
悬空引用 fatal。IR 描述"应该做什么"；运行期执行仍归 SessionPlan /
PlanGraph / geocompute（本 ADR 不实现 ExecutionGraph 调度器）。

### 2.3 语义词汇（S10-S12）

- 统计语义：`measure_semantics ∈ {count, rate, density, percentage, index,
  mean, area_share}`；`denominator_required=true` 表示**无条件**分母义务
  （replay 强制）；混合度量集（如 count+density）的技能 flag 可为 false，
  分母义务由**分支级步骤证据**承担（如 normalize_if_rate 步骤要求
  denominator_evidence）。`denominator_required=false` 且度量集全部为
  需分母度量 = 校验违规；
- 地理语义：scope / analysis / aggregation / display unit 四分离；
- 时间语义：`temporal_mode ∈ {snapshot, comparison, trend, seasonal,
  before_after}` + 可比性义务（same extent / compatible classes /
  comparable source / time labeling）。

### 2.4 选择、资格、组合（S4/S5/S9）

`SkillResolver`：deterministic-first（零 LLM）。信号=本体任务匹配+
intent_patterns+task_types+几何/数据资格硬门槛+输出目的对齐+pack 过滤；
输出 ranked/confidence/matched signals/rejected(原因码)/clarification/
fallback 建议。 choropleth 型技能对点数据**显式 ineligible**（不偷画），
给 reason + fallback。

`SkillComposition`：有序成员（primary/supporting/presentation + 显式
depends_on）；无环、悬空 fatal、确定性拓扑序。V1 成员为**扁平**技能
引用（depth=1，组合不可嵌套组合），`MAX_COMPOSITION_DEPTH=4` 为将来
嵌套引用演进预留护栏。与
CompositeRecipe 的划界：后者编排 plan 内制图层，前者编排作业阶段。

### 2.5 资产形态与治理（S15/S16/S23）

技能是**版本化 YAML 资产**（`app/services/gis_harness/skills/library/`），
pydantic 契约 + 严格 loader + `validate_gis_library` 启动期 fail-loud
（重复 id/悬空 capability/recipe/ontology 引用/环/非法决策节点/未知证据
种类/deprecated 依赖/缺 procedure 的纯 markdown 技能全 fatal）。运行期
不得自修改技能；反馈只能产出 stats（V1 不建）。V1 只建 core pack，
pack registry 结构就绪。弃用链强制对账：存活技能的 alternative_skill
目标与组合成员不得指向 deprecated 技能（resolver 默认排除弃用项，
悬空建议 = 自相矛盾）。每个技能必须对三个核心失败触发器
（missing_input / unsupported_geometry / insufficient_data）各声明
至少一条 fallback —— 缺失即 fatal。

### 2.6 面向 Pi 的渐进披露（S17-S20/S26）

只读 tier-2 工具三件（对齐 knowledge_tools 模式）：`gis_skill_search`
（返回有界 SkillCard：id/描述/when_to_use/要求）、`gis_skill_detail`
（按需全文投影）、`gis_skill_replay_check`（义务→plan/evidence 覆盖比
对）。**不注入全部技能全文**；不建 SkillAgent；不改 Pi schema（与
#1274 的边界：Skill 定义程序语义，Typed Tool Surface 定义模型可调用
工具面）。guidance 字段 ≤240 字；无 procedure 的 markdown 技能 fatal。

### 2.7 证据与重放（S21/S25）

`SkillEvidenceRecord`（selected/version/reason/step/skipped/fallback/
completion；不记录模型 CoT）+ `replay_procedure` 纯函数：required 步骤
与义务逐一对照 plan/evidence 投影 → covered/missing/unknown/
skipped_declared，供 Goal Evaluator 消费。

## 3. 与既有对象的对账（防重复声明）

| 对象 | 语义 | Skill 关系 |
|------|------|-----------|
| Recipe | 制图/算法选择策略 | 引用 recipe_id；选择裁决仍在 planner |
| Template | 视觉组件组合 | 不声明组件，仅 product_requirements 引用 |
| Product | 最终交付 | Skill 只产出 requirements 投影 |
| Capability | 能力事实源 | 只存 id，校验注入 CapabilityRegistry.has |
| WorkflowProfile | recipe 挂载的角色/义务/完成契约 | 词表同源 import，不另造 |
| CompositeRecipe | plan 内制图层组合 | SkillComposition 编排作业阶段，可嵌套引用 |

## 4. 与未合并分支的协调

- Situation（#1275）：`SituationLike` Protocol（structural typing），
  合并后 GISSituation 天然满足，本线零改动；
- Capability Graph v1：master 已有 V8 图与 V7 描述符，本线只用注册表
  存在性校验；分支的 `resolve_capabilities` 合并后可在 bridges 层增强，
  不改契约；
- Typed Tool Surface（#1274）：本线只加 tier-2 只读工具，遵守其
  pre-dispatch 门与字节预算纪律。

## 5. 后果

- 正面：作业过程知识可版本化、可校验、可重放、可渐进披露；LLM 只能
  建议，不能越过确定性资格与义务；为 benchmark（≥150 任务语料）提供
  确定性评测面。
- 代价/风险：新概念入场（以 ADR+词表+validator+引用纪律控制）；与
  recipe/composite 的边界需要 review 持续守护；技能 YAML 增加资产维护
  面（fail-loud + 单测锁词表）。
- 回滚：删除 `app/services/gis_harness/skills/`、`app/tools/
  skill_library_tools.py` 一行注册、registry_validation 的 skills 校验
  块与对应测试即可，无 schema/存储迁移。
