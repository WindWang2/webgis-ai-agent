# ADR-0120: GIS Methodology & Template Intelligence V2 — Method Knowledge Graph + Cartographic Composition + Agent Planning Intelligence

- 状态: Proposed
- 日期: 2026-09-10
- 关联: ADR-0118（Semantic Workflow Compiler V4 —— methodology family V4 即本 ADR 的基座）、ADR-0092（Analysis Patterns）、ADR-0101（Recipe Conformance / Cartographic Template Library V3）、ADR-0103（Cartographic Design System V4）
- Epic: `feat/gis-methodology-template-intelligence-v2`（Epic 11）

## 1. 上下文

Workflow Compiler V4（ADR-0118）落地了 12 个方法论族与 44 个候选方法的
确定性资格裁决，但方法知识仍然**散落**在 6+ 处 canonical 词表里
（ontology 任务、methodology 候选、capability/algorithm 的 map-model 兼容
声明、artifact 的 typical_map_models、组件目录、recipe packs），没有单一
的类型化视图；方法级检索没有 Recall@k / MRR / invalid-rate / abstention
基准；模板组合只有静态槽位声明，没有「数据绑定 / 能力前置 / 导出约束 /
不确定性义务」的一等契约；算法产物与制图表达之间没有 typed bridge。

## 2. 决策

### D1 — 知识层为 lib 级只读投影包 `app/lib/gis/methodology/`

taxonomy（20 类任务分类学）、graph（方法知识图）、descriptors（方法级
增强）、qualification（统一资格报告）、ranking（混合排序 + abstention）、
provenance（出处登记）、viz_bridge（产物→表达桥）、case_corpus（端到端
案例）、feedback（离线反馈契约）、service（统一门面）。零 LLM、零 I/O、
有界；lib 不顶层 import services（registry 参数注入 + 函数体内延迟对接
先例）。

### D2 — Graph 不是第二 registry

知识图是从 canonical registries + taxonomy/descriptor 审定表**确定性投影**
的只读有向图（421 节点 / 883 边），边只持 `kind:id` 引用；悬空引用 =
`GraphBuildError`（fail-closed，中央校验转 issue）；fingerprint 键控的
单例缓存（容量 1）；structural diff 只对边集。同构先例：
`workflow_families` 投影（V3）。

### D3 — V4 资格引擎保持权威；V2 是增强适配

qualification 引擎复用 V4 四态语义（pass/unknown/transform/fail；
unknown ≠ 不满足）与 `DATA_FIT_SCORE`；科学性检查**全部委托**既有
oracle：`scientific_preconditions`、`AlgorithmDescriptor.crs_class` ×
`crs_safety.crs_class_allows`（R1-F1：buffer@EPSG:4326 = pass——实现
内建 UTM 投影；PROJECTED_REQUIRED@4326 = transform 修复链显式化）、
V4 `min_sample_size`。categorical 量测 × `assumes_continuous_measure`
方法 → 拒绝（R1-F4：按方法声明 scope，指示克里金对类别数据合法）。

### D4 — Taxonomy 是分类学视图，不是第二任务表

20 个类目 = Epic 定义的稳定词表，映射到 ontology tasks / methodology
families；数据需求（角色/几何/产物）在载入期从 ontology 成员任务
**投影派生**（不手写）；**不新建关键词表**（R1-F6）——lexical 证据 =
成员任务/族关键词的投影，且最长优先非重叠匹配（防「对比图/对比」子串
双重计分）；V4 族路由裁决优先，ranker 以 `_ROUTING_BONUS` 表达一致性。

### D5 — 本体纯加法补齐

ONTOLOGY_TASKS 46→51（proximity_buffer / point_clustering /
spatiotemporal_pattern / overlay_composite / atlas_reporting）；
METHODOLOGY_FAMILIES 12→13（proximity；长特异关键词，R1-F9）；候选方法
44→51（indicator_kriging、point_cluster_dbscan、space_time_pattern、
overlay_composite、proximity 族三项）。词表纯加法演进，既有 44 方法
id/语义不变；family coverage 集合断言与 `family_count()==13` 是仅有的
两处允许演进的既有断言（R1-F2/F7）。

### D6 — Ranker：确定性混合 + 诚实弃权

冻结权重 taxonomy .30 / qualification .25 / graph .15 / lexical .10 /
prior .10 / cost .05 / constraint .05；rejected 恒排末段；全部不可行时
ontology minimal-tier 描述性兜底（显式披露，不虚构结论）；弃权三理由码
（NO_EVIDENCE / ALL_REJECTED / AMBIGUOUS_TIE）。反自证循环（R1-F13）：
方法级双语语料（25 案例）先于 ranker 调参冻结入库；invalid gold 锚定
既有 oracle；报告披露 lexical-only 失败占比。实测（25 案例）：
recall@1=0.96、recall@5=1.0、MRR=0.98、invalid=0.04、ambiguous
valid-top1=1.0；测试钉值取保守下限。

### D7 — Viz Bridge：收编文档性字段为有校验消费的桥

artifact type → 16 表达族 → 6 图例语义 → 槽位绑定；数据源 =
artifact `typical_map_models` × capability `compatible_map_models` ×
组件 `compatible_artifact_types` 交叉投影。分歧语义（R1-F5）：悬空 =
fatal `methodology_intel:`；跨源模型分歧 = 结构化 DISCLOSURE（两处已知
分歧在审定 reconcile 表预解析，不改 canonical registries）。

### D8 — Template Intelligence V2

TemplateSpecV2 = base 组合模板引用（不复制槽位）+ DataBinding +
CapabilityRequirement + ExportConstraint + uncertainty/comparison 义务；
12 个类目亲和规格。Composition Planner 规则驱动：输出目标过滤 → 亲和
评分 → 槽位填充（基底 ∪ taxonomy 组件期望 ∪ viz bridge 绑定 ∪ 义务
组件）→ 绑定防空。「学校分布 → 热力图」式 query 硬编码被 case corpus
负例与平行不变性测试锁定禁止。与 Cartography V6 边界：本层只裁决
「该用什么组件、怎么组合」，renderer 负责「如何正确绘制」。

### D9 — Component Registry V2（增量字段）

`semantic_role`（11 角色词表）/ `examples` 以缺省字段加入
MapComponentDescriptor；19 个 seed 的角色在载入期从
`COMPONENT_SEMANTIC_ROLES` 单一事实源投影回填（扩展组件缺省空）。

### D10 — Provenance 与 Feedback

Provenance 词表（curated / code_derived / test_derived / doc_derived /
reference_derived）**故意不含 LLM 生成类**——LLM 生成内容不得成为
authoritative gold 的机器可读表达。Feedback：默认禁用的 JSONL writer、
10000 条硬上限 + truncation 披露、project/session 隔离字段；**无任何
运行时回灌 authoritative 知识表的路径**（测试锁定）。

### D11 — Agent 工具面

6 个 tier-1 只读工具（gis_task_classify / gis_method_qualify /
gis_method_rank / gis_method_explain / gis_template_plan /
gis_component_query），全部以 KnowledgeService 为界、bounded 投影；
图结构不出现于 LLM 可见面。注册 = `@tool` 装饰器 +
`app/tools/__init__.py::_TOOL_MODULES` 一行（R1-F10）。

## 3. 后果

- 方法知识首次具备单一类型化视图 + 指纹链 + 中央校验收编
  （`methodology_intel:` 前缀，含 taxonomy/descriptors/viz_bridge/
  template_spec/graph/provenance）。
- 方法级检索有可复现基准与反循环纪律；语料演进只允许追加。
- 已知 registry gap（记录不改）：KDE 算法未声明资源硬闸
  （`max_features_hint`/`resource_envelope`）——大层场景的资源裁决
  待算法层补声明后接入 qualification。
- 中央校验纳入 5 个新前缀；BENCHMARK_MANIFEST 因 registry 投影变化
  需由权威 generator 再生成。

## 4. 本地验证

`tests/unit/gis/methodology/`（taxonomy/graph/descriptors/qualification/
ranking/case_corpus/service/feedback/provenance/budgets/central
validation，62 测试）+ `tests/cartography/test_template_intelligence.py`
（12）+ `tests/unit/gis_harness/test_knowledge_tools.py`（7）+
既有 methodology/compiler 套件零回归。
