# 05 — Review Findings（两轮 review 记录）

## Round 0 — 架构挑战（Subagent-A，实现前）

结论：ARCHITECTURE-NEEDS-REVISION → 全部 must-fix 采纳后冻结。
见 `01-architecture.md` §8 R1 修订记录（R1-F1…R1-F13），逐条落点：

- R1-F1 CRS 唯一事实源 → `qualification._adjudicate_crs_scale` +
  `test_geographic_crs_metric_buffer_not_silent`
- R1-F2 corpus 并入 wave 1 → commit de70c310
- R1-F3 去重 uncertainty_mapping → 未新建该任务
- R1-F4 categorical scope → `assumes_continuous_measure` +
  `interp.indicator_kriging` 候选 + 测试
- R1-F5 bridge 冲突语义 → `viz_bridge._RECONCILE_OVERRIDES` + 披露
- R1-F6 无第四张关键词表 → taxonomy lexical 投影 + `_ROUTING_BONUS`
- R1-F7 断言改动点 → 仅 corpus coverage 集合断言 + family_count==13
- R1-F8 lib→services 注入 → `_default_sources()` 延迟对接 + GraphSources
- R1-F9 proximity 长特异关键词 → methodology.py 族表
- R1-F10 工具注册 → `_TOOL_MODULES` 一行
- R1-F11 atlas 期望留空 → gis_ontology
- R1-F12 feedback 硬上限 → `MAX_RECORDS=10000` + truncation
- R1-F13 反循环 → 语料先冻结（e97aef86）+ oracle 锚定 + 平行不变性

## Round 1 — 最终正确性 review（Subagent-A）

结论：**APPROVE-WITH-FIXES**。R1-F1..F13 采纳全部验证落地；
gold 无漂移（仅 m.admin.parks_zh 输入事实补齐，期望未动）；
110 新测试 + 106 既有相邻测试全绿；validate_gis_library() 0 issues。

Must-fix（3，已全部修复）：
1. MAJOR viz_bridge 披露键错配（disclosures_text→disclosures）——修复 +
   扁平化为有界文本进 CompositionPlan.disclosures
2. MAJOR rank_family_methods selected 与 ranked 分歧（rejected 可当
   selected）——selected=ranked[0]（全拒时 None+abstain）
3. MAJOR invalidGeometryRatio 对无 geometry_kinds 方法不可达——独立
   裁决分支 + repair_geometry 预处理/披露

Should-fix（同步修复）：
4. MRR 分母（ambiguous 未命中记 0）
6. method_corpus trap 语义披露（direct 案例 trap=排序优劣提示）
7. precondition 路径 transform → reproject+披露（local_metric_crs_required）
8. ABSTAIN_AMBIGUOUS_TIE 落地（类目 tie + 跨族方法 tie）
9. 模板选择输出目标软罚落地（-0.3，替 pass 死分支）
10. 图缓存键补全（+capabilities/algorithms/artifacts/map_models/components
    失效键）；EDGE_RELATIONS 移除死 consumes_artifact；NODE_KINDS 构建
    期强制；docstring 修正
11. mixed_crs 注释/测试名更新为 R1-F1 语义
12. ADR-0120 §3.1 R1-F6 边界注记（_METHOD_KEYWORDS 只进排序面）
13. QUAL_METHOD_STATUSES 收编 unknown

INFO 命名漂移（实现自洽，文档已对齐）：ADR/CHANGELOG 列真实六工具。

## Round 2 — 性能/安全/可维护性 review（Subagent-B）

（待填）
