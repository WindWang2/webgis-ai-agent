"""GIS Harness agent 工具面（Pi / ChatEngine 共用，经 ToolDispatchService 执行）。

三个工具对应 Harness 的三个职责入口：

- ``webgis_map_intent``     —— Intent 解析 + Recipe 候选 + 计划骨架（纯确定性，
                              LLM hint 显式合并、显式记录）；
- ``webgis_map_product``    —— 数据/图层到位后的产品组装：eligibility 复检
                              → 角色绑定 → 缺失图层授权 → 组件/版面落 MapSpec；
- ``webgis_component_update``—— 组件局部突变（「换一个指南针」「色条竖向」），
                              只动 layout.components，绝不触发数据重查/重分析。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.services.session_data import session_data_manager
from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)

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


class MapIntentArgs(BaseModel):
    query: str = Field(..., description="用户原始 GIS 请求文本（如『成都小学的分布情况』）")
    task_hint: Optional[str] = Field(
        None, description="[可选] LLM 语义提示：任务类型。合法值见返回 intent.task 的类型族")
    scope_hint: Optional[str] = Field(None, description="[可选] 范围提示（如 成都市）")
    subject_hint: Optional[str] = Field(None, description="[可选] 主体提示（如 小学）")
    geometry_hint: Optional[str] = Field(
        None, description="[可选] 期望几何 point/line/polygon/raster")


class MapProductArgs(BaseModel):
    query: str = Field(..., description="用户原始请求文本（驱动 intent/recipe/计划）")
    session_id: Optional[str] = Field(None, description="会话 ID")
    layer_ids: List[str] = Field(
        default_factory=list,
        description="已存在于 MapSpec 的图层 id（来自先前工具 step_result.layer_id）。"
                    "按图层类型确定性绑定角色：heatmap→primary、circle→point_overlay、"
                    "fill→admin_choropleth、raster→raster_surface")
    primary_ref: Optional[str] = Field(
        None, description="主数据 ref（ref:geojson-xxx）。缺失的规划图层会从该 ref 授权补齐")
    overlay_refs: List[str] = Field(
        default_factory=list,
        description="辅助数据 ref 列表（如行政区边界面 ref），补充 reference 角色")
    title: Optional[str] = Field(None, description="产品标题（缺省由 intent 派生）")
    template_id: Optional[str] = Field(None, description="产品模板 id（缺省按 recipe 自动匹配）")
    palette: str = Field("classic", description="热力配色 classic/magma/viridis/thermal")
    radius_px: Optional[int] = Field(
        None, ge=4, le=100, description="视觉热力半径（像素）。显式像素语义")


class ComponentUpdateArgs(BaseModel):
    session_id: Optional[str] = Field(None, description="会话 ID")
    component_id: Optional[str] = Field(
        None, description="组件 id（如 north-arrow）。与 component_type 二选一")
    component_type: Optional[str] = Field(
        None, description="组件类型（如 north_arrow）——按类型命中第一个")
    enabled: Optional[bool] = Field(None, description="启用/禁用（『不要指南针』= enabled:false）")
    position: Optional[str] = Field(
        None, description="位置 top-left/top-center/top-right/bottom-left/bottom-center/bottom-right")
    style: Optional[Dict[str, Any]] = Field(None, description="样式合并（半透明字典）")
    options: Optional[Dict[str, Any]] = Field(
        None, description="选项合并（如 {'variant':'compass_rose'} / {'orientation':'vertical'}）")


def register_gis_harness_tools(registry: ToolRegistry):
    """注册 GIS Harness 工具（tier 1：意图解析廉价且恒可用）。"""

    @tool(
        registry,
        tier=2, domains=["statistics", "report"], name="webgis_map_intent",
        description=(
            "GIS 制图意图解析器（确定性，无副作用）。输入用户请求，返回 typed "
            "MapRequestIntent（scope/subject/task/analysis_intents/cartography_intents/"
            "output_intents）、候选 CartographyRecipe 与推荐 recipe、MapProductPlan "
            "骨架（数据需求/分析步骤/图层角色/组件/输出，按能力面声明而非硬编码工具）。"
            "\n何时用：任何「做一张图/分布/密度/各区统计/周边/报告配图」类请求的"
            "第一步——先拿结构化意图与计划，再执行数据工具；数据回来后用 "
            "webgis_map_product 复检并组装产品。"
            "\n语义护栏：『每平方公里密度』是定量分析意图，不会被降级为视觉热力；"
            "『各区数量』首选行政聚合+choropleth 而非热力图。"
        ),
        args_model=MapIntentArgs,
    )
    async def webgis_map_intent(
        query: str,
        task_hint: Optional[str] = None,
        scope_hint: Optional[str] = None,
        subject_hint: Optional[str] = None,
        geometry_hint: Optional[str] = None,
    ) -> dict:
        from app.services.gis_harness.intent import merge_intent_hints, resolve_map_request_intent
        from app.services.gis_harness.planner import MapProductPlanner, resolve_tool_for_capability

        base = resolve_map_request_intent(query)
        hints: Dict[str, Any] = {}
        if task_hint:
            hints["task"] = task_hint
        if geometry_hint:
            hints["geometry_expectation"] = geometry_hint
        intent = merge_intent_hints(base, hints)
        # scope/subject 提示只做补全（不覆盖确定性命中）
        if scope_hint and not intent.scope.name:
            from app.services.gis_harness.intent import ScopeIntent
            intent.scope = ScopeIntent(name=scope_hint, level="city")
            intent.hint_applied.append(f"scope->{scope_hint}")
        if subject_hint and intent.subject.type == "unknown":
            from app.services.gis_harness.intent import SubjectIntent
            intent.subject = SubjectIntent(type="poi", category=subject_hint)
            intent.hint_applied.append(f"subject->{subject_hint}")

        planner = MapProductPlanner()
        candidates = planner.recipes.select_candidates(intent)
        plan = planner.plan_from_intent(intent)

        available = set(registry._tools.keys()) if hasattr(registry, "_tools") else set()
        capabilities = []
        for req in plan.data_requirements:
            capabilities.append({
                "capability": req.capability,
                "purpose": req.purpose,
                "resolved_tool": resolve_tool_for_capability(req.capability, available),
            })

        return {
            "success": True,
            "intent": intent.model_dump(),
            "candidates": [
                {"id": r.id, "name": r.name, "primary_cartography": r.primary_cartography,
                 "description": r.description}
                for r in candidates
            ],
            "recommended_recipe": plan.recipe_id,
            "plan": plan.model_dump(),
            "capabilities": capabilities,
            "summary": (
                f"意图:{intent.task} 范围:{intent.scope.name or '未识别'} "
                f"主体:{intent.subject.category or '未识别'} → 推荐 recipe:{plan.recipe_id}"
            ),
        }

    @tool(
        registry,
        tier=2, domains=["statistics", "report"], name="webgis_map_product",
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
    )
    async def webgis_map_product(
        query: str,
        session_id: Optional[str] = None,
        layer_ids: Optional[List[str]] = None,
        primary_ref: Optional[str] = None,
        overlay_refs: Optional[List[str]] = None,
        title: Optional[str] = None,
        template_id: Optional[str] = None,
        palette: str = "classic",
        radius_px: Optional[int] = None,
    ) -> dict:
        from app.services.gis_harness.intent import resolve_map_request_intent
        from app.services.gis_harness.planner import MapProductPlanner
        from app.services.gis_harness.components import CartographyComponent
        from app.services.mapspec_store import mapspec_store
        from app.services.spatial_meta_profiler import profile_from_descriptor

        if not session_id:
            return {"success": False, "message": "Missing session_id"}

        layer_ids = list(layer_ids or [])
        overlay_refs = list(overlay_refs or [])

        intent = resolve_map_request_intent(query)
        planner = MapProductPlanner()
        plan = planner.plan_from_intent(intent, template_id=template_id or "")

        # 主数据 profile（eligibility 复检输入）：优先 primary_ref descriptor，
        # 其次第一个绑定图层的 source profile，缺省空 profile（诚实降级）。
        profile: Optional[Dict[str, Any]] = None
        primary_data: Optional[Any] = None
        if primary_ref:
            descriptor = await session_data_manager.get_ref_descriptor(session_id, primary_ref)
            if descriptor:
                profile = profile_from_descriptor(descriptor)
            primary_data = await session_data_manager.get(session_id, primary_ref)

        if profile is None:
            spec = await mapspec_store.get_mapspec(session_id)
            for layer_id in layer_ids:
                layer = next(
                    (l for l in (spec or {}).get("layers", [])
                     if isinstance(l, dict) and l.get("id") == layer_id),
                    None,
                )
                if not layer:
                    continue
                source = (spec or {}).get("sources", {}).get(layer.get("source", ""), {})
                src_profile = source.get("profile")
                if isinstance(src_profile, dict):
                    profile = src_profile
                    break

        plan = planner.finalize_with_profile(plan, profile)

        # 角色绑定：已存在图层按类型确定性映射
        type_role_map = {
            "heatmap": ("primary", "visual_heatmap"),
            "circle": ("secondary", "point_overlay"),
            "fill": ("reference", "administrative_choropleth"),
            "raster": ("primary", "raster_surface"),
        }
        bound_layers: List[Dict[str, Any]] = []
        spec = await mapspec_store.get_mapspec(session_id) or {}
        for layer_id in layer_ids:
            layer = next(
                (l for l in spec.get("layers", [])
                 if isinstance(l, dict) and l.get("id") == layer_id),
                None,
            )
            if layer is None:
                continue
            role, carto = type_role_map.get(layer.get("type", ""), ("secondary", "point_overlay"))
            bound_layers.append({"layer_id": layer_id, "role": role, "cartography": carto})

        # 缺失图层补齐：热力层被禁时不补；点叠加缺 → 从 primary_ref 授权
        out: Dict[str, Any] = {"success": True, "plan_id": plan.plan_id}
        committed_layer_ids: List[str] = [b["layer_id"] for b in bound_layers]

        primary_layer = next(
            (l for l in plan.map_layers if l.role == "primary" and l.enabled), None
        )
        need_heatmap = (
            primary_layer is not None
            and primary_layer.layer_type == "heatmap"
            and not any(b["cartography"] == "visual_heatmap" for b in bound_layers)
        )
        need_points = not any(b["cartography"] == "point_overlay" for b in bound_layers)

        if (need_heatmap or need_points) and primary_data is not None:
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
                converted, _, _warn = convert_analysis_to_mapspec_layer(analysis_payload)
                slug = _hashlib.sha256(f"{plan.plan_id}:heatmap".encode()).hexdigest()[:8]
                converted["id"] = f"product-{slug}-heatmap"
                src_ref = {
                    "type": "geojson", "ref_id": primary_ref,
                    "profile": profile,
                }
                res = await mapspec_store.layer_upsert(session_id, converted, src_ref)
                if res.get("success"):
                    committed_layer_ids.append(converted["id"])
                    bound_layers.append({
                        "layer_id": converted["id"], "role": "primary",
                        "cartography": "visual_heatmap", "authored": True,
                    })
                    out = _forward_evidence(res, out)
                else:
                    out.setdefault("warnings", []).append(
                        f"heatmap layer authoring failed: {res.get('message')}")
            if need_points:
                analysis_payload = {
                    "geojson": primary_data,
                    "profile": profile,
                    "algorithm": "webgis_map_product",
                }
                converted, _, _warn = convert_analysis_to_mapspec_layer(analysis_payload)
                slug = _hashlib.sha256(f"{plan.plan_id}:points".encode()).hexdigest()[:8]
                converted["id"] = f"product-{slug}-points"
                res = await mapspec_store.layer_upsert(
                    session_id, converted,
                    {"type": "geojson", "ref_id": primary_ref, "profile": profile},
                )
                if res.get("success"):
                    committed_layer_ids.append(converted["id"])
                    bound_layers.append({
                        "layer_id": converted["id"], "role": "secondary",
                        "cartography": "point_overlay", "authored": True,
                    })
                else:
                    out.setdefault("warnings", []).append(
                        f"point layer authoring failed: {res.get('message')}")

        # 组件 + 标题落 MapSpec layout.components
        components = list(plan.components)
        if title:
            from app.services.gis_harness.components import title_component
            components = [c for c in components if c.type != "title"]
            components.append(title_component(title))
        component_dicts = [c.to_mapspec() for c in components]
        layout_res = await mapspec_store.layout_set(
            session_id, components=component_dicts,
        )
        if not layout_res.get("success"):
            out.setdefault("warnings", []).append(
                f"layout components commit failed: {layout_res.get('message')}")

        # 绑定记录回填 plan（供 evidence 消费）；数据/画像步骤随绑定完成
        for entry in bound_layers:
            for planned in plan.map_layers:
                if planned.cartography == entry["cartography"] and not planned.bound_ref:
                    planned.layer_id = entry["layer_id"]
                    planned.bound_ref = primary_ref or ""
                    break
        if primary_data is not None:
            done_caps = {"poi_query", "point_profile", "raster_source"}
            for req in plan.data_requirements:
                if req.capability in done_caps:
                    req.status = "available"
                    req.bound_ref = primary_ref or ""
            for step in plan.analysis_steps:
                if step.capability in done_caps:
                    step.status = "done"
                    step.bound_ref = primary_ref or ""
        plan.completeness = planner.assess_completeness(plan)

        out.update({
            "recipe_id": plan.recipe_id,
            "template_id": plan.template_id,
            "status": plan.status,
            "layers": bound_layers,
            "components": component_dicts,
            "fallbacks": [fb if isinstance(fb, dict) else fb.model_dump() for fb in plan.fallbacks],
            "eligibility": plan.eligibility,
            "completeness": plan.completeness,
            "intent": {"task": intent.task, "scope": intent.scope.name,
                       "subject": intent.subject.category},
            "map_product_evidence": {
                "intent_resolution": {
                    "query": intent.query, "task": intent.task,
                    "matched_rules": intent.matched_rules,
                    "confidence": intent.confidence,
                    "hint_applied": intent.hint_applied,
                },
                "recipe_selection": {
                    "selected": plan.recipe_id,
                    "candidates": [r for r in planner.recipes.all_ids
                                   if r == plan.recipe_id],
                },
                "recipe_eligibility": plan.eligibility,
                "fallback_decisions": out.get("fallbacks", []),
                "component_selection": [c["type"] for c in component_dicts],
                "map_product_completeness": plan.completeness,
                "bound_layers": bound_layers,
            },
            "summary": (
                f"产品组装完成 recipe={plan.recipe_id}；图层 {len(bound_layers)}，"
                f"组件 {len(component_dicts)}，fallback {len(plan.fallbacks)} 次"
            ),
        })
        final_spec = layout_res.get("mapspec") if isinstance(layout_res.get("mapspec"), dict) else None
        if final_spec is not None:
            out["mapspec"] = final_spec
            out = _forward_evidence(layout_res, out)
        return out

    @tool(
        registry,
        tier=2, domains=["report"], name="webgis_component_update",
        description=(
            "制图组件局部突变：只改命中的单个组件（id 或类型），其余组件与所有"
            "数据图层完全不动——不触发任何数据重查/重分析。"
            "\n适用：『换一个指南针』(component_type=north_arrow, options.variant="
            "compass_rose)、『不要指南针』(enabled=false)、『比例尺放左下角』"
            "(position=bottom-left)、『色条改成竖向』(component_type="
            "continuous_colorbar, options.orientation=vertical)、『换成 Viridis 色带』、"
            "『图例放左下』『标题改成…』(component_type=title, options.text=…)。"
        ),
        args_model=ComponentUpdateArgs,
    )
    async def webgis_component_update(
        session_id: Optional[str] = None,
        component_id: Optional[str] = None,
        component_type: Optional[str] = None,
        enabled: Optional[bool] = None,
        position: Optional[str] = None,
        style: Optional[Dict[str, Any]] = None,
        options: Optional[Dict[str, Any]] = None,
    ) -> dict:
        from app.services.gis_harness.components import (
            CartographyComponent,
            mutate_component,
        )
        from app.services.mapspec_store import mapspec_store

        if not session_id:
            return {"success": False, "message": "Missing session_id"}
        if not component_id and not component_type:
            return {
                "success": False,
                "message": "component_id 或 component_type 必须提供其一",
                "correction_hint": "如 component_type='north_arrow' 或 component_id='north-arrow'。",
            }
        if not any(v is not None for v in (enabled, position, style, options)):
            return {
                "success": False,
                "message": "至少提供一个突变字段 (enabled/position/style/options)",
            }

        spec = await mapspec_store.get_mapspec(session_id) or {}
        raw_components = ((spec.get("layout") or {}).get("components")) or []
        components = [
            CartographyComponent.model_validate({**c})
            for c in raw_components if isinstance(c, dict)
        ]
        if not components:
            return {
                "success": False,
                "message": "当前 MapSpec 无 layout.components 可突变",
                "correction_hint": "先用 webgis_map_product 或 webgis_layout_set 建立组件集。",
            }

        mutated, change = mutate_component(
            components,
            component_id=component_id,
            component_type=component_type,
            enabled=enabled,
            position=position,
            style=style,
            options=options,
        )
        if change is None:
            return {
                "success": False,
                "message": f"未找到组件 component_id={component_id} component_type={component_type}",
                "correction_hint": "当前组件: " + ", ".join(f"{c.id}({c.type})" for c in components),
            }

        res = await mapspec_store.layout_set(
            session_id, components=[c.to_mapspec() for c in mutated],
        )
        out: Dict[str, Any] = {
            "success": bool(res.get("success")),
            "change": change,
            "components": [c.to_mapspec() for c in mutated],
            "component_mutation_evidence": {
                "change": change,
                "layer_count_unchanged": True,
                "layers_before": len(spec.get("layers", [])),
                "layers_after": len((res.get("mapspec") or {}).get("layers", [])),
            },
        }
        if res.get("success"):
            out["summary"] = (
                f"组件 {change.get('id')} 已更新（{len(mutated)} 组件，数据层未动）"
            )
            out["mapspec"] = res.get("mapspec")
        return _forward_evidence(res, out)
