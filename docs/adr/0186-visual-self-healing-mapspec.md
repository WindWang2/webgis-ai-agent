# ADR-0186: 视觉驱动的 MapSpec 闭环自愈与微变异重排引擎（visual self-healing v1）

- 状态：Proposed（随 `agent/02-visual-self-healing-mapspec` 分支评审）
- 日期：2026-09-14
- 关联：ADR-0158（visual judge selfheal closure，本决策是其"编译器"半区的落地）、ADR-0156（layout auto-compose self-healing）、ADR-0058（MapSpec mutation lifecycle）、ADR-0060（validity ceiling：visual 结论不入 validity 阶梯）、ADR-0167（selfheal blocking 开关）、ADR-0074（cartographic QA 与修复预算）

## 背景 / 上下文

自适应制图闭环目前存在一条**断裂的最后一公里**：视觉裁判（`app/lib/harness/visual_evaluator.py`，
ADR-0158）已经能产出结构化的 `VisualJudgeReport`（`VisualCritique` 按
readability / color_discriminability / composition_balance / information_density / polish_completeness
五维度给出 error/warning 批评），但下游没有任何机制把这些批评**编译**成 MapSpec 补丁：

- G1：`quality_loop.review_and_repair_cartography` 的 desired-state 修复只覆盖确定性规则的
  AUTO_SAFE 白名单（normalize_opacity / change_palette 等），其触发面是 `carto.*` 规则与
  `VISUAL_<DIM>` 检查行的 `suggested_fix`（且仅 color_discriminability+error 一条通路）。
  注记重合（label collision）、图面遮挡（z-order 倒置）、系统性低对比等视觉缺陷没有结构化编译路径。
- G2：`cartography_runtime.py` 的运行时自愈只有 palette 轮换一代际记忆
  （`_cartographic_repair_state.tried`），缺少"缺陷指纹 → 已试补丁 → 无改进即停"的收敛账本；
  同一视觉缺陷可能被反复翻译成同一无效补丁（震荡）。
- G3：`VisualCritique` 是维度级的（无 layer_id、无数值分），直接消费会把整图当靶子；
  需要**定位归一化**层把"图面级批评"落到"具体图层 × 具体属性"。

本决策引入 **Visual Self-Healer**（`app/services/mapspec/visual_healer.py`）：一个纯函数的
缺陷→微变异（micro-mutation）编译器，外加 `MapSpecLifecycleEngine.apply_visual_heal_patch`
事务入口。它不替代 quality_loop / runtime repair，而是其上游缺口的补全——三者共用
锁、CAS、checkpoint、blocking 校验、revision 单调这同一套事务机制。

## 决策

### D1 — 缺陷归一化：VisualCritiqueItem（维度级批评 → 可定位缺陷）

新增值对象 `VisualCritiqueItem`（dataclass，frozen）：

```python
@dataclass(frozen=True)
class VisualCritiqueItem:
    category: str                       # healable 白名单四类之一
    severity: str = "warning"           # error | warning | info
    layer_ids: Tuple[str, ...] = ()     # 靶图层（空 = 未定位）
    occluder_layer_id: Optional[str] = None   # 遮挡者/垫底层（LAYER_ORDER/OPACITY 用）
    dimension: str = ""                 # 原 VISUAL_DIMENSION，仅溯源
    evidence: str = ""                  # 原文证据，仅溯源
    min_contrast_ratio: Optional[float] = None  # contrast 缺陷的达标下限（缺省 3.0 = WCAG AA 大字号/图形件；4.5/7.0 显式提供）
    suggested_operation: Optional[str] = None   # 评判建议，诚实传递、不盲从
```

类别白名单（`HEALABLE_CATEGORIES`）：`label_collision | contrast | layer_order | opacity`。
归一化桥 `normalize_visual_report(report, *, known_layer_ids)`：

- dimension → category 的确定性映射（见 `docs/dev/visual-self-healing-rules.md` 映射矩阵）；
  readability 类批评按证据文本关键词细分（重叠→label_collision、对比→contrast、遮挡→layer_order、
  透明→opacity），**映射不出的条目诚实丢弃**并记入 plan.skipped（`unmappable_dimension`）。
- 定位：仅在 evidence/suggestion 文本**命中已知 layer id** 时才填 layer_ids；未命中 → 空定位，
  planner 按 `unlocalized_defect` 诚实跳过。禁止"猜整图"。

### D2 — 原子操作集（微变异词汇表）

四个 heal 操作常量（与既有 Intent 词汇并存，**不**混入 `SELFHEAL_ACTIONS` 注册表——那是
确定性修复的面；本表是视觉缺陷编译面）：

| 常量 | 目标属性面 | 解析器 |
|---|---|---|
| `MUTATION_HEAL_LABEL_COLLISION` | symbol 层 layout（text-allow-overlap / text-padding / text-ignore-placement / text-size） | `LabelCollisionResolver` |
| `MUTATION_HEAL_CONTRAST` | paint 分类色 + legend_spec 色带（长度匹配才动，镜像 `_apply_palette_change` 语义） | `ContrastRemapper` |
| `MUTATION_HEAL_LAYER_ORDER` | layers 数组次序（单次"提升到遮挡者正上方"的最小重排） | `LayerZOrderAdjuster` |
| `MUTATION_HEAL_OPACITY` | paint `*-opacity`（复用引擎 `_OPACITY_PAINT_KEYS` 映射） | `OpacityAdjuster` |

不变量：所有操作**只触呈现面**（layout/paint 呈现键/legend 色带/layers 次序），绝不触碰
sources、数据绑定、分类断点（legend_spec 的 min/max/breaks/categories[].key）——与
`_presentation_copy` 的呈现语义一致；分类语义漂移属于 semantic risk，不在本引擎自动面。

### D3 — 策略规划：VisualHealStrategyPlanner

`plan(mapspec, defects) -> VisualHealPlan`（纯函数）：

1. 过滤白名单外类别与未定位缺陷（全部进 skipped，附机器可读 reason）。
2. 排序：severity（error > warning > info）优先，其次影响域权重
   （layer_order 4 > contrast 3 > label_collision 2 > opacity 1——遮挡破坏的是整图可读性，
   故最优先），再以 defect 指纹字典序稳定化（确定性、可重放）。
3. 冲突消解：同图层同属性面（contrast 与 opacity 同触 paint、label_collision 与 contrast
   同触 symbol 呈现）只保留优先级最高者，落选者记 skipped（`superseded_by_higher_priority`）。
4. 每个缺陷派发给对应 Resolver 产出 `HealOp`（op / layer_ids / rationale / 关联缺陷指纹 /
   op 专属载荷）；Resolver 无法安全处理（如 paint 颜色为表达式、目标已在正确层位）→ 诚实
   skipped（`unsafe_target` / `already_optimal` / `no_occluder_found`）。

`VisualHealPlan.ops_signature` = ops 的规范化哈希；空 ops 的计划会被引擎拒绝
（`HEAL_PLAN_EMPTY`），**不提交、不推 revision、不造 checkpoint**——诚实无操作优先于形式提交。

### D4 — 收敛防护：≤2 次迭代 + SelfHealConvergenceExhausted

引擎实例持有收敛账本 `self._visual_heal_ledger`（键 = 缺陷集指纹
`vheal-sha256:<...>`，值 = {attempts, last_score, no_improvement, tried_signatures}，
bounded 128 FIFO 淘汰）。`apply_visual_heal_patch(...)` 在构建意图前检查：

- **迭代上限**：同一缺陷指纹已提交 `attempts >= MAX_VISUAL_HEAL_ITERATIONS (=2)` 仍再次请求
  → 抛 `SelfHealConvergenceExhausted`。即同一缺陷至多自动自愈 2 次，第 3 次请求必然触发保护。
- **评分不提升**：调用方提供 `quality_score`（视觉裁判的最新质量分）时，与账本 last_score
  比较，连续 2 次不提升（`no_improvement >= 2`）→ 提前抛出。
- **重复补丁**：新计划的 ops_signature 已在该指纹的 tried_signatures 中 → 抛出
  （同补丁重放必无新信息，镜像 quality_loop 的 `repeated_patch` 终止语义）。
- **优雅降级**：`on_exhausted="raise"|"degrade"`（缺省 raise）。degrade 模式返回
  `MapSpecResult(error_code="HEAL_CONVERGENCE_EXHAUSTED", is_error=True)`，MapSpec 与
  revision 保持不动——保护机制绝不以死循环或半提交为代价。
- 账本仅在**提交成功**后推进（is_error=False 且非 duplicate）；失败回滚不留账面。

### D5 — 事务挂载：ApplyVisualHealPatchIntent

新意图 `ApplyVisualHealPatchIntent(defects, quality_score)` 加入 `MutationIntent` Union，
分发分支在**锁内**用权威 `loaded` spec 重新规划并 `apply_heal_plan` COW 应用（candidate 与
其它意图同构），从而白嫖全套既有机制：mutation_id 幂等去重、CAS/superseded、
`guard_intent_locks`（op 的全部 layer_ids 注册进 `intent_lock_targets`，用户锁面照常
`layer_locked` 拒绝）、`review_and_repair_cartography` 复核、`validate_mapspec` blocking-diff、
auto-checkpoint、revision +1 单调、失败 rollback。注册面共五处：Union、锁目标、
`_PRESENTATION_INTENT_TYPES`、`_layers_touching`、分发 elif。

对外入口：`engine.apply_visual_heal_patch(session_id, defects_or_report, *, origin="system",
quality_score=None, on_exhausted="raise", mutation_id=None, expected_revision=None)`。
origin 缺省 `system`（区别于 agent 的用户意图），review/审计与既有 provenance 同路。

### D6 — ContrastRemapper 的选色判据（WCAG + 感知区分度）

画布色 = layers 中 `type=="background"` 层的 `background-color`（缺省 `#ffffff`）；
`relative_luminance(canvas) < 0.18` 判定暗底。候选集 = `COLOR_PALETTES` 全表按需采样 n 色
（n = 现有分类色数），排序键：(画布最差对比 ≥ min_contrast_ratio 优先, `min_adjacent_delta_e` 降序,
调色板名字典序)。门限缺省 3.0（WCAG AA 大字号/图形件，配色库多色候选实测可达标）；
4.5/7.0 按需经 `min_contrast_ratio` 显式提供，无候选达标时诚实跳过，不降门限凑数。
**只有当候选的感知诊断严格优于当前色**（min-ΔE 更大，且画布最差对比比不降低）才产出补丁；
否则 `already_optimal` 诚实跳过。应用镜像 `_apply_palette_change`：legend `palette_colors`、
`categories[].color`、paint step/interpolate/match 按**长度一致**逐位替换；长度不匹配不动。

### D7 — 诚实边界（非目标与限制）

- 账本为**进程内**状态（与 mutation dedup 的 map_state 侧写不同）：多 pod 部署下收敛防护
  按 pod 独立计数；接受理由——同一缺陷指纹的重复请求通常落在同一评估回路内，且
  quality_loop/runtime repair 各自仍有独立预算兜底。
- 归一化桥的 layer 定位依赖证据文本提及 layer id；Direction 01 后续若在 VisualCritique 上
  增配结构化 layer_id 字段，桥直接透传（映射矩阵预留）。
- 视觉结论依旧不进 validity 阶梯（ADR-0060 不变）：heal 提交的合法性由
  validate_mapspec / review 背书，不由"视觉上变好了"背书；quality_score 仅作收敛信号，
  不作提交依据。
- record-only 传统延续：本引擎是显式调用的事务 API，不在任何评审路径里隐式触发；
  blocking 化沿用 ADR-0158/0167 的开关节奏，不在本决策内。

## 备选方案

- **复用 SELFHEAL_ACTIONS 注册表**（把四操作建成 4 个 action spec）：拒绝——注册表按
  triggers×expected_effect 面向确定性检查行设计，而视觉缺陷需要逐缺陷携带 layer 载荷与
  迭代阶梯，硬塞会污染其 select_actions 语义；两者保持"确定性面 / 视觉面"分治。
- **在 cartography_runtime 运行时侧实现**：拒绝——运行时自愈是"观测代际"驱动且锁外多段
  提交，结构化 MapSpec 微变异需要单事务原子性；desired-state 侧（lifecycle engine）才是
  正确挂载点（与任务书 §3 一致）。
- **planner 在锁外规划、锁内只应用**：拒绝——TOCTOU：锁窗口内他方提交会使计划命中过期
  spec；锁内重规划（D5）成本可忽略（纯函数、层数有限）。

## 切换条件 / 验收

- `tests/unit/test_visual_self_healing.py` 全绿：15 组典型缺陷→补丁精确命中靶图层×属性；
  收敛防护（raise + degrade 双路径）；healed spec 通过 `validate()` 与
  `MapSpecDocument.model_validate`；混合 mutation 序列 revision 严格单调、指纹逐次变化。
- `ruff check` 对新文件零告警。
