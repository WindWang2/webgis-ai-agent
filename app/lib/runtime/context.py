"""RuntimeContext re-export shim（ADR-0216）。

实现本体下沉 ``app/core/runtime_context.py``（ContextVar 关联原语是进程级
基础件，core 的日志过滤器消费它，core 不得反向 import lib）。本 shim 保持
全部既有 import path。
"""
from app.core.runtime_context import *  # noqa: F401,F403
from app.core.runtime_context import (  # noqa: F401
    RuntimeContext,
    bind_runtime_context,
    current_runtime_context,
    new_request_id,
    new_run_id,
    new_turn_id,
    runtime_context_snapshot,
)
