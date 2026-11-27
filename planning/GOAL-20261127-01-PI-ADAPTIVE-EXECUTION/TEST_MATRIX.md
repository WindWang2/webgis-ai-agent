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

## 本线新增（S2 起逐项填）

| 旅程/用例 | 命令 | 期望 | 结果 |
|---|---|---|---|
| J1 429 注入→恢复→不重复副作用 | （S3 定） | route/attempt/job/revision 串证，费用不双计 | — |
| J2 长任务取消→终态/续跑义务 | （S3 定） | Pi 与 job 终态一致，地图保留 | — |
| J3 能力不匹配→硬资格过滤 | （S2 定） | typed 拒绝/降级留痕 | — |
| J4 双 owner 并发隔离 | （S5 定） | 无串 trace/数据/结算 | — |
| J5 既有诊断/schema/compaction 回归 | 基线表 | 不回退 | — |

## 资源纪律记录

- （登记端口/缓存/并发参数）
