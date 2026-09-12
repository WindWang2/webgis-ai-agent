# ADR-0146: 旅程分层测试架构与确定性策略（quality-e2e-v9）

日期：2026-09-12 · 状态：Accepted · 线：foundation/quality-e2e-v9

## 背景

产品主路径（对话→分析→出图→导出等）零端到端保护：Playwright nightly 是
runtime validator（验渲染管线，#532/#672），不是用户旅程；CI 注释自认
「5~6 类 E2E 场景仍待建」（production.yml:445）。同时三个新子系统
（workflow_runtime / explorer / data_lifecycle）的回归保护显著低于
gis_harness/data_fabric 水位（19/18/8 vs 147/107 个测试引用），
benchmarks 存在但无预算闸，#1223–#1233 十一连 CI 修复波次的故障谱系
没有 chaos 化沉淀，WAYFINDER 两张 Pi 票挂账。

## 决策

### D1 — 旅程分层：mock / real 双档（journey tiers）

- **mock 档**：`page.route` 劫持全部产品 API + 有状态 fixture 世界
  （`frontend/e2e/fixtures/api-stubs.ts`，继承 capture.mjs 的拦截模式）+
  生产契约 SSE 回放。零后端、PR 冒烟档（`@smoke` 两条，≤8 分钟）、
  本地秒级可跑。
- **real 档**：真实后端（uvicorn 最小 SQLite/fakeredis 配置）+ 真实前端
  + **确定性 LLM 边界**（`e2e/tools/llm-stub.mjs`：OpenAI wire 格式脚本化
  应答，`STUB_FAIL_AT`/`STUB_DELAY_FIRST_TOKEN_MS` 编排取消/重试时序）。
  旅程保护的是产品路径编排，不是 LLM 随机性；真实 LLM 仅 P6 三条冒烟
  （`PI_REAL_E2E=1` + 真实 key 门控）。nightly 档 `REQUIRE_BROWSER=1`
  硬失败语义沿用 runtime-validator lane（缺浏览器 = 红，无绿色 skip）。
- 命名纪律：旅程文件 `*.journey.ts`，与 vitest 的 `*.{test,spec}.*` 互斥；
  Playwright config（`frontend/playwright.config.ts`）与 runtime validator
  的编程式用法互不干涉。

### D2 — 旅程注册表（journey registry）

`frontend/e2e/helpers/registry.ts` 是旅程唯一索引（id/file/smoke/modes/owner）。
D–J 并行线的扩展路径：新增 journey 文件 → 注册表登记 → `@smoke` 标记即进
PR 冒烟档。CI 不持有旅程清单（gate 面与旅程同 PR 评审）。

### D3 — changed-lane 映射双引擎锁定（#1216 纪律）

#1216 的失效类 = 「映射键存在但永不匹配自己的目录」。因此：
- 前端 lane 的 diff→旅程映射（`frontend/e2e/tools/changed-lanes.mjs`）用
  前缀键 `f == key || f.startsWith(key + "/")`，每个键由
  `frontend/tests/e2e-lanes/changed-lanes.test.ts` 断言能命中其目录内代表文件；
- 后端 quality_runner 的 #1216 修复由
  `tests/unit/test_quality_runner_lane_mapping.py` 以 subprocess fake 钉住
  原始沉默失效面（app/core、app/services、app/api、app/tools 两段键）。

### D4 — 确定性策略（journey determinism）

- 断言一律轮询用户可见的生产信号（`expect().toPass()` /
  `expect.poll` / UI aria），零固定 sleep；
- 主题「不闪白」契约的可观测定义：内联 no-flash 脚本是 parser-blocking，
  `readystatechange → interactive`（解析完成、模块脚本/React 未执行）是
  规范保证的最早可观测点；
- idempotent 导航（重复点击已激活 rail tab 会折叠面板——UI 事实）；
- 视觉 diff 用显式容差（平均通道差 1.5/255）而非像素相等。

### D5 — 预算闸 opt-in 纪律（perf budgets）

`perf/budgets.json` 四条首批预算线，数量级回归闸定位（防 O(n²) 退化，
非微基准）；容差 10% 抗噪；**预算刷新 = 显式改文件（PR 可审）**——
无 env 后门（#1230–#1233 预算漂移波次的机制教训）。门禁自证：
`--self-test` 注入 10 倍超线测量必须红。

### D6 — chaos 门控纪律

chaos 用例 = `heavy` 标记 + `CHAOS_STACK=1` + 依赖可达性探测，否则
显式 skip（理由内联）——同 `real_services` 既有模式；故障注入是真实
进程控制（SIGKILL worker、compose stop redis、客户端 abort）。

## 后果

- PR 面：+8 分钟 journey-smoke + ~5 分钟 perf-budgets（quality-e2e.yml），
  不进 production.yml 的 release-gate 链（独立 workflow，不阻塞发布门）；
  nightly 总增量 ≈ 63 + 30 + 40 ≈ 2.2 小时 runner 分钟（错峰 02:30 UTC）。
- 业务代码零改动（例外：PERSIST_KEY RSC bug 最小修复，diff 6 行，
  commit 5e442223 单列）。
- 已知缺口（诚实登记）：上传 UI 未挂载（J1 上传腿钉在传输契约层）；
  「结果」工作台 rail tab 已不在壳上（capture.mjs 为旧界面）——均在
  `frontend/e2e/README.md` 与 PR 描述登记。
