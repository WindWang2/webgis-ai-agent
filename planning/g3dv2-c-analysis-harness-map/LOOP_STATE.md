# LOOP_STATE — g3dv2-c-analysis-harness-map

## 轮次账本（goal-loop）

| 轮 | 改动 | 验证 | 结果 | 下一步 |
|---|---|---|---|---|
| 1 | 现场侦查（git/gh 实时核对 + 2 个 Explore agent 断链审计 + 文档地图） | BASELINE/PR_OVERLAP 落盘；master@70dab2b 与任务书一致 | 通过 | 建 worktree |
| 1 | M1a viewshed_geometry.py | 14 例（含 2 处我方测试算术错修正） | 通过 | M1b |
| 1 | M1b volumes.py | 16 例解析 oracle | 通过 | M2a |
| 1 | M2a/b 工具面（viewshed 几何 + cut/fill 工具 + 契约） | 9 例 + 既有 36 回归 | 通过 | M2c |
| 1 | M2c profile 缝（认可键/白名单/工厂/互斥合并） | 3 例 + 组件回归 78 | 通过（工厂缺失根因修复） | M3 |
| 1 | M3 编排全算法 + staleness 接线 | 12 新增 + scene3d 125 + F20-F26 45 | 通过（dem 检查跳过语义修正） | M4 |
| 1 | M4 组合模板 + golden 再生 | pack 10 例 + cartography 2060 | 通过（模板选择抢占根因修复） | M5 |
| 1 | M5 F27 族 + 验收基建 | 61 场景 + pin | 通过（产物提升/选择上下文/观察者语义收窄三个根因修复） | M6 |
| 1 | M6a ADR-0276 + 账本 + watermark | ruff 自文件清零；契约 18；协调 32 | 通过（吸收 #1830 快照/水位欠账） | M6b/c/d |

## 当前状态

- 分支 zcode/g3dv2-c-analysis-harness-map @ worktree ../webgis-wt-g3dv2-c-analysis
- 原子 commits：M1+M2ab / M2c / M3 / M4 / M5 / M6（ADR+账本）
- 进行中：tests/unit 全量串行（后台）；前端 vitest 4626/4628（1 失败为
  data-plane.perf 并行负载抖动，单跑通过——零前端改动，等机器空闲复跑）

## 终局记录（2026-10-28）

- 独立评审两轮完成：第一轮 1×P1+2×P2+7×P3 → 全部清偿（commit 5a82cd646）；
  第二轮复验 9/9 CLOSED，结论「可合」（含证伪实验复跑 + F1 反证对拍）。
- tests/unit 全量（17567 passed / 40 failed）→ 逐项归因：25 项为本分支
  引发（注册表/目录/快照再生 + _parsed 行为恢复 + 前端目录欠账），全部
  清偿；12 项 master 既有红（干净 worktree 对照同失败：metadata 棘轮
  158/159、binding taxonomy、execution catalog、component catalog、
  export corpus、mutation registry、platform build info、behavioral
  webgis、response model coverage——#1829/#1830 遗留）；2 项 runtime
  fixture（Playwright）在 origin/master + 同一 node_modules 下同样失败
  （环境性，双向对照完成）；1 项 vitest perf 抖动（单跑通过）。
- 修复后再全量复跑受影响域：lib/gis/gis_tool_conformance/scene3d/
  dispatch/profile/tool ×2232 passed + 1 既有棘轮红（已归因）。
- 前端：typecheck ✓ eslint 0 warning ✓ vitest 4626/4628（1 抖动 + 1
  expected fail）+ catalogue-contract 修复后 2/2。
- master 无前移（交付前复查 origin/master == 70dab2b）。

## 验收 Oracle 对照（goal-loop 完成判定）

| Oracle | 结果 |
|---|---|
| 基线核对+排除矩阵 | ✅ BASELINE/PR_OVERLAP（实时 gh 核对） |
| ≥1 新算法解析 oracle | ✅ cut/fill 16 例闭式（<1e-6 m³） |
| LOS/profile/viewshed 零回归 | ✅ 25+36 例 + stats 逐位一致 |
| 图层+剖面+ACK+导出+失效判定 | ✅ F27-01/02/04/12/14 |
| ≥12 任务级场景 | ✅ F27=14（pin 61） |
| 本地确定性质量门 | ✅ 本分支域全绿（12 项既有红全部 master 对照归因留档） |
| 独立 review→fix→复验 | ✅ 两轮，9/9 CLOSED |
| PR 已创建 | ✅ **PR #1867**（gh 返回号；关键 oracle 二次复跑 73 passed 防侥幸） |
