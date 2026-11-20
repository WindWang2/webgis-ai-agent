# G24 LOOP_LEDGER — 里程碑与状态

分支 `zcode/g24-mvt-authoritative-selection-20261111T151706Z`；BASE `b8f2a6ecc`。
规则：not_started / in_progress / implemented / verified；implemented ≠ verified。

## 里程碑

| # | 项 | 状态 | 证据 |
|---|---|---|---|
| M1 | 侦察（master/PR/issues/契约） | verified | RECON.md / INTERFACES.md（调查员报告全文消化） |
| M2 | P0 身份链统一 + 2^53 + 歧义防线 | verified | commit `3e96a8188`、`tests/unit/test_g24_identity_guards.py` 5 passed；MVT/feature_edit/pages 回归 180 passed |
| M3 | P3 selection handles 后端 | verified | `test_selection_handles.py` 16 + `test_selection_handle_routes.py` 10 passed |
| M4 | P2 前端权威拾取 + 编辑接线 | verified | `geometry-hit.test` 6 + `mvt-pick.test` 12 + `feature-layer-editor.test` 4 passed；tsc/lint 0 |
| M5 | P3/P4/P5 客户端贯通 | verified | selection-store/brush/map-panel/table-panel 回归 128 passed；selection-table-data 3 passed；journey（A19/A22）2 passed |
| M6 | P6 Pi 工具 + 旅程 | verified | `test_selection_tools.py` 5 + acceptance journey 2 passed（真实 dispatch） |
| M7 | P7 大图层证据 | verified | 100k 档实测（132B/0 扫描）；1M gated |
| M8 | 全量回归 | verified | 后端 tests/unit 全量：**20486 passed / 47 failed / 27 errors**；74 项失败/错误清单在干净 BASE（b8f2a6ecc）同条件复跑**逐一一致**（同一批预存失败，G24 引入=0）。前端全量：5198 passed / 4 failed（4 例预存：BASE 未触碰文件）。IR 全族 175 passed（manifest additive 再生成后） |
| M9 | 独立 review → fix | verified | 三轮闭环：R1 REQUEST_CHANGES（P1×2/P2×2/P3×7）→ fix → R2 narrow RC（R2-1..R2-5）→ fix → **R3 APPROVE**（reviewer 复跑 166 后端/186 前端/tsc 0/manifest 2 行纯增量）；REVIEW.md 全记录 |
| M10 | PR | pending | 推分支 → gh pr create（不合并） |

## 预存失败登记（BASE 同条件复现，非本方向引入）

- 后端全量 47 failed + 27 errors：与 BASE 逐项一致（workflow_runtime/test_api 11E、
  api_contract 8E+4E、gis_tool_conformance 注册审计 17 个 field_compute/import 工具
  结构性 fail、session_lock/review_migration/data_fabric/audit4 等 —— 均他人 PR 带入的
  预存问题域，本方向不吞并）。
- Next 生产构建未本地执行（重任务锁被并行方向占用；tsc+vitest 全绿兜底，CI 构建）。

- `tests/unit/api_contract/test_api_v2.py`：4 errors（干净 BASE worktree 复现一致）。
- `frontend test/i18n/no-raw-cjk.test.ts`：`components/data-import/import-workbench.tsx:724`（#2079 带入的裸 CJK，本方向未触碰该文件）。
- `frontend poi-info-panel.test.tsx`（1）+ `comparison-view.test.tsx`（2）：style 断言（本方向未触碰）。

## 下一步可执行动作

1. 后端全量收尾 → 记录最终计数进 TEST_EVIDENCE。
2. spawn 独立 reviewer（BASE→HEAD 全量 diff + 复跑关键套件 + 负向测试审查）。
3. review → fix → review 循环至 P0/P1/P2 findings 归零。
4. fetch 最新 master 复核冲突 → 推分支 → PR。

## 预算/资源记录

- 子代理：调查员 1 个（13.2M tokens 实耗）；实现/测试主 Agent 自完成；reviewer 待用（≤3 限额内）。
- 构建：pnpm install 1m31s（worktree 独立 node_modules）；无 native/编译重任务；宿主重锁仅用于全量 pytest（flock）。
- 后端测试借主仓 .venv 只读层；未启动常驻服务；无浏览器 E2E（边界披露见 TEST_EVIDENCE §4）。
