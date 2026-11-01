# RISK_REGISTER — GOAL 04

| 风险 | 等级 | 处置 |
|---|---|---|
| shapely mapping 产出 tuple 坐标（内存 ref 不经 JSON） | 已发生→修复 | `_geojson_geometry` 规范化（M9 场景实测发现） |
| 播种 ingestion 的双层 inlineData 在 SVG 孪生画 0 要素 | 中（master 既有，已核对基线） | 不认领；测试内按生产惯例水合/解包并披露 |
| CAS 链基线误用 rev+1（superseded 静默） | 已发生→修复 | 链基线=前一步 mutation_revision + superseded 显式停链（测试锁定 committed） |
| filter 缺陷族 #1809/#1810/#1811 | 高（外部） | D5 绕开：取舍在工件期物化，不改运行时 filter |
| aggregate_grid 工件未实现 | 低 | 词表保留，seam 语义不变（ADR-0279 已知边界） |
| 共享边界保护是锚点级非弧级 | 中 | topology_risk 披露 + 覆盖面积不变量测试；弧级需 topology 框架（超范围） |
| 性能基准环境依赖 | 中 | 同机对照+复现脚本（MSP_PERF=1）+ 优化前后基线（44s→3.2s@10k） |
| Playwright 视觉回归未执行 | 中 | not_evaluated 披露；vitest+tsc+双孪生 parity 锁定 |
