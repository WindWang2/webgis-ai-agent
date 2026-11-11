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
    """z ∈ [0,24) 恰落一档；z=24 按渲染器域上界显式钳末档（R1 F12）。

    不再用 1e-9 把 24 偷偷拉回域内伪装「恰落一档」：24 是 MapLibre 域
    闭边界，语义 = 钳到末档（与 representation_mode_at_zoom 同口径）。
    """
    bands = sp.spec_label_zoom_bands()
    zooms = [float(c["zoom"]) for c in _corpus()["cases"]] + [0.0, 2.9, 19.0, 23.9]
    for zoom in zooms:
        hits = [
            i for i, b in enumerate(bands)
            if b["minZoom"] <= zoom < b["maxZoom"]
        ]
        assert len(hits) == 1, (zoom, hits)
    assert bands[-1]["maxZoom"] == 24.0
    assert sp.resolve_effective_scale_portrayal(
        zoom=24.0, layer={"id": "L"},
        policy={"version": sp.SCALE_POLICY_VERSION, "mode": "auto_lod"},
    ).band_id == "lod-3"


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
    # review F5：默认只投影既有 label.zoomBands；decimate_budget 是解释面
    # 披露，不自动改写文档级导出预算（原实现投影后被两条落笔路径丢弃）。
    patch = sp.project_scale_portrayal_spec(decision)
    assert set(patch) == {"label"}
    assert set(patch["label"]) <= set(MapSpecLayerLabel.model_fields)
    assert set(patch["label"]["zoomBands"][0]) <= set(MapSpecLabelZoomBand.model_fields)
    # 解释面档表 == spec 投影档表（review F8）
    assert decision.label_zoom_bands == sp.spec_label_zoom_bands()
    assert patch["label"]["zoomBands"] == sp.spec_label_zoom_bands()
    # 显式要求预算时，字段也必须落在既有 schema 内
    with_budget = sp.project_scale_portrayal_spec(decision, include_budget=True)
    assert set(with_budget) == {"label", "thresholds"}
    assert set(with_budget["thresholds"]) <= set(MapThresholds.model_fields)
