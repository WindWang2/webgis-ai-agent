"""图层浏览/聚合工具（Track B · M14）—— Agent 的有界分页读面。

把 ``GET /layers/data/{ref}/features|aggregate|feature`` 的浏览半边开放给
Harness/Pi：Agent 可以「分页浏览大图层属性、按字段排序/typed 过滤、取
视口窗口统计、查单要素详情」，**不必也不允许**整包拉 FC。

诚实纪律（INTERFACE_CONTRACTS §7）：
- 响应必带 ``is_complete``/``truncated``/``reason_codes``/``total_matching``
  —— Agent 看到的就是客户端看到的同一份窗口语义，不得宣称全量；
- ``total_matching`` 只在服务端可证时给出，否则 ``null``（total_unknown）；
- 会话隔离：所有读经 ``session_data_manager`` 的 owner 校验路径（同 REST）。
"""
from __future__ import annotations

import logging
from typing import Any, List, Optional

from pydantic import BaseModel, Field

from app.services.session_data import session_data_manager
from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)


class LayerFeaturesPageArgs(BaseModel):
    ref_id: str = Field(..., description="会话数据引用（ref:xxx / 别名）")
    limit: int = Field(20, ge=1, le=200, description="页大小（Agent 读面 ≤200）")
    cursor: Optional[str] = Field(None, description="上一页返回的 next_cursor")
    order: Optional[str] = Field(None, description="单字段排序：'<field>' / '-<field>'")
    where: Optional[str] = Field(
        None,
        description=(
            "typed 谓词 AST（JSON 字符串）："
            '{"field":"类型","op":"eq","value":"小学"} / {"field":"学生数","op":"gte","value":100} / '
            '{"op":"or","terms":[...]}；op ∈ eq|ne|in|between|contains|startswith|gte|gt|lte|lt'
        ),
    )
    bbox: Optional[str] = Field(None, description="窗口 'w,s,e,n'（经纬度，粗滤）")
    fields: Optional[str] = Field(None, description="properties 投影 CSV（建议限定，减小输出）")
    v: Optional[int] = Field(None, description="客户端持有的 content_revision（revision guard）")


class LayerAggregateArgs(BaseModel):
    ref_id: str = Field(..., description="会话数据引用（ref:xxx / 别名）")
    group_by: str = Field(..., description="分组字段（properties 键）")
    agg: str = Field("count", description="count|sum|mean|min|max")
    agg_field: Optional[str] = Field(None, description="聚合数值字段（agg≠count 必填）")
    bbox: Optional[str] = Field(None, description="窗口 'w,s,e,n'（视口统计用）")
    where: Optional[str] = Field(None, description="typed 谓词 AST（同 features 工具）")
    max_groups: int = Field(64, ge=1, le=256)
    v: Optional[int] = Field(None, description="revision guard")


class LayerFeatureDetailArgs(BaseModel):
    ref_id: str = Field(..., description="会话数据引用（ref:xxx / 别名）")
    feature_id: str = Field(..., description="要素 id（链内稳定 id；或位置 id + id_mode='positional'）")
    id_mode: Optional[str] = Field(
        None, description="'positional'=位置 id 寻址（MVT 无稳定 id 数据集；仅 revision 内稳定）"
    )


def _extract_fc(data: Any) -> Optional[dict]:
    if isinstance(data, dict):
        nested = data.get("geojson")
        if isinstance(nested, dict) and nested.get("type") == "FeatureCollection":
            return nested
        if data.get("type") == "FeatureCollection":
            return data
    return None


async def _resolve_ref_fc(session_id: str, ref_id: str):
    """ref → (resolved, descriptor, fc)；鉴权/缺失语义与 REST 路由一致。"""
    resolved = await session_data_manager.resolve_alias(session_id, ref_id)
    descriptor = await session_data_manager.get_ref_descriptor(session_id, resolved)
    res = await session_data_manager.get_ref_data_shared(session_id, resolved)
    if not res.success:
        error = res.error or "数据不可用"
        return resolved, descriptor, {"error": error, "error_type": res.error_type}
    fc = _extract_fc(res.data)
    if not fc:
        return resolved, descriptor, {"error": "ref 不是 FeatureCollection"}
    return resolved, descriptor, fc


def register_layer_browse_tools(registry: ToolRegistry):
    """注册图层浏览/聚合工具（tier-1 always-on；只读、有界）。"""

    @tool(
        registry,
        name="webgis_layer_features_page",
        capabilities=["layer_browse"],
        tier=1,
        domains=["dataset"],
        description=(
            "分页浏览会话图层的要素属性（有界读面，绝不整包下载）。"
            "支持单字段排序（order）与 typed 谓词过滤（where JSON）、bbox 窗口。"
            "响应含 total_matching/is_complete/truncated/reason_codes —— "
            "total_matching=null 表示总数未知，绝不能当作全量表述。"
            "\n何时用：用户要看图层的属性列表/表格、按条件筛选要素、"
            "浏览大图层（MVT 通道）的属性。"
        ),
        args_model=LayerFeaturesPageArgs,
        side_effect="cacheable_read",
        deterministic=True,
        latency_class="medium",
        memory_class="light",
        scale_class="large",
        tags=("属性表", "分页", "浏览", "过滤", "排序", "features", "table"),
        output_semantic_type="json",
        result_size_policy="inline_small",
    )
    async def webgis_layer_features_page(
        ref_id: str,
        limit: int = 20,
        cursor: Optional[str] = None,
        order: Optional[str] = None,
        where: Optional[str] = None,
        bbox: Optional[str] = None,
        fields: Optional[str] = None,
        v: Optional[int] = None,
        session_id: Optional[str] = None,
    ) -> dict:
        import asyncio

        from app.services.feature_pages import (
            FeaturePageError,
            FeatureRevisionConflict,
            count_matching,
            id_mode_of,
            page_features,
            parse_bbox_param,
            parse_fields_param,
            parse_order_param,
            parse_where_param,
        )

        if not session_id:
            return {"error": "Missing session_id context"}
        try:
            window_bbox = parse_bbox_param(bbox)
            field_list = parse_fields_param(fields)
            order_spec = parse_order_param(order)
            where_ast = parse_where_param(where)
        except FeaturePageError as e:
            return {"error": f"参数非法: {e}"}

        resolved, descriptor, fc = await _resolve_ref_fc(session_id, ref_id)
        if "error" in fc:
            return fc
        current_revision = descriptor.get("content_revision") if descriptor else None
        if v is not None and current_revision is not None and int(v) != int(current_revision):
            return {
                "error": "revision_conflict",
                "current_revision": current_revision,
                "hint": "数据已被覆写，请以 current_revision 重新开始分页",
            }
        cache_key = (resolved, str(current_revision))
        try:
            page = await asyncio.to_thread(
                page_features, fc, limit=limit, cursor=cursor, bbox=window_bbox,
                fields=field_list, order=order_spec, where=where_ast, cache_key=cache_key,
            )
            total_matching = None
            if where_ast is not None or window_bbox is not None:
                total_matching = await asyncio.to_thread(
                    count_matching, fc.get("features", []), where=where_ast,
                    bbox=window_bbox, cache_key=cache_key,
                )
            id_info = await asyncio.to_thread(id_mode_of, fc.get("features", []), cache_key)
        except FeaturePageError as e:
            return {"error": f"参数非法: {e}"}
        except FeatureRevisionConflict:  # pragma: no cover - 防御（page_features 不发射）
            return {"error": "revision_conflict"}
        # 取数后复查（R1 评审：与 REST 双查对称 —— 覆写落在 descriptor 读与
        # 取数之间时，不得把新载荷贴旧 revision 标签返回）。
        live_descriptor = await session_data_manager.get_ref_descriptor(session_id, resolved)
        live_revision = live_descriptor.get("content_revision") if live_descriptor else None
        if current_revision is not None and live_revision is not None and int(live_revision) != int(current_revision):
            return {
                "error": "revision_conflict",
                "current_revision": live_revision,
                "hint": "计算期间数据被覆写，请以 current_revision 重新分页",
            }

        reason_codes: List[str] = []
        if window_bbox is not None:
            reason_codes.append("bbox_coarse")
        if where_ast is not None:
            reason_codes.append("filter_applied")
        if order_spec is not None:
            reason_codes.append(f"sorted:{order_spec[0]}")
        if page["truncated"]:
            reason_codes.append("scan_budget_stop")
        if total_matching is not None:
            reason_codes.append("total_exact")
        elif where_ast is not None or window_bbox is not None:
            reason_codes.append("total_unknown")

        return {
            "features": page["features"],
            "pagination": {
                "next_cursor": page["next_cursor"],
                "has_more": page["has_more"],
                "returned": page["returned"],
                "is_complete": not page["has_more"],
                "truncated": page["truncated"],
            },
            "total_matching": total_matching,
            "feature_count": descriptor.get("feature_count") if descriptor else None,
            "id_mode": id_info["id_mode"],
            "duplicate_id_count": id_info["duplicate_id_count"],
            "revision": current_revision,
            "reason_codes": reason_codes,
            "scope_note": (
                "窗口分页结果；仅 total_matching 非 null 时才可表述匹配总数，"
                "绝不能把本页 returned 当作全量"
            ),
        }

    @tool(
        registry,
        name="webgis_layer_aggregate",
        capabilities=["layer_browse"],
        tier=1,
        domains=["dataset", "statistics"],
        description=(
            "会话图层的分组聚合（count/sum/mean/min/max，全扫口径）：视口/"
            "过滤窗口统计的服务端真值。null 键/非数值计数与组溢出（__other__）"
            "都如实披露；分母缺失时不得产出 per-capita 类结论。"
            "\n何时用：『视口内各类型学校数量』『按区统计均值』等图表/统计问答。"
        ),
        args_model=LayerAggregateArgs,
        side_effect="cacheable_read",
        deterministic=True,
        latency_class="medium",
        memory_class="light",
        scale_class="large",
        tags=("聚合", "统计", "分组", "视口统计", "aggregate", "group_by"),
        output_semantic_type="json",
        result_size_policy="inline_small",
    )
    async def webgis_layer_aggregate(
        ref_id: str,
        group_by: str,
        agg: str = "count",
        agg_field: Optional[str] = None,
        bbox: Optional[str] = None,
        where: Optional[str] = None,
        max_groups: int = 64,
        v: Optional[int] = None,
        session_id: Optional[str] = None,
    ) -> dict:
        import asyncio

        from app.services.feature_aggregate import (
            AggregateOverLimit,
            aggregate_features,
            validate_aggregate_params,
        )
        from app.services.feature_pages import (
            FeaturePageError,
            parse_bbox_param,
            parse_where_param,
        )

        if not session_id:
            return {"error": "Missing session_id context"}
        try:
            group_by, agg, agg_field, max_groups = validate_aggregate_params(
                group_by, agg, agg_field, max_groups)
            window_bbox = parse_bbox_param(bbox)
            where_ast = parse_where_param(where)
        except FeaturePageError as e:
            return {"error": f"参数非法: {e}"}

        resolved, descriptor, fc = await _resolve_ref_fc(session_id, ref_id)
        if "error" in fc:
            return fc
        current_revision = descriptor.get("content_revision") if descriptor else None
        if v is not None and current_revision is not None and int(v) != int(current_revision):
            return {"error": "revision_conflict", "current_revision": current_revision}

        try:
            result = await asyncio.to_thread(
                aggregate_features, fc, group_by=group_by, agg=agg, agg_field=agg_field,
                bbox=window_bbox, where=where_ast, max_groups=max_groups,
            )
        except AggregateOverLimit as e:
            return {"error": "aggregate_over_limit", "feature_count": e.feature_count}
        except FeaturePageError as e:
            return {"error": f"参数非法: {e}"}
        # 取数后复查（同 features 工具 —— 双查对称）。
        live_descriptor = await session_data_manager.get_ref_descriptor(session_id, resolved)
        live_revision = live_descriptor.get("content_revision") if live_descriptor else None
        if current_revision is not None and live_revision is not None and int(live_revision) != int(current_revision):
            return {"error": "revision_conflict", "current_revision": live_revision}

        reason_codes = ["aggregate_full_scan"]
        if window_bbox is not None:
            reason_codes.append("bbox_coarse")
        if where_ast is not None:
            reason_codes.append("filter_applied")
        if result["truncated"]:
            reason_codes.append("groups_truncated")
        return {
            "rows": result["rows"],
            "group_count": result["group_count"],
            "is_complete": result["is_complete"],
            "truncated": result["truncated"],
            "total_matching": result["total_matching"],
            "null_key_count": result["null_key_count"],
            "non_numeric_count": result["non_numeric_count"],
            "agg": agg,
            "agg_field": agg_field,
            "group_by": group_by,
            "revision": current_revision,
            "feature_count": descriptor.get("feature_count") if descriptor else None,
            "reason_codes": reason_codes,
            "scope_note": (
                "聚合按 filter×bbox 全扫口径；truncated=true 时 __other__ 行合并了"
                "溢出组 —— 统计表述必须带上该披露"
            ),
        }

    @tool(
        registry,
        name="webgis_layer_feature_detail",
        capabilities=["layer_browse"],
        tier=1,
        domains=["dataset"],
        description=(
            "按 id 取单要素详情（几何+属性）。链内稳定 id 寻址（id/OBJECTID/"
            "fid/osm_id/@id/featureId/feature_id）；MVT 无稳定 id 数据集用 "
            "id_mode='positional'（位置 id 仅当前数据版本内稳定）。"
        ),
        args_model=LayerFeatureDetailArgs,
        side_effect="cacheable_read",
        deterministic=True,
        latency_class="fast",
        memory_class="light",
        scale_class="large",
        tags=("要素详情", "单要素", "feature", "detail"),
        output_semantic_type="json",
        result_size_policy="inline_small",
    )
    async def webgis_layer_feature_detail(
        ref_id: str,
        feature_id: str,
        id_mode: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> dict:
        from app.services.feature_pages import resolve_feature_stable_id

        if not session_id:
            return {"error": "Missing session_id context"}
        if id_mode is not None and id_mode != "positional":
            return {"error": "id_mode 仅支持 'positional'"}
        resolved, _descriptor, fc = await _resolve_ref_fc(session_id, ref_id)
        if "error" in fc:
            return fc
        features = fc.get("features", [])
        if id_mode == "positional":
            try:
                pos = int(feature_id)
            except ValueError:
                return {"error": "位置 id 必须是整数（FC 自然索引+1）"}
            if pos < 1 or pos > len(features):
                return {"error": "位置 id 越界"}
            f = features[pos - 1]
            if resolve_feature_stable_id(f) is not None:
                return {
                    "error": "该要素存在链内稳定 id，请用默认寻址",
                    "hint": "位置 id 与链内 id 是两个命名空间，不混用",
                }
            out = dict(f) if isinstance(f, dict) else {"type": "Feature"}
            out.setdefault("id", pos)
            return {"feature": out, "id_mode": "positional", "scope_note": "位置 id 仅当前 revision 内稳定"}
        for f in features:
            if resolve_feature_stable_id(f) == str(feature_id):
                return {"feature": f, "id_mode": "native"}
        return {"error": "feature_not_found", "feature_id": feature_id}
