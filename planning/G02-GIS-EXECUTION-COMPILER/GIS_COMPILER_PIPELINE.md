# GIS 执行编译管线（GIS_COMPILER_PIPELINE）

> G02-GIS-EXECUTION-COMPILER 工程文档。配套：`BASELINE.md`（M0 审计断链）、
> `PLAN.md`（Oracle 表）、`SCIENTIFIC_ORACLES.md`（科学 Oracle）。
> 本文只描述已落地代码的真实行为，路径相对仓库根；schema：`task_compilation.v1`、
> `family_conformance.v1`。核心模块：`app/services/gis_harness/execution_compiler/`
> （contracts / requirement_to_tasks / units / catalog_conformance / acceptance_bridge /
> invalidation）。

## 1. 管线定位

在既有 RequirementIR / analysis_workflows / goal_satisfaction 之上补一个**纯函数
编译投影层**，闭合 BASELINE §2 的四条断链：

| 断链 | 补法 | 代码 |
|---|---|---|
| C1 IR 不驱动执行编译（LLM 自由选族） | `compile_requirement_to_tasks`：IR → 族/表达/单位义务候选 | `requirement_to_tasks.py` + `units.py` |
| C2 族运行回执对 goal 验收不可见 | 验收桥：`WorkflowRunState` → chapter 行只读投影 | `acceptance_bridge.py` |
| C3 族声明 ↔ 注册表无一致性 fail-closed 投影 | `audit_family_conformance` 全族对账 | `catalog_conformance.py` |
| C4 IR patch → 受影响步骤集推导缺失 | `patch_invalidation_scope` 语义差分 | `invalidation.py` |
| C5 单位/量纲无跨层静态对账 | `units.py` 声明性义务投影（见 SCIENTIFIC_ORACLES.md） | `units.py` |

红线（`execution_compiler/__init__.py` docstring）：零 LLM / 零 IO（同输入逐字节
同输出，`data_facts` 由调用方显式注入）；不新增第二 planner / 注册中心 / 验收器
——编译产物**喂给**既有 `plan_workflow` 与 goal_satisfaction，裁决权威不变。

## 2. 端到端编译管线图

```
自然语言话语
   │ resolve_map_request_intent（app/services/gis_harness/intent）
   ▼
MapRequestIntent（core —— 唯一理解真相）
   │ build_document → derive_sections（requirement_ir/build.py）
   │ · typed sections 确定性派生；genesis 收割 harvest_geometry_kind /
   │   normalize_normalization（词表命中才收，origin=user）
   ▼
RequirementDocument（requirement_document.v1）
   │ compile_requirement_to_tasks（execution_compiler/requirement_to_tasks.py）
   ▼
TaskCompilation（task_compilation.v1；verdict ∈ compilable /
   │             needs_clarification / blocked —— 封闭三态）
   ├─ family_candidates      IR 信号 → 七族排序（信号溯源，≤4）
   ├─ expression_candidates  表达候选（user_requested 分离，≤6）
   └─ unit_obligations       量纲义务（missing_denominator → 披露，≤8）
   │ LLM/Pi 选族（只读工具 webgis_requirement_compile；
   │ 显式偏离 top 候选必须在回复中披露）
   ▼
plan_workflow（analysis_workflows/orchestrator.py；七族词表 families.FAMILY_IDS）
   ├─ build_contract（families.py：族模板 → AnalysisWorkflowContract）
   ├─ validate_workflow_contract（fail-closed：环/词表/单位/前置）
   └─ typed 步骤裁决（ready/blocked/skipped）+ contract_fingerprint
   ▼
WorkflowExecutor.execute（analysis_workflows/executor.py；拓扑波次并发）
   ├─ registry.dispatch（执行真相：Pydantic/护栏/记账全继承，零旁路）
   ├─ envelope 铸造（ref:geojson-*；envelope_facts 实测事实入封套）
   ├─ step_fingerprint（recompute.py；satisfied 且指纹不变 → 复用）
   └─ run_final_gate → state.science_validation（science_final_gate 投影）
   ▼
WorkflowRunState（session store 单键 alias analysis_workflow.v1:<workflow_id>）
   │ completion/pipeline.run_map_finalization
   │ → evaluation_view_with_workflow_rows（acceptance_bridge.py）
   │    ├─ load_workflow_runs（按 alias 只读枚举，≤8）
   │    ├─ merge_workflow_rows（行 capability=awf.<family>.<step_id>，幂等）
   │    └─ workflow_disclosures（科学终审未过/未匹配需求/skipped）
   ▼
map_product_block(chapter=合并评估视图, workflow_disclosures=…)
   └─ goal_satisfaction.evaluate_goal_satisfaction（ADR-0183 权威，零改动消费）
      → block["goal_satisfaction"] / block["workflow_disclosures"] 持久化

── patch 旁路（同一管线的差分入口）────────────────────────────────────
RequirementDocument(old) ──patch──▶ RequirementDocument(new)
   │ patch_invalidation_scope（invalidation.py）
   ▼
InvalidationScope{invalidated_roles, science_invalidated_steps,
                  preserved_steps, *_changed}
   └─ 语义失效种子上限：实际重算由 recompute.py 内容指纹层精确裁决
```

## 3. 阶段权威表（输入 / 输出 / 裁决权威 / 失败语义）

| 阶段 | 输入 | 输出 | 裁决权威 | 失败语义（closed verdict） |
|---|---|---|---|---|
| S0 意图 | 话语文本 | `MapRequestIntent` | `resolve_map_request_intent` 规则表（intent 层独占） | 词表外 = intent 层既有语义（不在本层） |
| S1 genesis | query + core | `RequirementDocument` | core 单一真相；sections 派生（`derive_sections`） | AOI 指代污染 → 清空 + `aoi.resolver_state=degraded`（clarify 接管）；收割只认词表命中，未命中 = 未表达 |
| S2 编译 | `RequirementDocument`（+ 可选 `data_facts`、`family_allowlist`） | `TaskCompilation` | 本层只做**证据投影**，不选族不裁决执行 | 阻断歧义 open → `needs_clarification`；无信号命中/空 allowlist → `blocked`（`no_family_signal_match` / `family_allowlist_empty`）；不伪填数据 |
| S3 规划 | family id + bindings + overrides | `WorkflowPlanResult`（契约+逐步裁决+指纹） | `plan_workflow`（orchestrator）+ `validate_workflow_contract` | required+block 角色缺失 → typed `blocked`（`AWF_DATA_ROLE_MISSING:<ROLE>` 等族特定码）；optional 缺输入 → typed `skipped`；环/词表越界 → `ContractValidationError` |
| S4 执行 | 契约 + 运行态 | `WorkflowRunState`（envelopes/receipts/science_validation） | `registry.dispatch`（执行真相）+ `run_final_gate`（科学终审） | 工具失败/异常 → 步骤 `failed`、下游 `blocked`，verdict 绝不 satisfied；终审 fail-open 落 `not_evaluated` |
| S5 验收桥 | session 的 `WorkflowRunState` 清单 + chapter | 合并评估视图 + 披露 | goal_satisfaction evaluator（ADR-0183，零改动） | 零运行/桥失败 → 原 chapter 原样评估（零漂移）；`skipped` 行不背书 fulfilled |
| P patch | old/new `RequirementDocument`（+ 契约） | `InvalidationScope` | 语义差分本层；**内容身份**权威在 recompute 指纹层 | 词表外/事实缺席 = 保守失效（AOI 变 → 全量角色），绝不假复用 |

## 4. 阶段细节

### 4.1 S0→S1：意图与 genesis 收割

- 理解真相唯一：`GISIntentSpec.core` 内嵌 `MapRequestIntent`（`requirement_ir/
  contracts.py`）；sections 由 `derive_sections`（`requirement_ir/build.py`）确定性
  派生，无第二套解析规则。
- genesis 收割（GC-01，`build.py` derive_sections 尾段，仅 `query` 在场时）：
  `norm.harvest_geometry_kind(query)` 命中词表 → `representation.geometry_kind`
  + `field_provenance["representation.geometry_kind"]=origin:user`；
  `norm.normalize_normalization(query)` 命中且 statistics 口径仍为 none →
  `statistics.normalization` 升级（同样 origin=user）。只收词表命中——
  `harvest_geometry_kind` 无 passthrough，未命中返回 `""`（未表达，零推断）。

### 4.2 S2：`compile_requirement_to_tasks`（纯函数）

入口：`compile_requirement_to_tasks(doc, *, data_facts=None,
family_allowlist=None)` → `TaskCompilation`。

- **信号**（`_signals_of`）：IR 字段路径+值+origin（`field_provenance` 缺席记
  `rule`）+权重（`task.task_type`=3.0、`spatial_relation.kind`=2.5、
  `statistics.dimension`=2.0 等），≤12 条，声明序确定。
- **族候选**（`_family_score`）：`task.task_type`→族强映射表 `_TASK_TYPE_FAMILY`
  +各族信号谓词（如 `service_accessibility` 吃 `spatial_relation.kind=service_area`
  权重 4.0；`hazard_exposure` 吃 `subject.category` 危险源词面）；得分>0 才入候选，
  按 `(-score, family)` 排序取前 4；每候选携带命中信号与 `role_requirements`
  投影（`RoleNeed`）。**不选族**——排序是证据投影，选择权在 LLM/Pi 消费面。
- **表达候选**（`_expression_candidates`，GC-02）：由 subject 类型/几何期望/
  normalization 推导（point_map / admin_choropleth / area_density / heatmap /
  per_capita_choropleth…）；`user_requested=True` 仅当 `representation.
  geometry_kind` 命中 `_GEOMETRY_KIND_EXPRESSION`；`suitable` 三态——数据事实
  （`data_facts["subject"]["feature_count"]` 等）缺席 = `None`（不虚构），密度
  类需 `feature_count >= MIN_DENSITY_POINTS(30)`；用户点名但不可推导的 kind 仍
  保留并披露（不静默丢）。
- **单位义务**：`units.build_unit_obligations`（详见 SCIENTIFIC_ORACLES.md）。
- **verdict**：`blocking and state=="open"` 歧义 → `needs_clarification`（前 4 条
  入 `clarification_requests`）；候选空 → `blocked`；否则 `compilable`。
  `missing_denominator` / `suitable is None` / 用户点名不适合 → `disclosures`（≤6）。

### 4.3 S3：`plan_workflow`（七族，既有层——编译器只喂不改）

`families.FAMILY_IDS`：`admin_facility_distribution / service_accessibility /
hazard_exposure / temporal_change / statistical_zones / raster_zonal_summary /
network_proximity`。`build_contract` 把族模板实例化为 `AnalysisWorkflowContract`
（绑定自动铺满角色需求；参数覆盖只允许模板已有键；`denominator_kind=field` 的
分母字段只认 `role:denominator` 绑定的 `qualifier.field`）。plan 期 typed 门：
temporal 年份对齐（`AWF_TEMPORAL_MISMATCH`）、role_kind（`AWF_ROLE_KIND_MISMATCH`）、
几何画像（`check_role_qualifier`）、分母列清单（`AWF_DENOMINATOR_MISSING`）。
契约指纹 `contract_fingerprint`（不含绑定 ref——换内容才算变化）。

### 4.4 S4：`WorkflowExecutor`（dispatch → envelope → 指纹重算）

- 波次：`_waves`（Kahn 拓扑）同波并发、波间顺序；波前依赖未满足 → `blocked`。
- 执行真相：`registry.dispatch(tool, args, session_id)`（工具面
  `_production_dispatch` 接共享 ToolDispatchService）；步骤产物经 session store
  铸 `ref:geojson-*`，实测事实（`envelope_facts`）入 `AnalysisArtifactEnvelope`。
- 幂等：步骤 satisfied 且 `step_fingerprint`（`recompute.py`；schema=2，单位/
  语义合同/科学上下文/输入内容身份全入指纹）不变 → 直接复用旧 envelope。
- 科学终审：全步骤终态后 `run_final_gate(state)` → `state.science_validation =
  {final_gate: pass|blocked|not_evaluated, checked_in_turn, findings_n}`。

### 4.5 S5：验收桥（completion/pipeline 接线点）

生产入口 `evaluation_view_with_workflow_rows(session_id, chapter)`（async）：

- `load_workflow_runs`：枚举 session store alias 前缀 `analysis_workflow.v1:`
  （≤8，按 workflow_id 确定性排序）；store 缺席 → 诚实空。
- `merge_workflow_rows`：`project_workflow_rows` 把每步骤投影为 chapter
  `analysis_steps` 行——`capability=awf.<family>.<step_id>`（≤64，与 plan 行零
  冲突）、状态直映（`satisfied→complete / failed→failed / blocked→unavailable /
  skipped→skipped`；pending/running/stale→pending；cancelled→failed）、
  `bound_ref=envelope.ref`、`resolved_algorithm=envelope.method_algorithm_id`；
  按 capability 幂等去重，行上限 64。**只进评估视图，不写回持久 chapter**。
- `workflow_disclosures`：`science_validation.final_gate != pass`、
  `unmatched_demand_count` 合计、`skipped_steps`（≤6 条）。
- 接线（`app/services/gis_harness/completion/pipeline.py` ~1304-1345，
  `run_map_finalization` 内）：合并视图作为 `map_product_block(chapter=…)` 的
  评估输入（行指纹守卫仍消费原 `fresh.gis_chapter`）；披露经
  `workflow_disclosures=` 参数持久化到块（≤8 条，additive 键）；块内
  `evaluate_goal_satisfaction` 消费合并视图 → `derive_goal_contract` 把每行造为
  `analysis:<capability>` 需求、`build_evidence_registry` 把行状态造为
  deterministic `TOOL_RECEIPT` 证据、`_eval_row_requirement` 裁决——
  **节点成功（步骤 complete）≠ 目标达成**（map/export 义务仍按既有规则）。
- 接线现状注记：`evaluation_view_with_workflow_rows` /
  `load_workflow_runs` 已在 `execution_compiler/__init__.py` re-export
  （review-B 发现的静默 ImportError 已修复并有 oracles 测试钉死）；
  finalizer 锁外预取运行清单（P2-4），锁内纯内存合并。

### 4.6 工具面（`app/tools/analysis_workflow_tools.py`）

`webgis_requirement_compile`（tier-2，`side_effect="pure"`，`deterministic=True`）：
读会话 `RequirementDocument`（`requirement_ir.service.load_state`）→
`compile_requirement_to_tasks(doc, data_facts=...)` → `to_bounded_dict()` 有界
返回。typed 拒绝码：`REQUIREMENT_DOCUMENT_ABSENT` / `REQUIREMENT_DOCUMENT_INVALID`
/ `AWF_BAD_JSON`。纪律写进 description：选族优先取 top 候选，显式偏离必须披露。
plan 工具（`webgis_analysis_workflow_plan`）的 family 参数仍是显式入口——
编译器不接管，只提供排序证据。

## 5. patch 差分 → 选择性重算（C4 / GC-06 / GC-09）

`patch_invalidation_scope(old_doc, new_doc, *, contract=None)` → `InvalidationScope`。

### 5.1 语义快照与变化类

`_snap` 把 IR 分成 style 面（palette / geometry_kind / hidden / locked / pins /
components / output_purpose / dpi / camera）与科学面（aoi / subject / measure /
time / spatial_relation / scene_data）；`_change_classes` 输出 7 个布尔变化类；
`_semantic_change` = 任一非 style 类为真。

### 5.2 角色失效推导（closed；词表 = `workflow_schema.DATA_ROLES`）

| 变化类 | 失效角色 | 理由 |
|---|---|---|
| aoi | **全量**（契约在场取 `contract.data_contracts` 全部键；缺席退化到 10 角色固定集） | 数据是 AOI 切片产物——跨城市旧 ref 不得假匹配（GC-06/07） |
| subject | `subject` + `facility` | 同为主题数据面 |
| measure | `normalization ∈ per_capita` 或新统计词含 `rate/ratio` 时 + `denominator`；否则不动 denominator | 计数→面积密度只动 boundary 派生步骤，不动 subject ref |
| time | `baseline` + `target_time` | 双期身份变化 |
| spatial_relation | `network` | 等时/邻近的权重面 |
| style | ∅（永不失效科学面） | 与 `recompute.style_only_invalidation` 同一钉扎语义 |

### 5.3 步骤失效与复用证明链

步骤失效集 = 消费失效角色的步骤（`role_inputs` 前缀 `role:`）∪ 参数语义直落
measure 的步骤（`step_params` 含 `denominator_kind` / `value_field` /
`numerator_field` 且 measure 变化）∪ 沿 `depends_on`+`step:` 边的下游闭包；
其余步骤进 `preserved_steps`（复用证明面）。

| 场景 | 期望（characterization 钉扎） |
|---|---|
| style-only（改色） | `style_changed=True`、科学失效集与失效角色皆空——零科学重算 |
| 换城市（成都→北京，样式不变） | `style_changed=False`、全量角色失效、`science_invalidated_steps` 非空（角色失效必须传播到步骤） |
| 统计变化（→每万人） | `per_capita_rate` 失效、`zones_count` 保留（输入未变不重跑昂贵计算）——最小集证明 |

### 5.4 与 recompute 指纹层的分层

本层做**需求语义差分**（哪些角色/步骤的语义身份变了），`analysis_workflows/
recompute.py` 做**内容身份差分**（`ref@content_revision` / 上游指纹，`step_
fingerprint` 精确到参数/单位/科学上下文）。本层输出是执行计划失效种子的
**上限**；两层都过才是最小重算。

## 6. 族声明 conformance（C3 / GC-03）

`audit_family_conformance(*, tool_exists=None, family_ids=None)` →
`FamilyConformanceReport`（`family_conformance.v1`；确定性排序 + sha256 指纹
前 32 hex，漂移检测面）。对齐 `tool_capability_conformance` 纪律：只读投影、
不接管裁决、fail 不进启动闸——运行期权威仍在 `validate_workflow_contract` /
registry dispatch chokepoint。

### 6.1 FC_* 码表（`catalog_conformance.py`）

| 码 | 对账面 | 当前状态 |
|---|---|---|
| `FC_TOOL_DANGLING` | 步骤 tool 在 ToolRegistry 未注册（模板腐烂，先于 run 期 `AWF_TOOL_UNREGISTERED` 可见） | 发射 |
| `FC_CAPABILITY_DANGLING` | `capability_registry.get` 不可解析 | 发射 |
| `FC_UNIT_OUT_OF_VOCAB` | `unit_contract` 单位 ∉ `UNIT_VOCABULARY`（m/km/min/h/unitless） | 发射 |
| `FC_PRECONDITION_DANGLING` | 科学前置 `precondition_exists` 未注册 | 发射 |
| `FC_ROLE_OUT_OF_VOCAB` | 族角色 ∉ `DATA_ROLES`（`workflow_schema`） | 发射 |

（envelope 实测口径的对账——aggregation_basis 词表/实测单位量纲——属运行期
证据面，由 `run_final_gate` / envelope v2 `result_semantics` 承载，静态层不保留
死常量；量纲互斥守卫在 `units.dimensions_compatible`（SCIENTIFIC_ORACLES.md
U1），声明面单位词表校验即上表 `FC_UNIT_OUT_OF_VOCAB`。）

### 6.2 registry 视图与三态裁决

- `tool_exists=None` → live 视图：`app.agent_pi_bridge.try_get_tool_registry()`
  的 `registry.has`（lifespan 缺席 → 该步 `not_evaluated`，诚实留白不虚构 pass）；
  测试可注入 `tool_exists=lambda t: False` → 全悬空 fail（fail-closed 验证）。
- 步裁决 `pass | fail | not_evaluated`（封闭）。各面缺席语义：capability
  registry 不可用 → 按不可解析（fail）；scientific_preconditions 注册面不可用
  → 该步 `not_evaluated`；角色对账是 `DATA_ROLES` 纯词表成员判定（无注册面）。

## 7. 「不建第二事实源」边界表

| 事实源 | 权威在 | 本层角色 |
|---|---|---|
| 意图理解 | `MapRequestIntent` / `resolve_map_request_intent` | IR sections 与编译信号都是 core 的投影 |
| 需求文档 | `requirement_ir`（patch/service；`requirement_document.v1`） | 编译器只读 doc；genesis 收割在 IR 层内完成 |
| 族/角色/单位词表 | `families.FAMILY_IDS` / `workflow_schema.DATA_ROLES` / `analysis_workflows.contracts.UNIT_VOCABULARY` | 只消费（`RoleNeed`/信号/义务投影），零新词表（量纲表见 SCIENTIFIC_ORACLES.md §4 分工） |
| 族选择与 plan 裁决 | `plan_workflow` / orchestrator typed 门 | 只排序候选；override 由工具面披露 |
| 执行真相 | `registry.dispatch` / ToolDispatchService | executor 薄循环；conformance 只审计注册面 |
| 重算 | `recompute.step_fingerprint`（内容身份） | `InvalidationScope` 是语义失效种子上限 |
| 科学裁决 | `scientific_preconditions` / `science_final_gate` / 工具 apply_contract | `units.py` 仅编译期声明性对账（不重合、不接管） |
| 目标验收 | `goal_satisfaction`（ADR-0183；deterministic/structural 才可背书 PASS） | 桥是只读行投影 + 披露，评估器零改动 |
| 注册中心 | ToolRegistry / `capability_registry` / 前置注册表 | `audit_family_conformance` 是只读对账报告 |

## 8. 测试锚点

- `tests/unit/gis_harness/test_execution_compiler_characterization.py`（19 测试）：
  C1 确定性/信号溯源/表达分离/点名优先/blocked/needs_clarification；C2 行投影/
  节点成功≠目标达成/failed 诚实阻断；C3 live 对账/悬空 fail/指纹确定；C4
  style-only 零失效/换城全量/统计最小集；C5 单位义务四态。
- `tests/unit/gis_harness/test_execution_compiler_oracles.py`：**由本 PR 后续
  commit 添加**（当前不存在），计划覆盖面见 `SCIENTIFIC_ORACLES.md` §3.2。
