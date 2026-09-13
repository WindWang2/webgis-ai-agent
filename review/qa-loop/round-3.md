# qc-loop Round 3（2026-09-13）

起点 tag `qc-loop-r2`

## P · Prioritize（top-3）

| id | 位置 | 类别 | 分数 |
|---|---|---|---|
| Q002 | pdf_renderer.py:81 | correctness P1 | 19.8（后被裁定误报，见下） |
| R2-6 | planner.py:698 | correctness P1 | 17.3 |
| Q023 | component_registry.py:1063 | error-handling P1 | 17.3 |

## 证据闸拦截：Q002 误报（本轮最重要的事件）

子代理报告"pdf_renderer 指北针/图例框缺 `transform=fig.transFigure`，在每张生产 PDF 上渲染为亚像素点"。Round 3-F 初版修复后，为修复写的回归测试失败，暴露 matplotlib 真实行为：**`Figure.add_artist` 对未显式设 transform 的 artist 会自动补 `transFigure`**（`is_transform_set()` 为 False 时；mpl 3.10.6 实证验证，Polygon/Rectangle 均确认）。即生产 PDF 的指北针/图例框**本来就正常渲染**——子代理把 Axes 域的语义套到了 Figure 域（Axes.add_artist 确实不补）。处置：

- 回退无谓改动、删除测试；pool Q002 标记 `false_positive`（附实证说明）。
- 教训入台账：渲染类断言必须经 matplotlib 实证，不能只读源码推行为。

## F · Fix（每条独立 commit）

1. `0468cc29` fix(qc-loop): bind candidates on explicit-recipe path so evidence chain emits (round 3, correctness)
   - planner.py:698：`candidates` 只在兜底重选分支绑定，显式 recipe_id 主干道（workflow_compiler/tools/plan_orchestrator 均如此调用）触发 UnboundLocalError 被 `except: pass` 吞掉 → 证据链断在 CANDIDATE_WORKFLOWS。修复：预置空表。+回归测试（bind turn → 断言两阶段发射 + selected 正确）。
2. `c88ffe92` fix(qc-loop): fail closed on component registry internal validation errors (round 3, error-handling)
   - component_registry.py:1063：`_validate_inner` 整体 `except: pass` 使 :1008 的 fail-closed 契约不可达。修复：内部异常转为 issue。+回归测试（monkeypatch renderer registry 抛错 → validate() 必须非空）。
3. `b5689596` fix(qc-loop): accept canonical list-shaped observation layers in critique checks (round 3, correctness)
   - map_critique.py:79：`_observed_layers` 把 canonical `layers: list[dict]` 当 dict 调 `.items()` → AttributeError 被 pipeline 吞掉 → blank_map/invalid_bounds/publication/label_collision/overlay 五项 V7 检查在真实观察下全数静默失效（现有测试全用 dict 形状，故未暴露）。修复：双形状兼容，list 按 id/runtime_store_id 双键索引（与 render_observation._observed_layers_by_id 同口径）。+回归测试 4 条（含 dict 形状兼容）。

无公共 API 语义变更，免 ADR。

## V · Verify

- ruff 全部通过；新回归测试 7 条全绿；受影响模块既有测试（map_critique_v7 等）全绿。
- map_critique 修复的爆炸半径复查：全仓无其他测试消费 collect_final_map_findings → 重启用检查不产生额外测试冲击。
- 本轮未触及渲染/导出（pdf_renderer 改动已回退）→ SKIP_BROWSER=1 门禁（round-3.gate.log）。

## L · Log · 指标

| 指标 | round-2 末（open） | round-3 变化 | 修后（open） |
|---|---|---|---|
| P0 | 0 | — | **0** |
| P1 | 14 | 修 3 + 误报剔除 1 | **13** |
| P2 | 142 | — | **142** |

Δ = (0−0)×3 + (14−13)×1 + (142−142)×0.3 = **1.0**。Δ ≥ 1.0，E2 未触发（连续 2 轮判定从本轮起算）。棘轮：无回退、无新增回归。

## 遗留（Round 4 输入）

open P1 13 条，按分数头部：R2-12 completion/pipeline.py:415 verdict dict-shape（16.7）、R2-3 recipes.py:233 资格门键名错配（16.1）、R2-10 completion/pipeline.py:121 截断先于判定（16.1）、R2-8 components.py:1234 rebind 旧 inline chart（13.4）、Q037 render_scene.py:210 舍入 parity（14.4，需含 golden 完整门禁轮）、R2-7 trace_store.py:327 逐出顺序（11.0）。
