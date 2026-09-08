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
