# G25 LOOP_LEDGER — 迭代台账

BASE_SHA：`b8f2a6ecc777d0ae633202034b609a48a8d140d9`（origin/master @ 2026-11-12 fetch）
分支：`zcode/g25-interactive-frame-runtime-20261111T173941`
worktree：`/home/kevin/projects/webgis/webgis-wt-g25-frame-runtime-20261111T173941`

## 里程碑状态（not_started / in_progress / implemented / verified / blocked）

| 里程碑 | 状态 | 证据 |
|---|---|---|
| M0 侦察（master/PR/review/文件） | verified | RECON.md（BASE 重fetch；#2084-2086 已合入；J17 编号空闲核实） |
| M1 P0 契约层（frame-runtime/where-ast/后继/守卫） | verified | vitest where-ast 8/8 + frame-runtime 9/9 |
| M1 P3 相机 hub（resolveGroupSync 接线+三层防回声） | verified | frame-camera-hub 8/8 |
| M2 P1 纯模块（instance registry/snapshot LRU/effective-spec） | verified | frame-runtime-stores + effective-spec 7/7（含 time_window 归一） |
| M2 P1 组件（FrameViewport/FrameGrid 单 live 槽位+warmup） | verified | frame-grid.test 7/7（振荡缺陷已修复：预算判定排除自身槽位） |
| M3 P2/P5 编辑接线（激活 CAS/scope 路由/键盘/面板一致） | verified | frame-edit-scope 4/4 + grid 键盘/激活用例 |
| M4 P4 资源（卸载捕获/context loss 代际/会话失效/30 次挂卸） | verified | frame-viewport.test 5/5 + grid 会话/A13 用例 |
| M5 P6 观察生产者+后端接线 | verified | 前端 frame-observation（grid 观察用例）+ 后端 test_g25_observation_wiring 6/6 |
| M6 P7 J17 mock 档（真实浏览器） | verified | playwright Chromium 通过（6.5s/10s 两轮稳定） |
| M6 P7 J17 real 档（真后端） | in_progress | 本地栈已起（LLM stub/uvicorn/admin/前端 3325）；阻断=vendor/pi RPC entry 未构建 → 构建中 |
| M7 独立 review + PR | not_started | — |

## 关键修复记录（可证伪循环）

1. **预算振荡（BLOCKER 级，自审+组件测试发现）**：`budgetBlocked = liveCount >= budget`
   把网格自身 frame 槽计入 → main+frame=2 触发卸载 → 1 → 挂载 → 无限
   mount/unmount 循环（真实表现为测试挂起 + RSS 增长）。修复：预算判定
   排除自身槽位（`liveCount - gridFrameRegistered + 1 <= budget`）。
2. **mock canvas 无 addEventListener**：context-loss 监听对 mock canvas 抛
   TypeError → 树崩。修复：防御守卫（真实浏览器恒有）。
3. **后继选择用了删除后帧序**：indexOf 找不到被删帧 → 回退首帧（A13 测试
   抓到）。修复：保留删除前帧序（prevOrderRef）环形定位。
4. **测试基建端口污染（环境级）**：playwright webServer `reuseExistingServer`
   复用了 **G22 worktree** 占用的 3310 端口 → 我的测试跑在别人的代码上
   （chip 永不出现）。修复：本方向 E2E 一律 E2E_PORT=3325。教训已记
   TEST_EVIDENCE。
5. **chip 被右侧 HUD/图例遮挡**：click 等待 actionability 超时。修复：入口
   chip 移至右下角。
6. **Escape 关闭排队**：切换链串行 + capture 5s 上限 → 连点后 Escape 等
   待超 10s。修复：capture 上限 2.5s（交互路径）+ 断言窗 20s。
7. **time_window 归一缺口**：G15 fixture 用 ISO 字符串界 + year_int 表示，
   后端有 normalize_boundary_value、前端初版没有 → 字符串窗对 int 字段
   永不命中（空帧）。修复：normalizeBoundaryValue JS 移植（含契约时区
   Asia/Shanghai 边界用例）。

## BASE 既有失败（非本方向，两侧同现）

- `lib/mapspec-runtime/label-authoring.test.ts`（tsc -p tsconfig.test.json，
  G16 文件）
- `components/data-import/import-workbench.tsx` L724 裸 CJK（no-raw-cjk，
  G17 文件）
- `components/map/comparison/comparison-view.test.tsx` clip-path `0px` 归一
  ×2（jsdom 31 疑似）
- `components/map/poi-info-panel.test.tsx` 顶部翻转 1 例
全部在 stash 后干净 BASE b8f2a6ec 复现（主工作区 816705218 通过——差异
来自 #2084-2086 三合并，与本方向无关）。

## 下一步（按序）

1. vendor/pi 构建完成 → 重启后端 → J17 real 档复跑至通过（或如实 blocked）。
2. 全量前端回归复跑（确认 i18n 修复后仅剩 BASE 4 失败）。
3. 受影响后端回归（frame_products 单元 + mapspec mutation 合同）。
4. ACCEPTANCE.md 矩阵 A01-A28 逐项映射 + TEST_EVIDENCE 汇总。
5. 独立 reviewer（3rd subagent）全量 diff 审查 → fix → 复审。
6. 整合最新 master → PR（不合并）。

## 资源/预算记录

- 硬件：16 核 / 62GiB（可用 ~36GiB）；7 方向并行，vitest 进程峰值 ~140。
- 重任务锁：`.heavy-task.lock`（flock）——pnpm install/venv/全量 pytest/
  playwright/vendor-pi build 全部持锁执行。
- 后台服务（本方向启动，用后即停）：LLM stub :8767、uvicorn :8001
  （GIS_MANIFEST_STRICT=0 本地降级——manifest 悬挂全为其他方向工具，见
  TEST_EVIDENCE）、next dev :3325。
- Playwright workers=1；vitest 单文件粒度。
