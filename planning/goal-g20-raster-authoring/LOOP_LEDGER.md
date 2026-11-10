# LOOP_LEDGER — G20 迭代账本

上下文恢复：先读本文件 + `git diff --stat` + 实际源码，不从零重做。

| 轮 | 阶段 | 完成内容 | 验证 | 提交 |
|---|---|---|---|---|
| 0 | P0 | 核对 remote/nameWithOwner/默认分支；fetch --prune；解析 `origin/master=816705218a5aa6202bf15d745cb31cddb3437f44`；复核开放/合并 PR 与 review/inline comments；复核开放 issues；建立 BASELINE/DEDUP/PLAN/INTERFACES/TEST_MATRIX；创建独立 worktree+branch | 命令输出见 BASELINE.md | 未提交（文档随首个代码提交一起） |
| 1 | P1 | 待开始：严格表达契约 | pending | pending |
| 2 | P2 | 待开始：MapSpec 持久提交/恢复 | pending | pending |
| 3 | P3 | 待开始：tile 数据面 | pending | pending |
| 4 | P4 | 待开始：descriptor/probe | pending | pending |
| 5 | P5 | 待开始：compiler/runtime/UI | pending | pending |
| 6 | P6 | 待开始：Pi 工具 | pending | pending |
| 7 | P7 | 待开始：图例/导出 | pending | pending |
| 8 | P8 | 待开始：fixture 旅程与测试矩阵 | pending | pending |
| 9 | P9 | 待开始：独立 review→修复→复审→PR | pending | pending |

## 当前状态

- BASE_SHA `816705218a5aa6202bf15d745cb31cddb3437f44`；分支
  `zcode/goal-g20-raster-authoring-20261110T131721Z`。
- 工作树干净（仅新增 planning 文档，尚未 commit）。
- 下一步：实现 `app/schemas/raster_spec.py` 的严格表达契约 +
  `app/services/raster_authoring/` 领域模块，先跑纯契约单测。
- 已知阻塞：无。线上 CI 按要求不等待。
