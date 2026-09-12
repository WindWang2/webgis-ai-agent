# AC-02 勘察报告 + 复核纪要（ADR-0151 / P0）

分支 `adaptive-cartography/02-recipe-adjudication` · 基线 `09d839d3` · 2026-09-13

## 0. 复核纪要（防重复）

| 检查项 | 结果 | 结论 |
|---|---|---|
| PR 搜索（recipe/eligibility/fallback/降级/planner，200 条） | #704/#904/#1085/#1144/#1152/#1173 建立了 recipe registry、模板库、unified orchestration、Conformance Foundation；**无任何 PR 实现**多维度 eligibility 或声明式 fallback 链 | 不重复，本线是新能力层 |
| Issue 搜索（recipe/配方/降级/ineligible/方案B，300 条） | #1067 修 fallback_decisions 证据保真（恒空）；#873 h3 静默降级；均为点状修复 | 无重复实现 |
| 分支搜索 `recipe\|planner\|fallback\|adjudic` | 仅本线分支 | 无并行线冲突 |
| `grep check_eligibility\|RECIPE_INELIGIBLE\|visual_heatmap\|aggregate_grid app/services/gis_harness/` | 12 处：recipes.py（定义）、planner.py（两条硬编码降级 + RECIPE_INELIGIBLE 兜底）、tools.py（NEEDS_ADMIN_UNITS bind 期消费）等 | 现状与任务书 §1 描述一致 |
| ADR-0119 / 0121 / 0129 阅读与冲突确认 | 0119=观测/修复回路；0121=语义检索/上下文；0129=方法知识图+统一资格报告。本线 recipe 级 eligibility 扩展与 0129 的 qualification 哲学同向（词表复用、单一事实源），与三者无重叠冲突 | 可开工 |
| ADR 编号 watermark | docs/adr 最高 0147；open PR 无 0148-0151 占用声明；任务书预分配 **ADR-0151** | 采用 0151（0148-0150 为并行线保留，已在 ADR 注明） |
| 现状确认 | SEED 17 + packs 147 = 164（P0 实测见 §1）；`check_eligibility` 三类检查（几何/min_points/requires_fields）；硬编码降级仅 `visual_heatmap`/`density_overview` 与 `aggregate_grid` 两条 | 与任务书一致 |

**环境偏差记录**：任务书 §0.1 的 `pip install -e .` 在本仓不可用（pyproject
setuptools flat-layout 自动发现遇多顶层包报错，属存量问题，非本线引入）。
`pytest.ini` 已有 `pythonpath = .`，测试从 rootdir 解析 `app` 包，无需 editable
安装；本线与相邻 worktree（ac-03/ac-04）一致仅安装 `requirements-dev.txt`。

## 1. Recipe 矩阵（P0.1/P0.2/P0.3）

数据来源：`python scripts/recipe_eligibility_audit.py --csv docs/dev/ac-02-recipe-matrix.csv`

```
total_recipes=164          （seed 17 + packs 147，24 模块）
with_fallback_decls=100    coverage=61.0%
  seed: 11/17   pack: 89/147
pack_modules=accessibility(6),change_detection(6),density(6),disaster(5),
distribution(12),environment(5),equity(5),exposure(4),hydrology(6),
interpolation(6),natural_resources(6),network(7),point_pattern(5),
public_health(4),remote_sensing(9),risk(6),sar(5),site_selection(6),
statistics(10),suitability(5),temporal(6),terrain(8),transport(4),urban(5)
```

**与任务书预估的偏差**：pack fallback 声明覆盖率实测 61%（预估 ~0）。
但现有声明全部是「单元素、单跳」`RecipeFallback{use: point_overlay}`——
只记录降级意向，无链式求解、无资格复检、无 `auto_generated` 标记、
无未命中时的次选。缺口重述为：①64 条零声明；②100 条声明均非链且
`check_eligibility` 单条 `break` 匹配（recipes.py:241-250）从不评估次选；
③要素级声明与 recipe 级失格（element="recipe"）之间无桥接。

## 2. 降级失效最小复现集（P0.4）

探针：`.agent-work/ac-02/p0_downgrade_probe.py`（10 样本，plan→finalize 全链）。

| # | 场景 | 当前行为 | 判定 |
|---|---|---|---|
| 01 | heatmap 5点 | visual_heatmap 禁用，点层升 primary | ✓ 硬编码分支生效 |
| 02 | grid 10点 | aggregate_grid 禁用，点层升 primary | ✓ 硬编码分支生效 |
| 03 | grid+面数据 | recipe 失格 → RECIPE_INELIGIBLE；点层因硬编码分支幸存 | △ 靠巧合而非链 |
| 04 | hotspot 6点 | heatmap 禁用，点层升 primary | ✓ |
| 05 | **extrusion_3d + 点数据** | recipe 失格但 **extrusion_3d 主层仍 enabled**（disabled_elements={"recipe"} 与图层 cartography 名不匹配 → L1042 `layer.cartography in disabled_elements` 恒 False）→ 无点图兜底（仍有存活层） | ✗ **自相矛盾计划** |
| 06 | **moran + 点数据** | 同 05：choropleth/hotspot 层全存活，RECIPE_INELIGIBLE 仅留痕 | ✗ **自相矛盾计划** |
| 07-10 | 几何可容场景 | 正常（对照组） | ✓ |

结论：recipe 级失格的降级**只在「恰好全部图层被禁」时**才追加点图兜底
（planner.py:1053）；常规情况产出「失格 recipe + 存活图层」的矛盾计划。
方案 B/C（换 recipe）完全不存在。05/06 是 P1-P3 的固定回归锚。

## 3. 结论

P0→P1 的设计约束（全部来自实测）：

1. **桥接 recipe 级失格与图层裁决**：element="recipe" 的失格必须禁用
   主表达并触发链式降级（修 05/06 矛盾计划）。
2. **链式求解替代单条 break**：`RecipeFallback` 声明按序评估 + 新
   `fallback_links`（recipe 级链）深度优先、带环守卫与深度上限。
3. **旧三维保留 fast-fail**，6 个新维度作为正式裁决层（additive 字段，
   不改写既有 164 条的语义）。
4. **每步落结构化证据**：`FallbackAttempt`（含落选者与原因），
   `FallbackDecision` 扩展 `attempts`/`auto_generated`。

（P0 完）
