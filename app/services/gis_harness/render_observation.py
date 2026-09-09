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
    F_CHART_DATA_MISSING,
    F_RENDER_COMPONENT_MISSING,
    F_RENDER_ERROR,
    F_RENDER_INCOMPLETE,
    F_RENDER_LAYER_MISSING,
    F_RENDER_REVISION_STALE,
    F_RENDER_SOURCE_MISSING,
    F_RENDER_STYLE_NOT_APPLIED,
    F_RENDER_UNVERIFIED,
)

# map_state 里 observation 的存放键（既有通道，P9 增维不换通道）
OBSERVATION_STATE_KEY = "_cartographic_observation"

MAX_RENDER_FINDINGS = 8
_MAX_ERROR_DETAIL = 160

# V6 W8 确定性布局检查预算：floating 组件 ≤32（DTO 上限），pairwise O(n²)
# 有界；overlap/offscreen finding ≤4 条（总 finding 仍受 MAX_RENDER_FINDINGS）。
_MAX_LAYOUT_FINDINGS = 4
_OVERLAP_MIN_AREA_PX = 1.0


def _component_rect(comp: Dict[str, Any]) -> Optional[tuple]:
    """ObservedComponent.rect → (x, y, w, h)；缺尺寸分量 → None（不判定）。"""
    rect = comp.get("rect")
    if not isinstance(rect, dict):
        return None
    try:
        x = float(rect.get("x"))
        y = float(rect.get("y"))
        w = rect.get("width")
        h = rect.get("height")
        if w is None or h is None:
            return None
        return (x, y, float(w), float(h))
    except (TypeError, ValueError):
        return None


def _intersection_area(a: tuple, b: tuple) -> float:
    dx = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    dy = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    if dx <= 0 or dy <= 0:
        return 0.0
    return dx * dy


def derive_component_layout_findings(
    observation: Dict[str, Any],
) -> List[MapCompletionFinding]:
    """floating 组件的确定性布局检查（实测像素 rect，非 spec placement）。

    - 两两重叠（交集 > 1px²）→ layout_conflict warning（disclosure：组件
      位置可被用户拖动 —— transient interaction 不判 error，user-wins）；
    - 容器像素尺寸在场时：完全越出画布 → layout_conflict warning
      （mounted 但不可见的事实披露；仍不判 error —— offscreen 可能是
      用户拖离的瞬态）；部分越界不finding（边缘停靠是合法布局）。
    全部 optional-telemetry 门控：无 rect / 无 canvas → 相应检查缺席
    （旧客户端零新 finding，诚实降级）。
    """
    from app.services.gis_harness.map_completion import F_LAYOUT_CONFLICT

    findings: List[MapCompletionFinding] = []
    floats: List[tuple] = []
    for comp in observation.get("components") or []:
        if not isinstance(comp, dict):
            continue
        rect = _component_rect(comp)
        if rect is None or comp.get("mounted") is False:
            continue
        floats.append((str(comp.get("id") or comp.get("type") or ""), rect))
    for i in range(len(floats)):
        for j in range(i + 1, len(floats)):
            if len(findings) >= _MAX_LAYOUT_FINDINGS:
                return findings
            id_a, ra = floats[i]
            id_b, rb = floats[j]
            area = _intersection_area(ra, rb)
            if area > _OVERLAP_MIN_AREA_PX:
                findings.append(MapCompletionFinding(
                    code=F_LAYOUT_CONFLICT,
                    severity="warning",
                    target=id_a[:64],
                    detail=(
                        f"floating components overlap: {id_a[:32]} ∩ {id_b[:32]} "
                        f"({area:.0f}px² measured)"
                    ),
                ))
    canvas = observation.get("canvas")
    if isinstance(canvas, dict):
        try:
            cw = float(canvas.get("width"))
            ch = float(canvas.get("height"))
        except (TypeError, ValueError):
            cw = ch = 0.0
        if cw > 0 and ch > 0:
            for cid, rect in floats:
                if len(findings) >= _MAX_LAYOUT_FINDINGS:
                    break
                x, y, w, h = rect
                fully_outside = (
                    x + w <= 0 or y + h <= 0 or x >= cw or y >= ch
                )
                if fully_outside:
                    findings.append(MapCompletionFinding(
                        code=F_LAYOUT_CONFLICT,
                        severity="warning",
                        target=cid[:64],
                        detail=(
                            f"floating component fully offscreen at "
                            f"({x:.0f},{y:.0f}) in {cw:.0f}×{ch:.0f} canvas"
                        ),
                    ))
    return findings


def derive_component_lifecycle(
    observation: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """组件生命周期统一投影（V6 W8；纯派生，零状态）。

    requested → mounted(materialized) → rendered → visible → layout_valid →
    data_bound → diagnostics：

    - requested = spec enabled（ObservedComponent.enabled）；
    - mounted = chrome 挂载（含 fallback 注入镜像）；
    - rendered = chart telemetry rendered（非 chart 族 None —— 无证据不虚构）；
    - visible = mounted 且未 collapsed（floating 需有实测 rect）；
    - layout_valid = 该组件不参与 overlap/offscreen finding；
    - data_bound = chart data_points > 0（非 chart 族 None）；
    - diagnostics = 该组件关联的 finding code 列表（有界）。
    """
    layout_findings = derive_component_layout_findings(observation)
    by_target: Dict[str, List[str]] = {}
    for f in layout_findings:
        by_target.setdefault(str(f.target), []).append(f.code)
    # layout_invalid 集合：与 derive_component_layout_findings 同一几何
    # 判据就地重算（有界；避免从 finding 文本回解析 id）。
    floats: List[tuple] = []
    for comp in observation.get("components") or []:
        if not isinstance(comp, dict):
            continue
        rect = _component_rect(comp)
        if rect is None or comp.get("mounted") is False:
            continue
        floats.append((str(comp.get("id") or comp.get("type") or ""), rect))
    invalid_ids = set(by_target.keys())
    for i in range(len(floats)):
        for j in range(i + 1, len(floats)):
            if _intersection_area(floats[i][1], floats[j][1]) > _OVERLAP_MIN_AREA_PX:
                invalid_ids.add(floats[i][0])
                invalid_ids.add(floats[j][0])
    charts_by_id: Dict[str, Dict[str, Any]] = {}
    for ch in observation.get("charts") or []:
        if isinstance(ch, dict) and ch.get("id"):
            charts_by_id[str(ch["id"])] = ch
    out: List[Dict[str, Any]] = []
    for comp in (observation.get("components") or [])[:32]:
        if not isinstance(comp, dict):
            continue
        cid = str(comp.get("id") or comp.get("type") or "")
        ctype = str(comp.get("type") or "")
        chart = charts_by_id.get(cid)
        is_chart = "chart" in ctype
        mounted = bool(comp.get("mounted"))
        rect = _component_rect(comp)
        floating = bool(comp.get("floating"))
        collapsed = bool(comp.get("collapsed"))
        visible = mounted and not collapsed and (not floating or rect is not None)
        layout_valid = cid not in invalid_ids
        diags = list(by_target.get(cid, []))[:4]
        points = 0
        rendered: Optional[bool] = None
        data_bound: Optional[bool] = None
        if is_chart:
            if chart is not None:
                rendered = bool(chart.get("rendered"))
                raw = chart.get("data_points")
                points = int(raw) if isinstance(raw, (int, float)) \
                    and not isinstance(raw, bool) else 0
                data_bound = points > 0
            else:
                rendered = None
                data_bound = None
        out.append({
            "id": cid[:64],
            "type": ctype[:48],
            "requested": bool(comp.get("enabled")),
            "mounted": mounted,
            "rendered": rendered,
            "visible": visible,
            "layout_valid": layout_valid,
            "data_bound": data_bound,
            "diagnostics": diags,
        })
    return out


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
        # V5 W5 rendered-state telemetry（全部 optional 门控 —— 旧客户端
        # 条目不带新字段时不产生新 finding）：
        if entry.get("source_status") == "error":
            # 源加载失败是 requested(挂载) ↔ actual(加载错误) 的硬分歧，
            # 比"未收敛"强 —— error 级。
            findings.append(MapCompletionFinding(
                code=F_RENDER_SOURCE_MISSING,
                severity="error",
                target=lid,
                detail="observed layer source in error state at current revision",
            ))
        if entry.get("render_complete") is False:
            findings.append(MapCompletionFinding(
                code=F_RENDER_INCOMPLETE,
                severity="error",
                target=lid,
                detail="layer family mounted but render not complete (tiles/source pending)",
            ))
        if entry.get("style_converged") is False:
            findings.append(MapCompletionFinding(
                code=F_RENDER_STYLE_NOT_APPLIED,
                severity="warning",
                target=lid,
                detail="requested style not converged on live layer (presentation pending)",
            ))
        try:
            feature_count = entry.get("feature_count")
            if isinstance(feature_count, (int, float)) and \
                    not isinstance(feature_count, bool) and int(feature_count) == 0:
                findings.append(MapCompletionFinding(
                    code=F_RENDER_INCOMPLETE,
                    severity="warning",
                    target=lid,
                    detail="layer rendered with zero observed features (viewport-scoped count)",
                ))
        except (TypeError, ValueError):
            pass

    # required 组件槽族：观察到的组件必须覆盖（fallback 注入与 chrome 同规则）
    chart_required = False
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
            chart_required = chart_required or any(
                "chart" in t for t in family)
            if not _component_family_observed(family, observed_types):
                findings.append(MapCompletionFinding(
                    code=F_RENDER_COMPONENT_MISSING,
                    severity="warning",
                    target=family[0],
                    detail=(
                        f"required component slot '{family[0]}' not observed in live chrome"
                    ),
                ))

    # V5 W5：chart_required 的**数据级**核验（V4 只断言组件槽在场）。
    # requested(chart with data) ↔ actual(rendered series) —— telemetry
    # 缺席（旧客户端）→ 诚实 warning 披露，不假通过也不误伤兼容性。
    if chart_required:
        charts = observation.get("charts")
        if isinstance(charts, list) and charts:
            rendered_ok = False
            any_settled = False
            for ch in charts:
                if not isinstance(ch, dict):
                    continue
                # review R2 #3：pending（取数中）非终态 —— 全 pending 的
                # 观察按 warning 披露，不判 error（避免 400ms settle 窗口
                # 与 chart 数据 fetch 竞速产生假 NEEDS_REPAIR）。
                if ch.get("pending") is True:
                    continue
                any_settled = True
                points = ch.get("data_points")
                pts = points if isinstance(points, (int, float)) and \
                    not isinstance(points, bool) else 0
                if bool(ch.get("rendered")) and int(pts) > 0:
                    rendered_ok = True
                    break
            if not any_settled:
                findings.append(MapCompletionFinding(
                    code=F_CHART_DATA_MISSING,
                    severity="warning",
                    target="chart_panel",
                    detail="chart render still pending (data fetch in flight)",
                ))
            elif not rendered_ok:
                findings.append(MapCompletionFinding(
                    code=F_CHART_DATA_MISSING,
                    severity="error",
                    target="chart_panel",
                    detail=(
                        f"{len(charts)} chart panel(s) observed but none "
                        "rendered with data"
                    ),
                ))
        elif "charts" not in observation:
            findings.append(MapCompletionFinding(
                code=F_CHART_DATA_MISSING,
                severity="warning",
                target="chart_panel",
                detail=(
                    "chart required but no chart render telemetry received — "
                    "validated at component-slot level only"
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

    # V6 W8：floating 组件确定性布局检查（实测像素 rect；overlap/offscreen
    # 仅 warning 披露 —— 组件位置可被用户拖动，transient 不判 error）。
    for f in derive_component_layout_findings(observation):
        if len(findings) >= MAX_RENDER_FINDINGS:
            break
        findings.append(f)

    # 层断言在场的会话：任一 render error（层缺席）→ issues；否则 verified。
    render_errors = [f for f in findings if f.severity == "error"]
    status = RENDER_ISSUES if render_errors else RENDER_VERIFIED
    return status, findings[:MAX_RENDER_FINDINGS]
