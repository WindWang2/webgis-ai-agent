# 实现进度

## 已完成 waves
- wave 1（commit cabefbc）：cluster/contracts.py 状态机白名单 + lease/epoch/worker/claim 契约（14 tests）
- wave 2（commit 22807ff）：db_model 三表 + migration 0032（可重入，drift guard 通过）+ ClusterRunStore CAS + ClusterLedger（20 tests）
- wave 3-5（本次 commit）：executor run_id/yield_check/owner_scope_override 注入（默认行为不变）；plan.PREEMPTED；
  cluster/scheduler.py ClusterCoordinator（leadership epoch 仲裁/续约 fencing、reclaim、cancel sweep、
  fairness 派发、心跳 watchdog 线程、fenced 终态、安全点抢占）；cluster/fairness.py（可重建轮转 + 通道匹配）；
  REST 之前的核心全部落地（10 tests）。全量 geocompute 回归 347 passed。

## 审计外新发现（已修复）
- **P1（既有 bug，V6 round0 发现）**：run_evidence.py / reuse_index.py 的
  `_default_session_factory` 返回 sessionmaker 而非 Session —— SQLAlchemy 2.0 的
  sessionmaker 无上下文协议，`with session_factory()` 必然 TypeError → 生产默认路径的
  run 终态证据快照与复用记录被 fail-open **静默丢弃**（V5 测试因注入工厂未暴露）。
  已修复为返回 `SessionLocal()`（jobs 层同一纪律）。

## 环境注记
- 新 worktree 缺 `data/webgis.db` 开发库（test_geocompute_authz 两个用例 seed users 到
  全局库）→ 从主 worktree 复制；非代码问题。

## 待做
- wave 6：tasks.py P0-3（worker 侧 budget 注入）
- wave 12：REST submit/list/cancel 升级 + cluster metrics + OpenAPI 快照
- wave 11：chaos corpus 测试文件
- wave 13：结构性 perf 预算测试

## 追加（waves 6-13）
- wave 6（P0-3）：plan budget 经 durable task_kwargs 穿透 worker（不进 params —— 幂等键不变），
  worker 侧 OperatorContext 恢复 ResourceBudget（2 tests）。
- wave 7：cluster/workers.py —— celery signals（worker_ready/shutdown）+ 心跳 daemon + profile 槽位 env。
- wave 12：REST submit(202/413/429/422)/list(行∪快照去重)/get(三级回退)/cancel(两级幂等)/metrics(admin 503)
  + run_evidence.list_snapshots + cluster/metrics.py（封闭词表）+ OpenAPI 快照再生成（185 行 additive）。
- wave 11：chaos corpus 9 tests（kill scheduler/延迟心跳/重复投递/stale epoch/DB 瞬断/账本崩溃清理/
  取消竞态/双旗标/worker 通道收缩）。
- wave 13：结构性 perf 预算 4 tests（B1 tick 查询数 O(批量)：5 queued=16q / 200 queued=2q；
  B2 pick 线性 ratio 8.2；B3 派发 ≤2 tick；B4 inflight ≤ slots）。
- 修复：cluster/store 默认工厂同款 sessionmaker bug；routes 对未迁移库 fail-open（V5 语义回退）。
