# ADR-0205: Cartographic Grammar — 视觉变量通道规划与制图约束求解基座

- 状态：Accepted（本分支）
- 日期：2026-09-20
- 关联：ADR-0152（symbology 唯一裁决）、ADR-0073（分布驱动分类 +
  model library）、ADR-0154（标注计划契约）、ADR-0101（布局求解器）、
  ADR-0156（component composer）、ADR-0200（义务 rule graph 与 QA
  pack）、ADR-0118（user-wins 抑制）、ADR-0189（痛点出处：不推理视觉
  变量通道占用）、ADR-0199（multiscale LOD）
- 设计与勘察：`docs/dev/cartographic-grammar-foundation.md`

## 背景

ADR-0189 记录了痛点：通用 Agent 出图「不推理视觉变量（位置/大小/形状/
明度/色相）的通道占用」。仓库此后落成了完整的**裁决引擎族**
（resolve_symbology / choose_classification / label_plan /
layout_solver）与**事后校验族**（semantic_checks 的
`carto.visualvar.overload`、standards rule graph），但**事前规划层**
仍缺失：

1. 没有任何模块在成图前决定「哪个字段以哪个测量语义占用哪个视觉
   通道」——通道过载只能在成图后报警，不能在成图前避免；
2. `SymbologyProfile.data_kind`（sequential/diverging/qualitative/
   cyclic）决定 `resolve_symbology` 的整个色带族（`_FAMILY_ORDER`），
   但全仓没有调用点从数据证据推导它——除一处硬编码外全部默认
   `"sequential"`。signed change 数据因此被系统性画成单向色带，
   正负语义丢失（制图学错误，应 diverging 族）；
3. 表达选择（密集点→heatmap/grid/cluster、低 N→符号图、类别过多→
   收纳 Other）散落在 recipe 与工具硬编码中，无确定性、带 reason
   code 的裁决面；
4. 尺度语义（国家/省/市/街区）与表达选择、可见范围、聚合建议未成
   契约（现有 zoom 分档只覆盖标注密度与 LOD 系数）。

## 决策

D1 **Grammar 是规划层，不是第二裁决。** 新增三个纯函数模块：
`visual_variables.py`（测量语义×视觉变量适配矩阵 + `data_kind` 推导）、
`scale_rules.py`（语义尺度带×表达动作）、`grammar_solver.py`
（`GrammarRequest → GrammarDecision` 确定性求解）。Grammar 只产出
**输入与绑定**：data_kind 与 recommended 喂给 `resolve_symbology`
（最终 method/k/palette 仍由它唯一裁决）；label 委托 `label_plan`；
布局委托 `layout_solver`；必配组件复用 `required_components_for` 词表。

D2 **测量语义是有界词表，通道适配是冻结矩阵。**
`MEASUREMENT_KINDS = (nominal, ordinal, quantitative, ratio, rate,
signed_change, uncertainty, temporal)`；
`VISUAL_VARIABLES = (position, size, shape, hue, lightness, saturation,
opacity, texture, orientation)`，texture=unsupported、orientation=partial
按 runtime 现状诚实标注，grammar 永不分配 runtime 不支持的通道。
`CHANNEL_FIT[measurement][variable] ∈ {preferred, allowed, rejected}`
按 Bertin/Mackinlay 表达力原则冻结，每格带稳定 reason code。
到 standards `DATA_SEMANTICS`（ADR-0200 冻结词表）是**单向投影**
（grammar→standards），不反向依赖、不改其词表。

D3 **data_kind 必须有推导单点。** `derive_data_kind()`：
nominal→qualitative、signed_change→diverging、temporal×cyclic 词素→
cyclic（仓内无 cyclic 色带时 resolve_symbology 已有诚实降级披露）、
其余→sequential。`infer_measurement_kind()` 以确定性证据（负值占比、
有界值域+rate/ratio/pct/share 词素、样本基数、rank/level、时间词素）
推断测量语义，附 reasons/rejected 对账面。证据不足时 fallback
quantitative 并披露 `GRAMMAR.MEAS.INSUFFICIENT_EVIDENCE`，行为与
现状逐字节一致。

D4 **表达与配对规则输出 rejected + reason code。** 求解器裁决：
nominal→hue/shape（categorical legend）；signed→diverging；
polygon×rate→graduated+归一化披露、count_vs_rate 沿用 standards 词
族；密集点（密度阈值证据）→ heatmap/grid/cluster 候选排序；低 N→
符号图；类别数超过定性色带容量→收纳 top-(N-1)+Other 或建议替代通
道；qualitative↔colorbar、sequential↔categorical-legend 属非法配对
（`GRAMMAR.PAIR.*`，修复只路由建议，不 mutation）。全部落选者与
理由入 `GrammarDecision.rejected`（与 choose_classification 的
rejected 对账面同风格）。

D5 **尺度带与标注 zoom 带同界。** `SCALE_TIERS`（world/province/
city/street）边界与 `label_plan.DEFAULT_ZOOM_BANDS` 完全一致
（0-8/8-11/11-14/14-24），契约测试锁定相等——同一 zoom 心智模型覆
盖标注与表达两层；符号/字号系数引用 `scene_lod.LOD_BANDS` 的
size_ratio，不重抄数值。每带产出聚合建议（行政级）、点表达资格、
可见范围建议、泛化提示。

D6 **user-wins。** 调用方/用户显式 pin（通道/色带/表达）以最高优先
写入 `user_wins` 并记录 `GRAMMAR.PIN.*`；与矩阵冲突只披露不覆盖
（无障碍与可分辨硬约束仍由 resolve_symbology 兜底），与 ADR-0118
语义一致。

D7 **审计只读、无第二 verdict。** `GrammarDecision.audit()` 对成图
layer 做只读对账（色带族↔data_kind、legend↔colorbar 配对、通道数
阈值与 `semantic_checks` 的 `_VISUALVAR_*` 常量对齐——跨模块契约测
试锁定不漂移）。quality_loop 的 review 报告新增只读 `grammar_audit`
段：无 decision 时 `not_evaluated`，绝不新增阻断，也不替代
SEMANTIC_VALID（ADR-0200 D2 同纪律）。

D8 **接线最小化。** 本 PR 只改两个工具入口
（`create_thematic_map`、`apply_template` 的数值路径：method 未显式
时推导 data_kind，evidence 附 grammar 摘要）与 quality_loop 审计段。
`semantic_checks.py`/`mapspec_schema`/契约 JSON 零改动（ADR-0200 D7
竞争纪律延续）。推导异常时退回现状路径并披露（等效回滚开关）。

## 后果与边界

- signed/rate 数据从本 PR 起获得正确色带族（行为修正，sequential 数
  据路径 golden 不变）。
- GrammarDecision 为 additive 工件，随 classification_plan 同通道下
  发；旧消费方不读不受影响；前端零改动。
- 已知边界：测量语义推断基于字段名词素+值证据（与 label_plan/
  standards 的启发式同水平，诚实披露误报面）；表达选择在 recipe/
  harness 的全面接线（替换 recipe 内表达偏好）是后续工作，本 PR 只
  建立确定性契约与两处入口证明。
- texture/orientation 在 runtime 支持落地前不参与分配（矩阵标注
  unsupported/partial，测试锁定）。
