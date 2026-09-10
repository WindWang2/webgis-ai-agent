"""兼容性资格矩阵测试（04-test-matrix §1 全 10 类）。"""
from __future__ import annotations

import pytest

from app.lib.modelops.compatibility import (
    FAILURE_BAND_COUNT,
    FAILURE_BAND_ORDER,
    FAILURE_CRS,
    FAILURE_DTYPE,
    FAILURE_RESOLUTION,
    FAILURE_TEMPORAL_LENGTH,
    InputProfile,
    WARNING_NODATA_HEAVY,
    qualify,
    report_to_error,
)
from app.lib.modelops.descriptor import (
    ClassSchema,
    ResolutionRange,
    SpatialRequirements,
    TemporalRequirements,
)
from app.lib.modelops.errors import CompatibilityError
from app.lib.modelops.promptable import PromptSpec
from app.lib.modelops.temporal import TemporalStackSpec


def _profile(**kw) -> InputProfile:
    base = dict(width=256, height=256, band_count=3, band_names=("red", "green", "blue"),
                dtype="float32", crs="EPSG:32650", m_per_px=1.0, nodata=-9999.0,
                nodata_ratio=0.0, temporal_length=1)
    base.update(kw)
    return InputProfile(**base)


def _desc(tiny_desc_factory, **kw):
    return tiny_desc_factory(
        spatial=SpatialRequirements(
            chip_size=(16, 16), context_size=(16, 16),
            resolution_range=ResolutionRange(min_m_per_px=0.1, max_m_per_px=10.0),
        ),
        temporal=TemporalRequirements(max_length=1),
        **{k: v for k, v in kw.items() if k != "spatial"},
    )


def codes(report):
    return {f.code for f in report.failures}


def test_wrong_band_count(tiny_desc_factory):
    report = qualify(_desc(tiny_desc_factory), _profile(band_count=4))
    assert FAILURE_BAND_COUNT in codes(report)


def test_band_order_missing_semantics(tiny_desc_factory):
    report = qualify(_desc(tiny_desc_factory), _profile(band_names=("blue", "green", "nir")))
    assert FAILURE_BAND_ORDER in codes(report)


def test_rgb_model_vs_multispectral(tiny_desc_factory):
    from app.lib.modelops.compatibility import FAILURE_MODALITY as M

    report = qualify(_desc(tiny_desc_factory), _profile(band_count=8))
    assert M in codes(report) or FAILURE_BAND_COUNT in codes(report)


def test_sar_missing_polarization(tiny_desc_factory):
    d = tiny_desc_factory(
        input_modalities=("sar",),
        input_bands=2,
        band_order=("VV", "VH"),
        class_schema=ClassSchema(classes=("bg", "t")),
    )
    report = qualify(d, _profile(band_count=2, band_names=("VV", "HH")))
    assert FAILURE_BAND_ORDER in codes(report)


def test_wrong_dtype(tiny_desc_factory):
    report = qualify(_desc(tiny_desc_factory), _profile(dtype="complex128"))
    assert FAILURE_DTYPE in codes(report)


def test_unsupported_resolution_rejected_without_policy(tiny_desc_factory):
    d = tiny_desc_factory(spatial=SpatialRequirements(
        chip_size=(16, 16), context_size=(16, 16),
        resolution_range=ResolutionRange(min_m_per_px=10.0, max_m_per_px=100.0),
        allow_reproject=False,
    ))
    report = qualify(d, _profile(m_per_px=0.5))
    assert FAILURE_RESOLUTION in codes(report)


def test_unsupported_resolution_with_explicit_reproject_yields_plan(tiny_desc_factory):
    d = tiny_desc_factory(spatial=SpatialRequirements(
        chip_size=(16, 16), context_size=(16, 16),
        resolution_range=ResolutionRange(min_m_per_px=10.0, max_m_per_px=100.0),
        resampling_policy="bilinear", allow_reproject=True,
    ))
    report = qualify(d, _profile(m_per_px=0.5))
    assert report.compatible
    assert report.reproject and report.reproject["target_m_per_px"] > 0.5


def test_invalid_crs(tiny_desc_factory):
    d = tiny_desc_factory(spatial=SpatialRequirements(
        chip_size=(16, 16), context_size=(16, 16),
        crs_requirements=("EPSG:32650",),
    ))
    report = qualify(d, _profile(crs="EPSG:4326"))
    assert FAILURE_CRS in codes(report)


def test_crs_reproject_needs_explicit_opt_in(tiny_desc_factory):
    d = tiny_desc_factory(spatial=SpatialRequirements(
        chip_size=(16, 16), context_size=(16, 16),
        crs_requirements=("EPSG:32650",),
        resampling_policy="nearest", allow_reproject=True,
    ))
    report = qualify(d, _profile(crs="EPSG:4326"))
    assert report.compatible
    assert report.reproject and report.reproject["target_crs"] == "EPSG:32650"


def test_temporal_length_mismatch(tiny_desc_factory):
    spec = TemporalStackSpec(times=("2026-01-01",) * 1 and ("2026-01-01", "2026-02-01",
                                                              "2026-03-01"), max_length=8)
    report = qualify(_desc(tiny_desc_factory), _profile(), temporal=spec)
    assert FAILURE_TEMPORAL_LENGTH in codes(report)


def test_temporal_model_requires_stack(tiny_desc_factory):
    from app.lib.modelops.capabilities import TASK_TEMPORAL_FORECAST

    d = tiny_desc_factory(
        task_types=(TASK_TEMPORAL_FORECAST,),
        class_schema=None,
        output_types=("temporal_stack",),
        temporal=TemporalRequirements(max_length=4),
    )
    report = qualify(d, _profile(temporal_length=1))
    assert FAILURE_TEMPORAL_LENGTH in codes(report)


def test_nodata_heavy_is_warning_not_failure(tiny_desc_factory):
    report = qualify(_desc(tiny_desc_factory), _profile(nodata_ratio=0.8))
    assert report.compatible
    assert WARNING_NODATA_HEAVY in {w.code for w in report.warnings}


def test_prompt_against_non_promptable_model(tiny_desc_factory):
    prompt = PromptSpec(points=((10, 10),))
    report = qualify(_desc(tiny_desc_factory), _profile(), prompt=prompt)
    assert not report.compatible


def test_report_to_error_is_typed(tiny_desc_factory):
    report = qualify(_desc(tiny_desc_factory), _profile(band_count=7))
    with pytest.raises(CompatibilityError) as excinfo:
        raise report_to_error(report)
    assert excinfo.value.failures
