"""跨系统科学回归防线（ADR-0104 Wave 13）。

对抗性输入类的统一红线：每一类输入喂给**真实入口**（算法函数或
ToolRegistry.dispatch 的守卫面）后，系统必须给出四种诚实结局之一 ——

- typed-reject        ：``app/lib/gis/scientific_errors.py`` 词表的类型化异常
                        （或该族既有的类型化拒绝通道：GeoAnalysisResult
                        (success=False) / KrigingInputError 等 ValueError 子类）；
- honest-fallback     ：不伪造数值的诚实空/降级结果（全 None 行、
                        reachable=False 披露）；
- explicit-warning    ：结果内携带 warning / 披露字段；
- disclosed-approx    ：degraded_cells / approximation 等近似披露。

**绝不允许**语义非法输入上静默产出貌似合理的结果（success 无警告且语义
被违反）。按 goal 约定：当前仍在静默成功的站点写成 ``xfail(strict=False)``
并登记进 KNOWN-GAP —— 修复后自动转绿，而不是放宽断言。

KNOWN-GAP（截至 2026-09，逐条带证据）：
1. 【CRS 类】不可解析的 CRS 字符串（如 "EPSG:99999999"）穿透消费管线时，
   ``to_utm_gdf_with_note``（app/lib/geo_processor/core.py:465，geopandas
   构造器内部调 pyproj）以**裸 pyproj.CRSError** 逃逸，而不是
   scientific_errors.InvalidCRS —— dispatch 层会落入通用 ``except
   Exception`` 变成 TOOL_ERROR，丢失 correction_hint 科学通道。
2. 【坏几何类】自交多边形（bowtie）在分析管线里静默通过：
   ``zonal_statistics``（app/lib/geo_analysis/raster_ops.py）对 invalid
   几何既不拒绝也不告警，GEOS 按歧义规则解释出貌似合理的统计值；
   ``to_utm_gdf_with_note``（core.py:466）的 ``make_valid()`` 修复同样
   无任何修复计数/披露（对照：显式修复通道
   SpatialRepairPipeline 是有 audit log 的）。

方法学：全部离线、确定、无网络无 LLM 无 sleep；坏数据场景一律从
tests/fixtures/gis_samples.py 的参数化工厂派生（Wave 18 契约），测试内
不做第二套夹具实现。
"""
from __future__ import annotations

import asyncio

import numpy as np
import pytest

from tests.fixtures.gis_samples import (
    bad_crs_fc,
    invalid_geometry_fc,
    point_fc,
    temporal_fc,
    zero_variance_fc,
)

# ── 通用小工具 ───────────────────────────────────────────────────────────


def _xy(fc: dict) -> np.ndarray:
    """点 FC → (n, 2) 坐标阵（按夹具契约：全是 Point 要素）。"""
    return np.asarray(
        [f["geometry"]["coordinates"] for f in fc["features"]], dtype=float
    )


def _assert_scientific_reject(exc: BaseException) -> None:
    """typed-reject 的形状断言：科学词表异常必须带机器可读码 + 修复提示。"""
    code = getattr(exc, "scientific_code", None)
    hint = getattr(exc, "correction_hint", None)
    assert code, f"科学错误缺少 scientific_code: {exc!r}"
    assert hint, f"科学错误缺少 correction_hint: {exc!r}"


# ── 类 1：CRS 误配（声明 vs 坐标域；度量假设冲突）────────────────────────


def test_crs_mismatch_classifier_adjudicates_bad_crs_fixture():
    """bad_crs_fc（成都 WGS84 域坐标 + CGCS2000 声明）必须得到 CRS 裁决。

    分类器把 EPSG:4490 判为 geographic —— 后续度量门据此拒绝度数坐标进
    二阶统计；绝不允许"分类不了就当地理/当投影"的静默猜测。
    """
    from app.lib.gis.crs_safety import classify_crs

    fc = bad_crs_fc()
    assert fc["crs"] == "EPSG:4490"
    assert classify_crs(fc["crs"]) == "geographic"
    # 对照组：投影米（3857）与局部度量（UTM）分得开 —— 防线词汇完整性
    assert classify_crs("EPSG:3857") == "projected"
    assert classify_crs("EPSG:32648") == "projected_local_metric"


def test_crs_mismatch_resolver_predicate_refuses_metric_only_algorithms():
    """消费端守卫谓词：度量必需算法 × 地理数据 = 拒绝（resolver 硬门）。"""
    from app.lib.gis.crs_safety import classify_crs, crs_class_allows, recommend_metric_crs

    data_class = classify_crs(bad_crs_fc()["crs"])
    assert crs_class_allows("LOCAL_METRIC_REQUIRED", data_class) is False
    assert crs_class_allows("PROJECTED_REQUIRED", data_class) is False
    # 度数兼容/大地测量/不可知算法照常放行（诚实缺省哲学）
    assert crs_class_allows("GEOGRAPHIC_OK", data_class) is True
    assert crs_class_allows("GEODESIC", data_class) is True
    assert crs_class_allows("CRS_AGNOSTIC", data_class) is True
    # 且系统给出**披露式**的度量投影建议（UTM 48 带覆盖成都），不是静默换系
    bbox = (103.95, 30.55, 104.10, 30.70)
    rec = recommend_metric_crs(bbox)
    assert rec.startswith("EPSG:326"), rec


@pytest.mark.parametrize("declared_crs", ["EPSG:4326", "EPSG:4490"])
def test_crs_mismatch_metric_second_order_statistics_reject_degrees(declared_crs):
    """度量必需算法（Ripley K / 样方 χ²）× 度数坐标 → InvalidCRS 类型化拒绝。

    直接喂 bad_crs_fc 的坐标 + 其声明 CRS：度数下的 K / 样方面积没有科学
    意义 —— 这是 ``_assert_metric_xy`` 防线（point_pattern.py:60）。
    """
    from app.lib.gis.scientific_errors import InvalidCRS
    from app.lib.geo_analysis.point_pattern import quadrat_test, ripley_k

    fc = bad_crs_fc(n=14)
    xy = _xy(fc)
    with pytest.raises(InvalidCRS) as ri:
        ripley_k(xy, crs=declared_crs)
    _assert_scientific_reject(ri.value)
    with pytest.raises(InvalidCRS) as rq:
        quadrat_test(xy, crs=declared_crs)
    _assert_scientific_reject(rq.value)


def test_crs_mismatch_unparseable_crs_is_typed_scientific_reject():
    """不可解析 CRS 必须落在科学词表内（InvalidCRS），而不是裸三方异常。

    science-v4 W2 收口（原 KNOWN-GAP #1，xfail 已转正）：to_utm_gdf_with_note
    在 GeoDataFrame 构造前校验声明 CRS 可解析性，pyproj.CRSError 在边界
    折叠成 InvalidCRS（ValueError 系）—— dispatch 保留 scientific_code 与
    correction_hint 科学通道。
    """
    from app.lib.gis.scientific_errors import InvalidCRS
    from app.lib.geo_analysis.statistics import moran_i_narrated

    fc = point_fc(n=12)
    fc["crs"] = "EPSG:99999999"
    with pytest.raises(InvalidCRS):
        moran_i_narrated(fc, "value")


# ── 类 2：单位错误（参数单位 vs 算法单位契约）────────────────────────────


def test_wrong_units_distance_band_in_degrees_is_rejected_not_interpreted():
    """距离带参数的契约是**米**（E-7）。度数尺度（0.01 ≈ 1km）喂给 UTM
    度量数据后不得被"宽容解释"成貌似合理的结果 —— 权重矩阵为空时必须
    显式拒绝，而不是输出 random/clustering 叙事。
    """
    from app.lib.geo_analysis.statistics import moran_i_narrated

    res = moran_i_narrated(
        point_fc(n=12), "value", weights_scheme="distance_band", distance_band=0.01
    )
    assert res.success is False
    assert res.error_message, "空权重矩阵必须携带解释信息"
    assert "weights" in res.error_message.lower()
    assert res.data is None, "拒绝路径不得携带任何统计载荷"


def test_wrong_units_metric_gate_is_unit_explicit_in_message():
    """度量门的报错必须点破单位（degrees vs metric），可自愈。"""
    from app.lib.gis.scientific_errors import InvalidCRS
    from app.lib.geo_analysis.point_pattern import ripley_k

    rng = np.random.default_rng(3)
    xy = np.column_stack([rng.uniform(103.9, 104.1, 20), rng.uniform(30.5, 30.7, 20)])
    with pytest.raises(InvalidCRS) as ei:
        ripley_k(xy, crs="EPSG:4326")
    assert "geographic" in str(ei.value) and "metric" in str(ei.value)
    # 自愈通道在 correction_hint（dispatch 会透传给 LLM/用户）
    assert "reproject" in (ei.value.correction_hint or "").lower()


# ── 类 3：坏几何（自交 bowtie → 修复/校验路径）───────────────────────────


def test_invalid_geometry_explicit_repair_discloses_audit_log():
    """显式修复通道（repair_spatial_dataset 的后端）：修复必须有 audit log
    披露 + 输出几何确实变 valid（honest-fallback 带披露）。
    """
    from shapely.geometry import shape

    from app.services.spatial_repair_pipeline import SpatialRepairPipeline

    fc = invalid_geometry_fc()
    assert shape(fc["features"][0]["geometry"]).is_valid is False

    repaired, logs = SpatialRepairPipeline.repair_dataset(fc)
    assert any("make_valid" in entry for entry in logs), logs
    out_geom = shape(repaired["features"][0]["geometry"])
    assert out_geom.is_valid, "修复后几何必须 valid"
    # 非破坏性契约：输入不被原地改写
    assert shape(fc["features"][0]["geometry"]).is_valid is False


def test_invalid_geometry_analysis_path_typed_reject(tmp_path):
    """分析入口对 semantically-invalid 几何必须类型化拒绝（科学词表）。

    science-v4 W3 收口（原 KNOWN-GAP #2，xfail 已转正）：zonal_statistics
    默认 strict=True，自交 bowtie → InvalidGeometry（科学词表 +
    correction_hint）；strict=False 时修复带 geometry_repair 披露（见
    test_geometry_repair_v4）。
    """
    import rasterio
    from rasterio.transform import from_origin

    from app.lib.geo_analysis.raster_ops import zonal_statistics
    from app.lib.gis.scientific_errors import InvalidGeometry

    raster_path = tmp_path / "valid.tif"
    with rasterio.open(
        raster_path, "w", driver="GTiff", width=16, height=16, count=1,
        dtype="float32", crs="EPSG:4326",
        transform=from_origin(103.95, 30.70, 0.005, 0.005),
    ) as dst:
        dst.write(np.full((1, 16, 16), 7.0, dtype="float32"))

    with pytest.raises((InvalidGeometry, ValueError)) as ei:
        zonal_statistics(invalid_geometry_fc(), str(raster_path))
    _assert_scientific_reject(ei.value)


# ── 类 4：样本量不足（低于方法学下限 → 类型化科学错误）───────────────────


def test_too_few_samples_kriging_rejects_below_min():
    """克里金下限 8 点（去重后）：3 点 → KrigingInputError + 改用 IDW 提示。"""
    from app.lib.geo_analysis.kriging import KrigingInputError, kriging_interpolation

    with pytest.raises((KrigingInputError, ValueError)) as ei:
        kriging_interpolation(point_fc(n=3), "value")
    msg = str(ei.value)
    assert "8" in msg, f"拒绝信息必须点破下限: {msg}"
    assert "IDW" in msg, f"拒绝信息必须给出替代方法: {msg}"


def test_too_few_samples_moran_rejects_below_three():
    from app.lib.geo_analysis.statistics import moran_i_narrated

    res = moran_i_narrated(point_fc(n=2), "value")
    assert res.success is False
    assert "3 features" in res.error_message
    assert res.data is None


def test_too_few_samples_ripley_and_mann_kendall_typed_scientific_errors():
    """Ripley K（<10 点）与 MK 趋势（<4 期）走科学词表 InsufficientSamples。"""
    from app.lib.gis.scientific_errors import InsufficientSamples
    from app.lib.geo_analysis.point_pattern import ripley_k
    from app.services.temporal.trend import mann_kendall

    rng = np.random.default_rng(5)
    xy = np.column_stack([rng.uniform(0, 1, 6), rng.uniform(0, 1, 6)])
    with pytest.raises(InsufficientSamples) as er:
        ripley_k(xy)
    _assert_scientific_reject(er.value)

    with pytest.raises(InsufficientSamples) as em:
        mann_kendall([1.0, 2.0])
    _assert_scientific_reject(em.value)


# ── 类 5：零方差（统计量无意义的退化数据）────────────────────────────────


def test_zero_variance_autocorrelation_rejects_instead_of_random_verdict():
    """零方差 × Moran's I：历史缺陷是 I=0/p=1/"random" 的伪造裁决（E-2）。

    现契约：success=False + 点破恒定值 —— 绝不产出貌似合理的空间模式。
    """
    from app.lib.geo_analysis.statistics import moran_i_narrated

    res = moran_i_narrated(zero_variance_fc(n=12), "value")
    assert res.success is False
    assert "identical" in res.error_message.lower()
    assert res.data is None, "退化数据不得携带统计载荷"


def test_zero_variance_kriging_constant_surface_with_disclosure():
    """零方差 × 克里金：唯一诚实的输出是**常量面** + 退化披露。

    metadata 必须如实披露 value_range=[v,v] / 变差图 sill=0（或
    degraded_cells 计数）—— 静默输出一个带方差的"表面"即为伪造。
    """
    from app.lib.geo_analysis.kriging import kriging_interpolation

    out = kriging_interpolation(zero_variance_fc(n=40), "value")
    md = out["metadata"]
    recs = out["records"]
    assert recs, "退化输入也需要诚实的（常量）输出面"
    preds = {round(float(r["value"]), 9) for r in recs}
    assert len(preds) == 1, f"零方差输入的克里金面必须是常量: {len(preds)} 个不同值"
    assert md.get("value_range") == [3.14, 3.14]
    variogram = md.get("variogram") or {}
    disclosed = (
        float(variogram.get("sill") or 0.0) == 0.0
        or int(md.get("degraded_cells") or 0) > 0
    )
    assert disclosed, f"退化性必须被披露: variogram={variogram}, degraded={md.get('degraded_cells')}"


# ── 类 6：极端量级（1e12 → 溢出/鲁棒性）──────────────────────────────────


def test_extreme_magnitude_kriging_outputs_stay_finite():
    """1e12 量级属性 × 克金全链：输出（预测/方差/CV）不得被 NaN/Inf 毒化。

    量纲一致性：值放大 1e12 → 方差量级 ~1e24 起是数学上自洽的（半变差在
    值²域），关键是**有限性**与 value_range 与输入量级一致。
    """
    from app.lib.geo_analysis.kriging import kriging_interpolation

    fc = point_fc(n=40)
    for f in fc["features"]:
        f["properties"]["value"] = float(f["properties"]["value"]) * 1e12

    out = kriging_interpolation(fc, "value")
    recs = out["records"]
    assert recs
    for r in recs:
        assert np.isfinite(r["value"]), f"预测值溢出: {r}"
        assert np.isfinite(r["kriging_variance"]), f"方差溢出: {r}"
    lo, hi = out["metadata"]["value_range"]
    assert hi > 1e11, f"value_range 必须与输入量级一致: {[lo, hi]}"
    # 交叉验证摘要也必须有限（不被极端值打成 NaN 叙事）
    cv = out["metadata"].get("cross_validation") or {}
    if isinstance(cv, dict) and isinstance(cv.get("rmse"), (int, float)):
        assert np.isfinite(cv["rmse"]) and np.isfinite(cv["mae"])


# ── 类 7：NaN / Inf 注入（属性与参数树）──────────────────────────────────


def test_nonfinite_arguments_rejected_before_tool_body_runs():
    """dispatch 安全面：NaN/±Inf 实参 → VALIDATION_ERROR，且工具体**从未执行**
    （错误-looking success 无从产生）。"""
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    reached = {"ok": False}

    @reg.tool(name="sci_canary", description="canary: 到达即意味着防线失守")
    def _canary(v: float) -> dict:  # pragma: no cover — 防线在后不应到达
        reached["ok"] = True
        return {"success": True, "doubled": v * 2}

    async def _run():
        for bad in (float("nan"), float("inf"), float("-inf")):
            res = await reg.dispatch("sci_canary", {"v": bad})
            assert res.get("success") is False, f"{bad} 未被拒绝: {res}"
            assert res.get("code") == "VALIDATION_ERROR"
            assert "NaN/Infinity" in (res.get("message") or "")
            assert res.get("correction_hint"), "拒绝必须带自愈提示"
        assert reached["ok"] is False, "canary 被执行 —— 非有限值穿透了防线"

    asyncio.run(_run())


def test_nonfinite_scanner_reports_paths_and_incomplete_budget():
    """扫描器本身：命中路径可定位；预算耗尽必须报 incomplete（不许静默截断）。"""
    from app.tools.registry import _find_nonfinite_numbers

    payload = {
        "a": 1.0,
        "nested": {"deep": [1, 2, float("nan")]},
        "inf_node": float("inf"),
    }
    found, incomplete = _find_nonfinite_numbers(payload)
    assert incomplete is False
    paths = set(found)
    assert "nested.deep[2]" in paths, paths
    assert "inf_node" in paths, paths

    # 预算为 0 → incomplete=True（调用方拒绝），绝不悄悄当作"没扫到"
    _, incomplete_zero = _find_nonfinite_numbers(payload, max_nodes=0)
    assert incomplete_zero is True


def test_nonfinite_property_values_typed_reject_in_kriging():
    """算法自检面：属性里混入 NaN → 类型化拒绝（ValueError + 中文点破）。"""
    from app.lib.geo_analysis.kriging import kriging_interpolation

    fc = point_fc(n=12)
    fc["features"][3]["properties"]["value"] = float("nan")
    with pytest.raises(ValueError) as ei:
        kriging_interpolation(fc, "value")
    assert "非数值" in str(ei.value)


# ── 类 8：不连通网络（结构事实，不是异常路径的借口）───────────────────────


def _island_network_fc() -> dict:
    """两个互不连通的"岛"路段（从 nominal 线域派生的确定性最小路网）。"""
    def _seg(a, b):
        return {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [a, b]},
            "properties": {},
        }

    return {
        "type": "FeatureCollection",
        "features": [
            _seg([104.000, 30.600], [104.010, 30.600]),
            _seg([104.050, 30.650], [104.060, 30.650]),
        ],
    }


def test_disconnected_network_od_matrix_typed_scientific_error():
    """全对不可达（无 cutoff）→ DisconnectedNetwork（科学词表，结构事实）。"""
    from app.lib.gis.scientific_errors import DisconnectedNetwork
    from app.services.network.engine import NetworkGraphEngine

    async def _run():
        engine = NetworkGraphEngine()
        with pytest.raises(DisconnectedNetwork) as ei:
            await engine.solve_od_matrix(
                _island_network_fc(),
                [[104.001, 30.60]],
                [[104.055, 30.65]],
            )
        _assert_scientific_reject(ei.value)

    asyncio.run(_run())


def test_disconnected_network_shortest_path_discloses_unreachable():
    """单路径请求 × 不连通：honest-fallback —— status=success 但
    summary.reachable=False 且 cost=inf；绝不以欧氏直线伪装可达路线。"""
    import math

    from app.services.network.engine import NetworkGraphEngine

    async def _run():
        engine = NetworkGraphEngine()
        res = await engine.solve_shortest_path(
            _island_network_fc(), [104.001, 30.60], [104.055, 30.65]
        )
        assert res.status == "success"
        assert res.summary.get("reachable") is False, res.summary
        routes = res.routes or []
        assert routes and not math.isfinite(routes[0].total_cost)

    asyncio.run(_run())


# ── 类 9：栅格 nodata（全无效窗口 → 诚实空，绝不伪造数值）────────────────


def test_all_nodata_raster_zonal_stats_honest_empty(tmp_path):
    """全 nodata 栅格 × 区间统计：count=0 + 其余统计全 None（显式空披露）。

    伪造形态对照：把 nodata 当 0 参与统计 → mean/sum 出"看似合理"的小数值。
    """
    import rasterio
    from rasterio.transform import from_origin

    from app.lib.geo_analysis.raster_ops import zonal_statistics

    raster_path = tmp_path / "all_nodata.tif"
    with rasterio.open(
        raster_path, "w", driver="GTiff", width=16, height=16, count=1,
        dtype="float32", crs="EPSG:4326",
        transform=from_origin(103.95, 30.70, 0.005, 0.005),
        nodata=-9999.0,
    ) as dst:
        dst.write(np.full((1, 16, 16), -9999.0, dtype="float32"))

    zone = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [[
                [103.96, 30.63], [104.00, 30.63], [104.00, 30.68],
                [103.96, 30.68], [103.96, 30.63],
            ]]},
            "properties": {},
        }],
    }
    rows = zonal_statistics(zone, str(raster_path), stats=["mean", "sum", "count"])
    assert len(rows) == 1
    row = rows[0]
    assert row["count"] == 0, f"必须显式披露零有效像素: {row}"
    assert row["mean"] is None and row["sum"] is None, "空窗口不得伪造任何数值"


# ── 类 10：时间缺口（缺失周期的时间聚合）─────────────────────────────────


def _temporal_monthly_means(fc: dict) -> dict:
    """时间 FC → {月份: 均值}（确定性的月度聚合，模拟 temporal 通道）。"""
    series: dict = {}
    for f in fc["features"]:
        series.setdefault(f["properties"]["time"], []).append(
            float(f["properties"]["value"])
        )
    return {m: float(np.mean(v)) for m, v in series.items()}


def test_temporal_gap_too_few_periods_typed_scientific_reject():
    """中间月份缺失（2024-03/04 掉档）后仅剩 3 期 → MK 检验类型化拒绝
    （InsufficientSamples，n=3 < 4），绝不输出低样本趋势叙事。"""
    from app.lib.gis.scientific_errors import InsufficientSamples
    from app.services.temporal.trend import mann_kendall

    monthly = _temporal_monthly_means(temporal_fc())
    assert len(monthly) == 6
    gapped = [v for m, v in sorted(monthly.items()) if m not in ("2024-03", "2024-04")]
    assert len(gapped) == 4
    with pytest.raises(InsufficientSamples):
        mann_kendall(gapped[:3])


def test_temporal_gap_seasonal_mk_discloses_per_season_evidence():
    """季节性 MK 在缺口季节（1/3/5 月）上：正常工作且**逐季披露**证据
    （per_season 明细 + seasons_used）—— 聚合检验不允许黑箱。"""
    from app.services.temporal.trend import seasonal_mann_kendall

    fc = temporal_fc(n_per_period=5, periods=6)
    keep = {"2024-01", "2024-03", "2024-05"}
    values: list = []
    dates: list = []
    for f in fc["features"]:
        if f["properties"]["time"] in keep:
            values.append(float(f["properties"]["value"]))
            dates.append(f["properties"]["time"])

    out = seasonal_mann_kendall(values, dates)
    assert out.get("seasons_used") == 3, out
    per_season = out.get("per_season") or []
    assert {int(s["season"]) for s in per_season} == {1, 3, 5}
    assert all("n" in s and s["n"] == 5 for s in per_season)


# ── 防线自检：四类允许结局之外没有第五种（词表完整性）────────────────────


def test_scientific_error_vocabulary_covers_all_codes():
    """词表封闭性：每个 ScientificError 子类都有稳定 scientific_code，
    to_dict 可序列化（dispatch/证据通道的机器读契约）。"""
    from app.lib.gis import scientific_errors as se

    subs = [
        getattr(se, name)
        for name in dir(se)
        if isinstance(getattr(se, name, None), type)
        and issubclass(getattr(se, name), se.ScientificError)
        and getattr(se, name) is not se.ScientificError
    ]
    assert len(subs) >= 10
    codes = set()
    for cls in subs:
        try:
            inst = cls("probe")
        except TypeError:
            continue  # 需要额外参数的类型跳过码采集
        code = inst.to_dict()["scientific_code"]
        assert code and code == code.upper()
        codes.add(code)
    assert {"INSUFFICIENT_SAMPLES", "INVALID_CRS", "DEGENERATE_DATA",
            "DISCONNECTED_NETWORK"} <= codes
