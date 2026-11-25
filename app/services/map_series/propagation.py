"""Map Series 传播（WP3）—— preview（只读）与 apply（逐图 CAS）。

权威纪律（简报红线 3/4）：

- **传播 = 逐图 CAS**：每成员经既有 mutation 通道
  （``apply_gis_mutation`` → lifecycle engine）独立落图；单成员失败不
  影响后续；绝不宣称原子批量 —— 逐图 receipt
  ``{member_id, status: committed|superseded|failed|skipped_locked,
  revision, error?}``。
- **不把某图 ref 传播到其他图**：意图只能是规范投影出的表达意图
  （palette / 分类方法 / 组件 / 版式参数）；数据引用（ref:）逐图绑定
  各自会话 —— 本模块的 intent 模型根本没有数据载体字段。
- **user-wins**：成员 ``locked_fields`` 命中意图对应表达字段 → 该意图
  skip 并披露；引擎层锁拒绝（locked_layer_ids / locked_component_ids）
  同样如实披露。

分类方法传播的诚实边界：method/k 是方法声明；旧方法算出的 breaks 属于
数据值（本就禁止跨图复制）—— 传播时随 legend_spec 重置 breaks，receipt
``skipped_intents``/``changes`` 如实披露 ``breaks_reset``。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.services.map_series import service as ms_service
from app.services.map_series.consistency import INTENT_LOCK_FIELDS
from app.services.map_series.contract import CLASSIFICATION_METHODS
from app.services.map_series.errors import MapSeriesValidationError
from app.services.map_series.store import get_series, get_version, resolve_payload

logger = logging.getLogger(__name__)

_MAX_COMPONENT_OPS = 8
_HEX = r"#[0-9a-fA-F]{3,8}"

_ORDER = ("palette", "classification", "components", "typography")


class _IntentModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PaletteIntent(_IntentModel):
    """色带表达意图：目标层 paint 主色 + 可选分级 ramp（表达值，非数据值）。"""

    target_layer_id: str = Field(max_length=80)
    color: Optional[str] = Field(default=None, pattern=_HEX, max_length=9)
    ramp: List[str] = Field(default_factory=list, max_length=12)

    @field_validator("ramp")
    @classmethod
    def _v_ramp(cls, v: List[str]) -> List[str]:
        import re

        bad = [c for c in v if not re.fullmatch(_HEX, str(c))]
        if bad:
            raise ValueError(f"ramp entries must be hex colors: {bad[:4]}")
        return v


class ClassificationIntent(_IntentModel):
    """分类方法意图（只声明 method/k；breaks 逐图按各自数据重算）。"""

    target_layer_id: str = Field(max_length=80)
    method: str
    k: Optional[int] = Field(default=None, ge=2, le=12)

    @field_validator("method")
    @classmethod
    def _v_method(cls, v: str) -> str:
        if v not in CLASSIFICATION_METHODS:
            raise ValueError(
                f"unknown classification method: {v} (allowed: {CLASSIFICATION_METHODS})"
            )
        return v


class ComponentOp(_IntentModel):
    """组件意图：ensure / remove / update（经 PatchComponentIntent 通道）。"""

    component_type: str = Field(max_length=64)
    op: str = "ensure"
    options: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("op")
    @classmethod
    def _v_op(cls, v: str) -> str:
        if v not in ("ensure", "remove", "update"):
            raise ValueError("op must be ensure|remove|update")
        return v

    @field_validator("options")
    @classmethod
    def _v_options(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        if len(v) > 12:
            raise ValueError("options over budget (>12 keys)")
        return v


class TypographyIntent(_IntentModel):
    """版式参数意图（落在 title 组件 style.typography）。"""

    font_resource: Optional[str] = Field(default=None, max_length=64)
    title_size_pt: Optional[float] = Field(default=None, ge=8.0, le=48.0)
    base_size_pt: Optional[float] = Field(default=None, ge=6.0, le=24.0)


class PropagationIntents(_IntentModel):
    """传播意图集合（preview/apply 两侧同构；typed，extra=forbid）。"""

    palette: Optional[PaletteIntent] = None
    classification: Optional[ClassificationIntent] = None
    components: List[ComponentOp] = Field(default_factory=list, max_length=_MAX_COMPONENT_OPS)
    typography: Optional[TypographyIntent] = None

    @field_validator("components")
    @classmethod
    def _v_components(cls, v: List[ComponentOp]) -> List[ComponentOp]:
        if len(v) > _MAX_COMPONENT_OPS:
            raise ValueError(f"component ops over budget (>{_MAX_COMPONENT_OPS})")
        return v

    def present(self) -> List[str]:
        dump = self.model_dump(exclude_none=True)
        keys = [k for k in _ORDER if dump.get(k) not in (None, [], {})]
        return [k for k in _ORDER if k in keys]


def parse_intents(intents: Dict[str, Any]) -> PropagationIntents:
    try:
        return PropagationIntents.model_validate(intents)
    except ValidationError as exc:
        raise MapSeriesValidationError(
            "propagation intents failed validation",
            violations=[
                {
                    "field": ".".join(str(p) for p in err.get("loc", ())),
                    "ref": "",
                    "reason": f"schema:{err.get('type', 'invalid')}",
                }
                for err in exc.errors()[:16]
            ],
        ) from exc


def _effective_plan(db: Any, series_id: str, spec_version: Optional[int]):
    """系列 effective 计划（spec_version pin → 版本深合并；缺省主行 payload）。"""
    series = get_series(db, series_id)
    if spec_version is not None:
        row = get_version(db, series_id, int(spec_version))
        effective, _chain = resolve_payload(db, row)
        return series, effective
    return series, (series.payload or {})


# ── 成员级计划（preview/apply 同构内核） ─────────────────────────────


def _series_member_pairs(plan_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    members = plan_payload.get("members")
    if not isinstance(members, list):
        return []
    return [m for m in members if isinstance(m, dict) and m.get("enabled", True)]


def _layer_by_id(mapspec: Dict[str, Any], layer_id: str) -> Optional[Dict[str, Any]]:
    for layer in ms_service.layers_of(mapspec):
        if str(layer.get("id") or "") == str(layer_id):
            return layer
    return None


def _component_by_type(mapspec: Dict[str, Any], ctype: str) -> Optional[Dict[str, Any]]:
    for comp in ms_service.components_of(mapspec):
        if str(comp.get("type") or "") == str(ctype):
            return comp
    return None


def _paint_color_key(layer: Dict[str, Any]) -> str:
    ltype = str(layer.get("type") or "fill")
    return {
        "fill": "fill-color",
        "circle": "circle-color",
        "line": "line-color",
        "heatmap": "heatmap-color",
        "fill-extrusion": "fill-extrusion-color",
    }.get(ltype, "fill-color")


def plan_member(
    member: Dict[str, Any],
    mapspec: Optional[Dict[str, Any]],
    intents: PropagationIntents,
) -> Dict[str, Any]:
    """单成员传播计划（纯函数；preview 直接用，apply 据此执行）。"""
    locked = set(str(x) for x in (member.get("locked_fields") or []))
    plan: Dict[str, Any] = {
        "member_id": member.get("member_id"),
        "session_id": member.get("session_id"),
        "unresolvable": mapspec is None,
        "locked_fields_hit": [],
        "changes": [],
        "skipped": [],
    }

    def _note_locked(name: str) -> None:
        field = INTENT_LOCK_FIELDS[name]
        plan["locked_fields_hit"].append(field)
        plan["skipped"].append(
            {"intent": name, "reason": "user_locked", "field": field}
        )

    for name in intents.present():
        if name in ("palette", "classification", "typography", "components"):
            field = INTENT_LOCK_FIELDS[name]
            if field in locked:
                _note_locked(name)
                continue
        if name == "palette":
            intent = intents.palette
            assert intent is not None  # noqa: S101 — present() 保证
            layer = _layer_by_id(mapspec or {}, intent.target_layer_id)
            if layer is None:
                plan["skipped"].append(
                    {"intent": name, "reason": "target_layer_missing"}
                )
                continue
            paint = layer.get("paint") if isinstance(layer.get("paint"), dict) else {}
            current_color = paint.get(_paint_color_key(layer))
            if intent.color and intent.ramp:
                plan["changes"].append(
                    {
                        "intent": name,
                        "action": "patch_paint_and_ramp",
                        "component": intent.target_layer_id,
                        "detail": {"color": intent.color, "ramp_len": len(intent.ramp)},
                    }
                )
            elif intent.color and str(current_color or "") == intent.color:
                plan["skipped"].append({"intent": name, "reason": "no_op"})
                continue
            elif intent.color:
                plan["changes"].append(
                    {
                        "intent": name,
                        "action": "patch_paint",
                        "component": intent.target_layer_id,
                        "detail": {"color": intent.color, "ramp_len": 0},
                    }
                )
            elif intent.ramp:
                plan["changes"].append(
                    {
                        "intent": name,
                        "action": "set_ramp",
                        "component": intent.target_layer_id,
                        "detail": {"color": None, "ramp_len": len(intent.ramp)},
                    }
                )
            else:
                plan["skipped"].append({"intent": name, "reason": "no_op"})
                continue
        elif name == "classification":
            intent = intents.classification
            assert intent is not None  # noqa: S101
            layer = _layer_by_id(mapspec or {}, intent.target_layer_id)
            if layer is None:
                plan["skipped"].append(
                    {"intent": name, "reason": "target_layer_missing"}
                )
                continue
            current = ms_service.legend_method(layer)
            if current == intent.method and (
                intent.k is None
                or not isinstance(layer.get("legend_spec"), dict)
                or layer["legend_spec"].get("k") == intent.k
            ):
                plan["skipped"].append({"intent": name, "reason": "no_op"})
                continue
            plan["changes"].append(
                {
                    "intent": name,
                    "action": "set_legend_method",
                    "component": intent.target_layer_id,
                    "detail": {
                        "from": current,
                        "to": intent.method,
                        "k": intent.k,
                        "breaks_reset": True,
                    },
                }
            )
        elif name == "components":
            for op_index, op in enumerate(intents.components):
                existing = _component_by_type(mapspec or {}, op.component_type)
                if op.op == "remove":
                    if existing is None:
                        plan["skipped"].append(
                            {
                                "intent": name,
                                "reason": "component_missing",
                                "component_type": op.component_type,
                            }
                        )
                        continue
                    plan["changes"].append(
                        {
                            "intent": name,
                            "action": "remove_component",
                            "component": str(existing.get("id") or ""),
                            "detail": {
                                "component_type": op.component_type,
                                "op_index": op_index,
                            },
                        }
                    )
                else:
                    action = "update_component" if existing is not None else "ensure_component"
                    plan["changes"].append(
                        {
                            "intent": name,
                            "action": action,
                            "component": str(
                                (existing or {}).get("id")
                                or f"series_{op.component_type}"
                            ),
                            "detail": {
                                "component_type": op.component_type,
                                "options_keys": sorted(op.options.keys()),
                                "op_index": op_index,
                            },
                        }
                    )
        elif name == "typography":
            intent = intents.typography
            assert intent is not None  # noqa: S101
            title = _component_by_type(mapspec or {}, "title")
            if title is None:
                plan["skipped"].append(
                    {"intent": name, "reason": "title_component_missing"}
                )
                continue
            plan["changes"].append(
                {
                    "intent": name,
                    "action": "patch_title_typography",
                    "component": str(title.get("id") or ""),
                    "detail": intent.model_dump(exclude_none=True),
                }
            )
    plan["eligible"] = bool(plan["changes"])
    return plan


async def build_propagation_preview(
    db: Any,
    series_id: str,
    *,
    spec_version: Optional[int] = None,
    intents: Dict[str, Any],
) -> Dict[str, Any]:
    """传播预览（只读）：受影响成员 / 将变更字段 / 锁定 / 不可解析 / eligibility。"""
    series, plan_payload = _effective_plan(db, series_id, spec_version)
    typed = parse_intents(intents)
    members = _series_member_pairs(plan_payload)
    out_members: List[Dict[str, Any]] = []
    for member in members:
        mapspec = await ms_service.load_member_mapspec(str(member.get("session_id") or ""))
        revision = await ms_service.member_revision(str(member.get("session_id") or ""))
        entry = plan_member(member, mapspec, typed)
        entry["current_revision"] = revision
        out_members.append(entry)
    return {
        "series_id": series.id,
        "member_count": len(members),
        "unresolvable_member_ids": [
            m["member_id"] for m in out_members if m["unresolvable"]
        ],
        "members": out_members,
    }


# ── apply：逐图 CAS，绝不宣称原子批量 ────────────────────────────────


def _build_engine_intents(
    mapspec: Dict[str, Any],
    change: Dict[str, Any],
    typed: PropagationIntents,
) -> List[Any]:
    """单条 plan change → 既有通道的 engine intent 列表（同通道逐笔 CAS）。"""
    name = change["intent"]
    out: List[Any] = []
    if name == "palette":
        from app.services.mapspec.intents import (
            PatchLayerStyleIntent,
            UpsertLayerIntent,
        )

        pi = typed.palette
        assert pi is not None  # noqa: S101 — plan 已保证
        layer = _layer_by_id(mapspec, pi.target_layer_id) or {}
        if pi.color:
            out.append(
                PatchLayerStyleIntent(
                    layer_id=pi.target_layer_id,
                    paint={_paint_color_key(layer): pi.color},
                )
            )
        if pi.ramp:
            patched = dict(layer)
            legend = dict(patched.get("legend_spec") or {})
            legend["colors"] = list(pi.ramp)
            patched["legend_spec"] = legend
            out.append(UpsertLayerIntent(layer=patched))
    elif name == "classification":
        from app.services.mapspec.intents import UpsertLayerIntent

        ci = typed.classification
        assert ci is not None  # noqa: S101
        layer = _layer_by_id(mapspec, ci.target_layer_id) or {}
        patched = dict(layer)
        legend = dict(patched.get("legend_spec") or {})
        legend["method"] = ci.method
        if ci.k is not None:
            legend["k"] = ci.k
        # 旧方法的 breaks 属于数据值 —— 传播只带方法声明，breaks 重置
        # （逐图按各自数据重算；plan 明细已披露 breaks_reset）。
        legend.pop("breaks", None)
        patched["legend_spec"] = legend
        out.append(UpsertLayerIntent(layer=patched))
    elif name == "components":
        from app.services.mapspec.intents import (
            PatchComponentIntent,
            RemoveComponentIntent,
        )

        op_index = int(change["detail"].get("op_index", 0))
        comp_op = typed.components[op_index]
        if comp_op.op == "remove":
            out.append(RemoveComponentIntent(component_id=str(change["component"])))
        else:
            out.append(
                PatchComponentIntent(
                    component_id=str(change["component"]),
                    component_type=comp_op.component_type,
                    options=comp_op.options or None,
                    upsert=True,
                )
            )
    elif name == "typography":
        from app.services.mapspec.intents import PatchComponentIntent

        ti = typed.typography
        assert ti is not None  # noqa: S101
        out.append(
            PatchComponentIntent(
                component_id=str(change["component"]),
                style={"typography": ti.model_dump(exclude_none=True)},
            )
        )
    return out


async def _apply_intent_set(
    member: Dict[str, Any],
    mapspec: Dict[str, Any],
    plan: Dict[str, Any],
    typed: PropagationIntents,
    *,
    actor: str,
    series_id: str,
    expected_revision: Optional[int],
) -> Dict[str, Any]:
    """对单成员执行其 plan 里的 changes（逐意图独立 CAS）。返回 receipt。"""
    from app.services.gis_world_state.mutation import apply_gis_mutation

    session_id = str(member.get("session_id") or "")
    current_rev = expected_revision
    applied: List[Dict[str, Any]] = []
    for change in plan["changes"]:
        engine_intents = _build_engine_intents(mapspec, change, typed)
        if not engine_intents:
            continue
        for intent_obj in engine_intents:
            result = await apply_gis_mutation(
                session_id,
                intent_obj,
                origin="agent",
                actor=str(actor or "map-series")[:255],
                expected_revision=current_rev,
                reason=f"map_series:{series_id} propagation",
            )
            if result.superseded:
                return {
                    "member_id": member.get("member_id"),
                    "status": "superseded",
                    "revision": result.mutation_revision,
                    "error": result.error_msg or "MapSpec revision changed",
                    "applied_intents": applied,
                }
            if result.is_error:
                engine_locked = bool(
                    result.locked_layer_ids or result.locked_component_ids
                )
                if engine_locked:
                    return {
                        "member_id": member.get("member_id"),
                        "status": "skipped_locked",
                        "revision": result.mutation_revision,
                        "error": result.error_msg or "targets locked (user-wins)",
                        "locked_layer_ids": list(result.locked_layer_ids),
                        "locked_component_ids": list(result.locked_component_ids),
                        "applied_intents": applied,
                    }
                return {
                    "member_id": member.get("member_id"),
                    "status": "failed",
                    "revision": result.mutation_revision,
                    "error": result.error_msg or "mutation rejected",
                    "correction_hint": result.correction_hint,
                    "applied_intents": applied,
                }
            current_rev = result.mutation_revision
        applied.append(
            {
                "intent": change["intent"],
                "action": change["action"],
                "component": change.get("component"),
                "revision": current_rev,
            }
        )
    return {
        "member_id": member.get("member_id"),
        "status": "committed",
        "revision": current_rev,
        "applied_intents": applied,
    }


async def apply_propagation(
    db: Any,
    series_id: str,
    *,
    spec_version: Optional[int] = None,
    intents: Dict[str, Any],
    actor: str = "map-series",
    expected_revisions: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    """逐成员传播（one by one；每成员独立 try/except；失败不中断后续）。"""
    series, plan_payload = _effective_plan(db, series_id, spec_version)
    typed = parse_intents(intents)
    members = _series_member_pairs(plan_payload)
    expected_revisions = expected_revisions or {}
    receipts: List[Dict[str, Any]] = []
    for member in members:
        member_id = str(member.get("member_id") or "")
        session_id = str(member.get("session_id") or "")
        try:
            mapspec = await ms_service.load_member_mapspec(session_id)
            plan = plan_member(member, mapspec, typed)
            if plan["unresolvable"]:
                receipts.append(
                    {
                        "member_id": member_id,
                        "status": "failed",
                        "revision": None,
                        "error": "session MapSpec unavailable",
                    }
                )
                continue
            locked_only = plan["locked_fields_hit"] and not plan["changes"]
            if locked_only:
                receipts.append(
                    {
                        "member_id": member_id,
                        "status": "skipped_locked",
                        "revision": None,
                        "error": "all intents touch user-locked fields",
                        "locked_fields": plan["locked_fields_hit"],
                    }
                )
                continue
            if not plan["changes"]:
                receipts.append(
                    {
                        "member_id": member_id,
                        "status": "failed",
                        "revision": None,
                        "error": "no_applicable_intent",
                        "skipped_intents": plan["skipped"],
                    }
                )
                continue
            expected = expected_revisions.get(member_id)
            if expected is None:
                expected = await ms_service.member_revision(session_id)
            receipt = await _apply_intent_set(
                member,
                mapspec or {},
                plan,
                typed,
                actor=actor,
                series_id=series.id,
                expected_revision=expected,
            )
            if plan["skipped"]:
                receipt["skipped_intents"] = plan["skipped"]
            receipts.append(receipt)
        except Exception as exc:  # noqa: BLE001 —— 单成员失败绝不中断整批
            logger.warning(
                "map series %s propagation failed for member %s: %s",
                series_id, member_id, exc,
            )
            receipts.append(
                {
                    "member_id": member_id,
                    "status": "failed",
                    "revision": None,
                    "error": str(exc)[:200],
                }
            )
    summary = {
        "committed": sum(1 for r in receipts if r["status"] == "committed"),
        "superseded": sum(1 for r in receipts if r["status"] == "superseded"),
        "failed": sum(1 for r in receipts if r["status"] == "failed"),
        "skipped_locked": sum(1 for r in receipts if r["status"] == "skipped_locked"),
    }
    return {
        "series_id": series.id,
        "receipts": receipts,
        "summary": summary,
        "note": "per-member CAS via existing mutation channel; batch is not atomic",
    }


__all__ = [
    "PropagationIntents",
    "PaletteIntent",
    "ClassificationIntent",
    "ComponentOp",
    "TypographyIntent",
    "parse_intents",
    "build_propagation_preview",
    "apply_propagation",
    "plan_member",
]
