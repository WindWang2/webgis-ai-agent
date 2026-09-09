# 测试矩阵与结果（本地验证 Phase D）

## V6 新增测试
| 文件 | 用例数 | 覆盖 |
|---|---|---|
| tests/unit/test_geocompute_v6_contracts.py | 16 | 状态机白名单/fencing 词表/优先级封闭/worker 能力 + budget 穿透（P0-3）|
| tests/unit/test_geocompute_v6_store.py | 20 | CAS 认领互斥/epoch fencing/reclaim 预算/取消旗标幂等/leadership failover/账本 enforcing+advisory+钳零守恒/worker 注册剪枝 |
| tests/unit/test_geocompute_v6_scheduler.py | 10 | submit→terminal 端到端/双 coordinator 无双重执行/kill scheduler 恢复/attempt 预算/分布式取消（在跑+排队）/安全点抢占/通道匹配 HoL 削减/公平轮转可重建 |
| tests/unit/test_geocompute_v6_routes.py | 12 | REST submit(202/401/422/413/429)/list 合并去重/get 三级回退+读隔离/cancel 跨进程幂等/metrics admin |
| tests/unit/test_geocompute_v6_chaos.py | 9 | kill scheduler/延迟心跳/重复投递/stale epoch/DB 瞬断/账本崩溃清理/取消-终态竞态/双旗标/kill worker 通道收缩 |
| tests/perf/test_geocompute_v6_perf.py | 4 | B1 tick 查询数 O(批量)（5q→16 / 200q→2）/ B2 pick 线性（ratio 8.2≤30）/ B3 派发 ≤2 tick / B4 inflight ≤ slots |

## 回归
- 全部 geocompute（V5+V6）：347+ passed（`pytest tests/unit -k geocompute`）
- V5 关键面：v5_scheduler(26)/durable(4+)/executor_v4/execution/authz(26)/routes/governance/chaos/boundary(AST)
- OpenAPI：快照再生成（+185 additive 行），确定性 + 破坏性检测 12 passed
- Migration：0032 up/down/up 可重入；drift guard（模型↔迁移逐列）passed
- jobs 子系统回归（durable job 语义未被破坏）：test_geocompute_durable + tasks 穿透测试 passed

## 结果记录
- 结构性预算全部 gate；wall-clock 仅 [INFO timing]（B1: 16/2 queries, B2: 8.2x ratio）。
- 观察到的既有失败（与本 Epic 无关，master 同样存在）：无 —— 数据库文件环境差异已用主 worktree
  开发库对齐（test_geocompute_authz 两个 seed 全局库的用例）。
