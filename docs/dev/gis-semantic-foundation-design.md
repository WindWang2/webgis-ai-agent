# GIS Semantic Foundation — 勘察与设计（方向 2）

> 分支：`feat/gis-semantic-measurement-foundation`（自 origin/master 5a4d4632）
> 日期：2026-09-20
> 状态：设计定稿 → 实现

## 1. Problem Statement

Intent、Planner、Qualification、Capability、Cartography、Verification 各自
猜测 geometry / field / unit / CRS / scale / temporal 语义。勘察证实：**语义
资产的"生产侧"已基本闭环，但"消费侧"大面积断线**——

- 语义角色（`SemanticFieldRole` 14 角色 + 三级证据置信）已存在，但
  cartography（classify/palette/legend unit）**零消费**；
- CRS 资格（classify_crs / scientific_preconditions / crs_projection_
  obligation）三层闭环，但 **unit 只有 hint 与报警，没有量纲契约**：
  count vs density、rate vs absolute、percent [0,100] vs fraction [0,1]
  全靠 prompt 文本与 LLM 自觉；
- 字段解析只有"每角色第一个命中字段"的 role_index，**没有用户短语 → 字段**
  的确定性 resolver；"人口增长率"与"人口"、"学校数量"与"每平方公里学校数"
  无法区分；
- signed change（变化率）数据走 sequential 色带出图（应 diverging），
  图例 `unit` 在生产链路从不填充（仅 LLM 手传 3D extrusion 的 height_unit）。

## 2. Current Architecture（勘察事实，只读 @ 5a4d4632）

| 资产 | 位置 | 状态 |
|---|---|---|
| DatasetProfile 统一契约（V2-V5，零扫描投影） | `app/lib/gis/dataset_profile.py` | 已合并，resolver 唯一出口 |
| DatasetProfileV3 深剖析（有界：50k 行/512 边/16 波段） | `app/lib/data/profile.py` | 已合并 |
| 语义角色 + 证据分级 | `app/lib/gis/semantic_profile.py` | 已合并（ADR-0092 C1/C2） |
| CRS 分类/资格/UTM 推荐 | `app/lib/gis/crs_safety.py` + `scientific_preconditions.py` | 已合并，勿重写 |
| unit hint（名称正则 → persons/count/currency/meters） | `app/lib/data/profile.py:unit_hint_for_field` | 已合并 |
| unit 报警检测（unit_ambiguous/inconsistent_unit） | `app/services/data_quality/semantic_checks.py` | 已合并 |
| 低置信语义绑定闸（FIELD_ROLE_AMBIGUOUS） | `app/services/gis_harness/data_qualification.py:282` | 已合并 |
| 符号化唯一裁决（method×k×palette×clip） | `app/lib/cartography/symbology.py` | 已合并（ADR-0152） |
| legend_spec 契约（unit 为 v2 冻结字段） | `app/lib/cartography/thematic_spec.py` | 已合并 |
| 模式匹配 + 诚实披露 | `app/lib/gis/pattern_projection.py` | 已合并 |
| advisory 工具（profile_dataset_semantics 等） | `app/tools/semantic_tools.py` | 已合并 |

**禁止重复清单**（勘察红线，见 §9）：

1. 不新建第二套 profile 契约；
2. 不重写 CRS 分类/资格谓词；
3. 不重写字段角色推理与置信分级；
4. 不新建 unit 检测器（DQH semantic_checks 已是报警生产者）；
5. 不在 cartography 重造 classify/jenks/palette；
6. 不做栅格全幅读取；
7. 不重做经度约定处理（longitude.py 在位）。

## 3. 方向偏移说明（任务书 §7）

任务书 S1（Canonical DataProfile vNext）预设的 versioned/bounded/
descriptor-first DataProfile **已被 master 完整实现**（DatasetProfile V2-V5 +
V3 深剖析 + V9 增量画像 + digest 入 ArtifactContract/指纹）。按约束不机械
重做。工作重心迁移到该方向下一个更基础的缺口：**语义消费断线** ——
新建 Measurement Semantics 契约（S1 的"字段级 measurement/unit"维度真缺口）
+ 双语字段 resolver（S4）+ 两条生产链真实消费接线（cartography、
qualification），S3 复用既有 CRS 闭环只补 unit 维度 fail-closed，S5 收敛为
scale/geometry display hints 纯函数 + 热路径接线。

## 4. Ownership / Authority

- **MapSpec** 仍是 desired cartographic state 唯一权威；本方向不改 MapSpec。
- `DatasetProfile` 仍是派生画像唯一契约；`DatasetMeasurementProfile` 是其
  **语义投影**（同 semantic_profile 之先例：派生层，非第二真相）。
- `resolve_symbology` 仍是 method×k×palette×clip 唯一裁决入口；measurement
  只作为**新增证据输入**参与裁决，不另设第二裁决点。
- 用户/LLM 显式指定的 method/palette/unit 恒为 user-wins；语义证据只在
  显式缺席时参与裁决，且所有推翻留痕 rejected[]/reasons。

## 5. Canonical Data Contracts（新增）

### 5.1 `app/lib/gis/measurement.py`（新）

```
MeasurementKind(str, Enum):
  COUNT / ABSOLUTE_QUANTITY / RATIO / RATE / DENSITY / PERCENTAGE /
  INDEX / CATEGORY / ORDINAL / SIGNED_CHANGE / UNCERTAINTY

UnitDimension(str, Enum):
  COUNT / POPULATION / LENGTH / AREA / CURRENCY / RATIO / PERCENT /
  INDEX / TEMP / DENSITY_COUNT_AREA / NONE

UnitEntry: canonical unit 注册（name → dimension + display + scale_to_si）
  meters / kilometers / square_meters / square_kilometers / hectares /
  persons / count / percent / fraction / cny / usd / index / celsius / years / none

FieldSemantics(BaseModel):        # 每字段一行（有界 ≤64 字段）
  field / measurement_kind / unit_dimension / unit /
  unit_confidence / kind_confidence / domain_hint(可选 min/max) /
  center_hint(可选) / evidence[≤6] / checks[≤4]   # check = code+detail

DatasetMeasurementProfile(BaseModel):  # versioned + serializable + bounded
  measurement_profile_version: Literal[1] = 1
  fields: List[FieldSemantics]
  to_dict() / from_dict()（roundtrip 兼容；未知版本拒收 → ValueError）
```

推导（纯函数 `derive_measurement_profile(profile, semantic_profile,
value_samples=None, unit_overrides=None)`，零 IO、值样本 ≤200/字段）：

- 角色 → kind：count_measure→COUNT、population→ABSOLUTE_QUANTITY(POPULATION)、
  area→ABSOLUTE_QUANTITY(AREA + 名称单位 km²/公顷/m²)、distance→ABSOLUTE_
  QUANTITY(LENGTH + m/km)、ratio→RATIO（样本全 [0,1]→fraction；任一 >1 且
  ≤100→PERCENTAGE）、category→CATEGORY、weight→ABSOLUTE_QUANTITY、
  continuous_measure→INDEX（名称温度/价格细分 dimension）；
- 名称叠加：密度（密度|每平方公里|per_km²|density）→DENSITY(denominator=AREA)；
  变化/增减/增长/change/growth + 样本跨 0 →SIGNED_CHANGE(center 0)；
  rate（率 + 时间字段在场 + 名称含 率/rate 且样本含负值或时间证据）→RATE；
- 单位证据合并序：user/LLM 显式 override > 值样本结构（[0,1]→fraction）>
  名称 unit_hint > dimension 缺省（unknown 不虚构）；
- danger 检查（fail-closed 证据，不静默）：
  - `UNIT_DIMENSION_MISMATCH`：role 期望维度 ≠ 单位维度（如分母角色绑到
    count 字段、面积角色绑 persons）；
  - `DEGREE_LIKE_METRIC`：LENGTH/AREA 维度 + 地理 CRS + 值结构呈度级
    （|max|≤360 且带小数）→ 疑似 degrees 冒充 meters（clarify 级）；
  - `RATE_MISSING_TEMPORAL`：RATE 但无时间字段证据；
  - `DENSITY_MISSING_DENOMINATOR`：名称密度但无面积/人口分母证据；
- nodata/NaN：值样本先过 finite 过滤（与 thematic_spec.is_finite_number 同规）。

### 5.2 `app/lib/gis/field_resolver.py`（新，S4）

```
FieldQuery(BaseModel): kind(期望 MeasurementKind) / role(期望 SemanticFieldRole)
  / subject(可选主题词) / denominator(期望分母维度) / temporal_required(bool)

parse_measure_phrase(phrase) -> FieldQuery   # 双语（zh/en）纯规则：
  人口→POPULATION；人口增长率/增长率/growth rate→RATE over POPULATION；
  学校数量/count of X→COUNT(subject)；每平方公里…/per square kilometer→
  DENSITY(denominator=AREA)；土地利用类型/land use type→CATEGORY；
  变化率/change rate→SIGNED_CHANGE…（命中不明 → kind=None 走 role 兜底）

FieldResolution(BaseModel):
  selected: List[FieldCandidate]（≤3，含 field/role/kind/confidence/evidence/
    unit/unit_dimension）
  ambiguity: List[str]                # 平级候选名
  needs_clarification: bool
  disclosures: List[str]              # temporal 缺席等诚实披露
  to_bounded_dict()

resolve_measure_field(phrase, profile, semantic_profile,
  measurement_profile=None, project_aliases=None) -> FieldResolution
```

打分（确定性）：kind/role 匹配（语义证据 ≥ metadata）+ 主题词∩字段名 +
别名表（project knowledge 有界注入）+ 分母维度匹配；平分多候选 →
ambiguity + needs_clarification（fail-closed：不替用户猜）。

### 5.3 `app/lib/gis/scale_semantics.py`（新，S5）

`display_hints(profile, *, feature_count=None, value_count=None) -> Dict`
（有界、纯函数）：geometry family、高密度点层聚类提示、面要素标注密度、
栅格分辨率-缩放兼容提示、multipart 提示（geometry_types 有 Multi* 即提示）。
消费点：create_thematic_map `layer_meta.display_hints`。

### 5.4 symbology 加性扩展（S2 接线，唯一裁决入口内）

- `SymbologyProfile.measurement_kind: Optional[str] = None`（additive）；
- 裁决序：显式 method 恒最高；**语义 CATEGORY**（显式语义证据）在无显式
  method 时走 categorical 模式（qualitative 色带族）——语义证据优先于分布
  猜测；**SIGNED_CHANGE** → decision.`diverging_center=0.0`（新可选字段）
  供 builder 走 divergent；其余 kind → sequential（重尾证据仍可推翻为
  head_tail，分布证据不因语义降权——分层职责：语义定"族"，分布定"法"）。

## 6. State Transitions / Integration Seams（消费链）

```
链 1（cartography 热路径，DoD-1）：
create_thematic_map(geojson, field, unit?=None, semantic_profile?=None)
  ├─ derive_measurement_profile(bounded values + semantic_profile)
  ├─ resolve_symbology(values, measurement_kind=…, requested…=显式)
  │    ├─ SIGNED_CHANGE & 未显式 method & 样本跨 0 → build_divergent_spec(center=0)
  │    └─ 其余 → build_graduated_spec（现有唯一分类路径不变）
  ├─ legend.unit ← 显式 unit > 派生 canonical display unit（生产链首次自动填充）
  └─ layer_meta.display_hints ← scale_semantics.display_hints(...)

链 2（qualification 热路径，DoD-1）：
qualify_data_role(req, …, semantic_profile)        # 签名不变，调用方零改动
  ├─ （现有）_semantic_role_guard：低置信绑定 → FIELD_ROLE_AMBIGUOUS
  └─ （新增）_unit_dimension_guard：由 semantic_profile+unit_hint 推导
       FieldSemantics → UNIT_DIMENSION_MISMATCH 等失败事实 → degraded
       （与角色闸同收敛规则：仅唯一失败时为 headline reason）

链 3（Pi advisory 面）：
semantic_tools.resolve_field_semantics（新 tier-2 纯工具）
  query + geojson_ref → FieldResolution（ambiguity → 澄清问题）
```

## 7. Failure Semantics / Idempotency / Security

- 推导是纯函数：同输入恒同输出；工具层 fail-soft（异常 → 记 checks 不出图
  中断），契约层 fail-closed（from_dict 未知版本拒收）。
- 语义推翻一律留痕（SymbologyDecision.reasons/rejected；qualification
  checks[facts]）；禁止静默改数沿 ADR-0152 纪律。
- user-wins：显式 unit/method/palette/roles 恒优先；user_roles 沿用既有
  USER_DECLARED 最高置信。
- 大数据安全：值样本 ≤200/字段、字段 ≤64、零全量扫描（create_thematic_map
  现有 values 提取本就是有限要素集；不新增任何读取路径）。

## 8. Observability / Backward Compat / Migration

- 全部新参数 Optional，缺省时行为与 master 逐字节一致（测试锁）；
- legend_spec 形状零变化（unit 本就是 v2 冻结字段，只是首次有生产者填充）；
- SymbologyDecision 新增 `diverging_center: Optional[float] = None`——
  pydantic 加性可选字段，旧消费方不受影响；
- qualification 新 reason codes：UNIT_DIMENSION_MISMATCH / DEGREE_LIKE_METRIC
  （warn 级事实）/ RATE_MISSING_TEMPORAL / DENSITY_MISSING_DENOMINATOR。
- 无迁移、无 feature flag（纯加性 + 缺省关闭）；回滚 = revert 单 PR。

## 9. Acceptance Matrix（→ 测试）

| # | 验收项 | 测试 |
|---|---|---|
| A1 | geographic CRS 度级值冒充 meters → DEGREE_LIKE_METIC 证据 | test_measurement_semantics |
| A2 | meters/degrees 维度冲突 fail-closed | 同上 |
| A3 | count vs density（名称+样本）区分 | 同上 |
| A4 | rate vs absolute（时间证据有无）区分 | 同上 |
| A5 | categorical vs quantitative（语义 CATEGORY → categorical 模式） | test_symbology_measurement |
| A6 | signed metric → diverging(center 0)（palette hint） | 同上 |
| A7 | nodata/NaN 不污染 kind/单位推导 | test_measurement_semantics |
| A8 | 字段别名歧义 → ambiguity + needs_clarification | test_field_resolver |
| A9 | temporal coverage mismatch（增长率无时间）→ 披露 | 同上 |
| A10 | geometry 不兼容提示（点层密度/面标注/栅格分辨率） | test_scale_semantics |
| A11 | 大合成数据集有界推导（50k 要素、≤200 采样、耗时/内存不炸） | 同上（synthetic） |
| A12 | DatasetMeasurementProfile 序列化/版本兼容 roundtrip | test_measurement_semantics |
| A13 | 缺省参数下 create_thematic_map 输出与 master 一致（回归锁） | test_thematic_measurement_wiring |
| A14 | qualification 单位维度闸 fail-closed + 收敛规则 | test_qualification_measurement_guard |

## 10. Out of Scope（本 PR 明确不做）

- #1418 遗留的 B 时相配对重投影（modelops，显式 blocked，另行 issue）；
- V9 incremental/unified 状态持久化落库（无消费方，另立方向）；
- 把 D1DatasetDescriptor 迁移到 measurement 契约（数据 fabric 面另行 PR）;
- gis_ontology 任务级本体的字段级化（1840 行已锁定，非本缺口）；
- 任何 MapSpec / 前端结构变化。
