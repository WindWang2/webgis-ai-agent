"""G45 WP12 — 独立数值 oracle、候选面与维度/轴序/安全合同测试。

oracle 纪律（任务书）：golden 值来自**解析式/公开常数**推导，不由生产
transformer 即时生成；往返测试只作补充。容差与依据逐处注明。
"""
from __future__ import annotations

import math

import pytest

from app.services.coordinate_operations import candidates, execute
from app.services.coordinate_operations.contracts import CoordinateOperationError
from app.services.coordinate_operations.crs_contract import describe_crs
from tests.coordinate_operations.conftest import make_point_features

# ── 独立解析 oracle（WGS84 → UTM 50N / WebMercator）───────────────────────
# 依据：
# - UTM：Karney (2011) Transverse Mercator 精解的公开参考实现常数 —— 测试用
#   独立实现的 Redfearn 级数（k0=0.9996, a=6378137.0, f=1/298.257223563，
#   展开至 4 阶），与 EPSG 公式同源但**非** pyproj 调用；
# - Web Mercator：EPSG:3857 球面公式 x = R·λ，y = R·ln(tan(π/4+φ/2))，
#   R=6378137.0 —— 公开解析式。
_WGS84_A = 6378137.0
_WGS84_F = 1 / 298.257223563
_UTM_K0 = 0.9996


def _utm50n_analytic(lon: float, lat: float) -> tuple[float, float]:
    """Redfearn 级数（独立于 pyproj；4 阶展开，中心经线 117°E，北半球）。"""
    a, f = _WGS84_A, _WGS84_F
    e2 = 2 * f - f * f
    ep2 = e2 / (1 - e2)
    lam = math.radians(lon - 117.0)
    phi = math.radians(lat)
    N = a / math.sqrt(1 - e2 * math.sin(phi) ** 2)
    T = math.tan(phi) ** 2
    C = ep2 * math.cos(phi) ** 2
    A = math.cos(phi) * lam
    M = a * (
        (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256) * phi
        - (3 * e2 / 8 + 3 * e2 ** 2 / 32 + 45 * e2 ** 3 / 1024) * math.sin(2 * phi)
        + (15 * e2 ** 2 / 256 + 45 * e2 ** 3 / 1024) * math.sin(4 * phi)
        - (35 * e2 ** 3 / 3072) * math.sin(6 * phi)
    )
    x = _UTM_K0 * N * (
        A + (1 - T + C) * A ** 3 / 6
        + (5 - 18 * T + T ** 2 + 72 * C - 58 * ep2) * A ** 5 / 120
    ) + 500000.0
    y = _UTM_K0 * (
        M + N * math.tan(phi) * (
            A ** 2 / 2
            + (5 - T + 9 * C + 4 * C ** 2) * A ** 4 / 24
            + (61 - 58 * T + T ** 2 + 600 * C - 330 * ep2) * A ** 6 / 720
        )
    )
    return x, y


def _webmercator_analytic(lon: float, lat: float) -> tuple[float, float]:
    R = _WGS84_A
    return (R * math.radians(lon),
            R * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))


def _frozen_op_for(src: str, dst: str, pick: int = 0, aoi=None):
    listing = candidates.list_operations(src, dst, aoi)
    avail = [c for c in listing["candidates"] if c["availability"] == "available"]
    cid = avail[pick]["candidate_id"]
    locked = candidates.resolve_candidate_for_lock(src, dst, aoi, cid)
    return listing, locked, execute.FrozenOperation(
        locked["operation_projjson"], locked["definition_sha256"],
        locked["environment"])


class TestIndependentOracle:
    """C01/C37：生产实现 vs 独立解析 oracle（容差按 oracle 截断阶注明）。"""

    def test_c01_wgs84_point_to_utm50n_matches_analytic(self):
        _, _, op = _frozen_op_for("EPSG:4326", "EPSG:32650")
        # 容差：Redfearn 4 阶 vs EPSG 精解，在远离边缘的区域内 <1e-4 m 量级；
        # 取 0.01 m（官方控制点精度量级之上、截断误差之下）。
        for lon, lat in [(116.0, 40.0), (116.397, 39.909), (117.0, 0.5),
                         (114.5, 41.2)]:
            got = op.transform_array(
                __import__("numpy").array([lon]),
                __import__("numpy").array([lat]))
            want = _utm50n_analytic(lon, lat)
            assert abs(got[0][0] - want[0]) < 0.01, (lon, lat, got, want)
            assert abs(got[1][0] - want[1]) < 0.01

    def test_c01_utm_central_meridian_equator_known_value(self):
        # 中央经线赤道点：x=500000（假东），y≈0 —— 无穷小解析事实
        _, _, op = _frozen_op_for("EPSG:4326", "EPSG:32650")
        import numpy as np

        x, y = op.transform_array(np.array([117.0]), np.array([0.0]))
        assert abs(x[0] - 500000.0) < 1e-6
        assert abs(y[0]) < 1e-6

    def test_webmercator_analytic(self):
        _, _, op = _frozen_op_for("EPSG:4326", "EPSG:3857")
        for lon, lat in [(0.0, 0.0), (116.0, 40.0), (-45.5, -22.25)]:
            got = op.transform_array(
                __import__("numpy").array([lon]),
                __import__("numpy").array([lat]))
            want = _webmercator_analytic(lon, lat)
            assert abs(got[0][0] - want[0]) < 0.02
            assert abs(got[1][0] - want[1]) < 0.02

    def test_c37_wrong_axis_order_version_is_caught(self):
        """轴序错误版本（lat/lon 互换喂入）必须被 oracle 语义抓出来。

        非对称测试点：两值互换后仍是合法经纬度（55,25 → 25,55），
        但结果偏移远超容差 —— 语义错误不靠范围检查也能被抓出。
        """
        _, _, op = _frozen_op_for("EPSG:4326", "EPSG:32650")
        import numpy as np

        lon, lat = 55.0, 25.0
        got_swapped = op.transform_array(
            np.array([lat]), np.array([lon]))  # 故意交换
        want = _utm50n_analytic(lon, lat)
        assert abs(got_swapped[0][0] - want[0]) > 1000.0  # 显著错位

    def test_c06_foot_units_factor(self):
        """EPSG:2263（US survey foot）与 NAD83 米制同点 —— 单位因子一致。"""
        _, _, op_ft = _frozen_op_for("EPSG:4269", "EPSG:2263")
        import numpy as np

        # NAD83 经纬度 → NY Long Island ftUS
        x_ft, y_ft = op_ft.transform_array(
            np.array([-73.94172]), np.array([40.79335]))
        # 同点米制带 EPSG:6539（NAD83(2011) / NY Long Island ftUS 的米制邻带
        # 不可用 —— 改用直接常数验证：ftUS = m / 0.30480060960121924
        # 用 4269→2263 的输出与 4269→32118（同一投影带米制版）对账
        listing = candidates.list_operations("EPSG:4269", "EPSG:32118", None)
        avail = [c for c in listing["candidates"]
                 if c["availability"] == "available"]
        locked = candidates.resolve_candidate_for_lock(
            "EPSG:4269", "EPSG:32118", None, avail[0]["candidate_id"])
        op_m = execute.FrozenOperation(
            locked["operation_projjson"], locked["definition_sha256"],
            locked["environment"])
        x_m, y_m = op_m.transform_array(
            np.array([-73.94172]), np.array([40.79335]))
        ftus = 0.30480060960121924
        assert abs(x_ft[0] - x_m[0] / ftus) < 0.05  # ftUS 换算一致（<0.05 ft）
        assert abs(y_ft[0] - y_m[0] / ftus) < 0.05


class TestCandidateContract:
    """C07–C11：候选事实、签发与注入面。"""

    def test_c07_multiple_operations_with_different_conditions(self):
        listing = candidates.list_operations("EPSG:4230", "EPSG:4326", None)
        avail = [c for c in listing["candidates"]
                 if c["availability"] == "available"]
        assert len(avail) >= 5  # 实测 35；截断返回 16
        accuracies = {c["accuracy"] for c in avail}
        assert len(accuracies) > 1  # 不同精度条件可对照（C07）
        assert listing["available_truncated"] > 0  # 溢出如实披露
        ids = [c["candidate_id"] for c in avail]
        assert len(set(ids)) == len(ids)  # 签发 id 唯一

    def test_c08_missing_grid_kept_with_reason(self):
        listing = candidates.list_operations("EPSG:4267", "EPSG:4269", None)
        unavail = [c for c in listing["candidates"]
                   if c["availability"] == "unavailable"]
        assert len(unavail) >= 3
        assert all("格网" in (c.get("missing_reason") or "") for c in unavail)
        assert all(c["grids"] for c in unavail)
        assert all(not g["available"] for c in unavail for g in c["grids"])
        # 最佳已知（高精度）不可执行 vs 当前可执行（ballpark noop）同列
        avail = [c for c in listing["candidates"]
                 if c["availability"] == "available"]
        assert avail and avail[0]["ballpark"] is True
        assert avail[0]["accuracy"] is None and avail[0]["accuracy_unknown"]

    def test_c09_unknown_accuracy_never_zero(self):
        listing = candidates.list_operations("EPSG:4267", "EPSG:4269", None)
        for c in listing["candidates"]:
            if c["accuracy_unknown"]:
                assert c["accuracy"] is None

    def test_c11_fake_candidate_id_rejected(self):
        with pytest.raises(CoordinateOperationError) as ei:
            candidates.resolve_candidate_for_lock(
                "EPSG:4326", "EPSG:32650", None, "cand_0000000000000000dead")
        assert ei.value.error_code == "operation_not_in_plan"

    def test_c11_unavailable_candidate_lock_rejected(self):
        listing = candidates.list_operations("EPSG:4267", "EPSG:4269", None)
        unavail = [c for c in listing["candidates"]
                   if c["availability"] == "unavailable"][0]
        with pytest.raises(CoordinateOperationError) as ei:
            candidates.resolve_candidate_for_lock(
                "EPSG:4267", "EPSG:4269", None, unavail["candidate_id"])
        assert ei.value.error_code == "operation_unavailable"

    def test_lock_is_exact_same_operation(self):
        """锁定重建与直接 from_crs 同参数下数值一致（bit-exact 语义）。"""
        from pyproj import CRS, Transformer

        _, locked, op = _frozen_op_for("EPSG:4326", "EPSG:32650")
        ref = Transformer.from_crs(
            CRS.from_epsg(4326), CRS.from_epsg(32650), always_xy=True)
        import numpy as np

        lons = np.array([114.0, 116.0, 118.0, 119.9])
        lats = np.array([1.0, 20.0, 45.0, 83.0])
        gx, gy = op.transform_array(lons, lats)
        rx, ry = ref.transform(lons, lats)
        assert (gx == rx).all() and (gy == ry).all()

    def test_c13_antimeridian_aoi_rejected(self):
        with pytest.raises(CoordinateOperationError) as ei:
            candidates.list_operations(
                "EPSG:4326", "EPSG:32601",
                {"west": 170, "south": 0, "east": -170, "north": 10})
        assert ei.value.error_code == "invalid_spec"

    def test_environment_fingerprint_stable_and_offline(self):
        env1 = candidates.environment_fingerprint()
        env2 = candidates.environment_fingerprint()
        assert env1 == env2
        assert env1["network_enabled"] is False  # 运行时禁自动联网
        assert env1["pyproj_version"] and env1["proj_version"]

    def test_c10_env_drift_detected(self):
        """环境证据漂移 → FrozenOperation 构造即阻断（OPERATION_ENV_CHANGED）。"""
        _, locked, _op = _frozen_op_for("EPSG:4326", "EPSG:32650")
        tampered = dict(locked["environment"])
        tampered["proj_version"] = "9.9.9-fake"
        with pytest.raises(CoordinateOperationError) as ei:
            execute.FrozenOperation(
                locked["operation_projjson"],
                locked["definition_sha256"], tampered)
        assert ei.value.error_code == "operation_env_changed"


class TestCrsContract:
    """C02/C03/C05/C14/C16：CRS 描述、轴序与维度拒绝面。"""

    def test_c02_official_axis_order_disclosed_not_applied(self):
        d = describe_crs("EPSG:4326")
        # 官方轴序 lat-first（展示事实）
        assert d["axis_official"][0]["direction"] == "north"
        assert d["axis_official"][0]["abbrev"].lower() == "lat"
        # 载体约定恒 x=east/y=north —— 不因官方轴序交换（执行链 always_xy）
        assert "always_xy" in d["carrier_convention"]

    def test_c02_geojson_lonlat_not_double_swapped(self):
        """116E,40N 点经 UTM 操作后与解析 oracle 一致 —— 若执行链把
        lon/lat 再交换一次（双重交换），坐标将落在 UTM 带外/大偏移。"""
        _, _, op = _frozen_op_for("EPSG:4326", "EPSG:32650")
        feats = make_point_features(3)
        stats = execute.BatchStats()
        out = execute.transform_feature(feats[0], op, stats, row_index=1)
        x, y = out["geometry"]["coordinates"]
        want = _utm50n_analytic(116.0, 39.9)
        assert abs(x - want[0]) < 0.01 and abs(y - want[1]) < 0.01

    def test_c05_same_crs_noop_derivation(self):
        listing = candidates.list_operations("EPSG:4326", "EPSG:4326", None)
        avail = [c for c in listing["candidates"]
                 if c["availability"] == "available"]
        assert avail, "同 CRS 也应有无参 noop 候选（no-op 派生政策显式化）"

    def test_c14_compound_and_dynamic_rejected(self):
        d = describe_crs("EPSG:9518")  # NAD83(2011) + NAVD88 height
        assert d["dimension_supported"] is False
        assert "compound" in (d["dimension_unsupported_reason"] or "")
        d2 = describe_crs("EPSG:7912")  # ITRF2014 动态 datum
        assert d2["dimension_supported"] is False
        assert "dynamic" in (d2["dimension_unsupported_reason"] or "")

    def test_z_dimension_allowed_with_policy(self):
        d = describe_crs("EPSG:4979")  # WGS84 3D
        assert d["dimension_supported"] is True  # Z 走 preserve_unconverted

    def test_c03_asymmetric_points_distinguish_axis_convention(self):
        """两对互换不同的 x/y：载体约定下结果可裁决（不凭范围自动纠正）。

        (55,25) 互换为 (25,55)：两者都是合法经纬度，但结果偏移巨大。
        """
        _, _, op = _frozen_op_for("EPSG:4326", "EPSG:32650")
        import numpy as np

        a = op.transform_array(np.array([55.0]), np.array([25.0]))
        b = op.transform_array(np.array([25.0]), np.array([55.0]))
        assert abs(a[0][0] - b[0][0]) > 1e5  # 交换即显著不同

    def test_units_disclosed(self):
        d = describe_crs("EPSG:2263")
        units = [a["unit_name"] for a in d["axis_official"]]
        assert "US survey foot" in units
        d2 = describe_crs("EPSG:32650")
        assert all(a["unit_name"] == "metre" for a in d2["axis_official"])


class TestGeometryKernel:
    """C17/C18/C15/C19：内核结构保真、空几何、Z、非有限、预算闸。"""

    def _op(self):
        return _frozen_op_for("EPSG:4326", "EPSG:32650")[2]

    def test_c17_all_geometry_types_preserved(self):
        op = self._op()
        feats = [
            {"type": "Feature", "properties": {"i": 1}, "geometry": {
                "type": "Point", "coordinates": [116.0, 40.0]}},
            {"type": "Feature", "properties": {"i": 2}, "geometry": {
                "type": "MultiPoint", "coordinates": [
                    [116.0, 40.0], [116.1, 40.1]]}},
            {"type": "Feature", "properties": {"i": 3}, "geometry": {
                "type": "LineString", "coordinates": [
                    [116.0, 40.0], [116.2, 40.0]]}},
            {"type": "Feature", "properties": {"i": 4}, "geometry": {
                "type": "Polygon", "coordinates": [
                    [[116.0, 40.0], [116.3, 40.0], [116.3, 40.3],
                     [116.0, 40.3], [116.0, 40.0]],
                    [[116.05, 40.05], [116.1, 40.05], [116.1, 40.1],
                     [116.05, 40.05]]]}},
            {"type": "Feature", "properties": {"i": 5}, "geometry": {
                "type": "MultiPolygon", "coordinates": [
                    [[[116.0, 40.0], [116.1, 40.0], [116.1, 40.1],
                      [116.0, 40.1], [116.0, 40.0]]]]}},
            {"type": "Feature", "properties": {"i": 6}, "geometry": {
                "type": "GeometryCollection", "geometries": [
                    {"type": "Point", "coordinates": [116.3, 40.3]},
                    {"type": "LineString", "coordinates": [
                        [116.0, 41.0], [116.4, 41.2]]}]}},
        ]
        stats = execute.BatchStats()
        outs = [execute.transform_feature(f, op, stats, row_index=i + 1)
                for i, f in enumerate(feats)]
        assert stats.rows == 6
        assert outs[0]["geometry"]["type"] == "Point"
        assert outs[1]["geometry"]["type"] == "MultiPoint"
        assert len(outs[1]["geometry"]["coordinates"]) == 2
        assert outs[3]["geometry"]["type"] == "Polygon"
        assert len(outs[3]["geometry"]["coordinates"]) == 2  # 含洞环结构保持
        assert len(outs[3]["geometry"]["coordinates"][0]) == 5
        gc = outs[5]["geometry"]
        assert gc["type"] == "GeometryCollection"
        # GC 子几何已真实变换（非原值）
        gcxy = gc["geometries"][0]["coordinates"]
        want = _utm50n_analytic(116.3, 40.3)
        assert abs(gcxy[0] - want[0]) < 0.01
        # 属性与顺序保持
        assert [o["properties"]["i"] for o in outs] == [1, 2, 3, 4, 5, 6]

    def test_c18_null_and_empty_geometry_counted_separately(self):
        op = self._op()
        feats = [
            {"type": "Feature", "properties": {}, "geometry": None},
            {"type": "Feature", "properties": {}, "geometry": {
                "type": "Point", "coordinates": []}},
            {"type": "Feature", "properties": {}, "geometry": {
                "type": "Point", "coordinates": [116.0, 40.0]}},
        ]
        stats = execute.BatchStats()
        outs = [execute.transform_feature(f, op, stats, row_index=i + 1)
                for i, f in enumerate(feats)]
        assert stats.null_geometry == 1
        assert stats.empty_geometry == 1
        assert stats.rows == 1
        assert outs[0]["geometry"] is None
        assert outs[1]["geometry"] == {"type": "Point", "coordinates": []}

    def test_c18_non_finite_input_fails_all_or_nothing(self):
        op = self._op()
        feat = {"type": "Feature", "properties": {}, "geometry": {
            "type": "Point", "coordinates": [float("nan"), 40.0]}}
        with pytest.raises(CoordinateOperationError) as ei:
            execute.transform_feature(feat, op, execute.BatchStats(), row_index=7)
        assert ei.value.error_code == "execute_failed"
        assert "非有限" in ei.value.reason

    def test_c15_z_preserved_unconverted(self):
        op = self._op()
        feat = {"type": "Feature", "properties": {}, "geometry": {
            "type": "Point", "coordinates": [116.0, 40.0, 888.8]}}
        out = execute.transform_feature(
            feat, op, execute.BatchStats(), row_index=1)
        x, y, z = out["geometry"]["coordinates"]
        assert z == 888.8  # Z 原值（未做高程基准变换 —— 政策在回执披露）
        want = _utm50n_analytic(116.0, 40.0)
        assert abs(x - want[0]) < 0.01

    def test_c19_single_geometry_vertex_budget_gate(self):
        op = self._op()
        ring = [[116.0 + (i % 50) * 0.001, 40.0 + (i // 50) * 0.001]
                for i in range(execute.contracts.MAX_SINGLE_GEOM_VERTICES + 1)]
        feat = {"type": "Feature", "properties": {}, "geometry": {
            "type": "LineString", "coordinates": ring}}
        with pytest.raises(CoordinateOperationError) as ei:
            execute.transform_feature(feat, op, execute.BatchStats(), row_index=1)
        assert ei.value.error_code == "budget_exceeded"

    def test_staging_stream_and_verify(self, tmp_path):
        """run_streaming_transform：双载体输出 + 核验 + GeoJSON 语义。"""
        import json as _json

        import pyarrow.parquet as pq

        from app.services.data_fabric.vector_carrier import features_to_arrow

        op = self._op()
        feats = make_point_features(61, crs_z=True)
        feats.append({"type": "Feature", "properties": {}, "geometry": None})
        src = tmp_path / "src.parquet"
        pq.write_table(features_to_arrow(feats, crs="EPSG:4326"), str(src))
        frozen = {
            "frozen_path": str(src), "carrier": "parquet",
            "row_count": len(feats), "crs": "EPSG:4326",
            "frozen_sha256": "0" * 64, "source_kind": "session_ref",
            "source_fingerprint": "0" * 64, "geometry": None,
            "frozen_bytes": 0,
        }
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        stats = execute.run_streaming_transform(
            frozen, out_dir, op, "EPSG:32650", batch_rows=17)
        assert stats["rows_transformed"] == 61
        assert stats["null_geometry"] == 1
        assert stats["total_vertices"] == 61
        # 权威 GeoJSON 语义
        doc = _json.loads((out_dir / "output.geojson").read_text())
        assert len(doc["features"]) == 62  # 61 变换 + 1 null 保留
        assert doc["properties"]["schema_info"]["crs"] == "EPSG:32650"
        assert doc["crs"]["properties"]["name"] == "EPSG:32650"
        # 属性保真（C36）
        p0 = doc["features"][0]["properties"]
        assert p0["name"] == "要素0·中文"
        assert p0["big_int"] == 98765432101234
        assert p0["maybe_null"] is None
        assert p0["_wfid"] == "f-00000"
        # Z 保留
        assert len(doc["features"][0]["geometry"]["coordinates"]) == 3
        # parquet 投影存在且 CRS 正确
        assert stats["parquet_projection"]
        schema = pq.read_schema(str(out_dir / "output.parquet"))
        geo = _json.loads(schema.metadata[b"geo"])
        col = geo["primary_column"]
        assert geo["columns"][col]["crs"]
        # 核验通过
        verified = execute.verify_staged_output(
            out_dir, "output", "EPSG:32650", 62)
        assert verified["rows"] == 62

    def test_output_crs_mismatch_blocked(self, tmp_path):
        import json as _json

        d = tmp_path / "o"
        d.mkdir()
        (d / "output.geojson").write_text(_json.dumps({
            "type": "FeatureCollection",
            "features": [{"type": "Feature", "properties": {},
                          "geometry": None}],
            "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
            "properties": {"schema_info": {"crs": "EPSG:4326"}}}), encoding="utf-8")
        with pytest.raises(CoordinateOperationError) as ei:
            execute.verify_staged_output(d, "output", "EPSG:32650", 1)
        assert ei.value.error_code == "internal_error"
