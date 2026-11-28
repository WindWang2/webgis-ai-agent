"""设计合同与样本量预览测试（WP1）。"""
from __future__ import annotations

import pytest

from app.lib.survey_sampling.design import (
    MIN_N_PER_STRATUM_HARD,
    SurveyDesign,
    StratumSpec,
    design_digest,
    design_weights,
    preview_sample_size,
)
from app.lib.gis.scientific_errors import DegenerateData, InsufficientSamples


def _strata() -> list:
    return [
        StratumSpec(stratum_id="forest", N_units=5000, area=5000.0),
        StratumSpec(stratum_id="crop", N_units=3000, area=3000.0),
        StratumSpec(stratum_id="water", N_units=200, area=200.0),
    ]


def test_weights_area_proportional() -> None:
    W = design_weights(_strata())
    assert W.sum() == pytest.approx(1.0)
    assert W[0] == pytest.approx(5000 / 8200)


def test_preview_equal_allocation() -> None:
    p = preview_sample_size(_strata(), strategy="equal", n_total=90)
    assert sum(p["allocation"].values()) == 90
    assert all(v == 30 for v in p["allocation"].values())


def test_preview_target_se() -> None:
    """Eq.13：默认保守 S_h=0.5 ⇒ n = (0.5/S)²；S=0.05 → 100。"""
    p = preview_sample_size(_strata(), strategy="proportional",
                            target_se_oa=0.05)
    assert p["n_total"] == 100
    W = design_weights(_strata())
    alloc = p["allocation"]
    assert sum(alloc.values()) == 100
    # 比例分配：forest ≈ 100×0.6098
    assert alloc["forest"] == pytest.approx(round(100 * W[0]), abs=2)


def test_preview_no_rare_class_skip() -> None:
    """稀有类（water, N=200）不被跳过：等额保底 ≥ 硬下限。"""
    p = preview_sample_size(_strata(), strategy="equal", n_total=9)
    for sid, n in p["allocation"].items():
        assert n >= MIN_N_PER_STRATUM_HARD, f"{sid} below hard minimum"
    assert p["allocation"]["water"] >= MIN_N_PER_STRATUM_HARD


def test_preview_neyman_prefers_uncertain_large() -> None:
    prior = {"forest": 0.9, "crop": 0.9, "water": 0.5}
    p = preview_sample_size(_strata(), strategy="neyman", n_total=100,
                            prior_precision=prior)
    # water 变异最大 → 相对其权重获得超额分配
    W = design_weights(_strata())
    share_water = p["allocation"]["water"] / 100
    assert share_water > W[2]


def test_preview_budget_too_small_rejected() -> None:
    with pytest.raises(InsufficientSamples):
        preview_sample_size(_strata(), strategy="equal", n_total=5)


def test_preview_below_recommended_disclosed() -> None:
    p = preview_sample_size(_strata(), strategy="equal", n_total=60)
    assert any("below recommended" in w for w in p["warnings"])


def test_design_digest_stability() -> None:
    payload = {"a": 1, "b": [1, 2, 3]}
    assert design_digest(payload) == design_digest({"b": [1, 2, 3], "a": 1})
    assert design_digest(payload) != design_digest({**payload, "a": 2})


def _design(**overrides) -> SurveyDesign:
    kwargs = dict(
        design_id="svy_test",
        design_type="stratified_srs",
        frame_ref="frame-abc",
        frame_crs="EPSG:6933",
        strata=_strata(),
        allocation={"forest": 30, "crop": 20, "water": 10},
        backup_per_stratum=2,
        seed=42,
        target_time="2026-11",
        objective="县域土地覆盖核查",
        max_nonresponse_rate=0.2,
        source_version="fp-123",
        class_dictionary_version="{'1': 'forest'}",
        total_area=8200.0,
    )
    kwargs.update(overrides)
    return SurveyDesign(**kwargs)


def test_design_freeze_and_digest_roundtrip() -> None:
    d = _design().with_digest()
    assert d.digest
    again = _design().with_digest()
    assert d.digest == again.digest
    changed = _design(seed=43).with_digest()
    assert changed.digest != d.digest


def test_design_rejects_missing_stratum_allocation() -> None:
    with pytest.raises(ValueError, match="every stratum"):
        _design(allocation={"forest": 30, "crop": 20}).with_digest()


def test_design_rejects_unsupported_type() -> None:
    with pytest.raises(Exception, match="unsupported design type"):
        _design(design_type="adaptive_cluster")


def test_design_rejects_overdraw() -> None:
    with pytest.raises(InsufficientSamples):
        _design(allocation={"forest": 5000, "crop": 20, "water": 10},
                backup_per_stratum=1)


def test_design_rejects_bad_nonresponse_rate() -> None:
    with pytest.raises(ValueError, match="max_nonresponse_rate"):
        _design(max_nonresponse_rate=1.0)


def test_design_rejects_empty_stratum_spec() -> None:
    with pytest.raises(DegenerateData, match="N_units"):
        _design(strata=[StratumSpec(stratum_id="x", N_units=0, area=1.0)])
