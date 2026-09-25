# ADR-0214: Composition Contract — versioned Component ABI 与模板组合契约基座

- 状态：Accepted（本分支）
- 日期：2026-09-26
- 关联：ADR-0088（组件库 v2：MapSpec 单一存储 / renderer 真值矩阵）、
  ADR-0101（模板库 v3：pack 化 / TemplateSpecV2 / golden corpus）、
  ADR-0103（design system v4：descriptor 能力字段 / catalog 导出）、
  ADR-0070（PatchComponentIntent 单变更入口）、ADR-0072（user-wins 守卫）、
  ADR-0152（symbology 唯一裁决）、ADR-0160（W0.3 备选组合调用契约定稿，
  W5 接线面）、ADR-0205（grammar 规划层）、ADR-0211（export lineage /
  EXPORT_PARITY_EXEMPT_TYPES）
- 设计与勘察：`docs/dev/f11-composition-contract-design.md`、
  `docs/dev/f11-template-component-composition-recon.md`

## 背景

组件目录学（descriptor V7）、组件图投影（typed links/cycle/topo）、组合
模板槽位语义、TemplateSpecV2 规划器、renderer 真值矩阵均已落地，但
「模板由可替换组件组成、Agent 能发现并组合、用户能锁定局部」的**稳定
契约基座**仍缺五块：

1. **版本失语**：descriptor 只有族级 `schema_version=1`；
   `ComponentTemplate.template_version` 是从未被消费的死字段；MapSpec、
   plan、product 指纹均不携带「这张图用了哪个模板的哪个版本、哪些组件
   ABI 版本」——模板/组件定义演进无法被 fingerprint 捕获，replay 与
   drift 归因缺少组合身份维度。
2. **契约失形**：composition template 是扁平 slot 列表；「Template =
   Component Graph + Constraint Set + Style Tokens」三件套分散在三个
   模块且没有任何一个 versioned、可序列化、可 diff、可确定性重放的对象
   把它们装订在一起。
3. **Agent 无组合工具面**：`registry.search/recommend`（有界+理由）、
   `composition_alternatives_payload`（W5 接线契约）已建成但零生产调用；
   Agent 只能整包 apply 风格模板，不能按目的发现组件、不能按契约组合、
   不能替换单个组件。
4. **用户锁缺失**：组件级 user-wins 零实现——模板应用与 agent 变更
   理论上可覆盖用户已调的样式/位置（现状仅靠 expected_revision 乐观并发
   与前端 `_userPinned`，服务端无守卫）。
5. **conformance 缺执行器**：export parity 只有静态矩阵（`live_export_parity`
   声明无检查）；模板创作期 layout/zone 约束、契约↔组件版本兼容、a11y
   披露均无。

## 决策

D1 **Component ABI 是投影，不是第二注册表。** 新增
`app/lib/cartography/component_abi.py`：`COMPONENT_ABI_VERSION=1`；每组件
类型一条 `ComponentABIRecord`（id/type/version/slots/layout intent/export
support/a11y/compat/deps/defaults），由 descriptor + renderer 真值矩阵 +
组件模板 default_options **投影**生成（沿 `_SUPPORT_MATRIX` /
`COMPONENT_SEMANTIC_ROLES` 平行表先例），单一事实仍在既有模块。props
schema 用**有界受限词汇**（type/enum/default/max/min/bounded），不做全量
JSON Schema；每类型声明与 `validate_props` 纯校验配套。registry.validate
新增 fail-closed 交叉检查：每个 native 组件类型必须有 ABI 条目、ABI 引用
的组件模板 id 必须可解析。**不新增 ComponentType 成员**（ADR-0101/0103
红线）。

D2 **TemplateContract v1 是装订层，不是第二模板真相。** 新增
`app/lib/cartography/composition_contract.py`：`CompositionContractV1` =
template_id/version + slot 索引（引用 MapCompositionTemplate，不复制）+
graph skeleton（slot 间 typed links：requires/under/annotates/groups）+
constraints（zone/exports/tokens 绑定）+ StyleTokens preset 引用 +
compatibility（map models / min abi version）。canonical serialize
（sort_keys 确定性 JSON）、`contract_fingerprint`（sha256，模式同
TemplateSpecRegistry）、`diff_contracts`（有界结构 diff + reason codes）、
`apply_contract`（确定性 lock-aware replay：只填空槽、只换未锁实例、
保留用户编辑、逐项披露跳过原因）。契约错引用（模板 id / token preset /
slot 引用不存在）在注册期 fail-closed。

D3 **组合身份入 spec、版本变化入指纹。** MapSpec additive：`LayoutSpec.
composition`（template_id、template_version、contract_id、
contract_fingerprint、component_abi_version、component_versions 投影、
applied_revision —— 全部有界）。组件实例不新增字段：`provenance`
（origin/source_template/contract）经 `extra="allow"` 自由域写入，
锁单一事实在 workbench（见 D4）。`extra="allow"` 已保真，不升 schema
版本、不加 upgrader。提交通道：`SetLayoutIntent` additive 双字段
`component_links` / `composition`（None = 不触碰既有值），
`mapspec_store.layout_set` 透传 `expected_revision` CAS。身份块随 `layout` 进入既有 `cartographic_fingerprint` 投影 →
模板/组件版本变化自动改变指纹（DoD）；product/plan 侧因内嵌 spec 同步
捕获。纯增量：未应用契约的存量 spec 指纹不变。

D4 **user 锁单一事实 = W15 workbench 锁集，契约层只做槽位级避让。**
勘察修正：W15（Contextual Cartographic Harness V6）已在 lifecycle engine
落地组件级锁——``spec.workbench.lockedComponentIds`` +
``guard_intent_locks`` 对 agent/system 意图全量拒绝（单码
``layer_locked`` + ``locked_component_ids`` 载荷）。本 ADR **不新增第二
锁位**（最初的 per-instance ``user_lock`` 字段方案废弃）：契约 apply 读取
锁集（``locked_component_ids_of``）做槽位级跳过（锁实例零触碰 + 逐项
披露）；``webgis_apply_composition`` 在提交载荷含锁组件时提前拒绝
（``component_locked:user_wins`` + 解锁指引），引擎守卫事务内二次裁决；
契约作者的 ``locked_default`` 降为 advisory 披露（agent/契约不得代替
用户置锁）。SetLayoutIntent 以 additive 双字段（``component_links`` /
``composition``，None = 不触碰）成为契约提交通道——身份块随 layout 进入
既有指纹投影（锁定变化不入指纹：锁是组织态而非制图语义，沿 W15 分类）。

D5 **Conformance 是纯函数报告，失败披露优先于硬失败。** 新增
`app/lib/cartography/composition_conformance.py`：码表（两档 severity）
——`export_parity_gap`（在场组件类型 × 声明导出目标 × _SUPPORT_MATRIX，
尊重 `EXPORT_PARITY_EXEMPT_TYPES`）、`version_incompatible`（契约
min_abi vs `COMPONENT_ABI_VERSION`；spec 内组件 abi 投影 vs 当前表）、
`slot_zone_invalid`（模板创作期：slot zone ∉ 描述符 allowed_positions）、
`a11y_undisclosed`（warning：组件 a11y 元数据缺失，诚实披露不否决）、
既有图级码复用 `validate_component_graph`。报告有界（码表封顶），
`CompositionTemplateRegistry.validate` 创作期接入（appenditive——存量
seed/pack 必须零新 issue，测试锁）。

D6 **Agent 工具三件，全部有界 + reason codes + 租户纪律。** 新增
`app/tools/composition_tools.py` 注册三工具：
`webgis_discover_components`（输入=结构化目的/角色/输出目标/工件类型，
禁 query 字符串——沿 ADR-0160 W0 负例纪律；输出=有界候选 + 理由 +
`composition_alternatives_payload` W5 接线 + 能力预披露：候选组件的
renderer/exporter support 如实带回，缺失能力提前暴露）；
`webgis_apply_composition`（契约/模板 → lock-aware apply → 身份块落盘 →
返回 conformance 预检；conformance error 级存在即 fail-closed 不落盘）；
`webgis_plan_component_replace`（**只读**替换规划器：同语义角色替代 +
ABI props 前置校验 + 锁预检 + conformance 预披露，返回
`webgis_component_update` 执行参数——组件突变单一入口仍是
PatchComponentIntent/webgis_component_update（ADR-0070），本工具不建
第二写路径）。DB 触点沿用 `template_scope_clause` +
`asyncio.to_thread`（#1442/#1444 模式）；本三工具为纯目录/内存域，无
新 DB 面。

D7 **统一 preset 面是引用表，不是第四处真值。** 新增
`app/lib/cartography/component_presets.py`：purpose → `{composition
template id, per-slot component-template presets, StyleTokens preset id,
contract id}`；F11 四目的（basic_thematic / heat_distribution_stats /
classified_categorical / change_comparison）各一份 bundle；taxonomy 单一
真值仍是 component_taxonomy / SEMANTIC_ROLES / component_templates。

D8 **pack 只补真缺口。** 新增 `composition_packs/core_purposes.py`：
`composition.classified_categorical`（categorical_legend 主绑定的通用
分类专题图，非遥感域）+ 配对 TemplateSpecV2 spec；其余三目的复用既有
模板（standard_analysis / density_map / statistical_map /
temporal_change_report 族），以 preset bundle 组织，不堆重复模板
（seed id/priority/fallback 链零改动）。

## 非目标

- 不新增 `ComponentType` 成员；不动 `_SUPPORT_MATRIX` 既有行。
- 不改 grammar 裁决语义（ADR-0205）；契约层只消费其词汇。
- 不动前端 catalog schemaVersion（ABI 投影是后端 agent 契约；前端继续走
  生成目录，schema churn 留给后续方向）。
- 不做用户自定义 composition template 的 DB 存储面（只读身份与锁定；
  存储面是独立方向，需 scope/租户/退役路径完整设计）。
- 不改 `list_templates`/`apply_template`（风格模板域保持 #1442/#1444 后
  现状）。

## 后果

- 组件/模板定义演进从此可被 plan/product 指纹捕获（DoD #4）；
  replay/drift 归因获得组合身份维度。
- Agent 组合从「整包风格模板」升级为「发现→组合→替换」三步契约面，
  全程有界 + reason codes + 用户锁守卫。
- 存量零迁移：全部 additive（descriptor 不动、schema 不升版、seed 不动、
  golden 默认选择不动）；契约未应用的 spec 指纹逐位不变。
- 新增失败面（ABI 表漏项/契约错引用）全部 fail-closed 在注册/校验期暴露，
  不进入运行时静默。
