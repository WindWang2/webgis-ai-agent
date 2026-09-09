# Workflow V4 — Baseline (Phase A)

- worktree: `/home/kevin/projects/webgis/workflow-v4`
- branch: `feat/workflow-v4-semantic-compiler`
- baseline commit: `445ad30e` (origin/master, PR #1162 merge)
- date: 2026-09-09

## 既有平台事实（已验证，非假设）

### Semantic Workflow / Recipe Foundation V3（本 Epic 的直接基座）

| 事实 | 证据 |
|---|---|
| Recipe DSL V2：`WorkflowProfile`（schema_version=2）挂 `CartographyRecipe.workflow`，additive | `app/services/gis_harness/workflow_schema.py:162` |
| 数据角色词表 15 词 + 获取通道 4（local/data_fabric/user_upload/derived）+ 缺失策略 2（block/degrade） | `workflow_schema.py:40-77` |
| 科学义务 6 kind（precondition/denominator/temporal/uncertainty/transformation/disclosure）× 3 action | `workflow_schema.py:79-92` |
| 完成契约 7 维（data/analysis/science/cartography/observed_map/methodology_disclosure/uncertainty_disclosure） | `workflow_schema.py:95-103` |
| 降级分类 5（equivalent/approximation/proxy/degraded/not_allowed）+ 重算轴 5（data/algorithm/parameter/style/output） | `workflow_schema.py:106-115` |
| 确定性编译器 15 阶段（normalize_intent → … → produce_completion_contract），零 LLM/零 I/O | `workflow_compiler.py:35-51` |
| 编译产物 `WorkflowCompilation`（stages+reason codes+bounded evidence） | `workflow_compiler.py:76-118` |
| Recipe 总量 164（17 seed V1 + 147 V2），24 领域包 | 运行时验证 `r.count=164` |
| Family 层 V3（FAMILY_LAYER_VERSION=3）：152 families + 12 composites + 7 scenarios，从 RecipeRegistry 派生 | `workflow_families.py:12` + 运行时验证 |
| GIS 任务本体 V3（ONTOLOGY_VERSION=3）：9 域 46 任务，TaskDescriptor 含数据角色/几何期望/能力引用/歧义规则/回退层 | `gis_ontology.py:37,40,83` |
| 数据资格四态（qualified/degraded/unresolved/blocked）+ 修复声明 | `data_qualification.py` |
| 四层回退（preferred/degraded/minimal/blocked） | `fallback_v3.py` |
| 单测基线：test_workflow_compiler/test_workflow_schema/test_workflow_families = 56 passed | 本地 pytest 实测 |

### 明确不许碰的边界（其他平台所有）

- tool 执行 runtime → GIS Harness V4/V5（`planner_runtime.py`/`workflow_instance.py`）
- 算法数值实现 → Spatial Science（`app/lib/gis/scientific_preconditions.py` 单一事实源）
- 分布式调度 → GeoCompute V5/V6
- 渲染引擎 → Cartography Workbench V4（MapSpec/MapModel registry）

## V4 差距图（真实 gap，全部有代码证据）

1. **Typed Workflow DAG 缺失**：`compile_capability_dag` 产物是字符串节点列表 `{capability, kind, status, optional, depends_on}`（`workflow_compiler.py:363-369`）——无 typed ports、无 units、无 CRS、无 artifact 类型边、无条件分支、无 parallel-safe 标记。
2. **Methodology family 一等模型缺失**：ontology domain ≠ methodology family；没有"描述制图/密度/插值/分区统计/适宜性/网络/地形水文/遥感/变化检测/空间统计/多准则/组合制图"的 12 方法族绑定（任务→候选方法→资格→义务→输出）。
3. **Recipe V4 候选算法契约缺失**：recipe 无 `candidate algorithms + qualification criteria + 排序` 声明；现 plan_candidates 是场景级（plan_candidates.py），不是方法级。
4. **义务继承缺失**：CompositeRecipe 组合时义务/数据角色不从 base→supporting 传播（`workflow_families.py` 组合层只做并入，无继承语义）。
5. **Workflow package/versioning 缺失**：无 semver、无 immutable compiled form、无兼容性矩阵。
6. **部分重算 affected-subgraph 缺失**：RECOMPUTE_DIMENSIONS 只是词表，没有"参数变化 → DAG 受影响节点集"的编译期计算。
7. **Workflow diff 缺失**：无两次编译产物间的语义 diff（recipe/参数/算法替换/义务变化）。
8. **Acquisition 声明窄**：4 通道无 catalog/STAC、无 provider、无 synthetic-explicit-only。
9. **Cartographic obligation 规则缺失**：无"点数不足禁 choropleth、密度语义禁 choropleth"等表达-数据资格约束。
10. **双语方法语料/编译器评估缺失**：conformance corpus 是中文为主；无中英平行语料 + 无效方法拒绝评估 + 确定性编译评估。

## 冻结的 Scope

### 必做（P0/P1，全部在本 Epic ownership 内）
- Typed workflow DAG（typed ports/units/CRS/artifact/optional/conditional/parallel-safe）
- Methodology family model V4（12 族，绑定本体任务）
- Recipe V4 schema 扩展（candidates + qualification criteria + evidence requirements）
- Qualification engine V4（method-level ranking，复用 data_qualification 事实）
- Obligation 继承（composite/nested 传播 + provenance）
- Workflow package/versioning（semver + immutable compiled + compatibility）
- Partial recompute（affected subgraph 编译期计算）
- Workflow diff（语义级）
- Acquisition alternatives 声明扩展
- Cartographic obligations（表达-资格规则）
- 双语方法语料 + 编译器评估 harness

### 明确不做（他 Epic ownership / 防冲突）
- 不改 tool runtime、算法数值实现、调度、渲染引擎
- 不动 `.github/workflows/**`、`scripts/ci-local.sh`、`CHANGELOG.md`（除非模板要求的最小追加）
- 不新增 alembic migration（V4 是编译期契约层，不落库——见架构决策）
