"""Topology Workbench —— 定点修复桥与证据导出（WP10/WP11）。

修复产品纪律（任务书 §六/WP10）：
- 候选修复 = **有解释的建议**（列出目标身份、几何/属性变化、量变、前置
  版本），不是可提交承诺；
- 真正提交走 **G14 既有链**（feature_edit build_and_store_plan → 用户确认 →
  commit；CAS/journal/undo/局部拓扑闸全部继承）—— 本模块绝不另写数据；
- 业务归属不自动判定：缺口填充/重叠消除需要用户选择保留方后才生成计划；
- 一次需要多个 ref 修改时不冒称原子事务：逐项预览、逐项提交、逐项复验；
- 只读源：复用 G14 editable-copy 指引 / data-quality new-ref 语义，原源
  字节不变（T23）；
- 不支持的操作 → typed unsupported + 人工编辑路径，不返回假 receipt。

导出（WP11）：findings 证据 GeoJSON —— BlobStore 产物 + 独立回读核验，
不另造多格式交付器（G18 data_delivery 负责通用交付）。
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from app.services.topology_workbench.service import (
    WorkbenchError,
    _get_owned_run,
)

logger = logging.getLogger(__name__)

#: 每类 finding 可生成的修复候选族（封闭）
CANDIDATE_KINDS = {
    "line_endpoints_connected": ["snap_endpoint"],
    "objects_covered_by_target": ["trim_to_target"],
    "area_coverage": ["fill_gap"],
    "polygons_no_overlap": ["resolve_overlap"],
    "cross_layer_no_overlap": ["resolve_overlap"],
}

#: 需要用户归属选择的候选（不自动生成单解计划）
NEEDS_OWNERSHIP_CHOICE = {"fill_gap", "resolve_overlap"}


# ─── 候选修复生成 ─────────────────────────────────────────────────────


def build_repair_candidates(
    finding_id: str, *, actor_user_id: Optional[str], is_admin: bool = False,
) -> Dict[str, Any]:
    """finding → 候选修复列表（有界；含前置版本与量变预估；纯建议不写数据）。"""
    from app.core.database import SessionLocal
    from app.models.topology_workbench import TopologyFinding

    with SessionLocal() as db:
        finding = db.get(TopologyFinding, finding_id)
        if finding is None:
            raise WorkbenchError("finding_not_found", "问题不存在", status=404)
        run = _get_owned_run(db, finding.run_id, actor_user_id, is_admin)
        metric = finding.metric or {}
        locators = finding.locators or []
        bindings = _bounded_bindings(run)

    rt = finding.rule_type
    kinds = CANDIDATE_KINDS.get(rt, [])
    if not kinds:
        return {
            "finding_id": finding_id, "rule_type": rt, "candidates": [],
            "unsupported_reason": (
                f"规则类型 {rt} 没有确定性修复操作 —— 请在编辑器人工修正后复验"
            ),
        }

    def _base(kind: str) -> Dict[str, Any]:
        return {
            "kind": kind,
            "ownership_choice_required": kind in NEEDS_OWNERSHIP_CHOICE,
            "preview_available": False,
            "estimated_change": {
                "unit": metric.get("unit", "m2"),
                "expected": f"影响范围 ≈ {metric.get('value')}",
            },
            "metric": metric,
        }

    candidates: List[Dict[str, Any]] = []
    for kind in kinds:
        cand = _base(kind)
        if kind == "snap_endpoint":
            cand.update({
                "description": (
                    "把线端点吸附到最近连接对象（G14 snap_to_ref，容差 = 规则容差 "
                    f"{metric.get('threshold')} m）"
                ),
                "target_locators": [loc for loc in locators if loc.get("role") == "checked"],
                "preview_available": True,
            })
        elif kind == "trim_to_target":
            cand.update({
                "description": (
                    "把越界对象裁剪回 target 覆盖范围（G14 replace_geometry，"
                    "新几何 = 对象 ∩ ∪相关target）"
                ),
                "target_locators": [loc for loc in locators if loc.get("role") == "checked"],
                "preview_available": True,
            })
        else:  # fill_gap / resolve_overlap —— 归属选择类
            cand.update({
                "description": (
                    "该修复涉及业务归属选择（缺口填给哪一块 / 重叠区归谁保留），"
                    "系统不自动判定 —— 请提供 ownership_choice 后生成具体计划"
                ),
                "target_locators": locators,
            })
        candidates.append(cand)

    return {
        "finding_id": finding_id, "rule_type": rt,
        "candidates": candidates,
        "run_bindings": bindings,
        "note": "候选是建议（preview_available=false 的项不可自动预览）；提交一律经 G14 确认链",
    }


def _bounded_bindings(run: Any) -> Dict[str, Any]:
    return {
        role: {"kind": ev.get("kind"), "ref": ev.get("ref"),
               "content_revision": ev.get("content_revision")}
        for role, ev in (run.bindings or {}).items()
    }


# ─── G14 预览桥（唯一写路径仍是 feature_edit executor） ─────────────────


async def build_g14_preview(
    finding_id: str, *, session_id: str, kind: str,
    ownership_choice: Optional[Dict[str, Any]] = None,
    actor_user_id: Optional[str] = None, is_admin: bool = False,
) -> Dict[str, Any]:
    """候选 → G14 预览 plan（build_and_store_plan；绝不直接写数据）。"""
    from app.core.database import SessionLocal
    from app.models.topology_workbench import TopologyFinding

    with SessionLocal() as db:
        finding = db.get(TopologyFinding, finding_id)
        if finding is None:
            raise WorkbenchError("finding_not_found", "问题不存在", status=404)
        _get_owned_run(db, finding.run_id, actor_user_id, is_admin)
        metric = finding.metric or {}
        locators = finding.locators or []

    if kind in NEEDS_OWNERSHIP_CHOICE and not (ownership_choice or {}).get("keeper_locator"):
        raise WorkbenchError(
            "repair_unsupported",
            f"修复 {kind} 需要业务归属选择（ownership_choice.keeper_locator = "
            "保留方 locator）；系统不自动判定地块所有权",
        )
    if kind not in ("snap_endpoint", "trim_to_target", "fill_gap", "resolve_overlap"):
        raise WorkbenchError("repair_unsupported", f"未知修复候选类型: {kind}")

    editable = [loc for loc in locators if loc.get("role") in ("checked", "source_a")]
    if not editable:
        raise WorkbenchError(
            "invalid_locator", "finding 缺少可编辑的 checked/source_a locator"
        )
    primary = editable[0]
    identity = primary.get("identity") or {}
    if identity.get("kind") not in ("wfid", "natural_id"):
        raise WorkbenchError(
            "invalid_locator",
            "目标要素没有稳定身份（快照内 index 定位不能跨版本编辑）—— "
            "请先物化 _wfid（对该数据集执行一次编辑即自动物化）",
        )
    ref_id = primary.get("ref") or ""
    if not str(ref_id).startswith("ref:"):
        raise WorkbenchError(
            "repair_unsupported",
            "该 finding 的源不是会话 ref-backed 数据集 —— 请先派生编辑副本"
            "（editable-copy）或使用数据集级修复（data-quality autofix new-ref）",
        )

    if kind == "snap_endpoint":
        target_ref = _connector_ref(locators)
        if target_ref is None:
            raise WorkbenchError(
                "repair_unsupported",
                "连接对象不是会话 ref（snap_to_ref 需要会话内目标数据集）",
            )
        ops = [{
            "op": "snap_to_ref",
            "target_ref": target_ref,
            "tolerance_m": float(metric.get("threshold") or 1.0),
        }]
        feature_ids = [identity["value"]]
    elif kind == "trim_to_target":
        new_geometry = await _compute_trim_geometry(
            session_id, primary, [loc for loc in locators if loc.get("role") == "target"]
        )
        ops = [{"op": "replace_geometry", "geometry": new_geometry}]
        feature_ids = [identity["value"]]
    else:  # fill_gap / resolve_overlap（已带归属选择）
        raise WorkbenchError(
            "repair_unsupported",
            f"{kind} 的自动计划生成尚未支持 —— 请在地图编辑器人工执行后复验"
            "（归属选择类修复的自动几何生成属于后续增量）",
        )

    from app.services.feature_edit import service as fe_service

    plan, validation = await fe_service.build_and_store_plan(
        session_id, ref_id=ref_id, feature_ids=feature_ids, ops=ops,
    )
    summary = fe_service.summarize(plan, validation)
    return {
        "finding_id": finding_id, "kind": kind,
        "g14_plan": summary,
        "committable": bool(plan.get("committable")) if plan else False,
        "validation": validation[:8],
        "note": (
            "提交需经 G14 既有确认链（编辑提案卡 / vector_feature_edit_commit）；"
            "本预览不写任何数据；plan TTL 10 分钟，过期请重新预览"
        ),
    }


def _connector_ref(locators: List[Dict[str, Any]]) -> Optional[str]:
    for loc in locators:
        if loc.get("role") in ("connectors", "target") and \
                str(loc.get("ref") or "").startswith("ref:"):
            return str(loc["ref"])
    return None


async def _compute_trim_geometry(
    session_id: str, checked_locator: Dict[str, Any],
    target_locators: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """trim 候选几何：当前源上的对象 ∩ 相关 target 并集（线程池内计算）。

    对象按身份链在**当前** ref 载荷中解析 —— 若源 revision 已前进、对象消失
    或身份解析失败 → typed 拒绝（预览期安全失败，绝不编辑错对象，T21）。
    """
    from app.services.session_data import session_data_manager

    ref_id = str(checked_locator.get("ref") or "")
    payload = await session_data_manager.get(session_id, ref_id)
    if not isinstance(payload, dict) or not isinstance(payload.get("features"), list):
        raise WorkbenchError(
            "source_unresolvable", f"会话 ref 不可读: {ref_id[:48]}", status=410
        )
    features: List[Dict[str, Any]] = payload["features"]

    identity = checked_locator.get("identity") or {}
    import asyncio

    target_payloads: List[Any] = []
    for tl in target_locators[:4]:
        tref = str(tl.get("ref") or "")
        if tref.startswith("ref:"):
            target_payloads.append(await session_data_manager.get(session_id, tref))

    def _compute() -> Dict[str, Any]:
        from shapely.geometry import shape
        from shapely.ops import unary_union

        from app.services.feature_pages import FEATURE_ID_KEYS

        # 身份链解析目标要素（与三侧同源链）
        target_feature = None
        id_value = str(identity.get("value") or "")
        for f in features:
            props = (f or {}).get("properties") or {}
            for key in FEATURE_ID_KEYS:
                if str(props.get(key) or "") == id_value:
                    target_feature = f
                    break
            if target_feature is not None:
                break
        if target_feature is None or not (target_feature.get("geometry") or {}):
            raise WorkbenchError(
                "invalid_locator",
                "目标要素在当前源中不可解析（可能已被编辑/删除）—— 请重新运行检查",
                status=409,
            )
        geom = shape(target_feature["geometry"])
        tgt_geoms = []
        for tp in target_payloads:
            if isinstance(tp, dict) and isinstance(tp.get("features"), list):
                for tf in tp["features"]:
                    # 只取与对象相交的相关 target（有界）
                    try:
                        tg = shape((tf or {}).get("geometry") or {})
                    except Exception:
                        continue
                    if not tg.is_empty and tg.intersects(geom):
                        tgt_geoms.append(tg)
        if not tgt_geoms:
            raise WorkbenchError(
                "repair_unsupported",
                "相关 target 几何不可读 —— 无法计算裁剪；请人工编辑后复验",
            )
        trimmed = geom.intersection(unary_union(tgt_geoms))
        if trimmed.is_empty:
            raise WorkbenchError(
                "repair_unsupported",
                "对象与 target 没有交集（数据可能已变化）—— 请重新运行检查",
            )
        from shapely.geometry import mapping

        m = mapping(trimmed)
        if m["type"] == "GeometryCollection":
            m = {"type": "MultiPolygon", "coordinates": [
                g["coordinates"] for g in trimmed.geoms
                if g.geom_type == "Polygon"]} if any(
                g.geom_type == "Polygon" for g in trimmed.geoms) else m
        return m

    return await asyncio.to_thread(_compute)


# ─── 证据导出（WP11） ─────────────────────────────────────────────────


def export_run_findings_geojson(
    run_id: str, *, actor_user_id: Optional[str], is_admin: bool = False
) -> Dict[str, Any]:
    """运行 → 问题证据 GeoJSON（BlobStore 产物 + 独立回读核验）。"""
    from app.core.database import SessionLocal
    from app.models.topology_workbench import TopologyFinding
    from app.services.durable_blob_store import get_filesystem_blob_store

    with SessionLocal() as db:
        run = _get_owned_run(db, run_id, actor_user_id, is_admin)
        digest_expected = run.ruleset_digest
        findings = db.execute(
            select(TopologyFinding).where(TopologyFinding.run_id == run_id)
            .order_by(TopologyFinding.finding_key)
        ).scalars().all()
        rows = [
            {
                "type": "Feature",
                "properties": {
                    "finding_id": f.id, "finding_key": f.finding_key,
                    "rule_id": f.rule_id, "rule_type": f.rule_type,
                    "severity": f.severity, "status": f.status,
                    "metric": f.metric, "message": f.message,
                    "bbox": [f.bbox_w, f.bbox_s, f.bbox_e, f.bbox_n],
                },
                "geometry": None,
            }
            for f in findings
        ]
    export = {
        "type": "FeatureCollection",
        "crs": {"code": "EPSG:4326"},
        "features": rows,
        "_meta": {
            "run_id": run_id, "ruleset_digest": digest_expected,
            "findings_total": len(rows),
            "export_note": "geometry 为空（定位用 bbox）—— 完整 witness 经 findings/{id}/witness 读取",
        },
    }
    raw = json.dumps(export, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    store = get_filesystem_blob_store()
    store.put_blob(digest, raw.encode("utf-8"), "json")
    back = store.get_blob(digest, expected_sha256=digest)
    verified = back == raw.encode("utf-8")
    return {
        "run_id": run_id,
        "export_key": digest, "export_sha256": digest,
        "readback_verified": verified,
        "feature_count": len(rows),
    }


__all__ = [
    "build_repair_candidates", "build_g14_preview",
    "export_run_findings_geojson", "CANDIDATE_KINDS",
]
