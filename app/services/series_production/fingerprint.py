"""Series Production 指纹 —— 与 MapSpec store 同算法（.fp sidecar 一致性）。

冻结 pin 的 ``mapspec_fingerprint`` 必须与 ``mapspec/store.py`` 的
canonical-JSON SHA256 **逐字节同算法**，否则冻结对账会把每个成员都误判
stale。因此这里显式委托 store 的实现（``_fingerprint_sync``，单次
O(spec bytes)；调用方已在线程内），不另造第二指纹实现。
"""
from __future__ import annotations

from typing import Any, Dict


def document_fingerprint(document: Dict[str, Any]) -> str:
    """MapSpec 文档 → store 同款指纹（对账用；线程内调用）。"""
    from app.services.mapspec.store import _fingerprint_sync

    return _fingerprint_sync(document)


__all__ = ["document_fingerprint"]
