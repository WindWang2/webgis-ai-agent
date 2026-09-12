# ops-console-v9 交付台账（feat/ops-console-v9 → master）

> 基线 `origin/master@8b5b8375`（2026-09-11）。ADR-0142。纯前端线：`git diff origin/master -- app/ migrations/` 为空（已验证）。
> 变更面：51 文件，+7100/−7；存量改动仅 geocompute.ts（扩展）、runtime-inspector.tsx（契约漂移修正）、system-settings.tsx（append tab）、4 处 rail 注册 append-only 行、nav-rail.test.tsx（注册行期望同步）。

## §5 门禁逐项

| # | 门禁 | 证据 | 状态 |
|---|---|---|---|
| 1 | runtime-inspector 有真实调用方（非测试 ≥1） | `grep RuntimeInspector`：`components/sidebar/ops/runtime-section.tsx:118` fetcher 注入挂载；测试 `runtime-section.test.tsx` 断言 `wf-status`/`wf-node-*` testid 出现在真实挂载树 | ✅ |
| 2 | typed client 全覆盖 + 三态 fixtures | 契约表全勾（`ops-console-recon.md` §2/§3：geocompute 14 端点 + workflow-runtime 13 端点 + 健康面 5 端点）；`test/fixtures/ops-routes.ts` 每端点 ok/empty/error 三态注册器，旅程测试即用 | ✅ |
| 3 | 验收旅程（集群→stuck→干预→回执；校验→提交→瀑布） | `ops-journey.test.tsx` 7 用例（含 403 权限态、空集群态、409 并发回执） | ✅ |
| 4 | 系统健康通道全接通或诚实空态 | health/version/ready/status-detailed/jobs/metrics 六通道接通；错误分类 top-N=诚实空态卡；特性标志=诚实缺失注记；无假数据无死按钮 | ✅ |
| 5 | 断路器三态可视 + 快照 | `breaker-panel.test.tsx` 4 张 DOM 快照（empty/closed/open/half_open，时刻打码跨时区确定）+ 明暗 Playwright 截图 | ✅ |
| 6 | 大屏键盘可达；reduced-motion 禁轮播 | `wallboard.test.tsx`（←/→/Space/F/Esc、aria-pressed 默认关）+ capture `reducedMotion:'reduce'` 下截图显示「手动模式」 | ✅ |
| 7 | typecheck 0 / lint 0 / vitest 全绿 / 覆盖率 ≥75 | typecheck 双 tsconfig 0 error；`eslint --max-warnings 0` 绿；全量见下节；覆盖率 lines 79.3% / stmts 79.4% / funcs 69.3% / branches 82.2%（阈值 75/75/70/60） | ✅ |
| 8 | next build 成功一次；后端零改动 | build exit=0（Compiled successfully）；`git diff origin/master --stat -- app/ migrations/` 输出为空 | ✅ |

## 测试与取证

- 全量：**320 文件 / 2962 用例全绿**（`--retry=2`；`lib/workbench/collab.test.ts` C4 为 master 既有负载敏感 flake——本分支对该文件零改动（`git diff origin/master -- frontend/lib/workbench/` 为空），单跑两遍均绿，首轮全量亦全绿；该测试最近一次 master 提交即修其计时（bc0844c7），记协调点）。
- 本线新增测试：**10 文件 / 109 用例**（9 组件测试文件 + 3 client 测试 + 2 hook 纪律测试 + 注册防回退；含 4 张三态快照）。
- Playwright 视觉：`.visual/ops-after/` 40 张（ops-cluster/runtime/breaker/health/wallboard × 4 视口 × 明暗），failures=0。
- 清理：`.next/`、`coverage/` 已删。

## 协调点（给并发线与后端）

1. **错误分类 top-N**：无 HTTP 端点（failure_taxonomy/RemediationLedger 进程内）→ 健康卡显「能力待后端支持」，请求后端补只读端点。
2. **断路器/缓存聚合**：`engine_breaker`/`result_cache` 仅内嵌于联邦查询载荷，无独立端点 → 披露留存型面板 + 诚实空态；落地独立端点后切轮询。
3. **master flake**：`lib/workbench/collab.test.ts` C4 负载敏感（约 1/2 全量轮次失败，单跑稳定），建议 W 线跟进。
4. **rail 注册行**：`LeftTab`/`MODE_TABS`/`RAIL_GROUPS`/context-panel 各一行 append（`ops`），D/F/H/J 线 rebase 时按 append-only 合并。
5. **settings tab**：SystemSettings 内 append「集群健康」子 tab（不改 settings-panel NAV_ITEMS，与 G 文案键零冲突）。

## 复核纪要（摘要，全文见 ops-console-recon.md §0）

无重叠 PR/分支照单执行；与任务书假设的 9 处事实修正（无 msw→零依赖 stub 等价物、无 workflow tab→新 ops rail tab、events 为轮询 JSON 非 SSE、无 /healthz、cluster 面 require_admin 等）均在 ADR-0142 决策化。

## /code-review 两轴结论（§6.2）

- **Standards 轴**：修 1 个真实并发缺陷（run-events tick 不 abort 前驱 → 同页双取风险，已修）；修 1 个 portal 缺陷（context-panel transform 收编 fixed 定位 → 大屏 portal 到 body）；11 个 lint warning 清零；轮询清理/订阅卸载/合成序列不进生产逐项核过（grep 生产代码零 fixtures 引用）。
- **Spec 轴**：§2 P0–P8 全交付；§5 八项门禁全过；加深杠杆完成 1 项（指标图断路器跳闸时间带叠加）。
