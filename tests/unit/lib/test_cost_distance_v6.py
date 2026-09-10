"""Conformance tests for the least-cost surface family (Science V6).

Contract bullets under test (descriptors ``terrain.cost_distance`` /
``terrain.least_cost_path`` in app/lib/gis/algorithms/terrain.py):

- uniform friction → closed-form accumulated cost (straight = c·d,
  diagonal = c·√2·d);
- full-grid equivalence vs networkx Dijkstra with identical 8-neighbour
  mean-friction edge weights (typed reference cross-check);
- high-friction barrier forces a detour; drain path is strictly
  cost-decreasing and terminates at a zero-cost source;
- nodata region is impassable → NaN + disclosed unreachable count;
- zero/negative friction is a typed DegenerateData rejection;
- determinism: identical surfaces on repeated runs.
"""
import numpy as np
import pytest

from app.lib.geo_analysis.cost_surface import (
    cost_distance,
    least_cost_path,
)
from app.lib.gis.scientific_errors import (
    DegenerateData,
    NoValidObservations,
    ResourceScaleMismatch,
)

pytestmark = pytest.mark.unit


def test_cost_distance_uniform_closed_form():
    """均匀摩擦 5×5、源在角上：对角端点 = 2·10·4·√2（闭合式）。"""
    cost = np.full((5, 5), 2.0)
    surface, meta = cost_distance(cost, [(0, 0)], cell_size=10.0)
    assert surface[4, 4] == pytest.approx(2.0 * 10.0 * 4.0 * np.sqrt(2.0))
    assert surface[0, 4] == pytest.approx(2.0 * 10.0 * 4.0)
    assert meta["algorithm"] == "dijkstra_8n_mean_friction"
    assert meta["reachable"] == 25
    assert meta["unreachable"] == 0
    assert meta["n_source_cells"] == 1
    assert surface[0, 0] == 0.0


def test_cost_distance_matches_networkx_dijkstra():
    """随机摩擦面 == networkx Dijkstra（同 8 邻接平均摩擦边权，<1e-9）。"""
    nx = pytest.importorskip("networkx")

    rng = np.random.default_rng(3)
    cost = rng.uniform(0.5, 3.0, size=(8, 8))
    surface, _ = cost_distance(cost, [(0, 0)], cell_size=1.0)

    h, w = cost.shape
    graph = nx.Graph()
    for r in range(h):
        for c in range(w):
            for dr, dc in ((-1, -1), (-1, 0), (-1, 1), (0, -1),
                           (0, 1), (1, -1), (1, 0), (1, 1)):
                nr, nc = r + dr, c + dc
                if 0 <= nr < h and 0 <= nc < w:
                    step = np.hypot(1.0, 1.0) if (dr and dc) else 1.0
                    graph.add_edge(
                        (r, c), (nr, nc),
                        weight=(cost[r, c] + cost[nr, nc]) * 0.5 * step)
    ref = nx.single_source_dijkstra_path_length(graph, (0, 0))
    ref_arr = np.array([[ref[(r, c)] for c in range(w)] for r in range(h)])
    assert np.abs(surface - ref_arr).max() < 1e-9


def test_cost_distance_barrier_detour():
    """高摩擦中列迫使绕行；排水路径单调下降并终止于源。"""
    cost = np.ones((7, 7))
    cost[:, 3] = 50.0
    surface, _ = cost_distance(cost, [(0, 0)], cell_size=1.0)
    # 直接穿列（1+50+1 ≈ 对角均摊）贵于绕行 —— 断言对面成本高于无障情形
    assert surface[0, 6] > 6.0
    path, meta = least_cost_path(surface, 0, 6)
    assert path[-1] == (0, 0)
    assert meta["total_cost"] == pytest.approx(float(surface[0, 6]), abs=1e-6)
    # 路径严格成本下降（源端点 = 0 终止）
    vals = [float(surface[r, c]) for r, c in path]
    assert all(a > b for a, b in zip(vals[:-1], vals[1:]))
    assert vals[-1] == 0.0


def test_least_cost_path_drain_monotone():
    """均匀面上路径 = 几何最优；无障碍时 drain 走对角直线。"""
    surface, _ = cost_distance(np.ones((6, 6)), [(0, 0)], cell_size=2.0)
    path, meta = least_cost_path(surface, 5, 5)
    # 6×6 均匀面：从 (5,5) 回源 = 5 步对角
    assert len(path) == 6
    assert meta["n_cells"] == 6
    # meta 的 total_cost 舍入到 6 位小数（确定性 meta 契约）
    assert meta["total_cost"] == pytest.approx(
        float(np.hypot(10.0, 10.0)), abs=1e-5)


def test_least_cost_path_typed_errors():
    surface, _ = cost_distance(np.ones((4, 4)), [(0, 0)], cell_size=1.0)
    with pytest.raises(ValueError, match="outside grid"):
        least_cost_path(surface, 4, 0)
    nan_surface = surface.copy()
    nan_surface[3, 3] = np.nan
    with pytest.raises(NoValidObservations, match="unreachable"):
        least_cost_path(nan_surface, 3, 3)
    with pytest.raises(NoValidObservations, match="stall|stuck"):
        # 平滑面（非 Dijkstra 产物）：局部极小 → 停滞
        least_cost_path(np.sin(np.linspace(0, 3, 36)).reshape(6, 6) * 5 + 5,
                        5, 5)


def test_cost_distance_unreachable_and_zero_cost_rejection():
    """nodata 隔离区不可达（NaN + 计数披露）；零摩擦类型化拒绝。"""
    cost = np.ones((5, 5))
    cost[:, 2] = np.nan
    surface, meta = cost_distance(cost, [(0, 0)], cell_size=1.0)
    assert np.isnan(surface[:, 3:]).all()
    assert meta["unreachable"] == 10
    assert meta["invalid_cells"] == 5
    with pytest.raises(DegenerateData, match="positive"):
        cost_distance(np.zeros((3, 3)), [(0, 0)], cell_size=1.0)
    # 源点在网格外是调用方错误（ValueError）；全部源无效是类型化空观测。
    with pytest.raises(ValueError, match="outside grid"):
        cost_distance(np.ones((3, 3)), [(9, 9)], cell_size=1.0)
    with pytest.raises(NoValidObservations):
        cost_distance(np.full((3, 3), np.nan), [(0, 0)], cell_size=1.0)
    with pytest.raises(ResourceScaleMismatch):
        # 硬顶护栏：超大网格在分配前拒绝（用小构造验证类型）
        big = np.ones((1, 50_000_001))
        cost_distance(big, [(0, 0)], cell_size=1.0)


def test_cost_distance_determinism():
    rng = np.random.default_rng(11)
    cost = rng.uniform(0.5, 2.0, size=(9, 9))
    a, meta_a = cost_distance(cost, [(0, 0), (8, 8)], cell_size=1.5,
                              cell_size_x=1.0)
    b, meta_b = cost_distance(cost, [(0, 0), (8, 8)], cell_size=1.5,
                              cell_size_x=1.0)
    assert np.array_equal(a, b, equal_nan=True)
    assert meta_a == meta_b


# ── Tool wiring（组合链：摩擦面 → 累积面 → 路径）──────────────────────


def _write_tif(path, arr, transform, crs=None, nodata=None):
    import rasterio
    from rasterio.transform import Affine

    with rasterio.open(
        str(path), "w", driver="GTiff", height=arr.shape[0],
        width=arr.shape[1], count=1, dtype="float64", crs=crs,
        transform=Affine(*transform), nodata=nodata,
    ) as dst:
        dst.write(arr.astype("float64"), 1)


@pytest.fixture()
def terrain_tools(monkeypatch):
    from app.tools.registry import ToolRegistry
    from app.tools import terrain_analysis as ta

    monkeypatch.setattr(ta, "validate_data_path", lambda p: str(p))
    registry = ToolRegistry()
    ta.register_terrain_tools(registry)
    return registry


def test_cost_distance_tool_chain(tmp_path, terrain_tools):
    """组合链端到端：摩擦 GeoTIFF + 源点 FC → 累积面产物 → 路径工具消费。"""
    import json

    n = 8
    cell = 10.0
    # 原点 (500000, 5000080)，行向南下（像 terrain 域 _bowl_tif 约定）
    transform = (cell, 0.0, 500000.0, 0.0, -cell, 5000080.0)
    friction = np.full((n, n), 2.0)
    friction[:, 4] = 40.0  # 高摩擦列 → 绕行
    tif = tmp_path / "friction.tif"
    _write_tif(tif, friction, transform, crs="EPSG:32650")

    sources = {"type": "FeatureCollection", "features": [{
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [500005.0, 5000075.0]},
        "properties": {},
    }]}  # 像元 (0,0)

    payload = terrain_tools._tools["cost_distance_analysis"](
        str(tif), json.dumps(sources), nodata=0)
    assert payload["success"] is True
    assert "scientific_evidence" in payload
    assert payload["cost_metadata"]["n_source_cells"] == 1
    out_path = payload["accumulated_raster_path"]
    assert out_path.endswith("_costdist.tif")

    # 地理参考往返：产物栅格的 transform/bounds 与源一致（防 from_gdal
    # 错位解析回归 —— 既有 _persist_filled_dem 同款缺陷已在本分支修复）。
    import rasterio

    with rasterio.open(str(tif)) as src, rasterio.open(out_path) as out:
        assert out.transform.almost_equals(src.transform)
        assert out.shape == src.shape
        assert str(out.crs) == str(src.crs)

    # 路径工具直接消费上一步产物（组合链不重算）
    target_x = 500000.0 + 7.5 * cell
    target_y = 5000080.0 - 7.5 * cell
    path_payload = terrain_tools._tools["least_cost_path_analysis"](
        out_path, target_x, target_y)
    assert path_payload["success"] is True
    assert "scientific_evidence" in path_payload
    feat = path_payload["features"][0]
    assert feat["geometry"]["type"] == "LineString"
    assert len(feat["geometry"]["coordinates"]) >= 8
    assert feat["properties"]["total_cost"] == pytest.approx(
        path_payload["path_metadata"]["total_cost"], rel=1e-6)
