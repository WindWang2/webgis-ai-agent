# app/tasks —— Celery 任务模块

## 现状（V9 P7 结构债盘点结论：保留并文档化）

- `explorer/task_chain.py` —— Explorer 的 Celery 任务链（worker prefork
  持久事件循环 + session store 存取）。**不是死代码**：活跃消费方为
  `app/services/explorer/orchestrator.py`（`chain(...).apply_async()`）与
  celery include `app/services/task_queue.py`；测试
  `tests/test_critical_backend_correctness.py`。
- V9 新增的 durable job 任务模块不放在本包（遵循各自的领域归属）：
  - 数据质量评估 → `app/services/data_quality/jobs.py`
  - GC 计划执行 → `app/services/data_lifecycle/jobs.py`
  （两者都注册进 `app.services.task_queue.celery_app.include`。）

## 约定

新 Celery 任务优先落在**领域服务包**内（`app/services/<domain>/jobs.py`），
经 `celery_app.include` 注册；`app/tasks/` 只保留跨领域/遗留的链式任务，
不再新增散件。
