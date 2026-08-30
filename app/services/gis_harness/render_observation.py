"""Render Observation Runtime — 渲染级完成度证据（P9 render-observed closure）。

解决的问题：``MapSpec says layer exists`` ≠ ``MapLibre actually rendered it``。

边界（刻意收窄，全部为派生观察逻辑）：

- RenderObservation 是**观察**，不是第二地图真相：desired state 仍然只有
  MapSpec（ADR-0076/0081 不变量）；本模块只回答「浏览器此刻实际挂载/渲染
  了什么」；
- observation 由前端经既有 ``POST /sessions/{sid}/cartographic-observation``
  上报（latest-wins 覆盖 map_state 键 ``_cartographic_observation``，有界、
  session 级 ephemeral）——不新建 endpoint、不新建 store、不持久化为业务数据；
- ``mapspec_revision`` 由**服务端**在接受门（content fingerprint 相等）通过后
  盖章：fingerprint 相等 ⇒ observation 描述的 spec 内容就是当前 revision 所
  代表的内容，客户端无从伪造 revision 语义；
- 消费方只有 Map Product Finalizer（``map_completion``）——observation 只产
  出 findings 披露；一切修复仍走既有 desired-state mutation 通道
  （GISMutationBatch / mapspec_store），绝不 RenderObservation → 独立改图；
- revision 防护（P9 §9）：finalizer 只消费 ``observation.mapspec_revision ==
  当前 revision`` 的观察；stale/absent → 如实降级（render_status = stale /
  unknown），绝不 false complete。

性能契约：校验 O(结果层 + required slots + 观察条目)，全部是 ID/布尔/小
元数据比对——不读 GeoJSON、不逐 feature 扫描（observation 载荷本身就被
前端/后端双重有界）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.services.gis_harness.map_completion import (
    RENDER_ISSUES,
    RENDER_NOT_APPLICABLE,
    RENDER_STALE,
    RENDER_UNKNOWN,
    RENDER_VERIFIED,
    MapCompletionFinding,
    RESULT_LAYER_ROLES,
    _layer_declared_visible,
    F_RENDER_COMPONENT_MISSING,
    F_RENDER_ERROR,
    F_RENDER_LAYER_MISSING,
    F_RENDER_REVISION_STALE,
    F_RENDER_SOURCE_MISSING,
    F_RENDER_UNVERIFIED,
)

# map_state 里 observation 的存放键（既有通道，P9 增维不换通道）
OBSERVATION_STATE_KEY = "_cartographic_observation"

MAX_RENDER_FINDINGS = 8
_MAX_ERROR_DETAIL = 160


async def load_render_observation(
    session_id: str,
    map_state: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """读取最新 render observation（latest-wins；无观察 → None）。

    ``map_state`` 已在调用方持有时直接采用（省一次状态读）；缺省时补一次
    异步读 —— 读失败按无观察处理（unknown 降级）。
    """
    if map_state is None:
        try:
            from app.services.session_data import session_data_manager

            map_state = await session_data_manager.get_map_state(session_id)
        except Exception:  # noqa: BLE001 — 读失败按无观察处理（unknown 降级）
            return None
    if not isinstance(map_state, dict):
        return None
    obs = map_state.get(OBSERVATION_STATE_KEY)
    if not isinstance(obs, dict) or obs.get("source") != "frontend_runtime":
        return None
    if obs.get("session_id") and obs.get("session_id") != session_id:
        return None  # 跨会话污染防御（写入侧已有守卫，这里防御性复检）
    return obs


def observation_sequence(observation: Optional[Dict[str, Any]]) -> int:
    """observation 单调序号（latest-wins 覆盖语义的代次门输入）。"""
    if not isinstance(observation, dict):
        return 0
    try:
        return int(observation.get("sequence") or 0)
    except (TypeError, ValueError):
        return 0


def observation_revision(observation: Optional[Dict[str, Any]]) -> Optional[int]:
    """观察绑定的 MapSpec revision；None = pre-revision 观察（旧客户端）。"""
    if not isinstance(observation, dict):
        return None
    raw = observation.get("mapspec_revision")
    if isinstance(raw, bool) or raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _observed_layers_by_id(
    observation: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    """观察条目索引：spec 层 id 与 HUD runtime 行 id 双键（family 别名）。"""
    by_id: Dict[str, Dict[str, Any]] = {}
    for entry in observation.get("layers") or []:
        if not isinstance(entry, dict):
            continue
        for key in ("id", "runtime_store_id"):
            val = str(entry.get(key) or "")
            if val and val not in by_id:
                by_id[val] = entry
    return by_id


def _planned_result_layer_ids(chapter: Dict[str, Any]) -> List[str]:
    """与 validate_layers 同源的结果层断言（单一计算源，不发明第二语义）。"""
    return [
        str(ly.get("layer_id") or "")
        for ly in (chapter.get("map_layers") or [])
        if isinstance(ly, dict)
        and ly.get("layer_id")
        and str(ly.get("role") or "") in RESULT_LAYER_ROLES
        and ly.get("enabled") is not False
    ]


def _component_family_observed(
    family: List[str],
    observed_types_by_presence: Dict[str, bool],
) -> bool:
    """slot 族内是否任一类型被观察到 mounted（族语义与 desired-state 一致）。"""
    return any(observed_types_by_presence.get(t) for t in family)


def validate_render_observation(
    chapter: Dict[str, Any],
    mapspec: Dict[str, Any],
    observation: Optional[Dict[str, Any]],
    current_revision: int,
    required_slots: Optional[List[List[str]]] = None,
) -> tuple[str, List[MapCompletionFinding]]:
    """渲染级校验：observation 是不是当前 revision 的、结果是否真的渲染了。

    返回 ``(render_status, findings)``：

    - 无观察能力（旧客户端 / 前端离线）→ ``unknown`` + ``render_unverified``
      warning —— 向后兼容：旧客户端必须仍能完成，只做如实披露；
    - observation revision ≠ 当前 revision → ``stale`` +
      ``render_revision_stale`` warning —— 瞬态（前端会再观察），不判失败；
    - 匹配 revision → 逐结果层/required slot 对照观察条目：缺席 → error
      finding（产品不得静默宣称 verified）；全部在场 → ``verified``。
    """
    result_ids = _planned_result_layer_ids(chapter)
    spec_layers = {
        str(ly.get("id") or ""): ly
        for ly in (mapspec.get("layers") or [])
        if isinstance(ly, dict)
    }
    has_assertions = bool(result_ids) or bool(required_slots)
    if not has_assertions:
        return RENDER_NOT_APPLICABLE, []

    if observation is None:
        return RENDER_UNKNOWN, [
            MapCompletionFinding(
                code=F_RENDER_UNVERIFIED,
                severity="warning",
                target="render",
                detail="no render observation received — MapSpec validated as desired state only",
            )
        ]

    obs_rev = observation_revision(observation)
    if obs_rev is None:
        return RENDER_UNKNOWN, [
            MapCompletionFinding(
                code=F_RENDER_UNVERIFIED,
                severity="warning",
                target="render",
                detail="render observation predates revision binding — desired state only",
            )
        ]
    if obs_rev != current_revision:
        return RENDER_STALE, [
            MapCompletionFinding(
                code=F_RENDER_REVISION_STALE,
                severity="warning",
                target="render",
                detail=(
                    f"observation revision {obs_rev} != current {current_revision} "
                    "(re-observation expected after mutation)"
                ),
            )
        ]

    # revision 匹配 —— 逐断言对照观察
    findings: List[MapCompletionFinding] = []
    obs_by_id = _observed_layers_by_id(observation)
    for lid in result_ids:
        layer = spec_layers.get(lid)
        if layer is None:
            continue  # desired-state 缺失已由 F_LAYER_MISSING 披露，不重复计
        entry = obs_by_id.get(lid)
        if entry is None:
            findings.append(MapCompletionFinding(
                code=F_RENDER_LAYER_MISSING,
                severity="error",
                target=lid,
                detail="planned result layer not mounted in observed runtime",
            ))
            continue
        try:
            runtime_count = int(entry.get("runtime_layer_count") or 0)
        except (TypeError, ValueError):
            runtime_count = 0
        declared_visible = _layer_declared_visible(layer)
        visible = bool(entry.get("visible"))
        if runtime_count <= 0:
            findings.append(MapCompletionFinding(
                code=F_RENDER_LAYER_MISSING,
                severity="error",
                target=lid,
                detail="result layer family has no live MapLibre layers",
            ))
            continue
        if declared_visible and not visible:
            # spec 期望可见而运行时不可见：真实渲染分歧（挂载失败/被跳过）。
            # 无自动修复 —— 期望态正确，等 re-render/re-observation 收敛。
            findings.append(MapCompletionFinding(
                code=F_RENDER_LAYER_MISSING,
                severity="error",
                target=lid,
                detail="result layer mounted but observed not visible at current revision",
            ))
            continue
        if entry.get("source_converged") is False:
            # 层挂载而源未收敛（ref 解析中/类型回退）—— 诊断性 warning。
            findings.append(MapCompletionFinding(
                code=F_RENDER_SOURCE_MISSING,
                severity="warning",
                target=lid,
                detail="observed layer source not converged (ref resolution pending?)",
            ))

    # required 组件槽族：观察到的组件必须覆盖（fallback 注入与 chrome 同规则）
    if required_slots:
        components = observation.get("components") or []
        observed_types: Dict[str, bool] = {}
        for comp in components:
            if not isinstance(comp, dict):
                continue
            ctype = str(comp.get("type") or "")
            if not ctype:
                continue
            mounted = bool(comp.get("mounted"))
            observed_types[ctype] = bool(observed_types.get(ctype)) or mounted
        for family in required_slots:
            family = [t for t in family if t] or ["title"]
            if not _component_family_observed(family, observed_types):
                findings.append(MapCompletionFinding(
                    code=F_RENDER_COMPONENT_MISSING,
                    severity="warning",
                    target=family[0],
                    detail=(
                        f"required component slot '{family[0]}' not observed in live chrome"
                    ),
                ))

    # 有界 runtime error 披露（瞬态瓦片错误不判失败 —— 层/源在场性才是判据）
    errors = observation.get("runtime_errors")
    if isinstance(errors, list) and errors:
        first = errors[0] if isinstance(errors[0], dict) else {}
        detail = str(first.get("message") or "")[:_MAX_ERROR_DETAIL]
        findings.append(MapCompletionFinding(
            code=F_RENDER_ERROR,
            severity="warning",
            target=str(first.get("target") or "runtime")[:64],
            detail=f"{len(errors)} runtime error(s) observed; latest: {detail}",
        ))

    # 层断言在场的会话：任一 render error（层缺席）→ issues；否则 verified。
    render_errors = [f for f in findings if f.severity == "error"]
    status = RENDER_ISSUES if render_errors else RENDER_VERIFIED
    return status, findings[:MAX_RENDER_FINDINGS]
