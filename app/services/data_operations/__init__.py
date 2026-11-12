"""G23 — 数据操作的持久任务运行基础（durable data operations）。

把导入（data_import）与字段计算（field_compute）的进程内执行接入既有
durable jobs 事实源（analysis_tasks，ADR-0052），复用 G18 数据交付确立的
「DB 事实源 + 进程内 runner」范式（不新建任务平台、不建第二张表）：

  * 同一 job、同一输入快照引用、同一收据与同一取消结果对 UI 与 Pi 可见；
  * 跨进程查询/取消：任意 API 副本经 ``/api/v1/tasks/jobs`` 与领域端点读取
    同一 ``analysis_tasks`` 行；取消落 ``cancel_requested_at`` 持久事实，
    执行进程的看门狗轮询后点燃本地 token；
  * fencing：终态写入携带 ``worker_id + attempt``（#1398），丢租约/被重试
    抢占的迟到执行不能改写终态；
  * 恢复：批次/计划状态归一化以「活跃 durable job」为判据（进程内存不再
    是唯一在飞证据）。

公共入口：
  * :class:`AsyncJobHandle` / :func:`async_durable_job` — asyncio 执行体包装
  * :func:`submit_async_job` — 幂等创建 + 认领 + 启动 runner 的统一提交面
"""
from app.services.data_operations.runner import (
    AsyncJobHandle,
    AlreadyFinished,
    async_durable_job,
    find_active_jobs,
    run_async_job,
    submit_async_job,
)

__all__ = [
    "AsyncJobHandle",
    "AlreadyFinished",
    "async_durable_job",
    "find_active_jobs",
    "run_async_job",
    "submit_async_job",
]
