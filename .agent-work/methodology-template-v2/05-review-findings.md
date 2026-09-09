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

（待填）

## Round 2 — 性能/安全/可维护性 review（Subagent-B）

（待填）
