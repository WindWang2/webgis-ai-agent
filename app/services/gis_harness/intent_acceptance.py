"""IntentAcceptance —— 用户意图满足的独立验收判定（V7 ADR-0134 D6）。

V6 基线缺口：finalizer 持久化时 ``intent_verified=(result.status ==
"complete")`` —— **循环论证**（完成 → 意图满足），observation ladder 的
``semantically_correct`` 晋级建立在这个自证之上；「检查原始用户意图是否
满足 / 必要图层是否显示 / 组件是否在场 / 结论与结果是否一致」没有独立
判定面。

V7 契约（确定性；只消费既有事实源）：

- **需求面**：``derive_intent_requirements`` —— planned layers（角色 +
  layer_id）与 required component slots（facet 槽位既有真相）→ 有界
  需求清单。
- **验收判定**：``assess_intent_acceptance`` ——
  - *desired*：每个需求层在 MapSpec 中在场且期望可见（spec 级意图）；
  - *observed*：render observation 在场时，逐层核对 mounted+visible
    （渲染面证据）；观察缺席 → ``observed_confirmed=False``（诚实未知，
    不假通过 —— semantically_correct 晋级随之收紧）；
  - *verdict*：product_verdict ∈ {READY, READY_WITH_WARNINGS}；
  - accepted = verdict ∧ desired 全满足；
  - intent_verified = accepted ∧ observed 全证实（渲染证据级）。
- **与 V6 的行为差**（诚实收紧）：observation 缺席时 intent_verified
  从「status==complete → True」降为 False —— semantically_correct 不再
  在无渲染证据时被自证晋级（测试钉死）。

输出进 ``map_product["intent_acceptance"]``（additive 有界块）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

READY_VERDICTS = ("READY", "READY_WITH_WARNINGS")

#: 需求/不满足清单上界（bounded everything）。
MAX_REQUIREMENTS = 16
MAX_UNMET = 8


def derive_intent_requirements(chapter: Dict[str, Any]) -> Dict[str, List[str]]:
    """章节意图事实 → 需求清单（**计划结果层**单一来源 + 角色/组件披露）。

    评审 F4：需求面 = ``_planned_result_layer_ids``（render_observation
    既有判定 —— 与 finalizer 观察核对同一词表），非全部 map_layers；
    辅助角色（basemap 等非结果层）不参与意图核对。"""
    layers: List[str] = []
    roles: List[str] = []
    try:
        from app.services.gis_harness.render_observation import (
            _planned_result_layer_ids,
        )

        layers = [str(lid)[:64]
                  for lid in (_planned_result_layer_ids(chapter) or [])]
    except Exception:  # noqa: BLE001 — 助手缺席退化为 role 过滤
        layers = []
    if not layers:
        for ly in (chapter.get("map_layers") or [])[:MAX_REQUIREMENTS]:
            if isinstance(ly, dict) and ly.get("layer_id"):
                layers.append(str(ly["layer_id"])[:64])
    layer_roles = {
        str(ly.get("layer_id")): str(ly.get("role") or "secondary")[:24]
        for ly in (chapter.get("map_layers") or [])
        if isinstance(ly, dict) and ly.get("layer_id")
    }
    roles = [layer_roles.get(lid, "secondary") for lid in layers]
    components = [
        str(slot)[:48] for slot in (chapter.get("required_components") or [])
    ][:MAX_REQUIREMENTS]
    return {"layers": layers[:MAX_REQUIREMENTS], "roles": roles,
            "components": components}


def _spec_layers_by_id(mapspec: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    if not isinstance(mapspec, dict):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for ly in (mapspec.get("layers") or []):
        if isinstance(ly, dict) and ly.get("id"):
            out[str(ly["id"])[:64]] = ly
    return out


def _observed_by_id(observation: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    if not isinstance(observation, dict):
        return {}
    return {
        str(k)[:64]: v for k, v in (observation.get("layers") or {}).items()
        if isinstance(v, dict)
    }


def assess_intent_acceptance(
    chapter: Optional[Dict[str, Any]],
    mapspec: Optional[Dict[str, Any]] = None,
    observation: Optional[Dict[str, Any]] = None,
    *,
    product_verdict: str = "",
) -> Dict[str, Any]:
    """意图验收（纯函数）：verdict + desired + observed 三面独立核对。

    user-wins（评审 F4）：spec 中用户主动隐藏的层**不阻断**验收 —— 与
    V6 ``F_LAYER_HIDDEN``（warning 级、只披露）同语义；只记入
    ``disclosures`` 并跳过其 observed 核对（用户选择即最终语义）。"""
    if not isinstance(chapter, dict) or not chapter:
        return {"accepted": False, "intent_verified": False, "unmet": ["no_chapter"]}
    requirements = derive_intent_requirements(chapter)
    spec_layers = _spec_layers_by_id(mapspec)
    observed = _observed_by_id(observation)
    unmet: List[str] = []
    disclosures: List[str] = []

    verdict_ok = str(product_verdict or "").startswith("READY")
    if not verdict_ok:
        unmet.append(f"verdict:{product_verdict or 'none'}")

    desired_ok = True
    user_hidden: List[str] = []
    for layer_id in requirements["layers"]:
        spec_layer = spec_layers.get(layer_id)
        if spec_layer is None:
            desired_ok = False
            unmet.append(f"layer_not_in_spec:{layer_id}")
            continue
        if spec_layer.get("visible") is False:
            # user-wins：不阻断、不核 observed（用户选择即最终语义）
            user_hidden.append(layer_id)
            disclosures.append(f"layer_hidden_by_user:{layer_id}")

    observed_confirmed = bool(observed) and desired_ok
    if bool(observed):
        for layer_id in requirements["layers"]:
            if layer_id in user_hidden:
                continue
            entry = observed.get(layer_id)
            if entry is None:
                observed_confirmed = False
                unmet.append(f"layer_not_observed:{layer_id}")
                continue
            if entry.get("mounted") is not True or (
                    entry.get("visible") is False):
                observed_confirmed = False
                unmet.append(f"layer_not_visible_observed:{layer_id}")

    accepted = verdict_ok and desired_ok
    intent_verified = accepted and observed_confirmed
    return {
        "accepted": accepted,
        "intent_verified": intent_verified,
        "observed_confirmed": observed_confirmed,
        "verdict_ok": verdict_ok,
        "requirements": requirements,
        "unmet": [u[:96] for u in unmet[:MAX_UNMET]],
        "disclosures": [d[:96] for d in disclosures[:MAX_UNMET]],
    }


__all__ = [
    "READY_VERDICTS",
    "derive_intent_requirements",
    "assess_intent_acceptance",
]
