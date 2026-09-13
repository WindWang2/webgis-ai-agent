# AC-09 勘察纪要：视觉裁判与自愈闭环断点盘点（P0）

基线：`origin/master@bf049ad0`（2026-09-13），worktree `webgis-wt-ac-09`。
本文是只读勘察产物；实现改动见 ADR-0158 与 PR 描述。

## 1. 评审覆盖矩阵（制图相关工具 × 是否进入 `evaluate_cartographic_session`）

评审触发唯一条件（legacy：`app/services/chat/tool_pipeline.py:284`；Pi：`app/agent_pi_bridge.py:917`）：
`outcome.status == "ok"` 且 `raw_result.mapspec_fingerprint` 非空。
`ToolDispatchService.dispatch` 的 authoring seam（`_author_display_result` /
`_author_raster_display_result`）会给矢量/热力栅格结果**补** fingerprint，因此矩阵如下：

| 工具 / 路径 | 结果形态 | dispatch authoring | 结果含 fingerprint | 进入评审 |
|---|---|---|---|---|
| GIS 分析工具（返回 geojson/data FC，~29+ 站点） | geojson → ref | ✅ `_author_display_result` | ✅（authoring 附加） | ✅ |
| `create_thematic_map`（choropleth/lisa） | geojson + legend_spec | ✅ | ✅ | ✅ |
| 热力图等 `heatmap_raster` 结果 | type=heatmap_raster | ✅ `_author_raster_display_result` | ✅ | ✅ |
| `webgis_layer_upsert` 等 `MAPSPEC_MUTATION_TOOLS`（6 个 webgis_*） | 自带 mapspec | （自身即变更） | ✅ | ✅ |
| `apply_template` composite 分支 | 走 `mapspec_store.layer_upsert` | — | ✅（结果显式携带） | ✅ |
| `apply_template` symbology single/categorical | `command: LAYER_STYLE_UPDATE` | — | ❌（`_track_legacy_template_in_mapspec` 已提交 lifecycle 但 fingerprint 未回填结果） | **✗ 断裂** |
| `apply_template` thematic heatmap 变体 | `command: add_native_heatmap` | — | ❌（#722 记录在案的 residual：spec 侧追踪缺失） | **✗ 断裂** |
| `apply_template` basemap / layout | `BASE_LAYER_CHANGE` / `export_map` | — | ❌ | ✗（basemap 有 `set_basemap` 追踪但同样不回填；layout/export 非图面内容变更，见 §6 触发白名单取舍） |
| `set_layer_style`（layer_manager:516）、`set_layer_visibility`（:460/:688）、`apply_layer_filter`（:645）、`reorder_layers`（:566）、`remove_layer`（:607） | 纯 HUD command，无 desired-state 追踪 | — | ❌ | **✗ 断裂** |
| `fly_to` / `zoom_to_bbox` / `set_map_view` / 注记 / 量测 / `query_features` / `export_map` | 相机/ chrome/导出 command | — | ❌ | ✗（设计如此：非图面内容变更，不触发评审——见 §6） |

结论：**断裂口集中在"前端 command 渲染路径"** —— 主成图路径（authoring seam）在 master 已被
#735/#789/#722 系列接通；残余断口是模板 symbology/heatmap 分支与 layer_manager 样式族。

## 2. L1–L5 实际取值分布

代码级事实（`pi_agent_harness.py:1747 _success_levels`）：

- L1 `execution_validity`：真实计算（tool error → fail；有证据 → pass）。
- L2 `map_state_validity`：= `cartography.runtime_status`（观测收敛驱动）。
- L3 `cartographic_structural_validity`：desired review passed/fail/not_evaluated。
- L4 `cartographic_quality`：trusted 复评三态。
- L5 `goal_satisfaction`：**恒 `"not_evaluated"`**（硬编码；注释："No structured
  visual/goal oracle is installed. Missing evidence stays explicit rather than
  inheriting L4 success."）。
- `CartographicReviewEvidence.visual_evidence` 字段已存在（`_MAX_VISUAL_EVIDENCE = 4`），
  当前唯一写入方是 `webgis_runtime_validate` 的 headless 代理
  (`evidence_class: heuristic`，record-only，ADR-0061：不得单独产出 L4/L5 PASS)。
- 视觉重叠检查在 `semantic_checks.py` 恒 `visual`/`not_evaluated`（无渲染证据时不猜）。

动态分布（204 条闭环语料）：本仓无现成"204 条语料"脚本入口；以覆盖矩阵 + 代码事实替代
定量统计——所有路径 L5 ≡ not_evaluated（100%），无需运行即可断言（恒等代码路径）。
L4 的 not_evaluated 占比由观测到达率决定（`stale_runtime_observation` /
`runtime_action_ack_pending` 是测试中常见终止原因，见 tests/cartography 用例断言）。

## 3. AUTO_SAFE 白名单与 MAX_RUNTIME_REPAIR_ITERATIONS 现状

- 期望态（lifecycle 内，`quality_loop.review_and_repair_cartography`）：
  `MAX_REPAIR_ITERATIONS = 2`；composer 白名单 operation =
  {`normalize_opacity`, `refresh_style_from_legend`, `set_layer_visibility`,
  `change_palette`, `set_map_legend_visibility`, `resolve_floating_layout`}；
  终止语义：`repair_exhausted`（repeated_failure/repeated_patch/max_iterations）、
  `superseded`、`failed_repairable`、`failed_unrepairable`。
- 运行态（`runtime_repair.plan_runtime_repairs`）：`MAX_RUNTIME_REPAIR_ITERATIONS = 2`；
  `AUTO_SAFE_RUNTIME_RULES` = {`RUNTIME_RESULT_VISIBILITY`, `RUNTIME_OPACITY_CONVERGENCE`,
  `RUNTIME_LEGEND_CONVERGENCE`, `RUNTIME_STYLE_CONVERGENCE`}。动作=把 desired 投影重放给
  前端（`cartographic_runtime_repair` action，patch {before, desired}）。**没有**分类方法/
  级数/图型/值域/标注/版面动作；**没有**效果判定——同 patch_fingerprint 复发即
  `repair_exhausted`（`repeated_runtime_repair`）。
- 锁 guard：被用户锁定图层的 patch 拒发（`locked_refused` 披露）。

## 4. visual_evaluator 接口契约与缺失接线点

- 实际路径：**`app/services/gis_harness/visual_evaluator.py`（153 行，任务书误写为
  app/lib/harness/）**。注意 §8 将 `app/services/gis_harness/**` 列为禁改（01/02 线），
  而可改区写的是 `app/lib/harness/visual_evaluator.py`（不存在）→ 处置：gis_harness 桩
  **不碰**；在可改区新建 `app/lib/harness/visual_evaluator.py` 生产实现（ADR-0158 记录）。
- 桩契约（保持兼容词汇）：env 注入 `GIS_VISUAL_EVALUATOR="module:callable"`；触发白名单
  `VISUAL_EVALUATION_TRIGGERS`；输出 `UnifiedFinding[]`（domain=visual）经白名单消毒，
  `degradation_only=True`；无配置/加载失败/抛错 → 空列表（诚实降级）。
- 缺失的接线点（谁该调它）：
  1. **输入**：headless validator（`app/services/runtime_validator.py`）已产出
     screenshot + canvas 统计到 `runtime_dir`；`ToolCallEvidence.runtime_evidence_path`
     已从 `result.runtime_dir` 接线 —— 截图定位链路现成，缺的是"从 evidence 拿截图 →
     调 judge → 写回 review"这一段。
  2. **调用方**：`PiAgentHarness.evaluate_with_evidence` /
     `_collect_cartographic_evidence`（trust 边界内）应把视觉裁判结论作为
     `evidence_class: visual` 的 check + `visual_evidence` 条目落账。
  3. **裁决消费**：`_success_levels()` L5 应从视觉证据 + L4 锚点推导（fail-closed）。
- 约束（docs/cartographic-closed-loop.md 硬约束）：`visual` 证据**不得单独**判 L4/L5
  PASS；必须声明 `evidence_class: visual` + source/confidence；deterministic > heuristic
  > visual 的证据层级。

## 5. 防重复复核（§0.2 纪要）

- PR 检索（visual judge/self heal/自愈/repair/L5/critique/verdict，limit 200）：无同线
  实现。最近邻：#1258（AC-03 adaptive symbology，OPEN）、#1173（V6 visual observation，
  已合并——即 headless 代理与 runtime repair 的来源）、#1189（Harness V7，已合并）。
- Issue 检索（自愈/修复/评审/L5/视觉/裁判，limit 300）：全部是缺陷报告/审计项，无 L5
  视觉裁判的既有实现或进行中工作；#656（fail-closed live Observed Map）已关闭且正是
  本线必须维持的语义。
- 分支检索 `judge|heal|repair|critique|verdict`：仅本线分支。
- `SymbologyDecision`（03 线 `rejected[]` 候选源）：master 不存在（PR #1258 未合）→
  按任务书 §8 契约：本线定义接口（`SelfHealCandidate` 协议 + mapspec/results 上的
  `symbology_decision.rejected[]` 读取约定），测试用 fixture 驱动；03 合入后无损对接。
- ADR watermark：`docs/adr/` 最高 0147；0148–0157 为兄弟线预留位；本线占用 **ADR-0158**。
- 三大断点确认：
  1. `tool_pipeline.py:284`（及 Pi 桥 :917）fingerprint 门 —— ✅ 属实（覆盖矩阵见 §1）。
  2. `_success_levels()` L5 恒 not_evaluated —— ✅ 属实。
  3. `visual_evaluator.py` 无生产调用方 —— ✅ 属实（仅 self-ref + 测试；注意实际路径
     在 gis_harness，见 §4）。

## 6. 评审触发的"地图变更"判定（P1 依据）

命令族三分类（由 §1 矩阵导出）：

1. **图面内容变更**（应触发评审）：`add_layer`、`add_native_heatmap`、
   `add_heatmap_raster`、`LAYER_STYLE_UPDATE`、`LAYER_VISIBILITY_UPDATE`、
   `APPLY_LAYER_FILTER`、`REORDER_LAYER`、`REMOVE_LAYER`。
2. **相机/chrome/导出**（不触发）：`fly_to`、`zoom_to_bbox`、`set_map_view`、
   `draw_measurement`、`add_marker`、`clear_annotations`、`query_features`、
   `export_map`、`FINALIZE_DISPLAY`、`switch_chart_type`。
3. **底图**（`BASE_LAYER_CHANGE`）：有独立 `set_basemap` 意图通道，不挂本次评审触发
   （与 04 线 lifecycle pre-commit 无冲突；保持保守，避免每轮底图切换空转评审）。

fail-closed 边界：command-only 结果（无 fingerprint）进入评审后，运行态收敛仍诚实
`not_evaluated`（`mapspec_fingerprint_missing`）——评审覆盖 ≠ 伪造收敛证据。Plan A
（symbology 分支回填 lifecycle fingerprint）让该路径恢复完整阶梯；Plan B（pipeline 触发
面加命令形态判定）让无追踪的 command 路径至少进入评审并落账。
