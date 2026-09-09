"""Marketplace 装配：settings → RegistryService（进程内共享实例）。

Registry/trust store 是进程级单例：同一进程内的 API 面与 installer 共享
同一个 store/trust store 视图（吊销写回即时可见；跨进程靠文件锁 + 惰性
重读，见 distribution._current_trust_store）。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

from ..marketplace.service import PublishPolicy, RegistryService
from ..marketplace.store import RegistryStore
from ..trust_store import TrustStore

_LOCK = threading.Lock()
_SERVICE: Optional[RegistryService] = None
_CONFIGURED: Optional[str] = None


def registry_service_from_settings() -> Optional[RegistryService]:
    """EXTENSION_REGISTRY_DIR / EXTENSION_TRUST_STORE_PATH → 共享服务实例。

    未配置 → None（marketplace 关闭，API 404 语义）；配置非法 → typed
    异常（fail closed，宁可宿主起不来不带半份信任根运行）。
    """
    global _SERVICE, _CONFIGURED
    from app.core.config import settings

    registry_dir = (settings.EXTENSION_REGISTRY_DIR or "").strip()
    if not registry_dir:
        return None
    trust_path = (settings.EXTENSION_TRUST_STORE_PATH or "").strip()
    key = f"{registry_dir}|{trust_path}"
    with _LOCK:
        if _SERVICE is not None and _CONFIGURED == key:
            return _SERVICE
        trust_store: Optional[TrustStore] = None
        if trust_path:
            trust_store = TrustStore.load(Path(trust_path))
        service = RegistryService(
            RegistryStore(Path(registry_dir)),
            trust_store=trust_store,
            policy=PublishPolicy(
                # HTTP 面只读；publish 的 allowlist 在 CLI 层按运维输入构造。
                allowed_publishers=frozenset(),
            ),
        )
        _SERVICE = service
        _CONFIGURED = key
        return service
