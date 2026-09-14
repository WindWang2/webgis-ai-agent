"""Situation 查询 API（方向 2 S6，ADR-0180）。

业务代码不再散落地读 ``map_state`` 字典键 —— 从 GISSituation 的命名事实
做纯函数查询。全部只读、确定性、partial（unknown → None/空，绝不猜默认）。

``is_usable`` 纪律：只有 ``status=known`` 的事实参与决策；stale/unavailable
需要调用方显式豁免（拿到 SitFact 自查 status）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.services.gis_situation.contract import GISSituation
from app.services.gis_situation.facts import SitFact, is_usable


def get_fact(situation: GISSituation, context: str, name: str) -> Optional[SitFact]:
    """按坐标取原始 SitFact（调用方自行裁决 stale 豁免）。"""
    ctx = getattr(situation, context, None)
    if ctx is None:
        return None
    return getattr(ctx, name, None)


def get_active_dataset_by_role(
    situation: GISSituation, role: str
) -> Optional[str]:
    """role → 挂载该角色的数据 ref_id（mapspec.layers.context_role 派生）。"""
    fact = situation.data.active_roles
    if not is_usable(fact) or not isinstance(fact.value, dict):
        return None
    ref = fact.value.get(role)
    return str(ref) if ref else None


def get_active_datasets(situation: GISSituation) -> List[Dict[str, Any]]:
    """活跃数据集描述符行（有界；绝无 payload）。"""
    fact = situation.data.datasets
    if not is_usable(fact) or not isinstance(fact.value, list):
        return []
    return [row for row in fact.value if isinstance(row, dict)]


def get_visible_layers(situation: GISSituation) -> List[Dict[str, Any]]:
    """当前 desired spec 中可见的图层摘要（id/type/role）。"""
    fact = situation.map.layers
    if not is_usable(fact) or not isinstance(fact.value, list):
        return []
    return [
        row for row in fact.value
        if isinstance(row, dict) and row.get("visible") is True
    ]


def resolve_geographic_scope(situation: GISSituation) -> Optional[Dict[str, Any]]:
    """当前地理范围（viewport 优先，agent framed view 兜底；区域名/CRS 附带）。"""
    scope: Dict[str, Any] = {}
    if is_usable(situation.geographic.viewport):
        scope["viewport"] = situation.geographic.viewport.value
    elif is_usable(situation.geographic.framed_view):
        view = situation.geographic.framed_view.value
        scope["viewport"] = view if isinstance(view, dict) else None
    if is_usable(situation.geographic.scope_name):
        scope["region_name"] = situation.geographic.scope_name.value
    if is_usable(situation.geographic.crs):
        scope["crs"] = situation.geographic.crs.value
    if is_usable(situation.geographic.scale):
        scope["scale"] = situation.geographic.scale.value
    return scope or None


def get_selected_feature(situation: GISSituation) -> Optional[Dict[str, Any]]:
    """用户当前选中要素快照（有界；前端观察通道）。"""
    fact = situation.interaction.selected_feature
    if is_usable(fact) and isinstance(fact.value, dict):
        return fact.value
    return None


def query_user_locks(situation: GISSituation) -> Dict[str, Any]:
    """用户 durable 决策（agent 不得反转的面）+ 当前聚焦。

    - ``hidden_layers``：provenance 裁决的用户隐藏层（与
      UserPresentationGuard 同一裁决源）；
    - ``focus_layer_id``：用户聚焦层。
    """
    locks: Dict[str, Any] = {"hidden_layers": [], "focus_layer_id": None}
    hidden = situation.interaction.user_hidden_layers
    if is_usable(hidden) and isinstance(hidden.value, list):
        locks["hidden_layers"] = [str(x) for x in hidden.value]
    focus = situation.interaction.focus_layer_id
    if is_usable(focus) and focus.value:
        locks["focus_layer_id"] = str(focus.value)
    return locks


def get_delivery_target(situation: GISSituation) -> Optional[str]:
    fact = situation.delivery.target
    if is_usable(fact) and fact.value:
        return str(fact.value)
    return None


def get_cartographic_constraints(situation: GISSituation) -> Dict[str, Any]:
    """制图约束面（verdict 门 + 显式约束；v1 budget/security 显式缺席）。"""
    out: Dict[str, Any] = {}
    if is_usable(situation.cartographic.verdict):
        out["verdict"] = situation.cartographic.verdict.value
    elif situation.cartographic.verdict.status == "stale":
        out["verdict"] = {"status": "stale_fingerprint"}
    if is_usable(situation.constraints.explicit):
        out["explicit"] = situation.constraints.explicit.value
    return out


def get_pending_mutations(situation: GISSituation) -> List[Dict[str, Any]]:
    fact = situation.interaction.pending_mutations
    if is_usable(fact) and isinstance(fact.value, list):
        return [row for row in fact.value if isinstance(row, dict)]
    return []


def get_active_analysis(situation: GISSituation) -> Optional[Dict[str, Any]]:
    fact = situation.analysis.plan_progress
    if is_usable(fact) and isinstance(fact.value, dict):
        return fact.value
    return None
