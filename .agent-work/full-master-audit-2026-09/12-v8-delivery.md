# 12 — Harness V8 交付记录（Phase D）

## 已交付（commit 88d4ecde，ADR-0136）

| 组件 | 文件 | 验收 |
|---|---|---|
| V8.1 Unified Capability Graph | gis_harness/capability_graph.py | 723→742 节点 / 1228 边（含 10 种子模型）；同指纹零重建结构测试；词表封闭（11 kinds / 13 relations）；validate 零 error |
| V8.2 Model 一等实体 | capabilities/modelops.py（8 caps）+ algorithms/modelops.py（8 algos）+ 图 model 节点 | models_for_capability('model_image_segmentation') → tiny-landcover-seg/promptable-seg；parity 全绿（capability→algorithm→tool 链闭合，B-10/#1212） |
| V8.3 Qualification Engine | gis_harness/qualification_v8.py | eligible/ineligible/degraded/unknown + {check,observed,expected,hint}；地理 CRS degraded 口径与 R1-C3/B-6 一致 |
| V8.4 ExecutionEstimate | qualification_v8.py | 逐维度 basis（measured/declared/estimated/unknown）+ confidence |
| V8.5 Reliability 扩展 | candidate_planner_v8.reliability_penalty_v8 | entity 键（tool:/model:/provider:）前缀匹配 ledger `||` 键；bounded min(1, fails/4) |
| V8.6 候选规划 | candidate_planner_v8.plan_candidates_v8 | 资格过滤+成本排序+可靠性罚分+确定性 tie-break；产出=计划证据（非第二执行真相） |
| 机器闸 | registry_validation.py 接 validate_graph | validate_gis_library 含图级 dangling/duplicate/词表检查 |
| 文档 | docs/adr/0136 | 决策 D1-D6 + 兼容性 + 已知限制 |

## §29 五案例覆盖映射

| Case | planner 级切片（tests/unit/gis_harness/test_capability_graph_v8.py） | 子系统验收（合并分支自带） |
|---|---|---|
| 1 成都学校分布 | test_case1_school_distribution_multi_candidates（kde_surface/kde_contours/heatmap_data 同列候选，无 query 硬编码） | cartography-v7 composition_selection（学校场景结构化推导，工作树测试） |
| 2 遥感建筑提取 | test_case2_building_extraction_model_chain + bands 不兼容排除（qualification reasons） | modelops-v3 vectorize/geo_output + scheduling 套件 |
| 3 大规模空间分析 | （resource envelope 投影经 estimate_for_node 的 declared/estimated basis） | geocompute-v8 acceptance（partition/robustness 190 tests） |
| 4 失败恢复 | test_case4_failure_feedback_penalizes_ranking（罚分改变排序） | recovery_ledger v6 + workflow v6 durable journal recovery |
| 5 用户继续交互 | （user-wins 不变量） | harness-v7 user-wins 验收 + 本会话 C-1/C-2/C-3 前端修复 + 315 前端测试 |

## 诚实披露

- V8 planner 当前未被 tool_surface_v3 热路径调用（零热路径成本）；生产接线
  （session_plan evidence 注入）是下一迭代 —— 图/资格/估计先以机器闸 +
  测试钉住语义。
- workflow/methodology/template/component 节点 kinds 在词表但构建段未投影
  （ADR-0136 已知限制；按需接入）。
- ExecutionEstimate 的 measured basis 待性能台账回填。
