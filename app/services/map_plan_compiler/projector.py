"""MapPlanIR 投影层（F12 / ADR-0214 D2）。

职责边界：把**权威决策产物**（MapProductPlan / GrammarDecision / workbench
锁快照）转录为 refs-only MapPlanIR；多轮演进走 typed PlanAmendment →
``amend_plan_ir``。本模块**不重新推断** field semantics / grammar / 任务
流程 —— 任何语义都来自上游权威 ref；投影失败（超界/未知目标/锁冲突）
一律抛 ValueError（fail-closed），绝不静默裁剪用户意图。
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional, Sequence

from app.lib.cartography.plan_ir import (
    AmendmentKind,
    AnalysisOutputRef,
    AuthorityRef,
    ComponentIntent,
    DatasetRef,
    EvidenceRef,
    ExportObligation,
    FinalDisplayObligation,
    LayerBlueprint,
    LayerIntent,
    LayoutObligation,
    MapPlanIR,
    ROLE_ACTIONS,
    RequirementRef,
    UserLockSnapshot,
    compute_ir_id,
    digest_of,
)
from app.services.map_plan_compiler.plan_amendment import PlanAmendment

__all__ = ["PlanAmendment", "project_plan_ir", "amend_plan_ir"]


# ── grammar → registry 组件词表对齐（grammar 用制图惯用语，registry 是
#    MapSpec 组件真相词表；映射显式、可审计，未列出 = 同名直通）──────────
_GRAMMAR_COMPONENT_ALIASES: Dict[str, str] = {
    "colorbar": "continuous_colorbar",
}

#: grammar 义务里不可缺席的组件族（与 layout_participants 的 optional=False
#: 集合一致 —— title/legend/scale_bar/north_arrow/attribution）。
_REQUIRED_GRAMMAR_COMPONENTS = frozenset((
    "title", "legend", "scale_bar", "north_arrow", "attribution",
))


def _text_digest(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:32]


def _authority(kind: str, ref_id: str, fingerprint: str, version: str) -> AuthorityRef:
    return AuthorityRef(kind=kind, ref_id=str(ref_id)[:128],
                        fingerprint=str(fingerprint or "")[:80],
                        schema_version=str(version or "")[:32])


def _lock_snapshot_of(user_locks: Optional[UserLockSnapshot]) -> UserLockSnapshot:
    if user_locks is None:
        return UserLockSnapshot()
    return user_locks


def project_plan_ir(
    plan: Any,
    *,
    grammar: Any = None,
    user_locks: Optional[UserLockSnapshot] = None,
    requirements: Sequence[RequirementRef] = (),
    datasets: Optional[Sequence[DatasetRef]] = None,
    analysis_outputs: Sequence[AnalysisOutputRef] = (),
    exports: Sequence[ExportObligation] = (),
    output_purpose: str = "screen_16_9",
    viewport_px: Sequence[int] = (1280, 720),
    purpose: str = "",
    audience: str = "",
    medium: str = "",
    pinned_zones: Optional[Dict[str, str]] = None,
    layer_paint_overrides: Optional[Dict[str, Dict[str, Any]]] = None,
) -> MapPlanIR:
    """MapProductPlan (+GrammarDecision +lock 快照) → MapPlanIR（纯投影）。

    ``plan`` 接受 MapProductPlan（或同形 dict）；``grammar`` 接受
    GrammarDecision（或 None —— 义务组件退化回 plan.components）。
    ``layer_paint_overrides``：layer_id → 有界 paint 子集（来自 grammar
    symbology 或调用方显式 pin），受 LayerBlueprint 字节闸约束。
    """
    locks = _lock_snapshot_of(user_locks)
    locked_layers = frozenset(locks.layer_ids)
    locked_components = frozenset(locks.component_ids)
    overrides = layer_paint_overrides or {}

    # ── datasets：调用方提供的富引用优先；否则从 plan.data_requirements 转录
    if datasets is None:
        datasets = [
            DatasetRef(
                dataset_id=str(dr.capability)[:128],
                bound_ref=str(getattr(dr, "bound_ref", "") or "")[:128],
            )
            for dr in (plan.data_requirements or [])
        ]

    # ── authorities（全部引用，不复制内容）─────────────────────────────
    authorities: List[AuthorityRef] = [
        _authority("plan", plan.plan_id, "", ""),
        _authority("recipe", plan.recipe_id, "", "recipe"),
    ]
    template_id = str(plan.template_id or "")
    if template_id:
        sel = plan.template_selection or {}
        authorities.append(_authority(
            "template", template_id,
            str(sel.get("fingerprint", "") or ""), str(sel.get("version", "") or "")))
    manifest_fp = str(plan.manifest_fingerprint or "")
    if manifest_fp:
        authorities.append(_authority("capability_manifest", "runtime_manifest", manifest_fp, "4"))
    grammar_fp = ""
    grammar_version = ""
    if grammar is not None:
        grammar_fp = str(getattr(grammar, "fingerprint", "") or "")
        grammar_version = str(getattr(grammar, "grammar_version", "") or "")
        authorities.append(_authority("grammar_decision", "grammar", grammar_fp, grammar_version))

    # ── layer intents（计划顺序 = 权威顺序；id 只依赖索引，确定性）──────
    layer_intents: List[LayerIntent] = []
    for idx, pl in enumerate(plan.map_layers or [], start=1):
        intent_id = f"li-{idx:02d}-{pl.role}"
        layer_id = str(pl.layer_id or f"pl-{intent_id}")[:128]
        action = ROLE_ACTIONS.get(pl.role, "present_secondary")
        bp = LayerBlueprint(
            layer_type=str(pl.layer_type or "circle")[:48],
            cartography=str(pl.cartography or "")[:96],
            paint=dict(overrides.get(layer_id) or {}),
        )
        layer_intents.append(LayerIntent(
            intent_id=intent_id,
            action=action,  # type: ignore[arg-type]
            layer_id=layer_id,
            title=str(pl.note or "")[:256],
            source_ref=str(pl.bound_ref or "")[:128],
            blueprint=bp,
            expected_visible=bool(pl.enabled),
            locked=layer_id in locked_layers,
            evidence_refs=[EvidenceRef(kind="symbology_rationale",
                                       ref=f"recipe:{plan.recipe_id}"[:192])],
        ))

    # ── component intents：plan.components + grammar 义务（去重合并）────
    component_intents: List[ComponentIntent] = []
    seen_types: set[str] = set()
    for idx, comp in enumerate(plan.components or [], start=1):
        ctype = str(getattr(comp, "type", "") or "")
        cid = str(getattr(comp, "id", "") or f"comp-{ctype}-{idx:02d}")[:128]
        options = dict(getattr(comp, "options", {}) or {})
        component_intents.append(ComponentIntent(
            intent_id=f"ci-{idx:02d}-{ctype}"[:128],
            component_type=ctype,
            component_id=cid,
            action="ensure",
            required=False,
            title=str(options.get("title", "") or "")[:256],
            options=options,
            bound_layer_id="",
            pinned_zone=str((pinned_zones or {}).get(cid, ""))[:48],
            locked=cid in locked_components,
        ))
        seen_types.add(ctype)
    if grammar is not None:
        for ctype in (grammar.component_obligations or []):
            ctype = str(ctype)
            aligned = _GRAMMAR_COMPONENT_ALIASES.get(ctype, ctype)
            if aligned in seen_types:
                continue
            cid = f"comp-{aligned}"
            component_intents.append(ComponentIntent(
                intent_id=f"ci-g-{aligned}"[:128],
                component_type=aligned,
                component_id=cid,
                action="ensure",
                required=aligned in _REQUIRED_GRAMMAR_COMPONENTS,
                options={},
                bound_layer_id="",
                pinned_zone=str((pinned_zones or {}).get(cid, ""))[:48],
                locked=cid in locked_components,
            ))
            seen_types.add(aligned)

    # ── exports：显式参数优先；否则从 plan.exports / intent.export_intents
    export_obs: List[ExportObligation] = list(exports)
    if not export_obs:
        seen_fmts: set[str] = set()
        for fmt in list(plan.exports or []) + list(getattr(plan.intent, "export_intents", []) or []):
            word = str(fmt).strip().lower()[:24]
            if word and word not in seen_fmts:
                seen_fmts.add(word)
                export_obs.append(ExportObligation(fmt=word))

    # ── final display 义务（DoD「最终显示确认」的期望面）─────────────────
    final_display = FinalDisplayObligation(
        expected_visible_layers={
            li.intent_id: li.expected_visible
            for li in layer_intents if li.action != "remove"
        },
        expected_components=[
            ci.intent_id for ci in component_intents if ci.required and ci.action != "remove"
        ],
    )

    evidence: List[EvidenceRef] = []
    if grammar is not None:
        evidence.append(EvidenceRef(kind="grammar_audit", ref=f"grammar:{grammar_fp or 'none'}",
                                    digest=digest_of(grammar.model_dump())[:32]
                                    if hasattr(grammar, "model_dump") else ""))
    disclosures: List[str] = []
    if plan.methodology_warnings:
        disclosures.append(f"methodology_warnings:{len(plan.methodology_warnings)}")
    if plan.fallbacks:
        disclosures.append(f"plan_fallbacks:{len(plan.fallbacks)}")

    payload: Dict[str, Any] = {
        "ir_version": MapPlanIR.model_fields["ir_version"].default,
        "revision": 1,
        "supersedes": "",
        "requirements": [r.model_dump() for r in requirements],
        "datasets": [d.model_dump() for d in datasets],
        "authorities": [a.model_dump() for a in authorities],
        "analysis_outputs": [o.model_dump() for o in analysis_outputs],
        "layer_intents": [li.model_dump() for li in layer_intents],
        "component_intents": [ci.model_dump() for ci in component_intents],
        "layout": LayoutObligation(
            output_purpose=str(output_purpose)[:48],
            viewport_px=[int(viewport_px[0]), int(viewport_px[1])],
            pinned_zones={str(k)[:128]: str(v)[:48] for k, v in (pinned_zones or {}).items()},
            purpose=str(purpose)[:48], audience=str(audience)[:48], medium=str(medium)[:48],
        ).model_dump(),
        "exports": [e.model_dump() for e in export_obs],
        "final_display": final_display.model_dump(),
        "user_locks": locks.model_dump(),
        "evidence": [ev.model_dump() for ev in evidence],
        "disclosures": disclosures,
        "plan_fingerprint": str(compute_plan_fingerprint(plan))[:80],
    }
    ir_id = compute_ir_id(payload)
    return MapPlanIR(ir_id=ir_id, **payload)


def compute_plan_fingerprint(plan: Any) -> str:
    """MapProductPlan 的内容指纹转录（plan_runtime 口径不引入 —— 这里是
    projector 本地的身份指纹：与 MapPlanIR AuthorityRef.plan 对应）。"""
    body = {
        "plan_id": getattr(plan, "plan_id", ""),
        "recipe_id": getattr(plan, "recipe_id", ""),
        "template_id": getattr(plan, "template_id", ""),
        "manifest_fingerprint": getattr(plan, "manifest_fingerprint", ""),
        "layers": [
            [getattr(l, "role", ""), getattr(l, "layer_type", ""),
             getattr(l, "cartography", ""), getattr(l, "bound_ref", ""),
             bool(getattr(l, "enabled", True))]
            for l in (getattr(plan, "map_layers", []) or [])
        ],
        "components": [str(getattr(c, "id", "")) for c in (getattr(plan, "components", []) or [])],
        "exports": list(getattr(plan, "exports", []) or []),
    }
    return "planfp-" + digest_of(body)[:32]


# ── 多轮 amendment（ADR-0214 D2）────────────────────────────────────────

_AMENDMENT_UNSET = object()


def _find_layer(layers: List[Dict[str, Any]], layer_id: str) -> Optional[int]:
    for i, li in enumerate(layers):
        if li.get("layer_id") == layer_id:
            return i
    return None


def _find_component(components: List[Dict[str, Any]], component_id: str) -> Optional[int]:
    for i, ci in enumerate(components):
        if ci.get("component_id") == component_id:
            return i
    return None


def _amend_evidence(seq: int, kind: str) -> EvidenceRef:
    return EvidenceRef(kind="amendment", ref=f"amend:{seq:02d}:{kind}"[:192])


def amend_plan_ir(
    base: MapPlanIR,
    amendments: Sequence[PlanAmendment],
) -> MapPlanIR:
    """在 base IR 上确定性应用 amendment 序列 → revision+1 新 IR。

    纪律：只触碰 amendment 指名的节点；锁目标 fail-closed（ValueError）；
    未知名目标 fail-closed。同一 (base, amendments) 输入字节级同输出。
    """
    payload = base.model_dump()
    layers: List[Dict[str, Any]] = payload["layer_intents"]
    components: List[Dict[str, Any]] = payload["component_intents"]
    locked_layers = set(payload["user_locks"]["layer_ids"])
    locked_components = set(payload["user_locks"]["component_ids"])
    evidence: List[Dict[str, Any]] = payload["evidence"]
    disclosures: List[str] = payload["disclosures"]
    reason_codes: List[str] = payload["reason_codes"]

    chart_seq = sum(1 for ci in components if ci["intent_id"].startswith("ci-a-"))
    for seq, am in enumerate(amendments, start=1):
        kind = am.kind
        ev = _amend_evidence(seq, kind).model_dump()
        evidence.append(ev)
        if len(evidence) > 32:  # MAX_EVIDENCE
            del evidence[:-32]

        if kind == "add_chart":
            chart_seq += 1
            ctype = am.chart_type or "chart_panel"
            cid = am.component_id or f"comp-chart-{chart_seq:02d}"
            components.append({
                "intent_id": f"ci-a-{chart_seq:02d}-{ctype}"[:128],
                "component_type": ctype,
                "component_id": cid,
                "action": "ensure",
                "required": False,
                "title": am.title[:256],
                "options": {"title": am.title} if am.title else {},
                "bound_layer_id": am.layer_id[:128],
                "pinned_zone": am.zone[:48],
                "locked": cid in locked_components,
                "evidence_refs": [ev],
                "reason_codes": ["AMEND_ADD_CHART"],
            })
            if len(components) > 48:  # MAX_COMPONENT_INTENTS
                raise ValueError("amendment 超出组件意图上限 48")

        elif kind == "set_layer_visibility":
            idx = _find_layer(layers, am.layer_id)
            if idx is None:
                raise ValueError(f"set_layer_visibility: 未知 layer_id={am.layer_id!r}")
            if am.layer_id in locked_layers:
                raise ValueError(f"set_layer_visibility: layer {am.layer_id!r} 已被用户锁定（user-wins）")
            li = dict(layers[idx])
            li["action"] = "set_visibility"
            li["expected_visible"] = bool(am.visible)
            li["origin"] = "user"
            li["reason_codes"] = (li.get("reason_codes") or []) + ["AMEND_VISIBILITY"]
            layers[idx] = li

        elif kind == "restyle_layer":
            idx = _find_layer(layers, am.layer_id)
            if idx is None:
                raise ValueError(f"restyle_layer: 未知 layer_id={am.layer_id!r}")
            if am.layer_id in locked_layers:
                raise ValueError(f"restyle_layer: layer {am.layer_id!r} 已被用户锁定（user-wins）")
            li = dict(layers[idx])
            bp = dict(li.get("blueprint") or {})
            if am.paint:
                merged = dict(bp.get("paint") or {})
                merged.update(am.paint)
                bp["paint"] = merged
            if am.classification:
                merged_cls = dict(bp.get("classification") or {})
                merged_cls.update(am.classification)
                bp["classification"] = merged_cls
            check_bp = LayerBlueprint(**bp)  # 有界闸复用（超界构造期拒绝）
            li["blueprint"] = check_bp.model_dump()
            li["action"] = "restyle"
            li["origin"] = "user"
            li["reason_codes"] = (li.get("reason_codes") or []) + ["AMEND_RESTYLE"]
            layers[idx] = li

        elif kind == "set_title":
            title = am.title[:256]
            target_cid = am.component_id or "comp-title"
            if target_cid in locked_components:
                raise ValueError(
                    f"set_title: component {target_cid!r} 已被用户锁定（user-wins）")
            idx = _find_component(components, target_cid)
            if idx is None:
                components.append({
                    "intent_id": "ci-a-title",
                    "component_type": "title",
                    "component_id": am.component_id or "comp-title",
                    "action": "ensure",
                    "required": True,
                    "title": title,
                    "options": {"title": title},
                    "bound_layer_id": "",
                    "pinned_zone": "",
                    "locked": (am.component_id or "comp-title") in locked_components,
                    "evidence_refs": [ev],
                    "reason_codes": ["AMEND_SET_TITLE"],
                })
            else:
                ci = dict(components[idx])
                ci["action"] = "patch"
                ci["title"] = title
                ci["options"] = dict(ci.get("options") or {}) | {"title": title}
                ci["reason_codes"] = (ci.get("reason_codes") or []) + ["AMEND_SET_TITLE"]
                components[idx] = ci

        elif kind == "add_export":
            word = am.fmt.strip().lower()[:24]
            if not word:
                raise ValueError("add_export: fmt 为空")
            if not any(e["fmt"] == word for e in payload["exports"]):
                payload["exports"].append(
                    {"fmt": word, "required": True, "reason_codes": ["AMEND_EXPORT"]})

        elif kind == "pin_component_zone":
            idx = _find_component(components, am.component_id)
            if idx is None:
                raise ValueError(f"pin_component_zone: 未知 component_id={am.component_id!r}")
            if am.component_id in locked_components:
                raise ValueError(
                    f"pin_component_zone: component {am.component_id!r} 已被用户锁定（user-wins）")
            ci = dict(components[idx])
            ci["pinned_zone"] = am.zone[:48]
            ci["reason_codes"] = (ci.get("reason_codes") or []) + ["AMEND_PIN_ZONE"]
            components[idx] = ci
            payload["layout"]["pinned_zones"][am.component_id] = am.zone[:48]

        elif kind == "remove_layer":
            idx = _find_layer(layers, am.layer_id)
            if idx is None:
                raise ValueError(f"remove_layer: 未知 layer_id={am.layer_id!r}")
            if am.layer_id in locked_layers:
                raise ValueError(f"remove_layer: layer {am.layer_id!r} 已被用户锁定（user-wins）")
            li = dict(layers[idx])
            li["action"] = "remove"
            li["expected_visible"] = False
            li["origin"] = "user"
            li["reason_codes"] = (li.get("reason_codes") or []) + ["AMEND_REMOVE"]
            layers[idx] = li

        elif kind == "remove_component":
            idx = _find_component(components, am.component_id)
            if idx is None:
                raise ValueError(f"remove_component: 未知 component_id={am.component_id!r}")
            if am.component_id in locked_components:
                raise ValueError(
                    f"remove_component: component {am.component_id!r} 已被用户锁定（user-wins）")
            ci = dict(components[idx])
            ci["action"] = "remove"
            ci["required"] = False
            ci["reason_codes"] = (ci.get("reason_codes") or []) + ["AMEND_REMOVE"]
            components[idx] = ci

        else:  # pragma: no cover — Literal 已封闭
            raise ValueError(f"未知 amendment kind={kind!r}")

        code = f"AMEND_{kind.upper()}"
        if code not in reason_codes:
            reason_codes = reason_codes + [code]

    # final display 义务随意图面重算（remove 不再进入期望面）
    payload["final_display"] = {
        "expected_visible_layers": {
            li["intent_id"]: bool(li["expected_visible"])
            for li in layers if li["action"] != "remove"
        },
        "expected_components": [
            ci["intent_id"] for ci in components if ci["required"] and ci["action"] != "remove"
        ],
        "ack_mode": payload["final_display"].get("ack_mode", "auto"),
        "reason_codes": ["AMENDED"],
    }
    payload["revision"] = int(base.revision) + 1
    payload["supersedes"] = base.ir_id
    payload["evidence"] = evidence
    payload["disclosures"] = disclosures
    payload["reason_codes"] = reason_codes[-12:]
    payload.pop("ir_id", None)
    ir_id = compute_ir_id(payload)
    return MapPlanIR(ir_id=ir_id, **payload)
