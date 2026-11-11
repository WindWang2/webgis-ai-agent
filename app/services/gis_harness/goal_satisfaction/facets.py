"""G28 P3 —— desired/observed/artifact 切面只读投影。

单一职责：在 finalizer 触发点把「MapSpec desired state」与「观察/导出
回执」的对账投影成有界事实面（当前交付 frame 切面）。红线：

- 验证逻辑单一来源 = ``frame_products.verify_frame_product``（G15 WP11），
  本模块**只组装输入、不重算**；
- 观察回执缺席 = 诚实 ``not_observed``（不伪造逐帧 ACK；G25 产生 frame
  runtime 观察后本函数自动受益 —— 输入面已预留）；
- revision 对账用当前 MapSpec mutation revision（旧截图/旧导出不冒充
  当前地图，A05）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def frame_observed_receipts(
    observation: Optional[Dict[str, Any]],
    export_receipts: Optional[List[Dict[str, Any]]],
) -> Dict[str, Dict[str, Any]]:
    """既有观察面 → {frame_id: {revision, mode}}（纯函数）。

    来源（按可信度归并）：
    - render observation 自带的 ``frames`` 字段（G25 合入后出现；今日缺席
      → 空 —— 诚实 not_observed，不伪造）；
    - 章节 ``export_receipts`` 行携带 ``frame_id`` + ``revision`` 时计为
      ``export_verified``（导出物证 ≥ snapshot，仍低于 live_observed）。
    """
    observed: Dict[str, Dict[str, Any]] = {}
    obs_frames = _dict(observation).get("frames")
    if isinstance(obs_frames, dict):
        for fid, entry in obs_frames.items():
            if isinstance(entry, dict) and str(fid).strip():
                observed[str(fid)[:64]] = {
                    "revision": entry.get("revision"),
                    "mode": str(entry.get("mode") or "")[:24],
                }
    for rcpt in (export_receipts or [])[:16]:
        rcpt = _dict(rcpt)
        fid = str(rcpt.get("frame_id") or "").strip()
        if not fid:
            continue
        revision = rcpt.get("revision")
        prev = observed.get(fid)
        # 同帧双证不降级：live_observed 优先；export 填缺席者。
        if prev is None or str(prev.get("mode") or "") not in (
                "live_observed",):
            observed[fid[:64]] = {"revision": revision,
                                  "mode": "export_verified"}
    return observed


async def frame_verification_facet(
    session_id: str,
    *,
    spec: Optional[Dict[str, Any]] = None,
    mapspec_revision: Optional[int] = None,
    observation: Optional[Dict[str, Any]] = None,
    export_receipts: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """MapSpec frame_product → 逐画幅终验块（``verify_frame_product`` 只读
    组装；无 frame_product 绑定 → None，零漂移）。"""
    del session_id  # 预留：观察面按 session 读取的统一入口（今日纯输入组装）
    if not isinstance(spec, dict):
        return None
    product = spec.get("frame_product")
    if not isinstance(product, dict) or not product:
        return None
    from app.services.gis_harness.frame_products.verification import (
        verify_frame_product,
    )

    observed = frame_observed_receipts(observation, export_receipts)
    try:
        return verify_frame_product(
            spec, observed, mapspec_revision=mapspec_revision)
    except Exception:  # noqa: BLE001 — 切面失败 = 诚实缺席（绝不阻断终验）
        logger.debug("[GoalFacets] frame verification failed", exc_info=True)
        return None


def frame_facet_summary(
    verification: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """终验块 → SSE 有界摘要（无块/not_bound → None）。"""
    if not isinstance(verification, dict):
        return None
    if verification.get("status") == "not_bound":
        return None
    summary = _dict(verification.get("summary"))
    return {
        "status": str(verification.get("status") or "")[:16],
        "required": int(summary.get("required") or 0),
        "verified": int(summary.get("verified") or 0),
        "snapshot_only": int(summary.get("snapshot_only") or 0),
        "not_observed": int(summary.get("not_observed") or 0),
    }


__all__ = [
    "frame_observed_receipts",
    "frame_verification_facet",
    "frame_facet_summary",
]
