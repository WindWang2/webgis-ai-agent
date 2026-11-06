"""Independently testable webgis_map_product handler and explicit registration."""
from __future__ import annotations
import asyncio
import copy
from app.services.session_data import session_data_manager
from app.tools.registry import tool
from functools import partial
from app.services.gis_harness.failure_observation import observe_optional_failure

from app.services.gis_harness.tools import (
    ToolRegistry,
    Any,
    Dict,
    List,
    MapProductArgs,
    Optional,
    _forward_evidence,
    _manifest_stale,
    _product_composition_identity,
    _project_verified_recipes,
    bounded_selection_evidence,
    logger,
)


async def webgis_map_product(
    registry: ToolRegistry,
    query: str,
    session_id: Optional[str] = None,
    layer_ids: Optional[List[str]] = None,
    primary_ref: Optional[str] = None,
    overlay_refs: Optional[List[str]] = None,
    title: Optional[str] = None,
    template_id: Optional[str] = None,
    palette: str = "classic",
    radius_px: Optional[int] = None,
    recipe_id: Optional[str] = None,
    task_hint: Optional[str] = None,
) -> dict:
    from app.services.gis_harness.intent import (
        merge_intent_hints,
        resolve_map_request_intent,
    )
    from app.services.gis_harness.planner_runtime import get_planner_runtime
    from app.services.gis_harness.recipes import FallbackDecision
    from app.services.mapspec_store import mapspec_store
    from app.services.spatial_meta_profiler import profile_from_descriptor

    if not session_id:
        return {"success": False, "message": "Missing session_id"}

    layer_ids = list(layer_ids or [])
    overlay_refs = list(overlay_refs or [])

    # 计划连续性：意图阶段（webgis_map_intent）接受的 hint 与推荐 recipe
    # 在此重放——两个阶段绝不产出两份互相矛盾的计划。
    intent = resolve_map_request_intent(query)
    if task_hint:
        intent = merge_intent_hints(intent, {"task": task_hint})
    # v3(Phase C)：共享 PlannerRuntime —— intent→product 链跨工具调用
    # 复用同一 planner 与 memo（此前每调用新建，memo 从不跨调用命中）。
    planner = get_planner_runtime()
    # H-9（#864）：与意图阶段同源的真实选择参数——
    # ① 注册表可见工具传给 planner，unavailable 能力不退回 pending
    #   （两阶段 evidence 的能力状态一致，audit #825 承诺）；
    # ② 候选选择带 project_verified（ADR-0069 项目记忆），一次取数
    #   同时驱动 recipe 解析与 evidence 记录（此前 evidence 二次选择
    #   不带记忆，记录的候选序与真实决策不一致）。
    try:
        available = set(registry.list_tools())
    except Exception:  # noqa: BLE001 - 能力解析是建议性信息
        available = set()
    # V1（ADR-0181）：与意图阶段同源的 situation —— intent 阶段已知
    # 的推荐 recipe 在此重放时资格层语义一致；finalize 再以 profile
    # 数据事实刷新（同一 QualificationContext 载体）。
    try:
        from app.services.gis_harness.capability_resolution import (
            build_situation,
            capability_planning_v1_enabled,
        )

        _situation = (
            build_situation(task_hint=str(intent.task or ""))
            if capability_planning_v1_enabled() else None
        )
    except Exception:  # noqa: BLE001 - 能力解析是建议性信息
        _situation = None
    _verified = await _project_verified_recipes()
    _candidates = planner.recipes.select_candidates(
        intent, project_verified=_verified, situation=_situation
    )
    _selected_recipe = recipe_id or (_candidates[0].id if _candidates else "")
    plan = await asyncio.to_thread(
        planner.plan_from_intent, intent,
        template_id=template_id or "",
        recipe_id=_selected_recipe,
        available_tools=available or None,
        project_verified=_verified,
        situation=_situation,
    )

    # 主数据 profile（eligibility 复检输入）：优先 primary_ref descriptor，
    # 其次第一个绑定图层的 source profile，缺省空 profile（诚实降级）。
    # descriptor 读取廉价；完整 FC 数据推迟到确需补层时再取（避免无谓的
    # 全量 deepcopy）。
    profile: Optional[Dict[str, Any]] = None
    primary_descriptor: Optional[Dict[str, Any]] = None
    if primary_ref:
        descriptor = await session_data_manager.get_ref_descriptor(session_id, primary_ref)
        if descriptor:
            primary_descriptor = descriptor
            profile = profile_from_descriptor(descriptor)

    spec = await mapspec_store.get_mapspec(session_id) or {}
    if profile is None:
        for layer_id in layer_ids:
            layer = next(
                (ly for ly in spec.get("layers", [])
                 if isinstance(ly, dict) and ly.get("id") == layer_id),
                None,
            )
            if not layer:
                continue
            source = spec.get("sources", {}).get(layer.get("source", ""), {})
            src_profile = source.get("profile")
            if isinstance(src_profile, dict):
                profile = src_profile
                break

    # 与工具/converter 守卫同源的阈值设置（recipe 资格不与执行侧漂移）
    try:
        from app.core.config import settings as _settings
        min_points = max(1, int(getattr(_settings, "HEATMAP_MIN_POINTS", 10)))
    except Exception:  # noqa: BLE001
        min_points = 10
    plan = planner.finalize_with_profile(
        plan, profile, min_points_default=min_points,
        available_tools=available or None,
        situation=_situation,
    )

    # 角色绑定：#784 —— 以终稿计划为权威。按实际 MapSpec 图层类型解析到
    # 计划里同类型的规划图层（取其 role/cartography），仅当计划中无同
    # 类型已启用图层时才退回全局 type→role 映射。旧实现把每个 fill 一律
    # 记成 reference/administrative_choropleth —— recipe 主 choropleth 被
    # 降级成 reference，simple_view/grid 流的计划标签永远对不上。
    type_role_map = {
        "heatmap": ("primary", "visual_heatmap"),
        "circle": ("secondary", "point_overlay"),
        "fill": ("reference", "administrative_choropleth"),
        "raster": ("primary", "raster_surface"),
    }
    _role_order = {"primary": 0, "secondary": 1, "reference": 2}
    planned_by_type: Dict[str, list] = {}
    for planned in plan.map_layers:
        if planned.enabled:
            planned_by_type.setdefault(planned.layer_type, []).append(planned)
    for same_type in planned_by_type.values():
        same_type.sort(key=lambda p: _role_order.get(p.role, 3))
    _consumed_planned: set = set()

    def _resolve_binding(layer_type: str) -> tuple:
        """(role, cartography, layer_type)：计划标签优先，类型映射兜底。

        #784: evidence 同时携带实际图层类型（converter 可能按真实几何
        换型，如面数据上的 circle→fill），不再只记计划的 cartography 串。
        """
        for planned in planned_by_type.get(layer_type, []):
            if id(planned) not in _consumed_planned:
                _consumed_planned.add(id(planned))
                return planned.role, planned.cartography, layer_type
        role, carto = type_role_map.get(layer_type, ("secondary", "point_overlay"))
        return role, carto, layer_type

    bound_layers: List[Dict[str, str]] = []
    for layer_id in layer_ids:
        layer = next(
            (ly for ly in spec.get("layers", [])
             if isinstance(ly, dict) and ly.get("id") == layer_id),
            None,
        )
        if layer is None:
            continue
        actual_type = str(layer.get("type") or "")
        role, carto, _ = _resolve_binding(actual_type)
        bound_layers.append({
            "layer_id": layer_id, "role": role, "cartography": carto,
            "layer_type": actual_type,
        })

    # 缺失图层补齐：热力层被禁时不补；点叠加缺 → 从 primary_ref 授权
    out: Dict[str, Any] = {"success": True, "plan_id": plan.plan_id}
    authoring_failures: List[str] = []  # #716: honest failure ledger
    committed_layer_ids: List[str] = [b["layer_id"] for b in bound_layers]

    primary_layer = next(
        (ly for ly in plan.map_layers if ly.role == "primary" and ly.enabled), None
    )
    need_heatmap = (
        primary_layer is not None
        and primary_layer.layer_type == "heatmap"
        and not any(
            b.get("layer_type") == "heatmap" or b["cartography"] == "visual_heatmap"
            for b in bound_layers
        )
    )
    # #784/#781: 点叠加授权必须以真实点几何为前提 ——
    # - 面 primary_ref 不再复制出第二个常量 fill 层（layer id 却叫
    #   product-*-points、cartography 记 point_overlay 的错标重复层）；
    # - 栅格 primary_ref（intent.geometry_expectation=='raster' 或
    #   descriptor.raster_capable）不得产出挂在 geojson 名义源上的空
    #   circle 层。无 profile 时保持旧行为（converter 自行按真实几何
    #   推断图层类型）。
    raster_primary = (
        intent.geometry_expectation == "raster"
        or bool(primary_descriptor and primary_descriptor.get("raster_capable"))
    )
    profile_geom_types = set((profile or {}).get("geometryTypes") or [])
    has_point_geometry = any(
        g in ("Point", "MultiPoint") for g in profile_geom_types
    )
    need_points = (
        not raster_primary
        # simple_view 的 simple_point_map / 比例符号也是点族标签
        and not any(
            b.get("layer_type") == "circle"
            or b["cartography"] in ("point_overlay", "simple_point_map",
                                    "proportional_symbol")
            for b in bound_layers
        )
        and (profile is None or has_point_geometry)
    )

    primary_data: Optional[Any] = None
    if (need_heatmap or need_points) and primary_ref:
        # 只在确需补层时取全量数据（get 返回 deepcopy，避免无谓 O(N) 拷贝）
        primary_data = await session_data_manager.get(session_id, primary_ref)
    if primary_data is not None:
        from app.services.analysis_cartography_converter import (
            convert_analysis_to_mapspec_layer,
        )
        import hashlib as _hashlib

        if need_heatmap:
            analysis_payload = {
                "geojson": primary_data,
                "profile": profile,
                "algorithm": "webgis_map_product",
                "type_hint": "heatmap",
                "metadata": {
                    "render_type": "native",
                    "palette": palette,
                    **({"radius_px": radius_px} if radius_px else {}),
                    **({"point_count": profile.get("featureCount")}
                       if profile and isinstance(profile.get("featureCount"), int) else {}),
                },
            }
            # #718: legend evidence same-source with the heatmap_data /
            # dispatch seam path (NATIVE_HEATMAP_COLORS via the single
            # builder) — previously product-authored heatmaps carried no
            # legend_spec and the review could not see the gap.
            try:
                from app.lib.cartography.palettes import build_heatmap_legend_spec
                analysis_payload["legend_spec"] = build_heatmap_legend_spec(palette)
            except Exception as leg_exc:  # noqa: BLE001 - legend is best-effort
                out.setdefault("warnings", []).append(
                    f"heatmap legend_spec build failed: {leg_exc}")
            converted, _, conv_warn = convert_analysis_to_mapspec_layer(analysis_payload)
            if conv_warn:
                # converter 降级披露（几何换型/点密度封顶等）进诚实披露通道
                out.setdefault("warnings", []).extend(conv_warn)
            slug = _hashlib.sha256(f"{plan.plan_id}:heatmap".encode()).hexdigest()[:8]
            converted["id"] = f"product-{slug}-heatmap"
            # 图层名进 spec（前端面板镜像行直接采用）：无名的 product-*
            # 在面板里只能显示 id 后缀，用户无法辨认。
            converted["name"] = f"{title}·密度热力图" if title else "密度热力图"
            src_ref = {
                "type": "geojson", "ref_id": primary_ref,
                "profile": profile,
            }
            res = await mapspec_store.layer_upsert(session_id, converted, src_ref)
            if res.get("success"):
                committed_layer_ids.append(converted["id"])
                # #784: 记录 converter 的实际图层类型 + 从终稿计划解析
                # 角色/标签（converter 可能按真实几何换型）。
                h_role, h_carto, h_type = _resolve_binding(
                    str(converted.get("type") or "heatmap"))
                bound_layers.append({
                    "layer_id": converted["id"], "role": h_role,
                    "cartography": h_carto, "layer_type": h_type,
                    "authored": True,
                })
                out = _forward_evidence(res, out)
            else:
                msg = f"heatmap layer authoring failed: {res.get('message')}"
                out.setdefault("warnings", []).append(msg)
                authoring_failures.append(msg)
        if need_points:
            analysis_payload = {
                "geojson": primary_data,
                "profile": profile,
                "algorithm": "webgis_map_product",
            }
            converted, _, conv_warn = convert_analysis_to_mapspec_layer(analysis_payload)
            if conv_warn:
                # converter 降级披露（几何换型/点密度封顶等）进诚实披露通道
                out.setdefault("warnings", []).extend(conv_warn)
            slug = _hashlib.sha256(f"{plan.plan_id}:points".encode()).hexdigest()[:8]
            converted["id"] = f"product-{slug}-points"
            converted["name"] = f"{title}·点位分布" if title else "点位分布图"
            res = await mapspec_store.layer_upsert(
                session_id, converted,
                {"type": "geojson", "ref_id": primary_ref, "profile": profile},
            )
            if res.get("success"):
                committed_layer_ids.append(converted["id"])
                p_role, p_carto, p_type = _resolve_binding(
                    str(converted.get("type") or "circle"))
                bound_layers.append({
                    "layer_id": converted["id"], "role": p_role,
                    "cartography": p_carto, "layer_type": p_type,
                    "authored": True,
                })
                # #1068(E-7): points 层也转发证据 —— layout 后续失败时
                # 结果不再携带 heatmap 的过期 mutation_revision。
                out = _forward_evidence(res, out)
            else:
                msg = f"point layer authoring failed: {res.get('message')}"
                out.setdefault("warnings", []).append(msg)
                authoring_failures.append(msg)

    # 组件 + 标题落 MapSpec layout.components
    components = list(plan.components)
    # #718: colorbar must reference the actual authored heatmap layer and
    # carry the palette — previously layerId defaulted "" and the frontend
    # had to guess which layer/ramp the colorbar described.
    primary_bound = next(
        (b for b in bound_layers if b.get("role") == "primary"), None)
    # #1067(E-8): 组合期 layer_bindings 恒空（finalize 在绑定回填之前
    # 运行），legend 族组件此前永远 layerId=""。#718 只给 colorbar 补了
    # 绑定 —— 扩展到全部 legend 族（legend / categorical_legend）。
    _thematic_bound = primary_bound or next(iter(bound_layers), None)
    for comp in components:
        _comp_type = getattr(comp, "type", "")
        if _comp_type in ("continuous_colorbar", "legend", "categorical_legend") and _thematic_bound:
            opts = dict(getattr(comp, "options", {}) or {})
            if not opts.get("layerId"):
                opts["layerId"] = _thematic_bound["layer_id"]
            if _comp_type == "continuous_colorbar" and (need_heatmap or any(
                b.get("layer_type") == "heatmap"
                or b.get("cartography") == "visual_heatmap"
                for b in bound_layers
            )):
                opts["palette"] = palette
            comp.options = opts
    # ── 设计候选 + 决策 trace（/goal cartography-design M2）：表达模型
    # 层 2–4 候选（含数据资格 typed 门控与降级路径）+ 「为何选/为何弃」
    # 决策链。增值证据面：best_effort 降级不阻断组装（同 #1549 纪律）；
    # 投影只来自结构化事实（intent task / profile / bound_layers），
    # 无 query 字符串。
    design_payload: Dict[str, Any] = {}
    try:
        from app.lib.cartography.design_candidates import (
            DenominatorEvidence as _DenomEvidence,
            DesignTaskContext as _DesignCtx,
            generate_design_candidates as _gen_design_candidates,
        )
        from app.lib.cartography.design_decision_trace import (
            build_design_decision_trace as _build_trace,
        )
        from app.services.gis_harness.planner import _TASK_CATEGORY_BY_INTENT

        # overlay 次几何/字段证据（扩展可行域与跨源分母投影用）：
        # 成都学校（点主数据）+ 行政区（overlay 面，含 population）→
        # 面族表达可行 + 分母可来自 overlay。描述符读取廉价。
        _secondary_geoms: List[str] = []
        _overlay_fields: List[str] = []
        for _oref in (overlay_refs or [])[:4]:
            _odesc = await session_data_manager.get_ref_descriptor(
                session_id, _oref)
            if not _odesc:
                continue
            for _g in (_odesc.get("geometry_types")
                       or _odesc.get("geometryTypes") or []):
                _cat = {"Point": "point", "MultiPoint": "point",
                        "LineString": "line", "MultiLineString": "line",
                        "Polygon": "polygon", "MultiPolygon": "polygon",
                        }.get(str(_g))
                if _cat and _cat not in _secondary_geoms:
                    _secondary_geoms.append(_cat)
            _of = _odesc.get("field_schema") or _odesc.get("fields")
            if isinstance(_of, dict):
                _overlay_fields.extend(
                    str(f).lower() for f in _of.keys())
        _geom_types = set((profile or {}).get("geometryTypes") or [])
        if _geom_types:
            from app.services.gis_harness.recipes import _geometry_category
            _gcat = _geometry_category(list(_geom_types))
            _geom_kind = {"point": "point", "line": "line"}.get(
                _gcat, "polygon")
        else:
            _geom_kind = {"point": "point", "line": "line",
                          "polygon": "polygon", "raster": "raster"}.get(
                str(intent.geometry_expectation or ""), "polygon")
        # 分母证据：DENOMINATOR_FIELD_HINTS 单一事实源词表命中（字段名
        # 级匹配；宁缺毋滥——无命中即无分母，不猜）。
        _fields = (profile or {}).get("fields") or {}
        _field_names = [str(f).lower() for f in (
            _fields.keys() if isinstance(_fields, dict) else [])]
        from app.services.gis_harness.workflow_schema import (
            DENOMINATOR_FIELD_HINTS,
        )
        _denom_field = next(
            (f for f in _field_names
             if any(h in f for h in DENOMINATOR_FIELD_HINTS)),
            # 跨源分母：主数据无分母字段时，overlay（如行政区 population）
            # 字段词表命中同样成立——分母证据来源如实标注 overlay。
            next((f for f in _overlay_fields
                  if any(h in f for h in DENOMINATOR_FIELD_HINTS)), ""))
        _denom_source = (
            "profile" if _denom_field and _denom_field in _field_names
            else ("overlay" if _denom_field else "profile"))
        # 时相证据：意图声明 change_detection 且 profile 存在时相字段
        # 才认定双期可达（声明与数据双证据）。
        from app.services.gis_harness.workflow_schema import (
            TIME_FIELD_HINTS,
        )
        _temporal_periods = 2 if (
            intent.task == "change_detection"
            and any(h in f for f in _field_names
                    for h in TIME_FIELD_HINTS)
        ) else 0
        # 用户显式表达要求投影：只来自 typed 信号 —— ① intent 规则引擎
        # 的 bare_density_visual（裸「密度」视觉概览语义锁，G16/G17/C018；
        # visual_heatmap 注册别名即 density_overview）；② user-wins 表达
        # pin（design_select 固化，apply_pending_bindings 已发布快照）。
        # 不做 NL 二次猜测；两者缺席 → 无显式要求（自动推荐语义）。
        _pinned_model = ""
        try:
            from app.lib.cartography.model_library import (
                get_map_model_registry as _design_model_reg,
            )
            from app.services.gis_harness.representation_binding.requirement_to_mapspec import (
                _load_document,
                grammar_face_pins,
            )
            _pin_doc = await _load_document(session_id)
            if _pin_doc is not None:
                _pinned_raw = str(
                    grammar_face_pins(_pin_doc).get("pinned_representation")
                    or "")
                # pin 存归一化词（别名可解析）——canonical 到注册 id，
                # 与候选 map_model_id 同一比对空间。
                if _pinned_raw:
                    _pm = _design_model_reg().resolve(_pinned_raw)
                    _pinned_model = _pm.id if _pm is not None else ""
        except Exception:  # noqa: BLE001 — pin 缺席 = 无显式要求
            _pinned_model = ""
        _user_models = tuple(dict.fromkeys(filter(None, (
            _pinned_model,
            "visual_heatmap" if "bare_density_visual" in (
                intent.matched_rules or []) else "",
        ))))
        _design_ctx = _DesignCtx(
            task_categories=_TASK_CATEGORY_BY_INTENT.get(
                str(intent.task or ""), ("thematic_cartography",)),
            geometry_kind=_geom_kind,
            statistic=(
                "density" if (
                    str(getattr(intent, "measure", "") or "")
                    in ("density", "rate", "ratio")
                    or intent.task in ("analytical_density",
                                       "concentration_analysis"))
                else "count"),
            artifact_types=tuple(dict.fromkeys((
                *{
                    "point": ("point_feature_set", "admin_aggregate_table"),
                    "line": ("line_feature_set", "admin_aggregate_table"),
                    "polygon": ("polygon_feature_set", "admin_aggregate_table"),
                    "raster": ("raster_surface",),
                }.get(_geom_kind, ("admin_aggregate_table",)),
                # overlay 面数据在场 → 行政边界产物面成立（面族表达证据）
                * (("admin_boundary_set",) if "polygon" in _secondary_geoms
                   else ()),
            ))),
            secondary_geometry_kinds=tuple(
                g for g in _secondary_geoms if g != _geom_kind),
            point_count=int((profile or {}).get("featureCount") or 0)
            if _geom_kind == "point" else 0,
            point_count_basis="profile" if (
                _geom_kind == "point"
                and isinstance((profile or {}).get("featureCount"), int)
            ) else "unknown",
            denominator=_DenomEvidence(
                field=_denom_field, available=bool(_denom_field),
                unit=(
                    "户数" if _denom_field and any(
                        h in _denom_field
                        for h in ("household", "户数"))
                    else ("人口" if _denom_field else "")),
                source=_denom_source),
            temporal_periods=_temporal_periods,
            user_requested_models=_user_models,
            scale_hint=str(getattr(intent.scope, "level", "") or ""),
        )
        _primary_bound_model = str(
            (primary_bound or {}).get("cartography") or "")
        _cand_set = _gen_design_candidates(
            _design_ctx, primary_model_id=_primary_bound_model)
        # design_digest：LLM 上下文预算（MSG_MAX_CHARS=2500）放不下的
        # 全量候选——紧凑投影（模型/族/适宜性/一句理由）随 summary 面
        # 保留（llm_result_formatter 键保留）；全量候选走 details 面
        # （64KB 预算）与原始结果。
        _digest_cands = [
            {"m": c.map_model_id, "f": c.expression_family,
             "s": c.suitability.value,
             **({"why": (c.condition_reasons_zh[0]
                         or c.reasons_zh[0])[:60]}
                if (c.condition_reasons_zh or c.reasons_zh) and
                c.suitability != "suitable" else {})}
            for c in _cand_set.candidates[:4]
        ]
        design_payload = {
            "design_candidates": _cand_set.to_bounded_dict(),
            "design_digest": {
                "count": len(_cand_set.candidates),
                "selected": _cand_set.selected_candidate_id[:48],
                "candidates": _digest_cands,
                "blocked": [c.map_model_id for c in _cand_set.candidates
                            if c.suitability.value == "blocked"],
                "honored": list(
                    _cand_set.user_request_handling.get("honored") or [])[:4],
            },
            "design_decision_trace": _build_trace(
                _design_ctx, _cand_set,
                intent_summary={
                    "task": intent.task,
                    "scope": intent.scope.name,
                    "subject": intent.subject.category,
                    "geometry": intent.geometry_expectation,
                },
                mapspec_fingerprint=str(out.get("mapspec_fingerprint") or ""),
                primary_bound_model=_primary_bound_model,
            ),
        }
    except Exception as _design_exc:  # noqa: BLE001 — 证据面降级
        logger.warning(
            "webgis_map_product design candidates degraded: %s", _design_exc)
        design_payload = {"design_candidates_degraded": {
            "code": "design_candidates_error",
            "detail": str(_design_exc)[:160],
        }}

    if title:
        from app.services.gis_harness.components import title_component
        components = [c for c in components if c.type != "title"]
        components.append(title_component(title))
    # 设计候选面板（/goal carto-design M7）：design 成功 → 只读披露组件
    # 随产品落 layout（options.design = design_digest 有界投影；接受
    # 动作经 webgis_map_design_select 统一 action，面板不带状态机）。
    if isinstance(design_payload.get("design_digest"), dict):
        from app.contracts.cartography_components import (
            validate_design_candidates_payload,
        )
        from app.services.gis_harness.components import CartographyComponent
        _design_options = {"design": design_payload["design_digest"]}
        # F4（评审 R2）：面板载荷过契约校验器（与其它披露面板工厂
        # 同纪律；坏形状拒绝落组件而不是静默绕过）。
        _design_invalid = validate_design_candidates_payload(
            {"design": _design_options["design"]})
        if _design_invalid is None:
            components = [
                c for c in components if c.type != "design_candidates"]
            components.append(CartographyComponent(
                id="design-candidates", type="design_candidates",
                position="top-left", priority=49,
                options=_design_options,
            ))
        else:
            out.setdefault("warnings", []).append(
                f"design candidates panel skipped: {_design_invalid}")
    component_dicts = [c.to_mapspec() for c in components]
    # #1068(E-7): layout_set 整表替换组件表 —— 用户经
    # webgis_component_update create=true 建的浮动面板（chart/statistics）
    # 此前被静默丢弃。保留不在本产品组合内的既有用户面板；组合内的
    # 以新组合（本次 agent 意图）为准。
    _composed_ids = {str(c.get("id")) for c in component_dicts}
    for _existing in (spec.get("layout") or {}).get("components") or []:
        if (
            isinstance(_existing, dict)
            and _existing.get("type") in ("chart_panel", "statistics_panel")
            and str(_existing.get("id")) not in _composed_ids
        ):
            component_dicts.append(_existing)
    # H02-W3：被锁组件（workbench.lockedComponentIds = 用户 pin 通道）
    # 的 placement/position 在 agent 再组合中保持用户决策 —— omission
    # 守卫保护存在性，空间决策同样不可被组合默认值覆写（user-wins）。
    try:
        from app.contracts.workbench_locks import locked_component_ids_of

        _pinned = {
            str(c.get("id")): c
            for c in (spec.get("layout") or {}).get("components") or []
            if isinstance(c, dict)
        }
        for _lock_id in locked_component_ids_of(spec):
            _prev = _pinned.get(_lock_id)
            _new = next(
                (c for c in component_dicts
                 if str(c.get("id")) == _lock_id), None,
            )
            if _prev is None or _new is None:
                continue
            if _prev.get("placement") is not None:
                _new["placement"] = copy.deepcopy(_prev["placement"])
            if _prev.get("position"):
                _new["position"] = _prev["position"]
    except Exception:  # noqa: BLE001 — pin 保持是增值面，不阻断组装
        observe_optional_failure(__name__)
    layout_res = await mapspec_store.layout_set(
        session_id, components=component_dicts,
        # H02-G3：产品组合身份随同一 layout 事务落地（ADR-0214 D2/D3
        # composition 通道）—— 物理 MapSpec 可追溯产品模板契约；
        # 用户显式契约身份（contract_id 非空）不覆盖（user-wins）。
        composition=_product_composition_identity(plan, component_dicts, spec),
    )
    if not layout_res.get("success"):
        msg = f"layout components commit failed: {layout_res.get('message')}"
        out.setdefault("warnings", []).append(msg)
        authoring_failures.append(msg)

    # 绑定记录回填 plan（供 evidence 消费）；#784: 除 cartography 字符串
    # 相等外，按实际图层类型兼容匹配 —— simple_view 的 simple_point_map、
    # grid 的 aggregate_grid 等 template 标签与全局映射词汇表不同名，
    # 此前永远匹配不上（completeness 永远 incomplete）。
    for entry in bound_layers:
        actual_type = entry.get("layer_type")
        for planned in plan.map_layers:
            if planned.layer_id:
                continue
            if (
                planned.cartography == entry["cartography"]
                or (bool(actual_type) and planned.layer_type == actual_type)
            ):
                planned.layer_id = entry["layer_id"]
                planned.bound_ref = primary_ref or ""
                break
    # #784: done 能力面不再只有 poi_query 一族 —— 绑定图层自身的
    # provenance（生成该图层的分析算法）映射回能力 id，admin_aggregation
    # / grid_binning 等步骤在图层绑定后即可标记完成，统计维 completeness
    # 不再对已完成的行政/格网产品报 missing。
    # 映射不再手写：主表由 AlgorithmRegistry 派生（tool_candidates →
    # 主 capability）；仅 converter provenance 词汇里的非工具别名保留
    # 在 _LEGACY_PROVENANCE_ALIASES（raster converter 的 algorithm 串）。
    from app.lib.gis.algorithm_registry import get_algorithm_registry
    _LEGACY_PROVENANCE_ALIASES = {
        "aggregate_points": "admin_aggregation",
        "local_raster": "raster_source",
        "remote_sensing_index": "raster_source",
    }
    _PROVENANCE_CAPABILITY = {
        **_LEGACY_PROVENANCE_ALIASES,
        **get_algorithm_registry().tool_to_capability(),
    }
    # artifact lineage（§27 有界证据）：绑定图层 → 语义 artifact 类型。
    # 同一轮遍历顺带完成 provenance → capability 回填（done_caps）。
    from app.lib.gis.capability_registry import get_capability_registry
    _caps_registry = get_capability_registry()
    _artifact_lineage: List[Dict[str, str | None]] = []
    done_caps: set = set()
    # #1067(E-4): 谱系查新 —— 此前用的是 :363 的变更前快照，本轮
    # authored 的图层（heatmap/points）不在其中，producer_tool 恒空。
    _post_spec = (
        layout_res.get("mapspec") if isinstance(layout_res.get("mapspec"), dict) else None
    )
    if _post_spec is not None:
        spec = _post_spec
    if primary_data is not None:
        # #1067: 此前无差别标 {poi_query, point_profile, raster_source} ——
        # 栅格/面主数据也把点类能力标完成，completeness 谎报。按主数据
        # 真实形态只标实际适用的来源能力。
        _primary_geoms = set((profile or {}).get("geometryTypes") or [])
        if str(primary_ref).startswith("ref:raster/"):
            done_caps.add("raster_source")
        elif "Point" in _primary_geoms or not _primary_geoms:
            done_caps.update({"poi_query", "point_profile"})
        else:
            done_caps.add("poi_query")
    for entry in bound_layers:
        prov_layer = next(
            (ly for ly in spec.get("layers", [])
             if isinstance(ly, dict) and ly.get("id") == entry["layer_id"]),
            None,
        )
        provenance = (prov_layer or {}).get("provenance")
        algorithm = (
            provenance.get("algorithm") if isinstance(provenance, dict) else None
        )
        provenance_cap = _PROVENANCE_CAPABILITY.get(algorithm or "")
        if provenance_cap:
            done_caps.add(provenance_cap)
        planned_src = next(
            (ly for ly in plan.map_layers if ly.layer_id == entry["layer_id"]),
            None,
        )
        capability = (
            (planned_src.source_capability if planned_src else "")
            or provenance_cap or ""
        )
        cap_desc = _caps_registry.get(capability) if capability else None
        _artifact_lineage.append({
            "layer_id": entry["layer_id"],
            "role": entry.get("role"),
            "cartography": entry.get("cartography"),
            "source_ref": primary_ref or "",
            "producer_tool": algorithm or "",
            "capability": capability,
            "artifact_type": (
                cap_desc.output_artifact_types[0]
                if cap_desc and cap_desc.output_artifact_types else ""
            ),
        })
    for req in plan.data_requirements:
        if req.capability in done_caps:
            req.status = "available"
            req.bound_ref = primary_ref or ""
    for step in plan.analysis_steps:
        if step.capability in done_caps:
            step.status = "done"
            step.bound_ref = primary_ref or ""
    plan.completeness = planner.assess_completeness(plan)

    # audit #835: 声明式 NEEDS_ADMIN_UNITS 回退可达化 —— 行政面未落地
    # （fill 计划 primary 未绑定）而点层已绑时，按 recipe 声明记录
    # FallbackDecision 并把点层计划条目升为 primary（此前声明是死配置，
    # 主层不回退也不记原因，completeness 永远 missing）。
    _primary_planned = next(
        (ly for ly in plan.map_layers if ly.role == "primary" and ly.enabled),
        None,
    )
    if (
        _primary_planned is not None
        and not _primary_planned.layer_id
        and _primary_planned.layer_type == "fill"
        and any(b.get("layer_type") == "circle" for b in bound_layers)
    ):
        _recipe_decl = planner.recipes.get(plan.recipe_id)
        _fb_decl = next(
            (fb for fb in (_recipe_decl.fallbacks if _recipe_decl else [])
             if fb.reason_code == "NEEDS_ADMIN_UNITS"),
            None,
        )
        if _fb_decl is not None:
            _point_planned = next(
                (ly for ly in plan.map_layers
                 if ly.enabled and ly.layer_id
                 and ly.layer_type == "circle" and ly.role != "primary"),
                None,
            )
            if _point_planned is not None:
                _primary_planned.role = "secondary"
                _primary_planned.note = (
                    (_primary_planned.note + "; " if _primary_planned.note else "")
                    + "demoted: NEEDS_ADMIN_UNITS (no admin face materialized)"
                )
                _point_planned.role = "primary"
                plan.fallbacks.append(FallbackDecision(
                    from_element=_primary_planned.cartography,
                    to_element=_fb_decl.use or "point_distribution",
                    reason_code="NEEDS_ADMIN_UNITS",
                    evidence={
                        "bound_layer_types": sorted({
                            b.get("layer_type") for b in bound_layers if b.get("layer_type")
                        }),
                        "unbound_primary": _primary_planned.cartography,
                    },
                ))
                plan.completeness = planner.assess_completeness(plan)


    # ── 语义产品层（ADR-0258）：spec 构建/编辑存活合并 + 编译 + 语义
    # 完整性 —— additive 结果键，降级绝不阻断既有组装路径。既有 spec
    # 读取自 SessionPlan chapter（用户此前的 product edit 存活于此）。
    product_layer: Dict[str, Any] = {}
    try:
        from app.services.gis_harness.product_runtime import (
            load_chapter_product_spec,
            produce_product_layer,
        )
        from app.services.gis_harness.template_catalog import get_template_catalog
        from app.services.session_plan import load_session_plan

        _envelope = await load_session_plan(session_id)
        _existing_spec = (
            load_chapter_product_spec(_envelope.gis_chapter)
            if _envelope is not None and _envelope.gis_chapter is not None
            else None
        )
        _tpl = (
            get_template_catalog().get_product_template(plan.template_id)
            if plan.template_id else None
        )
        product_layer = produce_product_layer(
            plan=plan, intent=intent, template=_tpl,
            existing_spec=_existing_spec, primary_ref=primary_ref or "",
        )
    except Exception as _prod_exc:  # noqa: BLE001 — 增值面降级
        logger.warning("webgis_map_product product layer degraded: %s", _prod_exc)
        product_layer = {
            "product_compile_fallback": {
                "code": "product_layer_error",
                "detail": str(_prod_exc)[:160],
            },
        }

    # audit4 #979: 产品阶段 guidance 投影（与 intent 阶段同理由）——
    # 绑定结果/fallback 证据/完备度必须有界地到达 LLM，否则降级与缺口
    # 不可见、模型无法自纠。
    def _item_field(item: Any, key: str) -> Any:
        if isinstance(item, dict):
            return item.get(key)
        return getattr(item, key, None)

    product_guidance: List[str] = [
        f"recipe={plan.recipe_id} 状态={plan.status}",
        f"已绑定图层 {len(bound_layers)} 个"
        + (
            "（" + ", ".join(
                str(_item_field(b, "layer_id") or _item_field(b, "role") or "?")
                for b in bound_layers[:3]
            ) + ("…" if len(bound_layers) > 3 else "") + "）"
            if bound_layers else ""
        ),
    ]
    if plan.fallbacks:
        # V4（ADR-0151）：降级可解释 —— guidance 用结构化决策的披露面
        # （disclosure/reason_code），不再打印 dict 转义噪声。
        first_fb = plan.fallbacks[0]
        fb_msg = (
            _item_field(first_fb, "disclosure")
            or _item_field(first_fb, "reason_code")
            or _item_field(first_fb, "reason")
            or _item_field(first_fb, "message")
            or first_fb
        )
        product_guidance.append(
            f"⚠ fallback {len(plan.fallbacks)} 次（如: {str(fb_msg)[:120]}）")
    if authoring_failures:
        product_guidance.append(
            f"⚠ {len(authoring_failures)} 项图层/组件提交失败 —— 产品不完整，需补数据或重试"
        )
    # ADR-0258：语义产品面投影（views/completeness 摘要到 LLM，有界）。
    _pc = product_layer.get("product_completeness") or {}
    if _pc:
        _views_n = len(product_layer.get("product_views") or [])
        product_guidance.append(
            f"产品视图 {_views_n} 个（kind: "
            + ", ".join(str(v.get("kind")) for v in (product_layer.get("product_views") or [])[:4])
            + "）"
        )
        if not _pc.get("complete", True):
            _err_codes = [
                str(f.get("code")) for f in (_pc.get("findings") or [])
                if f.get("severity") == "error"
            ]
            if _err_codes:
                product_guidance.append(
                    f"⚠ 产品完整性: {_err_codes[0]}"
                    + (f" 等{len(_err_codes)}项" if len(_err_codes) > 1 else "")
                    + "（semantic completeness, 见 product_completeness）"
                )
    _fallback_dicts = [
        fb if isinstance(fb, dict) else fb.model_dump() for fb in plan.fallbacks
    ]
    # V4（ADR-0151 / P4）：降级决策的 LLM 上下文渲染 + 前端可见通道
    # （只改后端事件字段；呈现由 07 线消费）。
    from app.services.gis_harness.recipes import render_fallback_for_llm
    _fallback_llm = render_fallback_for_llm(plan.fallbacks)
    _all_recipe_ids = set(planner.recipes.all_ids)
    out.update({
        "fallback_llm": _fallback_llm,
        "fallback_summary": {
            "count": len(plan.fallbacks),
            "reason_codes": [
                str(_item_field(fb, "reason_code") or "")
                for fb in plan.fallbacks[:8]
            ],
            "recipe_swapped": any(
                str(_item_field(fb, "from_element")) in _all_recipe_ids
                and _item_field(fb, "to_element") in _all_recipe_ids
                for fb in plan.fallbacks
            ),
        },
        "recipe_id": plan.recipe_id,
        "template_id": plan.template_id,
        "status": plan.status,
        "layers": bound_layers,
        "components": component_dicts,
        "fallbacks": _fallback_dicts,
        "eligibility": plan.eligibility,
        "completeness": plan.completeness,
        # Workflow V2（Goal C / R1-A1）：finalize 期契约摘要与方法论警告
        # 必须随工具结果上行 —— SessionPlan chapter 合并后，完成管线的
        # verdict V2（七维 + BLOCKED_BY_*）在生产路径才真正可达。
        "workflow_contract": plan.workflow_contract,
        "methodology_warnings": list(plan.methodology_warnings)[:8],
        "guidance": product_guidance[:10],
        "intent": {"task": intent.task, "scope": intent.scope.name,
                   "subject": intent.subject.category},
        **design_payload,
        "map_product_evidence": {
            "intent_resolution": {
                "query": intent.query, "task": intent.task,
                "matched_rules": intent.matched_rules,
                "confidence": intent.confidence,
                "hint_applied": intent.hint_applied,
            },
            "recipe_selection": {
                "selected": plan.recipe_id,
                # #723: record what the deterministic selector actually
                # considered — the old comprehension could only ever yield
                # [plan.recipe_id], a degenerate singleton.
                # H-9（#864）：与真实选择同源（带 project_verified 的
                # 一次取数），evidence 候选序不再与决策依据漂移。
                "candidates": [
                    c.id for c in _candidates
                ] or [plan.recipe_id],
            },
            "recipe_eligibility": plan.eligibility,
            # #1067: 此前是 out.get("fallbacks", []) —— 位于本字面量内部，
            # 求值时 out 尚无该键，恒为 []（spec §2.7 要求证据携带回退转录）。
            "fallback_decisions": _fallback_dicts,
            # ── registry 编排证据（§27，有界转录）────────────────────
            "capability_resolution": [
                {"capability": r.capability, "status": r.status,
                 "resolved_tool": r.resolved_tool,
                 "resolved_algorithm": r.resolved_algorithm}
                for r in plan.data_requirements
            ],
            "algorithm_selection": [
                bounded_selection_evidence(r)
                for r in plan.algorithm_selections
            ],
            "map_model_selection": plan.map_model_selection,
            "template_selection": plan.template_selection,
            "artifact_lineage": _artifact_lineage,
            "component_selection": [c["type"] for c in component_dicts],
            "map_product_completeness": plan.completeness,
            "bound_layers": bound_layers,
            # #1084: 产品证据携带 registry 语义指纹；与当前编译指纹
            # 不一致（计划跨部署重放）时如实披露 stale。
            "manifest_fingerprint": plan.manifest_fingerprint,
            "manifest_stale": _manifest_stale(plan.manifest_fingerprint),
        },
        "summary": (
            f"产品组装完成 recipe={plan.recipe_id}；图层 {len(bound_layers)}，"
            f"组件 {len(component_dicts)}，fallback {len(plan.fallbacks)} 次"
            + (f"；⚠ {len(authoring_failures)} 项图层/组件提交失败（见 warnings）"
               if authoring_failures else "")
        ),
    })
    # ADR-0258：语义产品层 additive 键（product_spec/product_views/
    # product_compile/product_completeness 或 product_compile_fallback）。
    out.update(product_layer)
    if authoring_failures and not bound_layers:
        # #716: nothing was actually mounted — do not let the caller
        # (or the plan tick) treat this as a complete product.
        out["cartographic_authoring_failed"] = True
        out["success"] = False
        out["error"] = f"Failed to mount any map layers: {'; '.join(authoring_failures)}"
    final_spec = layout_res.get("mapspec") if isinstance(layout_res.get("mapspec"), dict) else None
    if final_spec is not None:
        out["mapspec"] = final_spec
        out = _forward_evidence(layout_res, out)
    if authoring_failures:
        # #716: evidence forwarding REPLACES warnings — re-merge the
        # authoring-failure ledger so the failure survives the final
        # layout forward.
        merged = list(out.get("warnings") or [])
        for msg in authoring_failures:
            if msg not in merged:
                merged.append(msg)
        out["warnings"] = merged
    return out



def register_map_product(registry: ToolRegistry) -> None:
    tool(
        registry,
        tier=2, domains=["statistics", "report", "network", "temporal"], name="webgis_map_product",
        capabilities=['thematic_cartography'],
        # #996: audit4 #979 给 result 形状加了 guidance 键（绑定/fallback/
        # 完备度有界投影）——RESULT 契约变更，contract_version 1→2（1.0#cv2）。
        contract_version=2,
        description=(
            "地图产品组装器：数据/图层到位后，按 CartographyRecipe 复检资格"
            "（几何/最小点数/字段——代码侧确定性），把已授权图层绑定到产品角色、"
            "补齐缺失图层（如热力+点叠加+组件）、写 layout.components（标题/色条/"
            "图例/指北针/比例尺/署名）并提交 MapSpec。"
            "\n何时用：webgis_map_intent 出计划、数据工具执行完之后——把散落图层"
            "组成完整产品（『分布情况』→ 热力+点+统计+色条+标题）。"
            "样本不足时热力层会被确定性禁用并记录 fallback 证据（自动降级点图）。"
            "\n注意：本工具不重跑分析；只做资格复检、角色绑定、组件与版面。"
        ),
        args_model=MapProductArgs,
        side_effect="state_mutation",
        deterministic=False,
        latency_class="medium",
        memory_class="medium",
        scale_class="medium",
        tags=("地图产品", "组装", "图例", "色条", "标题", "指北针", "map product", "成图"),
        output_semantic_type="map_product",
        result_size_policy="inline_small",
        required_context=("map_state", "cartography_state", "ref_cursor"),
        map_mutations=("add_layer", "style_layer", "component", "map_product"),
        data_mutations=("session_state",),
        failure_modes=("invalid_args", "missing_data"),
        summary=(
            "地图产品组装器：数据/图层到位后按 recipe 复检资格（几何/最小点数/"
            "字段），把已授权图层绑定到产品角色、补齐缺失图层（如热力+点叠加+组"
            "件）、写标题/色条/图例/指北针/比例尺/署名等组件并提交 MapSpec。在 "
            "webgis_map_intent 出计划、数据工具执行完之后调用；不重跑分析，样本"
            "不足时热力层自动降级为点图。"
        ),
        examples=(
            "数据都齐了，把这些图层组装成最终分布图",
            "热力图上叠加原始点，加上标题、色条和指北针",
        ),
        anti_examples=(
            "重新跑一遍缓冲区分析——本工具不重跑分析，只做资格复检与组装",
            "只改指南针样式——那是组件局部突变，用 webgis_component_update",
        ),
    )(partial(webgis_map_product, registry))
