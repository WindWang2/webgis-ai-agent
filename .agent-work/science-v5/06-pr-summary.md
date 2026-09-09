# Science V5 — PR Summary（最终态）

## 验证结果（rebase 后复测，origin/master=8a33e3a5 无新提交，rebase 为 no-op）

- **合并车道**（unit/lib + unit/gis + science_oracles + quality + integration）：
  2783 passed；2 失败为**仓库已记录的负载敏感 flake**
  （`test_quality_scenario_corpus::test_slice_geometry_states_green[*]`——
  V4 PR 同名记录；隔离运行恒绿 12/12；本分支对该文件及其被测面零改动）
- **分车道精确数**：unit/lib + oracle = 2071 passed；quality + unit/gis +
  integration = 714 passed；oracle 全量 1116（含 science_v5 域 25 case）；
  work-count（perf 车道）4 passed；ruff 全绿（生成器 32 项为 master 既有）
- registry validate 0 issues；参数 parity 门绿；contract drift BLOCKER/MAJOR=0；
  生成物账本（staleness ledger）一致

## Two-round review 修复总览

- **Round 0（架构挑战，实现前）**：14 项（2 CRITICAL + 5 MAJOR + 6 MINOR + 1 NIT）
  全部回写架构后实施；R1 时全部验证关闭。
- **Round 1（正确性）**：BLOCKER 1（st_cross_validate 自条件泄漏——契约改
  3-arg + 仅训练子集条件 + spy 对抗测试）+ MAJOR 2（oracle 哑弹重绑生产
  目标）+ MAJOR 1（格网守卫）+ 6 MINOR 全部修复。
- **Round 2（性能/安全/并发/兼容）**：MAJOR 3（拓扑校验取消点+链头种子化/
  ST 执行计划单位错配/phenology 内存披露）+ 6 MINOR（含 SGS chunk<k 索引
  与钳制、批量掩膜向量化、ST chunk 局部邻域、工具零拷贝预检、cv 契约
  文档、判别性测试）全部修复。

## 关键数字

- 新增生产代码：cv.py（330 行）/ uncertainty.py（~300）/ temporal_cube.py
  （~250）/ phenology.py（~450）/ kriging_simulation batched（~300）/
  terrain 多级+拓扑（~330）/ backend_selection plan_execution（~100）
- 新增测试：~130 个（单元 118 + oracle 25 + benchmark 4）
- 修复本域 P1：2 处（SGS 序贯区制 sim-sim 非一致协方差；条件树位置索引
  错位）——均在 ≤1 chunk 区制外（既有 oracle/测试锚定区制行为不变）
