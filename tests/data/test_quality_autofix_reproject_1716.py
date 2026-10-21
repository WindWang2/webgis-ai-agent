"""#1716 回归 —— autofix reproject 不得半变换。

旧实现只对 ``features[:max_scan]`` 重投影，却把整个 payload 打上目标
CRS 标签：扫描窗外的要素坐标静默留在源 CRS，下游按错误 CRS 解释坐标
（同一函数的 dedupe 分支对未扫描尾部的处理是诚实标注，reproject 没有）。

修复后 reproject 全量变换（CRS 标签是 payload 级声明，半变换即伪造），
证据报告 ``features_transformed``。
"""
from __future__ import annotations

from app.services.data_quality.autofix import (
    AutofixStep,
    apply_autofix,
    dry_run_autofix,
)


def _point_fc(n: int, lon0: float = 116.0) -> dict:
    return {"geojson": {"type": "FeatureCollection", "features": [
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [lon0 + i * 0.01, 39.5]},
         "properties": {}} for i in range(n)]}}


def test_reproject_transforms_every_feature_beyond_scan_window():
    """max_scan 窗外的要素也必须被重投影 —— 否则 CRS 标签与坐标不一致。"""
    payload = dict(_point_fc(6))
    payload["crs"] = "EPSG:4326"
    steps = [AutofixStep(operation="reproject",
                         params={"target_crs": "EPSG:3857"})]

    new_payload, evidence = apply_autofix(payload, steps, max_scan=2)
    entry = evidence["operations"][0]
    assert entry["reprojected"] is True
    assert entry["features_transformed"] == 6, "扫描窗外要素不得留在源 CRS"
    assert new_payload["crs"] == "EPSG:3857"

    from pyproj import Transformer

    fwd = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    for i, f in enumerate(new_payload["geojson"]["features"]):
        x, y = f["geometry"]["coordinates"]
        ex, ey = fwd.transform(116.0 + i * 0.01, 39.5)
        assert abs(x - ex) < 1e-6 and abs(y - ey) < 1e-6, \
            f"要素 {i} 未按目标 CRS 变换：{(x, y)} != {(ex, ey)}"


def test_reproject_without_pyproj_discloses_label_only():
    """pyproj 缺席时仍只打标签，但证据诚实写明（不冒充已重投影）。"""
    import app.services.data_quality.autofix as af

    payload = dict(_point_fc(3))
    payload["crs"] = "EPSG:4326"
    steps = [AutofixStep(operation="reproject",
                         params={"target_crs": "EPSG:3857"})]

    real = af._reproject_features
    af._reproject_features = lambda feats, src, dst: False
    try:
        _new_payload, evidence = apply_autofix(payload, steps)
    finally:
        af._reproject_features = real
    entry = evidence["operations"][0]
    assert entry["reprojected"] is False
    assert entry["features_transformed"] == 0
    assert "pyproj unavailable" in entry["note"]


def test_dry_run_reproject_does_not_mutate_input():
    import copy

    payload = dict(_point_fc(3))
    payload["crs"] = "EPSG:4326"
    snapshot = copy.deepcopy(payload)
    steps = [AutofixStep(operation="reproject",
                         params={"target_crs": "EPSG:3857"})]
    preview = dry_run_autofix(payload, steps)
    assert preview["would_change"]["reproject"]["reproject_coordinates"] is True
    assert payload == snapshot, "dry-run 绝不改动输入"
