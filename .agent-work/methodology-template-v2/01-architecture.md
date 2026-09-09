# 01 — Architecture（Phase B 架构冻结，R1 修订版）

Epic 11 — GIS Methodology & Template Intelligence V2。
基线 `origin/master @ 8a33e3a5`；审计见 `00-baseline.md`。
**R1**：已并入 Subagent-A 架构挑战全部 must-fix（见 §8 修订记录）。

## 0. 总原则（不可协商红线）

1. **单一事实源**：canonical registries（capability/algorithm/artifact/ontology/
   methodology/recipe/map model/component/composition/product/style/reference）
   保持唯一权威。本 Epic 新增的一切知识表都是 **registered curated artifacts**：
   引用 canonical id、经 `validate_gis_library()` 对账、悬空 fatal。
2. **Graph 不是第二 registry**：knowledge graph 是从 canonical registries +
   taxonomy 知识表**确定性投影**出的只读有向图（同 `workflow_families` 投影
   先例）。不存储 payload 复制；节点/边只持 canonical id + 关系类型。
3. **零 LLM / 零 I/O / 有界**：全部分类、资格、排序、组合、解释为确定性纯函数；
   产物可序列化、bounded dict 投影；同输入同输出。
4. **不重写**：不重写 Workflow Runtime / Cartography renderer / Harness memory /
   V4 methodology 资格引擎。V2 层是**增强适配**（richer 事实 + 统一报告 +
   abstention + 基准），复用 `data_qualification` 五态、
   `scientific_preconditions`、`DATA_FIT_SCORE`、`qualify_method_candidates`。
5. **Additive 演进**：ontology tasks / methodology families 词表纯加法；
   descriptor 新字段全部带缺省（存量零迁移）；不改 15 阶段 base 契约。

## 1. 分层架构（目标态）

```
Canonical Registries（不动，唯一权威）
        ↓ references（校验对账）
[新增] GIS Task Taxonomy V2 (taxonomy.py)
        categories(20) → ontology tasks → methodology families 对齐视图
        + 每类: intent semantics / invalid methods / alt methods / viz / components
        ↓ projection
[新增] Methodology Knowledge Graph (graph.py)
        typed edges; integrity; fingerprint; diff; bounded build/cache
        ↓
[新增] Dataset Qualification Engine V2 (qualification.py)
        统一 MethodQualificationReport（viable/rejected/degraded/missing/
        preprocessing/confidence）← 复用五态资格 + preconditions + DatasetProfile
        ↓
[新增] Method Candidate Ranker V2 (ranking.py)
        hybrid: taxonomy + graph + qualification + lexical + prior + cost
        + constraints + abstention（Recall@k/MRR/invalid-rate/abstention 基准）
        ↓
[新增] Template Intelligence V2 (app/lib/cartography/template_intelligence.py)
        TemplateSpecV2（slots+bindings+layout+style+capability+export）
        + ComponentRoleVocabulary + CompositionPlanner（规则驱动，非硬编码）
        ↓
[新增] Viz Bridge (viz_bridge.py)
        artifact type → viz family → legend semantics → template slot binding
        ↓
Workflow Skeleton + Render Intent（消费 V4 typed DAG + MapModel；
        render intent = 结构化制图意图声明，不渲染）
        ↓
Harness / Workflow Compiler / Cartography（经 knowledge_tools 门面消费）
```

## 2. 模块与放置

| 模块 | 路径 | 职责 |
|---|---|---|
| Taxonomy V2 | `app/lib/gis/methodology/taxonomy.py` | `GIS_TASK_CATEGORIES`（20 类稳定词表）+ `TaskCategoryDescriptor`（对齐 ontology tasks / families；invalid/alternative methods；viz/component 期望）+ category→task 匹配。**不新建关键词表**：lexical 证据由 category→tasks/families 投影其既有 keywords 派生（R1-F6） |
| Knowledge Graph | `app/lib/gis/methodology/graph.py` | `MethodologyGraph`（typed edge model）+ `build_graph(registries...)`（registry 投影；**全部 registry/词表以参数注入**，lib 不得顶层 import services——R1-F8）+ integrity validate + fingerprint + structural diff + bounded 单例缓存（fingerprint-keyed） |
| Descriptor V2 | `app/lib/gis/methodology/descriptors.py` | `MethodologyDescriptorV2`（problem class / assumptions / parameters / alternatives / computational class / uncertainty support / **assumes_continuous_measure**（R1-F4）/ invalid-degraded cases / viz guidance / provenance ref）——method_id 键的增强层，校验对齐 MethodologyRegistry |
| Qualification V2 | `app/lib/gis/methodology/qualification.py` | `MethodQualificationEngine`：DatasetProfile/ResolverProfile 事实 → 逐方法 unified report（geometry/sample/CRS-scale/measure_semantics/temporal/nodata/roles/precondition 维度；viable/rejected/degraded/missing/preprocessing/confidence）。科学性**全部委托** `scientific_preconditions` + `AlgorithmDescriptor.crs_class`（R1-F1），五态语义与 `data_qualification` 一致 |
| Ranker V2 | `app/lib/gis/methodology/ranking.py` | `rank_methods()`：确定性加权混合（taxonomy match / graph compatibility / qualification / lexical hit / prior / computational cost / user constraints）+ `abstain` 判定（证据不足/全拒绝/歧义不可分）+ 排序解释 |
| Provenance | `app/lib/gis/methodology/provenance.py` | `KnowledgeProvenance`（source_type: curated/code_derived/test_derived/doc_derived/reference_derived；confidence；validated_fingerprint）+ 语料级 provenance 登记 |
| Viz Bridge | `app/lib/gis/methodology/viz_bridge.py` | artifact semantic type → `VisualizationFamily` → legend semantics → template slot binding；覆盖 KDE/hotspot/interpolation+uncertainty/accessibility/classified/change/network/terrain/stats（校验 artifact/map model/component id 全部 canonical） |
| Feedback | `app/lib/gis/methodology/feedback.py` | `MethodologyFeedbackRecord` schema（selected/rejected/failures/render diagnostics/user corrections/fallback）→ 只进离线 improvement corpus；无运行时改写通道 |
| Case Corpus | `app/lib/gis/methodology/case_corpus.py` | `GIS_CASE_CORPUS`（≥21 案例：Epic §9 全覆盖，fixture 化 profile，含 intent/correct families/invalid methods/expected artifacts/viz/components） |
| Service 门面 | `app/lib/gis/methodology/service.py` | `KnowledgeService`：classify/retrieve/qualify/explain/template/compose/components/explain_plan/alternatives——**不暴露 graph 内部结构给 LLM**（输出 bounded 投影） |
| Template Intel | `app/lib/cartography/template_intelligence.py` | `TemplateSpecV2` + `DataBinding`/`CapabilityRequirement`/`ExportConstraint` + `plan_composition()`（task/artifacts/data/output/uncertainty/comparison → composition plan） |
| Harness 工具 | `app/services/gis_harness/knowledge_tools.py` | agent tool 面：以 KnowledgeService 为界的 6 个只读工具（`@tool` 装饰器 + **`app/tools/__init__.py::_TOOL_MODULES` 一行注册**（R1-F10）；tier=1 read-only） |
| Central validation | `app/services/gis_harness/registry_validation.py`（追加） | 新知识表/graph 对账（函数体内 deferred import，同既有模式），issues 前缀 `methodology_intel:` |

## 3. 词表与 schema 冻结

### 3.1 Taxonomy（20 类，稳定词表，纯加法演进）
`spatial_distribution, density, administrative_aggregation, proximity,
accessibility_network, hotspot, clustering, interpolation, suitability,
overlay, terrain, hydrology, change_detection, spatiotemporal_pattern,
remote_sensing_extraction, uncertainty, comparison, multi_criteria,
thematic_cartography, atlas_reporting`

`TaskCategoryDescriptor` 字段：category_id / label_zh / label_en /
intent_semantics / ontology_task_ids(⊆ ONTOLOGY_TASKS，校验) /
methodology_family_ids(⊆ METHODOLOGY_FAMILIES，校验) / required_data_roles
(⊆ DATA_ROLES) / geometry_requirements / crs_scale_requirements
(**文档性字段，不参与裁决**——CRS 裁决唯一事实源是
`AlgorithmDescriptor.crs_class`，R1-F1) / min_data_quality /
invalid_method_ids(⊆ method 词表，校验) / alternative_method_ids(⊆ method
词表，校验——不指向未注册方法) / output_artifact_types(校验) /
recommended_visualizations(map model id，校验) / required_components /
optional_components / provenance_id。
**无独立 keywords 字段**（R1-F6）：category 的 lexical 证据 =
投影(ontology_task_ids → keywords ∪ methodology_family_ids → keywords)，
V4 路由裁决优先，taxonomy lexical 仅作 ranker tie-break 证据。

### 3.2 本体纯加法补齐（Epic 20 类对齐缺口，F-P1-1；R1-F3 修订为 5 条）
新增 ONTOLOGY_TASKS 5 条（native、带 fallback_strategy/keywords/roles）：
`decision.proximity_buffer`（proximity）、`spatial_statistics.point_clustering`
（显式点聚类；与 LISA 正交——capability 层已声明独立性）、
`spatial_statistics.spatiotemporal_pattern`（时空格局；消费
space_time_interaction/spatiotemporal_clustering 能力）、
`cartographic.atlas_reporting`（图集/报告；**cartographic_expectations
留空**——无 atlas MapModel，R1-F11；组件期望仅引用既有组件类型）、
`overlay.geometry_composite`（叠加合成；消费 geometry_overlay）。
`uncertainty` 类别映射到既有 `interpolation.uncertainty_surface`
（**不新增重复任务**，R1-F3）。
同步补 methodology：13 族新增 `proximity`（**关键词必须长且特异**：
缓冲/缓冲区/周边/半径/覆盖范围；禁用「距离」等短泛词——追加族在
tie-break 中按 index 恒败，R1-F9），候选方法新增：
`proximity.multi_ring_buffer` / `proximity.euclidean_buffer` /
`proximity.service_distance`、`stats.point_cluster_dbscan`（clustering）、
`stats.space_time_pattern`（spatiotemporal）、
`interp.indicator_kriging`（categorical 数据的合法插值——R1-F4，
capability `indicator_kriging` + algorithm `interpolation.indicator_kriging`
均已存在）、`overlay.composite_overlay`。
METHODOLOGY_FAMILIES 12→13（纯加法；coverage/corpus 断言同步演进）。

### 3.3 Graph 边类型（冻结）
`requires_data_role, supports_method, alternative_method, incompatible_with,
degraded_when, requires_crs_class, requires_scale, requires_quality,
produces_artifact, recommended_visualization, requires_component,
serves_category, consumes_artifact`
节点类型：`task / method / capability / algorithm / artifact_type /
map_model / component / category`。全部 id 引用 canonical registries 或
taxonomy 词表；`build_graph()` 后 `validate()` 必须零悬空（否则 build
失败——fail-closed）。fingerprint = sha256(canonical edge JSON + registry
fingerprints 拼接)；diff = (added_edges, removed_edges, changed_rels)。

### 3.4 Qualification 维度与裁决（冻结；R1-F1/F4 修订）
维度：`geometry / sample_size / crs_scale / measure_semantics(categorical vs
continuous) / temporal / nodata_quality / data_roles / scientific_precondition`。
每维四态 `pass/unknown/transform/fail`（与 methodology V4 precondition 四态
同构；unknown ≠ 不满足）。
**CRS/尺度裁决唯一事实源 = `AlgorithmDescriptor.crs_class`**（R1-F1）：
- `GEOGRAPHIC_OK`（如 geometry.buffer——实现内建 UTM 投影）在地理 CRS 下
  → `pass`（可附「内部重投影」披露，不误报 transform）；
- `PROJECTED_REQUIRED`/`LOCAL_METRIC_REQUIRED`（如 terrain.slope）在地理
  CRS 下 → `transform`（soft，reproject 修复链）——与既有
  `local_metric_crs_required` precondition 的 REQUIRES_TRANSFORM→soft
  语义一致（test_methodology_v4: test_crs_transform_is_soft_not_rejecting）；
- `CRS_AGNOSTIC` → `pass`（无裁决事实则 unknown）。
**measure_semantics 裁决按方法声明 scope**（R1-F4）：仅当
`measure_semantics=categorical AND descriptor.assumes_continuous_measure`
才 reject（连续假设方法吃类别字段）；`interp.indicator_kriging` 声明
`assumes_continuous_measure=False`，对 categorical 合法（indicator 变换）。
输出 `MethodQualificationReport`：
`method_id / status(viable|degraded|rejected) / dimension_states /
reason_codes(稳定前缀 QUAL_<DIM>_<VERDICT>) / missing_requirements /
recommended_preprocessing(⊆ REMEDIATION_OPS) / confidence(有事实维度占比)`。
硬错误对（Epic §5.D 禁止项）由 corpus 锁定（gold 全部锚定既有 oracle——
V4 `qualify_method_candidates` 拒绝集 + `scientific_preconditions`
verdict + ontology fallback tiers，R1-F13b）：
无点数据选 KDE → rejected（`point_support_required` oracle）；
categorical 字段 × assumes_continuous_measure 方法 → rejected；
`LOCAL_METRIC_REQUIRED` 算法在地理 CRS → transform（非静默、非拒绝）；
tiny sample hotspot 显著性 → rejected（min_numeric_samples oracle）；
raw-count 归一化场景 → degraded+披露（分母缺失时 rate 不可比，
ontology fallback oracle）。

### 3.5 Ranker 权重与 abstention（冻结；R1-F6 消融词冲突）
`score = 0.30*taxonomy + 0.25*qualification + 0.15*graph_compat +
0.10*lexical + 0.10*prior + 0.05*cost_fit + 0.05*constraint_fit`；
lexical 分量 = 投影关键词（ontology/family 既有词表）命中归一，
**不新建第四张关键词表**；V4 `resolve_methodology_family_for_query`
的路由裁决优先于 ranker lexical 分量（一致性断言进 `methodology_intel:`
校验：ranker 不推翻 V4 族路由，只能在其允许集内排序）。
全部分量 [0,1] 确定性；平局 `(‑score, priority, method_id)`。
Abstention 触发（返回空选择 + 理由）：(a) 全部候选 rejected；
(b) top1‑top2 分差 < ε(0.02) 且类别歧义；(c) 分类置信度不足（0 任务/族
命中且零词汇证据）；(d) 乱码/无关 query 必须 abstain（corpus 锁定）。
基准（R1-F13 反循环措施）：语料在 ranker 调权**之前**冻结入库（wave 14-15
先于 13/16 的调参 commit）；invalid-method gold 锚定既有 oracle
（V4 拒绝集/preconditions），不新造 V2 表 gold；报告必须披露
「lexical-only baseline 失败、结构化信号救回」的案例占比（防 vacuous
MRR）；Recall@k(k=1,3,5)、MRR、invalid_selection_rate、
abstention_precision、abstention_recall 按实测钉保守下限。

### 3.6 TemplateSpecV2（冻结）
`TemplateSpecV2 = base(MapCompositionTemplate id 引用) + component_slots
(引用) + data_bindings[slot_id→(artifact_type|data_role, bind_scope)] +
layout_rules(profile+device+size) + style_rules(style_template ids) +
capability_requirements(⊆ capability 词表) + export_constraints(targets/
dpi/page/parity) + uncertainty_requirements + comparison_requirements`。
校验：slot allowed types / map model / component / artifact / capability
全部 canonical 对账。Composition planner 输出 `CompositionPlan`：
spec id + slot fills + bindings + 披露（不确定性/降级）+ 置信度；
规则源 = taxonomy component 期望 + viz bridge + template compatibility；
**禁止**「学校→热力图」式 query 硬编码（corpus 负例锁定）。

### 3.7 Viz Bridge 词表（冻结；R1-F5 冲突语义）
`VisualizationFamily` = {point_distribution, choropleth_normalized,
choropleth_raw, density_surface, hotspot_significance, cluster_map,
interpolation_surface, uncertainty_overlay, accessibility_isochrone,
service_area, classified_raster, change_map, flow_map, terrain_derivative,
statistics_chart, comparison_frame}。
`bridge(entry)`: artifact_type(校验) → family → legend_semantics
(graduated|categorical|continuous|significance|uncertainty|none) →
slot_bindings(legend/colorbar/chart/uncertainty_panel/methodology_note)。
数据来源：artifact `typical_map_models` 收编 + capability
`compatible_map_models` + 组件 `compatible_artifact_types` 交叉派生。
**冲突语义（R1-F5）**：悬空 id → fatal `methodology_intel:` issue；
跨源**模型分歧** → 结构化 `DISCLOSURE` 条目（进解释面，不 fatal、
不静默合并）。已知两处既有分歧（`density.analytical.mixed` 的
density_surface vs choropleth 族；`interpolation.dasymetric` 的
polygon_feature_set vs dasymetric_map）在 bridge 审定 reconcile 表中
预解析（声明权威来源 + 披露），不改 canonical registries。

### 3.8 Provenance & Feedback（冻结）
Provenance：每张新知识表条目可引用 `KnowledgeProvenance` 记录
（`PROVENANCE_LEDGER`，id→record；source_type 词表
`curated|code_derived|test_derived|doc_derived|reference_derived`；
confidence ∈[0,1]；`validated_registry_fingerprint` 惰性核对——表指纹
变更 → provenance stale 标记，不阻塞运行时）。LLM 生成内容不得注册为
`curated`；`llm_suggested` 类型**不存在**于词表（Non-goal 的机器可读表达）。
Feedback：`MethodologyFeedbackRecord`（session/project 隔离字段、
method_id、decision、outcome、diagnostics、reasons）；
`FeedbackCorpusWriter` 只追加 JSONL 到离线路径（可配置，默认禁用；
**单文件硬上限 10000 条，达到后停止写入并产出 truncation 披露事件**
——R1-F12）；运行时**无任何**读取-改写 authoritative 表的路径（测试锁定）。

### 3.9 缓存（冻结）
Graph 单例缓存：key = (各 registry fingerprint 拼接)；
TTL = 进程生命周期 + fingerprint 失效（registry reset → key 变化 → rebuild）；
容量 1（图本身有界：节点 ≤ O(registries)，边 ≤ O(nodes × 关系)）；
negative cache 不适用（build fail 即抛，不缓存失败态）；
service 层无二级缓存（直查 registry O(1) 索引）。

### 3.10 性能预算（冻结）
graph build ≤ 150ms（首建，进程内一次）；lookup/category match ≤ 5ms；
qualification（≤50 方法 × 单 profile）≤ 20ms；ranking ≤ 10ms；
内存：graph ≤ 5MB；corpus 评估全量 ≤ 30s（pytest 60s timeout 内）。
预算测试用真实计时断言上界（同 `test_workflow_v4_budget.py` 先例）。

## 4. 兼容性与迁移

- 全部新模块为**新增文件**；对既有文件的改动仅限：
  `gis_ontology.py`（追加 5 tasks + export）、
  `workflow_v4/methodology.py`（追加 1 family + 候选方法 + 词表 12→13）、
  `app/evaluation/methodology_corpus.py`（追加 proximity 族案例，
  **wave 1 内完成**——否则 family coverage 集合断言当日红，R1-F2）、
  `registry_validation.py`（追加对账段）、
  `component_registry.py`（19 seeds 增量字段 semantic_role/examples，
  带缺省）、
  `app/tools/__init__.py`（_TOOL_MODULES 一行注册，R1-F10）、
  `docs/adr/`（新 ADR）、`CHANGELOG.md`（最小追加）。
- 不改：15 阶段 base 契约、COMPILER_STAGES 测试锁定、既有 44 方法的
  method_id/语义、既有 corpus 案例断言（只允许新增）。
- **允许的既有断言改动点（仅两处，R1-F7）**：
  `tests/unit/gis_harness/test_methodology_corpus_v4.py` 的 family
  coverage 集合断言（随词表 12→13 自然传播）与
  `tests/unit/gis_harness/test_methodology_v4.py:30` 的
  `family_count() == 12` 硬编码（改 13）。其余既有断言
  （COMPILER_STAGES==15、`len(ONTOLOGY_TASKS)` 无断言、corpus ≥36 下限）
  全部不动。

## 5. 测试与 oracle

- `tests/unit/gis/methodology/`（新目录）：
  test_taxonomy / test_graph / test_descriptors / test_qualification /
  test_ranking / test_viz_bridge / test_case_corpus / test_service /
  test_provenance / test_feedback / test_budgets
- `tests/unit/gis_harness/test_knowledge_tools.py`（工具面契约）
- `tests/cartography/test_template_intelligence.py`（TemplateSpecV2+planner）
- 中央校验：`methodology_intel:` issues == [] 断言并入既有
  central validation 模式
- 语料红线：case corpus 全绿（每 case：正确族命中、invalid 方法全被拒、
  artifact/viz/component 期望可满足）；hard-negative 双语方法语料
  Recall@5≥0.9、MRR≥0.85、invalid rate==0（curated 语料上的保守钉值，
  实测后定）；abstention：歧义/无证据案例必须 abstain。
- anti-硬编码：corpus 中同 category 不同 subject/规模/CRS 的平行案例
  断言同一裁决（排除 per-query if/else）。

## 6. 决策记录（自动选择理由）

- **D1 新包 `app/lib/gis/methodology/`**：knowledge 层是 lib 域（零 IO、
  纯函数、被 services 消费）——同 `app/lib/gis/` 既有算法/能力层定位；
  不放 services 因为其无 runtime/状态。
- **D2 沿用 V4 资格引擎而非新建**：F-P0-1；V2 qualification 聚焦 V4 未覆盖
  维度（CRS/scale、categorical/continuous、nodata、统一报告、abstention），
  复用其四态/排序哲学，避免双路径漂移。
- **D3 graph 做投影不做存储**：F-P0-3；与 workflow_families 同构；
  curated 关系（invalid/alternative/recommended-viz）放 taxonomy/descriptor
  审定表，graph 只做合并投影 → 单一事实源不分裂。
- **D4 方法级 retrieval 不引入 embedding**：现有 embedding infra
  （rag/faiss_store）是文档 RAG，非确定性且重依赖；Epic Non-goals 禁
  无界 vector storage；lexical（词表整词/子串，同 registry 红线）+
  结构化信号足以支撑 curated 语料目标；语义升级留给 harness-v6。
- **D5 新 ADR 编号 0120**：扫描 `docs/adr/` 最大为 0119（PR 列表可见
  ADR-0119 已被并行 epic 占用）→ 本 epic 用 0120。
- **D6 proximity 独立成族（13 族）**：buffer/多环缓冲/邻近既是高频独立
  任务类，能力词表齐备（geometry_buffer/multi_ring_buffer/proximity_buffer），
  与 suitability（多因子）/network（路网）语义不同；纯加法不破坏 12 族。

## 8. R1 修订记录（Subagent-A 架构挑战 must-fix 全采纳）

- R1-F1（CRITICAL）：CRS 裁决唯一事实源改为 `AlgorithmDescriptor.crs_class`；
  buffer@4326 = pass（实现内建投影），§3.4 重写；taxonomy
  crs_scale_requirements 降为文档性。
- R1-F2（CRITICAL）：wave 1 交付物补 `app/evaluation/methodology_corpus.py`
  proximity 案例（family coverage 集合断言防当日红）。
- R1-F3（MAJOR）：删除 `interpolation.uncertainty_mapping`（重复既有
  `interpolation.uncertainty_surface`）；新任务 6→5。
- R1-F4（MAJOR）：categorical×连续插值拒绝按 `assumes_continuous_measure`
  逐方法 scope；wave 1 注册 `interp.indicator_kriging` 候选方法
  （capability/algorithm 均已存在）；alternative 只引用已注册方法。
- R1-F5（MAJOR）：viz bridge 冲突语义冻结：悬空=fatal、模型分歧=DISCLOSURE；
  两处已知分歧预解析进 reconcile 表。
- R1-F6（MAJOR）：taxonomy 不新建关键词表；lexical=投影既有词表；
  V4 路由优先 + methodology_intel 一致性断言。
- R1-F7（MAJOR）：§4 断言改动点改为两处（corpus coverage 集合断言 +
  test_methodology_v4:30 family_count==12→13）。
- R1-F8（MAJOR）：lib→services 依赖方向冻结：registry/词表参数注入；
  services 侧 deferred import（registry_validation 函数体内）。
- R1-F9（MINOR）：proximity 关键词长且特异（缓冲/缓冲区/周边/半径/覆盖
  范围），禁短泛词。
- R1-F10（MINOR）：工具注册点更正为 `@tool` 装饰器 +
  `app/tools/__init__.py::_TOOL_MODULES`；tier=1。
- R1-F11（MINOR）：atlas task cartographic_expectations 留空（无 atlas
  MapModel seed；不扩 scope）。
- R1-F12（MINOR）：feedback JSONL 单文件硬上限 10000 条 + truncation 披露。
- R1-F13（INFO）：基准反循环四措施入库（语料先冻结 / gold 锚既有 oracle /
  lexical-baseline 失败占比披露 / 平行案例不变性 + 乱码 abstain）。
- Wave 32 补：重新生成 BENCHMARK_MANIFEST（registry 投影变化）。

## 7. Agent 工具面（6 只读工具）

`gis_task_classify` / `gis_method_qualify` / `gis_method_explain` /
`gis_template_plan` / `gis_component_query` / `gis_knowledge_stats`
（名称以 tool surface 注册约定为准）。输入输出 typed schema；
graph/descriptor 内部结构不出现于 LLM 可见面（只出 bounded 投影与解释）。
