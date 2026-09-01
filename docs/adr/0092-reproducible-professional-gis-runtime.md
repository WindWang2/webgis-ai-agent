# ADR-0092: Reproducible Professional GIS Project & Analysis Runtime

- 状态: Accepted
- 日期: 2026-09-02
- 关联: ADR-0076 (SessionPlan), ADR-0081/0085/0087 (Product Finalization / ProductGraph / Facet Contract), ADR-0082 (ArtifactRegistry), ADR-0069 (项目制图记忆), ADR-0090/0091 (Workspace Interaction / Selection V2)

## Context

系统已经能通过 Pi + GIS Harness 完成一次 GIS 分析并产出地图产品，但审计（`.audit/next-professional-gis-runtime-audit.md`，baseline 49973c2）暴露了四组结构性缺口：

1. **不可复现**：SessionPlan → Workflow 只靠 LLM 手抄 steps（capability/算法/参数/产品要求全部丢失）；WorkflowRun 产物本体存放在 4h TTL 的 SessionStore，DB 里是指针；run manifest 不含 runtime manifest 指纹 / capability / algorithm / MapSpec / facet 证据。数周后既解释不了也重跑不了。
2. **不可评估**：没有面向完整 GIS 任务的语义回归基准——现有 golden 测试分散在 pytest 里，没有 query → 期望能力/禁用算法 → metrics 的契约与 CLI。
3. **语义缺位**：DatasetProfile 只有结构画像（dtype/几何/数量），没有字段语义角色（人口？面积？行政维度？），Agent 无法区分「数 schools」和「评价教育公平」这类方法论差异。
4. **OD/flow 断链**：`network_od_matrix` 能算成本，但 `flow_od_arc` MapModel 是 planned、无 OD→流向线要素工具、无前端通道，recipe 缺失——审计 Q10 逐层确认。

同时，Phase E（Kriging + Uncertainty）在本轮中**未实施**（见 Deferred）。

## Decision

在**既有**体系上做四件事，不新建任何并行系统。

### 1. State Ownership（不变式重申）

- Pi 仍是 Agent Host；SessionPlan 仍是会话计划真相；MapSpec 仍是地图期望态唯一真相；ToolRegistry 仍是唯一执行入口；无第二 SessionPlan / MapSpec / Artifact truth / workflow engine。
- 本 ADR 新增的一切都是**既有实体的扩展或派生投影**。

### 2. Reproducible Project Runtime（Phase A）

**A1 SessionPlan → Workflow promotion（确定性转换器）**
- 新增 `app/services/gis_harness/workflow_promotion.py`：成功计划（`promotion_blockers` 校验所有必需能力行 resolved）→ `WorkflowCreate`。steps 以 capability 为一等语义（`WorkflowStepSpec.capability/algorithm_preference/input_roles/description` 新增可选字段），tool id 降级为执行证据；`args_template` 只含可重放参数（bound_ref/status 等会话绑定字段被剥离）；产品要求（recipe/template/intent/outputs/exports/map_layers/manifest_fingerprint）保存在 `graph_spec.metadata`。
- `save_plan_as_workflow` 工具改为默认从 session plan 自动提升（steps 列表仅作 legacy 回退），并补齐 `created_from_session`。

**A2 Executable Snapshot**
- `RunManifestBuilder` 扩展：per-step capability + algorithm、`runtime_manifest_fingerprint`（registry 世代）、`mapspec_fingerprint`、`product_facets`、`qa_summary`、`finalization_summary`（全部有界投影）。
- run_fingerprint 稳定投影**纳入** capability/algorithm（重解析出不同算法 = 不同的计算计划，指纹必须不同）；outcome 证据（facets/QA/render）**不纳入**（结果而非计划）。

**A3 Project Artifact Promotion**
- 新增 `app/services/project_artifact_promotion.py`：run 完成后（有 session + project 上下文时自动，best-effort）或经 `POST /projects/{id}/runs/{run_id}/promote-artifacts` 显式触发。
- 内容写入 content-addressed 存储（`PROJECT_ARTIFACT_CONTENT_DIR`，缺省 `DATA_DIR/project_artifacts`，键 = content_fingerprint 或 payload sha256）。DB Artifact 行原位更新（不铸造第二身份）：`content_location`、`content_payload_sha256`、`content_summary`（feature_count/bbox/schema 摘要）、producer 三元组。
- 诚实语义：会话过期 → `content_status: "session_expired"`（指针保留，绝不伪造摘要）；已提升 → 幂等 `already_promoted`。重开项目不再依赖原 SessionStore。

**A4 Lineage 语义列**
- `artifact_lineages` 增加 `producing_capability / producing_algorithm / mapspec_fingerprint`（迁移 0022）。Dataset → Capability → Algorithm → Tool → Artifact → MapSpec 链在**同一张**血缘表上可表达，不建第二图。

**A5 Incremental Re-run**
- `WorkflowEngine.rerun_from_step`：显式失效入口——from_step 及其拓扑后代重执行，其余已完成步骤经 `_reconstruct_prior` + 指纹校验后复用；`REST POST /projects/{id}/runs/{run_id}/rerun`、工具 `rerun_workflow(from_run_id, from_step)`。
- **重跑绝不 replay 旧 tool calls**：capability 步骤执行时经 `resolve_step_tool` 走 AlgorithmResolver 重新裁决（registry 视图不可用时诚实回退到记录的 tool id 并在 `resolution_evidence` 披露）。
- Style-only / MapSpec-only 变化不触发 workflow 重跑（既有 mutation 语义，本 ADR 以 Map Product 版本 diff 机器可读化，见 A6）。

**A6 Map Product Versioning**
- 新表 `map_products`（迁移 0022）+ `MapProductService`：每版本记录 product_fingerprint（输入指纹 + 计算计划 + MapSpec 指纹 + 产物指纹的 canonical hash）、input_dataset_fingerprints、compute_plan（有界快照）、output_fingerprints、五维 diff（data/algorithm/parameter/style/output_changed + `analysis_recomputation_expected`）。
- `style_changed && !analysis_recomputation_expected` 是「样式变更不重算」契约的机器读面。REST：`GET/POST /projects/{id}/map-products`。

### 3. GIS Agent Evaluation Harness（Phase B）

- 新增 `app/evaluation/`：`GISBenchmarkCase`（B1 契约：expected_task/capabilities/allowed/forbidden_algorithms/facets/max_tool_calls/script/numeric/component 断言/交互语义）、`GISBenchmarkRunner`（两档：plan 档 = 确定性 planner 解析断言；execute 档 = 真实 ToolRegistry 按脚本分派 fixture 数据）、12 个金场景（B2）、markdown 报告（B3 metrics 全集；未测量项如实 `n/a`）。
- CLI：`python manage.py gis-benchmark [--case] [--group] [--offline] [--report]`。
- B4 Deterministic-first：判定只来自 schema/数值 golden/工具 trace/MapSpec/facet 契约/修复计划探针（`classify_runtime_repairs` 纯函数）；无 LLM judge。工具未注册的场景**如实 skipped**，绝不假通过。
- 锁定测试：`tests/unit/gis_harness/test_benchmark_harness.py`（语义回归 + 确定性双跑一致）。

### 4. Semantic GIS Intelligence（Phase C）

- `app/lib/gis/semantic_profile.py`：`SemanticFieldRole`（12 角色）+ 证据分级 `rule_derived > metadata_derived > user_declared > unknown`。**仅字段名永远到不了 rule_derived**（名称+dtype 最多 metadata；rule 需 dtype+有界值样本印证）；population/area ⇒ `normalization_denominator` 派生角色；不确定保持 unknown。
- `app/lib/gis/analysis_patterns.py`：11 个 metadata-only 模式（distribution/density/administrative_comparison/accessibility/service_coverage/spatial_equity/site_selection/risk_exposure/temporal_change/mobility_flow/suitability），每个声明 required semantic roles、recommended/optional capabilities、required facets、归一化指引、经典 GIS 陷阱。
- `app/lib/gis/pattern_projection.py`：query + intent task + 语义画像 → 模式匹配 + **诚实披露**（C4 红线：缺人口分母时必须输出「只能评价数量/密度，公平性结论需要分母」，不得静默降级为计数比较）。
- 工具：`profile_dataset_semantics`、`suggest_analysis_patterns`（tier-2，advisory）。最终执行仍走 SessionPlan → CapabilityRegistry → AlgorithmResolver；pattern 不是第二 planner。

### 5. OD Flow Product（Phase D）

- **D1/D2**：artifact 类型 `od_table`（与纯成本表 `od_matrix` 的区别：携带坐标）注册；工具 `od_flow_edges`（`app/tools/flow_tools.py`）消费 OD 边表（od_table/rows/FC 三形态，字段名可覆盖）→ 带权线要素 FC（`id = origin->destination`、weight、weight_norm、distance_km）。
- **D3**：top-N（默认 500，硬上限 5000）+ min_weight 阈值 + bidirectional/origin/destination 聚合 + 权重归一化。聚合 O(N)，选择 heap O(N log k)，**禁止 pair 展开导致 O(N²)**（50k 边测试锁定 <20s）。权重域取全阈值总体——top-N 同权重退化分布不会破坏颜色/宽度通道。
- **D4**：`flow_od_arc` MapModel planned → **native**（MapLibre line；deck.gl 仅作对照登记，不引入第二渲染运行时）。converter 支持 `type_hint=flow_od_arc`：width ← weight（interpolate 1→8px）、color ← weight（continuous Plasma legend 投影）、opacity 0.85；几何非线时诚实回退并告警。SVG/PNG 导出与 live 同源（mapspec-to-svg 逐要素解析 paint 方法），披露「直线段非真实路径」。
- **D5**：flow 要素携带稳定 id（`{origin_id}->{destination_id}`），复用既有 SelectionContext（transient，不写 MapSpec）；前端锁定测试保证 id 与编译通道不被静默破坏。
- **D7**：intent 新增 `mobility_flow` 任务（通勤流/出行流/客流/流向词族）+ `od_flow_overview` recipe（od_matrix → od_flow_mapping → flow_od_arc 主表达 + statistics/chart facets）+ capability `od_flow_mapping` + algorithm `flow.od_arc_build`（tool_candidates=[od_flow_edges]）。解析链 resolver 实证可达。

### 6. 附带修正（benchmark 暴露的真实缺陷，非为过测而改测试）

- **simple_view 规则过窄**：「显示」只匹配句首 → 「在地图上显示咖啡店」被过度分类为 distribution_overview 并规划全套 KDE/热点。修正为显式前缀分支（在地图上/帮我/把/将 + 展示动词）+ 分析关键词负向前瞻；**不放任意字间隙**（否则「用气泡图展示…」的形态信号被吞，proportional_symbol 路由破坏——回归测试锁定）。
- **显式图表请求缺席 facet 契约**：查询点名「柱状图」时 output_intents 不含 chart、facet 契约不要求 chart。intent 增加 `_CHART_WORD_RE` → chart 意图；`derive_facet_contract` 增加意图信号源。
- **poi_distribution_overview 的 export_profile 补 `chart: True`**（goal G1 的产品构成）。
- **显式 NDVI 请求从未规划 ndvi capability**：新增 `vegetation_index` 任务规则 + raster_distribution recipe 的 task_optional_analysis。
- 旧测试按新语义更新并注明依据（test_intent/test_model_injection/test_model_library）。

## State Ownership（摘要表）

| 状态 | 载体 | 本 ADR 变更 |
|---|---|---|
| 会话计划 | SessionPlan（SessionStore） | 不变；promotion 是派生 |
| 地图期望态 | MapSpec（CAS revision） | 不变；flow 图层经既有 layer_upsert |
| 会话产物 | ArtifactRegistry + ref | 不变；promotion 是原行增强 |
| 项目真相 | projects/workflows/runs/artifacts/lineages | +语义列、+map_products、+content 存储 |
| 执行裁决 | Capability→Algorithm→Resolver→ToolRegistry | flow/OD 注册项扩充；rerun 重解析 |
| 评估 | app/evaluation（只读投影 + 真实工具） | 新增，无生产状态 |

## Project vs Session lifecycle

Session Artifact（ref，TTL 4h）→ promote → Project Artifact（content-addressed 持久内容 + 全语义元数据）。Promotion 不铸造第二身份：引擎在执行时已落 DB Artifact 行，promotion 只补内容与语义。session_expired 状态如实披露。

## Workflow reproducibility

同一 (graph revision, inputs, tool versions, registry 世代) ⇒ 同 run_fingerprint（capability/algorithm 折入）。工具改名/下线后：capability 步骤重解析给出新答案或诚实披露（`used_recorded_tool`）。

## Failure Semantics

- promotion blockers：未完成计划拒绝提升（blockers 列表返回）。
- rerun_from_step：上游不可重构 → 拒绝并建议全量重跑；不会静默重算全部。
- flow 工具：空输入/坏坐标/未知聚合模式 → 结构化错误 + correction_hint；50k 上限前置拒绝。
- benchmark：工具未注册 → skipped（披露），绝不计为 pass。

## Compatibility

- 迁移 0022 全部为可空列/独立表；旧 lineage 行 NULL 语义 = pre-promotion 边。
- `WorkflowStepSpec` 新字段全部可选；旧 graph_spec/manifest 逐位兼容（manifest outcome 块缺省不出现）。
- run_fingerprint 投影变更（+capability/algorithm）意味着与旧 run 的指纹不可直接比 —— compare_runs 的 diff_keys 会如实显示 steps 变化。
- 前端零必需改动（compiler 已支持 interpolate StyleMethod；line/channel 编译由 flow-layer.test.ts 锁定）。

## Rejected Alternatives

1. **LLM 手抄 steps 保留为唯一保存路径** → 丢失语义、漂移，否决（只留 legacy 回退）。
2. **新建 ProjectMapState / FlowMapState** → 违反 MapSpec 唯一真相，否决。
3. **为 flow 引入 deck.gl 第二渲染运行时** → MapLibre line + 数据驱动 paint 已满足（编译器与 SVG parity 均实证），否决。
4. **Pattern Library 直接驱动工具选择** → 会成为第二 planner；定位为 advisory 投影，否决。
5. **Artifact promotion 铸造新 artifact id 体系** → 破坏 lineage 连续性；原行增强，否决。
6. **Kriging 本轮落地** → 前四阶段已饱和，仓促塞入风险大于价值，列入 Deferred。

## Deferred

- **Phase E（Kriging + Uncertainty）**：`interpolation.kriging` planned → native（Ordinary Kriging 优先，spherical/exponential/gaussian 变差函数 + 有界自动拟合 fallback；RMSE/MAE/bias 交叉验证；prediction + uncertainty 双 artifact；IDW/Kriging resolver 裁决）。下一轮独立 ADR。
- `extrusion_3d`、`isoline_contour` 仍 planned。
- Map Product 前端 UI diff 编辑器（后端账本与 REST 已就绪）。
- 行政区 choropleth 的 legend 必需性未由 composition 模板统一声明（G7 已覆盖 chart；legend 槽位在 statistical_map 家族已 required）。
- token usage / tool latency metrics（B3 可选集）离线不可测，报告如实 n/a。

## 性能红线对照（§11）

- vector 150k：fetch-on-demand descriptor，G4 断言 LLM 载荷 ≤20KB 且零内联要素。
- OD 50k：G12 + 单测锁定 O(N log k)（<20s sanity bound）与 ≤1000 有界输出。
- 无新全量栅格读回归（本轮未触碰栅格执行路径）。
- 前端：flow 输出有界（≤5000），DOM 与 viewport 相关性由既有 layer 机制承担。
