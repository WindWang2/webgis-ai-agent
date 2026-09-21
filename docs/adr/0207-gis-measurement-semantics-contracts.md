# ADR-0207: GIS Measurement Semantics Contracts（量纲语义契约与消费接线）

日期：2026-09-20
状态：Accepted
关联：ADR-0092（semantic profile C1/C2）、ADR-0078/0152（legend_spec / symbology 唯一裁决）、ADR-0104（resolver 事实词表）

## Context

方向勘察（docs/dev/gis-semantic-foundation-design.md）证实：字段级语义
角色（14 角色 + 证据分级）、CRS 资格三层闭环、unit 名称 hint 与报警检测均
已合并；但 **measurement/unit 语义没有任何确定性契约对象**，cartography
与 qualification 只消费角色名与数值分布：

- count vs density、rate vs absolute、percent vs fraction 依赖 prompt 文本；
- signed change 数据走 sequential 色带（制图学上应为 diverging）；
- 生产链 legend_spec.unit 从不填充；
- 无用户短语 → 字段的确定性解析（role_index 只给每角色首字段）。

## Decision

1. **新建 `DatasetMeasurementProfile`（v1）** 作为 DatasetProfile 的语义
   投影（同 semantic_profile 先例：派生层，非第二数据真相）：字段 →
   MeasurementKind（11 种）+ UnitDimension + canonical unit + 证据 +
   fail-closed 检查码。纯函数推导，零扫描，样本 ≤200/字段。
2. **唯一裁决入口吸收语义证据**：`SymbologyProfile.measurement_kind` 加性
   可选；语义 CATEGORY → categorical 模式（qualitative 族）；SIGNED_CHANGE
   → decision.diverging_center=0；显式 method/palette 恒 user-wins。分层
   职责：**语义定族（qualitative/diverging/sequential），分布定法
   （quantiles/natural_breaks/head_tail）**。
3. **qualification 增单位维度闸**：在既有 `_semantic_role_guard` 旁推导
   FieldSemantics，维度错配（分母绑 count、度级值冒充米、率无时间证据）
   → 失败事实 + 专用 reason code，收敛规则与角色闸一致（仅唯一失败时为
   headline）。签名不变，调用方零改动。
4. **字段 resolver（S4）**：`parse_measure_phrase`（双语规则，纯函数）+
   `resolve_measure_field`（kind/role/subject/分母维度/别名打分）；平级
   多候选 → ambiguity + needs_clarification（不替用户猜）。以 advisory
   工具 `resolve_field_semantics` 暴露给 Pi 面。
5. **create_thematic_map 消费**（生产热路径）：可选 `unit`（user-wins）与
   `semantic_profile` 入参；自动派生 measurement → data_kind/diverging；
   legend.unit 首次自动填充；`layer_meta.display_hints` 接 scale 语义。

## Consequences

- unit/measurement 语义首次进入确定性代码路径（不再仅 prompt 文本）——
  DoD-2 达成；cartography 与 qualification 两条生产链真实消费统一语义
  投影——DoD-1 达成；危险歧义有 fail-closed/clarify/reproject 证据码
  ——DoD-3 达成。
- 旧调用缺省参数下行为与 master 逐字节一致；legend_spec 形状零变化
  （unit 本是 v2 冻结字段）；SymbologyDecision 仅加可选字段。
- 不迁移全部 metadata、不扩 Recipe、不动 MapSpec（红线）。

## Alternatives Rejected

- **在 gis_ontology 加字段级量纲**：该文件是任务级 TaskDescriptor 登记
  （1840 行已锁定），字段级语义归属 DatasetProfile 投影族，混入会打破
  任务/字段分层。
- **新建独立 unit 服务/第二裁决器**：违反"单一裁决入口"（ADR-0152）与
  "不新增平行体系"约束。
- **LLM 输出 measurement 判定**：关键 GIS 正确性必须由确定性代码守卫
  （总约束 §2）；LLM 只能经 user_roles/显式参数提供 user-wins 声明。
