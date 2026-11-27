"""可复现选样测试（WP2 selection）。"""
from __future__ import annotations

import numpy as np
import pytest

from app.lib.survey_sampling.design import SurveyDesign, StratumSpec
from app.lib.survey_sampling.selection import (
    SampleUnit,
    derive_stratum_seed,
    draw_samples,
    draw_stratum_units,
    make_sample_id,
)


class _FakeFrame:
    """最小 frame 假体：unit_coordinates 契约（不做 IO）。"""

    def __init__(self) -> None:
        self.calls = 0

    def unit_coordinates(self, stratum_id, unit_index):
        self.calls += 1
        return (float(unit_index) * 2.0, float(unit_index) * 3.0,
                10.0 + unit_index * 1e-5, 20.0 + unit_index * 1e-5)


def _design(**overrides) -> SurveyDesign:
    kwargs = dict(
        design_id="svy_sel",
        design_type="stratified_srs",
        frame_ref="frame-x",
        frame_crs="EPSG:6933",
        strata=[StratumSpec("forest", 100, 100.0),
                StratumSpec("crop", 50, 50.0)],
        allocation={"forest": 6, "crop": 4},
        backup_per_stratum=2,
        seed=42,
        target_time="2026-11",
        objective="test",
        max_nonresponse_rate=0.2,
        source_version="fp",
        class_dictionary_version="{}",
        total_area=150.0,
    )
    kwargs.update(overrides)
    return SurveyDesign(**kwargs).with_digest()


def test_layer_srs_deterministic_and_wo_replacement() -> None:
    a = draw_stratum_units(100, 8, 12345)
    b = draw_stratum_units(100, 8, 12345)
    assert a == b
    assert len(set(a)) == len(a)                 # 不放回
    assert all(0 <= u < 100 for u in a)


def test_stratum_seed_independence() -> None:
    assert derive_stratum_seed(42, "forest") != derive_stratum_seed(42, "crop")
    assert derive_stratum_seed(42, "forest") == derive_stratum_seed(42, "forest")


def test_draw_samples_full_contract() -> None:
    frame = _FakeFrame()
    d = _design()
    units = draw_samples(d, frame)
    mains = [u for u in units if not u.is_backup]
    backups = [u for u in units if u.is_backup]
    assert len(mains) == 10                       # 6 + 4
    assert len(backups) == 4                      # 2 × 2
    assert frame.calls == len(units)
    for u in units:
        assert isinstance(u, SampleUnit)
        spec = next(s for s in d.strata if s.stratum_id == u.stratum_id)
        assert u.inclusion_prob == pytest.approx(
            d.allocation[u.stratum_id] / spec.N_units)
        assert u.weight == pytest.approx(1.0 / u.inclusion_prob)
        assert u.sample_id == make_sample_id(
            d.digest, u.stratum_id, u.unit_index)
    # 层内主样本序位连续、替补序位接续
    for sid in ("forest", "crop"):
        ranks = sorted(u.draw_rank for u in units if u.stratum_id == sid)
        assert ranks == list(range(len(ranks)))
    # 同一单元不重复入选
    assert len({(u.stratum_id, u.unit_index) for u in units}) == len(units)


def test_draw_samples_idempotent() -> None:
    d = _design()
    a = [u.as_dict() for u in draw_samples(d, _FakeFrame())]
    b = [u.as_dict() for u in draw_samples(d, _FakeFrame())]
    assert a == b


def test_sample_id_stable_across_frames() -> None:
    """sample_id 只依赖 (digest, 层, 单元索引)——坐标系统无关。"""
    sid = make_sample_id("digest-1", "forest", 7)
    assert sid == make_sample_id("digest-1", "forest", 7)
    assert sid != make_sample_id("digest-2", "forest", 7)
    assert sid != make_sample_id("digest-1", "crop", 7)


def test_overdraw_rejected() -> None:
    with pytest.raises(Exception, match="exceeds stratum population"):
        draw_stratum_units(5, 6, 1)
