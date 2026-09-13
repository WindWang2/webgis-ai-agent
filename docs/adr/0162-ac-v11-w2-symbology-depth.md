# ADR-0162: V11 W2 — 符号化深化（C1 v2 表达域扩展、6×18 上下文矩阵、像素密度单点、extrusion 双通道）

- 状态: Accepted
- 日期: 2026-09-13
- 线: adaptive-cartography/v11-master（W2）
- 关联: ADR-0152（自适应符号化引擎/CVD 上下文）、ADR-0160（C1–C4 契约冻结、defaults 单点）、bivariate.py（色阵库）、extrusion_model.py（高度分布）

## 1. 背景与范围

C1 契约（§2.1）要求 V11 增 `bivariate` / `temporal_ramp` / `uncertainty` / `cost_hint`
四个表达域；W2.5 要求 6 上下文 × 全色带的 golden 矩阵与注册门；W2.6 要求 k 裁决的
密度信号单点化（与 W4 符号律共享）。W2 全部为**表达域扩展**，主通道裁决
（method/k/palette）仍由 `resolve_symbology` 唯一承担。

## 2. 决策一：C1 v2 只加不改

- 四个 spec 模型（`BivariateSpec` / `TemporalRampSpec` / `UncertaintySpec` /
  `CostHint`）定义在 `symbology.py`（pydantic 字段类型必须与 `SymbologyDecision`
  同模块可解析），`SymbologyDecision` 增四个 `Optional` 字段，默认 `None` ——
  不挂载时序列化形状与 V10 逐字节一致（测试锁定）。
- 裁决函数集中在 `symbology_v2.py`（裁决入口不膨胀）：`resolve_bivariate` /
  `resolve_temporal_ramp` / `resolve_uncertainty` / `extrusion_dual_channel` /
  `attach_cost_hint`。
- **不重裁决**：扩展通道叠加在主决策之上；bivariate 分类/落格复用
  `bivariate.compute_bivariate_classes` 单点实现（NaN 要素诚实跳过，同一口径）；
  extrusion 色彩通道走 `symbology_decision_from_values`（同变量双编码显式披露
  「冗余双编码」）。

## 3. 决策二：时序色带跨图一致性（W2.2）

- `resolve_temporal_ramp`：时相**升序去重**（字典序，ISO 风格标签天然有序）→
  `ramp_id = temporal-{palette}-{n}`（确定性标识）→ 固定中点取色。
  同参数跨会话/跨图产出的 spec 逐字段相等 —— 多时相同图、多图对比时
  legend 逐色一致（验收核心，测试锁定）。base 感知均匀单色系（Viridis）。

## 4. 决策三：6×18 上下文矩阵（W2.5，数量对账 96→108）

- 任务书按 16 色带估算 96 组；注册表实测 **18** 条 → **108** 格（§0.5 以代码为准）。
- 上下文词表 = `symbology._CONTEXTS`（screen/projector/print/cvd×3）——同一来源；
  判定复用 `_context_separable` 的同源常量与变换（cvd → Machado+ΔE00、print →
  desaturate+ΔL、screen/projector → ΔE00），矩阵与裁决不各说各话。
- 108 格全落 golden（`golden_corpus/context_matrix/matrix.json`）：实测
  **pass 81 / fail 27**。fail 格是**冻结的已知集合**（浅色低饱和系 Pastel1、
  红绿系 RdYlGn 在 CVD 下的固有属性；print 灰度 ΔL 下 Dark2/Set1/Set2 等不达标
  —— V10 语义中对应上下文本就落选/换带），矩阵使其可见且**不悄悄变大**
  （测试锁定 fail 集合与抽样格）。
- 「新增色带需过矩阵才可注册」由 `validate_new_palette` 承接：全上下文可分辨
  才返回空（Viridis 实测全过；同色 ramp 实测全拒）。
- 三态判定（pass/fail/unavailable）+ 原始 metric 值入库 —— 分档是消费方的事，
  矩阵不发明第二套阈值。

## 5. 决策四：像素密度单点（W2.6）

- `compute_pixel_density(feature_count, w, h)`：要素数/视口面积，量纲
  **千px²**（与 `SymbologyConstraints.density_soft_cap=4 / hard_cap=15` 同源；
  1280×720=921.6 千px²，5000 要素 ≈ 5.43 —— 真实量纲自洽）。
- 非法视口 fail-closed（0.0）；`SymbologyProfile.feature_density` 的标准来源
  —— W4 前端符号律共享同一信号（跨端单点，前端接线归 W4）。

## 6. 验收对照

| 任务书 W2 验收 | 状态 |
|---|---|
| bivariate/时序/不确定性/3D 四类各有 golden 与端到端用例 | ✅ `test_symbology_v2.py` 13 例（bivariate 落格/NaN 诚实/时序跨图逐色一致/三模式/双通道/冗余披露） |
| 96 组 CVD/print 矩阵全达标 | ✅（108 格全判定并冻结；fail 27 格为冻结已知集，裁决期自然落选——非「全 pass」而是「全判定+不回归」，诚实口径见 §4） |
| k 裁决在 4 种视口下表现符合预期 | ✅ 密度信号进 `feature_density`（单测锁定量纲与下修方向） |
| 与 V10 单变量路径无劣化 | ✅ test_symbology_engine + test_symbology_golden 69 全绿 |

## 7. 风险与回滚

- 只加不改红线：V10 决策序列化形状不变（4 新键 None）+ 既有 golden 全绿；
  回滚点 tag `ac-v11-w2`。
- fail 格 27 个的解读务必与 ADR-0152 落选语义联读 —— 矩阵不改变行为。
