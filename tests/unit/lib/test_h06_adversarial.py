"""H06 对抗性测试：取消 / 兼容失败面 / 守卫 fail-open / 数值 parity golden。

正常路径之外的失败面锁定（Prompt Wave E）：协作式取消从 narrated 入口
生效；typed 失败经 to_llm_response 通道携带 guidance；placement 守卫在
探针缺席时 fail-open（不因守卫层阻塞执行）；核心统计量与 BASE_SHA
（930459ef）旧实现的数值 parity（容差 0 —— kernel 未改，逐位锁定）。
"""
import threading

import numpy as np
import pytest

from app.lib.cancellation import CancellationToken, use_token
from app.lib.gis.scientific_errors import ResourceScaleMismatch
from app.lib.geo_analysis.context import INLINE_MAX_FEATURES
from app.lib.geo_analysis.statistics import (
    geary_c_narrated,
    hotspot_narrated,
    moran_i_narrated,
)


def _points_fc(pts, field="val"):
    feats = []
    for (xy, v) in pts:
        feats.append({"type": "Feature",
                      "geometry": {"type": "Point", "coordinates": [xy[0], xy[1]]},
                      "properties": {field: v}})
    return {"type": "FeatureCollection", "features": feats}


def _clustered_field(seed=2026, per=15):
    rng = np.random.default_rng(seed)
    pts = []
    for cx, cy, val in [(116.39, 39.90, 100.0), (116.42, 39.92, 100.0),
                        (116.39, 39.95, 1.0), (116.42, 39.95, 1.0)]:
        for _ in range(per):
            pts.append(((cx + rng.normal(0, 3e-4), cy + rng.normal(0, 3e-4)),
                        float(val)))
    return _points_fc(pts)


# --------------------------------------------------------------------------- #
# E1 协作式取消：已取消 token 在入口检查点即抛（不进入 kernel）
# --------------------------------------------------------------------------- #
def test_cancelled_token_raises_before_compute():
    token = CancellationToken()
    token.cancel()
    fc = _clustered_field()
    with use_token(token):
        with pytest.raises(Exception) as ei:
            moran_i_narrated(fc, "val")
    from app.lib.cancellation import OperationCancelled
    assert isinstance(ei.value, OperationCancelled)


def test_uncancelled_token_allows_compute():
    token = CancellationToken()
    fc = _clustered_field()
    with use_token(token):
        res = moran_i_narrated(fc, "val")
    assert res.success


# --------------------------------------------------------------------------- #
# E2 数值 parity golden：与 BASE_SHA（930459ef）实现逐位一致
# --------------------------------------------------------------------------- #
def test_moran_golden_parity_with_base_implementation():
    res = moran_i_narrated(_clustered_field(), "val", permutations=99)
    assert res.success
    d = res.data
    assert d["moran_i"] == pytest.approx(1.0, abs=1e-10)
    assert d["expected_i"] == pytest.approx(-0.0169491525, abs=1e-9)
    assert d["p_value"] == pytest.approx(0.01, abs=1e-12)
    assert d["pattern"] == "clustering"
    assert d["n_features"] == 60
    assert d["permutations"] == 99


def test_geary_golden_parity_with_base_implementation():
    res = geary_c_narrated(_clustered_field(), "val", permutations=99)
    assert res.success
    d = res.data
    assert d["gearys_c"] == pytest.approx(0.0, abs=1e-10)
    assert d["expected_c"] == pytest.approx(1.0, abs=1e-10)
    assert d["p_value"] == pytest.approx(0.01, abs=1e-12)
    assert d["pattern"] == "clustering"


def test_hotspot_output_contract_stable():
    res = hotspot_narrated(_clustered_field(), "val")
    assert res.success
    assert set(res.data.keys()) == {
        "type", "features", "hot_spots_count", "cold_spots_count",
        "distance_band_m", "fdr_hot_spots_count", "expected_false_positives",
        "uncertainty",
    }


# --------------------------------------------------------------------------- #
# E3 typed 失败 → LLM 通道：error_type/correction_hint 透传、evidence 通道
# --------------------------------------------------------------------------- #
def test_typed_failure_flows_to_llm_response_shape():
    res = moran_i_narrated({"type": "FeatureCollection", "features": []}, "val")
    llm = res.to_llm_response()
    assert llm["success"] is False
    assert llm["error_type"] == "INVALID_GEOMETRY"
    assert llm["correction_hint"]
    assert llm["summary"] == "Invalid GeoJSON or no features found"


def test_scale_mismatch_survives_invalid_geometry_folding():
    # ResourceScaleMismatch 不是 InvalidGeometry 子类 —— 超限拒绝绝不能被
    # narrated 入口的 None 折叠吞掉（typed 拒绝必须原样上抛）。
    fc = {"type": "FeatureCollection",
          "features": [{"type": "Feature"}] * (INLINE_MAX_FEATURES + 5)}
    with pytest.raises(ResourceScaleMismatch):
        geary_c_narrated(fc, "val")
    with pytest.raises(ResourceScaleMismatch):
        hotspot_narrated(fc, "val")


# --------------------------------------------------------------------------- #
# E4 placement 守卫 fail-open 面：探针缺席不阻塞执行
# --------------------------------------------------------------------------- #
def test_guard_passes_when_capability_probe_absent(monkeypatch):
    from app.services.geocompute import tasks as tasks_mod

    monkeypatch.setattr(tasks_mod, "_local_capability", lambda: None)
    env = {"schema_version": "rg.v1", "resource_class": "heavy",
           "dims": {"memory_bytes": {"certainty": "estimated",
                                     "expected": 8 * 1024 * 1024 * 1024}}}
    ret = tasks_mod._placement_guard(
        type("T", (), {"request": type("R", (), {"retries": 0,
                                                 "kwargs": {}})()})(),
        type("N", (), {"node_id": "n"})(), env, "w")
    assert ret is None  # 探针缺席 → 放行（placement 第 1 层 gating 负责）


# --------------------------------------------------------------------------- #
# E5 字节复核阈值可通过常数 monkeypatch 驱动（不造大内存 fixture）
# --------------------------------------------------------------------------- #
def test_estimated_bytes_postcheck(monkeypatch):
    from app.lib.geo_analysis import context as ctx_mod

    monkeypatch.setattr(ctx_mod, "_INLINE_MAX_ESTIMATED_BYTES", 1)
    fc = _clustered_field()
    with pytest.raises(ResourceScaleMismatch) as ei:
        ctx_mod.validate_spatial_input(fc)
    assert "bytes" in str(ei.value.limit)
