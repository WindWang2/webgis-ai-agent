"""Design Spec 应用 —— 规范 → MapSpec 的投影与 dry-run（WP2）。

权威边界（不建第二执行面）：
- **样式轴**（palette / 分类方法与类数）：投影为
  :class:`~app.services.thematic_authoring.contract.ThematicAuthoringPlan`，
  dry-run = ``preview_expression``（与 commit 同一确定性构建器、零
  mutation），落图 = ``commit_expression``（一次 CAS / 一个 checkpoint）；
- **组件轴**（required/forbidden）：缺组件 → ``PatchComponentIntent
  (upsert=True)`` 经既有 ``apply_gis_mutation`` CAS；forbidden 组件**只
  报冲突不移除**（删图例/装饰是用户决策，规范不自动删）；
- **纸面轴**（typography / paper）：仅当图已有 ``print_layout`` 时生成
  operations 提案（经 print_layout authoring 既有 propose/apply）；否则
  如实披露「版式未启用，发布期生效」；
- **用户锁定优先**：spec ``overrides.locked`` 与逐图 ``locked_fields``
  的交集被 skip 并逐条披露 —— Agent 不背地里覆盖手工改图。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

#: 应用器版本（回执可追溯）。
APPLY_PLAN_VERSION = "design_spec_apply.v1"

#: overrides.locked / 逐图锁定 词表（与 contract.LOCKABLE_FIELDS 对齐）。
_LOCK_THEME = "theme"
_LOCK_PALETTES = "palettes"
_LOCK_CLASSIFICATION = "classification_method"
_LOCK_TYPOGRAPHY = "typography"
_LOCK_UNIT = "unit_semantics"
_LOCK_COMPONENTS = "required_components"

_VALID_LOCKS = {
    _LOCK_THEME, _LOCK_PALETTES, _LOCK_CLASSIFICATION,
    _LOCK_TYPOGRAPHY, _LOCK_UNIT, _LOCK_COMPONENTS,
}


def _thematic_layers(document: Dict[str, Any]) -> List[Dict[str, Any]]:
    """文档 → 专题层集合（legend_spec 或 data_binding 声明在场）。"""
    out: List[Dict[str, Any]] = []
    for layer in (document.get("layers") or []):
        if not isinstance(layer, dict):
            continue
        if isinstance(layer.get("legend_spec"), dict) or isinstance(
            layer.get("data_binding"), dict
        ):
            out.append(layer)
    return out


def _palette_of(layer: Dict[str, Any]) -> Optional[str]:
    legend = layer.get("legend_spec")
    if isinstance(legend, dict) and isinstance(legend.get("palette"), str):
        return legend["palette"]
    return None


def _k_of(layer: Dict[str, Any]) -> Optional[int]:
    legend = layer.get("legend_spec")
    if isinstance(legend, dict) and isinstance(legend.get("classes"), int):
        return legend["classes"]
    return None


def _method_of(layer: Dict[str, Any]) -> Optional[str]:
    legend = layer.get("legend_spec")
    if isinstance(legend, dict):
        method = legend.get("method") or legend.get("classification")
        if isinstance(method, str):
            return method
    return None


def build_apply_plan(
    spec_payload: Dict[str, Any],
    document: Dict[str, Any],
    *,
    locked_fields: Tuple[str, ...] = (),
    role_field: Optional[str] = None,
) -> Dict[str, Any]:
    """规范 × 当前图 → 应用计划（纯函数；dry-run 与 apply 共用）。"""
    spec_locks = set((spec_payload.get("overrides") or {}).get("locked") or [])
    effective_locks = (spec_locks | set(locked_fields)) & _VALID_LOCKS

    style_actions: List[Dict[str, Any]] = []
    skipped_locked: List[Dict[str, str]] = []
    conflicts: List[Dict[str, str]] = []
    component_ensures: List[Dict[str, Any]] = []
    layout_ops: List[Dict[str, Any]] = []
    disclosures: List[str] = []

    palette = (spec_payload.get("color") or {}).get("palette_sequential")
    classification = spec_payload.get("classification") or {}
    method = classification.get("default_method")
    k_default = (
        classification.get("k_min") if isinstance(classification.get("k_min"), int) else None
    )

    for layer in _thematic_layers(document):
        layer_id = str(layer.get("id") or "")
        binding = layer.get("data_binding") or {}
        field = str(binding.get("field") or "") or None
        if role_field and field and str(role_field) != field:
            continue
        plan: Dict[str, Any] = {"layer_id": layer_id, "mode": "graduated_auto"}
        changes: List[str] = []
        if palette and _palette_of(layer) != palette:
            if _LOCK_PALETTES in effective_locks:
                skipped_locked.append({
                    "layer_id": layer_id, "field": "palette",
                    "reason": f"palette locked（当前 {_palette_of(layer) or '未声明'}）",
                })
            else:
                plan["palette"] = palette
                changes.append("palette")
        if method and _method_of(layer) != method:
            if _LOCK_CLASSIFICATION in effective_locks:
                skipped_locked.append({
                    "layer_id": layer_id, "field": "classification_method",
                    "reason": f"classification locked（当前 {_method_of(layer) or '未声明'}）",
                })
            else:
                plan["method"] = method
                changes.append("method")
        if k_default and _k_of(layer) not in (None, k_default):
            if _LOCK_CLASSIFICATION in effective_locks:
                skipped_locked.append({
                    "layer_id": layer_id, "field": "classification_k",
                    "reason": f"classification locked（当前 k={_k_of(layer)}）",
                })
            else:
                plan["k"] = k_default
                changes.append("k")
        if changes:
            plan["changes"] = changes
            style_actions.append(plan)

    # 组件轴：required 缺失 → ensure 提案；forbidden 在场 → 冲突（不自动删）
    layout = document.get("layout") or {}
    components = [
        c for c in (layout.get("components") or []) if isinstance(c, dict)
    ]
    existing_types = {str(c.get("type") or "") for c in components}
    requirements = (spec_payload.get("components") or {})
    if _LOCK_COMPONENTS in effective_locks:
        required = []
        skipped_locked.append({
            "layer_id": "*", "field": "required_components",
            "reason": "components locked（不自动补组件）",
        })
    else:
        required = requirements.get("required") or []
    for req in required[:16]:
        ctype = str(req.get("component_type") or "")
        if not ctype:
            continue
        if ctype in existing_types:
            continue
        component_ensures.append({
            "component_type": ctype,
            "variant": req.get("variant"),
            "min_count": req.get("min_count", 1),
        })
    for ctype in (requirements.get("forbidden") or [])[:16]:
        if str(ctype) in existing_types:
            conflicts.append({
                "kind": "forbidden_component_present",
                "component_type": str(ctype),
                "message": (
                    f"规范声明禁用组件 '{ctype}'，但当前图已存在 —— "
                    "不自动移除；请用户显式决定（webgis 组件删除/禁用通道）"
                ),
            })

    # 纸面轴：仅 print_layout 在场时提案
    print_layout = layout.get("print_layout") if isinstance(layout.get("print_layout"), dict) else {}
    typography = spec_payload.get("typography") or {}
    output = spec_payload.get("output") or {}
    if print_layout:
        ops: List[Dict[str, Any]] = []
        if typography.get("font_resource") and _LOCK_TYPOGRAPHY not in effective_locks:
            ops.append({
                "op": "set_typography",
                "typography": {
                    "font_family": typography.get("font_resource"),
                    "title_size_pt": typography.get("title_size_pt"),
                    "base_size_pt": typography.get("base_size_pt"),
                },
            })
        elif typography.get("font_resource"):
            skipped_locked.append({
                "layer_id": "*", "field": "typography",
                "reason": "typography locked（纸面字体不改写）",
            })
        paper = output.get("paper_profile")
        if paper and _LOCK_TYPOGRAPHY not in effective_locks:
            from app.lib.cartography.publication_ir import PAGE_PROFILES

            if paper in PAGE_PROFILES:
                width_mm, height_mm = PAGE_PROFILES[paper]
                ops.append({
                    "op": "set_paper",
                    "paper": {
                        "profile": paper,
                        "width_mm": width_mm,
                        "height_mm": height_mm,
                        "orientation": "landscape" if width_mm > height_mm else "portrait",
                    },
                })
        if ops:
            layout_ops.extend(ops)
    else:
        if typography.get("font_resource") or output.get("paper_profile"):
            disclosures.append(
                "该图未启用纸面版式（print_layout）——typography/paper 在发布期应用；"
                "样式与组件已按规范投影"
            )
    if _LOCK_UNIT in effective_locks:
        disclosures.append("unit_semantics locked：本计划不改变图例单位声明")

    has_changes = bool(style_actions or component_ensures or layout_ops)
    return {
        "apply_plan_version": APPLY_PLAN_VERSION,
        "has_changes": has_changes,
        "effective_locks": sorted(effective_locks),
        "style_actions": style_actions,
        "component_ensures": component_ensures,
        "layout_ops": layout_ops,
        "conflicts": conflicts,
        "skipped_locked": skipped_locked,
        "disclosures": disclosures,
        "spec_digest_basis": {
            "theme_id": (spec_payload.get("color") or {}).get("theme_id"),
            "palette": palette,
            "classification_method": method,
            "k": k_default,
        },
    }


async def dry_run_apply(
    session_id: str,
    spec_payload: Dict[str, Any],
    *,
    locked_fields: Tuple[str, ...] = (),
    role_field: Optional[str] = None,
    owner_token: Optional[str] = None,
) -> Dict[str, Any]:
    """dry-run：计划 + 逐层真实 preview 回执（零 mutation）。"""
    from app.services.mapspec_store import mapspec_store
    from app.services.thematic_authoring.contract import ThematicAuthoringPlan
    from app.services.thematic_authoring.service import preview_expression

    store = mapspec_store
    document = await store.get_mapspec(str(session_id))
    if not isinstance(document, dict) or not document:
        from app.services.design_specs.errors import DesignSpecNotFoundError

        raise DesignSpecNotFoundError(
            f"session '{str(session_id)[:64]}' has no committed MapSpec"
        )
    plan = build_apply_plan(
        spec_payload, document,
        locked_fields=locked_fields, role_field=role_field,
    )
    previews: List[Dict[str, Any]] = []
    for action in plan["style_actions"]:
        try:
            theme_plan = ThematicAuthoringPlan(
                layer_id=action["layer_id"],
                mode="graduated_auto",
                method=action.get("method"),
                k=action.get("k"),
                palette=action.get("palette"),
            )
            receipt = await preview_expression(
                str(session_id), action["layer_id"], theme_plan,
                owner_token=owner_token, trusted_dispatch=True,
            )
            previews.append({
                "layer_id": action["layer_id"],
                "ok": True,
                "candidate_fingerprint": receipt.get("candidate_fingerprint"),
                "current_fingerprint": receipt.get("current_fingerprint"),
            })
        except Exception as exc:  # noqa: BLE001 —— 单层预览失败如实记录
            previews.append({
                "layer_id": action["layer_id"],
                "ok": False,
                "error": str(exc)[:200],
            })
    plan["style_previews"] = previews
    plan["session_id"] = str(session_id)[:80]
    return plan


async def apply_design_spec(
    session_id: str,
    spec_payload: Dict[str, Any],
    *,
    expected_revision: int,
    locked_fields: Tuple[str, ...] = (),
    role_field: Optional[str] = None,
    origin: str = "user",
    actor: str = "design_spec_apply",
    owner_token: Optional[str] = None,
) -> Dict[str, Any]:
    """真实应用：逐层 CAS 提交 + 组件 ensure CAS；返回逐项回执。

    语义如实：样式逐层各自 CAS（一次一层）；组件 ensure 一笔 CAS。
    任一单项失败 → 该项 ``failed`` 回执，其余继续（分批语义，不宣称原子）。
    """
    from app.services.gis_world_state import apply_gis_mutation
    from app.services.mapspec.intents import PatchComponentIntent
    from app.services.mapspec_store import mapspec_store
    from app.services.thematic_authoring.contract import ThematicAuthoringPlan
    from app.services.thematic_authoring.service import commit_expression

    store = mapspec_store
    document = await store.get_mapspec(str(session_id))
    if not isinstance(document, dict) or not document:
        from app.services.design_specs.errors import DesignSpecNotFoundError

        raise DesignSpecNotFoundError(
            f"session '{str(session_id)[:64]}' has no committed MapSpec"
        )
    plan = build_apply_plan(
        spec_payload, document,
        locked_fields=locked_fields, role_field=role_field,
    )
    if not plan["has_changes"]:
        return {**plan, "applied": False, "reason": "no_changes", "items": []}

    items: List[Dict[str, Any]] = []
    revision = int(expected_revision)

    # 1) 样式轴：逐层 commit（每层一次 CAS；失败不断批）
    for action in plan["style_actions"]:
        try:
            theme_plan = ThematicAuthoringPlan(
                layer_id=action["layer_id"],
                mode="graduated_auto",
                method=action.get("method"),
                k=action.get("k"),
                palette=action.get("palette"),
            )
            receipt = await commit_expression(
                str(session_id), action["layer_id"], theme_plan,
                expected_revision=revision,
                origin=origin, actor=actor,
                owner_token=owner_token, trusted_dispatch=True,
            )
            new_revision = receipt.get("revision")
            items.append({
                "kind": "style",
                "layer_id": action["layer_id"],
                "status": "committed" if receipt.get("committed") else "skipped",
                "revision": new_revision,
                "changes": action.get("changes", []),
            })
            if isinstance(new_revision, int):
                revision = new_revision
        except Exception as exc:  # noqa: BLE001 —— 单层失败如实记录，继续其余
            message = str(exc)
            # R1-P3：typed 判定（异常 code 属性），不再靠 message 关键词猜
            code = str(getattr(exc, "code", "") or "")
            if code.upper() == "SUPERSEDED" or "superseded" in message.lower():
                status = "superseded"
            else:
                status = "failed"
            items.append({
                "kind": "style",
                "layer_id": action["layer_id"],
                "status": status,
                "error": message[:200],
            })

    # 2) 组件轴：缺组件 upsert（一笔一个；共享 revision 链）
    for ensure in plan["component_ensures"]:
        try:
            result = await apply_gis_mutation(
                str(session_id),
                PatchComponentIntent(
                    component_id=f"spec-{str(ensure['component_type'])}",
                    component_type=str(ensure["component_type"]),
                    variant=ensure.get("variant"),
                    enabled=True,
                    upsert=True,
                ),
                origin=origin, actor=actor,
                expected_revision=revision,
            )
            new_revision = getattr(result, "revision", None)
            ok = not getattr(result, "is_error", True)
            items.append({
                "kind": "component",
                "component_type": ensure["component_type"],
                "status": "committed" if ok else "failed",
                "revision": new_revision,
                "error": None if ok else str(getattr(result, "error", "") or "")[:160],
            })
            if isinstance(new_revision, int) and ok:
                revision = new_revision
        except Exception as exc:  # noqa: BLE001
            items.append({
                "kind": "component",
                "component_type": ensure["component_type"],
                "status": "failed",
                "error": str(exc)[:200],
            })

    committed = sum(1 for i in items if i["status"] == "committed")
    failed = sum(1 for i in items if i["status"] == "failed")
    return {
        **plan,
        "applied": True,
        "items": items,
        "summary": {
            "total": len(items),
            "committed": committed,
            "failed": failed,
            "superseded": sum(1 for i in items if i["status"] == "superseded"),
            "final_revision": revision,
        },
    }


__all__ = [
    "APPLY_PLAN_VERSION",
    "build_apply_plan",
    "dry_run_apply",
    "apply_design_spec",
]
