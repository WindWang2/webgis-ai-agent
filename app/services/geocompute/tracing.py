"""执行可观测性（ADR-0096 D7）：结构化 trace 事件，无新依赖。

复用既有 RuntimeContext（correlation ids）+ 标准库 logging；事件是**有界
元数据**（状态迁移、计数、时长），绝不携带特征载荷/凭据/原始用户数据。
字段语义保持 OpenTelemetry 兼容（ Span 概念的平面化投影），后续接 OTel
SDK 时可直接映射，不必改事件源。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from typing import Any, Optional

logger = logging.getLogger("webgis.geocompute.trace")

#: 进程内最近事件环形缓冲（诊断/测试观测用；有界，绝不无限增长）。
_RING_SIZE = 1024
_ring: deque[dict[str, Any]] = deque(maxlen=_RING_SIZE)
_ring_lock = threading.Lock()


def emit(
    event: str,
    *,
    run_id: str,
    node_id: Optional[str] = None,
    plan_fingerprint: Optional[str] = None,
    status: Optional[str] = None,
    duration_s: Optional[float] = None,
    rows: Optional[int] = None,
    error_code: Optional[str] = None,
    **fields: Any,
) -> None:
    """记录一条执行 trace 事件。fields 里出现的敏感键会被丢弃。"""
    record = {
        "ts": time.time(),
        "event": event,
        "run_id": run_id,
        "node_id": node_id,
        "plan_fingerprint": plan_fingerprint,
        "status": status,
        "duration_s": round(duration_s, 6) if duration_s is not None else None,
        "rows": rows,
        "error_code": error_code,
    }
    for k, v in fields.items():
        if k in ("payload", "features", "rows_data", "credentials", "token", "sql"):
            continue  # 红线：载荷/凭据绝不进入 trace
        if v is not None:
            record[k] = v
    with _ring_lock:
        _ring.append(record)
    logger.info(json.dumps(record, ensure_ascii=False, default=str))


def recent_events(limit: int = 100) -> list[dict[str, Any]]:
    """读取最近事件（诊断端点/测试断言用；有界）。"""
    with _ring_lock:
        items = list(_ring)
    return items[-max(0, min(limit, _RING_SIZE)):]
