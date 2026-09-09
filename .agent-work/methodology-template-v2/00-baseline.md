# 00 — Baseline Audit（Phase A 只读深度审计）

基线：`origin/master @ 8a33e3a5`（2026-09-10，PR #1168 workflow-v4 已合并）。
审计方式：主 agent 直接阅读生产代码与测试；计数由 registry 单例实际加载验证。

## 1. Registry 全景（实测计数）

| Registry | 位置 | 计数 | 性质 |
|---|---|---|---|
| CapabilityRegistry | `app/lib/gis/capability_registry.py` + `app/lib/gis/capabilities/` 域包 | 139 | **canonical**（capability 词表） |
| AlgorithmRegistry | `app/lib/gis/algorithm_registry.py` + `app/lib/gis/algorithms/` 域包 | 208 | **canonical**（算法语义目录；`validate()` 校验 capability/artifact/工具/precondition/引用） |
| ArtifactTypeRegistry | `app/lib/gis/artifacts.py` | 21 | **canonical**（artifact 语义类型；含 `typical_map_models` 文档性字段） |
| TaskOntology (ONTOLOGY_TASKS) | `app/services/gis_harness/gis_ontology.py` | 46 tasks / 9 domains | **canonical**（任务本体；fallback 策略/歧义规则/组件期望） |
| MethodologyRegistry | `app/services/gis_harness/workflow_v4/methodology.py` | 12 families / 44 methods | **canonical**（方法论族透镜 + 候选方法 + 资格裁决） |
| RecipeRegistry | `app/services/gis_harness/recipes.py` + `recipe_packs/` 26 域包 | 164 | **canonical**（制图 recipe：能力/数据角色/义务/制图/资格规则） |
| MapModelRegistry | `app/lib/cartography/model_library.py` | 80 | **canonical**（制图模型：requirements/components/classifiers） |
| ComponentRegistry | `app/lib/cartography/component_registry.py` | 19 | **canonical**（组件 descriptor：placement/collision/accessibility/renderer 真值矩阵对账） |
| CompositionTemplates | `app/lib/cartography/composition_templates.py` | 5 seeds | **canonical**（组件槽位组合模板） |
| ProductTemplateRegistry | `app/services/gis_harness/product_templates.py` | 9 | **canonical**（产品模板：layer roles/任务亲和/默认组件） |
| TemplateRegistry（样式） | `app/schemas/template_registry.py` | ~100 | **canonical**（样式预设/composite） |
| MethodReferences | `app/lib/gis/method_references.py` | 124 | **canonical**（文献出处） |
| AnalysisPatterns | `app/lib/gis/analysis_patterns.py` | ~20 | **canonical**（分析模式：归一化指引/常见错误/披露） |
| WorkflowFamily/Composite/Scenario | `app/services/gis_harness/workflow_families.py` | 派生 | **projection**（RecipeRegistry 确定性投影，非第二事实源） |
| tool→capability / capability→tool | AlgorithmRegistry 派生缓存 | 派生 | **projection**（`tool_to_capability()` 按内容缓存） |
| BENCHMARK_MANIFEST / quality artifacts | `docs/quality/` | 生成 | **generated**（权威 generator 生成，禁手改） |

## 2. 自然语言请求 → 执行链路（现状）

```
query → intent.MapRequestIntent (intent.py)
      → Stage2 match_task_ontology (gis_ontology.match_intent, 打分制)
      → RecipeRegistry.select_candidates (recipes.py:927, 确定性评分)
      → plan_candidates (DATA_FIT_SCORE 五态贴合分)
      → compile_workflow (15 阶段 base) / compile_workflow_v4 (23 阶段)
          stage16 resolve_methodology: resolve_methodology_family_for_query
                  (专业词路由 > 任务覆盖族)
          stage17 select_method: qualify_method_candidates
                  (硬准则: geometry/sample/roles/preconditions;
                   软排序: role_fit .40 + method_quality .30 + priority .20 + precondition .10)
          stage18 compile_typed_dag (typed_dag.py)
      → Harness runtime / MapSpec / cartography render
```

**方法选择现状**：非 LLM 即兴 —— 是「本体任务匹配 + 专业词方法族路由 + 数据事实资格裁决」的确定性混合（`workflow_v4/methodology.py:695-939`）。
LLM 只能在 goal_graph 校验通道内提建议（`goal_graph.py:409` 确定性裁决）。

## 3. Phase A 问题解答（对应 Epic §3 的 12 问）

1. **registry 位置**：见 §1 表。
2. **canonical vs projection**：见 §1 表；family/composite/derived 索引均为投影；`validate_gis_library()`（`registry_validation.py:29`）是中央对账点（ontology + methodology + algorithms + capabilities + artifacts + models + recipes + products + catalog 全部收编）。
3. **NL 入口**：见 §2。
4. **方法选择机制**：规则+资格（混合），见 §2；V4 已有 method-level 资格裁决。
5. **模板本质**：静态样式（TemplateRegistry）+ 产品结构（ProductTemplate）+ 组件槽位组合（CompositionTemplate）三层；样式/结构/组合均为数据声明，非代码逻辑。
6. **算法输出↔可视化 typed bridge**：**部分存在**——ArtifactTypeDescriptor.typical_map_models（文档性，无消费方强制）、CapabilityDescriptor.compatible_map_models、MapComponentDescriptor.compatible_artifact_types（仅 legend/table 族声明）。**无统一的 artifact→viz family→legend semantics→slot binding typed 链**。
7. **声明可用但无 production consumer**：`ArtifactTypeDescriptor.typical_map_models`（文档性）；AnalysisPattern.optional_capabilities 部分仅投影；ComponentSlot.preferred_templates 消费薄弱。
8. **ID alias/drift**：MapModelRegistry 有别名解析（`models.resolve`）；component `type` vs `id` 双索引（`get_by_type`）；template_catalog 的 `_infer_style_compatibility` 从 kind/payload 推导兼容性（推导≠声明，存在隐性耦合）。`_component_id_for_type`（component_composer.py:10）含类型→实例 id 硬编码映射。
9. **同类 PR**：无。最近大方向为 workflow-v4（#1168 已合并，方法论族 V4 即其产物）、cartography V5（#1170）、quality V2（#1172）；open PR #1173-1180 为并行 Epic（harness-v6/lakehouse-v7/science-v5/geocompute-v7/modelops）。**本 Epic（methodology+template intelligence）无重复建设**。
10. **典型错误方法案例**：现有防线——ontology fallback 策略（分布≠热力图 `gis_ontology.py:149-192`）、density_quantitative 分母缺失披露（`:236-250`）、Gi* 统计前提不满足降级视觉热力（`:378-399`）、data_qualification CRS/样本/空值检查、algorithm_resolver crs_class 硬门（`algorithm_registry.py:27-36`）。**缺口**：categorical 字段被连续插值、4326 下米制 buffer 静默、raw-count choropleth 在应归一化场景的直接比较（pattern 层有指引但无 method-level 硬门）、antimeridian。
11. **retrieval benchmark 区分**：`app/evaluation/retrieval_corpus.py`（V4，query→tool 投影完备性 ≥2000）与 `retrieval_eval_corpus.py`（V5 开环 query→tool 人工金标 ≥60，hard-negative/near-duplicate/ambiguous）已区分；`methodology_corpus.py` 是 task→**family** 双语语料（36+ 评估，五维）。**task→method（method-level）的 Recall@k/MRR/invalid-rate/abstention 无基准**。
12. **知识来源**：全部为代码内审定表（curated tables），有 registry 指纹（ontology/methodology/recipe fingerprint）与中央校验；无 LLM 生成知识入库通道。**缺**：每条知识的 provenance 记录（source type/confidence/validated fingerprint）。

## 4. Findings（P0-P3）

### P0（阻塞本 Epic 设计的事实）
- **F-P0-1** 方法资格已有 V4 引擎（`workflow_v4/methodology.py:808` qualify_method_candidates）：Epic 的 D/E 必须做成**增强适配层**（richer 事实维度 + 统一报告 + abstention），不得重写第二套资格路径。证据：`tests/unit/gis_harness/test_methodology_v4.py`、`test_methodology_central_validation.py`。
- **F-P0-2** 中央校验 `validate_gis_library()` 是唯一对账入口：新 registry/知识表必须并入（前缀化 issues），否则违反仓库不变量。证据：`registry_validation.py:29-80`、`test_methodology_central_validation.py:11`。
- **F-P0-3** graph 不得成为第二 registry：全部边引用 canonical id，builder 从单例注册表投影构建；注册表变更 → rebuild + fingerprint 变化。仓库已有同构先例（workflow_families 投影模式，`workflow_families.py:1-27` 红线注释）。

### P1（本 Epic 必须修复/补齐）
- **F-P1-1** 任务本体缺口：Epic 20 类中 **proximity（邻近/缓冲）、overlay（叠加）、clustering（显式聚类）、spatiotemporal_pattern、uncertainty_mapping、atlas_reporting** 无对应 ontology task（`gis_ontology.py:144-1408` 全量核对）。需纯加法补齐 ontology tasks + taxonomy 对齐层。
- **F-P1-2** 无统一 knowledge graph：task→data/algorithm/artifact/viz/component 关系散落 6+ 处（ontology、methodology candidates、capability compatible_map_models、artifact typical_map_models、component compatible_*、recipe packs），无单一 typed 视图、无 graph fingerprint/diff。
- **F-P1-3** 方法级 retrieval 基准缺失：methodology_corpus 只到 family 级；无 Recall@k/MRR/invalid-method-rate/abstention-precision、无 method-level hard-negative 双语语料。
- **F-P1-4** Algorithm↔Visualization bridge 无 typed 契约：artifact→viz family→legend semantics→slot binding 链条不存在（§3.6）。
- **F-P1-5** TemplateSpec 无 DataBindings/CapabilityRequirements/ExportConstraints 一等字段：CompositionTemplate 有槽位/碰撞/布局，产品模板有 component_requirements（Dict[str,str] 弱类型），但「绑定到哪个 artifact/角色」「需要什么能力才可渲染」「导出约束」无一等 schema。
- **F-P1-6** 组件 descriptor 缺 semantic_role/examples 字段（`component_registry.py:37-76`）；compatible_artifact_types 仅 2 个组件声明 → 组合规划器的 artifact 兼容判定依据不足。
- **F-P1-7** 无知识 provenance 契约：method_references 只管文献；curated 知识无 source_type/confidence/validated_fingerprint。
- **F-P1-8** 无 feedback 契约：runtime 失败/用户纠偏无 schema 化进入离线语料的通道（cartography quality_loop 是渲染域局部闭环，不覆盖方法选择反馈）。

### P2（应修，非阻塞）
- **F-P2-1** `ArtifactTypeDescriptor.typical_map_models` 文档性无消费方（`artifacts.py:32` 注释自认）——viz bridge 应把它变成有校验消费方的字段或被 bridge 取代。
- **F-P2-2** `_infer_style_compatibility`（`template_catalog.py:21-65`）启发式推导与显式 compatibility 并存——推导规则可能漂移，bridge/planner 优先显式声明。
- **F-P2-3** `_component_id_for_type`（`component_composer.py:10-26`）硬编码类型→实例映射，新组件类型须手改——组合规划器应从 descriptor 派生。
- **F-P2-4** `component_expectations`（ontology）/`component_requirements`（product template）是自由字符串，无 registry 对账。
- **F-P2-5** AnalysisPatterns 的 common_pitfalls 是自由文本，无机器可读警告码（pattern_projection 有 warning_codes 但 pattern 表本身无结构化 invalid-method 声明）。

### P3（记录不改）
- **F-P3-1** RecipeRegistry 164 recipes 是产品族时代产物，与 methodology 候选方法并存但粒度不同——本 Epic 不动 recipe 层。
- **F-P3-2** ONTOLOGY_DOMAINS 9 域词表与 Epic 20 类分类学不一一对应——taxonomy 是分类学视图（categories→tasks 映射），不改域词表。
- **F-P3-3** DATA_FIT_SCORE 的权重/未知中性分哲学（unknown≠不满足）必须沿用，不得在新的 qualification 里引入二值裁决。

## 5. 现有测试基线（复用/扩展对象）

- `tests/unit/gis_harness/test_methodology_v4.py` — 12 族/44 方法资格
- `tests/unit/gis_harness/test_methodology_corpus_v4.py` — 双语语料 + 五维编译评估
- `tests/unit/gis_harness/test_data_qualification.py` — 五态资格
- `tests/unit/gis_harness/test_gis_ontology.py` — 本体匹配/校验/指纹
- `tests/unit/gis_harness/test_workflow_compiler.py` / `test_compiler_v4.py` — 管线
- `tests/unit/gis_harness/test_template_catalog.py`、`tests/cartography/` — 模板/组件
- `tests/unit/gis_harness/test_methodology_central_validation.py` — 中央校验收编锁

## 6. 环境与门禁

- ruff：默认选集（E4/E7/E9+F），line-length 未启用（pyproject.toml:63-76）
- pytest：`asyncio_mode=auto`，60s timeout，`addopts` 带 `--cov=app`（coverage 闸由 CI 命令行控制，本地可 `-p no:cacheprovider --no-cov`）
- mypy：**未配置**（无 [tool.mypy]）；typecheck = pyright 不存在 → 本地以 ruff + pytest 为门禁
- heavy marker：需 geopandas/numpy/rasterio 的测试；本 Epic 用小型 synthetic fixture，不依赖 heavy
