# F11 — Cartographic Template & Component Composition Foundation：Recon

- 基线：`origin/master = 9e1ad22907e99cd7b4721153294448ce6496c717`（2026-09-24 05:00 +08:00，
  Merge PR #1494）。执行时 `git fetch origin --prune` 后确认为最新；与 seed snapshot 一致。
- Worktree：`/home/kevin/project/wt-webgis-f11-template-component-composition-20260926-9e1ad229`
- Branch：`zcode/f11-template-component-composition-20260926-9e1ad229`（merge-base = 9e1ad229）
- 注意：主工作目录本地 master（d5315716）落后 origin/master 且携带未推送的 audit-batch
  提交；**本任务全部工作只基于 origin/master**，不从本地 master 搬运任何文件。

## 1. 基线事实（origin/master @ 9e1ad229）

### PR / issue 面去重

- Open PR：仅 #1489（dependabot node 25 bump）——与本方向零交集。
- 最近功能合并波次：#1479–#1488（2026-09-21，ADR-0204 世代）+ 依赖 bumps。
- 与本方向相关的近期安全修复：**#1442（list/apply_template 跨租户）已修**（PR #1446，
  `app/services/templates/scope.py::template_scope_clause`）；**#1444（apply_template 同步
  DB 阻塞）已修**（PR #1449，`asyncio.to_thread`）。二者均不得重复实现；新工具必须沿用
  scope.py 租户过滤 + to_thread 纪律。
- Open issues：#1436（i18n）、#1377（audit 延期跟踪）——均不与本方向冲突。

### #1479–#1488 复核（本 Prompt"未完成面"是否仍成立）

逐项对照后确认：F11 列出的缺口在 9e1ad229 上**仍然成立**，未被后续 PR 完成：

| F11 目标 | 最新 master 现状 | 结论 |
|---|---|---|
| versioned Component ABI | descriptor 仅 `schema_version=1`（族级）；`ComponentTemplate.template_version="1.0"` 从未消费；MapSpec/plan/product 均不携带组件/模板版本 | **缺口成立** |
| taxonomy/presets 统一 | taxonomy（component_taxonomy.py + SEMANTIC_ROLES）完备；presets 散在三处（component_templates 变体、chart-kind presets、required_components_for 基线），无统一 preset 面 | **缺口成立** |
| Template = graph+constraints+tokens+slots | composition_templates 是扁平 slot 列表；StyleTokens 存在但无模板引用；TemplateSpecV2 是最近壳但无 tokens/graph 节 | **缺口成立** |
| agent discovery/compose/replace 工具 | `registry.search/recommend`（有界+理由）已建但**无 agent 工具面**；`composition_alternatives_payload` 自注"W5 接线面"零生产调用 | **缺口成立** |
| 组件级 user locks/provenance/patch | patch 存在（webgis_component_update + expected_revision）；**组件级锁零实现**；provenance 仅 layer-visibility 域 | **缺口成立** |
| conformance（循环/slot/renderer 已有；export parity/layout/版本兼容缺） | 静态矩阵存在；`live_export_parity` 声明无执行器；无模板创作期 layout 约束检查；无版本兼容检查 | **缺口成立** |
| MapSpec/Compiler serialize/diff/replay + 指纹捕获 | MapSpec canonical 化 + cartographic_fingerprint 存在；**composition 模板身份不入 spec、registry/模板版本不入指纹** | **缺口成立** |
| 4 类 composition packs | 语义上均被域包覆盖，但无 basic-thematic/heat+stats/classified/change 的显式组织；通用分类图（非遥感）无模板 | **部分缺口**（通用 classified 缺，其余以 contract/preset 组织而非堆模板） |

## 2. 五列对照表

### Overlap（与本任务交集的活跃工作）

- #1489（dependabot/docker/node）：无文件交集。
- 并发 worktree：`wt-webgis-f09-trace-replay-oracle-v3-20260926-9e1ad229`（F09 方向，
  同基线）——heat zone 预计在 `app/lib/harness/replay/*`；本方向不触该目录。

### Already Done（不得重复实现）

- 组件 descriptor 目录学（V4/V5/V7 全量字段）、`_SUPPORT_MATRIX` renderer/exporter 机器真值
  及 `validate_against_descriptors` 交叉审计、`EXPORT_PARITY_EXEMPT_TYPES`（ADR-0211）。
- Component graph 投影（typed links/cycle/topo order/break cycles，schema 1.2 component_links）。
- Composition template slot 语义（required/optional/forbidden/bind_scope/preferred_templates）
  + `validate_component_composition` 槽位/描述符校验 + 8 seed + 20 pack 模板。
- TemplateSpecV2 + plan_composition 确定性规划器 + TemplateSpecRegistry 指纹。
- `search()/recommend()`（确定性得分 + 有界 reasons）、`composition_alternatives_payload`
  契约定稿（W0.3 fixture 锁定）。
- MapSpec canonical 化/upgrader/round-trip、cartographic_fingerprint、repair patch fingerprint、
  harness trace/replay 闭环（ADR-0212，F09 热区）。
- #1442 租户过滤 + #1444 异步化（scope.py + to_thread 模式）。
- 前端 catalog 生成链（schemaVersion 5 + registry-parity 测试）。

### Still Missing（本任务要补的下一层）

1. **Component ABI v1**：每组件语义 `version` + 有界 props schema + ABI 投影记录
   （type/id/version/slots/layout intent/export support/a11y/compat/deps/defaults 的单一可验证视图）。
2. **TemplateContract v1**：把 composition template 提升为「graph skeleton + constraints +
   style tokens + slots」的 versioned 可序列化契约：canonical serialize、fingerprint、
   bounded diff、确定性 lock-aware apply（replay）。
3. **Composition conformance 执行器**：export parity（对每个在场组件类型 × 声明导出目标
   查 _SUPPORT_MATRIX，尊重 EXEMPT）、模板创作期 layout/zone 约束、契约↔ABI 版本兼容、
   a11y 披露（honest warning，不做硬否决）。
4. **MapSpec composition 身份块**：`layout.composition`（template id/version、contract
   fingerprint、component abi 版本投影）additive 落盘 → 既有 cartographic_fingerprint 自然
   捕获版本变化（DoD #4）；plan/product 侧 digest 因内嵌 spec 同步捕获。
5. **Agent-facing 工具**：`webgis_discover_components`（purpose→有界候选+理由+alternatives
   W5 接线+能力预披露）、`webgis_apply_composition`（lock-aware 应用+身份块落盘）、
   `webgis_plan_component_replace`（**只读**替换规划：同语义角色替代 +
   ABI props 前置校验 + 锁预检 → 产出 webgis_component_update 执行参数；
   组件突变单一入口仍是 PatchComponentIntent，ADR-0070）。
6. **组件级 user locks**：实现取 W15 既有 `workbench.lockedComponentIds`
   单一真相（引擎守卫执行）——契约 apply 槽位级零触碰 + 工具层提前拒绝
   （reason code `component_locked:user_wins`）；组件 `provenance`
   （origin/source_template）经 extra="allow" 落盘。不新增第二锁位。
7. **统一 preset 面**：purpose→（composition 模板引用、slot→component-template preset、
   StyleTokens preset、contract id）四元组；4 个 F11 purpose 各一份 bundle。
8. **通用分类图 pack**：`composition.classified_categorical`（categorical_legend 主绑定的
   非遥感分类专题图）+ 配对 TemplateSpecV2。

### Must Not Touch

- `app/services/gis_harness/components.py` 的 `ComponentType` Literal（ADR-0101/0103
  non-goal：不新增成员；schema/components/descriptor/template 四层锁步）。
- `app/lib/cartography/component_renderers.py::_SUPPORT_MATRIX`（单一 renderer 真值，测试锁）。
- `app/lib/cartography/symbology.py::resolve_symbology`（ADR-0152 单一裁决）。
- `app/lib/cartography/mapspec_schema.py` 版本矩阵/canonical round-trip 契约（golden 锁；
  只允许 additive 可选字段，不升 schema 版本——`extra="allow"` 已保真）。
- composition/component templates 的 seed id、priority、fallback 链（golden corpus 依赖默认选择）。
- `tests/cartography/golden_corpus/**`（只经 `GOLDEN_CORPUS_UPDATE=1` 再生）。
- `frontend/lib/map-components/component-catalog.generated.json`（只经生成器再生）。
- `app/tools/templates.py`（#1442/#1444 刚重写；新工具放新模块，不顺手重构）。
- `app/services/gis_world_state/mutation.py`（ADR-0072 守卫；只扩不减）。
- `app/lib/harness/replay/**`（F09 并发方向热区）。
- grammar 语义裁决（ADR-0205 visual_variables/scale_rules/grammar_solver）——契约层只引用
  其输出词汇，不复制裁决逻辑。

### Integration Seams

1. ABI：新模块 `app/lib/cartography/component_abi.py` 平行表 + 投影（沿 `_SUPPORT_MATRIX`
   /`COMPONENT_SEMANTIC_ROLES` 先例；不改 descriptor 模型，零 catalog schema churn）。
2. Contract：`app/lib/cartography/composition_contract.py` 引用（不复制）composition
   template id + StyleTokens；canonical dumps 沿 `app/services/provenance/fingerprint.py`。
3. Conformance：新模块 `app/lib/cartography/composition_conformance.py` 纯函数；被 contract
   apply、新工具、`CompositionTemplateRegistry.validate`（创作期，appenditive）消费。
4. Tools：`app/tools/composition_tools.py` + `app/tools/__init__.py` 注册表加一行。
5. Locks/identity：`LayoutSpec` additive（`composition` 身份块）；`SetLayoutIntent` additive 双字段
   `composition` 块（extra="allow" 保真，不升 schema 版本，无 upgrader 需要）。
6. Packs：`app/lib/cartography/composition_packs/core_purposes.py` + `__init__.py` 一行。
7. Presets：`app/lib/cartography/component_presets.py`（引用面，无第二真值）。

## 3. 已知基线失败/flake

在干净基线（`git stash` 后的 9e1ad229 工作树）复现：

- `tests/unit/test_mapspec_store.py::test_validate_and_compile` ——
  **master 本来就红**（与本方向无关，未触碰其代码路径；本分支新增测试
  不依赖它）。其余本方向邻域（mapspec lifecycle/layout/store/lock guard、
  harness component 全域、cartography 合同域）在基线与分支上均绿。

## 4. 架构决策

见 `docs/adr/0214-composition-contract-component-abi.md` 与
`docs/dev/f11-composition-contract-design.md`。
