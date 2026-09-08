"""Tool-layer wiring tests（审计 F-3 / F-1 · A2）。

覆盖：

- ``stats.h3_hotspot``：tool_candidates 改指真实 Gi* 实现 hotspot_analysis
  （旧值 h3_lisa 只产 LISA 标签，不产 Gi* z/p/q —— 声明与接线错位）；
- ``h3_lisa`` / ``st_dbscan`` 工具：补挂 ``scientific_evidence`` 块
  （此前科学元数据整体缺失）；
- ``hotspot_analysis`` normal（默认）路径：同样挂科学证据块。

工具经 ToolRegistry.dispatch 调用（asyncio_mode=auto）。
"""
import pytest

from app.lib.gis.algorithm_registry import get_algorithm_registry
from app.tools.registry import ToolRegistry
from app.tools.spatial_stats import register_spatial_stats_tools

pytestmark = pytest.mark.unit


@pytest.fixture()
def registry():
    reg = ToolRegistry()
    register_spatial_stats_tools(reg)
    return reg


def _h3_grid_fc():
    import h3

    cells = h3.geo_to_cells(
        {"type": "Polygon",
         "coordinates": [[[116.36, 39.87], [116.46, 39.87],
                          [116.46, 39.95], [116.36, 39.95],
                          [116.36, 39.87]]]},
        8,
    )
    feats = []
    for i, c in enumerate(cells):
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [
                [(lng, lat) for lat, lng in h3.cell_to_boundary(c)]
            ]},
            "properties": {"h3_index": c,
                           "val": float((i * 37) % 11 + 1.0)},
        })
    return {"type": "FeatureCollection", "features": feats}


def _st_fc():
    from datetime import datetime, timedelta, timezone

    base = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
    feats = []
    for i in range(6):
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point",
                         "coordinates": [116.458 + i * 0.0001,
                                         39.908 + i * 0.0001]},
            "properties": {"id": f"c1_{i}",
                           "timestamp": (base + timedelta(
                               minutes=i * 2)).isoformat(),
                           "value": 10.0 + i},
        })
    return {"type": "FeatureCollection", "features": feats}


def _point_fc():
    feats = []
    for i in range(36):
        r, c = divmod(i, 6)
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point",
                         "coordinates": [116.0 + c * 0.01,
                                         39.0 + r * 0.01]},
            "properties": {"val": 100.0 if (r < 3 and c < 3) else 1.0},
        })
    return {"type": "FeatureCollection", "features": feats}


def test_h3_hotspot_descriptor_wired_to_real_gistar():
    """审计 F-1（A2）：stats.h3_hotspot 的 tool_candidates 必须指向真实
    产出 Gi* z/p/q_value_fdr 的工具（hotspot_analysis），而非只产 LISA
    标签的 h3_lisa。"""
    desc = get_algorithm_registry().get("stats.h3_hotspot")
    assert desc is not None
    assert "h3_lisa" not in desc.tool_candidates
    assert "hotspot_analysis" in desc.tool_candidates
    # 证据块声明的 statistical_significance 由实现层真实产出（同族锚）
    hotspot_desc = get_algorithm_registry().get("spatial.hotspot.local")
    assert hotspot_desc.uncertainty_producer_tests.get(
        "statistical_significance")


async def test_h3_lisa_tool_attaches_scientific_evidence(registry):
    """审计 F-3：h3_lisa 工具挂 scientific_evidence + uncertainty 通道。"""
    payload = await registry.dispatch(
        "h3_lisa", {"h3_geojson": _h3_grid_fc(), "value_field": "val"})
    assert payload.get("success") is True, payload.get("summary")
    ev = payload.get("scientific_evidence")
    assert ev and ev.get("algorithm") == "stats.h3_lisa"
    unc = ev.get("uncertainty") or []
    assert any(u.get("uncertainty_type") == "statistical_significance"
               and u.get("method") == "permutation"
               and u.get("permutations") == 999 for u in unc)
    # 逐格 BH q（additive）随要素输出
    props = payload["data"]["features"][0]["properties"]
    assert "q_value_fdr" in props


async def test_st_dbscan_tool_attaches_scientific_evidence(registry):
    """审计 F-3：st_dbscan 工具补挂科学元数据（无 uncertainty 声明 →
    uncertainty 列表为空，证据块本身必须在）。"""
    payload = await registry.dispatch("st_dbscan", {
        "geojson": _st_fc(),
        "eps1_spatial_meters": 1000.0,
        "eps2_temporal_seconds": 3600.0,
        "min_samples": 5,
        "timestamp_field": "timestamp",
    })
    assert payload.get("success") is True, payload.get("summary")
    ev = payload.get("scientific_evidence")
    assert ev and ev.get("algorithm") == "stats.st_dbscan"
    assert ev.get("parameters_applied", {}).get("min_samples") == 5


async def test_hotspot_tool_normal_path_attaches_evidence(registry):
    """审计 F-3：hotspot_analysis 默认（normal）路径挂科学证据块。"""
    payload = await registry.dispatch("hotspot_analysis", {
        "geojson": _point_fc(), "value_field": "val",
        "distance_band": 1500.0,
    })
    assert payload.get("success") is True, payload.get("summary")
    ev = payload.get("scientific_evidence")
    assert ev and ev.get("algorithm") == "spatial.hotspot.local"
    assert ev.get("parameters_applied", {}).get(
        "significance_method") == "normal"
    unc = ev.get("uncertainty") or []
    assert any(u.get("uncertainty_type") == "statistical_significance"
               and u.get("method") == "analytic_normal" for u in unc)
