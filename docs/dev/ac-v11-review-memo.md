# AC-V11 复核纪要（§0.2）

> 基线:origin/master `17c77c73`（Merge PR #1266）。复核日期:2026-09-13。
> 结论:**十线全部在基线内,无重复开线;V11 按 §3 波次推进,W0 编号 0160 起、迁移 0057 起。**

## 1. 基线与编号契约

| 项 | 状态 |
|---|---|
| PR #1257–#1269 | ✅ 全部在 origin/master（逐一 `git log --grep` 验证） |
| ADR-0150~0159 | ✅ 已落盘 `docs/adr/`；V11 从 **ADR-0160** 起 |
| 迁移 head | ✅ `0056_cartography_quality_facts`；V11 从 **0057** 起 |
| 重复开线 | ✅ 无（`gh pr list --search adaptive-cartography` 无未合入 V11 线） |
| 环境 | worktree `../webgis-wt-ac-v11`，venv 安装依赖（官方 PyPI 源；清华镜像 403 已绕过）。**pyproject 无 setuptools packages 配置,`pip install -e .` 在 flat-layout 上失败——本仓依赖 pytest `pythonpath = .` 从仓根导入,不需要 editable 安装（与主仓一致）。** |

## 2. 首轮门禁基线（W8 ratchet 锚点）

`SKIP_BROWSER=1 bash scripts/quality_gate_local.sh`（等价冒烟;`--smoke` 形态在 W0.5 落地）:

```
cartography lane 独立覆盖率（与后端 75% 分开计）
  app/lib/cartography :  8801 stmts,  48.55%   ← 闸 scope
  合并参考            : 10190 stmts,  47.85%
  下限（ratchet 只升不降）: 50.0%
  判定: ❌ 低于下限 —— 拦截        ← 本机（Windows / py3.13）首轮基线
ratchet check：观测 0 条（聚合后），基线 0 条（active 0），豁免 0 条
✅ 无劣化：全部观测不劣于基线（或无 active 基线可比）。（新 worktree 事实库为空）
lane 测试本体：794 passed, 21 skipped, 0 failed（124.58s）
```

**重要**：覆盖闸脚本头注的「首轮实测参考值 50.1%（origin/master 1fd4b035）」在作者
环境成立；本机为 48.55%（差 2 个百分点，794 全过、无失败，非测试红）。差距归因于
环境差（pypdf 缺失等 21 skip + 平台差异）。处置：**基线锚点以本机 48.55% 如实记录**，
不调低 floor；W0 的 44 码契约矩阵 + 公共模块测试把覆盖抬回 ≥50（M1 门禁以 floor=50
跑绿为准）。

## 3. 十一个缺口的复核证据（以代码为准）

| # | 缺口 | 复核证据（worktree 实测） | 结论 |
|---|---|---|---|
| G1 | `select_composition_alternatives` 零生产调用 | 全仓仅 `app/lib/cartography/composition_selection.py` 定义 + `tests/cartography/test_composition_selection_v7.py` 引用 | ✅ 实锤,按 W0.3 建契约+fixture,W5 真接线 |
| G2 | 版面自愈只 planned 不执行 | `frontend/lib/layout/compose.ts:12`「planCompositionRepairs 产出在 live 路径只作 planned 记录」;`:140/148` `__fallback_north_arrow`/`__fallback_scale_bar`;`:172`「工件不得声称已执行的修复」 | ✅ 实锤,W5 执行化 |
| G3 | 三套整饰渲染并存 | `frontend/lib/export/export-chrome.ts`（canvas）/ `frontend/components/map/map-spec-chrome.tsx`（React DOM）/ `mapspec-to-svg`（SVG）——详 S1 债扫描 | W0.1 以 C2 IR 收敛 |
| G4 | label_engine/label_collision 双实现 | 重复符号实测 8 组:`_is_cjk(_char)`、`estimate_label_box`、`_keep_upright`、`_overlaps`、`_inside_viewport`、`_Grid(_Index)`、`_corner_box`、`_centered_box`;另有 `DECLUTTER_OFFSETS` 同表 | ✅ 实锤,W0.2 建 `label_typography.py` |
| G5 | 交互侧无网格碰撞 | S1 复核中（`label-layout.ts:18-23`） | W4 |
| G6 | L5 goal_satisfaction 恒 not_evaluated | 复核中 | W7 |
| G7 | ts_projection / golden_diff 无 app 侧消费 | `ts_projection` 在 app 的 grep 命中均为 `supports_projection` 字段（extensions/data_fabric）,与制图模块无关;模块仅 `tests/cartography/test_ts_projection_contract.py` 引用。`golden_diff` 仅 `scripts/golden_baseline.py` + `tests/quality/test_golden_image_diff.py` | ✅ 实锤,W0.3 接线 |
| G8 | 硬编码残留 | 实测:`cartography_service.py` `classify(method="quantiles", k=5)`、`templates.py` `palette: str = "YlOrRd"`（621/684 两处）、`thematic_spec.py:198` `k: int = 5`、`classify.py:144` 同款 | ✅ 实锤,W0.4 收进 `defaults.py` |
| G9 | 导出高分/PDF 栅格/无 50k 基线 | 复核中 | W6/W8 |
| G10 | 阈值三档不统一 | S1 复核中 | W3/W7 |
| G11 | 无跨会话学习 | 复核中 | W1/W8 |

## 4. 门禁与脚本现状

- `scripts/quality_gate_local.sh` 已具备可执行位（-rwxr-xr-x）,四步:覆盖率闸 → golden 像素校验 → ratchet → 趋势报告;支持 `SKIP_BROWSER=1`。
- **尚无 `--smoke` 模式**（W0.5 补:四脚本各加 `--smoke`,总入口加参数透传）。
- 门禁脚本:coverage_cartography_gate.py（--floor/--skip-tests/--json）、quality_ratchet_gate.py（baseline/check/waive）、quality_trend_report.py（--last/--check/--csv/--dashboard）、golden_baseline.py（generate/verify/status/show）。

## 5. 对 V10 产物的修正决定（§0.5「以代码为准」）

1. `pip install -e .` 不可用（flat-layout 多顶层包）——以 `pythonpath = .` 的既有机制为准,不修 pyproject（超出本线边界,登记为已知事项）。
2. `layout_description.py` 已存在（ADR-0157 P6 出版版面 IR,218 行,page/texts/scaleBar/extent 四段）——W0.1 的 C2 IR 是**升级**（加组件/约束/层级/样式 token/排版指令五段）,非重写;既有 `PUBLICATION_LAYOUT_VERSION` 语义保持兼容。
3. 任务书称 cartography 库「61 文件」,实测顶层 51 文件（含 composition_packs/ 子包）——以代码为准。

## 6. S1 债扫描（详见 `docs/dev/ac-v11-debt-scan.csv`）

要点（与任务书 G1–G11 的差异修正，§0.5 以代码为准）：

1. **G1 修正**：`composition_selection` 的 `validate_affinity_table` 有 1 处 lazy
   调用（`gis_harness/registry_validation.py:265`）；`select_composition_alternatives`
   本体仍零生产调用 —— 结论不变（W0.3 契约 + W5 接线）。
2. **阻塞码位置修正**：3 个 blocking 码（INVALID_SOURCE_REF / INVALID_STOPS_COUNT /
   NON_INCREASING_STOPS）不在 `semantic_checks.py`,在
   `app/services/mapspec/lifecycle_engine.py:44-48`（`BLOCKING_VALIDATION_CODES`）,
   由 `coordinator.py:123/137/141` 发射。W0.6 契约矩阵以此为准。
3. **恒 not_evaluated 码**：`VISUAL_OVERLAP`（semantic_checks.py:2366,硬编码）、
   `STYLE_EXPRESSION_SUPPORT`(:2072)；条件性 not_evaluated：RESULT_VISIBILITY /
   OPACITY_VALIDITY / CRS_EVIDENCE / BBOX_VALIDITY / RESULT_DATA_PRESENCE /
   GEOMETRY_LAYER_TYPE / RESULT_MAP_PROVENANCE。W0.6 逐码给化解方案。
4. **新增孤儿模块（W9 清理候选）**：`catalog_docs.py`、`design_system.py`、
   `export_component_catalog.py`、`isoline_model.py`、`layout_solver.py`
   （被 component_composer 复制绕开）、`style_tokens.py`。
5. **G3 增补第四套残壳**：`frontend/lib/map-exporter/` 仅剩一个测试文件
   （真引擎在 `map-kit/exporter.ts`）；`exportCommands.ts:18` 注释仍引用不存在的
   `lib/map-exporter/index.ts`。W6 收敛时一并清除。
6. **G5 证实**：`label-layout.ts:18-23` 明示避让委托 MapLibre 内置
   `text-allow-overlap:false`,与导出孪生 `label-solver.ts` 互不依赖（ADR-0126）。
7. **G8 范围圈定**：YlOrRd/quantiles/k=5 的字面量大量集中在**调色板注册表本身**
   （palettes.py/themes.py —— 这是权威定义,非兜底）;defaults.py 收敛对象是
   **业务代码里的兜底字面量**（cartography_service / templates / thematic_spec /
   classify / selfheal_actions 等），调色板注册表不进 defaults.py。
8. `label_plan.py` 阈值 2000/20000 两档硬编码在 `:92,478-480`（G10 证据）。
9. 小残渣：`layout-math.test.ts` 无对应实现文件（实际测 numeric-scale.ts）——W9 处理。
