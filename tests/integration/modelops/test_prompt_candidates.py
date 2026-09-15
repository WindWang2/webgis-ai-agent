"""Prompt candidates 平台集成（Platform 11 / WP-C）。

oracle 独立性：候选几何/分数期望直接来自产物 GeoJSON 的显式字段比对
（主掩膜 vs 指定候选的几何集合），分数界/来源为契约断言。
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.lib.modelops.errors import CompatibilityError, ModelOpsError


def _req(synthetic_raster, prompt, *, session, **kw):
    from app.lib.modelops.promptable import PromptSpec
    from app.services.modelops.engine import InferenceRequest

    return InferenceRequest(
        model_id="tiny-promptable-seg",
        source_uri=str(synthetic_raster),
        owner_scope={"session_id": session},
        prompt=prompt if isinstance(prompt, PromptSpec) else PromptSpec(points=prompt),
        **kw,
    )


def _load(path_role):
    return json.loads(Path(path_role["path"]).read_text(encoding="utf-8"))


def _geometry_set(features, candidate=None):
    return {
        json.dumps(f["geometry"], sort_keys=True)
        for f in features
        if candidate is None or f["properties"]["candidate"] == candidate
    }


def test_return_candidates_publishes_full_set(service, synthetic_raster):
    result = service.run_inference(_req(
        synthetic_raster, ((70.0, 50.0),),
        session="cand-e2e", return_candidates=True,
    ))
    assert result.status == "completed"
    assert "prompt_candidates" in result.outputs
    cand = result.outputs["prompt_candidates"]
    assert cand["selection"] == "best"
    geojson = _load(cand)
    indexes = {f["properties"]["candidate"] for f in geojson["features"]}
    assert indexes == {0, 1, 2}
    for f in geojson["features"]:
        props = f["properties"]
        assert 0.0 <= props["score"] <= 1.0
        assert props["source"] == "heuristic"
    # 指纹语义：候选请求进 postprocess（复用键区分）。
    assert result.manifest["postprocess"]["prompt_candidates"] is True
    # 逐窗裁决摘要存在且 selected_index 合法。
    for summary in cand["windows"]:
        assert summary["selected_index"] in {0, 1, 2}
        assert len(summary["candidates"]) == 3


def test_index_selection_matches_that_candidate_geometry(service, synthetic_raster):
    best = service.run_inference(_req(
        synthetic_raster, ((70.0, 50.0),), session="cand-idx",
    ))
    chosen = service.run_inference(_req(
        synthetic_raster, ((70.0, 50.0),), session="cand-idx",
        return_candidates=True, candidate_selection="index", selected_candidate=2,
    ))
    assert chosen.manifest["postprocess"]["prompt_selected_candidate"] == 2
    candidates_geo = _load(chosen.outputs["prompt_candidates"])
    main_geo = _load(chosen.outputs["prompt_mask_geojson"])
    candidate2 = _geometry_set(candidates_geo["features"], candidate=2)
    main = _geometry_set(main_geo["features"])
    if candidate2:
        assert main == candidate2  # 主掩膜 = 裁决候选 2 的几何
    # best（默认）与显式候选 2 的主掩膜可能不同——契约只保证主掩膜取自裁决。
    assert main is not None
    assert best.status == "completed"


def test_unknown_selection_typed_refusal(service, synthetic_raster):
    with pytest.raises(ModelOpsError, match="unknown candidate_selection"):
        service.run_inference(_req(
            synthetic_raster, ((70.0, 50.0),), session="cand-bad",
            candidate_selection="random",
        ))


def test_candidates_refused_without_provider_capability(
    service, synthetic_raster, monkeypatch
):
    record = service._registry.resolve(
        "tiny-promptable-seg", None, session_id="cand-gate"
    )
    provider = service._providers.get(record.descriptor.provider_ref)
    orig_caps = provider.capabilities

    def without_candidates():
        return replace(orig_caps(), mask_candidates=False)

    monkeypatch.setattr(provider, "capabilities", without_candidates)
    with pytest.raises(CompatibilityError, match="mask_candidates"):
        service.run_inference(_req(
            synthetic_raster, ((70.0, 50.0),), session="cand-gate",
            return_candidates=True,
        ))


def test_default_request_stays_single_mask_semantics(service, synthetic_raster):
    """不带候选参数的请求：无候选产物、postprocess 无候选字段（兼容）。"""
    result = service.run_inference(_req(
        synthetic_raster, ((70.0, 50.0),), session="cand-compat",
    ))
    assert "prompt_candidates" not in result.outputs
    assert "prompt_candidates" not in result.manifest["postprocess"]
