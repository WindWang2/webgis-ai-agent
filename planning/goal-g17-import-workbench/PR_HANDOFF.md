# PR_HANDOFF — G17-IMPORT-WORKBENCH 交付清单

- **PR**：https://github.com/WindWang2/webgis-ai-agent/pull/2079
- **Branch**：`zcode/goal-g17-import-workbench-20261110T131224Z`（已 push）
- **Worktree**：`C:/Users/wangj.KEVIN/projects/webgis-worktrees/goal-g17-import-workbench-20261110T131224Z`
- **Base**：`816705218a5aa6202bf15d745cb31cddb3437f44`（origin/master @ 创建时）
- **Head**：`21913e103`（10 个提交）
- 不自动 merge、不自动发布、不删分支/worktree。

## 提交历史（阶段性）
1. `3657e6fed` 后端纵向切片（域服务 + API + 工具注册 + 首个全链测试）
2. `6b5bd10d0` 容器矩阵 + 表格矩阵（15 例）
3. `54061757f` 恢复/取消/预算 + 工具真实 dispatch + API 合同（13 例）
4. `2296b6b7b` 前端工作台 + 旧契约回归 + 跨入口一致性
5. `f817905c3` 故障注入反验 + 近限额性能实测 + 格式支持矩阵文档
6. `540d1fbe0` ruff 清理
7. `943addc9e` 独立评审 R1 修复（P0×1 + P1×2 + P2×12）
8. `ab848bbbd` zipset 提取目录哈希（防 sanitize 同名互相覆盖）
9. `21913e103` 复审收尾（DIAG 接线 / 端点 query 参数 / P2-8 真红绿 / 取消意图跨重启 / 删误跟踪空文件）

## 主要功能
- 多文件 staged 批次（GeoJSON/GPKG/KML/ZIP-shapefile/CSV/XLSX）→ 容器成员清单 →
  解释计划（版本 + hash）→ 有界预览 + 拒绝行 → 逐成员幂等提交（空间→IngestPipeline
  ref:upload-*；纯表→ref:table-*）→ 收据可验证；浏览器与 Pi 工具同一服务/计划/收据。

## 测试与评审结论
- data_import 41/41；旧契约六套 40/40；前端 12/12；ruff 全绿；tsc 新文件 0 错。
- 22 验收场景全落位（TEST_MATRIX.md）；故障注入 3 例 + 评审修复 8 例红→绿。
- 独立评审两轮（R1 发现 → 修复 → R2 复审）：无 P0/P1 残留；P2 全部处理或转跟进项
  （>64 列收据层披露、重启后取消意图已修、5000 魔法数已入 budgets）。
- 性能：PERFORMANCE.md（45.4MB 近限额：发现 3.2s/峰值 1MB；提交 O(N) 217s/553MB 如实披露；
  取消往返 6.2s；清理无残留）。

## 必要操作说明
- 运行：`pytest tests/data_import/ -q --no-cov`（conda python 3.13.9，依赖齐全）。
- XLSX 生产依赖 `openpyxl` 已入 requirements.txt/.lock（read_only，不执行公式）。
- 前端需 `frontend/node_modules`（本机 junction 自主工作区；基线相同未改 package.json）。

## 剩余外部阻塞
无。在线 CI 未等待（任务授权范围内）；如 CI 暴露新问题，按 PR 评论跟进。

## 复现指引（从小 fixture 跑完整闭环）
`tests/data_import/test_vertical_slice.py`（双 GeoJSON 全链）+
`test_tools_dispatch.py::test_tool_closed_loop_via_real_dispatcher`（工具闭环）+
`test_api_routes.py::test_api_full_journey`（HTTP 闭环）。
