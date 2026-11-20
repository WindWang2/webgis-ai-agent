# G23 RECON — 测试现状（调查员报告）

pytest.ini：asyncio_mode=auto、timeout=180、markers heavy/perf/cartography/real_services。

## T1. data_import（tests/data_import/）
fixtures.py（真实文件构造器）；test_api_routes(5)/test_vertical_slice(3)/test_containers(5)/test_tables(7)/test_resilience(6：重启恢复:113、取消无晚到成功:51、惰性过期:192)/test_fault_injection(3)/test_review_fixes(8)/test_tools_dispatch(4)。运行 `pytest tests/data_import`。

## T2. field_compute
tests/data/test_field_compute_v1.py(39)/_api_v1.py(8)/_review_v1.py(25：取消/回滚/幂等升级)；tests/unit/test_field_compute_tools_v1.py(6)。

## T3. jobs（tests/jobs/ 9 文件）
test_job_lifecycle（状态机不变式）/test_job_store（幂等/原子迁移/并发 cancel vs complete 单终态/sweep_stale:508）/test_job_worker（认领/watchdog 推取消不依赖 body 轮询/late-success/原子产物/重复投递跳过）/test_job_cancellation/test_job_api/test_job_agent_bridge/test_job_celery_e2e（eager 仿真，`_celery_offline`:31-41 钉死 eager+memory）/test_job_redaction_progress/test_job_migration。基准 tests/benchmarks/test_job_runtime_perf.py。

## T4. G18 验证范式（tests/delivery/——G23 照抄）
conftest.py `delivery_env`(:17-40)：**双引擎 SQLite（sync+async）+ temp DATA_DIR + 内存 session store、USE_REDIS=false、CELERY_BROKER_URL=memory://**。test_service_flow(23：幂等重提复验:跨 owner 404)/test_recovery_faults（**模拟重启=清 `_ACTIVE_RUNNERS`+registry → job/回执仍可恢复 :139-151**、取消中途无半文件:38、crash 干净失败:81、行数帽:156）/test_routes_tools/test_writers_roundtrip/test_review_fixes。

## T5. 多进程先例
- 无真正多进程 job 测试（grep 证实）。
- 并发正确性=进程内并发+DB 原子条件更新；跨进程真拓扑仅 real_services lane（真 PG+Redis+worker）。
- xdist：tests/conftest.py:229-256 每 worker 独立临时 SQLite。

## T6. G23 测试对接点
- 复用 delivery_env 范式；test_resilience 重启/取消用例改造为"清 runner/registry+job 行仍在"断言。
- 新增跨实例（两 service 对象/两进程）读路径不误判 COMMITTING 回归。
- compute 以 test_field_compute_review_v1 取消/回滚为基线，追加 job 终态与收据一致性断言。
- 双 uvicorn 集成脚本（真实进程，非对象模拟）为 P7 核心证据。
