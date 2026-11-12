"""G27 尺度表达策略服务 —— 只读有效策略 + 局部受控写（CAS / undo）。

复用既有链，不新建状态层：
- 读：``mapspec_store_instance.get_mapspec``（committed desired state）；
- 写：``UpsertLayerIntent`` + ``apply_gis_mutation``（与 GOAL 04
  scale_profile.v1 同款单事务 CAS 链、provenance、undo 栈）；
- 解析：``app.lib.cartography.scale_portrayal`` 单一纯函数入口。

纪律：
- **局部写**：只改目标 layer 的 ``scale_profile.g27_scale_portrayal``
  子键与由它投影的既有 ``label.zoomBands``；其余 layer 字节不动（A20）。
- **不新增 spec 字段 / 不新增 intent**：policy 持久化在既有开放 dict
  ``layer.scale_profile`` 的命名空间内，desired state 仍是单一 MapSpec。
- **不把 observed zoom 写成 desired**：zoom 只用于解释面计算；显式
  policy 修改才走 mutation（A09）。
- **不绕过验权**：REST 入口挂 ``require_owned_session``；工具面经
  ToolRegistry 的 session/project 上下文与 tier 守卫。
- **未知策略 fail-safe**：typed diagnostics + 拒写，不静默当已应用。
"""
from __future__ import annotations

import copy
import logging
from typing import Any, Dict, List, Optional, Sequence

from app.lib.cartography import scale_portrayal as sp

logger = logging.getLogger(__name__)

SCHEMA_VERSION = sp.SCALE_POLICY_VERSION
NAMESPACE = sp.SCALE_POLICY_NAMESPACE if hasattr(sp, "SCALE_POLICY_NAMESPACE") else "g27_scale_portrayal"
MAX_FIELDS = 32
MAX_FIELD_SCAN_FEATURES = 50


class ScalePortrayalError(Exception):
    """服务级 typed 错误（工具/API 面转结构化 error，不泄漏栈）。"""


async def _load_spec(session_id: str) -> Dict[str, Any]:
    from app.services.mapspec.store import mapspec_store_instance

    spec = await mapspec_store_instance.get_mapspec(session_id)
    if not isinstance(spec, dict) or not spec:
        raise ScalePortrayalError("会话暂无已提交 MapSpec（先出图再调用本能力）")
    return spec


def _match_layer(spec: Dict[str, Any], target: str) -> Dict[str, Any]:
    layers = [lyr for lyr in (spec.get("layers") or []) if isinstance(lyr, dict)]
    exact = [lyr for lyr in layers if str(lyr.get("id") or "") == target]
    if len(exact) == 1:
        return exact[0]
    suffix = [lyr for lyr in layers if str(lyr.get("id") or "").endswith(target)]
    if len(suffix) == 1:
        return suffix[0]
    ids = [str(lyr.get("id")) for lyr in layers]
    raise ScalePortrayalError(
        f"未唯一命中图层 {target!r}；候选：{', '.join(ids[:12])}")


def _available_fields(spec: Dict[str, Any], layer: Dict[str, Any]) -> List[str]:
    """字段引用校验的证据面：只从 inline FC 有界扫描属性键（refs-only）。

    ref 载荷不在此读取数据（避免把大对象拉进解释面）；此时仅返回 schema
    已声明的 ``data_binding.field`` / ``label.field`` 等显式字段。
    """
    fields: List[str] = []
    for key in ("field", "class_field", "x_field", "y_field"):
        v = (layer.get("data_binding") or {}).get(key) \
            if isinstance(layer.get("data_binding"), dict) else None
        if isinstance(v, str) and v:
            fields.append(v)
    label = layer.get("label") if isinstance(layer.get("label"), dict) else {}
    for key in ("field", "priorityField", "priority_field"):
        v = label.get(key)
        if isinstance(v, str) and v:
            fields.append(v)

    src_id = layer.get("source")
    sources = spec.get("sources") if isinstance(spec.get("sources"), dict) else {}
    src = sources.get(src_id) if isinstance(src_id, str) else None
    if isinstance(src, dict):
        payload = src.get("inlineData") if isinstance(src.get("inlineData"), dict) else src.get("data")
        feats = (payload or {}).get("features") if isinstance(payload, dict) else None
        if isinstance(feats, list):
            for feat in feats[:MAX_FIELD_SCAN_FEATURES]:
                props = (feat or {}).get("properties") if isinstance(feat, dict) else None
                if isinstance(props, dict):
                    for k in props:
                        if isinstance(k, str) and k not in fields:
                            fields.append(k)
                        if len(fields) >= MAX_FIELDS:
                            return fields[:MAX_FIELDS]
    return fields[:MAX_FIELDS]


def _frame_of_spec(spec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    view = spec.get("view") if isinstance(spec.get("view"), dict) else {}
    center = view.get("center")
    if isinstance(center, (list, tuple)) and len(center) >= 2:
        return {"center": [center[0], center[1]]}
    return None


def _source_revision(spec: Dict[str, Any], layer: Dict[str, Any]) -> str:
    src_id = layer.get("source")
    sources = spec.get("sources") if isinstance(spec.get("sources"), dict) else {}
    src = sources.get(src_id) if isinstance(src_id, str) else None
    if isinstance(src, dict):
        for key in ("data_fingerprint", "ref_id", "ref"):
            v = src.get(key)
            if isinstance(v, str) and v:
                return v[:64]
    return ""


def _effective_zoom(spec: Dict[str, Any], observed_zoom: Optional[float]) -> float:
    """优先 observed camera（只读解释）；缺省回落 authored view.zoom。"""
    if isinstance(observed_zoom, (int, float)) and not isinstance(observed_zoom, bool):
        return float(observed_zoom)
    from app.lib.cartography.representation import export_zoom_of_mapspec
    return export_zoom_of_mapspec(spec)


def _components_of_spec(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """页面组件声明（有界 32 条）—— P3 的生产输入面。"""
    layout = spec.get("layout") if isinstance(spec.get("layout"), dict) else {}
    comps = layout.get("components")
    if not isinstance(comps, list):
        return []
    return [c for c in comps[:32] if isinstance(c, dict)]


def _resolve(
    spec: Dict[str, Any], layer: Dict[str, Any], *,
    observed_zoom: Optional[float] = None,
    crs: str = "EPSG:3857",
    target_dpi: float = 96.0,
) -> sp.EffectiveScalePortrayal:
    policy = (layer.get("scale_profile") or {}).get(NAMESPACE) \
        if isinstance(layer.get("scale_profile"), dict) else None
    return sp.resolve_effective_scale_portrayal(
        zoom=_effective_zoom(spec, observed_zoom),
        layer=layer,
        policy=policy,
        available_fields=_available_fields(spec, layer),
        source_revision=_source_revision(spec, layer),
        frame=_frame_of_spec(spec),
        crs=crs,
        target_dpi=target_dpi,
    )


async def inspect_scale_portrayal(
    session_id: str,
    layer: str,
    *,
    observed_zoom: Optional[float] = None,
    crs: str = "EPSG:3857",
    target_dpi: float = 96.0,
) -> Dict[str, Any]:
    """只读：返回与 UI 同一份有界有效策略解释（A22/A24）。"""
    spec = await _load_spec(session_id)
    hit = _match_layer(spec, layer)
    decision = _resolve(spec, hit, observed_zoom=observed_zoom,
                        crs=crs, target_dpi=target_dpi)
    policy = (hit.get("scale_profile") or {}).get(NAMESPACE) \
        if isinstance(hit.get("scale_profile"), dict) else None
    return {
        "status": "ok",
        "schema_version": SCHEMA_VERSION,
        "layer_id": str(hit.get("id") or layer),
        "policy": copy.deepcopy(policy) if policy is not None else None,
        "explanation": sp.bounded_explanation(decision),
        "available_fields": _available_fields(spec, hit),
        # review F5：组件状态在此成为**真实生产消费者**（不是孤立纯函数），
        # 与 UI / Agent 回执同源；legend/colorbar 恒 full_domain。
        "components": sp.component_portrayal_state(
            decision, _components_of_spec(spec)),
    }


def _project_layer_patch(layer: Dict[str, Any], policy: Optional[Dict[str, Any]],
                         *, fields: Sequence[str]) -> Dict[str, Any]:
    """局部深拷贝 + 命名空间子键写入 + 既有 spec 字段投影。

    ``policy=None`` = 恢复自动：移除命名空间与 G27 投影的 zoomBands
    （仅当它与当前 spec 投影逐位相等 —— authored 值 user-wins 不删）。
    """
    new_layer = copy.deepcopy(layer)
    profile = dict(new_layer.get("scale_profile") or {}) \
        if isinstance(new_layer.get("scale_profile"), dict) else {}
    decision = sp.resolve_effective_scale_portrayal(
        zoom=11.0, layer=new_layer, policy=policy, available_fields=fields)
    patch = sp.project_scale_portrayal_spec(decision)
    bands = (patch.get("label") or {}).get("zoomBands")

    if policy is None:
        profile.pop(NAMESPACE, None)
        label = new_layer.get("label") if isinstance(new_layer.get("label"), dict) else {}
        if bands and label.get("zoomBands") == bands:
            label = dict(label)
            label.pop("zoomBands", None)
            new_layer["label"] = label
    else:
        profile[NAMESPACE] = dict(policy)
        if bands:
            label = dict(new_layer.get("label") or {}) \
                if isinstance(new_layer.get("label"), dict) else {}
            if not label.get("zoomBands"):
                label["zoomBands"] = bands
                # field 是 MapSpecLayerLabel 必填：缺省补最小合法值
                label.setdefault("field", "")
                new_layer["label"] = label
    new_layer["scale_profile"] = profile
    return new_layer


async def apply_scale_portrayal(
    session_id: str,
    layer: str,
    *,
    policy: Optional[Dict[str, Any]],
    mode: str = "preview",
    expected_revision: Optional[int] = None,
    operation_id: Optional[str] = None,
    note: str = "",
    origin: str = "agent",
    actor: str = "webgis_apply_scale_portrayal",
) -> Dict[str, Any]:
    """局部受控写：preview 零突变 / commit 走既有 CAS 单事务（A19/A20）。"""
    if mode not in ("preview", "commit"):
        return {"status": "rejected", "diagnostics": [
            {"code": "SCALE.POLICY.UNKNOWN_MODE", "field": "mode",
             "detail": "mode 只能是 preview | commit"}]}
    # review F9：user-origin commit 的 CAS 基线由引擎强制必填。缺基线时
    # 在此 typed 拒绝，绝不静默当成已提交（UI cursor 可能尚未取到 revision）。
    if mode == "commit" and origin == "user" and expected_revision is None:
        return {"status": "rejected", "diagnostics": [{
            "code": "SCALE.POLICY.MISSING_EXPECTED_REVISION",
            "field": "expected_revision",
            "detail": "用户来源的 commit 必须携带 expected_revision（CAS 基线）",
        }], "hint": "刷新 MapSpec revision 后重试；本次未产生任何 mutation"}
    spec = await _load_spec(session_id)
    hit = _match_layer(spec, layer)
    fields = _available_fields(spec, hit)

    if policy is not None:
        diagnostics = sp.validate_scale_policy(policy, available_fields=fields)
        if diagnostics:
            return {
                "status": "rejected",
                "layer_id": str(hit.get("id") or layer),
                "diagnostics": [d.model_dump() for d in diagnostics],
                "hint": "未知/非法策略不会落库，也不会显示已生效",
            }

    new_layer = _project_layer_patch(hit, policy, fields=fields)
    decision = _resolve(spec, new_layer)
    explanation = sp.bounded_explanation(decision)
    compact = {
        "layer_id": str(hit.get("id") or layer),
        "policy": copy.deepcopy(policy),
        "label_zoom_bands_count": len(
            (new_layer.get("label") or {}).get("zoomBands") or []),
        "explanation": explanation,
    }
    if mode == "preview":
        return {"status": "preview", "schema_version": SCHEMA_VERSION, **compact}

    UpsertLayerIntent, apply_gis_mutation = _mutation_dependencies()

    op_id = (operation_id or f"g27:{int(__import__('time').time() * 1000)}")[:64]
    try:
        res = await apply_gis_mutation(
            session_id,
            UpsertLayerIntent(layer=new_layer),
            origin=origin,
            actor=actor,
            expected_revision=expected_revision,
            mutation_id=f"op:{op_id}",
            reason=note or "G27 按尺度表达策略局部调整",
        )
    except Exception as exc:  # noqa: BLE001 - 工具错误契约，不泄漏栈
        logger.warning("[scale_portrayal] mutation failed", exc_info=True)
        return {"status": "error", "error": f"mutation_failed: {exc}", **compact}
    if getattr(res, "superseded", False):
        return {"status": "superseded",
                "error": "MapSpec revision moved (CAS); retry with current revision",
                "operation_id": op_id, **compact}
    if getattr(res, "is_error", False):
        return {"status": "error",
                "error": getattr(res, "error_msg", None) or "mutation rejected",
                "operation_id": op_id, **compact}
    return {
        "status": "committed",
        "schema_version": SCHEMA_VERSION,
        "operation_id": op_id,
        "mapspec_revision": getattr(res, "mutation_revision", None),
        **compact,
    }


def _mutation_dependencies():
    """延迟解析 CAS 链依赖（Web 进程外可注入，不触发 settings/DB 链）。

    这不是第二套 mutation：返回的仍是既有 ``UpsertLayerIntent`` 与
    ``apply_gis_mutation``，仅把 import 时机收敛到一个可替换 seam。
    """
    from app.services.mapspec.intents import UpsertLayerIntent
    from app.services.gis_world_state.mutation import apply_gis_mutation

    return UpsertLayerIntent, apply_gis_mutation


__all__ = ["SCHEMA_VERSION", "NAMESPACE", "ScalePortrayalError",
           "inspect_scale_portrayal", "apply_scale_portrayal",
           "_mutation_dependencies"]
