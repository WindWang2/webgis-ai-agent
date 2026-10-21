"""Dataset Semantics Qualification Bridge —— 资格裁决的 descriptor 供给缝（H08）。

消费面（``qualify_workflow_data_roles`` 的 descriptor/freshness 参数）早已
在库面就绪；本桥把「session 里现在有哪些语义身份」解析成它的供给：

- ``primary_session_descriptor``：session refs → 语义 store 顺序读
  （O(refs)，零扫描）→ 首个 ok descriptor（确定性：list_refs 顺序）；
- ``descriptor_evidence``：descriptor + 期望指纹 → 资格参数形状
  （``{descriptor, expected_descriptor_fingerprint}``），期望指纹缺席 =
  空串（freshness guard 诚实不激活，绝不用猜测的指纹对账）；
- G01/WP-A：``session_role_binding`` —— 多数据 session 的角色级供给。
  收集会话语义证据（ref_id 排序、有界、零扫描）+ durable 记录面指纹
  （recovery_state.source_fingerprints）→ ``bind_session_roles`` 的
  确定性绑定报告（bound/ambiguous/stale/not_found/not_authorized/partial）。
  ``primary_session_descriptor`` 保留为单 descriptor 兼容 shim（历史
  「首个命中」语义不变，见 role_binding 模块 docstring 的歧义讨论）。

纯读面：任何失败 → None/空（诚实缺席），绝不阻断编译主链。
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Sequence


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


async def recorded_fingerprints(session_id: str) -> Dict[str, str]:
    """durable 记录面 → {ref → descriptor_fingerprint}（记录时的指纹）。

    读 recovery_state.source_fingerprints（ingest/mapspec 绑定成功时经
    record_source_fingerprints 累积的绑定事实）。记录面缺席 = 空dict
    （诚实：无记录锚就不做 stale 对账，不虚构期望指纹）。
    """
    if not session_id:
        return {}
    try:
        from app.services.gis_harness.durable_context import load_recovery_state

        state = await load_recovery_state(session_id)
    except Exception:  # noqa: BLE001 — 记录面缺席按无期望
        return {}
    out: Dict[str, str] = {}
    for item in (state.get("source_fingerprints") or []):
        if not isinstance(item, dict):
            continue
        ref = str(item.get("ref") or "")[:64]
        fp = str(item.get("descriptor_fingerprint") or "")[:96]
        if ref and fp:
            out[ref] = fp
    return out


async def session_role_binding(
    session_id: str,
    role_reqs: Sequence[Any],
    *,
    bound_refs: Optional[Dict[str, str]] = None,
    name_hints: Optional[Dict[str, str]] = None,
    max_refs: int = 16,
) -> Any:
    """session × 角色需求 → RoleBindingReport（G01/WP-A 主入口）。

    证据 = collect_session_evidence（ref_id 排序、≤max_refs、零扫描）；
    期望指纹 = recorded_fingerprints（durable 记录面，缺席不做 stale 对账）。
    任何读取失败 → 空证据报告（绑定全 not_found，诚实缺席不阻断）。
    """
    from app.services.dataset_semantics.role_binding import (
        RoleBindingReport,
        bind_session_roles,
        collect_session_evidence,
    )

    if not session_id or not list(role_reqs or []):
        return RoleBindingReport(session_id=str(session_id or "")[:64])
    try:
        records = await collect_session_evidence(session_id, max_refs=max_refs)
        expected = await recorded_fingerprints(session_id)
    except Exception:  # noqa: BLE001 — 证据面失败 = 无证据（不阻断主链）
        return RoleBindingReport(session_id=str(session_id)[:64])
    return bind_session_roles(
        role_reqs, records,
        session_id=session_id,
        bound_refs=bound_refs,
        expected_fingerprints=expected,
        name_hints=name_hints,
    )


__all__ = [
    "primary_session_descriptor",
    "descriptor_evidence",
    "recorded_fingerprints",
    "session_role_binding",
]
