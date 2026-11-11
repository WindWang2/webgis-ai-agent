"""G24 / P6：权威拾取与选择集检查的 Agent 工具面。

工具语义（reviewer 重点）：
- 只接受**服务端认可的** layer/source ref（会话归属校验）与 selection
  handle（三重绑定 + revision 一致性）—— 不是渲染面命中集合的透传；
- refs-only 返回：count/completeness/revision/refs 与有界 id/属性投影；
  **绝不把全部 geometry 坐标返回给模型**（大要素内存/上下文爆炸）；
- 纯读（cacheable_read）：不创建/释放句柄（那是用户交互生命周期），
  不做任何 mutation。
"""

from typing import Any, Dict, List, Optional

from app.services.selection_handles import (
    SelectionHandleError,
    handle_store,
    scope_summary,
    selection_features_page,
)
from app.services.session_data import session_data_manager
from app.tools.registry import ToolRegistry, tool

_MAX_PROPS = 24
_MAX_PAGE = 200
_MAX_GEOM_SUMMARY_VERTICES = 100_000  # 计数护栏：超大几何只报 truncated 顶点数


def _geom_summary(geometry: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """几何摘要（类型/部件数/顶点数/bbox）—— 不返回坐标。"""

    def _count(node: Any) -> int:
        if isinstance(node, (list, tuple)):
            if node and all(isinstance(v, (int, float)) for v in node):
                return 1
            return sum(_count(v) for v in node)
        return 0

    def _bbox(node: Any, acc: List[float]) -> None:
        if isinstance(node, (list, tuple)):
            if len(node) >= 2 and all(isinstance(v, (int, float)) for v in node[:2]):
                acc[0] = min(acc[0], node[0]); acc[1] = min(acc[1], node[1])
                acc[2] = max(acc[2], node[0]); acc[3] = max(acc[3], node[1])
                return
            for v in node:
                _bbox(v, acc)

    if not isinstance(geometry, dict):
        return {"present": False}
    out: Dict[str, Any] = {"present": True, "type": geometry.get("type")}
    verts = _count(geometry.get("coordinates"))
    out["vertex_count"] = verts if verts <= _MAX_GEOM_SUMMARY_VERTICES else _MAX_GEOM_SUMMARY_VERTICES
    out["vertex_count_truncated"] = verts > _MAX_GEOM_SUMMARY_VERTICES
    bbox_acc = [float("inf"), float("inf"), float("-inf"), float("-inf")]
    _bbox(geometry.get("coordinates"), bbox_acc)
    if all(v != float("inf") and v != float("-inf") for v in bbox_acc):
        out["bbox"] = [round(v, 6) for v in bbox_acc]
    return out


def _bounded_props(properties: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(properties, dict):
        return {}
    out: Dict[str, Any] = {}
    for i, (k, v) in enumerate(properties.items()):
        if i >= _MAX_PROPS:
            out["__props_truncated__"] = True
            break
        if isinstance(v, (int, float, bool)) or v is None:
            out[k] = v
        elif isinstance(v, str):
            out[k] = v[:120]
        else:
            out[k] = f"<{type(v).__name__}>"
    return out


async def _authorized_features(session_id: str, ref_id: str):
    """会话内 ref → FC features（与 layer 路由同一授权通道）。"""
    resolved = await session_data_manager.resolve_alias(session_id, ref_id)
    if resolved:
        ref_id = resolved
    res = await session_data_manager.get_ref_data_shared(session_id, ref_id)
    if not res.success:
        return None, None
    data = res.data
    fc = data.get("geojson") if isinstance(data, dict) else None
    if not isinstance(fc, dict) or fc.get("type") != "FeatureCollection":
        fc = data if isinstance(data, dict) and data.get("type") == "FeatureCollection" else None
    descriptor = await session_data_manager.get_ref_descriptor(session_id, ref_id)
    revision = descriptor.get("content_revision") if descriptor else None
    return (fc.get("features") or [] if fc else []), revision


def register_selection_tools(registry: ToolRegistry) -> None:
    """注册 G24 权威拾取/选择集检查工具族。"""

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="webgis_pick_detail",
        side_effect="cacheable_read",
        network=False,
        deterministic=True,
        tags=["拾取", "pick", "要素详情", "MVT", "权威回查"],
        capabilities=["dataset_ingest"],
        latency_class="fast",
        memory_class="light",
        result_size_policy="inline_small",
        description=(
            "按稳定要素 id 权威回查单个要素详情（服务端完整数据，不是瓦片裁剪/简化几何）："
            "有界属性投影 + 几何摘要（类型/顶点数/bbox，不含坐标）+ revision + 完整性。"
            "用于核实用户在地图上选中的对象、编辑前的目标确认。"
            "ref 必须属于当前会话；id 缺稳定链（_wfid/id/OBJECTID/…）→ not_found。"
        ),
        param_descriptions={
            "ref_id": "图层 ref（ref:…，服务端校验会话归属）",
            "feature_id": "稳定要素 id（拾取回传的字符串 id）",
        },
    )
    async def webgis_pick_detail(
        ref_id: str,
        feature_id: str,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not session_id:
            return {"error": "session_required", "message": "缺少 session_id"}
        from app.services.selection_handles import _chain_id_of

        features, revision = await _authorized_features(session_id, ref_id)
        if features is None:
            return {"error": "ref_not_found", "message": f"ref 不存在或无权访问: {ref_id}"}
        target = None
        for f in features:
            if _chain_id_of(f) == str(feature_id):
                target = f
                break
        if target is None:
            return {
                "error": "feature_not_found",
                "message": f"要素 {feature_id} 不在 {ref_id} 的当前 revision 中（数据可能已更新）",
                "revision": revision,
            }
        geom = target.get("geometry") if isinstance(target, dict) else None
        return {
            "found": True,
            "count": 1,
            "completeness": "exact",
            "ref": ref_id,
            "feature_id": str(feature_id),
            "revision": revision,
            "properties": _bounded_props(target.get("properties")),
            "geometry_summary": _geom_summary(geom),
        }

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="webgis_selection_inspect",
        side_effect="cacheable_read",
        network=False,
        deterministic=True,
        tags=["选择集", "selection", "框选", "handle", "计数"],
        capabilities=["dataset_ingest"],
        latency_class="fast",
        memory_class="light",
        result_size_policy="inline_small",
        description=(
            "检查服务端选择集句柄（用户框选/条件选择的权威全集）：count/completeness/"
            "revision/scope 摘要 + 可分页枚举有界 id 列表（不返回几何）。"
            "handle 失效（数据更新 revision 前进 / 过期）→ typed stale 提示重建选择；"
            "handle 不属于该会话/ref → not_found（不泄露存在性）。"
            "Agent 不得把未核实的渲染面命中集当全集 —— 需要全集时用本工具。"
        ),
        param_descriptions={
            "ref_id": "选择集所属图层 ref（ref:…）",
            "handle": "服务端选择句柄（选择事件携带）",
            "page_size": "id 枚举页大小（≤200，缺省 50；只报 id 不报几何）",
            "cursor": "续页游标（首页缺省）",
        },
    )
    async def webgis_selection_inspect(
        ref_id: str,
        handle: str,
        page_size: int = 50,
        cursor: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not session_id:
            return {"error": "session_required", "message": "缺少 session_id"}
        resolved = await session_data_manager.resolve_alias(session_id, ref_id)
        if resolved:
            ref_id = resolved
        try:
            entry = handle_store.get(session_id, ref_id, handle)
        except SelectionHandleError as exc:
            return {
                "error": f"selection_{exc.code}",
                "message": exc.message,
                **({"current_revision": exc.detail["current_revision"]} if exc.code == "stale" else {}),
            }
        # revision 一致性（数据刷新 → 旧 handle 描述的全集不再可信）
        descriptor = await session_data_manager.get_ref_descriptor(session_id, ref_id)
        current_revision = descriptor.get("content_revision") if descriptor else None
        if (entry.revision is not None and current_revision is not None
                and int(entry.revision) != int(current_revision)):
            return {
                "error": "selection_stale",
                "message": "数据已更新（revision 前进），选择集句柄失效；请让用户重新框选",
                "handle_revision": entry.revision,
                "current_revision": current_revision,
            }
        res = await session_data_manager.get_ref_data_shared(session_id, ref_id)
        if not res.success:
            return {"error": "ref_not_found", "message": f"ref 不存在或无权访问: {ref_id}"}
        data = res.data
        fc = data.get("geojson") if isinstance(data, dict) else None
        if not isinstance(fc, dict) or fc.get("type") != "FeatureCollection":
            fc = data if isinstance(data, dict) and data.get("type") == "FeatureCollection" else None
        if not fc:
            return {"error": "ref_not_found", "message": "payload is not a FeatureCollection"}
        limit = max(1, min(int(page_size), _MAX_PAGE))
        try:
            page = await selection_features_page(
                entry, fc, limit=limit, cursor=cursor, fields=None, order=None)
        except SelectionHandleError as exc:
            return {"error": f"selection_{exc.code}", "message": exc.message}
        except Exception as exc:  # cursor 非法等
            return {"error": "bad_cursor", "message": str(exc)[:200]}
        from app.services.selection_handles import _chain_id_of

        ids: List[str] = []
        positional = 0
        for f in page["features"]:
            sid = _chain_id_of(f)
            if sid is not None:
                ids.append(sid)
            else:
                positional += 1
        out: Dict[str, Any] = {
            "handle": entry.handle,
            "ref": ref_id,
            "count": entry.count,
            "count_exact": entry.count_exact,
            "completeness": "exact" if entry.count_exact else "unknown_over_limit",
            "revision": entry.revision,
            "scope": scope_summary(entry.scope),
            "ids_page": ids,
            "ids_positional_count": positional,
            "page": {
                "returned": page.get("returned", len(page["features"])),
                "has_more": page.get("has_more", False),
                "next_cursor": page.get("next_cursor"),
            },
            "reason_codes": page.get("reason_codes", []),
        }
        if entry.missing_ids:
            out["missing_ids"] = entry.missing_ids
        return out
