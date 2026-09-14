"""Provider 健康只读视图（R11，ADR-0182 D2）。

Data Fabric 的 per-source 熔断器（``CircuitBreakerRegistry``）与 provider
health 是**既有 owner**——governor 绝不重建 breaker，只消费其状态作为
admission 输入。所有读取防御式：owner 故障 → 返回 unknown（admission
按无该输入处理，fail-open 纪律）。
"""
from __future__ import annotations

import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)

#: 状态归一化词表（对齐 CircuitState；unknown = 视图不可用）
HEALTH_UNKNOWN = "unknown"
HEALTH_CLOSED = "closed"
HEALTH_OPEN = "open"
HEALTH_HALF_OPEN = "half_open"


class HealthView:
    """熔断/provider 健康的只读投影。"""

    def __init__(self, breaker_registry: Optional[object] = None):
        # None = 进程默认 registry（延迟解析，避免 import 期耦合）
        self._registry = breaker_registry

    def _reg(self):
        if self._registry is not None:
            return self._registry
        try:
            from app.services.data_fabric.circuit_breaker import get_breaker_registry
            return get_breaker_registry()
        except Exception:  # noqa: BLE001 — owner 不可用 → unknown
            return None

    def provider_state(self, source_key: str) -> str:
        """单 provider 状态（closed/open/half_open/unknown）。"""
        reg = self._reg()
        if reg is None or not source_key:
            return HEALTH_UNKNOWN
        try:
            state = reg.state(source_key)
        except Exception:  # noqa: BLE001
            logger.debug("health view state read failed for %s", source_key,
                         exc_info=True)
            return HEALTH_UNKNOWN
        try:
            return str(getattr(state, "value", state))
        except Exception:  # noqa: BLE001
            return HEALTH_UNKNOWN

    def is_open(self, source_key: str) -> bool:
        return self.provider_state(source_key) == HEALTH_OPEN

    def degraded_sources(self, source_keys) -> Dict[str, str]:
        """批量投影（bounded 输入由调用方保证；观测/admission 用）。"""
        return {k: self.provider_state(k) for k in source_keys}


__all__ = [
    "HEALTH_UNKNOWN",
    "HEALTH_CLOSED",
    "HEALTH_OPEN",
    "HEALTH_HALF_OPEN",
    "HealthView",
]
