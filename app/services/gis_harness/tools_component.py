"""GIS Harness 组件生命周期面（自 tools.py 拆出，god-module 棘轮只减不增）。

组件 mutation 的「生命周期动作」半边（Runtime V4 §17-19）：``_component_lifecycle_action``
（remove/duplicate/rebind）及其依赖 —— ref 活性探测 ``_ref_probe``、evidence
转发 ``_forward_evidence``。tools.py 经 re-export 保持既有导入兼容
（``from app.services.gis_harness.tools import _component_lifecycle_action`` 等）。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

_EVIDENCE_KEYS = (
    "is_compiled", "cartography_findings", "cartographic_review",
    "mapspec_fingerprint", "runtime_observation_seq", "mutation_revision",
    "checkpoint_id", "warnings",
)


def _forward_evidence(res: Dict[str, Any], out: Dict[str, Any]) -> Dict[str, Any]:
    for key in _EVIDENCE_KEYS:
        if res.get(key) is not None:
            out[key] = res[key]
    return out


async def _component_lifecycle_action(
    *,
    session_id: str,
    component_id: Optional[str],
    component_type: Optional[str],
    action: str,
    new_component_id: Optional[str],
    rebind_chart_ref: Optional[str],
    rebind_table_ref: Optional[str],
    rebind_layer_id: Optional[str],
    expected_revision: Optional[int],
) -> dict:
    """Component Lifecycle V3（Runtime V4 §17-19）：remove/duplicate/rebind。

    与 patch 共用 webgis_component_update 入口（不开 per-action 工具）；语义
    验证在提交前完成 —— remove/duplicate 的目标解析（id 缺省按类型）、
    rebind 的绑定目标存在性（图层在场 / artifact ref 活性）。验证读的是
    提交前 spec，竞态窗口由引擎事务 + CAS 兜底（expected_revision）。
    """
    from app.services.mapspec_store import mapspec_store

    if action not in ("remove", "duplicate", "rebind"):
        return {
            "success": False,
            "message": f"未知 action: {action}",
            "correction_hint": "action 可选 'remove' | 'duplicate' | 'rebind'（缺省 patch）。",
        }
    spec = await mapspec_store.get_mapspec(session_id) or {}
    raw_components = ((spec.get("layout") or {}).get("components")) or []
    target_id = component_id or ""
    if not target_id and component_type:
        for c in raw_components:
            if isinstance(c, dict) and c.get("type") == component_type:
                target_id = str(c.get("id") or "")
                break
    if not target_id:
        current = ", ".join(
            f"{c.get('id')}({c.get('type')})" for c in raw_components if isinstance(c, dict)
        ) or "（无组件）"
        return {
            "success": False,
            "message": f"action={action} 需要 component_id（或按类型命中的 component_type）",
            "correction_hint": f"当前组件: {current}",
        }
    # 目标存在性（id 直查；类型解析已保证命中）
    target = next(
        (c for c in raw_components if isinstance(c, dict) and c.get("id") == target_id),
        None,
    )
    if target is None:
        return {
            "success": False,
            "message": f"组件 {target_id} 不存在",
            "correction_hint": "先 webgis_component_catalog 确认 id。",
        }

    summary = ""
    if action == "remove":
        res = await mapspec_store.remove_component(
            session_id, component_id=target_id, expected_revision=expected_revision,
        )
        summary = f"组件 {target_id}({target.get('type')}) 已删除"
    elif action == "duplicate":
        res = await mapspec_store.duplicate_component(
            session_id, component_id=target_id, new_id=new_component_id,
            expected_revision=expected_revision,
        )
        if res.get("success"):
            dup = next(
                (c for c in ((res.get("mapspec") or {}).get("layout") or {}).get("components") or []
                 if isinstance(c, dict) and c.get("id") in (
                     {new_component_id} if new_component_id else set())),
                None,
            )
            dup_id = (dup or {}).get("id") or f"{target_id}-copy*"
            summary = f"已复制 {target_id} → {dup_id}（floating 偏移放置）"
    else:  # rebind
        bindings: Dict[str, str] = {}
        if rebind_chart_ref:
            bindings["chartRef"] = rebind_chart_ref
        if rebind_table_ref:
            bindings["tableRef"] = rebind_table_ref
        if rebind_layer_id:
            bindings["layerId"] = rebind_layer_id
        if not bindings:
            return {
                "success": False,
                "message": "action=rebind 需要 rebind_chart_ref / rebind_table_ref / rebind_layer_id 其一",
                "correction_hint": "如 rebind_chart_ref='ref:chart-xxx' 或 rebind_layer_id='district-choropleth'。",
            }
        # 绑定目标存在性（§19 验证纪律：类型/基数/存在性一次过）
        layers = spec.get("layers") or []
        layer_ids = {
            str(layer.get("id") or "") for layer in layers if isinstance(layer, dict)
        }
        # 图层族前缀（HUD 行 id ↔ spec 层 id 两种寻址都接受）
        layer_families = set(layer_ids)
        for lid in layer_ids:
            fam = lid.split("__")[0]
            layer_families.add(fam)
        if "layerId" in bindings and bindings["layerId"] not in layer_families:
            return {
                "success": False,
                "message": f"重绑定图层 {bindings['layerId']} 不在当前 MapSpec",
                "correction_hint": f"当前图层: {', '.join(sorted(layer_families)[:8]) or '（无）'}",
            }
        for ref_key in ("chartRef", "tableRef"):
            if ref_key not in bindings:
                continue
            ref = bindings[ref_key]
            alive = await _ref_probe(session_id, ref)
            if alive is False:
                return {
                    "success": False,
                    "message": f"重绑定引用 {ref} 不存在或已过期",
                    "correction_hint": "引用必须是本会话产出的活性 artifact ref（如 ref:chart-*）。",
                }
        res = await mapspec_store.rebind_component(
            session_id, component_id=target_id, bindings=bindings,
            expected_revision=expected_revision,
        )
        summary = f"已重绑定 {target_id}: {bindings}"

    if res.get("status") == "superseded":
        return {
            "success": False,
            "status": "superseded",
            "superseded": True,
            "mutation_revision": res.get("mutation_revision"),
            "message": "组件状态已被更新的交互修改，请重新读取 catalog 后再试",
            "correction_hint": "调用 webgis_component_catalog 获取最新 mutation_revision。",
        }
    if not res.get("success"):
        return {
            "success": False,
            "message": res.get("message") or f"action={action} 失败",
            "correction_hint": res.get("correction_hint"),
        }
    final_components = (((res.get("mapspec") or {}).get("layout") or {}).get("components")) or []
    out: Dict[str, Any] = {
        "success": True,
        "components": final_components,
        "mapspec": res.get("mapspec"),
        "component_mutation_evidence": {
            "action": action,
            "layer_count_unchanged": len(spec.get("layers", []))
            == len((res.get("mapspec") or {}).get("layers", [])),
        },
        "summary": summary,
    }
    return _forward_evidence(res, out)


async def _ref_probe(session_id: str, ref: str) -> "bool | None":
    """探测 session artifact ref 活性（None=无法判定 —— 诚实降级不阻断）。"""
    try:
        from app.services.session_data import session_data_manager

        descriptor = await session_data_manager.get_ref_descriptor(session_id, ref)
        return descriptor is not None
    except Exception:  # noqa: BLE001 — 探测面故障 → 无法判定（不虚构拒绝）
        return None
