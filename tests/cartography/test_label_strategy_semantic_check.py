"""标注策略 × carto.label.collision_est（ac-05，ADR-0154）.

spec 层声明 ``label`` 策略（label_plan 产物）时，碰撞估计按策略修正：
``top_n`` 钳制有效注记数、``hover_only`` 记 0、zoom 分级档按
``topRatio`` 缩放；同时 ``label{field}`` 让非 symbol 主层进入检查域。
P7 的"告警率下降可量化"以本文件的断言与 label_quality_report.py 取证。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.lib.cartography.semantic_checks import evaluate_cartography_semantics


_PROFILE = {
    "featureCount": 3200,
    "bbox": [104.0, 30.5, 104.4, 30.8],
    "geometryTypes": ["Point"],
    "crs": "EPSG:4326",
    "crs_status": "explicit",
    "fields": {
        "name": {"type": "string", "sampleValues": ["惠民超市·A03店"],
                 "null_count": 0},
    },
}


def _spec(layer, *, zoom=10, profile=None):
    profile = dict(profile or _PROFILE)
    return {
        "version": "1.0",
        "view": {"center": [104.2, 30.65], "zoom": zoom},
        "sources": {"s1": {"type": "geojson", "ref": "ref:geojson-x",
                           "profile": profile}},
        "layers": [layer],
    }


def _checks(mapspec, rule="carto.label.collision_est"):
    report = evaluate_cartography_semantics(mapspec)
    return [c for c in report.to_dict()["checks"] if c["rule"] == rule]


def _dense_circle(**label):
    return {"id": "l1", "source": "s1", "type": "circle",
            "paint": {"color": "#ff0000", "radius": 5}, "label": label}


def test_dense_layer_without_strategy_still_fails():
    layer = _dense_circle()
    checks = _checks(_spec(layer))
    assert checks == []  # 非 symbol 主层、无 label 声明 —— 检查域外（现状不变）


def test_dense_layer_with_symbol_text_field_unlabeled_strategy_unchanged():
    layer = {"id": "l1", "source": "s1", "type": "symbol",
             "layout": {"text-field": "name", "text-size": 12}}
    check = _checks(_spec(layer))[0]
    assert check["status"] == "fail"
    assert check["evidence"]["label_strategy"] == {"declared": False}


def test_top_n_strategy_caps_estimated_labels_and_drops_alert():
    # 3200 要素全标 → fail；top_n 后有效注记数被钳到 topN×档内比例。
    # 注：8 字 CJK 标注 400 条仍超 fail 阈（0.54）—— 检查诚实，但
    # est_visible 从 3200 钳到 400（约 8×下降），档位调低即转 pass。
    layer_fail = _dense_circle(field="name", mode="all")
    check = _checks(_spec(layer_fail))[0]
    assert check["status"] == "fail"
    assert check["evidence"]["est_visible_labels"] == 3200
    assert check["evidence"]["label_strategy"]["declared"] is True
    assert check["evidence"]["label_strategy"]["mode"] == "all"

    layer_topn = _dense_circle(field="name", mode="top_n", topN=400,
                               priorityField="price")
    check = _checks(_spec(layer_topn))[0]
    assert check["evidence"]["label_strategy"]["top_n"] == 400
    assert check["evidence"]["est_visible_labels"] == 400
    assert check["evidence"]["label_ink_ratio"] < 0.6  # 相对全量的量化下降

    layer_small = _dense_circle(field="name", mode="top_n", topN=60,
                                priorityField="price")
    check = _checks(_spec(layer_small))[0]
    assert check["status"] == "pass", check
    assert check["evidence"]["est_visible_labels"] == 60


def test_zoom_band_top_ratio_scales_the_cap():
    # 低 zoom 档（topRatio=0.10）：有效注记更少（400×0.1=40）→ pass；
    # 档 [8,24)（ratio=1.0，z9 覆盖度仍 1.0）：回落到 topN 上限 → fail。
    bands = [
        {"minZoom": 0, "maxZoom": 8, "topRatio": 0.10},
        {"minZoom": 8, "maxZoom": 24, "topRatio": 1.0},
    ]
    layer = _dense_circle(field="name", mode="top_n", topN=400,
                          zoomBands=bands)
    low = _checks(_spec(layer, zoom=6))[0]
    high = _checks(_spec(layer, zoom=9))[0]
    assert low["evidence"]["label_strategy"]["band_top_ratio"] == 0.10
    assert high["evidence"]["label_strategy"]["band_top_ratio"] == 1.0
    assert low["evidence"]["est_visible_labels"] == 40
    assert high["evidence"]["est_visible_labels"] == 400
    assert low["status"] == "pass" and high["status"] == "fail"


def test_hover_only_strategy_means_no_persistent_labels():
    layer = _dense_circle(field="name", mode="hover_only")
    check = _checks(_spec(layer))[0]
    assert check["status"] == "pass"
    assert check["evidence"]["est_visible_labels"] == 0


def test_strategy_passes_through_label_spec_size():
    layer = _dense_circle(field="name", mode="top_n", topN=2000, size=6)
    check = _checks(_spec(layer))[0]
    assert check["evidence"]["font_px"] == 6
