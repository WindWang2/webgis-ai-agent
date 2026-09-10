"""Workflow Runtime V5 —— 用户/渲染面有界投影与解释。

回答 Epic §16 I 的全部问题：选了什么方法论、拒绝了哪些替代、当前节点、
为什么 blocked、哪些 stale/复用/重算、为什么重算/为什么没重算。
全部 bounded dict，无载荷、无秘密。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def instance_projection(
    inst: Dict[str, Any],
    nodes: List[Dict[str, Any]],
    *,
    package: Optional[Dict[str, Any]] = None,
    node_count_cap: int = 64,
) -> Dict[str, Any]:
    """实例投影（API GET /instances/{id} 的响应体）。"""
    node_proj = []
    for n in sorted(nodes[:node_count_cap], key=lambda x: x["node_id"]):
        proj = {
            "node_id": n["node_id"][:64],
            "state": n["state"],
            "attempts": n["attempts"],
            "error_code": n["error_code"][:64],
            "bound_ref": n["bound_ref"][:96],
            "output_ref": n["output_ref"][:96],
            "reused": bool((n.get("reuse") or {}).get("reused")),
            "reuse_evidence": n.get("reuse") or {},
            "binding_violations": [
                v.get("code", "")[:48]
                for v in ((n.get("binding") or {}).get("violations") or [])
            ][:8],
        }
        node_proj.append(proj)
    out: Dict[str, Any] = {
        "instance_id": inst["instance_id"],
        "package_id": inst["package_id"][:64],
        "package_version": inst["package_version"][:16],
        "package_fingerprint": inst["package_fingerprint"][:64],
        "status": inst["status"],
        "revision": inst["revision"],
        "cancel_requested": bool(inst.get("cancel_requested")),
        "nodes": node_proj,
        "counts": _counts(node_proj),
        "decisions": list(inst.get("decisions") or [])[:16],
        "pending_changes": list(inst.get("pending_changes") or [])[:16],
        "error_code": inst.get("error_code") or "",
        "error_detail": inst.get("error_detail") or "",
    }
    if package:
        out["methodology_family"] = (package.get("methodology_family")
                                     or "")[:40]
        out["compiler_version"] = (package.get("compiler_version")
                                   or "")[:16]
    return out


def _counts(node_proj: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for n in node_proj:
        counts[n["state"]] = counts.get(n["state"], 0) + 1
    return {k: v for k, v in sorted(counts.items())}


def explain(
    nodes: List[Dict[str, Any]],
    decisions: List[Dict[str, Any]],
    *,
    node_id: str = "",
) -> Dict[str, Any]:
    """「为什么重算 / 为什么没重算」的可读解释（投影面）。"""
    why_recomputed: List[str] = []
    why_reused: List[str] = []
    for d in decisions[-4:]:
        fp = d.get("changes_fp", "")
        if d.get("style_only"):
            why_reused.append(
                f"决策 {fp[:8]}：style 变更 → 科学子图零重算（呈现态刷新）")
            continue
        stale = d.get("marked_stale") or []
        if stale:
            why_recomputed.append(
                f"决策 {fp[:8]}：{','.join(stale[:5])} 因 "
                f"{','.join(d.get('dimensions') or [])[:40]} 变更标记重算")
        reused_note = d.get("reused") or []
        if reused_note:
            why_reused.append(
                f"决策 {fp[:8]}：{','.join(reused_note[:5])} 未受影响，复用")
    for n in nodes:
        if node_id and n["node_id"] != node_id:
            continue
        reuse = n.get("reuse") or {}
        if reuse.get("reused"):
            why_reused.append(
                f"{n['node_id']}: 复用 {reuse.get('artifact_ref', '')[:48]}"
                f"（输入指纹一致：{','.join(
                    reuse.get('verified_inputs', [])[:3])}）")
        if n["state"] == "STALE":
            why_recomputed.append(f"{n['node_id']}: 输入事实漂移，待重算")
    return {
        "why_recomputed": why_recomputed[:8],
        "why_reused": why_reused[:8],
        "blocked": [
            {"node": n["node_id"][:64],
             "codes": n["binding_violations"] or [n["error_code"]]}
            for n in nodes if n["state"] == "BLOCKED"
        ][:8],
    }
