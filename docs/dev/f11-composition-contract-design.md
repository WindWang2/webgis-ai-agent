# F11 设计文档 — Composition Contract 基座（ADR-0214 落地）

状态：设计定稿（本分支）。实现以本文为准；偏差须回写本文。

## 0. 模块拓扑

```
app/lib/cartography/
  component_abi.py            # D1 ABI 投影 + props schema（新）
  composition_contract.py     # D2 契约 serialize/fingerprint/diff/apply（新）
  composition_conformance.py  # D5 conformance 报告（新）
  component_presets.py        # D7 purpose→bundle 引用面（新）
  composition_packs/
    core_purposes.py          # D8 classified_categorical（新，+__init__ 一行）
app/tools/composition_tools.py# D6 三工具（新，+app/tools/__init__.py 一行）
app/lib/cartography/mapspec_schema.py  # additive: LayoutSpec.composition,
                                       # MapSpecComponent.user_lock/provenance
app/lib/cartography/composition_templates.py  # validate() 创作期接 conformance
app/lib/cartography/component_registry.py     # validate() 接 ABI 交叉检查
app/lib/cartography/template_intelligence.py  # _CURATED_SPECS 追加 1 条
```

依赖方向：`presets → contracts → abi → registry/renderers`；tools → 前四者
+ composition_selection（W5）。无反向依赖、无环。

## 1. Component ABI（D1）

```python
COMPONENT_ABI_VERSION = 1

class PropsFieldSpec(BaseModel):
    type: Literal["str","int","float","bool","str_list"]      # 受限词汇
    required: bool = False
    default: Optional[Union[str,int,float,bool,None]] = None
    enum: Tuple[Union[str,int,float], ...] = ()
    min: Optional[float] = None
    max: Optional[float] = None
    max_len: int = 64          # str/list 有界

class ComponentABIRecord(BaseModel):
    id: str; type: str
    version: str                        # 组件语义版本 "1.0.0"
    abi_version: int                    # = COMPONENT_ABI_VERSION
    category: str; semantic_role: str
    slots: Tuple[str, ...]              # 可担任的 composition slot 语义
    layout_intent: LayoutIntent         # placement/positions/size_range/collision/responsive
    support: SupportMatrix              # renderer/exporter/outputs（引用真值矩阵投影）
    accessibility: ComponentAccessibility
    compatibility: Compat               # map_models/artifact_types/runtime_status/deprecated(_by)
    dependencies: Tuple[str, ...]; conflicts: Tuple[str, ...]
    defaults: Defaults                  # default_variant/variants/states/props 默认值投影

COMPONENT_ABI_TABLE: dict[str, ComponentABIMeta]
# ComponentABIMeta = version + props_schema（每类型手审静态表——props 形状
# 的真值在前端 renderer 与 composer 写入面，静态表由 fail-closed 校验对齐）
```

规则：
- 每个注册 native 组件类型必须有 ABI 条目（registry.validate fail-closed，
  码 `abi_meta_missing`）；ABI 表引用不存在的类型 → `abi_meta_orphan`。
- `version` 变更纪律：props schema 或渲染契约破坏性变化 → bump minor；
  ABI 形状本身变化 → bump `COMPONENT_ABI_VERSION`（当前 1）。
- `props_schema` 不核验运行时 options 全量（options 是 open dict），只对
  **agent 写入面**（replace/compose 工具载荷）做 `validate_props` 前置校验；
  失败 → 结构化 reason（`props_invalid:<field>`），不静默丢弃。

## 2. CompositionContract（D2）

```python
CONTRACT_SCHEMA_VERSION = 1

class ContractSlot(BaseModel):
    slot_id: str; component_types: Tuple[str, ...]
    preferred_template: str = ""        # component_templates id
    bind_role: str = ""; bind_scope: str = ""
    locked_default: bool = False        # 模板建议默认锁（如 attribution）

class ContractLink(BaseModel):
    src_slot: str; dst_slot: str
    type: Literal["requires","under","annotates","groups"]

class CompositionContractV1(BaseModel):
    schema_version: int = CONTRACT_SCHEMA_VERSION
    contract_id: str                    # "contract.core.classified_categorical"
    template_id: str                    # 引用 MapCompositionTemplate（必须可解析）
    template_version: str               # 契约锚定的模板语义版本
    slots: Tuple[ContractSlot, ...]
    links: Tuple[ContractLink, ...]     # slot 级图骨架（apply 时投影为实例边）
    style_token_preset: str = ""        # style_tokens STYLE_PRESET_IDS / 未来注册表
    theme_profile: str = ""
    export_targets: Tuple[str, ...]
    min_abi_version: int = 1
    compatible_map_models: Tuple[str, ...]
    deprecated_by: str = ""
```

纯函数：
- `contract_fingerprint(c) -> "contract-sha256:<hex>"`（sort_keys canonical）。
- `diff_contracts(a, b) -> {"slots_added/removed/changed", "links_…",
  "version_changed", "disclosures[:16]"}（有界）。
- `apply_contract(spec, contract, *, registry, now_revision) -> ApplyReport`：
  确定性 replay 语义——
  1. 读 `spec.layout.components`；对每个 contract slot：已有**未锁**实例且
     类型 ∈ slot → 保留（用户编辑不重置）；已有**锁**实例 → 跳过（披露）；
  2. 缺失槽位 → 按 preferred_template/`component_composer` 既有展开语义
     生成实例（legend 族 per-layer 展开沿用 ADR-0088 D2，不重写）；
  3. links → `layout.component_links` 增量边（同型同端点幂等：已存在不重复）；
  4. 写 `layout.composition` 身份块（§3）+ 每新实例 `provenance`；
  5. ApplyReport：`created/replaced_preserved/locked_skipped/links_added/
     disclosures`（各有界）+ `composition_identity`。
  幂等：对已应用契约的 spec 再 apply → 零 created、零 links_added。
- `contracts` 注册表（module-level，deterministic load）+ `validate()`
  fail-closed：template_id/token preset/slot 引用、link 端点、export 词表。

## 3. MapSpec additive 身份与锁（D3/D4）

```python
class CompositionIdentity(_SpecModel):      # layout.composition
    template_id: str; template_version: str
    contract_id: str = ""; contract_fingerprint: str = ""
    component_abi_version: int = COMPONENT_ABI_VERSION
    component_versions: Dict[str, str]      # type → version（有界 ≤32）
    applied_revision: int = 0               # 写入时的 mutation revision
```

- `MapSpecComponent.user_lock: Optional[bool]`；`provenance` 沿 extra="allow"
  dict（不新增字段：origin/source_template 键入 compatibility 同级自由域，
  schema 文档化即可）。
- 有界：component_versions ≤32 类型、身份块字符串 ≤128。
- 指纹：`cartographic_fingerprint` 的 layout 投影已含全 layout dict → 身份
  块/锁位变化自动改变指纹。**测试锁**：同 spec ±template_version → 指纹必变；
  无身份块存量 spec → 指纹逐位不变。

## 4. Conformance（D5）

```python
class ConformanceIssue(BaseModel):
    code: str; severity: Literal["warning","error"]
    message: str; ids: List[str]
MAX_CONFORMANCE_ISSUES = 32

conformance_report(spec, *, contract=None, template=None,
                   export_targets=None) -> List[ConformanceIssue]
```

码表：`export_parity_gap` / `version_incompatible` / `slot_zone_invalid` /
`a11y_undisclosed`(w) / `cycle`(e, 转发) / `unknown_component_type`(w, 转发)
/ `required_slot_missing`(e, 转发 composition_validation 语义)。
`CompositionTemplateRegistry.validate` 创作期只接 `slot_zone_invalid`（对
每个 seed/pack slot zone ∉ allowed_positions 的组合报警——存量必须零
issue，测试锁）；运行期报告由 contract apply 与工具消费。

## 5. Agent 工具（D6）

- `webgis_discover_components(purpose?, map_model?, semantic_roles?,
  output_target?, artifact_types?, limit≤8)` →
  `{candidates:[{type,name_zh,semantic_role,score,reasons[:4],
  renderer_support,exporter_support,abi_version,deprecated}],
  composition_alternatives:{version,count,candidates}, reason_codes}`
  （recommend() + composition_alternatives_payload W5 接线；全部有界）。
- `webgis_apply_composition(contract_id | template_id, session_id,
  expected_revision?)` → ApplyReport + conformance 预检（error 级存在则
  fail-closed 不落盘，返回 issues）。
- `webgis_replace_component(session_id, component_id, replacement:
  {type|variant|template_id}, expected_revision?)` → 同语义角色校验、
  锁拒绝（`component_locked:user_wins`）、props 前置校验、有界 diff 摘要。

失败面 reason codes 词表常量 `COMPOSITION_TOOL_REASON_CODES`（测试锁）。
无 DB 触点；session/MutationFacade 沿 harness 既有入口。

## 6. Presets 与 pack（D7/D8）

```python
PURPOSE_PRESETS = {
  "basic_thematic":        PurposeBundle(composition_template="composition.standard_analysis", contract="contract.core.basic_thematic", slot_presets={...}, tokens="screen"),
  "heat_distribution_stats": ...(density_map/statistical_map + stats/chart 预设),
  "classified_categorical": ...(新 pack 模板 composition.classified_categorical),
  "change_comparison":     ...(temporal_change_report 族),
}
```

新 pack 模板 `composition.classified_categorical`：priority=29（不抢 seed
默认选择）、categorical_legend 主绑定 + title/north/scale/attribution 必备
+ stats/chart 可选；配对 TemplateSpecV2（affinity: landuse/zoning/类别域）
追加进 `_CURATED_SPECS`（指纹只锁稳定性，测试已确认不锁值）。

## 7. 测试设计（负例纪律）

- `test_component_abi_v1.py`：投影完整性（registry 全 native 类型覆盖）、
  fail-closed（删 ABI 表项 → validate 报 `abi_meta_missing`）、props 校验
  正/负例、版本变化→abi 记录变化。
- `test_composition_contract_v1.py`：canonical serialize 稳定性、指纹稳定
  + 差异敏感、diff 有界、apply 幂等/锁跳过/用户编辑保留/链接幂等、错引用
  fail-closed、身份块入指纹（±template_version 二值断言）。
- `test_composition_conformance_v1.py`：export parity 正/负（含 EXEMPT）、
  版本兼容正/负、slot zone 创作期（存量零 issue + 注入坏模板报
  `slot_zone_invalid`）、a11y 披露、码表封顶。
- `test_composition_tools_v1.py`：三工具 reason codes、锁拒绝、W5 接线
  payload 形态、有界性、props 前置校验负例。
- `test_component_presets_v1.py` + `test_composition_packs_core_purposes.py`：
  bundle 引用完整性、pack 载入确定性、seed id 零影响、默认选择零漂移
  （golden 守护）。
- 邻域回归：cartography 全目录 + gis_harness tools + mapspec schema +
  golden corpus（只读比对）。

## 8. Out of Scope

前端 catalog schema bump、用户自定义组合模板 DB 面、`list_templates/
apply_template` 重构、grammar 裁决语义、replay 录制件格式（ADR-0212）。
