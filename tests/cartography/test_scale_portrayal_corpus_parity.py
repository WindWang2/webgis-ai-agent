"""G27 共享契约语料 —— backend 侧双向漂移闸（P7 / A03 / A04 / A24）。

`tests/cartography/carto_ir_corpus/scale_portrayal_cases.json` 同时被
backend pytest 与 frontend vitest 消费；任意一侧改词表/档位不更新 corpus
即红。本文件不启动服务、不读数据、纯函数与静态语料校验。
"""
from __future__ import annotations

import json
from pathlib import Path

from app.lib.cartography import scale_portrayal as sp
from app.lib.cartography.scene_lod import build_label_zoom_bands

CORPUS_PATH = (
    Path(__file__).resolve().parent
    / "carto_ir_corpus" / "scale_portrayal_cases.json"
)


def _corpus() -> dict:
    return json.loads(CORPUS_PATH.read_text(encoding="utf-8"))


def test_corpus_vocab_matches_backend_constants():
    corpus = _corpus()
    assert corpus["corpus_version"] == sp.SCALE_POLICY_VERSION
    assert tuple(corpus["policy_modes"]) == sp.SCALE_POLICY_MODES
    assert tuple(corpus["units"]) == sp.SCALE_POLICY_UNITS


def test_label_zoom_bands_match_spec_projection_and_authority():
    """corpus 档 ≡ spec 投影；且投影不改权威表的数值/相邻边界。"""
    corpus = _corpus()
    bands = sp.spec_label_zoom_bands()
    assert corpus["label_zoom_bands"] == bands
    source = build_label_zoom_bands()
    assert len(bands) == len(source) == 4
    assert [b["topRatio"] for b in bands] == [b["topRatio"] for b in source]
    assert [b["sizeRatio"] for b in bands] == [b["sizeRatio"] for b in source]
    assert [b["minZoom"] for b in bands][1:] == [b["minZoom"] for b in source][1:]
    assert [b["maxZoom"] for b in bands][:-1] == [b["maxZoom"] for b in source][:-1]
    assert bands[0]["minZoom"] == sp.SPEC_ZOOM_DOMAIN_MIN == 0.0
    assert bands[-1]["maxZoom"] == sp.SPEC_ZOOM_DOMAIN_MAX == 24.0
    # 连续无缺口（A04 无死带）
    assert all(
        bands[i]["maxZoom"] == bands[i + 1]["minZoom"]
        for i in range(len(bands) - 1)
    )
    # 单调性（scene_lod 契约：zoom 越高标注比例/符号尺度不降）
    assert all(
        bands[i]["topRatio"] <= bands[i + 1]["topRatio"]
        and bands[i]["sizeRatio"] <= bands[i + 1]["sizeRatio"]
        for i in range(len(bands) - 1)
    )


def test_every_corpus_zoom_has_exactly_one_active_band():
    """所有语料 zoom（含 z=0 / z=19 / z=24 边界）恰落一档 —— 无死带。"""
    bands = sp.spec_label_zoom_bands()
    zooms = [float(c["zoom"]) for c in _corpus()["cases"]] + [0.0, 2.9, 19.0, 23.9, 24.0]
    for zoom in zooms:
        hits = [
            i for i, b in enumerate(bands)
            if b["minZoom"] <= min(24.0 - 1e-9, min(zoom, 24.0)) < b["maxZoom"]
        ]
        assert len(hits) == 1, (zoom, hits)


def test_bounded_explanation_is_bounded_and_keeps_diagnostics():
    """A24：解释面字节有界；裁剪优先保留 diagnostics。"""
    decision = sp.resolve_effective_scale_portrayal(
        zoom=10.0,
        layer={"id": "X"},
        policy={
            "version": sp.SCALE_POLICY_VERSION,
            "mode": "unknown_mode",
            "pinned_size_ratio": 999.0,
            "priority_field": "ghost",
        },
        available_fields=("name",),
        source_revision="r" * 200,
        frame={"center": [0.0, 30.0]},
    )
    payload = sp.bounded_explanation(decision)
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    assert len(raw) <= sp.MAX_EXPLANATION_BYTES
    assert payload["applied"] is False
    assert {d["code"] for d in payload["diagnostics"]} >= {
        "SCALE.POLICY.UNKNOWN_MODE",
        "SCALE.POLICY.INVALID_PINNED_RATIO",
        "SCALE.POLICY.GHOST_PRIORITY_FIELD",
    }
    assert len(payload["source_revision"]) <= 64
    assert all(len(v) <= 160 for v in payload["disclosures"])


def test_spec_projection_only_uses_existing_spec_fields():
    """不新增 spec 字段：投影键必须落在 schema 已有字段集合内。"""
    from app.lib.cartography.mapspec_schema import (
        MapSpecLabelZoomBand,
        MapSpecLayerLabel,
        MapThresholds,
    )

    decision = sp.resolve_effective_scale_portrayal(
        zoom=10.0, layer={"id": "L"},
        policy={"version": sp.SCALE_POLICY_VERSION, "mode": "auto_lod"},
    )
    patch = sp.project_scale_portrayal_spec(decision)
    assert set(patch) <= {"label", "thresholds"}
    assert set(patch["label"]) <= set(MapSpecLayerLabel.model_fields)
    assert set(patch["label"]["zoomBands"][0]) <= set(MapSpecLabelZoomBand.model_fields)
    assert set(patch["thresholds"]) <= set(MapThresholds.model_fields)
