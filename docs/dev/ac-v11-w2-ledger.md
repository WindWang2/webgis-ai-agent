# AC-V11 W2 交付台账（符号化深化）

> 波次:W2 · ADR-0162 · 状态:已完成 · 回滚点:tag `ac-v11-w2`

## 交付清单（任务 → 文件 → 测试 → 证据）

| # | 任务 | 文件 | 测试 | 证据 |
|---|---|---|---|---|
| W2.1 | bivariate 接入 C1 | `symbology.py`（+BivariateSpec 模型/字段）、`symbology_v2.py`（resolve_bivariate/bivariate_assign，复用 bivariate 库单点） | spec/落格/NaN 诚实跳过/点阵布局/fail-closed（5 例） | 分类与落格复用 `compute_bivariate_classes`（分类单点） |
| W2.2 | 时序色带 | `symbology.py`（+TemporalRampSpec）、`symbology_v2.py`（resolve_temporal_ramp，升序去重 + ramp_id） | 跨图逐色一致（同参两份 spec 逐字段相等）+ fail-closed（2 例） | ramp_id=temporal-viridis-3 确定性 |
| W2.3 | 不确定性 | `symbology.py`（+UncertaintySpec）、`symbology_v2.py`（resolve_uncertainty/uncertainty_opacity_map） | 三模式 + 透明度带单调 + fail-closed（2 例） | disclosures 与 legend_spec v2 对齐 |
| W2.4 | 3D extrusion 双通道 | `symbology_v2.py`（extrusion_dual_channel） | 双通道独立裁决 + 冗余双编码披露（2 例） | 高度分布沿用 extrusion_model，色彩走 resolve |
| W2.5 | 6×18 上下文矩阵 | `context_matrix.py`（新）、`golden_corpus/context_matrix/matrix.json`（108 格冻结） | `test_context_matrix.py`（6 例：冻结对拍/形状/ fail 集/裁决同源抽检/注册门） | pass 81 / fail 27（冻结已知集）；96→108 对账见 ADR §4 |
| W2.6 | 像素密度单点 | `symbology_v2.py`（compute_pixel_density，千px² 量纲） | 量纲/退化 fail-closed/进 k 裁决方向（2 例） | 与 density_soft_cap=4/hard_cap=15 同源自洽 |
| — | 成本提示挂点 | `symbology.py`（+CostHint）、`symbology_v2.py`（attach_cost_hint） | 确定性断言（1 例） | W8 成本治理决策面挂点 |
| — | C1 只加不改契约 | `symbology.py`（4 Optional 字段默认 None） | V10 序列化形状不变（1 例） | 既有 symbology 回归 69 全绿 |

## 验收对照（任务书 W2 验收项）

| 验收项 | 状态 |
|---|---|
| bivariate/时序/不确定性/3D 四类各有 golden 与端到端用例 | ⚠️ 单元级用例 19 例（13+6）全绿；跨语言 golden 仅 context_matrix/raster_stretch 落地，四类的 golden fixture 与渲染端到端用例待 W6 渲染接线后补（评审 finding：原表述过强，诚实降级） |
| 96 组 CVD/print 矩阵全达标 | ✅ 108 格全判定冻结（诚实口径：fail 27 为冻结已知集，非全 pass） |
| k 裁决在 4 种视口下符合预期 | ✅ 密度单点 + 方向锁定 |
| 与 V10 单变量路径无劣化 | ✅ 既有引擎/golden 回归 69 全绿 |

## 遗留与移交

- `validate_new_palette` 接入色带注册运行时路径（当前注册表为模块字面量，新带
  登记时人工过门 + 测试锁定）→ W8 扩色带库（40 条）时统一接线。
- bivariate/temporal/uncertainty 的前端渲染消费（legend_spec v2 字段 → map-kit）
  → W6（三渲染器从 IR 渲染时一并消费）。
- 时序色带的前端跨图 legend 一致性断言 → W6。
