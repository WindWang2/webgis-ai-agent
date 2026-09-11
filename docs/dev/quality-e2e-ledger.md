# quality-e2e-v9 交付台账与 flaky 风险登记表

> 分支 foundation/quality-e2e-v9 · 基线 origin/master 8b5b8375 · ADR-0146

## §5 门禁清单（逐项取证）

| # | 门禁 | 状态 | 证据 |
|---|---|---|---|
| 1 | 六条旅程 mock 全绿 | ✅ 6/6，~14s | 本地 playwright run（`E2E_MODE=mock pnpm exec playwright test`）；journey-smoke CI 取证见 PR 描述测试证据节 |
| 2 | real 档 nightly 全绿 | ✅ 已建，CI journey-real 档执行 | 无 key/栈环境显式 skip（3 skipped，理由内联）；CI nightly 档为权威取证面 |
| 3 | PR 冒烟 ≤8min / nightly ≤40min / 失败产物上传 | ✅ | journey-smoke `timeout-minutes: 8`；journey-real `timeout-minutes: 40`；`actions/upload-artifact`（failure） |
| 4 | changed-lane 映射正确性测试（#1216 类不可复发） | ✅ 21 例 | 前端 11（vitest）+ 后端 10（pytest，subprocess fake 钉 #1216 原始失效面） |
| 5 | 弱势区引用数达标（销项表全勾） | ✅ 35 / 30 / 20 | `grep -rl <domain> tests/ --include="*.py" \| wc -l`：workflow_runtime 35、explorer 30、data_lifecycle 20；缺口清单见 docs/dev/quality-e2e-recon.md §3 |
| 6 | budgets.json 四条线 CI 生效 + 合成超线能红 | ✅ 自证红 | `run_budget.py --self-test` 注入 10 倍测量 → gate RED；7 例门禁语义测试 |
| 7 | Pi 两票（E2E 绿（有 key 环境）+ 报告落库 + WAYFINDER 勾销） | ✅（数值待有 key 环境） | tests/integration/test_pi_real_llm_e2e.py（heavy + PI_REAL_E2E=1 + key 门控，workflow_dispatch 可跑）；docs/dev/pi-vs-chatengine-benchmark.md（复跑配方）；WAYFINDER_MAP.md 两票勾销并链接 |
| 8 | chaos 三类绿（heavy 手动档） | ✅ 已建，CI chaos 档执行 | 无栈环境 3 skipped（显式理由）；CI chaos job（nightly+dispatch）为权威取证面，运行记录见 PR 描述 |
| 9 | 业务代码零改动；`git diff origin/master -- migrations/` 为空 | ✅ | migrations diff 空；业务面仅 PERSIST_KEY 最小修复（6 行，commit 5e442223，PR 单列） |

## 交付物清单

| 阶段 | 交付物 | 位置 |
|---|---|---|
| P0 | 勘察报告（CI 图/复用面/销项表/基准盘点/故障谱系） | docs/dev/quality-e2e-recon.md |
| P1 | 旅程基建（config/桩/SSE 回放/helpers/注册表） | frontend/e2e/ + frontend/playwright.config.ts |
| P2 | 六条核心旅程（双模式） | frontend/e2e/journeys/*.journey.ts |
| P3 | CI lane + 映射测试 + LLM stub | .github/workflows/quality-e2e.yml + frontend/e2e/tools/ + tests/unit/test_quality_runner_lane_mapping.py |
| P4 | 三弱势区 36 个新测试文件（222 passed） | tests/unit/workflow_runtime/p4_*、tests/unit/p4_test_explorer_*、tests/unit/p4_test_lifecycle_* |
| P5 | 预算闸（四条线 + 自证红） | perf/budgets.json + scripts/perf/ + tests/unit/test_perf_budget_gate.py |
| P6 | Pi 两票 | tests/integration/test_pi_real_llm_e2e.py + scripts/perf/pi_vs_chatengine.py + docs/dev/pi-vs-chatengine-benchmark.md + WAYFINDER_MAP.md |
| P7 | chaos 三类 | tests/integration/chaos/ |
| §6 | 本台账 + flaky 登记表 + ADR-0146 + CHANGELOG | docs/dev/quality-e2e-ledger.md + docs/adr/0146-*.md |

## flaky 风险登记表

| 风险 | 概率 | 缓解 | 状态 |
|---|---|---|---|
| J3 画布像素 diff（容差 1.5/255）在 CI 软渲染下基线漂移 | 中 | 容差>渲染抖动；轮询式取样；real 档 30s 窗口 | 观察（首夜若红：调容差而非重试） |
| J2 mock 取消后 UI 重询时序（DELETE→轮询收敛） | 低 | stub 同事务翻转状态 + has_active 变化触发刷新 | mock 稳定绿；观察 |
| 真实后端模板库若无「深色影像底图」builtin，J3 real 显式 skip | 中 | 显式 skip（带 seed 指引），非静默绿 | 设计内 |
| J6 real 依赖 nightly postgis 种子目录 | 中 | 显式 skip（非静默绿）+ workflow 提供真实种子表 | 设计内 |
| SSE/chat 导入链在无 pango 环境的预算线 ERROR | 低（CI 有 pango） | CI perf-budgets job 与 backend job 同环境 | 已验证 CI 规格 |
| cube 窗口预算线在无 zarr 环境 | — | typed SKIP（非静默绿）；CI job 显式 pip install zarr | 设计内 |
| dev server 启动时长（Playwright webServer 120s 上限） | 低 | reuseExistingServer 本地生效；CI 冷启动实测 <60s | 观察 |

## 资源档位（§8 协调点：runner 分钟预算）

| Job | 触发 | 上限 | 实测/预估 |
|---|---|---|---|
| journey-smoke | PR+nightly+dispatch | 8 min | 本地 6 用例 ~15s + 安装 ~3min ≈ 4-5 min |
| journeys-mock | nightly+dispatch | 20 min | ~5 min |
| perf-budgets | PR+nightly+dispatch | 20 min | 自证 2s + 门禁 ~2 min + 安装 ~4 min ≈ 7 min |
| chaos | nightly+dispatch | 30 min | ~10 min |
| journey-real | nightly+dispatch | 40 min | build+栈启动 ~10 min + 6 用例 ~5 min ≈ 15-20 min |
| **合计** | PR ≈ 12 min/PR；nightly ≈ 75-85 min | 错峰 02:30 UTC（避开 production.yml 02:00 nightly-matrix 180min 档） |
