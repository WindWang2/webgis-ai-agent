# GeoWorkflow Architecture —— 专业 GIS 工作流知识库与编译管线（Goal C / ADR-0101）

## 一句话

LLM 负责理解语言并给出语义建议；**GIS 方法、资格检查、科学限制、fallback、
完成条件全部由代码侧确定性 contract 保证**。

```text
LLM / semantic intent
  ↓ ① resolve_map_request_intent（intent.py，纯正则词表，双语）
deterministic task normalization
  ↓ ② resolve_task_family（24 个任务族：18 个 V1 + 6 个 Workflow V2）
data requirements
  ↓ ③ resolve_data_roles（workflow_schema：15 个数据角色）
dataset profile
  ↓  （Spatial Meta Profile / resolver camelCase 事实，零全量扫描）
scientific preconditions
  ↓ ④ evaluate_obligations（workflow_schema → scientific_preconditions，
  ↓    单一事实源联动，不重复实现科学语义）
capability graph
  ↓ ⑤ compile_capability_dag（plan_graph：依赖推理 + 幂等求值）
algorithm resolution
  ↓ ⑥ resolve_algorithms（AlgorithmResolver：fallback_trail + cost）
analysis recipe
  ↓ ⑦ RecipeRegistry.select_candidates（keyword 路由 + seed 资历守卫）
cartographic model/template requirements
  ↓ ⑧ resolve_cartography（MapModelRegistry / ProductTemplateRegistry）
Map Product
  ↓ ⑨ produce_map_product_plan（MapProductPlanner 两阶段 draft/finalize）
completion/evidence/verdict
  ↓ ⑩ produce_completion_contract + derive_product_verdict（V2 七维）
```

入口：`app/services/gis_harness/workflow_compiler.py::compile_workflow`
（12 阶段确定性管线，零 LLM / 零 I/O，产物 bounded / serializable）。

## 分层职责（不建第二事实源）

| 层 | 模块 | 职责 | 明确不做 |
| --- | --- | --- | --- |
| 意图 | `gis_harness/intent.py` | NL → typed task/scope/subject/derived intents | 不选 recipe、不裁科学资格 |
| 方法 | `gis_harness/recipes.py` + `recipe_packs/` | 「这类工作流需要什么」的声明式契约 | 不硬编码工具序列、不是 workflow engine |
| 契约 | `gis_harness/workflow_schema.py` | 数据角色 / 科学义务 / 完成维度 / 语义回退 / 指纹 | 不实现科学检查（委托算法层） |
| 编排 | `gis_harness/workflow_compiler.py` | 12 阶段确定性编译 | 不执行、不持久（SessionPlan 负责） |
| 裁决 | `gis_harness/completion/contracts.py` | 完成七维 + 单字产品裁决 | 不修复（repairs.py / runtime_repair.py 负责） |
| 执行 | SessionPlan + Pi runtime | durable execution、工具分派 | 不理解 GIS 语义 |

## Recipe 路由的两条守卫（V2 扩容不漂移既有行为的关键）

`RecipeRegistry.select_candidates` 的排序键（V2 后共十层，前四层最关键）：

1. geometry 期望失配（#781，栅格主体绝不推荐 POI 热力族）
2. **seed 资历**：V1 seed 服务的任务族里，V2 recipe 后置 —— 通用短语
   （「各区小学数量」「地表覆盖分布」）的产品族契约不因知识库扩容漂移
3. task 精确命中
4. **V2 通用罚**：同 V2 之间，query 未命中其专业关键词者后置

效果：

- **通用表述** → 仍旧 17 个 V1 seed 产品族（306 个既有 corpus 案例零漂移）；
- **新任务族**（terrain / watershed / sar / autocorrelation / trend /
  network_route —— 无 V1 seed）→ V2 专业工作流直接路由；
- **专业关键词**（克里金/莫兰/流域/InSAR…）→ 族内关键词倒排索引路由，
  一次查询 O(命中数)（`_keyword_index`，register 时构建）；
- LLM hint（`merge_intent_hints`）可以把 task 升到新任务族 —— 这是专业
  工作流的语义入口；hint 不能降级受保护任务族。

## 与并行分支的边界

- 算法/能力/组件/模板只通过公开 registry 消费（64 capabilities / 88
  algorithms / 19 map models），recipe packs 不复制它们的事实；
- 所有 recipe 引用（capability / map model / artifact type / precondition
  id）经 `registry_validation` 与 `runtime_manifest` 编译期校验，
  capability 悬空是 fatal；
- workflow 语义变化 → `recipe_content_fingerprint` 变化 →
  `runtime_manifest.fingerprint` 变化 → 旧计划 `is_stale_plan` 可感知
  （复用既有 manifest 体系，无第二套 manifest）。

## 深读

- [Recipe DSL V2](./recipe-dsl.md)
- [数据角色词表](./data-roles.md)
- [回退语义](./fallback-semantics.md)
- [完成契约与 Verdict V2](./completion-contract.md)
- [Workflow 编写指南](./authoring-guide.md)
- [一致性语料库](./conformance-corpus.md)
- [Workflow Catalog（自动生成）](./workflow-catalog.md)
