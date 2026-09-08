"""行为化 dispatch 测试 —— 空间统计/点格局/降维/变化检测/时序 12 分析工具。

每个工具一个 test 函数（``test_<tool>_behavioral``），内含三段式契约：
1. validation —— 非法/缺参 → dispatch 返回 success=False 且错误信息可读；
2. happy path —— 小型合成数据真实执行（无网络/无大文件/无 LLM），
   断言结果关键字段；
3. error path —— 数据依赖缺失（字段不存在/样本不足/形状不一致）行为被断言。

全部经 ``registry.dispatch("tool_name", {...})`` 真实走到工具函数体 ——
这是 app/lib/quality/behavioral.py 识别的行为证据（actual dispatch）。
"""
import numpy as np
import pytest

from app.tools.advanced_spatial import register_advanced_spatial_tools
from app.tools.change_detection import register_change_detection_tools
from app.tools.point_pattern_tools import register_point_pattern_tools
from app.tools.registry import ToolRegistry
from app.tools.remote_sensing import register_rs_tools
from app.tools.spatial_stats import register_spatial_stats_tools
from app.tools.temporal_tools import register_temporal_tools


@pytest.fixture()
def registry():
    reg = ToolRegistry()
    register_spatial_stats_tools(reg)
    register_point_pattern_tools(reg)
    register_advanced_spatial_tools(reg)
    register_rs_tools(reg)
    register_change_detection_tools(reg)
    register_temporal_tools(reg)
    return reg


def _assert_error_readable(result) -> None:
    """失败应答必须 carrying success=False 与非空的可读错误信息。

    message（std_error_response）/ summary（GeoAnalysisResult 失败投影）/
    error（工具自管错误 dict）三者任一即可。
    """
    assert isinstance(result, dict), result
    assert result.get("success") is False, result
    msg = result.get("message") or result.get("summary") or result.get("error")
    assert isinstance(msg, str) and msg.strip(), result


def _point(lng, lat, **props):
    return {
        "type": "Feature",
        "properties": props,
        "geometry": {"type": "Point", "coordinates": [lng, lat]},
    }


def _fc(features):
    return {"type": "FeatureCollection", "features": features}


def _grid_fc(rows, cols, value_fn, origin=(116.0, 39.0), step=0.01):
    """rows×cols 点阵；value_fn(r, c) → properties dict。"""
    feats = []
    for r in range(rows):
        for c in range(cols):
            feats.append(
                _point(origin[0] + c * step, origin[1] + r * step, **value_fn(r, c))
            )
    return _fc(feats)


def _space_time_fc(n_per_cluster=10):
    """3 个空间簇 × 3 个时间窗（簇-窗绑定 → 时空聚集），Epoch 秒时间戳。"""
    centers = [(116.00, 39.00, 0.0), (116.03, 39.00, 3600.0), (116.015, 39.03, 7200.0)]
    rng = np.random.default_rng(7)
    feats = []
    for lng0, lat0, t0 in centers:
        for _ in range(n_per_cluster):
            feats.append(_point(
                float(lng0 + rng.normal(0, 0.002)),
                float(lat0 + rng.normal(0, 0.002)),
                ts=float(t0 + rng.uniform(0, 120)),
            ))
    return _fc(feats)


def _two_type_fc(n_per_type=12):
    """两类点（cat ∈ {a, b}），各自成簇 → 双变量点格局。"""
    rng = np.random.default_rng(11)
    feats = []
    for cat, (lng0, lat0) in (("a", (116.0, 39.0)), ("b", (116.04, 39.04))):
        for _ in range(n_per_type):
            feats.append(_point(
                float(lng0 + rng.normal(0, 0.002)),
                float(lat0 + rng.normal(0, 0.002)),
                cat=cat,
            ))
    return _fc(feats)


def _band(role, arr):
    return {role: np.asarray(arr, dtype=float).round(6).tolist()}


def _bands_8x8(seed=1, n_bands=3):
    rng = np.random.default_rng(seed)
    base = rng.normal(100.0, 10.0, size=(8, 8))
    bands = {}
    for i in range(n_bands):
        bands[f"b{i + 1}"] = (base * (1.0 + 0.1 * i)
                              + rng.normal(0, 1.0, size=(8, 8))).round(6).tolist()
    return bands


# ── spatial_stats ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_geary_c_behavioral(registry):
    # validation：缺必填参数 → 校验错误（success=False + 可读信息）
    bad = await registry.dispatch("geary_c", {"geojson": _fc([])})
    _assert_error_readable(bad)
    assert bad.get("code") == "VALIDATION_ERROR"

    fc = _grid_fc(5, 5, lambda r, c: {"v": float(c)})  # 沿 x 的平滑场
    # error path：字段不存在 → 业务失败
    missing = await registry.dispatch(
        "geary_c", {"geojson": fc, "value_field": "nope"})
    _assert_error_readable(missing)

    # happy path：平滑空间场 → Geary's C < 1（正自相关/聚集）
    out = await registry.dispatch(
        "geary_c", {"geojson": fc, "value_field": "v", "permutations": 99})
    assert out.get("success") is True, out
    data = out["data"]
    assert "gearys_c" in data and "p_value" in data
    assert 0.0 < data["gearys_c"] < 1.0
    assert data.get("n_features") == 25


@pytest.mark.asyncio
async def test_quadrat_analysis_behavioral(registry):
    # validation：缺 geojson → 校验错误
    bad = await registry.dispatch("quadrat_analysis", {})
    _assert_error_readable(bad)
    assert bad.get("code") == "VALIDATION_ERROR"

    # error path：空要素集 → 业务失败（无可分析点）
    empty = await registry.dispatch(
        "quadrat_analysis", {"geojson": _fc([]), "grid_rows": 2, "grid_cols": 2})
    _assert_error_readable(empty)

    # happy path：注意 —— 疑似生产 bug：SpatialAnalyzer.quadrat_test 返回
    # GeoAnalysisResult 对象，而工具体按 dict 下标取值（result["summary"]），
    # 任何合法输入都会 TypeError（'GeoAnalysisResult' object is not
    # subscriptable）。此处钉住该真实失败行为，不掩盖。
    rng = np.random.default_rng(3)
    feats = [_point(116.0 + abs(float(rng.normal(0, 0.002))),
                    39.0 + abs(float(rng.normal(0, 0.002))))
             for _ in range(40)]
    out = await registry.dispatch(
        "quadrat_analysis", {"geojson": _fc(feats), "grid_rows": 4, "grid_cols": 4})
    _assert_error_readable(out)
    assert out.get("code") == "TOOL_ERROR"
    assert "not subscriptable" in (out.get("message") or "")


@pytest.mark.asyncio
async def test_mgwr_regression_behavioral(registry):
    # validation：缺 explanatory_fields → 校验错误
    bad = await registry.dispatch(
        "mgwr_regression", {"geojson": _fc([]), "target_field": "y"})
    _assert_error_readable(bad)
    assert bad.get("code") == "VALIDATION_ERROR"

    def plane(r, c):
        return {"x1": float(c) / 7.0, "y": 2.0 + 3.0 * float(c) / 7.0}

    fc = _grid_fc(8, 8, plane)
    # error path：常数目标场 → DegenerateData（诚实拒绝）
    fc_const = _grid_fc(6, 6, lambda r, c: {"x1": float(c), "y": 1.0})
    degen = await registry.dispatch(
        "mgwr_regression",
        {"geojson": fc_const, "target_field": "y", "explanatory_fields": "x1",
         "bandwidth": 8})
    _assert_error_readable(degen)

    # happy path：精确平面 → 拟合成功且收敛
    out = await registry.dispatch(
        "mgwr_regression",
        {"geojson": fc, "target_field": "y", "explanatory_fields": "x1",
         "bandwidth": 16})
    assert out.get("success") is True, out
    data = out["data"]
    assert data.get("n_features") == 64
    assert data.get("backfitting", {}).get("converged") is True
    assert "surfaces" in data and "intercept" in data["surfaces"]


# ── point_pattern_tools ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_space_time_k_analysis_behavioral(registry):
    # validation：缺 time_field → 校验错误
    bad = await registry.dispatch("space_time_k_analysis", {"geojson": _fc([])})
    _assert_error_readable(bad)
    assert bad.get("code") == "VALIDATION_ERROR"

    fc = _space_time_fc()
    # error path：时间字段不存在于要素属性 → 类型化错误
    missing = await registry.dispatch(
        "space_time_k_analysis", {"geojson": fc, "time_field": "nope"})
    _assert_error_readable(missing)

    # happy path：簇-窗绑定 → 时空 K 显著（p<0.05）
    out = await registry.dispatch(
        "space_time_k_analysis",
        {"geojson": fc, "time_field": "ts", "permutations": "99"})
    assert out.get("success") is True, out
    data = out["data"]
    assert data.get("n") == 30
    assert data.get("time_field") == "ts"
    assert len(data.get("K") or []) == 8
    assert data.get("p_value") is not None and data["p_value"] < 0.05


@pytest.mark.asyncio
async def test_mantel_test_analysis_behavioral(registry):
    # validation：缺 time_field → 校验错误
    bad = await registry.dispatch("mantel_test_analysis", {"geojson": _fc([])})
    _assert_error_readable(bad)
    assert bad.get("code") == "VALIDATION_ERROR"

    # error path：时间全相同（零方差） → DegenerateData 诚实拒绝
    fc_const_t = _fc([
        _point(116.0 + i * 0.01, 39.0 + i * 0.01, ts=100.0) for i in range(12)
    ])
    degen = await registry.dispatch(
        "mantel_test_analysis", {"geojson": fc_const_t, "time_field": "ts",
                                 "permutations": "0"})
    _assert_error_readable(degen)

    # happy path：空间近 ⇔ 时间近 → Mantel r 显著为正
    # （契约枚举：permutations ∈ 0/199/499/999）
    out = await registry.dispatch(
        "mantel_test_analysis",
        {"geojson": _space_time_fc(), "time_field": "ts",
         "permutations": "199", "alternative": "greater"})
    assert out.get("success") is True, out
    data = out["data"]
    assert data.get("n") == 30
    assert isinstance(data.get("mantel_r"), float) and data["mantel_r"] > 0
    assert data.get("p_value") is not None and data["p_value"] <= 0.05


@pytest.mark.asyncio
async def test_cross_pcf_analysis_behavioral(registry):
    # validation：缺 type_field → 校验错误
    bad = await registry.dispatch("cross_pcf_analysis", {"geojson": _fc([])})
    _assert_error_readable(bad)
    assert bad.get("code") == "VALIDATION_ERROR"

    # error path：类型字段不存在 → 类型化错误
    missing = await registry.dispatch(
        "cross_pcf_analysis", {"geojson": _two_type_fc(), "type_field": "nope"})
    _assert_error_readable(missing)

    # happy path：两类点各自成簇 → g12 谱 + 置换包络
    out = await registry.dispatch(
        "cross_pcf_analysis",
        {"geojson": _two_type_fc(), "type_field": "cat",
         "n_steps": 6, "permutations": "99"})
    assert out.get("success") is True, out
    data = out["data"]
    assert data.get("n") == 24
    assert data.get("type_field") == "cat"
    assert len(data.get("g12") or []) == 6
    assert len(data.get("envelope_g12_high") or []) == 6
    assert data.get("p_value") is not None


# ── advanced_spatial ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_interpolation_model_compare_behavioral(registry):
    # validation：缺 value_field → 校验错误
    bad = await registry.dispatch("interpolation_model_compare", {"geojson": _fc([])})
    _assert_error_readable(bad)
    assert bad.get("code") == "VALIDATION_ERROR"

    # error path：样本不足（1 个点） → 明确的业务失败语义：
    # recommended=None，逐行披露跳过原因（该工具无 success 键，以
    # comparison/recommended 承载结论）。
    tiny = await registry.dispatch(
        "interpolation_model_compare",
        {"geojson": _fc([_point(116.0, 39.0, v=1.0)]), "value_field": "v"})
    assert isinstance(tiny, dict) and tiny.get("recommended") is None, tiny
    assert all(row.get("eligible") is False for row in tiny.get("comparison") or [])
    assert any(row.get("skipped_reason") for row in tiny.get("comparison") or [])

    # happy path：36 个平滑场样本 → 方法排名 + 确定性推荐
    fc = _grid_fc(6, 6, lambda r, c: {
        "v": float(10.0 * np.sin(c / 2.0) + 3.0 * r)})
    out = await registry.dispatch(
        "interpolation_model_compare", {"geojson": fc, "value_field": "v"})
    assert out.get("compare_metadata", {}).get("n_samples") == 36, out
    rows = out.get("comparison") or []
    assert rows, out
    assert all("method" in row for row in rows)
    assert out.get("recommended") is not None
    assert "scientific_evidence" in out


# ── remote_sensing（波段栈降维）───────────────────────────────────────


@pytest.mark.asyncio
async def test_mnf_transform_behavioral(registry):
    # validation：缺 bands → 校验错误
    bad = await registry.dispatch("mnf_transform", {})
    _assert_error_readable(bad)
    assert bad.get("code") == "VALIDATION_ERROR"

    # error path：各角色形状不一致 → 诚实拒绝
    ragged = {"b1": [[1.0, 2.0], [3.0, 4.0]], "b2": [[1.0, 2.0, 3.0]]}
    shape_err = await registry.dispatch("mnf_transform", {"bands": ragged})
    _assert_error_readable(shape_err)

    # happy path：3 波段 8×8 → 2 个 SNR 排序分量
    out = await registry.dispatch(
        "mnf_transform", {"bands": _bands_8x8(), "n_components": 2})
    assert out.get("success") is True, out
    assert len(out["snr"]) == 2
    assert len(out["component_rasters"]) == 2
    assert out["n_valid_pixels"] == 64
    assert out["band_order"] == ["b1", "b2", "b3"]
    assert "scientific_evidence" in out


@pytest.mark.asyncio
async def test_ica_transform_behavioral(registry):
    # validation：空 bands 字典 → 工具层校验错误
    bad = await registry.dispatch("ica_transform", {"bands": {}})
    _assert_error_readable(bad)

    # error path：1D 波段（ndim != 2） → 诚实拒绝
    flat = await registry.dispatch(
        "ica_transform", {"bands": {"b1": [1.0, 2.0, 3.0]}})
    _assert_error_readable(flat)

    # happy path：3 波段 8×8 → 2 个独立分量 + 混合矩阵
    out = await registry.dispatch(
        "ica_transform", {"bands": _bands_8x8(seed=2), "n_components": 2})
    assert out.get("success") is True, out
    assert isinstance(out["converged"], bool)
    assert len(out["component_rasters"]) == 2
    mixing = out.get("mixing_matrix")
    assert mixing and len(mixing) == 3 and len(mixing[0]) == 2
    assert out["n_valid_pixels"] == 64


# ── change_detection ──────────────────────────────────────────────────


def _epoch_bands(seed, size=6):
    rng = np.random.default_rng(seed)
    return {
        "red": (rng.uniform(0, 1, size=(size, size))).round(6).tolist(),
        "nir": (rng.uniform(0, 1, size=(size, size)) * 2).round(6).tolist(),
    }


@pytest.mark.asyncio
async def test_detect_change_cva_behavioral(registry):
    # validation：缺 t2_bands → 校验错误
    bad = await registry.dispatch("detect_change_cva", {"t1_bands": _epoch_bands(1)})
    _assert_error_readable(bad)
    assert bad.get("code") == "VALIDATION_ERROR"

    # error path：两景角色集不一致 → UnsupportedBandSemantics
    mismatch = await registry.dispatch("detect_change_cva", {
        "t1_bands": _epoch_bands(1),
        "t2_bands": {**_epoch_bands(2), "swir": [[1.0]]},
    })
    _assert_error_readable(mismatch)
    assert "角色" in (mismatch.get("message") or mismatch.get("summary") or mismatch.get("error") or "")

    # happy path：同角色双时相 → 逐像元幅度 + 方向
    out = await registry.dispatch("detect_change_cva", {
        "t1_bands": _epoch_bands(1), "t2_bands": _epoch_bands(2),
        "t1_date": "2024-01-01", "t2_date": "2024-06-01",
    })
    assert out.get("success") is True, out
    assert out["roles_used"] == ["nir", "red"] or set(out["roles_used"]) == {"red", "nir"}
    stats = out["stats"]
    assert stats["total_pixels"] == 36 and stats["valid_pixels"] == 36
    assert len(out["magnitude"]) == 6 and len(out["magnitude"][0]) == 6
    assert "angle_rad" in out


@pytest.mark.asyncio
async def test_detect_ratio_change_behavioral(registry):
    # validation：缺 b → 校验错误
    bad = await registry.dispatch(
        "detect_ratio_change", {"a": [[1.0, 2.0], [3.0, 4.0]]})
    _assert_error_readable(bad)
    assert bad.get("code") == "VALIDATION_ERROR"

    # error path：形状不一致 → 诚实拒绝
    shape_err = await registry.dispatch(
        "detect_ratio_change", {"a": [[1.0, 2.0]], "b": [[1.0], [2.0]]})
    _assert_error_readable(shape_err)

    # happy path：a == b → ratio 恒 1，均值 1.0
    a = [[1.5, 2.5, 3.5], [4.5, 5.5, 6.5]]
    out = await registry.dispatch(
        "detect_ratio_change", {"a": a, "b": a, "method": "ratio"})
    assert out.get("success") is True, out
    assert out["method"] == "ratio"
    assert out["stats"]["mean"] == pytest.approx(1.0, abs=1e-6)
    assert out["stats"]["valid_pixels"] == 6


# ── temporal_tools ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_temporal_changepoint_behavioral(registry):
    # validation：缺 values → 校验错误
    bad = await registry.dispatch("temporal_changepoint", {})
    _assert_error_readable(bad)
    assert bad.get("code") == "VALIDATION_ERROR"

    # error path：n < 3 → 样本不足（工具层兜底为 type=error 应答）
    tiny = await registry.dispatch(
        "temporal_changepoint", {"values": [1.0, 2.0]})
    assert isinstance(tiny, dict) and tiny.get("type") == "error", tiny
    assert "样本不足" in (tiny.get("message") or "")

    # happy path：前 15 低后 15 高的台阶 → 显著变点定位在台阶处
    rng = np.random.default_rng(5)
    values = [0.0 + float(v) for v in rng.normal(0, 0.1, 15)] + \
             [5.0 + float(v) for v in rng.normal(0, 0.1, 15)]
    out = await registry.dispatch(
        "temporal_changepoint",
        {"values": values, "bootstrap_draws": 100, "seed": 42})
    assert out.get("success") is True, out
    assert out["significant"] is True
    # cusum 变点指标 = argmax_{k∈1..n-1} |Σ_{i≤k}(x_i − x̄)|，k 为累计计数
    # → 0 基索引落在台阶边界 14/15 处
    assert out["change_point_index"] in (14, 15)
    assert abs(out["magnitude"]) > 4.0
    assert out["p_value"] < 0.05
    assert out["n"] == 30 and out["bootstrap_draws"] == 100
