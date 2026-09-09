# 两轮独立 Review —— findings 与修复

两轮均为独立 subagent（架构/正确性 + 性能/安全/UX），对同一 diff（445ad30..branch）
背靠背审查。两位 reviewer 在三个最高危问题上完全收敛。

## Round 1（架构/正确性/回归）— BLOCKER 2 / CRITICAL 2 / MAJOR 6 / MINOR 9
## Round 2（性能/安全/UX/可维护性）— BLOCKER 1 / CRITICAL 3 / MAJOR 7 / MINOR 11

## 已修复（全部 BLOCKER / CRITICAL / 影响正确性的 MAJOR）

| # | 级别 | 问题 | 修复 |
|---|---|---|---|
| B1 | BLOCKER（双轮收敛） | coordinator 从未接线 —— submit 202 后 run 永远 queued，全部调度面是死代码 | main.py lifespan 按 env 启动 `get_coordinator().run_forever`（daemon 线程）+ shutdown `stop()`；新增 REST→terminal 端到端行为测试 |
| B2/C1(R2) | BLOCKER/CRITICAL | `yield_requested_at` requeue 后不清 → 心跳再点燃 → 抢占自噬 livelock（reviewer 以探针复现 15 个自旋周期） | `requeue_preempted` 同 CAS 清旗标；实现 PREEMPT_EXHAUSTED 保险丝（preempts ≥ MAX → failed）；回归测试 |
| C1/C2(R2) | CRITICAL | PREEMPTED 转移跳过账本归还 + requeue 覆写 reserved_* → enforcing 模式抢占循环泄漏配额 → 全集群死锁 | 预留生命周期=lease 生命周期：PREEMPTED 同事务归还；cancel_flagged 收敛路径复用；账本守恒回归测试 |
| C3(R2) | CRITICAL | durable.py / reuse_index.py 默认 session 工厂仍是坏的（durable 返回未调用的函数对象 —— V5 既有生产 bug，await 轮询必 TypeError） | 三处工厂统一返回 Session 实例；CHANGELOG 如实更正 |
| C2(R1) | CRITICAL | 默认兜底队列 "celery" 进入 required_profiles → 非 eager 下该类 run 永久卡 queued | submit 侧推导与 EXECUTION_QUEUE_PROFILES 求交集；测试锁定 |
| M1(R1) | MAJOR | `_ensure_scope_row` 吞 flush 异常 → session 中毒（PendingRollbackError 炸掉 claim/finish） | scope 行预建移到业务事务外（`ensure_scopes` 独立短事务 + factory 透传），事务内只读检查；`_finish` 兜底 except 防线程死亡 |
| M2(R1) | MAJOR | migration 0032 缺模型声明的 3 条 CHECK 约束 | create_table 内联（SQLite/PG）+ 既有表 batch_alter（repo a1b2c3d4e5f6 惯例）；up/down/up 复验 |
| M3(R1/R2) | MAJOR | `record_cancellation_latency` 零调用 —— cancel_latency 指标恒 null | cancel sweep 与心跳路径两处打点；测试断言非空 |
| M4(R1) | MAJOR | cancel 本地命中分支不落持久旗标 → 执行进程崩溃后取消丢失、整 run 重跑 | 本地命中先持久旗标（幂等 no-op 安全）再点本地 token |
| M5(R1) | MAJOR | fair_pick 重算 remaining + 取模 → 租户队列中途耗尽时系统性跳位 | 固定环游标（空队列跳过不移位）；非均匀深度测试 |
| M6(R1) | MAJOR | tick 中破坏性 sweep 先于 leadership 续约校验 | renew-first 重排（续约失败立即卸任） |
| M1(R2) | MAJOR | geocompute_runs 无 GC + tick 全表聚合随历史恶化 | `purge_terminal` retention（env 可配，每 tick 有界批，PG 可移植）；`tenant_last_dispatch` 只聚合非终态行 |
| M2(R2) | MAJOR | `WEBGIS_CLUSTER_PREEMPT_WAIT_S` 文档有代码无 | get_coordinator 读取（+ retention env） |
| M4(R2) | MAJOR | 抢占受害者可选到远端 coordinator 的 run（不释放本地槽位，白罚一次重跑） | 受害者仅限本 coordinator 的在跑 run |
| M5(R2) | MAJOR | store 不可用被伪装成 404/空列表 | typed 503 CLUSTER_UNAVAILABLE（与 metrics 503 同语义） |
| m1(R1)/m1(R2) | MINOR | GET run 投影缺 cancel_requested_at（取消不可见）；request_cancel 终态 TOCTOU | 投影补字段；UPDATE 加状态 guard |
| m2(R1) | MINOR | created_at 按服务器本地时区解释 | 显式 UTC |
| m3(R1) | MINOR | project_key 双重哈希 → cluster 与同步路径治理 scope 不互通 | run 行存原始 project_id（analysis_tasks 同纪律），scheduler 用原始值 |
| m4(R1) | MINOR | submit 字段混入同步 execute 契约 | 拆分 ClusterSubmitRequest |
| m3(R2) | MINOR | claim 可认领已取消排队的 run | claim CAS 排除 cancel_requested_at 非空行 |
| m6(R2) | MINOR | 用户投影暴露 coordinator_id（hostname:pid 拓扑） | 用户投影剔除，控制面投影保留 |
| m8(R2) | MINOR | consume_* 死代码 | 删除 + 测试更新 |
| m9(R2)/m9(R1) | MINOR | 注释与事实不符（FK/类型） | 注释更正 |
| m6(R1) | MINOR | mark_running 失败 → leased 僵尸等 30s reclaim 白记 attempt | 立即 finish_run(FAILED[CLAIM_RACE])（同事务归还账本） |

## 记录不修（有据取舍）
- m4(R1) 背压 count-then-insert 有界 TOCTOU：docstring 已如实标注（与账本 advisory 同级取舍）。
- m10(R2) standby coordinator 行 180s prune churn：无害有界。
- M6(R2) session 工厂 4 处 seam 收敛到共享 helper：触及共享模块，超出本 Epic ownership → follow-up。
- M7(R2) 抢占等待从 created_at 起算：local-only 受害者修复后语义自洽；文档补 known limitation。
