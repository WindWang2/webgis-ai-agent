# GeoCompute V7 — 测试矩阵与本地验证记录

## 新增测试（V7）

| 文件 | 数量 | 覆盖 |
|---|---|---|
| tests/unit/test_geocompute_v7_core.py | 29 | capability 探针/满足判定/JSON 钳制；placement eligible/rank/owner 打分；events 词表/预算豁免/fail-open/进度投影/TTL；locality 键派生/owner 隔离/LRU/TTL/损坏行 miss；worker 载荷缓存 owner 隔离/超界跳过/注册失败放行；ResourceRequest 边界（extra=forbid/上界/词表过滤）；resource_class→队列路由回归锚 |
| tests/unit/test_geocompute_v7_cluster.py | 15 | submit resource（缺省=V6/union/422/钳制）；events 端点（owner 隔离/分页/404/401）；admin 四端点（admin-only/stuck→reset 全流程/attempt 耗尽→WORKER_LOSS/ledger 词表）；scheduler gating（GPU 无合格 worker 留队+waiting_resource 一次性/GPU 上线派发/无 envelope 恒派发=V6）；purge 级联删 events；bridge（相对加速比>2/同线程护栏/类型化超时） |
| tests/perf/test_geocompute_v7_perf.py | 6 | P1 tick 查询常数上界（5 vs 60 queued）；P2 事件 append/window O(1)；P3 进度投影有界；P4 100/1000 节点链 settle==N（1000 按计划预算拆 4 plan）；P4b 同步路径零事件写 |
| tests/unit/test_geocompute_v7_real_broker_e2e.py | 5 | real broker 全链/crash→stale→重派/跨进程取消/重复投递守卫/worker 重连（`REAL_SERVICES=1` 自跳过纪律） |

## 本地验证记录（真实执行结果）

### V6/V5 回归（改动后全绿）
- `tests/unit/test_geocompute_v6_{contracts,store,scheduler,chaos,review_fixes,round2,routes}.py`：**78 passed**
- `tests/unit/test_geocompute_{v5_scheduler,durable,execution,authz}.py`：**86 passed**
- `tests/unit -k geocompute` 全量：**404 passed, 1 skipped**

### V7 新增
- core + cluster + perf：**50 passed**
- OpenAPI 快照：`API_SNAPSHOT_UPDATE=1` 再生成（310 行 additive，非 breaking）
- ruff（改动文件全量）：**0 errors**

### real-services（真实 Redis + 生产 celery worker 子进程 + WAL SQLite 控制面）
- happy path（两节点全 durable 链含 input handoff）：**PASS**（本地多次验证）
- crash→stale→重派 / cancel / dup / restart：见 03-progress.md 最新记录
- 离线 self-skip：**5 skipped**（无 REAL_SERVICES=1 时诚实跳过，不伪装）

## 与 Epic §15「必须测试」对照

| Epic 要求 | 状态 | 位置 |
|---|---|---|
| two coordinators | V6 已有 + V7 回归全绿 | test_geocompute_v6_scheduler/chaos |
| stale epoch cannot finish | V6 已有，回归全绿 | test_geocompute_v6_chaos |
| worker dies after output before ACK | E2E dup 场景 + 入口守卫单测 | test_geocompute_v7_real_broker_e2e |
| duplicate task | E2E dup（AlreadyFinished 断言 + 输出计数） | 同上 |
| cancel from another API process | E2E cancel（持久旗标跨进程） | 同上 |
| high priority preemption | V6 已有，回归全绿 | test_geocompute_v6_scheduler |
| quota enforcement | V6 账本已有 + V7 admin limits 端点 | test_geocompute_v7_cluster |
| GPU requirement no eligible worker | V7 单测（gating 留队+事件+上线派发） | test_geocompute_v7_cluster::TestSchedulerGating |
| DataObject cache corrupt | 注册表损坏行 miss 单测 | test_geocompute_v7_core::TestLocality |
| owner mismatch | 缓存键 owner 隔离单测（put/get/打分三处） | 同上 |
| broker outage/recovery | E2E restart（kill→prune→新 worker 注册→新 run 完成） | E2E |
| Redis outage per fail policy | 既有（#386 socket 超时降级），V7 未改动该路径 | test_real_services_smoke |
| coordinator restart | V6 reclaim + V7 events 续接（seq 不重置） | V6 chaos 回归 |
| run resume | V6 reuse_index 跨进程 + 回归 | V6 round2 回归 |
| 100/1000 small nodes structural | P4（settle==N，计划预算拆分） | perf |
| bounded event/trace memory | 分层预算 + 超限丢弃 + TTL 单测 | core |

## 已知边界（诚实披露）

- Postgres 不可达（本地）：E2E 控制面用共享 SQLite（WAL + busy timeout）。
  CAS 全部单语句条件 UPDATE，SQLite/PG 同语义；CI real-services lane
  （postgis+redis 容器）可复跑同一测试文件。
- 节点级放置是「run 级强制 + worker 守卫有界收敛」，非消息级硬绑定
  （ADR-0119 D1 诚实边界）。
