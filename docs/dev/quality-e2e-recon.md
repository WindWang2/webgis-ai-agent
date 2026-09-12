# Quality E2E V9 勘察报告（P0）

> 线：foundation/quality-e2e-v9 · ADR-0146 · 基线 8b5b8375 · 勘察方式：S1 只读勘察 + 主 agent 复核。
> 本文是 P1–P7 的设计输入；《复核纪要》结论见 PR 描述。

## 0. 复核结论（§0.2）

- 无重叠，照单执行。最接近的历史资产：quality-v1/v2/v3（#1159/#1172/#1184，pytest 级质量体系）与 runtime validator（#532/#672/#673，渲染管线验证）都不是用户旅程 E2E；CI 注释（production.yml:445）自认「5~6 类 E2E 场景仍待建」即本线空间。
- 分支查重：`git branch -a | grep -iE "e2e|quality|benchmark|chaos"` 仅本线分支。
- 底数核对（与任务书一致）：tests/unit 737 文件 184,552 行；tests/benchmarks 35 py 文件 10,046 行；弱覆盖引用数 workflow_runtime 19 / explorer 18 / data_lifecycle 8（对照 gis_harness 147 / data_fabric 107）。
- #1216：quality_runner changed lane 映射键 bug（`join(parts[:3])` 对两段键永不匹配），已修为有序前缀映射；本线新增映射键必须带映射正确性测试。
- WAYFINDER_MAP.md:38-39 两张开放票（Pi bridge 真实 LLM E2E、Pi vs ChatEngine 基准）→ 本线 P6 清。

## 1. CI 全量 lane 图（production.yml，唯一 workflow，1237 行）

触发（:8-16）：push→master/release/**；所有 pull_request；schedule `0 2 * * *`（UTC 02:00）；workflow_dispatch。并发组 `${{workflow}}-${{ref}}`（仅 PR 取消在途）。Python 3.12。

| job | 触发 | 档位 | 说明 |
|---|---|---|---|
| lint (:33) | 全事件 | 20min | ruff + eslint --max-warnings 0 |
| test-backend (:89) | 全事件 | 60min | postgis+redis services；`pytest --cov-fail-under=75 -m "not perf and not cartography and not real_services"` |
| db-migrations (:223) | 全事件 | 30min | alembic upgrade head + 模型漂移 check |
| real-services-smoke (:282) | 全事件 | 45min | REAL_SERVICES=1，redis broker db6/result db7，worker 由 fixture spawn |
| test-perf (:350) | 全事件 | 45min | 显式 15 文件清单 `-m perf`，nightly 专属两文件排除 |
| cartography-smoke (:393) | 全事件 | 45min | `-m cartography`，无 Node/Chromium/LLM/外网 |
| runtime-validator (:455) | schedule+dispatch | 60min | pnpm → `playwright install --with-deps chromium` → tsx → `pytest tests/unit/test_runtime_validator.py -m heavy`，REQUIRE_BROWSER='1' 硬失败模式 |
| nightly-matrix (:514) | schedule+dispatch | 180min | `-m "cartography or perf"` |
| test-frontend (:554) | 全事件 | 45min | vitest test:ci + tsc×2 + next build |
| deploy-config (:610) | 全事件 | 20min | nginx -t |
| security (:635) | 全事件 | 20min | bandit -ll -ii（blocking） |
| dependency-audit (:671) | 全事件 | 20min | pip-audit + npm audit（非阻塞） |
| release-gate (:709) | 全事件 | 5min | needs 聚合 [lint, test-backend, test-frontend, security, test-perf, cartography-smoke, deploy-config, db-migrations, real-services-smoke] |
| build (:725) | needs release-gate | 90min | buildx → ghcr |
| preview (:802) | 仅 PR | 60min | compose 栈健康探测 |
| deploy-prod (:962) | master push | 60min | SSH compose up |
| rollback (:1090) | dispatch | 30min | registry SHA 优先 |

**changed-lane 现状**（scripts/quality_runner.py:182-209，#1216 修复后）：有序前缀映射 `app/lib/gis→tests/unit/gis`、`app/lib/quality→tests/quality`、`app/tools→tests/unit/tools`、`app/services→tests/unit`、`app/api→tests`、`app/core→tests`；匹配 `f == prefix or f.startswith(prefix+"/")`；tests/** 原样入列；base 恒跑 tests/quality/+tests/unit/tools/；QUALITY_ORDER_SEED 顺序轮换；截断 40。

**新 lane 挂载决策**：独立 workflow 无法进 release-gate needs（跨文件需 workflow_run，不引入）。`quality-e2e.yml`：schedule `30 2 * * *` 错峰（避开 02:00 的 180min nightly-matrix）+ workflow_dispatch；PR 冒烟档全事件触发（仅 mock 模式 2 条旅程）；复用 runtime-validator 的安装链（pnpm→playwright chromium→tsx）与 REQUIRE_BROWSER 硬失败语义；real 档复用 real-services-smoke 的 postgis+redis 容器规格。

## 2. Playwright 可复用基建

frontend/ 无 e2e/、无 playwright.config.*；playwright ^1.63.0 已在 devDependencies，仅两处编程式使用：

- **runtime-validate.ts**（frontend/lib/mapspec-compiler/runtime-validate.ts，449 行）：`chromium.launch({headless})` + context.tracing + 内置 http 静态服务器（listen(0)）；地图 ready 判定 = 轮询 `window.__MAP_LOADED__/__MAP_IDLE__`（html-template.ts:35-44 由 MapLibre load/idle 事件置位）；console error/pageerror/response>=400 三监听；canvas 截图→pngjs 空白判定；probes：layer-exists/feature-count/pixel-color。
- **capture.mjs**（frontend/test/visual/capture.mjs，752 行）：页面级模式可改造——`--force-color-profile=srgb`、viewport×light/dark 矩阵、reducedMotion、locale zh-CN、Date.now 锚定；`page.route('**/*')` 全拦截 fixture 应答（API :8000/:8001 + 瓦片 BLANK_TILE）；SSE 回放种子（:206-294）。
- REQUIRE_BROWSER 语义：tests/unit/test_runtime_validator.py:180-191，缺浏览器时 REQUIRE_BROWSER=1 → fail（硬红），否则 skip。

**前端挂点速查**（file:line）：

| 面 | 位置 | 选择器 |
|---|---|---|
| 地图 ready | components/map/map-panel.tsx:1317,1327 | aria-label「地图画布…」；hudStore.setMapLoaded |
| 聊天输入 | components/sidebar/chat-tab.tsx:488,515 | textarea[aria-label=输入空间分析指令] / button[aria-label=发送消息] |
| 导出 | components/sidebar/map-studio-tab.tsx:507,517 | 按钮「发布并导出 {format}」（未登录禁用 title） |
| 任务中心 | components/sidebar/tasks-tab.tsx:95,136,170,181 | data-testid job-card-${id}；aria 取消/重试 ${name} |
| 模板库 | components/drawers/template-gallery-v2.tsx | 顶栏按钮「模板库」 |
| StoryMap | app/story/page.tsx:201-219 | /story 路由；播放控制 aria；data-testid=story-md |
| 暗色主题 | app/page.tsx:260-272；components/tweaks-panel.tsx:116,216；lib/store/useHudStore.ts:126-129 | documentElement class 'dark' + data-theme |
| 数据源 | components/sidebar/data-sources-tab.tsx；data-sources/sources-toolbar.tsx:21 | 按钮「添加数据源」 |
| 结果列表 | capture.mjs:628 | ul[aria-label=分析结果列表] |
| Tab 导航 | capture.mjs:566-578 | [role="tab"][aria-label*=…] |

**API 基路径**：`NEXT_PUBLIC_API_URL ?? http://localhost:8001`（frontend/lib/api/config.ts:17-18）；无 next rewrites 代理，前端直连。Next 16 App Router + React 19 + maplibre-gl 5 + react-map-gl 8 + zustand。

**双模式决策（ADR-0146 核心）**：
- **mock 档**：Playwright `page.route` 劫持全部网络（继承 capture.mjs 模式）+ 种子 fixture（含 SSE 事件流回放）；不起后端；PR 冒烟档。
- **real 档**：真实前端（next build+start 或 dev）+ 真实后端（uvicorn，SQLite/fakeredis 最小配置）+ 真实上传/SSE/DB/导出链路；**LLM 边界用确定性 stub**（env 门控 canned OpenAI 兼容响应）——理由：旅程测试要保护的是产品路径编排而非 LLM 随机性；真 LLM 仅 P6 冒烟 3 条（有 key 才跑）。REQUIRE_BROWSER=1 语义沿用：缺浏览器 = 硬红。

## 3. 弱覆盖区缺口清单（P4 销项表输入）

### workflow_runtime（app/services/workflow_runtime/，22 文件 6741 行；现 19 引用 → 目标 ≥35）

已测 15 文件（tests/unit/workflow_runtime/）。缺测（优先级序）：

| # | 缺口 | 源码位 |
|---|---|---|
| W1 | 恢复抢占：杀进程→run 租约过期→他人抢占 + periodic_recovery_sweep 循环 | recovery.py:54,110,141,158,183,227；store.py:483,520,561-582 |
| W2 | node 续租失败/过期竞态 | driver.py:485（_renew_node_lease）；store.py:266-387 lease 随转移同事务 |
| W3 | 重试门拆分边界 | driver.py:351（_split_retry_gates） |
| W4 | hooks.py 全文件 0 测试 | hooks.py:23,56,80,112 |
| W5 | adapters_cartography.py / adapters_science.py 0 测试 | 239/141 行 |
| W6 | dispatch 选择/构建/durable 等待超时 | dispatch.py:98,304,344 |
| W7 | retry 失败类别×可重试矩阵 | retry.py:57,104 |
| W8 | projection.explain inspector 投影 | projection.py:67 |
| W9 | clone_run 终态/取消守卫负例 | service.py:303 |
| W10 | journal 重放顺序/截断语义 | store.py:423,700,717 |
| W11 | 状态机 ready_set/downstream_closure 边界 | machine.py:71,105,133 |
| W12 | fingerprints/binding 冲突与失效 | fingerprints.py / binding.py |

### explorer（app/services/explorer/ 11 文件 2212 行 + app/tasks/explorer/task_chain.py；现 18 → ≥30）

| # | 缺口 | 源码位 |
|---|---|---|
| E1 | geocode_stage 零专测：主流程/分片/预算截断/多 provider 降级/汇总 | geocode_stage.py:102,155,247,251,333 |
| E2 | stream_progress SSE 断线重连/背压 | orchestrator.py:475 |
| E3 | celery chain 提交失败（broker 不可达） | orchestrator.py:314 |
| E4 | 意图历史去重触发 | intent_detector.py:120 |
| E5 | quality degraded 联动负例 | fetch_stage.py:16 + quality_engine.py |
| E6 | task_chain stage 异常→chain 短路语义 | task_chain.py |

### data_lifecycle（app/services/data_lifecycle/ 3 文件 1429 行；现 8 → ≥20；只测既有路径，C 线改造面新测试归 C 线）

| # | 缺口 | 源码位 |
|---|---|---|
| D1 | snapshot_pointer_integrity 审计返回结构 | quota.py:836 |
| D2 | 孤儿修订清理 plan/execute | quota.py:759,787 |
| D3 | check_quota 类型化 QuotaDecision 边界矩阵（413/429/unlimited） | quota.py:198,265,413,429 |
| D4 | ArtifactLifecycleService 跨域级联 PropagationReport | service.py:75 |
| D5 | execute_session_gc 中途失败恢复/幂等重入 | gc.py:147 |
| D6 | retention 清理 vs 修订保护冲突负例 | quota.py:376,625 |

## 4. tests/benchmarks/ 盘点（35 py + 2 json 基线 + 3 helper）

Helper：_baseline_policy.py（#618-31：新 workload 无基线禁 silent-skip，刷新需显式 opt-in）、baselines.json、transport_baselines.json。bench_* 独立脚本 6 个（pytest 不收集，smoke/信息性）。pytest 文件 24 个中 22 个挂 `perf` marker；指标分四类：确定性计数（mutation 字节、调用次数、work-count）、数值基线（baselines.json 1.75x-4x）、宽墙钟上限（lag<25ms/200ms）、内存（tracemalloc<128MB）。

**选型结论（P5）**：k6 被否——CI runner 无二进制、引入外置工具链破坏 pip-only 纪律、且现有 SSE 传输测试（test_transport_perf.py + transport_baselines.json）已是同型 Python 内驱模式；pytest-benchmark 插件被否——与既有 _baseline_policy opt-in 纪律重复且引入新依赖。**采纳**：`scripts/perf/` Python asyncio 内驱 harness（httpx 已是依赖）+ `perf/budgets.json`（指标→预算→依据→tool 标注），P95 统计自实现，容差 10%，刷新 budgets.json 必须显式改文件（PR 可审）。首批四条：SSE 50 并发、MVT P95、cube window P95、聊天首 token P95。

## 5. #1223–#1233 故障谱系（P7 选题输入）

| 类别 | PR | chaos 化 |
|---|---|---|
| alembic 多头 | #1223/#1224 | 低 |
| **broker 池/对齐**（celery 发布/worker 收听 db 错位、kombu closed pool） | #1226/#1228 | **高**：杀 worker/重启 redis |
| **隔离性/状态污染**（FK 种子、breaker 跨测污染、注册表残留、teardown 表缺失） | #1225/#1229/#1230 | **高**：乱序/脏进程 |
| env 卫生（conftest 未 pin 新 env） | #1225/#1229 | 中高 |
| oracle/预算漂移（基线过期、float 漂移、ratchet） | #1230-#1233 | 低（机制缺口：刷新需显式 opt-in——P5 budgets.json 按此设计） |
| 字体缺失（CJK .notdef） | #1230 | 中 |
| 平台版本（3.13 API on 3.12） | #1231 | 低 |
| **JWT 模块级 token 中途过期** | #1232 | **高**：时钟推进 |
| 语法/静态 | #1227/#1233 | 无 |
| 真实代码 bug（缺 await） | #1233 | 中（旅程可覆盖 layer data 路径） |

**前三类高频**：① 隔离性/状态污染（跨 3 PR）② broker/池生命周期（跨 2 PR，平均需 2+ 轮修复）③ 预算/oracle 漂移（跨 4 PR）。P7 三场景映射：杀 Celery worker→②；停 Redis→①/② 降级；断网重连 SSE resume→网络扰动（#371/#419 谱系）。JWT 过期（时钟冻结）列为扩展类。

**波次规律**：故障密度与合并批量正相关（#1223 自述 Post-#1187–#1196 unified CI 集中爆雷）——本 lane 的主要捕获窗口是「合并后 nightly 首跑」。
