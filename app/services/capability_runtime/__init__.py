"""Capability Runtime vNext(H05):provider 健康 / 断路器 / 凭据租约 /
运行时合成快照的生产包。

模块边界:
- :mod:`health` —— tool-provider 健康状态机(断路器/半开/typed 失败);
- :mod:`snapshot` —— capability 运行时合成投影(五态词表);
- secret/lease 契约在 lib 层(:mod:`app.lib.secret_lease`),本包不重复。
"""
from app.services.capability_runtime.health import (  # noqa: F401
    ProviderFailureClass,
    ProviderHealthRegistry,
    ProviderHealthState,
    ProviderHealthVerdict,
    classify_failure,
    get_provider_health_registry,
    provider_health_enabled,
    provider_health_factor,
    set_provider_health_registry,
)
from app.services.capability_runtime.snapshot import (  # noqa: F401
    CapabilityRuntimeFact,
    CapabilityRuntimeSnapshot,
    ProviderRuntimeFact,
    build_capability_runtime_snapshot,
)
