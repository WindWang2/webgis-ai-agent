"""G46 联合探索工具（WP10）——inspect / preview / view 管理 / apply。

与 UI 面板共用 ``linked_exploration.service``（同一冻结、同一求值、同一
持久视图 CAS）。``session_id`` 由 ToolRegistry 从已授权上下文注入，模型
不能指定他人会话。回执**有界**：条件解释、匹配数与完整性、每图 facet
口径摘要、MapLibre 表达式摘要（不回传全量桶行——行数上限内给 top 桶），
绝不回传全量 feature 或无界桶列表。

side_effect 按实际行为声明：inspect/preview/open 是 ``cacheable_read``（含
真实读与冻结）；save/apply 是 ``state_mutation``（DB 写 / MapSpec 组件写）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)

_MAX_FIELDS = 40
_MAX_ROWS = 24          # 回执内类别桶/直方桶上限（完整统计走 UI preview）
_MAX_CHARTS = 8
_MAX_VIEWS = 20


class InspectArgs(BaseModel):
    ref: str = Field(
        ..., min_length=1, max_length=128,
        description="会话矢量 ref（ref: 前缀，必须有强 content_revision）",
        json_schema_extra={"ref_cursor": True})


#: charts/conditions 以原生 dict 列表传入（Pi 端 schema 校验不解析
#: 嵌套 pydantic $defs —— review 运行时发现；typed 校验由工具内
#: contract.ChartSpec/parse_conditions 完成，REST 面维持 pydantic 模型）。
class PreviewArgs(BaseModel):
    ref: str = Field(..., min_length=1, max_length=128,
                     json_schema_extra={"ref_cursor": True})
    charts: List[Dict[str, Any]] = Field(
        default_factory=list, max_length=8,
        description="ChartSpec dict 列表：chart_id/kind(category|range)/"
                    "field/aggregation(count|sum|mean|min|max)/value_field/"
                    "title/bins(2-32)/layer_id")
    conditions: List[Dict[str, Any]] = Field(
        default_factory=list, max_length=16,
        description="条件 dict 列表：category(values[]+include_null) 或 "
                    "range(gte/gt/lte/lt 各≤1 端有限数)")


class SaveViewArgs(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    #: ref_cursor：plan.anchor.ref_id 内嵌 ref id —— 解引用阶段跳过整字段
    plan: Dict[str, Any] = Field(
        ..., description="ExplorationPlan（schema_version=2：anchor/charts/conditions）",
        json_schema_extra={"ref_cursor": True})


class ViewIdArgs(BaseModel):
    #: 空 = 列出当前会话视图（工具描述广告的 list 分支；review P1-2：
    #: 此前必填导致 list 调用校验直接失败）
    view_id: str = Field(default="", max_length=64)


class UpdateViewArgs(ViewIdArgs):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    plan: Optional[Dict[str, Any]] = Field(
        default=None, json_schema_extra={"ref_cursor": True})
    status: Optional[str] = Field(default=None, description="draft | archived")
    expected_revision: int = Field(..., ge=1)


class ApplyArgs(ViewIdArgs):
    expected_revision: Optional[int] = Field(default=None, ge=1)


class PlanPreviewArgs(BaseModel):
    plan: Dict[str, Any] = Field(
        ..., description="ExplorationPlan（v3 多源：sources/links/"
                         "metrics/charts/conditions；v2 单源同面兼容）",
        json_schema_extra={"ref_cursor": True})
    selection_ref: Optional[str] = Field(
        default=None, max_length=32,
        description="选择史指代：'previous'（刚才那组）或代号；不可解析 → "
                    "SELECTION_REF_UNRESOLVED typed 拒绝 + 最小澄清")
    selection_source_id: Optional[str] = Field(
        default=None, max_length=64,
        description="指代作用源（缺省 primary）")


class DrillArgs(BaseModel):
    plan: Dict[str, Any] = Field(
        ..., json_schema_extra={"ref_cursor": True})
    source_id: str = Field(..., min_length=1, max_length=64)
    direction: str = Field(..., description="down（钻入父级）| up（上卷）")
    parent_value: Optional[Any] = Field(
        default=None, description="down 必填：父级键（typed scalar）")


def register_linked_exploration_tools(registry: ToolRegistry) -> None:
    """注册 G46 工具（只读×2 + 视图管理×3 + 受控写×1）。"""
    from app.services.linked_exploration import contract, service
    from app.services.linked_exploration.errors import LinkedExplorationError

    async def _guard(coro):
        try:
            return await coro
        except LinkedExplorationError as exc:
            payload = exc.payload()
            payload["success"] = False
            return payload
        except Exception:  # noqa: BLE001 - 工具错误契约
            logger.warning("[linked_exploration] tool failed", exc_info=True)
            return {
                "success": False, "code": "G46_TOOL_FAILED",
                "message": "linked exploration operation failed",
                "correction_hint": "Check ref/anchor, charts, conditions, "
                                   "view revision and session ownership.",
            }

    def _actor(session_id: str) -> Dict[str, Any]:
        return {"user_id": None, "org_id": None, "owner_token": None,
                "trusted_session_id": session_id}

    def _charts(raw: List[Dict[str, Any]]) -> List[Any]:
        out = []
        for i, c in enumerate(raw or []):
            try:
                out.append(contract.ChartSpec.model_validate(c))
            except Exception as exc:
                raise LinkedExplorationError(
                    "INVALID_CHART", f"charts[{i}] 非法：{exc}",
                    index=i, status_code=422) from exc
        return out

    def _conditions(raw: List[Dict[str, Any]]) -> List[Any]:
        return contract.parse_conditions(raw or [])

    def _explain_conditions(parsed: List[Any]) -> List[Dict[str, Any]]:
        """Pi 可读的条件解释（哪些条件生效、typed 值、口径）。"""
        out = []
        for c in parsed:
            entry: Dict[str, Any] = {
                "condition_id": c.condition_id,
                "chart_id": c.chart_id,
                "field": c.field,
                "kind": c.kind,
            }
            if c.kind == "category":
                entry["values"] = list(c.values)
                entry["include_null"] = c.include_null
                entry["semantics"] = "同控件内多值 OR（typed：1/\"1\"/true 互不等）"
            else:
                bounds = {k: getattr(c, k) for k in ("gte", "gt", "lte", "lt")
                          if getattr(c, k) is not None}
                entry.update(bounds)
                entry["semantics"] = "闭开与 evaluate_where 一致（gte/lte 闭，gt/lt 开）"
            out.append(entry)
        return out

    def _bounded_preview(result: Dict[str, Any]) -> Dict[str, Any]:
        charts_brief = []
        for c in result.get("charts", []):
            facet = c.get("facet", {}).get("statistics", {})
            full = c.get("full", {}).get("statistics", {})
            rows = (facet.get("rows") or facet.get("bins") or [])[:_MAX_ROWS]
            charts_brief.append({
                "chart_id": c.get("chart_id"),
                "kind": c.get("kind"),
                "field": c.get("field"),
                "facet_scope": "excluding-own-condition",
                "facet_matched": facet.get("matched"),
                "facet_rows": rows,
                "facet_truncated": bool(facet.get("truncated")),
                "full_matched": full.get("matched"),
                "full_valid_count": full.get("valid_count"),
                "denominators": {
                    "facet": facet.get("denominator_note"),
                    "full": full.get("denominator_note"),
                },
            })
        handle = result.get("selection_handle") or {}
        return {
            "success": True,
            "anchor": result.get("anchor"),
            "conditions_digest": result.get("conditions_digest"),
            "conditions": _explain_conditions(result.get("_parsed", [])),
            "combined_where": result.get("combined_where"),
            "map_filter_applies_to": "live MapLibre layer filter + SVG/PNG/PDF export",
            "match_count": result.get("match_count"),
            "match_count_exact": result.get("match_count_exact"),
            "total_source_features": result.get("total"),
            "completeness": result.get("completeness"),
            "selection_handle": (
                {"handle": handle.get("handle"), "count": handle.get("count"),
                 "count_exact": handle.get("count_exact"),
                 "note": "服务端权威完整集合句柄（600s TTL）"}
                if handle else None
            ),
            "charts": charts_brief,
            "note": "rows 截断到 top 桶；完整统计走 UI/preview 回执",
        }

    @tool(
        registry,
        name="webgis_exploration_inspect",
        capabilities=["linked_exploration", "layer_browse"],
        tier=2,
        domains=["exploration", "cartography"],
        args_model=InspectArgs,
        description=(
            "只读检查矢量源能否进入联合探索：强版本源锚（ref+单调 "
            "content_revision+指纹）、字段清单（类型/空值计数/类别候选抽样/"
            "数值范围估计）。无强版本/MVT 瓦片/纯 URL → typed 拒绝。"
            "\n何时用：用户要『围绕这层数据做多图联动筛选/探索』之前。"
            "\n何时不用：只需浏览分页要素用 webgis_layer_features_page。"
        ),
        side_effect="cacheable_read",
        network=False,
        deterministic=True,
        latency_class="medium",
        memory_class="medium",
        scale_class="large",
        tags=("联合筛选", "探索视图", "linked exploration"),
        output_semantic_type="json",
        result_size_policy="inline_small",
        failure_modes=("invalid_args", "missing_data", "permission_denied",
                       "no_strong_revision"),
    )
    async def webgis_exploration_inspect(
        ref: str, session_id: str = "",
    ) -> dict:
        if not session_id:
            return {"success": False, "code": "SOURCE_UNAVAILABLE",
                    "message": "Missing session_id context"}
        result = await _guard(service.inspect_source(
            session_id, ref, _actor(session_id)))
        if result.get("success") is False:
            return result
        return {
            "success": True,
            "anchor": result.get("anchor"),
            "feature_count": result.get("feature_count"),
            "fields": (result.get("fields") or [])[:_MAX_FIELDS],
            "fields_truncated": len(result.get("fields") or []) > _MAX_FIELDS,
            "schema_complete": result.get("schema_complete"),
            "sample_note": result.get("sample_note"),
        }

    @tool(
        registry,
        name="webgis_exploration_preview",
        capabilities=["linked_exploration", "layer_browse"],
        tier=2,
        domains=["exploration", "cartography"],
        args_model=PreviewArgs,
        description=(
            "组合条件求值预览（零 mutation）：同代冻结全源 → 组合 where（条件间 "
            "AND、同分类条件内 typed 值 OR）→ 完整匹配计数 + 每图两套口径统计"
            "（facet=排除本图自身条件 / full=全部条件）+ where-scope 完整集合"
            "句柄 + 精确 MapLibre 过滤表达式。空集如实返回 0，不回退全量。"
            "\n何时用：解释『当前条件下匹配多少/哪些类别可选』、检查组合语义。"
            "\n何时不用：要落图/保存视图用 view_save/apply。"
        ),
        side_effect="cacheable_read",
        network=False,
        deterministic=True,
        latency_class="slow",
        memory_class="medium",
        scale_class="large",
        tags=("联合筛选", "组合条件", "facet", "linked exploration"),
        output_semantic_type="json",
        result_size_policy="bounded",
        failure_modes=("invalid_args", "validation_error", "missing_data",
                       "no_strong_revision", "source_mutation_during_read",
                       "stats_over_limit"),
    )
    async def webgis_exploration_preview(
        ref: str, charts: List[Dict[str, Any]], conditions: List[Dict[str, Any]],
        session_id: str = "",
    ) -> dict:
        if not session_id:
            return {"success": False, "code": "SOURCE_UNAVAILABLE",
                    "message": "Missing session_id context"}
        try:
            chart_models = _charts(charts or [])
            cond_models = _conditions(conditions or [])
        except LinkedExplorationError as exc:
            payload = exc.payload()
            payload["success"] = False
            return payload
        result = await _guard(service.preview_conditions(
            session_id, ref, chart_models, cond_models, _actor(session_id)))
        if result.get("success") is False:
            return result
        result["_parsed"] = cond_models
        return _bounded_preview(result)

    @tool(
        registry,
        name="webgis_exploration_view_save",
        capabilities=["linked_exploration"],
        tier=2,
        domains=["exploration", "cartography"],
        args_model=SaveViewArgs,
        description=(
            "保存命名探索视图（持久，跨会话/重启可恢复）：计划锚+图表+条件 → "
            "exploration_views 行（revision CAS）。保存时重新冻结核对锚——源已"
            "推进返回 STALE_SOURCE，需重新预览。保存不隐含换当前地图。"
            "\n何时用：用户说『保存这套筛选视图/以后再打开』。"
            "\n何时不用：只是临时看组合结果（用 preview，不落盘）。"
        ),
        side_effect="state_mutation",
        network=False,
        deterministic=False,
        latency_class="medium",
        memory_class="light",
        scale_class="medium",
        tags=("联合筛选", "持久视图", "linked exploration"),
        output_semantic_type="json",
        result_size_policy="inline_small",
        data_mutations=("session_state",),
        failure_modes=("invalid_args", "invalid_plan", "stale_source",
                       "plan_over_limit"),
    )
    async def webgis_exploration_view_save(
        title: str, plan: Dict[str, Any], session_id: str = "",
    ) -> dict:
        if not session_id:
            return {"success": False, "code": "SOURCE_UNAVAILABLE",
                    "message": "Missing session_id context"}
        result = await _guard(service.save_view(
            session_id, _actor(session_id), title=title, plan_raw=plan))
        if result.get("success") is False:
            return result
        return {
            "success": True,
            "view_id": result.get("view_id"),
            "title": result.get("title"),
            "revision": result.get("revision"),
            "normalized_digest": result.get("normalized_digest"),
            "source_anchor": {
                "ref": result.get("source_ref"),
                "revision": result.get("source_revision"),
                "fingerprint": result.get("source_fingerprint"),
            },
            "note": "视图已持久化；open 校验锚，apply 写图表组件进 MapSpec",
        }

    @tool(
        registry,
        name="webgis_exploration_views",
        capabilities=["linked_exploration"],
        tier=2,
        domains=["exploration", "cartography"],
        args_model=ViewIdArgs,
        description=(
            "列出/打开命名探索视图。带 view_id → open：按 pinned 锚校验当前源"
            "（匹配 → ready + 同代预览摘要；漂移 → stale + 原因，绝不无声绑定"
            "最新数据）。不带 view_id → 列出当前会话视图（≤20）。"
            "\n何时用：用户要『重开之前保存的探索视图』或查看已保存视图。"
        ),
        side_effect="cacheable_read",
        network=False,
        deterministic=True,
        latency_class="medium",
        memory_class="light",
        scale_class="medium",
        tags=("联合筛选", "重开视图", "linked exploration"),
        output_semantic_type="json",
        result_size_policy="inline_small",
        failure_modes=("invalid_args", "view_not_found", "permission_denied"),
    )
    async def webgis_exploration_views(
        view_id: str = "", session_id: str = "",
    ) -> dict:
        if not session_id:
            return {"success": False, "code": "SOURCE_UNAVAILABLE",
                    "message": "Missing session_id context"}
        if not view_id:
            listed = await _guard(service.list_views(session_id, _actor(session_id)))
            if isinstance(listed, dict) and listed.get("success") is False:
                return listed
            views = listed if isinstance(listed, list) else []
            return {"success": True, "views": views[:_MAX_VIEWS],
                    "truncated": len(views) > _MAX_VIEWS}
        result = await _guard(service.open_view(
            view_id, session_id, _actor(session_id)))
        if result.get("success") is False:
            return result
        view = result.get("view") or {}
        out: Dict[str, Any] = {
            "success": True,
            "view_id": view.get("view_id"),
            "title": view.get("title"),
            "revision": view.get("revision"),
            "state": result.get("state"),
            "plan": result.get("plan"),
        }
        if result.get("state") == "ready":
            out["preview"] = result.get("preview")
        else:
            out["stale_reason"] = result.get("stale_reason")
            out["correction_hint"] = "重新 inspect/preview 后用新锚更新视图计划"
        return out

    @tool(
        registry,
        name="webgis_exploration_view_update",
        capabilities=["linked_exploration"],
        tier=2,
        domains=["exploration", "cartography"],
        args_model=UpdateViewArgs,
        description=(
            "CAS 修改命名视图（标题/计划/归档）。expected_revision 不匹配 → "
            "STALE_VIEW_REVISION（并发作者冲突，不盲覆盖）。archived 可再改回 "
            "draft 恢复。"
        ),
        side_effect="state_mutation",
        network=False,
        deterministic=False,
        latency_class="medium",
        memory_class="light",
        scale_class="medium",
        tags=("联合筛选", "视图管理", "linked exploration"),
        output_semantic_type="json",
        result_size_policy="inline_small",
        data_mutations=("session_state",),
        failure_modes=("invalid_args", "view_not_found", "stale_view_revision"),
    )
    async def webgis_exploration_view_update(
        view_id: str, expected_revision: int,
        title: Optional[str] = None, plan: Optional[Dict[str, Any]] = None,
        status: Optional[str] = None, session_id: str = "",
    ) -> dict:
        if not session_id:
            return {"success": False, "code": "SOURCE_UNAVAILABLE",
                    "message": "Missing session_id context"}
        result = await _guard(service.update_view(
            view_id, session_id, _actor(session_id), title=title,
            plan_raw=plan, status=status,
            expected_revision=expected_revision))
        if result.get("success") is False:
            return result
        return {"success": True, "view_id": result.get("view_id"),
                "revision": result.get("revision"), "status": result.get("status")}

    @tool(
        registry,
        name="webgis_exploration_apply",
        capabilities=["linked_exploration", "layer_display_control"],
        tier=2,
        domains=["exploration", "cartography", "mapspec"],
        args_model=ApplyArgs,
        description=(
            "把视图的图表组件写入 committed MapSpec（upsert、分步失败回滚、"
            "可撤销）。组件只携带配置（viewId/chartId/field/kind/源锚），渲染"
            "数据由前端实时 preview 获取。apply 不改源数据、不清既有图层过滤、"
            "不隐含换当前地图。"
            "\n何时用：用户要在地图工作区看到这套探索图表实例。"
            "\n何时不用：只想临时看统计（preview 即可）。"
        ),
        side_effect="state_mutation",
        network=False,
        deterministic=False,
        latency_class="medium",
        memory_class="light",
        scale_class="medium",
        tags=("联合筛选", "应用视图", "linked exploration"),
        output_semantic_type="json",
        result_size_policy="inline_small",
        required_context=("map_state",),
        map_mutations=("component",),
        failure_modes=("invalid_args", "view_not_found", "empty_view",
                       "mapspec_superseded", "apply_failed"),
    )
    async def webgis_exploration_apply(
        view_id: str, expected_revision: Optional[int] = None,
        session_id: str = "",
    ) -> dict:
        if not session_id:
            return {"success": False, "code": "SOURCE_UNAVAILABLE",
                    "message": "Missing session_id context"}
        result = await _guard(service.apply_view_to_mapspec(
            view_id, session_id, _actor(session_id),
            expected_revision=expected_revision))
        if result.get("success") is False:
            return result
        return {
            "success": True,
            "applied_components": result.get("applied_components"),
            "count": result.get("count"),
            "state": result.get("state"),
            "reconcile_via": "mapspec_event",
            "note": "图表组件已写入 MapSpec；条件激活/组合过滤由前端探索面板承载",
        }

    # ─── 多源（v3）：计划预览与层级钻取 ──────────────────────────────

    @tool(
        registry,
        name="webgis_exploration_plan_preview",
        capabilities=["linked_exploration", "layer_browse"],
        tier=2,
        domains=["exploration", "cartography"],
        args_model=PlanPreviewArgs,
        description=(
            "多源联动预览（零 mutation）：逐源同代冻结+锚核对 → per-source "
            "条件（跨控件 AND、同控件 OR）→ 关系传播键集半连接（DAG 单遍；"
            "未过滤上游不限制；上游零匹配→下游零匹配）→ 每源精确匹配计数 + "
            "每图 facet/full 统计 + 指标（rate 分母治理：分母 0/空 → 该组 "
            "unavailable，绝不冒充 0 率）。回答『选中 X 后另一源有多少、每万人"
            "指标是多少』。空集如实返回 0。"
            "\n何时用：多源任务（如学校点×区县人口表）的组合范围/指标问答。"
            "\n何时不用：单源快速筛选用 webgis_exploration_preview。"
        ),
        side_effect="cacheable_read",
        network=False,
        deterministic=True,
        latency_class="slow",
        memory_class="medium",
        scale_class="large",
        tags=("多源联动", "指标", "传播", "linked exploration"),
        output_semantic_type="json",
        result_size_policy="bounded",
        failure_modes=("invalid_args", "invalid_plan", "stale_source",
                       "propagation_keyset_over_limit", "no_strong_revision",
                       "source_mutation_during_read", "stats_over_limit",
                       "metric_over_limit", "spatial_metric_unsupported"),
    )
    async def webgis_exploration_plan_preview(
        plan: Dict[str, Any], selection_ref: Optional[str] = None,
        selection_source_id: Optional[str] = None, session_id: str = "",
    ) -> dict:
        if not session_id:
            return {"success": False, "code": "SOURCE_UNAVAILABLE",
                    "message": "Missing session_id context"}
        result = await _guard(service.preview_plan(
            session_id, plan, _actor(session_id),
            selection_ref=selection_ref,
            selection_source_id=selection_source_id))
        if result.get("success") is False:
            return result
        per_source_out = {}
        for sid, entry in (result.get("per_source") or {}).items():
            handle = entry.get("selection_handle") or {}
            skipped = entry.get("selection_handle_skipped") or {}
            per_source_out[sid] = {
                "role": entry.get("role"),
                "match_count": entry.get("match_count"),
                "match_count_exact": entry.get("match_count_exact"),
                "total": entry.get("total"),
                "map_filter_projected": entry.get("map_filter_projected"),
                "propagations": [
                    {k: p.get(k) for k in
                     ("link_id", "from_source", "target_fields", "key_count",
                      "from_unfiltered", "skipped_missing", "skipped_invalid")}
                    for p in (entry.get("propagations") or [])
                ],
                "selection_handle": (
                    {"handle": handle.get("handle"),
                     "count": handle.get("count"),
                     "note": "最终联动范围句柄（600s TTL）"} if handle else None),
                "handle_skipped_reason": skipped.get("reason") or None,
            }
        charts_brief = []
        for c in result.get("charts", []):
            facet = c.get("facet", {}).get("statistics", {})
            full = c.get("full", {}).get("statistics", {})
            rows = (facet.get("rows") or facet.get("bins") or [])[:_MAX_ROWS]
            charts_brief.append({
                "chart_id": c.get("chart_id"),
                "source_id": c.get("source_id"),
                "kind": c.get("kind"),
                "field": c.get("field"),
                "facet_matched": facet.get("matched"),
                "facet_rows": rows,
                "facet_truncated": bool(facet.get("truncated")),
                "full_matched": full.get("matched"),
                "denominator_notes": {
                    "facet": facet.get("denominator_note"),
                    "full": full.get("denominator_note"),
                },
            })
        metrics_out = []
        for m in result.get("metrics", []):
            groups = []
            for g in (m.get("groups") or [])[:_MAX_ROWS]:
                groups.append({
                    "key": g.get("key"),
                    "key_label": g.get("key_label"),
                    "count": g.get("count"),
                    "value": g.get("value") if g.get("available") else None,
                    "available": g.get("available"),
                    "unavailable_reason": g.get("unavailable_reason"),
                    "numerator": g.get("numerator"),
                    "denominator": g.get("denominator"),
                    # review C-P2-3：权重不可比行数是 weighted_mean 口径
                    # 解释的一部分（数值偏少的诚实披露）
                    "invalid_weight_count": g.get("invalid_weight_count"),
                })
            metrics_out.append({
                "metric_id": m.get("metric_id"),
                "op": m.get("op"),
                "unit": m.get("unit"),
                "scale": m.get("scale"),
                "truncated": m.get("truncated"),
                "groups": groups,
                "disclosure": m.get("disclosure"),
            })
        return {
            "success": True,
            "schema_version": result.get("schema_version"),
            "primary_source_id": result.get("primary_source_id"),
            "anchors": result.get("anchors"),
            "conditions_digest": result.get("conditions_digest"),
            "plan_digest": result.get("plan_digest"),
            "selection_ref_resolved": result.get("selection_ref_resolved"),
            "per_source": per_source_out,
            "charts": charts_brief,
            "metrics": metrics_out,
            "note": "rows/组截断到 top；分母 0/空 → unavailable+原因码；"
                    "完整统计走 UI plan-preview",
        }

    @tool(
        registry,
        name="webgis_exploration_drill",
        capabilities=["linked_exploration", "layer_browse"],
        tier=2,
        domains=["exploration", "cartography"],
        args_model=DrillArgs,
        description=(
            "多源计划层级钻取/上卷（零 mutation）：按计划内显式声明的 "
            "hierarchy.parent_field 改写钻取条件并重放权威 preview；返回逐父级"
            "计数摘要（下一级有哪些、各多少）。源未声明层级 → HIERARCHY_NOT_"
            "DECLARED 诚实阻塞（不按字段名称猜测行政区层级）。"
            "\n何时用：『选中武侯区后按区钻取学校』等跨粒度下探/上卷。"
            "\n何时不用：同粒度内筛选（直接改 conditions 走 plan_preview）。"
        ),
        side_effect="cacheable_read",
        network=False,
        deterministic=True,
        latency_class="slow",
        memory_class="medium",
        scale_class="large",
        tags=("多源联动", "钻取", "层级", "linked exploration"),
        output_semantic_type="json",
        result_size_policy="bounded",
        failure_modes=("invalid_args", "invalid_plan", "unknown_source",
                       "hierarchy_not_declared", "invalid_drill_value",
                       "stale_source", "propagation_keyset_over_limit"),
    )
    async def webgis_exploration_drill(
        plan: Dict[str, Any], source_id: str, direction: str,
        parent_value: Optional[Any] = None, session_id: str = "",
    ) -> dict:
        if not session_id:
            return {"success": False, "code": "SOURCE_UNAVAILABLE",
                    "message": "Missing session_id context"}
        result = await _guard(service.drill_plan(
            session_id, plan, _actor(session_id), source_id=source_id,
            direction=direction, parent_value=parent_value))
        if result.get("success") is False:
            return result
        return {
            "success": True,
            "plan": result.get("plan"),
            "drill": result.get("drill"),
            "preview_brief": result.get("preview_brief"),
            "note": "返回的 plan 为改写后的完整计划（含钻取条件与摘要图）；"
                    "继续追问请用它调用 plan_preview，保存用 view_save",
        }


def register_linked_exploration_export_tool(registry: ToolRegistry) -> None:
    """注册 G46 导出工具（artifact_creation —— 真实字节 + 受权下载）。"""
    from app.services.linked_exploration.export import export_exploration
    from app.services.linked_exploration.errors import LinkedExplorationError

    class ExportArgs(BaseModel):
        fmt: str = Field(default="svg", description="svg | png | pdf")
        dpi: int = Field(default=150, ge=72, le=400)
        view_id: Optional[str] = Field(default=None, max_length=64)
        plan: Optional[Dict[str, Any]] = Field(
            default=None, json_schema_extra={"ref_cursor": True})
        title: str = Field(default="", max_length=200)

    @tool(
        registry,
        name="webgis_exploration_export",
        capabilities=["linked_exploration", "map_export_publishing"],
        tier=2,
        domains=["exploration", "cartography", "publication"],
        args_model=ExportArgs,
        description=(
            "冻结导出联合探索成果（SVG/PNG/PDF）：重新冻结核对源锚 → 组合条件"
            "编译为图层 filter（与前端 live 同一表达式）→ facet 图表统计与口径"
            "卡印入出版面 → 真实字节落盘 + 受权下载 URL。refs-only 回执，不携带"
            "图像载荷；图面匹配集与统计同代（导出期间源变化 → typed 拒绝重冻）。"
            "\n何时用：用户要『把当前筛选视图导出成图/报告』。"
        ),
        side_effect="artifact_creation",
        network=False,
        deterministic=False,
        latency_class="slow",
        memory_class="medium",
        scale_class="large",
        tags=("联合筛选", "导出", "出版", "linked exploration"),
        output_semantic_type="artifact_refs",
        produced_refs=("artifact", "manifest"),
        result_size_policy="ref_offload",
        failure_modes=("invalid_args", "stale_source", "mapspec_unavailable",
                       "png_failed", "pdf_failed", "export_persist_failed"),
    )
    async def webgis_exploration_export(
        fmt: str = "svg", dpi: int = 150, view_id: Optional[str] = None,
        plan: Optional[Dict[str, Any]] = None, title: str = "",
        session_id: str = "",
    ) -> dict:
        if not session_id:
            return {"success": False, "code": "SOURCE_UNAVAILABLE",
                    "message": "Missing session_id context"}
        try:
            result = await export_exploration(
                session_id,
                {"user_id": None, "org_id": None, "owner_token": None,
                 "trusted_session_id": session_id},
                fmt=fmt, dpi=dpi, view_id=view_id, plan_raw=plan, title=title)
        except LinkedExplorationError as exc:
            payload = exc.payload()
            payload["success"] = False
            return payload
        except Exception:  # noqa: BLE001 - 工具错误契约
            logger.warning("[linked_exploration] export failed", exc_info=True)
            return {"success": False, "code": "G46_TOOL_FAILED",
                    "message": "exploration export failed"}
        return {
            "success": True,
            "format": result.get("format"),
            "dpi": result.get("dpi"),
            "bytes": result.get("bytes"),
            "download_url": result.get("download_url"),
            "manifest_url": result.get("manifest_url"),
            "filename": result.get("filename"),
            "match_count": result.get("match_count"),
            "total": result.get("total"),
            "conditions_digest": result.get("conditions_digest"),
            "filtered_layer_ids": result.get("filtered_layer_ids"),
            "note": "回执仅 refs；图面/统计与冻结代一致（回读核验用 manifest_url）",
        }
