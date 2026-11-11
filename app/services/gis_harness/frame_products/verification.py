"""G15 WP11 —— 逐画幅观察/终验/失效（有界 frame 证据，无第二完成状态机）。

纯函数：MapSpec + 观察回执（按 frame_id+revision 对账）→ 逐画幅状态与
产品级汇总。语义纪律：
- required frame 集合 = 启用产品画幅（按当前用户目标可再收窄 —— 调用方
  传入 required_frame_ids）；
- 观察模式分级：live_observed（实时可见）> export_verified（导出物证）
  > snapshot（缓存缩略图/未激活 —— **不冒充** live）> not_observed；
- 空帧（过滤后 0 要素）显式 `frame_no_data` —— 不借用相邻帧，不伪报
  完整；
- 陈旧观察（revision 不匹配）→ `stale_observation`，按未观察处理；
- 绑定失败/悬空 → `frame_unresolvable`；
- 全部 required 画幅 bound + observed(live|export) 才 complete ——
  「只有一帧成功 ACK」绝不汇总为产品完成。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

#: 观察回执词表（模式分级见模块 docstring）。
OBSERVATION_MODES = ("live_observed", "export_verified", "snapshot", "not_observed")

_MAX_EVIDENCE_FRAMES = 12


def verify_frame_product(
    document: Dict[str, Any],
    observed: Optional[Dict[str, Dict[str, Any]]] = None,
    *,
    mapspec_revision: Optional[int] = None,
    required_frame_ids: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """MapSpec + 观察回执 → 有界逐画幅终验块。

    ``observed``：{frame_id: {"revision": int, "mode": live_observed|
    export_verified|snapshot}} —— 前端 render observation / 导出回执按
    frame_id+revision 上报；缺席 = not_observed（诚实未知）。
    """
    product_raw = document.get("frame_product") if isinstance(document, dict) else None
    product = product_raw if isinstance(product_raw, dict) else None
    if product is None or not product.get("enabled"):
        return {"status": "not_bound"}

    observed = observed or {}
    from app.services.gis_harness.frame_products.resolver import (
        FrameResolutionError,
        resolve_frame_document,
    )

    slots = [s for s in (product.get("frames") or []) if isinstance(s, dict)]
    required = (
        set(required_frame_ids)
        if required_frame_ids is not None
        else {str(s.get("frame_id")) for s in slots if s.get("frame_id")}
    )

    frames_out: List[Dict[str, Any]] = []
    problems: List[str] = []
    for slot in slots[:_MAX_EVIDENCE_FRAMES]:
        fid = str(slot.get("frame_id") or "")
        row: Dict[str, Any] = {"frame_id": fid, "required": fid in required}
        # 绑定与解析（真实有效文档）。
        try:
            res = resolve_frame_document(document, fid)
            row["binding"] = "bound"
            row["fingerprint"] = res.fingerprint
            n = res.filtered_feature_count
            has_filter = "where_filter" in res.applied or "time_window" in res.applied
            if has_filter and n == 0:
                row["binding"] = "empty_data"
                problems.append(f"frame_no_data:{fid}")
        except FrameResolutionError as ex:
            row["binding"] = "unresolvable"
            row["binding_code"] = ex.code
            problems.append(f"frame_unresolvable:{fid}")
        # 观察对账（frame_id + revision）。
        obs = observed.get(fid) if isinstance(observed.get(fid), dict) else {}
        mode = str(obs.get("mode") or "not_observed")
        if mode not in OBSERVATION_MODES:
            mode = "not_observed"
        obs_rev = obs.get("revision")
        if (
            mapspec_revision is not None
            and isinstance(obs_rev, int)
            and obs_rev != mapspec_revision
        ):
            row["observation"] = "stale_observation"
            row["observed_at_revision"] = obs_rev
            problems.append(f"stale_observation:{fid}")
        else:
            row["observation"] = mode
        if mode == "not_observed" and fid in required:
            problems.append(f"frame_not_observed:{fid}")
        frames_out.append(row)

    def _frame_ok(r: Dict[str, Any]) -> bool:
        return (
            r.get("binding") == "bound"
            and r.get("observation") in ("live_observed", "export_verified")
        )

    required_rows = [r for r in frames_out if r["required"]]
    complete = bool(required_rows) and all(_frame_ok(r) for r in required_rows)
    return {
        "status": "complete" if complete else "incomplete",
        "mapspec_revision": mapspec_revision,
        "required_frames": sorted(required)[:_MAX_EVIDENCE_FRAMES],
        "frames": frames_out,
        "problems": problems[: _MAX_EVIDENCE_FRAMES * 2],
        # 「逐幅汇总」：一幅成功不掩盖另一幅空/失败。
        "summary": {
            "total": len(frames_out),
            "required": len(required_rows),
            "verified": sum(1 for r in required_rows if _frame_ok(r)),
            "live_observed": sum(
                1 for r in frames_out if r.get("observation") == "live_observed"),
            "export_verified": sum(
                1 for r in frames_out if r.get("observation") == "export_verified"),
            "snapshot_only": sum(
                1 for r in frames_out if r.get("observation") == "snapshot"),
            "not_observed": sum(
                1 for r in frames_out if r.get("observation") in
                ("not_observed", "stale_observation")),
        },
    }


def frame_evidence_rows(verification: Dict[str, Any]) -> List[Dict[str, str]]:
    """终验块 → 有界证据行（goal_satisfaction evidence 消费面；refs-only）。"""
    if not isinstance(verification, dict) or verification.get("status") == "not_bound":
        return []
    rows: List[Dict[str, str]] = []
    for r in (verification.get("frames") or [])[:_MAX_EVIDENCE_FRAMES]:
        fid = str(r.get("frame_id") or "")[:64]
        if not fid:
            continue
        ok = (
            r.get("binding") == "bound"
            and r.get("observation") in ("live_observed", "export_verified")
        )
        rows.append({
            "id": f"frame:{fid}",
            "status": "verified" if ok else str(
                r.get("binding") if r.get("binding") != "bound"
                else r.get("observation") or "not_observed"),
            "required": "true" if r.get("required") else "false",
        })
    return rows
