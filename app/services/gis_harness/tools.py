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
        None, ge=4, le=80, description="视觉热力半径（像素）。显式像素语义")
    recipe_id: Optional[str] = Field(
        None, description="沿用 webgis_map_intent 推荐的 recipe id（计划连续性："
                          "两阶段共用同一份计划，避免 hint 纠偏被丢失）")
    task_hint: Optional[str] = Field(
        None, description="[可选] 与 webgis_map_intent 相同的任务 hint（重放合并）")


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


async def _project_verified_recipes() -> set:
    """当前 turn 的项目已验证 recipe id 集合（无项目上下文 → 空集）。

    读账本是一次有界索引查询（recipe_outcome 事实通常个位数），且仅当
    RuntimeContext 携带 project_id 时发生——匿名/无项目会话零开销。
    账本不可用时返回空集（记忆是增值信号，绝不阻断推荐）。
    """
    from app.lib.runtime.context import current_runtime_context

    ctx = current_runtime_context()
    project_id = getattr(ctx, "project_id", None)
    if not project_id:
        return set()

    def _read() -> set:
        from app.core.database import SessionLocal
        from app.services.cartography.project_memory import get_verified_recipe_ids

        with SessionLocal() as db:
            return get_verified_recipe_ids(db, project_id)

    try:
        import asyncio as _asyncio

        return await _asyncio.to_thread(_read)
    except Exception:  # noqa: BLE001 — 记忆缺失退化为无加成
        return set()


def register_gis_harness_tools(registry: ToolRegistry):
    """注册 GIS Harness 工具（tier 1：意图解析廉价且恒可用）。"""

    @tool(
        registry,
        # #993: tier 1 — the docstring below has always promised "意图解析廉价
        # 且恒可用"，and SYSTEM_PROMPT's 意图先行 contract requires it visible for
        # keyword-less requests like『看看成都的大学』(no statistics/report/
        # network/temporal keywords → tier-2 domain gating hid it). The declared
        # domains stay for the H-8 task-family coverage contract (proximity→
        # network / change_detection→temporal); with tier 1 they no longer gate
        # visibility. Keep webgis_map_product tier=2: it runs only after data
        # tools returned.
        tier=1, domains=["statistics", "report", "network", "temporal"], name="webgis_map_intent",
        # #996: audit4 #979 给 result 形状加了 guidance 键（有界 capability→tool
        # 裁决投影）——RESULT 契约变更，contract_version 1→2（指纹 1.0#cv2）。
        contract_version=2,
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
            # #785: 主体提示先经栅格/面/线 token 表定类型（如 NDVI → raster），
            # 不再无条件 poi —— 否则栅格数据的 evidence 声称 POI 主体、
            # geometry_expectation 停留在旧值。token 表无命中才默认 poi。
            from app.services.gis_harness.intent import (
                SubjectIntent,
                _entity_geometry,
                _match_subject,
            )
            typed = _match_subject(subject_hint)
            if typed.type != "unknown":
                intent.subject = typed
            else:
                intent.subject = SubjectIntent(type="poi", category=subject_hint)
            intent.entity_type = intent.subject.type
            intent.geometry_expectation = _entity_geometry(
                intent.subject, intent.task)
            intent.hint_applied.append(f"subject->{subject_hint}")

        planner = MapProductPlanner()
        # ADR-0069 / spec 开放问题 3：推荐排序带项目记忆——本项目验证过的
        # recipe 前置。project_id 来自 turn 级 RuntimeContext（HTTP 入口
        # 绑定），无项目上下文时 verified 为空集，排序与既有行为一致。
        candidates = planner.recipes.select_candidates(
            intent, project_verified=await _project_verified_recipes()
        )
        try:
            available = set(registry.list_tools())
        except Exception:  # noqa: BLE001 - 能力解析是建议性信息
            available = set()
        # audit #825: 把注册表可见工具传给 planner —— 解析不到的能力在 plan
        # 里标记 unavailable（docstring 承诺的诚实报告）。
        plan = planner.plan_from_intent(intent, available_tools=available or None)

        capabilities = []
        # resolved_algorithm 与 plan 裁决证据同源；resolved_tool 保持与
        # 真实注册表视图（available，含空视图）一致。
        selection_by_cap = {
            r.capability: r for r in plan.algorithm_selections
        }
        for req in plan.data_requirements:
            record = selection_by_cap.get(req.capability)
            capabilities.append({
                "capability": req.capability,
                "purpose": req.purpose,
                "resolved_tool": resolve_tool_for_capability(req.capability, available),
                "resolved_algorithm": (
                    record.algorithm if record and record.status == "resolved"
                    else ""
                ),
            })

        # audit4 #979: 有界 guidance 投影 —— slim_tool_result 的 summary 分支
        # 此前把 intent/candidates/plan 整包丢弃，capability→tool 裁决从未
        # 到达 LLM（harness 只有建议权的具体机制）。guidance 在
        # _PRESERVED_META_KEYS 白名单内，每行一条裁决，总量闸兜底。
        guidance = [
            f"{c['capability']} → {c['resolved_tool'] or '无对应工具(需自选)'}"
            f"（{c['purpose']}）"
            for c in capabilities[:10]
        ]
        if len(capabilities) > 10:
            guidance.append(f"（另有 {len(capabilities) - 10} 项数据需求，见 map_product 阶段）")
        missing = [c["capability"] for c in capabilities if not c["resolved_tool"]]
        if missing:
            guidance.append(
                "⚠ 未解析能力: " + ", ".join(missing[:5]) + " —— 计划内无注册工具，需换路径"
            )

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
            "guidance": guidance,
            "summary": (
                f"意图:{intent.task} 范围:{intent.scope.name or '未识别'} "
                f"主体:{intent.subject.category or '未识别'} → 推荐 recipe:{plan.recipe_id}"
            ),
        }

    @tool(
        registry,
        tier=2, domains=["statistics", "report", "network", "temporal"], name="webgis_map_product",
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
        recipe_id: Optional[str] = None,
        task_hint: Optional[str] = None,
    ) -> dict:
        from app.services.gis_harness.intent import (
            merge_intent_hints,
            resolve_map_request_intent,
        )
        from app.services.gis_harness.planner import MapProductPlanner
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
        planner = MapProductPlanner()
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
        _verified = await _project_verified_recipes()
        _candidates = planner.recipes.select_candidates(
            intent, project_verified=_verified
        )
        _selected_recipe = recipe_id or (_candidates[0].id if _candidates else "")
        plan = planner.plan_from_intent(
            intent,
            template_id=template_id or "",
            recipe_id=_selected_recipe,
            available_tools=available or None,
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

        bound_layers: List[Dict[str, Any]] = []
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
                converted, _, _warn = convert_analysis_to_mapspec_layer(analysis_payload)
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
                converted, _, _warn = convert_analysis_to_mapspec_layer(analysis_payload)
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
        for comp in components:
            if getattr(comp, "type", "") == "continuous_colorbar" and primary_bound:
                opts = dict(getattr(comp, "options", {}) or {})
                opts.setdefault("layerId", primary_bound["layer_id"])
                if opts.get("layerId") == "":
                    opts["layerId"] = primary_bound["layer_id"]
                if need_heatmap or any(
                    b.get("layer_type") == "heatmap"
                    or b.get("cartography") == "visual_heatmap"
                    for b in bound_layers
                ):
                    opts["palette"] = palette
                comp.options = opts
        if title:
            from app.services.gis_harness.components import title_component
            components = [c for c in components if c.type != "title"]
            components.append(title_component(title))
        component_dicts = [c.to_mapspec() for c in components]
        layout_res = await mapspec_store.layout_set(
            session_id, components=component_dicts,
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
        _artifact_lineage: List[Dict[str, Any]] = []
        done_caps: set = set()
        if primary_data is not None:
            done_caps |= {"poi_query", "point_profile", "raster_source"}
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
            first_fb = plan.fallbacks[0]
            fb_msg = _item_field(first_fb, "reason") or _item_field(first_fb, "message") or first_fb
            product_guidance.append(f"⚠ fallback {len(plan.fallbacks)} 次（如: {str(fb_msg)[:80]}）")
        if authoring_failures:
            product_guidance.append(
                f"⚠ {len(authoring_failures)} 项图层/组件提交失败 —— 产品不完整，需补数据或重试"
            )
        out.update({
            "recipe_id": plan.recipe_id,
            "template_id": plan.template_id,
            "status": plan.status,
            "layers": bound_layers,
            "components": component_dicts,
            "fallbacks": [fb if isinstance(fb, dict) else fb.model_dump() for fb in plan.fallbacks],
            "eligibility": plan.eligibility,
            "completeness": plan.completeness,
            "guidance": product_guidance[:10],
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
                "fallback_decisions": out.get("fallbacks", []),
                # ── registry 编排证据（§27，有界转录）────────────────────
                "capability_resolution": [
                    {"capability": r.capability, "status": r.status,
                     "resolved_tool": r.resolved_tool,
                     "resolved_algorithm": r.resolved_algorithm}
                    for r in plan.data_requirements
                ],
                "algorithm_selection": [
                    r.model_dump() for r in plan.algorithm_selections
                ],
                "map_model_selection": plan.map_model_selection,
                "template_selection": plan.template_selection,
                "artifact_lineage": _artifact_lineage,
                "component_selection": [c["type"] for c in component_dicts],
                "map_product_completeness": plan.completeness,
                "bound_layers": bound_layers,
            },
            "summary": (
                f"产品组装完成 recipe={plan.recipe_id}；图层 {len(bound_layers)}，"
                f"组件 {len(component_dicts)}，fallback {len(plan.fallbacks)} 次"
                + (f"；⚠ {len(authoring_failures)} 项图层/组件提交失败（见 warnings）"
                   if authoring_failures else "")
            ),
        })
        if authoring_failures and not bound_layers:
            # #716: nothing was actually mounted — do not let the caller
            # (or the plan tick) treat this as a complete product.
            out["cartographic_authoring_failed"] = True
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
