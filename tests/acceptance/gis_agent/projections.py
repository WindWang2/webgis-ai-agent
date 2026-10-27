"""G05 TaskEvidence —— 各链路环节的权威产物投影（只读取证）。

单一事实源原则：每个投影键直接来自既有生产权威（SessionPlan 章节 /
MapSpecStore / settle 收据 / goal_satisfaction / product_manifest /
render_work_supply），本模块只做有界投影，不做第二判定。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


async def build_task_evidence(session_id: str, scenario) -> Dict[str, Any]:
    """收集场景执行后的全链证据（fail-open：单面缺席 = None/空，不抛）。"""
    evidence: Dict[str, Any] = {}
    evidence["session_id"] = session_id

    # ── SessionPlan（意图/步骤/turn 台账/章节）─────────────────────────
    plan = None
    try:
        from app.services.session_plan import load_session_plan
        plan = await load_session_plan(session_id)
    except Exception:  # noqa: BLE001
        plan = None
    chapter = getattr(plan, "gis_chapter", None) if plan else None
    evidence["plan"] = _project_plan(plan)
    evidence["chapter"] = chapter if isinstance(chapter, dict) else None
    evidence["chapter_summary"] = _project_chapter_summary(chapter)

    # ── MapSpec（MapSpecStore 读 API）──────────────────────────────────
    spec: Optional[Dict[str, Any]] = None
    revision = 0
    try:
        from app.services.mapspec_store import mapspec_store
        spec = await mapspec_store.get_mapspec(session_id)
    except Exception:  # noqa: BLE001
        spec = None
    try:
        from app.services.session_data import session_data_manager
        state = await session_data_manager.get_map_state(session_id)
        revision = int((state or {}).get("_cartographic_mutation_revision") or 0)
    except Exception:  # noqa: BLE001
        revision = 0
    evidence["mapspec"] = _project_mapspec(spec)
    if isinstance(evidence["mapspec"], dict):
        evidence["mapspec"]["mutation_revision"] = revision

    # ── 完成块（goal_satisfaction / task_complete / product_verdict）──
    evidence["map_product"] = _project_map_product(chapter)

    # ── RequirementDocument（requirement_ir 权威面）───────────────────
    evidence["requirement"] = await _project_requirement(session_id)

    # ── RenderWork（render_work_supply 投影）──────────────────────────
    evidence["render_work"] = await _project_render_work(session_id)

    # ── 导出语义一致性（product_manifest 纯函数，h02 同源判定）────────
    evidence["export"] = _project_export(spec)

    # ── G3D04：3D 完成判定（scene3d 双门）+ Interaction IR 投影 ──────
    evidence["scene3d"] = _project_scene3d(spec, revision)
    evidence["interaction"] = await _project_interaction(session_id)

    # ── G3D-V2-C：3D 分析绑定与失效裁决（analysis_binding_store 只读）──
    evidence["scene3d_analysis"] = await _project_scene3d_analysis(
        session_id, spec)

    # ── 最终答复（dispatch 缝不可观测的最终文本 → 诚实 not_evaluated 面）
    evidence["final_answer"] = {
        "observable": False,
        "disclosure": (evidence["map_product"] or {}).get("task_complete"),
        "note": "final assistant text lives in Pi turn result persistence "
                "(chat.py transcript), unreachable at the dispatch seam",
    }
    return evidence


# ── 子投影 ────────────────────────────────────────────────────────────────

def _project_plan(plan) -> Optional[Dict[str, Any]]:
    if plan is None:
        return None
    steps = []
    for s in plan.steps:
        latest = s.latest_evidence() if hasattr(s, "latest_evidence") else None
        steps.append({
            "id": s.id, "capability": s.capability, "status": s.status,
            "attempts": s.attempts, "turn_id": getattr(s, "turn_id", ""),
            "latest_ref": getattr(latest, "ref", "") if latest else "",
            "latest_error": (getattr(latest, "error", "") or "")[:160] if latest else "",
        })
    return {
        "envelope_id": plan.envelope_id,
        "revision": plan.revision,
        "user_goal": plan.user_goal,
        "replaced": plan.replaced,
        "superseded": plan.superseded,
        "recipe_id": (plan.gis_chapter or {}).get("recipe_id")
        if isinstance(plan.gis_chapter, dict) else "",
        "turns": [
            {"turn_id": t.turn_id, "status": t.status,
             "tool_calls": t.tool_calls}
            for t in plan.turns
        ],
        "steps": steps,
        "step_count": len(steps),
        "step_status_counts": _count_by(
            steps, lambda s: s["status"]),
        "progress": _project_progress(plan),
        "recovery": {
            "last_turn_status": plan.recovery.last_turn_status,
            "resume_count": plan.recovery.resume_count,
            "checkpoint_id": plan.recovery.checkpoint_id,
        },
        "decision_kinds": sorted({d.kind for d in plan.decisions}),
    }


def _project_chapter_summary(chapter) -> Optional[Dict[str, Any]]:
    """章节关键事实的有界摘要（fallback/methodology/intent 面）。"""
    if not isinstance(chapter, dict):
        return None
    intent = chapter.get("intent") or {}
    fallbacks = chapter.get("fallbacks") or []
    return {
        "recipe_id": str(chapter.get("recipe_id") or ""),
        "intent_task": str(intent.get("task") or ""),
        "intent_scope": str((intent.get("scope") or {}).get("name") or ""),
        "intent_subject": str((intent.get("subject") or {}).get("category") or ""),
        "intent_output": sorted(
            str(x) for x in (intent.get("output_intents") or [])[:6]),
        "fallback_reason_codes": sorted({
            str(fb.get("reason_code") or "") for fb in fallbacks
            if isinstance(fb, dict)} - {""}),
        "fallback_count": len(fallbacks),
        "methodology_warning_count": len(chapter.get("methodology_warnings") or []),
        "status": str(chapter.get("status") or ""),
        "query": str(chapter.get("query") or "")[:80],
    }


def _count_by(rows: List[Any], key) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for row in rows:
        k = str(key(row))
        out[k] = out.get(k, 0) + 1
    return out


def _project_progress(plan) -> Dict[str, Any]:
    """能力行进度（任务完成的行级事实源）。"""
    rows = []
    for row in (getattr(plan, "progress", None) or []):
        rows.append({
            "capability": str(getattr(row, "capability", "") or ""),
            "status": str(getattr(row, "status", "") or ""),
            "optional": bool(getattr(row, "optional", False)),
        })
    mandatory = [r for r in rows if not r["optional"]]
    return {
        "rows": rows,
        "status_counts": _count_by(rows, lambda r: r["status"]),
        "mandatory_total": len(mandatory),
        "mandatory_pending": sum(
            1 for r in mandatory if r["status"] in ("pending", "failed")),
        "mandatory_complete": all(
            r["status"] not in ("pending", "failed") for r in mandatory),
    }


async def _project_scene3d_analysis(
    session_id: str, spec: Optional[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """G3D-V2-C：分析绑定台账 + 当前失效裁决（生产只读模块投影）。

    无绑定时返回 None（未运行过 3D 分析——不是错误）；有绑定时给出
    有界 evaluations（binding/staleness 摘要：goal/observer/状态/码）。
    """
    try:
        from app.services.gis_harness.scene3d.analysis_binding_store import (
            evaluate_session_staleness,
            load_analysis_bindings,
        )

        bindings = await load_analysis_bindings(session_id)
        if not bindings:
            return None
        evaluations = await evaluate_session_staleness(session_id, mapspec=spec)
        return {
            "binding_count": len(bindings),
            "analysis_goals": sorted({b.analysis_goal for b in bindings}),
            "stale_reason_codes": sorted({
                (e.get("verdict") or {}).get("reason_code") or ""
                for e in evaluations
                if (e.get("verdict") or {}).get("state") != "fresh"
            } - {""}),
            "all_fresh": all(
                (e.get("verdict") or {}).get("state") == "fresh"
                for e in evaluations
            ),
            "evaluations": [
                {
                    "analysis_goal": (e.get("binding") or {}).get("analysis_goal"),
                    "state": (e.get("verdict") or {}).get("state"),
                    "reason_code": (e.get("verdict") or {}).get("reason_code"),
                    "recommended_action": (e.get("verdict") or {}).get(
                        "recommended_action"),
                }
                for e in evaluations
            ],
        }
    except Exception:  # noqa: BLE001 — 只读取证面 fail-open
        return None


def _project_mapspec(spec) -> Optional[Dict[str, Any]]:
    if not isinstance(spec, dict):
        return None
    layers = [
        {
            "id": str(ly.get("id") or ""),
            "type": str(ly.get("type") or ""),
            "visible": bool(ly.get("visible", True)),
            "name": str(ly.get("name") or ""),
            "source": str(ly.get("source") or ""),
        }
        for ly in spec.get("layers", []) if isinstance(ly, dict)
    ]
    by_type: Dict[str, List[str]] = {}
    for ly in layers:
        by_type.setdefault(ly["type"], []).append(ly["id"])
    components = [
        {"id": str(c.get("id") or ""), "type": str(c.get("type") or "")}
        for c in (spec.get("layout") or {}).get("components", [])
        if isinstance(c, dict)
    ]
    return {
        "exists": True,
        "version": spec.get("version"),
        "layer_count": len(layers),
        "layer_types": sorted(by_type),
        "layers": layers,
        "layers_by_type": by_type,
        "components": components,
        "component_types": sorted({c["type"] for c in components}),
        "sources": sorted((spec.get("sources") or {}).keys()),
        "title": ((spec.get("layout") or {}).get("title") or {}).get("text", "")
        if isinstance((spec.get("layout") or {}).get("title"), dict) else "",
        # G3D04：scene/view 投影（维度/地形绑定/相机档/视图态）
        "scene": _project_scene(spec.get("scene")),
        "view": _project_view(spec.get("view")),
    }


def _project_scene(scene) -> Optional[Dict[str, Any]]:
    if not isinstance(scene, dict):
        return None
    terrain = scene.get("terrain") if isinstance(scene.get("terrain"), dict) else {}
    camera = scene.get("camera") if isinstance(scene.get("camera"), dict) else {}
    return {
        "mode": str(scene.get("mode") or ""),
        "terrain_source": str(terrain.get("source") or ""),
        "exaggeration": terrain.get("exaggeration"),
        "camera_pitch": camera.get("pitch"),
        "camera_bearing": camera.get("bearing"),
    }


def _project_view(view) -> Optional[Dict[str, Any]]:
    if not isinstance(view, dict):
        return None
    return {
        "pitch": view.get("pitch"),
        "bearing": view.get("bearing"),
        "zoom": view.get("zoom"),
        "center": view.get("center"),
    }


def _project_map_product(chapter) -> Optional[Dict[str, Any]]:
    if not isinstance(chapter, dict):
        return None
    stored = chapter.get("map_product")
    if not isinstance(stored, dict):
        return None
    gs = stored.get("goal_satisfaction") or {}
    verdict = gs.get("verdict") if isinstance(gs, dict) else None
    pv = stored.get("product_verdict")
    pv_token = pv.get("verdict") if isinstance(pv, dict) else pv
    return {
        "exists": True,
        "status": str(stored.get("status") or ""),
        "task_complete": bool(stored.get("task_complete")),
        "goal_satisfaction_verdict": str(verdict or "not_evaluated"),
        "goal_satisfaction_signal": str(gs.get("signal") or "") if isinstance(gs, dict) else "",
        "goal_counts": dict(gs.get("counts") or {}) if isinstance(gs, dict) else {},
        "product_verdict": str(pv_token or ""),
        "intent_acceptance": {
            "accepted": bool((stored.get("intent_acceptance") or {}).get("accepted")),
            "intent_verified": bool(
                (stored.get("intent_acceptance") or {}).get("intent_verified")),
            "unmet": list((stored.get("intent_acceptance") or {}).get("unmet") or []),
        } if isinstance(stored.get("intent_acceptance"), dict) else None,
        "observation_health": (
            (stored.get("observation_health") or {}).get("aggregate")
            if isinstance(stored.get("observation_health"), dict) else None
        ),
        "summary": str(stored.get("summary") or "")[:160],
    }


async def _project_requirement(session_id: str) -> Optional[Dict[str, Any]]:
    try:
        from app.services.gis_harness.requirement_ir import service as req_svc
        from app.services.gis_harness.requirement_ir.contracts import (
            RequirementDocument,
        )
        from app.services.session_data import session_data_manager
        state = await req_svc.load_state(session_data_manager, session_id)
        if state is None or not isinstance(state.document, dict):
            return None
        doc = RequirementDocument.model_validate(state.document)
        summary = req_svc.summarize(doc)
        scene = doc.intent.scene
        return {
            "exists": True,
            "document_id": str(summary.get("document_id") or ""),
            "schema_version": str(summary.get("schema") or ""),
            "lifecycle": str(summary.get("lifecycle") or ""),
            "task_kind": str(summary.get("task_kind") or ""),
            "revision": summary.get("revision"),
            "requirement_digest": str(summary.get("requirement_digest") or ""),
            "folded_patch_count": getattr(state, "folded_patch_count", 0),
            # G3D04：SceneSpec 投影（typed 3D 需求面）
            "scene": {
                "dimension": scene.dimension,
                "degrade_reason": scene.degrade_reason,
                "terrain_requested": scene.terrain_requested,
                "extrusion_requested": scene.extrusion_requested,
                "exaggeration": scene.exaggeration,
                "camera_pitch": scene.camera_pitch,
                "camera_bearing": scene.camera_bearing,
                "analysis_goals": list(scene.analysis_goals),
                "scene_locks": sorted(
                    f"{lock.scope}={lock.value}" for lock in doc.intent.locks
                    if lock.scope in ("scene_mode", "scene_camera")),
            },
        }
    except Exception:  # noqa: BLE001 — requirement 面缺席不影响其他证据
        return None


async def _project_render_work(session_id: str) -> Optional[Dict[str, Any]]:
    try:
        from app.services.render_work_supply import get_render_work_envelope
        env = await get_render_work_envelope(session_id)
        if env is None:
            return None
        layer_work = list(getattr(env, "layer_work", ()) or ())
        component_work = list(getattr(env, "component_work", ()) or ())
        return {
            "exists": True,
            "work_id": str(getattr(env, "work_id", "") or ""),
            "schema_version": str(getattr(env, "schema_version", "") or ""),
            "revision": getattr(env, "mapspec_revision", None),
            "layer_work_count": len(layer_work),
            "component_work_count": len(component_work),
            "layer_work_layer_ids": [
                str(getattr(w, "layer_id", "") or "") for w in layer_work
            ],
        }
    except Exception:  # noqa: BLE001
        return None


def _project_export(spec) -> Dict[str, Any]:
    """导出语义一致性（与 h02 场景同一权威判定函数）。"""
    if not isinstance(spec, dict) or not spec.get("layers"):
        return {"available": False, "status": "", "check_statuses": []}
    try:
        from app.lib.cartography.product_manifest import (
            build_product_manifest,
            verify_export_semantic_consistency,
        )
        report = verify_export_semantic_consistency(dict(spec))
        statuses = [str(c.get("status") or "") for c in report.get("checks", [])]
        manifest = build_product_manifest(dict(spec))
        return {
            "available": True,
            "status": str(report.get("status") or ""),
            "check_statuses": statuses,
            "failed_aspects": [
                str(c.get("aspect") or "") for c in report.get("checks", [])
                if str(c.get("status") or "") == "fail"
            ],
            "not_evaluated_aspects": [
                str(c.get("aspect") or "") for c in report.get("checks", [])
                if str(c.get("status") or "") == "not_evaluated"
            ],
            "manifest_schema_version": str(
                manifest.get("schema_version") or ""),
            "manifest_layer_count": len(manifest.get("layers") or []),
        }
    except Exception as exc:  # noqa: BLE001 — 判定面异常如实暴露
        return {"available": False, "status": "", "check_statuses": [],
                "error": f"{type(exc).__name__}: {exc}"[:200]}


def _project_scene3d(spec: Optional[Dict[str, Any]],
                     revision: int) -> Optional[Dict[str, Any]]:
    """G3D04：scene3d 双门 verdict 投影（真实 spec + 真实 revision 证据）。"""
    if not isinstance(spec, dict):
        return None
    try:
        from app.services.gis_harness.scene3d.verdict import build_scene3d_verdict

        receipt = {"mutation_revision": revision} if revision else {}
        verdict = build_scene3d_verdict(mapspec=spec, scene_apply_receipt=receipt)
        return verdict.model_dump()
    except Exception:  # noqa: BLE001 — scene3d 面缺席不影响其他证据
        return None


async def _project_interaction(session_id: str) -> Optional[Dict[str, Any]]:
    """Interaction IR 有界投影（user-wins 相机/失效裁决的观测面）。"""
    try:
        from app.services.gis_harness.interaction_ir.projection import (
            interaction_snapshot_via_store,
        )
        from app.services.session_data import session_data_manager

        return await interaction_snapshot_via_store(session_data_manager, session_id)
    except Exception:  # noqa: BLE001
        return None
