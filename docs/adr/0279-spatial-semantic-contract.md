# ADR-0279：空间语义与量纲可信契约（SpatialSemanticContract v1）

状态：Proposed（随 zcode/goal-spatial-semantics-contract-v1 PR）
日期：2026-11-01 · 基线 master 6c5e71c2c

## 背景

GIS 工具链的底座构件已经很强：`crs_safety.classify_crs`（含实测尺度失真
门）、`measurement.py`（字段级量纲画像）、`MethodUseContract`（算法静态
目录）、`longitude.py`（跨 180° 纯函数）、`spatial_receipt`（inspect 期
CRS 收据）、`aggregate_with_denominator`（显式分母聚合）。但**执行期没有
统一的实例级契约**：working_crs / 距离换算链 / 分母口径 / 偏移帧归一 /
轴序嫌疑散落在各工具的 evidence dict，Agent 无法在执行前回答"这次计算
用什么 CRS、什么单位、依据是什么"。同时存在真实混淆通路：geocompute
载荷重建丢 crs member（G2）、voronoi/convex_hull 面积硬编码 /1e6（G3）、
3857 已投影输入直接度量（G4）、未声明 GCJ-02 静默错配（G5）。

## 决策

### D1 第四层契约：实例层（组合，不重叠）
`app/services/gis_harness/spatial_semantics/` 提供
`SpatialSemanticContract` v1 —— 一次数据 × 范围 × 操作的运行时绑定证据。
与既有三层的关系：MethodUseContract（算法静态要求）+ measurement（字段
画像）+ spatial_receipt（inspect 收据）均不改；本契约通过 import 组合它
们（classify_crs / CANONICAL_UNITS / crs_source 四态词表 / 偏移帧权威），
不复制任何实现。

### D2 未知 ≠ 猜测（fail-closed 缺省链）
crs_source 五态：declared / source / assumed / rfc7946_default / unknown。
未声明 + 度样坐标（|v|≤180/90 且含小数）→ rfc7946_default（RFC 7946 §4
规范缺省，附披露，**是规范不是猜测**）；未声明 + 非度样（投影样量级）→
blocked + declare_crs 修复建议，**绝不默认 4326**。CRS/单位/分母证据不足
的字段一律 unknown/""，消费方按词表判定，绝不静默填值。

### D3 判定与修复分离
validate 产五态判定（pass / pass_with_disclosure / degraded_with_
disclosure / requires_clarification / blocked / not_evaluated）+ SSC_*
封闭理由码（20 个，severity 四级）。修复建议分两级授权：
`explicit_required`（默认，须用户/上层授权）与 `deterministic_safe`
（双方显式声明的 EPSG↔EPSG 可逆变换）。**preflight 只判定不执行变换** ——
确定性安全变换的执行仍走既有执行层（transform_geojson 等），由调用方
显式授权后触发；错误时绝不执行潜在危险的转换。

### D4 工具适配不改签名、不改结果语义
四条生产路径（buffer_analysis / spatial_join / spatial_aggregate /
zonal_stats）在入口调用 `preflight_tool_input`：blocked/requires_
clarification → `ValueError("spatial_semantics:…")`（走既有错误折叠，
不改写成成功）；pass/degraded → `spatial_semantics` 有界证据块并入响应
（新增键，既有键原样）。spatial_aggregate 新增可选参数
`requested_density_unit`（加法式）：planner 把用户原始口径显式传入，与
denominator_kind 对账（人口分母 × "每平方公里"请求 → requires_
clarification，而非静默产出错误口径的 choropleth）。

### D5 descriptor 级开销与 LLM 安全
preflight 只评估描述符证据：坐标采样 ≤200 点、特征扫描 ≤2000 ——
50k feature 与 5k 的 preflight 同为有界（性能测试锁定）。契约与披露
（disclosure）绝不携带坐标采样/几何全量 —— 大数据经 ref: 的既有纪律
不变。

### D6 MapSpec 不建第二套
分析口径经既有通道流入 MapSpec：spatial_aggregate 证据块 density_unit
（个/km²）→ Agent 传 create_thematic_map(unit)（user-wins 通道）→
legend_spec["unit"]（既有开放键）→ 前端 graduated 图例显示（与
floating-legend/colorbar 同一消费口径）。MapSpec schema 零变更；create_
thematic_map 的量纲判定 crs 修正为数据真实声明（不再硬编码 4326）。

## 后果

- 正向：8 类典型错误（4326vs投影/mvs度/km²vscount/轴序/跨180°/多带/
  GCJ偏移帧/范围矛盾）在工具执行前有一致确定性判定；成都 500m、
  per_km² 密度、BD-09 join 三场景证据链完整可对账。
- 代价：preflight 增加每工具一次 O(bounded) 扫描（实测 50k <0.1s）；
  未声明投影样数据从"warn 后静默按 4326"变为 typed 拒绝（行为收紧，
  这是修复不是回归 —— 对应测试与文档同步）。
- 不做的事：不做多带分段投影（只披露 degraded + 测地建议）；不做未声明
  GCJ 自动归一（潜在危险转换，只警示）；不扩 MethodUseContract v1 schema；
  不建第二 MapSpec。
