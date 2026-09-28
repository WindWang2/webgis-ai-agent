"""Dataset Semantics Qualification Bridge —— 资格裁决的 descriptor 供给缝（H08）。

消费面（``qualify_workflow_data_roles`` 的 descriptor/freshness 参数）早已
在库面就绪；本桥把「session 里现在有哪些语义身份」解析成它的供给：

- ``primary_session_descriptor``：session refs → 语义 store 顺序读
  （O(refs)，零扫描）→ 首个 ok descriptor（确定性：list_refs 顺序）；
- ``descriptor_evidence``：descriptor + 期望指纹 → 资格参数形状
  （``{descriptor, expected_descriptor_fingerprint}``），期望指纹缺席 =
  空串（freshness guard 诚实不激活，绝不用猜测的指纹对账）。

纯读面：任何失败 → None/空（诚实缺席），绝不阻断编译主链。
"""
from __future__ import annotations

from typing import Any, Optional


async def primary_session_descriptor(
    session_id: str,
    *,
    max_refs: int = 8,
) -> Optional[Any]:
    """session 内首个带 descriptor 的 ref → GISDatasetDescriptor（或 None）。

    refs 上限 ``max_refs``（有界读；多数据 session 取 list_refs 首个命中
    —— 与 compile 期单 profile 语义一致，诚实不猜第二个）。
    """
    if not session_id:
        return None
    from app.services.dataset_semantics import get_dataset_semantic_store
    from app.services.session_data import session_data_manager

    try:
        refs = list((await session_data_manager.list_refs(session_id)).keys())
    except Exception:  # noqa: BLE001 — ref 清单缺席按无 descriptor
        return None
    store = get_dataset_semantic_store()
    for ref in refs[:max_refs]:
        try:
            rec = await store.get(session_id, ref)
        except Exception:  # noqa: BLE001 — 单 ref 读失败跳过
            continue
        if rec.ok and rec.descriptor is not None:
            return rec.descriptor
    return None


def descriptor_evidence(
    descriptor: Optional[Any],
    expected_descriptor_fingerprint: str = "",
) -> dict:
    """descriptor + 期望指纹 → 资格消费面参数形状（有界、确定性）。"""
    return {
        "descriptor": descriptor,
        "expected_descriptor_fingerprint": str(
            expected_descriptor_fingerprint or "")[:96],
    }


__all__ = [
    "primary_session_descriptor",
    "descriptor_evidence",
]
