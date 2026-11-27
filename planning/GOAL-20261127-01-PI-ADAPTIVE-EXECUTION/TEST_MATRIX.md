# TEST_MATRIX — GOAL-20261127-01

> 每项：命令 | 范围 | 基线结果 | 改后结果 | 状态。skip/xfail/预存失败如实分列。

## 基线（S1 建档，改代码前跑；2026-11-27 实测 @ f34cb69dd）

环境：本 worktree .venv（uv venv 3.12 + requirements.lock + requirements-dev.txt + httpx）；
`JWT_SECRET_KEY=ci-test-secret-key-32-chars-ok ENV=development LLM_API_KEY=test-key-not-real`。

| 命令 | 范围 | 基线 | 状态 |
|---|---|---|---|
| pytest tests/unit/test_pi_event_mapper.py tests/unit/test_pi_v16x_diagnostics.py tests/unit/test_pi_decision.py tests/unit/test_pi_decision_wiring.py -q | 事件映射+决策面 | **95 passed** | ✅ |
| pytest tests/unit/test_model_runtime_v2.py tests/unit/test_model_routing_bridge.py tests/unit/test_local_first_routing.py tests/governor -q | 路由/会话模型/governor | **254 passed, 1 failed**（test_backpressure_fairness::test_cumulative_accumulates_across_reservations） | ⚠️ 预存 |
| python scripts/pi_doctor.py | vendor/extension/rpc 六域 | （vendor build 后跑） | — |

### 预存失败诊断（非本线回归）

`test_cumulative_accumulates_across_reservations` 期望 session scope 累计
（5000+7000=12000），实现 `session_budget.py:277` 有意 keep-latest（commit
24655bf3a「stop prompt sums from lying」：整段 prompt 含历史重复计入会虚增
trip session cap，测试未跟上实现）。**测试陈旧**，与本线 WP3 预算路径直接
相关 → S3 顺带把测试对齐已文档化的实现语义（非弱化：实现语义有明确理由
注释与提交记录）。

## 本线新增（实测证据，2026-11-27）

| 旅程/用例 | 命令 | 结果 |
|---|---|---|
| F1 429×2→恢复四链证据（obs/retry/usage/health，真实 RPC） | pytest tests/integration/test_pi_provider_fixture_gate.py::test_provider_fixture_recovery_chain | ✅ passed |
| F2 持续 429 耗尽→诚实失败（stop=error/cost unknown） | 同文件 ::test_provider_fixture_exhaustion_chain | ✅ |
| F3 5xx→provider_unavailable 进退避 | 同文件 ::test_provider_fixture_500_matrix | ✅ |
| F4 401 观测诚实记录（unknown 词表） | 同文件 ::test_provider_fixture_401_no_blind_retry_signal | ✅ |
| F5 stream-cut 恰一次终态不伪成功 | 同文件 ::test_provider_fixture_stream_cut | ✅ |
| J3 能力/健康路由（硬资格+降级留痕+授权边界） | pytest tests/unit/test_pi_turn_strategy.py | ✅ 11 passed |
| 观测通道（解析/sink/桥接/泵） | pytest tests/unit/test_pi_provider_observation.py | ✅ 11 passed |
| attempt 链持久化/governor 回流/budget 投影 | pytest tests/unit/test_pi_turn_runtime_attempts.py | ✅ 7 passed |
| runtime 聚合投影 API | pytest tests/unit/test_session_runtime_view.py | ✅ 2 passed |
| J4 多会话（2 workers/4 sessions/40 turns，随机断连/取消风暴） | .venv/bin/python tests/benchmarks/bench_pi_bridge_pool_soak.py --workers 2 --sessions 4 --turns 40 --seed 42 | ✅ invariants PASS（0 泄漏/0 越序/0 未解释失败；wait p50=53.42ms p95=87.77ms；success 23/cancelled 17） |
| J5 既有回归（vendor contract/decision/event mapper/G37/strategy） | pytest tests/integration/test_pi_vendor_contract.py + 相关单测 | ✅ 18 passed；155 passed |
| J5 governor 权威回归 | pytest tests/governor | ✅ 209 passed |
| 安全负例 | pytest tests/unit/test_pi_strategy_security.py | ✅ 5 passed |
| vendor/extension/RPC 六域门 | python scripts/pi_doctor.py | ✅ 全 PASS（native） |
| 前端类型/检查 | pnpm exec tsc --noEmit; eslint components/chat/session-model-selector.tsx | ✅ 干净 |

## 旅程（G37 全栈 stub journey）与预存失败

| 项 | 结果 | 归因 |
|---|---|---|
| PI_JOURNEY_E2E=1 pytest tests/integration/test_pi_g37_session_model_journeys.py（默认 env） | 两次确定性 boot 失败（app/bridge 90s 未就绪，99.5s） | **master 预存 P1 缺陷**：app 启动即死于 runtime manifest 严格校验 —— 14 个 dangling capability_id（webgis_diagram_symbol_* → map_design；webgis_scenario_* → gis_scenario_harness；webgis_*_paper_scale_generalization → cartography_paper_scale_*），全部来自 #2274/#2275/#2287 已合并线，CapabilityRegistry 无对应声明（原始分支 worktree 同样无声明 → 引入即悬空，#2288 整合遗漏）。复现：本 worktree 直接 boot probe（`RuntimeError: Compiled GIS Runtime Manifest has fatal issues`）；这些模块与 origin/master **零 diff** → master 同样失败。**不修**（04/05 线能力语义域，边界纪律）；journey 证据改用文档化降级开关 `GIS_MANIFEST_STRICT=0`（runtime_manifest.py:645）+ PR 正文显著披露 |
| PI_JOURNEY_E2E=1 … GIS_MANIFEST_STRICT=0 | （运行中，结果回填） | — |
| 前端全量 vitest | 50 failed / 6394 passed | 干净主 worktree 同失败（map-panel.cartography-race 14/14 同红）→ master 预存，非本线 |

## 资源纪律记录

- 本 worktree .venv（uv，python 3.12，requirements.lock + dev）；frontend pnpm --frozen-lockfile
- 测试并发：默认串行 + OMP/OPENBLAS=2；vendor build 单进程；未使用全局缓存清理
- 端口：mock provider/stub/uvicorn 均 OS 分配随机端口；无固定端口占用
