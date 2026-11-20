"""G33 测试助手：GeoJSON fixture / 会话 ref 注册 / 生产式专题层落库。"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from app.lib.cartography.thematic_spec import build_graduated_spec


def make_fc(rows: Iterable[Dict[str, Any]], *, geom: str = "Polygon") -> Dict[str, Any]:
    """行字典 → FeatureCollection（坐标按索引生成合法小面/点/线）。"""
    features = []
    for i, props in enumerate(rows):
        if geom == "Point":
            g: Dict[str, Any] = {"type": "Point", "coordinates": [116.0 + i * 0.01, 39.0]}
        elif geom == "LineString":
            g = {"type": "LineString", "coordinates": [[116.0 + i * 0.01, 39.0], [116.01 + i * 0.01, 39.01]]}
        else:
            base = 116.0 + i * 0.01
            g = {"type": "Polygon", "coordinates": [[
                [base, 39.0], [base + 0.01, 39.0], [base + 0.01, 39.01], [base, 39.01], [base, 39.0],
            ]]}
        features.append({"type": "Feature", "geometry": g, "properties": dict(props)})
    return {"type": "FeatureCollection", "features": features}


async def register_geojson_ref(session_id: str, fc: Dict[str, Any]) -> str:
    from app.services.session_data import session_data_manager

    ref_id = await session_data_manager.store(
        session_id, {"kind": "geojson", "geojson": fc}, prefix="result",
    )
    return str(ref_id)


async def seed_thematic_layer(
    session_id: str,
    fc: Dict[str, Any],
    *,
    layer_id: str = "result-seed",
    field: str = "pop",
    ref_id: Optional[str] = None,
    layer_type: str = "fill",
) -> str:
    """生产式建层：注册 ref → build_graduated_spec → 单笔 UpsertLayerIntent
    （source_data 为 ref 元数据载体，与 dispatch 摄取路径一致）。"""
    from app.services.gis_world_state.mutation import apply_gis_mutation
    from app.services.mapspec.intents import UpsertLayerIntent

    if ref_id is None:
        ref_id = await register_geojson_ref(session_id, fc)
    legend = build_graduated_spec(fc, field=field)
    assert legend is not None, "种子层必须能构建 graduated 表达"
    from app.lib.cartography.thematic_spec import spec_to_paint

    paint_color, _warn = spec_to_paint(legend)
    layer: Dict[str, Any] = {
        "id": layer_id,
        "type": layer_type,
        "source": f"{layer_id}-source",
        "paint": {"color": paint_color},
        "legend_spec": legend,
        "data_binding": {"field": field, "field_type": "number"},
        "metadata": {"title": f"{field} 专题图"},
    }
    result = await apply_gis_mutation(
        session_id,
        UpsertLayerIntent(layer=layer, source_data={"ref_id": ref_id}),  # type: ignore[arg-type]
        origin="agent", actor="test:seed",
    )
    assert not result.is_error, f"种子层提交失败: {result.error_msg}"
    return layer_id


async def read_layer(session_id: str, layer_id: str) -> Optional[Dict[str, Any]]:
    from app.services.mapspec_store import mapspec_store

    spec = await mapspec_store.get_mapspec(session_id) or {}
    for layer in spec.get("layers") or []:
        if isinstance(layer, dict) and layer.get("id") == layer_id:
            return layer
    return None


async def current_revision(session_id: str) -> int:
    from app.services.session_data import session_data_manager

    raw = await session_data_manager.get_state_field(
        session_id, "_cartographic_mutation_revision")
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


def dirty_rows(n_valid: int = 24, *, field: str = "pop") -> List[Dict[str, Any]]:
    """覆盖验收 6 的脏值行：有限数值 + bool/字符串/NaN/Inf/null/缺失 + 零。"""
    rows: List[Dict[str, Any]] = []
    for i in range(n_valid):
        rows.append({field: float(i)})
    rows.append({field: True})              # bool
    rows.append({field: "12.5"})            # 字符串数字
    rows.append({field: float("nan")})      # NaN
    rows.append({field: float("inf")})      # Inf
    rows.append({field: None})              # null
    rows.append({"other": 1})               # 字段缺失
    rows.append({field: 0})                 # 零是有效值
    return rows
