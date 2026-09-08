# Workflow V4 — Architecture (Phase B)

- date: 2026-09-09
- baseline: `445ad30e`
- ADR：实现后补 `docs/adr/0118-semantic-workflow-compiler-v4.md`

## 1. Current-state dependency graph（审计证实）

```
chat 生产链（两段式 planner，ADR-0104）:
  plan_orchestrator._synth_plan_from_harness
    → resolve_map_request_intent → RecipeRegistry.select_candidates
    → MapProductPlanner.plan_from_intent → finalize_with_profile
    → SessionPlan / workflow_instance（运行态，wave1 已落地）

评估链（evaluation-only）:
  compile_workflow（15 阶段）
    → intent / gis_ontology / recipes / workflow_schema.resolve_data_roles
    / data_qualification / plan_candidates / fallback_v3 / plan_graph
    / evaluate_workflow_obligations → WorkflowCompilation

单一事实源（V4 只引用，不复制）:
  CapabilityRegistry(121) · AlgorithmRegistry(181, AlgorithmDescriptor 自带
  input/output artifact types + crs_requirements + unit_requirements +
  min_features + scientific_preconditions + fallback_semantics)
  · ArtifactTypeRegistry(21) · MapModelRegistry · RecipeRegistry(164)
  · TaskOntology(47 任务/9 域) · WorkflowFamilyRegistry(152/12/7)
  · scientific_preconditions（算法层）
```

## 2. 核心判断

1. **V4 不做第二套 planner**：V4 = 编译期语义层（决定"应该做什么、为什么、
   有哪些候选方法、需要满足什么义务"），执行仍归 Harness V4/V5 runtime。
2. **最大缺口 = 编译器与生产链的分叉**（审计 (b)1）。V4 的生产接入取
   "证据上行"而非"替换 planner"：`compile_workflow_v4` 作为 additive
   包装（15 阶段不动），V4 产物经 (a) 语义工具暴露给 agent 面，
   (b) plan_orchestrator 合成路径附加有界 evidence —— 零行为漂移。
3. **Typed DAG 引用既有算法契约**：AlgorithmDescriptor 已有
   input/output_artifact_types、crs/unit_requirements、min_features、
   scientific_preconditions —— typed port 校验直接消费这些事实，不新造
   词表。capability_dag 的手工 dict 投影升级为 typed 投影（保留旧键）。

## 3. Target-state component graph

```
app/services/gis_harness/workflow_v4/        ← 本 Epic ownership（新子包）
  methodology.py      12 方法族 + MethodCandidate + qualification ranking
  typed_dag.py        TypedWorkflowNode/Port/Edge + 兼容性校验
  compiler_v4.py      compile_workflow_v4（wrap 15 阶段 + 6 个 V4 阶段）
  obligations.py      义务继承（composite/nested）+ provenance 链
  package.py          WorkflowPackage（semver + immutable compiled + 兼容性）
  parameters.py       参数解析 + provenance（默认自动解析，不阻塞人工）
  recompute.py        变更 → affected subgraph（复用 RECOMPUTE_DIMENSIONS）
  diff.py             语义 diff（recipe/参数/算法/义务）→ 为什么重算
  acquisition.py      获取备选声明（local→catalog→provider→upload→derived）
  cartography.py      制图表达-数据资格义务规则
  evaluation.py       编译器评估（正确性/完备性/拒绝/确定性/可解释）

消费方（最小修改）:
  app/tools/semantic_tools.py          + compile_workflow_semantics 工具
  app/services/chat/plan_orchestrator.py  合成路径附加 workflow_v4 有界证据
  app/evaluation/methodology_corpus.py    双语方法语料（新）
```

## 4. 权威数据/状态所有权

| 事实 | 唯一写者 | V4 角色 |
|---|---|---|
| 数据角色/义务/完成契约 | workflow_schema（V2 DSL） | 只读引用+继承组合 |
| 资格裁决（五态） | data_qualification | 只读引用 |
| 算法资格事实 | AlgorithmDescriptor（算法层） | typed port 校验引用 |
| 方法族 | workflow_v4/methodology.py（新，唯一写者） | — |
| compiled 包指纹 | workflow_v4/package.py（新） | 派生自编译产物 |
| 运行态 | workflow_instance / SessionPlan | 不触碰 |

## 5. 关键语义决策

- **确定性**：全管线同输入同输出、零 LLM、零 I/O（与 V3 同红线）。
- **qualify 排序取代任意选择**：MethodCandidate ranking = 资格裁决映射
  （eligible=1.0/transform_required=0.75/degraded=0.4/blocked=0/unknown=0.5，
  与 plan_candidates._DATA_FIT_SCORE 同表引用）+ 算法层事实
  （preconditions、approximate 降分、deterministic 加分），rejected 候选带
  机器可读 reason codes —— LLM 不得绕过。
- **义务继承**：composite/nested 传播 = 按 obligation_id 去重的并集，
  provenance 记录来源链（recipe → composite → package）；on_violation 取
  最强（block_method > degrade_with_disclosure > warn），幂等。
- **参数**：默认值确定性自动解析（profile 事实 → 默认 → 用户覆盖），
  provenance ∈ {profile_derived, recipe_default, user, hint}；无人工确认
  阻塞；歧义但可安全消歧 → 走默认 + 披露。
- **Partial recompute**：变更分类复用 RECOMPUTE_DIMENSIONS；受影响子图 =
  typed DAG 上的正向闭包（输入角色/参数/算法变更 → 消费节点 → 下游）；
  可复用节点必须无变更路径可达。执行留 Harness（V4 只算集合）。
- **版本**：WorkflowPackage semver（major=方法族契约破坏，minor=候选/
  义务增补，patch=文案）；immutable compiled form = canonical JSON +
  sha256；兼容性 = compiler major 相同 + schema 版本 ≤ 当前。
- **获取备选**：compiler 只声明 ordered alternatives + feasibility 条件，
  不抓取；synthetic_demo 仅显式 opt-in（must_not_guess 红线延续）。
- **制图义务**：表达-资格规则（如 density 语义禁 raw-count choropleth、
  rate 需 denominator、points<N → proportional symbol），输出
  CartographicObligation 进完成契约（cartography 维已存在，additive）。

## 6. API / typed contract

- 公开新 API 全部在 `workflow_v4/`；旧公开 API 零签名变化
  （COMPILER_STAGES 15 阶段测试锁定不动）。
- `compile_workflow_v4(query, **kwargs) -> WorkflowCompilationV4`；
  `WorkflowCompilationV4.base: WorkflowCompilation` + v4 字段
  （methodology/typed_dag/package/acquisition/cartographic_obligations/
  v4_stages），`to_bounded_dict()` 有界可序列化。

## 7. 持久化 / migration

**不新增 DB migration**。V4 是编译期契约层：packages/diff/recompute 都是
可重建的纯函数产物；运行态归属 workflow_instance（Redis 会话章），
promoted workflow 归 WorkflowEngine。避免 Alembic head 冲突（并行 Epic）。

## 8. 取消/超时/重试

编译是纯函数（毫秒级），无取消语义需求；执行期语义仍归 runtime。
测试加 wall-clock 预算护栏（宽松，结构性预算为主：阶段数/产物有界）。

## 9. 可观测性 / evidence

每个 V4 阶段产出 WorkflowStageRecord（reason codes + bounded evidence，
同 V3 形态）；package 指纹 + compiler_version 进产物，供 trace 对账。

## 10. 失败模式与对策

| 失败模式 | 对策 |
|---|---|
| 方法族引用悬空任务/能力 | 注册期 validate（复用 registry_validation 谓词注入模式），fail-loud |
| typed port 不兼容（artifact/geometry/CRS/unit） | 编译期 port 兼容性检查 → 阻断节点 + reason code，不静默降级 |
| 义务继承幂等性破坏 | 纯函数 + 幂等测试（inherit(inherit(x)) == inherit(x)） |
| 排序不稳定 | 全排序键为确定性元组（分数, -priority, id），测试锁定 |
| 指纹漂移 | canonical JSON（sort_keys）+ 同输入双编译同指纹测试 |

## 11. 性能预算

- compile_workflow_v4 总时长结构性预算：V4 阶段 ≤ 6、产物有界
  （to_bounded_dict < 64KB，同 V3）；wall-clock 护栏 < 2s（宽松，防回归）。
- recompute/diff 为 O(nodes+edges) 图遍历；语料评估 < 5s（48+ 案例）。

## 12. 测试 oracle

- oracle = 既有 registry 词表 + scientific preconditions + 数据资格五态
  （全部既有事实）；语料期望为**语义计划**（族/任务/角色/义务/拒绝），
  不是工具序列。
- 确定性：同输入双编译 → base 与 v4 产物逐字段相等 + 同指纹。

## 13. Rollout / fallback

- 纯 additive：不启用时生产行为零变化；语义工具不可注册时 agent 面回落
  既有工具；plan_orchestrator 证据附加 try/except 包裹（失败 → 无证据，
  不阻塞合成）。

## 14. 与并行 Epic 的边界

- Harness V5（runtime 执行/恢复）→ 消费 V4 的 package/recompute 声明，
  接口 = typed DAG + affected subgraph 纯函数（typed interface 隔离）。
- Science V4（算法数值）→ 算法层事实由其拥有，V4 只引用 descriptor。
- GeoCompute V6（调度）→ 无接触。
- Cartography V5（渲染）→ cartography.py 只产义务声明，不产渲染规格。
