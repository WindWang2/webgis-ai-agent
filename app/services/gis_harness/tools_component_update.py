"""Component-update schema and registration, separate from map-product planning."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from app.services.gis_harness.tools_component import (
    _component_lifecycle_action,
    _forward_evidence,
)
from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)


class ComponentUpdateArgs(BaseModel):
    session_id: Optional[str] = Field(None, description="会话 ID")
    component_id: Optional[str] = Field(
        None, description="组件 id（如 north-arrow）。与 component_type 二选一")
    component_type: Optional[str] = Field(
        None, description="组件类型（如 north_arrow）——按类型命中第一个；"
                          "create=true 时为新组件类型")
    enabled: Optional[bool] = Field(None, description="启用/禁用（『不要指南针』= enabled:false）")
    position: Optional[str] = Field(
        None, description="位置槽 top-left/top-center/top-right/bottom-left/bottom-center/bottom-right")
    placement: Optional[Dict[str, Any]] = Field(
        None, description=(
            "typed 布局（position 的超集）：{mode:'anchor',anchor:'bottom-left'} "
            "或 {mode:'floating',x,y,width?,height?,zIndex?,collapsed?}。"
            "『移到右下角、缩小一些』→ floating + 坐标尺寸"))
    variant: Optional[str] = Field(
        None, description="variant（如 north_arrow: compass_rose/compass_needle/"
                          "arrow_simple；scale_bar: boxed/academic）。可用值见 "
                          "webgis_component_catalog")
    style: Optional[Dict[str, Any]] = Field(None, description="样式合并（半透明字典）")
    options: Optional[Dict[str, Any]] = Field(
        None, description="选项合并（如 {'orientation':'vertical'} / 统计面板 stats）")
    chart: Optional[Dict[str, Any]] = Field(
        None, description="chart_panel 数据（ChartData：{type:bar|line|pie|scatter,"
                          "title,data:[{name,value|x,y}]}，与 generate_chart 同契约，"
                          "≤500 点）。create=true 时用于新建图表面板")
    stats: Optional[Dict[str, Any]] = Field(
        None, description="statistics_panel 数据（{title?,items:[{label,value,unit?}]}，"
                          "≤24 条）")
    create: bool = Field(False, description="目标不存在时创建（需 component_type；"
                                            "面板加新图表/统计卡用同一入口）")
    action: Optional[str] = Field(
        None, description=(
            "生命周期动作（Runtime V4，缺省 patch=局部突变）："
            "'remove'=真删除组件（≠ enabled:false 隐藏；多实例如图表/表格/"
            "图例/注记可删）；'duplicate'=复制多实例组件（新 id + 浮动偏移，"
            "可用 new_component_id 指定新 id）；'rebind'=重绑定引用字段"
            "（需 rebind_chart_ref / rebind_table_ref / rebind_layer_id 其一）"))
    new_component_id: Optional[str] = Field(
        None, max_length=128,
        description="action=duplicate 时的新组件 id（缺省自动 -copy 后缀）")
    # Pi 兼容审查（BLOCKING）：rebind_* 携带的是 ref 游标/图层 id 本身 ——
    # 若不加 ref_cursor 声明，registry 的透明解引用会把 "ref:chart-xxx"
    # 整个替换成存储载荷，pydantic 校验随即将 dict 拒为 str（action=rebind
    # 按文档用法即必然失败，Pi 与 legacy 两路同罪）。
    rebind_chart_ref: Optional[str] = Field(
        None, max_length=200, json_schema_extra={"ref_cursor": True},
        description="action=rebind：chart_panel 新 chartRef（ref:chart-*）")
    rebind_table_ref: Optional[str] = Field(
        None, max_length=200, json_schema_extra={"ref_cursor": True},
        description="action=rebind：table_panel 新 tableRef（表 artifact ref）")
    rebind_layer_id: Optional[str] = Field(
        None, max_length=200, json_schema_extra={"ref_cursor": True},
        description="action=rebind：新绑定图层 id（图例/色条/表格换数据面）")
    expected_revision: Optional[int] = Field(
        None, ge=0, description="乐观并发：webgis_component_catalog 读到的 "
                                "mutation_revision；落后即 superseded（用户最新交互优先）")



def register_component_update_tool(registry: ToolRegistry) -> None:
    @tool(
        registry,
        tier=2, domains=["report"], name="webgis_component_update",
        capabilities=['thematic_cartography'],
        description=(
            "制图组件局部突变：只改命中的单个组件（id 或类型），其余组件与所有"
            "数据图层完全不动——不触发任何数据重查/重分析。"
            "\n适用：『换一个指南针』(component_type=north_arrow, variant="
            "compass_rose)、『不要指南针』(enabled=false)、『比例尺放左下角』"
            "(position=bottom-left)、『色条改成竖向』(component_type="
            "continuous_colorbar, options.orientation=vertical)、『图例放左下』"
            "『标题改成…』(component_type=title, options.text=…)、"
            "『把统计图移到右下角、缩小一些』(placement={mode:'floating',"
            "x:…,y:…,width:…,height:…})。"
            "\n创建：create=true + component_type=chart_panel + chart=ChartData"
            "（bar/line/pie/scatter，与 generate_chart 同契约）可在地图上加"
            "浮动统计图；component_type=statistics_panel + stats 建统计卡；"
            "component_type=table_panel + options={tableRef|layerId} 建交互"
            "表格（虚拟化、可排序、与地图/图表跨视图选择联动）。"
            "\n生命周期（Runtime V4）：action='remove' 真删除组件（≠隐藏）；"
            "action='duplicate' 复制多实例组件（图表/表格/图例/注记）；"
            "action='rebind' 换绑定（rebind_chart_ref/rebind_table_ref/"
            "rebind_layer_id 其一，目标存在性事务内校验）。"
            "\n多实例（legend/categorical_legend/continuous_colorbar/"
            "chart_panel/annotation）：第二个实例必须 create=true + 显式 "
            "component_id（如 chart-panel-2），并先在 catalog 的 components "
            "里确认 id 未占用——禁止依赖「第一个同型」这种隐式寻址。"
            "\n注记（annotation）：variant=text 静态卡 {text}；variant=callout "
            "{text, anchor:[lng,lat]} 地理锚定+引线；variant=group {items:["
            "{text, anchor?}] ≤12 条} 一组相关注记（Top N / 震中列表）。"
            "\n插图（inset_map）：options 必填 bbox=[w,s,e,n]，可选 mainBbox"
            "（主图范围指示）/boundary（≤512 点轮廓折线）/label。"
            "\n并发：先 webgis_component_catalog 读 mutation_revision，"
            "传 expected_revision 防止覆盖用户拖拽后的最新位置。"
        ),
        args_model=ComponentUpdateArgs,
        side_effect="state_mutation",
        deterministic=False,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=("组件", "指北针", "比例尺", "图例", "色条", "标题", "统计卡", "注记", "component"),
        output_semantic_type="text",
        result_size_policy="inline_small",
        required_context=("map_state", "cartography_state"),
        map_mutations=("component",),
        data_mutations=("session_state",),
        failure_modes=("invalid_args",),
        summary=(
            "制图组件局部突变：只改命中的单个组件（指北针/比例尺/图例/色条/标题/"
            "统计图/统计卡/表格/注记/插图），其余组件与所有数据图层完全不动，不触"
            "发数据重查。支持 create/remove/duplicate/rebind 与乐观并发（先读 "
            "webgis_component_catalog 的 mutation_revision 再传 expected_revision）。"
        ),
        examples=(
            "把比例尺放到左下角",
            "标题改成『2026年成都高校分布图』，图例放右下",
        ),
        anti_examples=(
            "删掉这个统计图层——那是数据图层删除，用 webgis_layer_remove",
            "重新查一遍统计数据——本工具只突变组件，不重查数据",
        ),
    )
    async def webgis_component_update(
        session_id: Optional[str] = None,
        component_id: Optional[str] = None,
        component_type: Optional[str] = None,
        enabled: Optional[bool] = None,
        position: Optional[str] = None,
        placement: Optional[Dict[str, Any]] = None,
        variant: Optional[str] = None,
        style: Optional[Dict[str, Any]] = None,
        options: Optional[Dict[str, Any]] = None,
        chart: Optional[Dict[str, Any]] = None,
        stats: Optional[Dict[str, Any]] = None,
        create: bool = False,
        action: Optional[str] = None,
        new_component_id: Optional[str] = None,
        rebind_chart_ref: Optional[str] = None,
        rebind_table_ref: Optional[str] = None,
        rebind_layer_id: Optional[str] = None,
        expected_revision: Optional[int] = None,
    ) -> dict:
        from app.services.gis_harness.components import (
            validate_chart_payload,
            validate_stats_payload,
        )
        from app.services.mapspec_store import mapspec_store

        if not session_id:
            return {"success": False, "message": "Missing session_id"}
        # Component Lifecycle V3（Runtime V4 §17）：生命周期动作与 patch 同一
        # 工具入口（不开 per-action 工具），但走专用意图 + 事务内验证。
        if action and action != "patch":
            return await _component_lifecycle_action(
                session_id=session_id,
                component_id=component_id,
                component_type=component_type,
                action=action,
                new_component_id=new_component_id,
                rebind_chart_ref=rebind_chart_ref,
                rebind_table_ref=rebind_table_ref,
                rebind_layer_id=rebind_layer_id,
                expected_revision=expected_revision,
            )
        if not component_id and not component_type:
            return {
                "success": False,
                "message": "component_id 或 component_type 必须提供其一",
                "correction_hint": "如 component_type='north_arrow' 或 component_id='north-arrow'。",
            }
        if not any(
            v is not None
            for v in (enabled, position, placement, variant, style, options, chart, stats)
        ):
            return {
                "success": False,
                "message": "至少提供一个突变字段 (enabled/position/placement/variant/style/options/chart/stats)",
            }

        # #1068(E-3): spec 只读一次 —— 此前 component_id-only 分支读一次、
        # 主路径再无条件读一次（每 single-component 编辑 2 次全量物化）。
        _spec_early = None
        effective_type = component_type
        if component_id and not effective_type:
            _spec_early = await mapspec_store.get_mapspec(session_id) or {}
            for c in (_spec_early.get("layout") or {}).get("components") or []:
                if isinstance(c, dict) and c.get("id") == component_id:
                    effective_type = str(c.get("type") or "")
                    break
        if chart is not None:
            if effective_type and effective_type != "chart_panel" and not create:
                return {
                    "success": False,
                    "message": "chart 参数只适用于 chart_panel 组件",
                    "correction_hint": "create=true + component_type='chart_panel'，或更新既有的 chart_panel。",
                }
            err = validate_chart_payload(chart)
            if err:
                return {"success": False, "message": f"chart 数据不合法: {err}"}
        if stats is not None:
            if effective_type and effective_type != "statistics_panel" and not create:
                return {
                    "success": False,
                    "message": "stats 参数只适用于 statistics_panel 组件",
                }
            err = validate_stats_payload(stats)
            if err:
                return {"success": False, "message": f"stats 数据不合法: {err}"}

        # chart 数据通道：小载荷 inline（options.chart），大载荷存 session
        # artifact ref（options.chartRef）——MapSpec 不背大数据（D2）。
        chart_options: Dict[str, Any] = {}
        _pending_old_chart_ref: Optional[str] = None
        if chart is not None:
            from app.lib.json_size import estimate_json_bytes
            if estimate_json_bytes(chart) > 32 * 1024:
                from app.services.session_data import session_data_manager
                ref_id = await session_data_manager.store(
                    session_id, {"chart": chart}, prefix="chart",
                )
                chart_options["chartRef"] = ref_id
                # P1（ADR-0082）：图表产物登记（chart_spec；GC 由 registry 的
                # 活引用集合兜住 —— chartRef 在 MapSpec 组件 options 上）。
                try:
                    from app.services.artifact_registry import register_tool_artifact

                    await register_tool_artifact(
                        session_id, ref_id, tool="mutate_component"
                    )
                except Exception as _artifact_reg_exc:  # noqa: BLE001 — 登账是增值披露
                    logger.debug("[component_update] artifact registry skipped: %s", _artifact_reg_exc)
                # #1068(E-10): 被替换的旧 chartRef 从 options 排除但从不
                # delete_ref —— 孤儿占 200 条/50MB LRU 预算，压力下可逐出
                # 仍被 MapSpec source 引用的活 ref。记录待提交成功后删除
                # （提前删会让失败路径的旧 ref 变悬空）。
                _pending_old_chart_ref = None
                _pre_spec = _spec_early if _spec_early is not None else (
                    await mapspec_store.get_mapspec(session_id) or {}
                )
                for c in (_pre_spec.get("layout") or {}).get("components") or []:
                    if not isinstance(c, dict) or not isinstance(c.get("options"), dict):
                        continue
                    matched = (
                        (component_id and c.get("id") == component_id)
                        or (not component_id and effective_type and c.get("type") == effective_type)
                    )
                    if matched and isinstance(c["options"].get("chartRef"), str):
                        _pending_old_chart_ref = c["options"]["chartRef"]
                        break
                if _pending_old_chart_ref == ref_id:
                    _pending_old_chart_ref = None
            else:
                chart_options["chart"] = chart
                # Runtime V4（§15）：chart.selectionField（字段映射路径自动
                # 推导）透传进面板 options —— agent 经 component_update 建
                # 面板与 generate_chart(attach_to_map) 同协议。
                derived_selection_field = str(chart.get("selectionField") or "").strip()
                if derived_selection_field:
                    chart_options["selectionField"] = derived_selection_field
        if stats is not None:
            chart_options["stats"] = stats
        merged_options = {**(options or {}), **chart_options} or None

        spec = (
            _spec_early
            if _spec_early is not None
            else await mapspec_store.get_mapspec(session_id) or {}
        )
        raw_components = ((spec.get("layout") or {}).get("components")) or []

        # change 记录经同一纯函数预计算（引擎事务内再算一次，确定性一致）——
        # 保持既有 evidence 契约（change.from/to）。
        from app.services.gis_harness.components import (
            CartographyComponent,
            mutate_component,
        )
        parsed = [
            CartographyComponent.model_validate(dict(c))
            for c in raw_components if isinstance(c, dict)
        ]
        # v2 注记框架 / 插图：payload 有界校验在「合并既有 options 之后」
        # 执行 —— callout 可先设 text 再补 anchor（分步突变不被卡）；非法
        # 合并结果不进 MapSpec。
        if merged_options and effective_type in ("annotation", "inset_map", "table_panel"):
            existing_opts: Dict[str, Any] = {}
            for c in parsed:
                cid = component_id or ""
                if (cid and c.id == cid) or (not cid and c.type == effective_type):
                    existing_opts = dict(c.options or {})
                    break
            preview: Dict[str, Any] = {**existing_opts}
            for k, v in merged_options.items():
                if isinstance(v, dict) and isinstance(preview.get(k), dict):
                    preview[k] = {**preview[k], **v}
                else:
                    preview[k] = v
            if effective_type == "annotation":
                from app.services.gis_harness.components import validate_annotation_payload

                err = validate_annotation_payload(preview)
                if err:
                    return {
                        "success": False,
                        "message": f"annotation options 不合法: {err}",
                        "correction_hint": (
                            "text 形态 {text}; callout {text, anchor:[lng,lat]}; "
                            "group {items:[{text, anchor?}] ≤12}"
                        ),
                    }
            elif effective_type == "table_panel":
                # Runtime V4：绑定面校验（合并后）—— 分步突变不被卡
                #（先建面板再补绑定合法）；无绑定键的纯样式合并跳过。
                from app.services.gis_harness.components import validate_table_binding

                if any(k in preview for k in ("tableRef", "layerId")):
                    err = validate_table_binding(preview)
                    if err:
                        return {
                            "success": False,
                            "message": f"table_panel 绑定不合法: {err}",
                            "correction_hint": (
                                "options.tableRef（ref 表 artifact）或 options.layerId"
                                "（图层属性表）二选一；columns ≤32 列可选。"
                            ),
                        }
            else:
                from app.services.gis_harness.components import validate_inset_payload

                err = validate_inset_payload(preview)
                if err:
                    return {
                        "success": False,
                        "message": f"inset_map options 不合法: {err}",
                        "correction_hint": (
                            "必填 bbox=[w,s,e,n]；可选 mainBbox / boundary(≤512 点) / label"
                        ),
                    }
        _, change = mutate_component(
            parsed,
            component_id=component_id,
            component_type=component_type,
            enabled=enabled,
            position=position,
            placement=placement,
            variant=variant,
            style=style,
            options=merged_options,
            upsert=create,
        )
        if change is None:
            current = ", ".join(
                f"{c.get('id')}({c.get('type')})" for c in raw_components if isinstance(c, dict)
            ) or "（无组件）"
            return {
                "success": False,
                "message": f"未找到组件 component_id={component_id} component_type={component_type}",
                "correction_hint": (
                    f"当前组件: {current}；新组件用 create=true + component_type。"
                ),
            }

        res = await mapspec_store.patch_component(
            session_id,
            # 未命中组件时 change 为 None 已提前返回；这里用预计算解析出的
            # 目标 id（component_id 缺省按类型命中时二者等价、确定性一致）。
            component_id=str(component_id or change.get("id") or ""),
            component_type=component_type,
            enabled=enabled,
            position=position,
            placement=placement,
            variant=variant,
            style=style,
            options=merged_options,
            upsert=create,
            expected_revision=expected_revision,
        )

        if res.get("status") == "superseded":
            return {
                "success": False,
                "status": "superseded",
                "superseded": True,
                "mutation_revision": res.get("mutation_revision"),
                "message": "组件状态已被更新的交互修改（用户拖拽/其他突变优先），请重新读取 catalog 后再试",
                "correction_hint": "调用 webgis_component_catalog 获取最新 mutation_revision 与组件状态。",
            }
        if not res.get("success"):
            return {
                "success": False,
                "message": res.get("message") or "组件突变失败",
                "correction_hint": res.get("correction_hint"),
            }

        # #1068(E-10): 变更已提交，旧 chartRef 不再被 spec 引用 —— 现在删除
        # 才安全（失败路径旧 ref 仍被引用）。
        if _pending_old_chart_ref:
            try:
                await session_data_manager.delete_ref(session_id, _pending_old_chart_ref)
            except Exception as _old_ref_exc:  # noqa: BLE001 - 新 ref 已提交，旧 ref 清理 best-effort
                logger.debug("[component_update] stale chartRef cleanup skipped: %s", _old_ref_exc)

        final_components = (((res.get("mapspec") or {}).get("layout") or {}).get("components")) or []
        target_id = (change.get("id") if isinstance(change, dict) else None) or component_id
        target_component = next(
            (c for c in final_components if c.get("id") == target_id),
            next((c for c in final_components if c.get("type") == component_type), None) if not target_id else None,
        )
        out: Dict[str, Any] = {
            "success": True,
            "change": change,
            "component": target_component,
            "components": final_components,
            # review P1-1：legacy SSE 路径读 result.mapspec 提交文档
            # （use-sse-stream:505），丢失该键 → agent 组件编辑在下次事件前
            # 不重渲。Pi details 白名单从未携带它，不受影响。
            "mapspec": res.get("mapspec"),
            "component_mutation_evidence": {
                "change": change,
                "layer_count_unchanged": len(spec.get("layers", []))
                == len((res.get("mapspec") or {}).get("layers", [])),
                "layers_before": len(spec.get("layers", [])),
                "layers_after": len((res.get("mapspec") or {}).get("layers", [])),
            },
            "summary": (
                f"组件 {change.get('id')} 已更新（{len(final_components)} 组件，数据层未动）"
            ),
        }
        if chart_options.get("chartRef"):
            out["chart_ref"] = chart_options["chartRef"]
        return _forward_evidence(res, out)
