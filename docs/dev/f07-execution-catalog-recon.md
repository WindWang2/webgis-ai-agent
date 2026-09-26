# F07 — Unified GIS Execution Catalog · Recon(2026-09-26)

基线:`origin/master = 9e1ad22907e99cd7b4721153294448ce6496c717`(2026-09-24,
merge #1494)。**worktree 分支即从该 SHA 派生**;本地主 checkout 的 master
(d5315716,audit 批次线)不含 #1482,与本任务无关。兄弟方向 f08–f15 分支
同 fork 自 9e1ad229,对应 open PR #1497–#1504。

## GitHub 状态

- Open PR:#1489(dependabot docker)、#1497(F12)、#1498(F13)、#1499(F08)、
  #1500(F15)、#1501(F11)、#1502(F10)、#1503(F09)、#1504(F14)。
  **无 F07 PR,方向未被占用。**
- 最近 merged:#1490–#1496(dependabot)、#1479–#1488(2026-09-21 基础波次,
  ADR-0204)。#1482「ABI 2.0 binding convergence / conformance gate / dispatch
  chokepoint」已在本基线上:`app/lib/gis/capability_conformance.py`(4 issue 码,
  fatal/warning 分级)、`runtime_manifest` v4、`capability_abi_profile()`。
- Open issues:#1436(i18n)、#1377(audit 台账)。与 F07 无关。

## F07 课题在最新 master 上是否仍成立(复核 #1479–#1488)

| Prompt 列的缺口 | 最新 master 事实 | 结论 |
|---|---|---|
| 工具/算法/recipe 稳定 ID 聚合目录 | 各 registry 齐备但**无统一 versioned 聚合投影**(runtime_manifest 投影是有意有界的摘要,不含 side_effect/scale/temporal/几何约束) | 仍缺 |
| capability-first recipe 引用 | recipes.py 已 capability-first(preferred/optional_analysis),缺的是 catalog 侧 tool-candidate resolution 的统一视图 | 部分缺 |
| conformance(悬空/契约/依赖/单位) | #1482 只覆盖 capability↔tool 绑定 4 码;算法输出契约×工具 output_semantic_type、unit/geometry 冲突、deprecated provider、recipe 可达性**无 catalog 级校验** | 仍缺 |
| discovery API | tool_retrieval(词法)/capability_descriptors V7/candidate_v8 三套并行,均为单层视角;无「capability+data descriptor+situation+resource envelope」统一有界查询 | 仍缺 |
| 版本/弃用/替代 + stale 指纹 | manifest/recipe/graph 指纹齐备但碎片化;无 per-entry 指纹与「精确 stale 解释」 | 仍缺 |
| extension certification 最小契约 | 扩展侧 6 阶段管线完整(ADR-0201);**core catalog 侧无 certification 证据投影** | 部分缺 |
| 生成文档 | cartography/catalog_docs 模式成熟;execution catalog 无对应物 | 仍缺 |

## 单一真相层(必须消费、不得复制)

- `app/tools/registry.py` + `app/tools/descriptor.py`:ToolDescriptor V3
  (side_effect/scale/latency/memory/crs/unit semantics、deprecation_of、
  security_tier、capability_source),`descriptors()`/`all_metadata()`。
- `app/lib/gis/algorithm_registry.py`:AlgorithmDescriptor(crs_class、
  unit_requirements、scientific_status、resource_envelope、cancellation_profile、
  conformance_tests、fallback_semantics)。
- `app/lib/gis/capability_registry.py`:CapabilityRegistry 词表权威。
- `app/services/gis_harness/recipes.py`:CartographyRecipe + RecipeRegistry
  (`content_fingerprint_of`)、`workflow_schema.recipe_capability_ids`。
- `app/lib/gis/runtime_manifest.py`:manifest v4 汇聚点 + `is_stale_plan`。
- `app/services/gis_harness/capability_graph.py`:`source_fingerprints()` 9 源、
  节点/关系词表。
- `app/extensions_platform/`:命名空间规则(`{ns}_tool`、`{ns}.algorithm`)、
  `pack_catalog.build_pack_catalog` + `certification_status_for`(认证证据源)。

## Overlap / Already Done / Still Missing / Must Not Touch / Integration Seams

- **Overlap(复用勿重做)**:runtime_manifest(对账汇聚)、capability_graph
  (关系索引)、capability_conformance(#1482 绑定闸)、pack_catalog(扩展目录)、
  catalog_docs(文档生成模式)。
- **Already Done**:见上表「最新 master 事实」列右移项。
- **Still Missing(F07 空间)**:统一 versioned ExecutionCatalog 投影;
  per-entry + generation 指纹与精确 stale 解释;catalog 级 conformance
  (输出契约/单位几何/deprecated provider/recipe 可达性/certification 契约);
  统一有界 discovery;catalog 生成文档+防漂移测试。
- **Must Not Touch(兄弟 PR 热区)**:`app/tools/__init__.py`(f11/f12,仅允许
  一行接线)、`gis_harness/completion/*`(f13/f14/f15)、`api/routes/chat.py`
  (f13/f15)、`app/main.py`(f15)、`decision_record.py`(f09/f12)、
  `governor/dispatch_adapter.py`(f08/f13)、`recipes.py` 主体(f10)、
  `component_registry.py`(f11)、`lib/harness/replay/*`(f09)、
  `runtime_manifest.py` 内部(#1482 刚改)、`ToolDispatchService.dispatch`、
  `capability_bind.py`、`agent_pi_bridge.py`、`session_plan.py`、
  `map_plan_compiler/*`(f12)、`capability_graph.py` 主体(f09 改中)。
- **Integration Seams(只读消费入口)**:`get_capability_registry()`、
  `get_algorithm_registry()`、`ToolRegistry.descriptors()/all_metadata()`、
  `get_recipe_registry()`+`content_fingerprint_of`、
  `get_runtime_manifest()`、`validate_capability_conformance`、
  `recipe_capability_ids`、`catalog_docs` GENERATORS/`--check` 模式、
  `build_pack_catalog`/`certification_status_for`。

## 架构决策(实现依据)

1. **只读派生投影**:catalog 不注册、不反写、不替代任何 registry
   (ADR-0181 D1 / ADR-0204 D1 纪律)。新模块全部位于 `app/lib/gis/
   execution_catalog*.py` + `app/tools/catalog_discovery_tools.py`。
2. **指纹复用原语**:canonical-JSON SHA-256(descriptor.canonical_json 同款);
   per-entry 指纹 + generation 指纹(`EXECUTION_CATALOG_VERSION` 参与盐)。
   不发明新哈希原语。
3. **conformance 形态**:与 `validate_capability_conformance` 同款纯函数、
   fatal/warning 分级、MAX_FINDINGS 有界、排序确定;链式复用其结果。
4. **discovery 形态**:纯函数查询 + 显式 reason codes + 证据;默认 limit=5、
   硬上限 16;确定性 tie-break。agent 面以 `catalog_discover` 工具暴露
   (独立模块注册,`app/tools/__init__.py` 一行接线)。
5. **certification 集成**:provider 归属按扩展命名空间规则;证据经
   pack_catalog(缺 host/包时 fail-open 为 `unknown`,测试可注入);
   7 facet 最小契约纯函数校验,缺口报 `catalog_extension_contract_incomplete`。
6. **文档**:生成 `docs/catalog/execution-catalog/*.md` + manifest JSON,
   `--check` 漂移模式 + subprocess 漂移测试(复刻 catalog_docs 模式)。
7. 不新增 ADR 编号(0214+ 已被兄弟分支占用),设计记录写
   `docs/dev/f07-execution-catalog-design.md`。

## 性能/资源预算

合成宽度目标:≥400 tools / ≥300 algorithms / ≥300 recipes 下编译 O(n) 有界、
查询有界;compile+query 预算以测试断言(编译 < 5s、发现查询 < 100ms 量级,
具体以本机实测校准);findings/candidates 全部有上限并披露截断。
