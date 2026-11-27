# TEST_MATRIX — GOAL-20261127-01

> 每项：命令 | 范围 | 基线结果 | 改后结果 | 状态。skip/xfail/预存失败如实分列。

## 基线（S1 建档，改代码前跑）

| 命令 | 范围 | 基线 | 状态 |
|---|---|---|---|
| （待跑）pytest tests/unit/test_pi_event_mapper.py tests/unit/test_pi_v16x_diagnostics.py -q | 事件映射 | — | — |
| （待跑）pytest tests/unit/test_pi_decision.py tests/unit/test_pi_decision_wiring.py -q | #2283 决策面回归 | — | — |
| （待跑）pytest tests/unit/ -q -k "model_runtime or session_model" | 路由/会话模型权威 | — | — |
| （待跑）pytest tests/ -q -k governor | governor 权威 | — | — |
| （待跑）python scripts/pi_doctor.py | vendor/extension/rpc 六域 | — | — |

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
