# Visual Self-Healing 设计规范：缺陷 → 微变异映射矩阵（v1）

- 关联：ADR-0186（架构决策）、ADR-0158（visual judge）、docs/cartographic-closed-loop.md（证据阶梯）
- 代码：`app/services/mapspec/visual_healer.py`（编译器）+ `lifecycle_engine.py`（事务挂载）
- 状态：随 ADR-0186 评审

本文是 Visual Self-Healer 的**操作规范**：归一化映射矩阵、各 Resolver 的阶梯规则、
选色判据、收敛防护与披露码。实现与测试（`tests/unit/test_visual_self_healing.py`）
以本文为准。

---

## 1. 归一化映射矩阵（VisualJudgeReport → VisualCritiqueItem）

输入：`VisualJudgeReport`（`VisualCritique` 列表，维度级、无 layer_id）+ 已知图层 id 集。
输出：`VisualCritiqueItem` 列表（可定位缺陷）。**只做加法不臆测**：定位不到就空 layer_ids，
映射不了就丢弃并披露。

| 原维度（VISUAL_DIMENSIONS） | 证据关键词（evidence/suggestion，忽略大小写） | category | severity |
|---|---|---|---|
| readability | `overlap` / `collision` / `重叠` / `注记` / `label` | label_collision | 继承 |
| readability | `contrast` / `对比` / `看不清` / `辨` | contrast | 继承 |
| readability | `occlud` / `遮挡` / `覆盖` / `hidden` / `below` / `遮盖` | layer_order | 继承 |
| readability | `opacity` / `透明` / `transparen` / `淡` | opacity | 继承 |
| color_discriminability | （任意/缺省） | contrast | 继承 |
| composition_balance | `occlud`/`遮挡`/`覆盖`/`hidden`/`below` | layer_order | 继承 |
| composition_balance | 其它/缺省 | —（unmappable，丢弃） | — |
| information_density | `overlap`/`collision`/`重叠`/`label` | label_collision | 继承 |
| information_density | 其它/缺省 | —（unmappable，丢弃） | — |
| polish_completeness | `opacity`/`透明` | opacity | 继承 |
| polish_completeness | 其它/缺省 | —（unmappable，丢弃） | — |

补充规则：

- **关键词优先级**：同一 critique 命中多类关键词时按
  `layer_order > opacity > contrast > label_collision` 取第一命中（遮挡类措辞通常含
  "覆盖/叠"易与 overlap 混淆，故遮挡类最优先）。
- **图层定位**：对 spec 的每个已知 layer id，在 evidence+suggestion 文本中做子串匹配
  （含子层约定 `{id}__` / `{id}-` 前缀）；命中的 id 全部记入 layer_ids（保序）。
  提及两个 id 时，文本中**先出现**者视为靶图层，其余候选按 occluder 规则（§4）判定。
- **severity**：原样继承（error/warning/info）；planner 排序 error > warning > info。
- **min_contrast_ratio**：证据文本含 `AAA` → 7.0；含数字对比（如 `4.5`、`3:1`）→ 解析值；
  缺省 3.0（WCAG AA 大字号/图形件阈值，实测配色库中多色候选可达标；4.5 正文阈值按需显式
  提供，无候选达标时诚实跳过 `no_palette_meets_target`，绝不降低门限凑数）。
- **suggested_operation**：透传原 suggested_fix.operation（仅溯源；编译器按本表决策，
  不盲从裁判建议）。
- **结构化直通**：若上游 critique 携带 `layer_ids` 字段（Direction 01 预留），直接透传，
  跳过文本定位。

## 2. 策略规划（VisualHealStrategyPlanner.plan）

1. 丢弃白名单外 category 与空 layer_ids 缺陷 → skipped（`unmappable_dimension` /
   `unlocalized_defect`）。
2. 排序键：`(severity_rank 降序, category_impact 降序, defect_fingerprint 字典序)`：

   | severity | rank | category | impact |
   |---|---|---|---|
   | error | 3 | layer_order | 4 |
   | warning | 2 | contrast | 3 |
   | info | 1 | label_collision | 2 |
   | | | opacity | 1 |

3. 属性面冲突消解（同图层只留最高优先缺陷，其余 `superseded_by_higher_priority`）：

   | 属性面 | 冲突对 |
   |---|---|
   | paint 颜色/透明度 | contrast × opacity × label_collision（symbol 的 icon-opacity） |
   | layers 次序 | layer_order 独占（不与其它冲突，但同一 occluder 对只留一单） |

   面冲突优先级沿用 impact：layer_order > contrast > label_collision > opacity。
4. 逐缺陷调用 Resolver → `HealOp` 或 skipped（`unsafe_target` / `already_optimal` /
   `no_occluder_found` / `unsupported_layer_type`）。

## 3. Resolver 规则

### 3.1 LabelCollisionResolver（MUTATION_HEAL_LABEL_COLLISION）

靶：`type=="symbol"` 且有文本呈现（`layout.text-field` 存在或 `label` 配置）的图层；
非 symbol 靶 → `unsupported_layer_type` 跳过。

| 迭代（attempt，来自账本） | layout 补丁 |
|---|---|
| 0 | `text-allow-overlap: false`（强制）；`text-padding: min(现值×2, 8)`（缺省 2→4）；`text-ignore-placement: true`（图标不再被注记挤掉） |
| 1 | 在迭代 0 基础上：`text-size: max(现值×0.85, 8)`（阶梯步进缩小；表达式值 → `unsafe_target` 跳过） |

多层靶：每个 symbol 层各产出一个 op（同补丁内容）。

### 3.2 ContrastRemapper（MUTATION_HEAL_CONTRAST）

靶：paint 携带分类色的图层（step/interpolate 的 stops、match 的 cases，缺一即
`unsafe_target`——纯表达式的 `["interpolate", ...]` 数组形态按不可靠目标跳过；表达式值
（dict）→ `unsafe_target`）。

1. 现色提取：与 `_apply_palette_change` 相同的取色面（legend_spec.palette_colors /
   categories[].color / paint stops/cases），n = 现色数。
2. 画布：`type=="background"` 层 `background-color`，缺省 `#ffffff`；
   `relative_luminance < 0.18` → 暗底。
3. 候选排序：`(画布最差对比 ≥ min_contrast_ratio 优先, min_adjacent_delta_e 降序, 调色板名字典序)`，
   取第一名。
4. **严格更优才动**：候选 min-ΔE ≤ 当前 min-ΔE，或画布最差对比 < 当前 →
   `already_optimal` 跳过（诚实无操作）。
5. 应用（长度一致逐位替换）：`legend_spec.palette_colors`/`colors`、
   `categories[].color`、paint `step`（default+stops）、`interpolate`（stops）、
   `match`（cases）；分类断点/键**不动**。

### 3.3 LayerZOrderAdjuster（MUTATION_HEAL_LAYER_ORDER）

靶 `layer_ids[0]` 为需抬升的前景层；occluder 取 `occluder_layer_id`，缺省时在 layers
次序中取靶层**上方位置最高**的遮挡型层（`heatmap / raster / hillshade / fill-extrusion /
fill`；目标态 = 靶层高于全部遮挡型层）；一个都没有 → `already_optimal`。
显式指定的 occluder 不存在于 spec → `no_occluder_found`；显式 occluder 已在靶层之下
（现状已满足诉求）→ `already_optimal`。

- 应用：从 layers 数组取出靶层，插入到 occluder 之后（视觉上恰好压在 occluder 之上），
  其余层相对次序不变（最小重排）。
- 不变量：`background` 恒为数组首元素；靶层为 `background` → `unsafe_target`；
  靶层已高于全部遮挡型层 → `already_optimal`。

### 3.4 OpacityAdjuster（MUTATION_HEAL_OPACITY）

靶 `layer_ids[0]`；paint 键 = `_OPACITY_PAINT_KEYS[layer.type]`（无映射 →
`unsupported_layer_type`）。

| 现状 | 补丁 |
|---|---|
| opacity < 1.0 | `opacity = min(1.0, 现值 + 0.3)`（阶梯抬升） |
| opacity ≥ 1.0 且指定 occluder | occluder 的 opacity `= max(0.25, 现值×0.6)`（压低垫底者） |
| opacity ≥ 1.0 且无 occluder | `already_optimal` 跳过 |
| opacity 为表达式（dict/list） | `unsafe_target` 跳过 |

## 4. 收敛防护（引擎侧）

账本键 = 缺陷集指纹 `vheal-sha256:<sha256(规范化 defects)>`；MAX_VISUAL_HEAL_ITERATIONS = 2。

| 触发条件（检查顺序） | 行为 |
|---|---|
| 账本 attempts ≥ 2 且同指纹再次请求 | `SelfHealConvergenceExhausted`（第 3 次必拦） |
| quality_score 连续 2 次 ≤ 上次（no_improvement ≥ 2） | 提前抛出 |
| 本计划 ops_signature ∈ tried_signatures | 抛出（重复补丁防护） |
| on_exhausted="degrade" | 不抛，返回 `error_code=HEAL_CONVERGENCE_EXHAUSTED`，spec/revision 不动 |

账本只在提交成功后推进；`HEAL_PLAN_EMPTY`（计划无任何可应用 op）不提交、不推 revision、
不留账面，返回 `error_code=HEAL_PLAN_EMPTY` 与 skipped 披露。

## 5. 披露码（机器可读，全小写下划线）

| 码 | 含义 |
|---|---|
| `unmappable_dimension` | 维度+关键词映射不出 healable 类别 |
| `unlocalized_defect` | 未定位到任何靶图层 |
| `superseded_by_higher_priority` | 属性面冲突落选 |
| `unsupported_layer_type` | 靶层类型不在该 Resolver 支持面 |
| `unsafe_target` | 目标属性为表达式/不可靠形态 |
| `already_optimal` | 现状已满足判据（严格更优才动） |
| `no_occluder_found` | 未找到遮挡者 |
| `HEAL_PLAN_EMPTY` | 计划无可应用 op（引擎拒绝，未提交） |
| `HEAL_CONVERGENCE_EXHAUSTED` | 收敛保护触发（未提交） |
| `layer_locked` | 用户锁面拒绝（复用既有单码契约） |

## 6. 兼容性验收

- healed MapSpec 必须通过 `app/services/mapspec/coordinator.validate()`（无新增 blocking 码）；
  抽样通过 `MapSpecDocument.model_validate`（typed 权威 schema）。
- revision 每次提交严格 +1；两次 heal 指纹必不同（no-op 拒绝保证）；checkpoint 照常产出。
- 与 quality_loop 共存：heal 提交内部仍会跑 `review_and_repair_cartography`，测试断言以
  **落盘终态**为准（review 可能对 palette 做确定性微调，属预期复核行为）。
