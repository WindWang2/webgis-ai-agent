"""Dataset Semantics Context Bridge —— descriptor 指纹进入 context/恢复面。

ADR-0215 D7：context reuse 与 dataset fingerprint 的联动缝。recovery
durable facts 的 ``source_fingerprints`` 条目（{ref, fingerprint}）在此
获得语义维度升级（+descriptor_fingerprint）：

- ``augment_source_fingerprints``：纯函数 —— 给既有 recovery 条目补
  descriptor 指纹（有界 ≤16；缺席不虚构）；
- ``descriptor_fingerprints_for_session``：async —— 从语义 store 解析
  session 内各 ref 的当前指纹（消费方：mission recovery / map digest
  builder；热区文件零改动，经本桥供数）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

#: recovery source_fingerprints 投影上限（与 context_layers data 域同界）。
MAX_TRACKED_REFS = 16


def augment_source_fingerprints(
    entries: Any,
    fingerprints_by_ref: Dict[str, str],
) -> List[Dict[str, str]]:
    """recovery 条目 ⊕ {ref → descriptor_fingerprint} → 升级条目（纯函数）。

    ``entries`` 是既有 source_fingerprints 形状（list[{ref, fingerprint}]）；
    只有 ref 命中 ``fingerprints_by_ref`` 的条目获得
    ``descriptor_fingerprint`` 键 —— 其他条目原样（诚实缺席）。
    """
    if not isinstance(entries, list):
        return []
    out: List[Dict[str, str]] = []
    for e in entries[:MAX_TRACKED_REFS]:
        if not isinstance(e, dict):
            continue
        ref = str(e.get("ref") or "")[:64]
        item: Dict[str, str] = {
            "ref": ref,
            "fingerprint": str(e.get("fingerprint") or "")[:32],
        }
        dsd = fingerprints_by_ref.get(ref)
        if dsd:
            item["descriptor_fingerprint"] = str(dsd)[:96]
        out.append(item)
    return out


async def descriptor_fingerprints_for_session(
    session_id: str,
    refs: Optional[List[str]],
) -> Dict[str, str]:
    """refs → {ref → 当前 descriptor 指纹}（store 顺序读，O(refs)）。

    读取失败的 ref 静默缺席（bridge 是 additive 证据面；调用方按
    DESCRIPTOR_MISSING 披露，不阻断）。
    """
    if not refs:
        return {}
    from app.services.dataset_semantics.store import get_dataset_semantic_store

    store = get_dataset_semantic_store()
    out: Dict[str, str] = {}
    for ref in list(dict.fromkeys(str(r) for r in refs))[:MAX_TRACKED_REFS]:
        if not ref:
            continue
        try:
            rec = await store.get(session_id, ref)
        except Exception:  # noqa: BLE001 — additive 证据面不阻断
            continue
        if rec.ok and rec.descriptor is not None:
            fp = rec.descriptor.descriptor_fingerprint
            if fp:
                out[ref] = fp
    return out


__all__ = [
    "MAX_TRACKED_REFS",
    "augment_source_fingerprints",
    "descriptor_fingerprints_for_session",
]
