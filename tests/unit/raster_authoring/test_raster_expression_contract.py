"""WP02：严格可序列化的栅格表达契约（已知答案 + 变形关系 + 故障回归）。"""
from __future__ import annotations

import json

import pytest

from app.lib.cartography.mapspec_schema import parse_mapspec
from app.schemas.raster_spec import (
    RASTER_EXPRESSION_SCHEMA_VERSION,
    RasterExpressionError,
    RasterExpressionSpec,
    RasterStretchBounds,
)
from app.services.raster_authoring.contract import (
    default_expression_request,
    resolve_expression,
)
from app.services.raster_authoring.palettes import canonical_colormap, colormap_lut
from app.services.raster_authoring.source import RasterSource


class _FakeSource:
    band_count = 2
    dataset_tags: dict = {}

    def band(self, index):
        return {"index": index, "label": f"band {index}"}


def _source(count: int, *, tags=None) -> RasterSource:
    class _D:
        band_count = count
        bands = []
        width = 8
        height = 8
        bounds = [10.0, 19.92, 10.08, 20.0]
        transform = [0.01, 0.0, 10.0, 0.0, -0.01, 20.0]
        crs = "EPSG:4326"
        nodata = None
        descriptor_bands = []

    return RasterSource(
        session_id="sess-g20-test", ref_id="ref:raster-test", path="/nonexistent",
        data_revision="fp:abc", content_fingerprint="abc", descriptor=_D(),
        band_meta=[{"index": i + 1, "label": f"band {i + 1}"} for i in range(count)],
        dataset_tags=tags or {},
    )


def test_two_band_default_is_single_and_explicit_rgb_is_rejected():
    src = _source(2)
    default = default_expression_request(src)
    assert default["mode"] == "single" and default["bands"] == [1]
    with pytest.raises(RasterExpressionError) as exc:
        resolve_expression({"bands": [1, 2], "stretch": {"min": 0, "max": 100}}, src)
    assert exc.value.code == "BAND_COUNT_UNSUPPORTED"
    assert "two-band" in exc.value.message


def test_four_band_rgb_order_is_preserved_not_sorted():
    src = _source(4)
    spec = resolve_expression(
        {"bands": [4, 3, 2], "stretch": {"min": 0, "max": 600}}, src
    )
    assert spec.bands == [4, 3, 2]
    assert spec.mode == "rgb"
    assert spec.colormap is None
    assert spec.channel_stretch(0) == (0.0, 600.0)


@pytest.mark.parametrize("bad,code", [
    ({"bands": [0], "stretch": {"min": 0, "max": 1}}, "BAND_OUT_OF_RANGE"),
    ({"bands": [1, 2, 9], "stretch": {"min": 0, "max": 1}}, "BAND_OUT_OF_RANGE"),
    ({"bands": [1], "stretch": {"min": float("nan"), "max": 1}}, "STRETCH_NON_FINITE"),
    ({"bands": [1], "stretch": {"min": 2, "max": 1}}, "STRETCH_REVERSED"),
    ({"bands": [1], "stretch": "auto", "colormap": "not_a_palette"}, "UNKNOWN_COLORMAP"),
    ({"bands": [1], "stretch": "auto", "resampling": "lanczos"}, "UNSUPPORTED_RESAMPLING"),
    ({"bands": [1], "stretch": "auto", "classification": "classified",
      "resampling": "bilinear"}, "CLASSIFIED_INTERPOLATION"),
    ({"bands": [1], "stretch": "auto", "opacity": 1.4}, "OPACITY_OUT_OF_RANGE"),
    ({"bands": [1, 1, 1], "stretch": "auto", "colormap": "viridis"}, "COLORMAP_ON_RGB"),
    ({"bands": [1], "stretch": "auto", "schema_version": "v0"}, "SCHEMA_VERSION_UNSUPPORTED"),
    ({"bands": [1], "stretch": {"min": 0, "max": 5,
      "per_channel": [[0, 1], [0, 2]]}}, "STRETCH_CHANNEL_MISMATCH"),
])
def test_invalid_inputs_get_specific_codes(bad, code):
    src = _source(4)
    with pytest.raises(RasterExpressionError) as exc:
        resolve_expression(bad, src)
    assert exc.value.code == code


def test_style_id_is_deterministic_and_detects_tampering():
    a = RasterExpressionSpec(
        mode="single", bands=[1],
        stretch=RasterStretchBounds(min=0.0, max=10.0),
        colormap="viridis", data_revision="fp:abc",
    ).finalize()
    b = RasterExpressionSpec(
        mode="single", bands=[1],
        stretch=RasterStretchBounds(min=0.0, max=10.0),
        colormap="viridis", data_revision="fp:abc",
    ).finalize()
    assert a.style_id == b.style_id == a.compute_style_id()
    tampered = a.model_copy(update={"opacity": 0.5})
    with pytest.raises(RasterExpressionError) as exc:
        tampered.finalize()
    assert exc.value.code == "STALE_STYLE"


def test_data_revision_is_part_of_style_identity():
    kw = dict(mode="single", bands=[1],
              stretch=RasterStretchBounds(min=0.0, max=10.0))
    a = RasterExpressionSpec(**kw, data_revision="fp:one").finalize()
    b = RasterExpressionSpec(**kw, data_revision="fp:two").finalize()
    assert a.style_id != b.style_id


def test_per_channel_stretch_is_not_a_single_global_slider():
    spec = RasterExpressionSpec(
        mode="rgb", bands=[4, 3, 2],
        stretch=RasterStretchBounds(
            min=0.0, max=1000.0,
            per_channel=[[0.0, 100.0], [0.0, 200.0], [0.0, 300.0]],
        ),
    ).finalize()
    assert [spec.channel_stretch(i) for i in range(3)] == [
        (0.0, 100.0), (0.0, 200.0), (0.0, 300.0)
    ]


def test_palette_names_have_one_explicit_mapping():
    assert canonical_colormap("Viridis") == "Viridis"       # cartography hex stops
    assert canonical_colormap("viridis") == "viridis"       # matplotlib
    assert canonical_colormap("RdYlGn_r") == "RdYlGn_r"     # reversed cartography
    assert canonical_colormap("bogus") is None
    assert colormap_lut("Viridis", 8).shape == (8, 3)
    assert colormap_lut("bogus", 8) is None


def test_mapspec_schema_roundtrip_and_unknown_fields_disclosure():
    spec = RasterExpressionSpec(
        mode="single", bands=[1],
        stretch=RasterStretchBounds(min=0.0, max=10.0),
        colormap="viridis", data_revision="fp:abc",
    ).finalize()
    doc = {
        "version": "1.9",
        "sources": {
            "raster-1": {
                "type": "numeric_raster",
                "schema_version": RASTER_EXPRESSION_SCHEMA_VERSION,
                "refId": "ref:raster-1",
                "bounds": [10.0, 19.9, 10.1, 20.0],
                "dataRevision": "fp:abc",
                "crs": "EPSG:4326",
                "bandCount": 1,
            }
        },
        "layers": [{
            "id": "layer-1", "type": "raster", "source": "raster-1",
            "raster_style": spec.model_dump(mode="json"),
        }],
    }
    result = parse_mapspec(doc)
    assert result.valid, result.invalid_fields
    assert result.document == json.loads(json.dumps(doc))  # canonical 保序深拷贝
    assert not [d for d in result.unknown_fields if "raster_style" in d.path]
