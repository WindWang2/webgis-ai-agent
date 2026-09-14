# GIS Skill Library V1 — Phase 0 Recon（Skill Landscape）

- 分支：`harness/gis-skill-procedure-library-v1`
- 基线：origin/master `580b33e923e992cd6706659d455dfd72ef55d033`（PR #1272 合入后）
- 勘察日期：2026-09-14
- 结论速览：仓库中 "skill" 有 3 种既有含义；结构化领域作业知识已由
  recipe/workflow/family/composite/ontology/capability 承担；真正的空白是
  **可重放的作业过程 IR + 统计/地理/时间语义 + Skill 级组合 + 面向 Pi 的
  渐进披露目录**。新 Skill 层必须以"引用不复制"的方式挂在这些对象之上。

## 1. Open PR 对账（执行时）

| PR | 内容 | 与本任务关系 |
|----|------|--------------|
| #1273 | qc-loop review & optimize 收敛 | 无冲突（质量线） |
| #1274 | Pi typed tool surface hardening | 不触碰 Pi schema；只加只读 tier-2 工具 |
| #1275 | GIS Situation / World Model（ADR-0180） | 未合并；Skill 层用 `SituationLike` Protocol 松耦合 |
| #1270 | CI adaptive hygiene | 无冲突 |

本地未合并分支：`harness/gis-capability-graph-v1`（领先 master 6 提交：
provider 投影 + `resolve_capabilities` facade + ADR-0181）、
`harness/pi-typed-tool-surface-v1`、`harness/gis-situation-world-model-v1`。
**master 上已有** `capability_graph.py`(V8/ADR-0137)、
`capability_descriptors.py`(V7)、`gis_world_state/`(ADR-0072)。

## 2. "skill" 的 3 种既有含义

| 含义 | 载体 | 形态 | 消费方 |
|------|------|------|--------|
| Runtime GIS 声明文档 | `app/skills/*.md`（6 个） | YAML frontmatter（name/description）+ 自然语言正文 | `app/tools/skills.py::_md_skills`；GET `/api/v1/chat/skills`；`execution_engine` prompt 注入；前端 `skills-hub.tsx` |
| Runtime 可执行技能脚本 | `app/skills/*.py` | importlib 加载注册进 ToolRegistry（`load_skills`） | 同一 loader；`create_new_skill` 工具可运行时写盘 |
| Developer/Agent 开发技能 | `agent/skills/`（41）+ `.agents/skills/`（99） | SKILL.md（tdd/review/gstack…） | 外部 skills CLI；根 `skills-lock.json`（无仓内代码消费方） |

要点：
- 目录/API 层面 developer 与 runtime skill 未混装，但 `app/skills/` 内部
  .md（声明文档）与 .py（可执行代码）混在同一 loader/上传端点 —— 既有结构债。
- 旧 .md 技能是**弱声明**（无结构化契约、无版本、无资格/义务/证据语义），
  不可机器校验，与"Skill 是否 Prompt 化"红线（S26）相悖。V1 不迁移不删除，
  在 ADR/词表中明确新旧边界。

## 3. 结构化领域知识对象现状（master）

| 对象 | 位置 | 语义（一句话） |
|------|------|----------------|
| `CartographyRecipe` (V1-V4) | `gis_harness/recipes.py` | 「这类制图意图怎么制图」：intent 匹配、确定性 eligibility、capability 选择、主/辅制图、元素级+recipe 级降级链 |
| `WorkflowProfile` (V2 DSL) | `gis_harness/workflow_schema.py` | Recipe 之上的契约层：数据角色（缺省 block/degrade）、科学义务（联动算法层 scientific preconditions）、七维完成契约、语义降级分类、证据要求 |
| `WorkflowFamily` / `CompositeRecipe` / `ScenarioTemplate` (V3) | `gis_harness/workflow_families.py` | family=recipe 簇投影；composite=base+条件 supporting；scenario=主体/本体/scope 信号 → 制图候选+终验期望+minimal 兜底 |
| `TaskDescriptor`（GIS Task Ontology V3） | `gis_harness/gis_ontology.py` | 「这类 GIS 任务是什么」：数据角色/几何期望/能力/产物/制图期望/歧义规则/四级 fallback 策略 |
| `CapabilityRegistry` / V7 描述符 / V8 图 | `app/lib/gis/capability_registry.py`、`gis_harness/capability_descriptors.py`、`capability_graph.py` | 分析能力事实源与统一能力图（capability/algorithm/model/workflow/provider） |
| `MapProductTemplate` / `ProductGraph` | `gis_harness/product_templates.py`、`product_graph.py` | 产品结构（recipe+图层角色+组件+输出物）；Goal→产品投影 |
| MapModel / Component / Composition templates | `app/lib/cartography/*` | 制图表达与视觉组件组合（AC-V11） |
| `KnowledgeService` + 6 个只读工具 | `app/lib/gis/methodology/service.py`、`gis_harness/knowledge_tools.py` | 方法知识只读工具面：classify/qualify/rank/explain/template_plan/component_query |
| ExecutionPlan / PlanGraph / SessionPlan | `geocompute/plan.py`、`gis_harness/plan_graph.py` | 运行期执行表示（Skill IR 不得替代） |

## 4. 差距分析：goal 的 Skill contract vs 现有对象

| Goal §8 字段 | 现有覆盖 | 判定 |
|--------------|----------|------|
| id/name/description/domain | recipe 有 | 引用即可 |
| intent_patterns | recipe.intent_tasks + workflow.keywords + scenario.match_subjects + ontology.keywords | 复用，不重造路由 |
| required/optional_situation | eligibility（数据事实）；situation 分支未合并 | **缺口**：会话级 situation 需求声明 + Protocol |
| input_roles/output_roles | workflow.data_roles / artifact types | 复用词表 |
| capability_requirements | recipe.preferred_analysis | 复用 capability id（校验走 capability_registry） |
| **procedure（有序作业步骤）** | 无（compiler 15 阶段是固定机器，不是领域声明） | **核心缺口 S2** |
| **decision_points（显式决策点）** | fallback 是资格驱动的，无显式决策 IR | **核心缺口 S2** |
| constraints / preconditions | eligibility + scientific preconditions | 复用 + 引用 |
| quality_obligations | workflow.obligations（工作流级） | Skill 级需引用+步骤级绑定 |
| fallback | 三层都有 | 复用语义词表（DOWNGRADE_CLASSES） |
| completion_evidence | completion_requirements + evidence_requirements | 复用维度词表 |
| **统计语义（count/rate/density/percentage）** | 仅 denominator 义务 | **缺口 S10** |
| **地理语义（scope/analysis/aggregation/display unit）** | data_roles 有角色无地理层级语义 | **缺口 S11** |
| **时间语义（snapshot/comparison/trend…可比性）** | baseline/target_time 角色 + temporal 义务 | **缺口 S12（部分）** |
| version/deprecated | schema_version 有；无 per-object deprecated | **缺口 S1** |
| SkillCard / 渐进披露 / catalog | 无（knowledge tools 是方法粒度） | **缺口 S17/S19** |
| Skill 组合（作业阶段级） | composite 是 recipe 制图层组合 | **缺口 S5（部分重叠，需 ADR 划界）** |
| 选择证据 + 重放 | selection 证据在 planner 内部；无 procedure 重放 | **缺口 S21/S25** |

## 5. 与 harness 的接缝

- 只读工具注册：`app/tools/__init__.py::_TOOL_MODULES`（一行式，ADR-0100 纪律）
- 启动校验：`gis_harness/registry_validation.py::validate_gis_library`（skills 块 additive）
- Pi 集成面：knowledge_tools 同款 tier-2 只读工具；**不建 SkillAgent、不改 Pi schema**
- 测试：`tests/unit/gis_harness/test_<模块>_v<N>.py`；`pytest -q tests/unit`；
  conftest 无共享 situation/plan fixture（内联构造）

## 6. 不得重复清单（本任务红线核对）

- 不新造 planner / ExecutionGraph / SessionPlan（复用 plan_graph/plan_runtime）
- 不新造 Tool Registry（复用 ToolRegistry/ToolCatalog/tool_surface）
- 不新造 Capability 词表/图（引用 capability id + CapabilityRegistry 校验）
- 不新造制图期望状态（MapSpec 唯一权威）
- 不复制 recipe/family/composite 的选择职责（Skill 引用 recipe_id）
- 不复制 scientific preconditions（obligation 引用 precondition_id）
- 不建第二个 agent loop / SkillAgent
- 不给模型注入全部技能全文（SkillCard + 按需 detail）
