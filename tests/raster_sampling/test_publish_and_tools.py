"""G39 发布抽稀（S27 曲线事实）+ 工具面注册合同。"""
from __future__ import annotations


from app.services.raster_sampling.publish import (
    build_display_curves,
    load_rows_for_curves,
    thin_series,
)


def test_load_rows_for_curves_streams_row_groups(tmp_path, monkeypatch):
    """P2 perf：曲线原料按行组分批读，不再 read_table 整表载入。"""
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.table({
        "sample_id": [f"s{i}" for i in range(5)],
        "source_ref": ["ref:x"] * 5,
        "band": [1, 1, 1, 2, 2],
        "chainage_m": [0.0, 1.0, 2.0, 0.0, 1.0],
        "station_index": [0, 1, 2, 3, 4],
        "physical_value": [10.0, 11.0, 12.0, 20.0, 21.0],
        "interpolated_value": [None] * 5,
        "raw_value": [10.0, 11.0, 12.0, 20.0, 21.0],
        "valid": [True] * 5,
        "part_id": ["p0"] * 5,
    })
    path = tmp_path / "curves.parquet"
    pq.write_table(table, path, row_group_size=2)

    def _whole_table_read(*args, **kwargs):
        raise AssertionError(
            "load_rows_for_curves must stream row groups, not read_table")

    monkeypatch.setattr(pq, "read_table", _whole_table_read)
    rows = load_rows_for_curves(str(path))
    assert [r["sample_id"] for r in rows] == ["s0", "s1", "s2", "s3", "s4"]
    assert rows[3]["band"] == 2


def test_thin_series_keeps_endpoints_gaps_extrema():
    """S27：抽稀保端点 / 缺口边界 / 局部极值；事实披露。"""
    # 2000 点线性 + 一个尖峰 + 中部缺口
    pts = []
    for i in range(1000):
        y = 1.0 if i != 500 else 99.0  # 尖峰（局部极大）
        pts.append({"x": float(i), "y": y, "sid": f"s{i}", "part": "p0",
                    "gap_before": False})
    pts[600]["y"] = None  # 无效站位（缺口）
    pts[601]["gap_before"] = True
    thinned, facts = thin_series(pts, limit=64)
    assert facts["method"] == "endpoint_gap_extrema_uniform"
    assert facts["kept"] == len(thinned) <= 64
    assert facts["total"] == 1000
    xs = [p["x"] for p in thinned]
    assert 0.0 in xs and 999.0 in xs           # 端点
    assert 500.0 in xs                          # 尖峰极值
    for i in (599, 600, 601, 602):              # 缺口边界保留
        assert float(i) in xs
    # 抽稀后缺口语义仍在（y=None 站位保留）
    assert any(p["y"] is None for p in thinned)


def test_thin_series_noop_under_limit():
    pts = [{"x": float(i), "y": float(i), "sid": f"s{i}", "part": "p0",
            "gap_before": False} for i in range(10)]
    out, facts = thin_series(pts, limit=64)
    assert out == pts
    assert facts["method"] == "none"


def test_build_display_curves_gap_breaks_between_parts():
    """part 断口在曲线上体现为 gap 标记（不跨空间跳跃连线）。"""
    rows = []
    for i in range(4):
        rows.append({"sample_id": f"a{i}", "source_ref": "ref:x", "band": 1,
                     "chainage_m": float(i), "station_index": i,
                     "physical_value": 10.0 + i, "interpolated_value": None,
                     "raw_value": 10.0 + i, "valid": True, "part_id": "part-0"})
    for i in range(4):
        rows.append({"sample_id": f"b{i}", "source_ref": "ref:x", "band": 1,
                     "chainage_m": float(i), "station_index": 4 + i,
                     "physical_value": 50.0 + i, "interpolated_value": None,
                     "raw_value": 50.0 + i, "valid": True, "part_id": "part-1"})
    curves = build_display_curves(rows)
    band = curves["bands"][0]
    pts = band["points"]
    gaps = [p for p in pts if p["gap"]]
    assert len(gaps) == 1  # part-1 首点带 gap（断口）
    assert gaps[0]["sid"] == "b0"


# ── 工具面注册合同（真实 registry） ────────────────────────────────

def test_tools_registered_with_contracts():
    from app.tools.registry import ToolRegistry
    from app.tools.raster_sampling_tools import register_raster_sampling_tools

    registry = ToolRegistry()
    register_raster_sampling_tools(registry)
    names = set(registry.list_tools())
    expected = {
        "webgis_raster_sampling_plan",
        "webgis_raster_sampling_preview",
        "webgis_raster_sampling_execute",
        "webgis_raster_sampling_status",
        "webgis_raster_sampling_results",
    }
    assert expected <= names
    for name in expected:
        assert registry.has(name), name
        meta = registry.metadata(name)
        assert meta.get("network", None) is False
        assert meta.get("tier") == 2
        assert meta.get("side_effect") in {
            "cacheable_read", "deterministic_compute", "state_mutation",
            "artifact_creation"}


def test_tool_plan_rejects_foreign_session():
    """模型传他人 session_id → _resolve_session_id 语义（无 runtime ctx 时
    直通 —— 由集成/Pi 旅程覆盖注入路径；单测锁定参数合同存在）。"""
    from app.tools.registry import ToolRegistry
    from app.tools.raster_sampling_tools import register_raster_sampling_tools

    registry = ToolRegistry()
    register_raster_sampling_tools(registry)
    fn = registry.get("webgis_raster_sampling_execute")
    assert fn is not None
    import inspect

    params = inspect.signature(fn).parameters
    assert "plan_id" in params and "session_id" in params


def test_build_display_curves_part_order_is_natural():
    """#2229: part-2 必须排在 part-10 之前（纯字典序 "part-10" < "part-2"），
    且 part 切换的 gap 断口语义保持不变。"""
    rows = []
    for part, sid in (("part-10", "p10a"), ("part-2", "p2a")):
        rows.append({"sample_id": sid, "source_ref": "ref:x", "band": 1,
                     "chainage_m": 0.0, "station_index": 0,
                     "physical_value": 1.0, "interpolated_value": None,
                     "raw_value": 1.0, "valid": True, "part_id": part})
    curves = build_display_curves(rows)
    pts = curves["bands"][0]["points"]
    assert [p["part"] for p in pts] == ["part-2", "part-10"]
    assert pts[0]["gap"] is False
    assert pts[1]["gap"] is True  # part 切换断口
