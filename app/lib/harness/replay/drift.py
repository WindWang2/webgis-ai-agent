"""Registry drift 检测 + 决策重推导比对（方向 8 / ADR-0204 决策四）。

回答「benchmark 能否定位**哪里变了**」：

- ``capability_registry_digest``：capability graph 行为面（节点 id/kind/
  status/version + capability→provider 边 + fallback/conflict 边）的规范
  投影 sha256 —— 录制时进 ``ReplayTrace.env``，重放时对当前 registry
  重算比对，漂移即显式披露（drift ≠ fail，但 digest 漂移有了归因面）。
- ``rederive_capability_decision``：用决策记录里冻结的 inputs
  （capability + situation）离线重跑同源 ``capability_status``，重建
  决策的 selected/alternatives —— 决策级 delta 把「digest 变了」钉到
  「哪个能力的哪个 provider 排序/资格变了」。
- ``diff_decisions``：按 decision_id / kind 对齐两组决策记录，输出
  结构化差异（selected_changed / alternatives_changed / added / removed）。

确定性、无 I/O、离线（capability graph 是静态数据面）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.lib.harness.replay.determinism import canonical_json, sha256_of

#: capability_resolution 决策种类（与 runtime.decision_record 词表一致；
#: 复制为常量避免生产 runtime 模块进入重放器导入面）。
_KIND_CAPABILITY_RESOLUTION = "capability_resolution"
_KIND_PLAN_SELECTION = "plan_selection"
_KIND_DISPATCH_DENIAL = "capability_dispatch_denial"


# ── registry digest ──────────────────────────────────────────────────────────


def capability_registry_digest(*, graph: Any = None) -> str:
    """capability registry 行为面的规范 sha256（确定性、有界投影）。

    参与面 = 会改变决策的行为事实：节点 id/kind/status/version、
    capability→providers 边、fallback 链、conflicts。标签/描述文案
    **不参与**（不改行为的文案变化不制造 drift 噪声）。
    """
    try:
        g = graph
        if g is None:
            from app.services.gis_harness.capability_graph import (
                get_capability_graph,
            )

            g = get_capability_graph()
        projection: List[Dict[str, Any]] = []
        for kind in ("capability", "algorithm", "tool", "model", "workflow",
                     "methodology", "template", "execution_backend"):
            nodes_fn = getattr(g, "nodes_by_kind", None)
            if nodes_fn is None:
                break
            for node in sorted(nodes_fn(kind), key=lambda n: str(n.id)):
                entry: Dict[str, Any] = {
                    "id": str(node.id),
                    "kind": str(getattr(node, "kind", kind)),
                }
                extras = getattr(node, "extras", None) or {}
                for key in ("status", "version", "deterministic",
                            "offline_capable", "side_effect", "scale_class"):
                    if key in extras and extras[key] is not None:
                        entry[key] = str(extras[key])
                projection.append(entry)
        # 边（行为面）：capability → providers / fallback / conflicts。
        # capability_providers 生产契约是 Dict[face, List[id]]（tools/
        # models/workflows/templates）—— 逐面排序后整体入投影（review
        # P1-1：对 dict 直接 sorted() 只取键名，provider 重接线不可见）。
        edges: List[Dict[str, Any]] = []
        cap_nodes = g.nodes_by_kind("capability") if hasattr(g, "nodes_by_kind") else []
        for node in sorted(cap_nodes, key=lambda n: str(n.id)):
            cap_id = str(node.id)
            providers_raw = (
                g.capability_providers(cap_id)
                if hasattr(g, "capability_providers") else {}
            )
            if isinstance(providers_raw, dict):
                providers: Any = {
                    str(face): sorted(str(p) for p in ids)[:8]
                    for face, ids in sorted(providers_raw.items())
                    if ids
                }
            else:  # 旧形态 / 测试 stub 容错
                providers = sorted(str(p) for p in providers_raw)
            fallbacks = (
                [str(f) for f in g.fallback_chain("capability", cap_id)]
                if hasattr(g, "fallback_chain") else []
            )
            conflicts = (
                sorted(str(c) for c in g.conflicts_of_capability(cap_id))
                if hasattr(g, "conflicts_of_capability") else []
            )
            edges.append({
                "capability": cap_id,
                "providers": providers,
                "fallbacks": fallbacks,
                "conflicts": conflicts,
            })
        return sha256_of({"nodes": projection, "edges": edges})
    except Exception:  # noqa: BLE001 — registry 缺席 → 空 digest（诚实缺席）
        return ""


def registry_drift(recorded_digest: str, current_digest: str) -> Optional[Dict[str, Any]]:
    """录制 vs 当前 registry 指纹比对（缺席侧不制造假漂移）。"""
    if not recorded_digest or not current_digest:
        return None
    if recorded_digest == current_digest:
        return None
    return {
        "kind": "registry_drift",
        "recorded_digest": recorded_digest[:16],
        "current_digest": current_digest[:16],
        "hint": "capability registry changed between record and replay; "
                "decision deltas below are attributable to it",
    }


# ── 决策重推导（capability_resolution 面的确定性重跑）────────────────────────


def rederive_capability_decision(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """用决策记录冻结的 inputs 重跑 capability_status → 重建决策投影。

    只支持 ``capability_resolution`` 种类（inputs 携带 capability +
    situation；resolve 侧是纯函数，离线可重跑）。其余种类返回 None
    （plan_selection 需要意图对象重建，dispatch denial 需要 registry
    fixture —— 见 ADR-0204 §2 决策五的边界）。
    """
    if not isinstance(record, dict) \
            or record.get("kind") != _KIND_CAPABILITY_RESOLUTION:
        return None
    inputs = record.get("inputs") or {}
    capability_id = str(inputs.get("capability") or "")
    if not capability_id:
        return None
    situation = _situation_from_projection(inputs.get("situation"))
    try:
        from app.services.gis_harness.capability_resolution import (
            capability_status,
        )

        status, ranked, rejected = capability_status(
            capability_id, situation)
    except Exception:  # noqa: BLE001 — 重推导缺席不制造假 delta
        return None
    best = ranked[0] if ranked else None
    alternatives = []
    for cand in ranked[:6]:
        alternatives.append({
            "id": str(cand.id),
            "score": round(float(cand.score), 4),
            "status": str(cand.qualification.status),
        })
    for cand in rejected[:2]:
        alternatives.append({
            "id": str(cand.id),
            "status": str(cand.qualification.status),
        })
    rederived = {
        "kind": _KIND_CAPABILITY_RESOLUTION,
        "selected": (f"{best.kind}:{best.id}" if best else ""),
        "status": status,
        "alternatives": alternatives,
    }
    recorded_selected = str(record.get("selected") or "")
    recorded_alts = [
        {"id": str(a.get("id") or ""),
         **({"score": round(float(a["score"]), 4)}
            if isinstance(a.get("score"), (int, float)) else {}),
         **({"status": str(a["status"])}
            if a.get("status") else {})}
        for a in (record.get("alternatives") or [])[:8]
        if isinstance(a, dict)
    ]
    rederived["decision_id"] = str(record.get("decision_id") or "")
    rederived["capability"] = capability_id
    rederived["recorded_selected"] = recorded_selected
    rederived["recorded_alternatives"] = recorded_alts
    return rederived


def _situation_from_projection(projection: Any) -> Any:
    """决策 inputs 里的 situation 投影 → QualificationContext（缺席默认）。

    与 ``QualificationContext.to_rederive_dict()`` 的全息快照字段一一
    对应（review P1-3：资格判定读取的 credentials_present /
    dependency_available / field_names 等必须还原，否则 rederive 会在
    被重置的默认上下文上重跑 → 假 delta）。旧有损投影（to_dict 形态）
    缺这些键时按缺席诚实降级（unknown），不猜值。
    """
    from app.services.gis_harness.qualification_v8 import QualificationContext

    ctx = QualificationContext()
    if not isinstance(projection, dict):
        return ctx
    simple = {
        "task_hint", "geometry_kinds", "crs", "crs_is_geographic",
        "field_names", "feature_count", "raster_bands",
        "resolution_m_per_px", "sensor", "temporal_inputs", "data_bytes",
        "map_layer_count", "gpu_available", "vram_bytes", "memory_bytes",
        "max_latency_class", "owner_scope_key", "offline", "auth_tier",
        "budget_cost_class", "quality_gate", "dependency_available",
        "credentials_present",
    }
    for key in simple:
        if key in projection and projection[key] is not None:
            try:
                setattr(ctx, key, projection[key])
            except (TypeError, ValueError):
                pass
    blocking = projection.get("blocking_issue_codes")
    if isinstance(blocking, list):
        ctx.blocking_issue_codes = [str(c)[:64] for c in blocking[:16]]
    return ctx


# ── decision delta ───────────────────────────────────────────────────────────


def decisions_digest(decisions: List[Dict[str, Any]]) -> str:
    """决策序列的行为摘要（不含墙钟/关联 id 的确定性投影）。"""
    projection = [
        {
            "kind": str(d.get("kind") or ""),
            "selected": str(d.get("selected") or ""),
            "inputs_digest": str(d.get("inputs_digest") or ""),
            "alternatives": [
                {"id": str(a.get("id") or ""),
                 "score": a.get("score"),
                 "status": str(a.get("status") or "")}
                for a in (d.get("alternatives") or [])[:8]
                if isinstance(a, dict)
            ],
        }
        for d in (decisions or [])[:16]
        if isinstance(d, dict)
    ]
    return sha256_of(projection)


def diff_decisions(baseline: List[Dict[str, Any]],
                   current: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """两组决策记录的结构化差异（「哪里变了」的决策级归因）。

    对齐：先按 decision_id（同 id 但 selected/alternatives 变化 → 精确
    归因），再按 kind 序配对剩余条目（id 变化的重推导面）。输出有界
    （≤32 条 diff）。
    """
    diffs: List[Dict[str, Any]] = []

    def _alt_key(entry: Dict[str, Any]) -> str:
        return canonical_json({
            "id": entry.get("id"),
            "score": entry.get("score"),
            "status": entry.get("status"),
        })

    base_ids = {
        str(d.get("decision_id") or "")
        for d in (baseline or []) if isinstance(d, dict) and d.get("decision_id")
    }
    base_by_id = {
        str(d.get("decision_id") or ""): d
        for d in (baseline or []) if isinstance(d, dict) and d.get("decision_id")
    }
    cur_by_id = {
        str(d.get("decision_id") or ""): d
        for d in (current or []) if isinstance(d, dict) and d.get("decision_id")
    }
    for did, cur in cur_by_id.items():
        base = base_by_id.pop(did, None)
        if base is None:
            continue
        if str(base.get("selected") or "") != str(cur.get("selected") or ""):
            diffs.append({
                "type": "selected_changed", "decision_id": did,
                "kind": str(cur.get("kind") or ""),
                "baseline": str(base.get("selected") or ""),
                "current": str(cur.get("selected") or ""),
            })
        base_alts = [_alt_key(a) for a in (base.get("alternatives") or [])
                     if isinstance(a, dict)]
        cur_alts = [_alt_key(a) for a in (cur.get("alternatives") or [])
                    if isinstance(a, dict)]
        if base_alts != cur_alts:
            diffs.append({
                "type": "alternatives_changed", "decision_id": did,
                "kind": str(cur.get("kind") or ""),
                "baseline_count": len(base_alts),
                "current_count": len(cur_alts),
            })
    for did, cur in cur_by_id.items():
        if did not in base_ids:
            diffs.append({
                "type": "added", "decision_id": did,
                "kind": str(cur.get("kind") or ""),
                "selected": str(cur.get("selected") or ""),
            })
    for did, base in base_by_id.items():
        diffs.append({
            "type": "removed", "decision_id": did,
            "kind": str(base.get("kind") or ""),
            "selected": str(base.get("selected") or ""),
        })
    return diffs[:32]


__all__ = [
    "capability_registry_digest",
    "registry_drift",
    "rederive_capability_decision",
    "decisions_digest",
    "diff_decisions",
]
