# Capability Graph V1 — Recon（Phase 0，执行时事实）

- 基线：`origin/master` = `580b33e9`（#1272 ADS-V1 合并后）。
- 本任务 branch：`harness/gis-capability-graph-v1`（worktree `.worktrees/gis-capability-graph-v1`）。
- 勘察方式：独立 read-only 勘察代理（全仓 registry 枚举 + 生产链追踪）+ 主 Agent 精读 ADR-0137 / capability_descriptors.py / pi-host-seams.md。

## 1. 当前 master 已有能力（与本方向直接相关）

| 能力 | 位置 | 状态 |
| --- | --- | --- |
| Capability registry（153 条，15 域包） | `app/lib/gis/capability_registry.py` + `app/lib/gis/capabilities/` | 事实源；有 deterministic/fallback_capabilities/status/version |
| Algorithm registry（230 条，18 域包） | `app/lib/gis/algorithm_registry.py` + `app/lib/gis/algorithms/` | 事实源；~55 字段含 fallback_algorithms/semantics、resource_envelope |
| Tool registry（327 条） | `app/tools/registry.py` + `app/tools/descriptor.py` | 事实源；tier/side_effect/network/deterministic/idempotent/version/deprecation_of 齐全 |
| V7 结构化检索描述符 | `app/services/gis_harness/capability_descriptors.py` | **生产在用**：`tool_surface_v3.descriptor_boosts` 喂 V3 rerank |
| V8 统一能力图（ADR-0137） | `app/services/gis_harness/capability_graph.py` | 派生只读投影；kind 词表 11 类但只投影 6 类；关系词表 13 种只发射 7 种 |
| Qualification engine（六面资格） | `app/services/gis_harness/qualification_v8.py` | **生产零调用**（只有 candidate_planner_v8 和测试调用） |
| 候选规划 plan_candidates_v8 | `app/services/gis_harness/candidate_planner_v8.py` | **生产零调用**（grep app/ 无 caller） |
| Recipe 体系（164 条，ADR-0151/0161） | `app/services/gis_harness/recipes.py` + `recipe_packs/`（24 域包） | **生产在用**：`recipes.select_candidates`（十一层稳定排序）+ `check_eligibility` + `resolve_fallback_chain`；intent evidence/affinity（0057 迁移） |
| Product templates（9）/ style catalog / components（21） | `product_templates.py` / `template_catalog.py` / `app/lib/cartography/component_registry.py` | component 有 dependency/conflict 引用校验；未投影入图 |
| Data Fabric adapters（15）/ ADS source registry（12 YAML） | `app/services/data_fabric/registry.py` / `source_registry.py` | ADS fallback（ADR-0174）在 `matrix.py` 生产在用；未投影入图 |
| ModelOps registry | `app/services/modelops/registry.py` | 已投影入图（model 节点 + implements 边） |
| 生成物纪律 | `docs/quality/generated-artifacts.json` + `scripts/gen_*.py` + `app/lib/quality/artifact_graph.py` + `scripts/check_generated_staleness.py` | 新 catalog 走同一套：确定性生成器 → 登记 artifact_graph → 刷新 ledger |
| 注册表校验闸 | `registry_validation.validate_gis_library`（含 `validate_graph`） | 仅测试/工具面调用；error 级发现为零是既有闸语义 |

## 2. 生产调用链（before）

```
两条宿主路径（USE_NEW_AGENT 默认 True）：
ChatEngine:  execution_engine._maybe_plan → plan_orchestrator.orchestrate_plan
               ├─ _synth_plan_from_harness: resolve_map_request_intent
               │     → RecipeRegistry.select_candidates → planner.plan_from_intent（零 LLM 确定性 plan）
               └─ LLM make_plan → parse_plan → _apply_capability_validation
                    → resolve_map_request_intent + recipes.select_candidates 附着（capture_intent_adjudication）
工具面:      execution_engine → compile_tool_surface → tool_catalog.select_schemas（tier/keyword/domain）
               + tool_surface_v3.DynamicToolSurface.select（reasons/dropped/abstain）
               ← descriptor_boosts(get_capability_index_cached())  ← V7 描述符唯一生产消费点
Pi 路径:     pi_native_surface.compute_turn_active_tools → DynamicToolSurface → WEBGIS_ACTIVE_TOOLS
               LLM → webgis_execute 代理 → ToolDispatchService.dispatch（单一执行管线）
               → webgis_map_intent（Shared 确定性工具）→ planner.plan_from_intent → finalize_with_profile
                 → check_eligibility / resolve_fallback_chain（ADR-0151）→ intent_learning 钩子（ADR-0161）
V8 图:       build_capability_graph（capability/algorithm/tool/model/artifact_type/provider 六段）
               ← candidate_planner_v8 / qualification_v8（生产零调用）/ registry_validation（测试面）
数据供给:    ADS retrieval（explorer/matrix）与 chat 规划链**无交汇**。
```

结论：能力图已经是"派生投影"，但 **plan_candidates_v8 + qualification_v8 是孤儿模块**；
V7 描述符只在 tool surface rerank 有一个消费点；能力图不是 planning 的一等输入 ——
这正是本任务核心缺口，也是任务书"不是文档摆设"验收的靶心。

## 3. 与最近 PR 的重叠矩阵

| 最近/在途 PR | 触及面 | 与本任务重叠 | 处置 |
| --- | --- | --- | --- |
| #1271 AC-V11（ADR-0160-0169，已合并） | intent learning、recipe、symbology、label、layout、export、quality baseline | recipe eligibility/affinity 语义是 C3/C5 的**复用对象**（不复制实现，直接调用）；无文件级冲突预期 | 复用 + 调用 |
| #1272 ADS-V1（ADR-0170-0179，已合并） | source registry、retrieval、fallback chains、version pinning | ADS fallback/retrieval 是数据供给 provider 事实；C1 把 adapters/sources 投影入图（只读） | 投影，不改其内部 |
| #1270 CI hygiene（open） | `app/services/mapspec/coordinator.py`、`frontend/lib/mapspec-compiler/compiler.ts`、`docs/quality/*`、`tests/conftest.py` | 仅 `docs/quality/*` 生成物 ledger 可能同窗重生成 | 不吞入；若 quality manifest 因本任务新增生成物需刷新，PR 中归因说明 |
| tts 线（worktree 锁定，0c933375 仅 docs，未提交代码） | 计划：pi surface 字节预算 / input gate / metrics；**拟占 ADR-0180** | 本任务只提供**只读 capability query protocol**，不动 pi surface | 接口让位：本任务取 ADR-0181 |

## 4. 硬编码地图点审计（C0 输入，勘察 top-15）

1. `app/services/tool_catalog.py:41` DOMAIN_KEYWORDS（9 域 + ~100 城市省份字面量）→ select_schemas/plan 域词表
2. `app/services/chat/pi_native_surface.py:29-56` NATIVE_TOOL_NAMES/_LABELS/_SNIPPETS（front-door 工具名硬编码）
3. `app/services/gis_harness/tool_surface.py:40-96` _DOMAIN_PHASE/_PHASE_PREFERRED/_PHASE_DOMAINS/_ACTION_PHASE
4. `app/services/gis_harness/action_intent.py:85-119` _ACTION_MODE/_ACTION_CLASS/_ADVISOR_ACTION_MAP
5. `app/services/gis_harness/capability_graph.py:520-531` _MODELOPS_TASK_TO_CAPABILITY（有意收敛的词汇单表）
6. `app/services/chat/plan_orchestrator.py:71` _TASK_DOMAIN_HINTS
7. `app/services/gis_harness/planner.py:232-247` 插值关键词门 + algorithm_hint="interpolation.kriging"
8. `app/services/gis_harness/planner.py:977-999` recipe 排序 keyword 规则
9. `app/lib/gis/algorithm_registry.py:493` _is_analysis_capability 启发式
10. `app/services/session_plan.py:662` `tool_name == "webgis_map_intent"` 特例
11. `app/services/chat/execution_engine.py:1072,1102` classify_followup 复用 DOMAIN_KEYWORDS
12. `app/services/chat/tool_surface_v3.py:357` 跨层 import 私有 _PHASE_PREFERRED
13. `app/services/gis_harness/recipes.py:102` DEFAULT_FALLBACK_CHAIN 硬编码 recipe id
14. `app/tools/registry.py:130` tier3_confirmed + tool_catalog.py:207-215 tier 数值政策
15. `app/lib/cartography/component_registry.py:1029-1032` dependency/conflict id 校验（软）

处置：本任务**不批量消灭**这些表（超出安全面），而是在 C0 审计中给出
"哪些表应当由 capability graph 派生"的清单，并让 planner 路径的新代码经
resolve_capabilities 而不是再添第 N 张硬编码表；已有表保持不动（回归风险控制）。

## 5. Descriptor 字段差距（C1 依据）

现有 `CapabilityDescriptorV7`（capability_descriptors.py:63-91）有：
id/kind/label/corpus_text、geometry/crs/min_features/required_fields/scientific 前提、
input/output_artifact_types、uncertainty_outputs、cpu/memory/io cost、complexity、
preferred_execution、fallback_chain、related_tools。

缺（本任务补投影或补 owner 声明）：domain、semantic purpose（description/purpose_template 未投影）、
required situation facts（数据量/分辨率/波段在 qualification 检查但描述符不声明）、
effects on world state（tool 层 map_mutations/data_mutations 有）、destructive/side-effect level（tool 层有）、
auth/tier（tool 层有）、latency hint（tool 层 latency_class 有）、offline（tool 层 network 有）、
deterministic/idempotent（三层均有但未投影；且 select_capabilities 文档承诺的"确定性偏好"代码未实现）、
rollback（全库缺失，唯一近似是 ToolRegistry.unregister）、evidence produced（仅 uncertainty_outputs）、
providers（recipe/template/adapter 面）、incompatibilities（仅 component registry）、version/deprecation（三层均有未投影）。

## 6. 图验证差距（C2/C8 依据）

- 词表 13 关系中 invokes/requires/contains/composed_of/binds_to/executed_by **定义了但从不发射**；
- 无环检测；无"输出类型无消费者"检查；无"不可能前提"检查；无"deprecated provider 被选中"检查；
- validate_graph 发现经 registry_validation 汇聚（error 级 fatal 语义已存在），生产启动只校验 runtime manifest。

## 7. 防重复结论（复用 / 扩展 / 不做）

**复用（直接调用，不复制）**：capability/algorithm/tool/capability-graph 构建器、
qualification_v8 检查语义、candidate_planner_v8 排序/tie-break、recipes.check_eligibility /
resolve_fallback_chain、intent_learning affinity、ADS retrieval/fallback、
reliability_from_ledger、artifact_graph 生成物纪律、registry_validation 闸语义。

**扩展（在原 owner 处加声明 + 投影）**：capability registry 增补 offline/side_effect/rollback/
evidence/incompatible 等可选声明字段；capability_graph 增 recipe/template/component/adapter 四段投影 +
requires/composed_of/binds_to/conflicts_with 边 + 环/孤儿/无消费者/deprecated 验证；
QualificationContext 增 offline/budget/auth_tier（additive optional）。

**不做（防重复/防越界）**：不建第 N 套 registry；不复制 Pi tool loop；不实现 scheduler（方向 5）；
不动 pi surface 动态面（方向 4，只给只读查询协议）；不吞 #1270；不批量重写 15 张硬编码表；
不做 Alembic 迁移（零表变更，0057 已够）。

## 8. 测试与基线

- 相关测试：tests/unit/gis_harness/test_capability_graph_v8.py（23）、test_capability_descriptors_v7.py（15）、
  test_eligibility_v4.py（44）、test_plan_candidates.py（11）、test_planner.py（14）等。
- pytest：asyncio_mode=auto、timeout=60、默认 --cov（迭代用 --no-cov）、xdist 可用（本任务限 -n 2）。
- master 已知失败：QUALITY_REPORT 仅记录科学/制图套件 xfail（strict=False）；无其他已记录失败；
  最终回归时若遇失败需做干净 master 对照归因。
