"""GeoPrompt artifact 契约测试（Platform 11 / WP-A）。

oracle 独立性纪律：
- 地图→像素期望用**前向**仿射在测试内手算（选已知像素 → 推地图坐标），
  实现用逆变换——两侧数学路径不同；
- 栅格化期望为**手导像元集合**（像元中心包含 / 触及语义），不调用平台
  栅格化器生成期望；
- artifact 身份测试直接比较 sha256 行为（同输入同 id、异输入异 id），
  不复用 identity_payload。
"""
from __future__ import annotations

import hashlib
import json

import numpy as np
import pytest
from affine import Affine

from app.lib.modelops.errors import PlanningError, PromptArtifactError
from app.lib.modelops.foundation import prompt_span, prompt_windows
from app.lib.modelops.geo_prompt import (
    GEO_PROMPT_SCHEMA_VERSION,
    MAX_GEOMETRY_PER_KIND,
    PROMPT_ROUNDTRIP_TOL_PX,
    GeoPromptArtifact,
    GeoPromptTarget,
    GeoPromptTime,
    MaskReference,
    ReferenceLayer,
    compile_prompt,
)
from app.lib.modelops.promptable import PromptSpec


# ── 构造校验 ──────────────────────────────────────────────────────────


def test_artifact_requires_at_least_one_prompt_input():
    with pytest.raises(PromptArtifactError, match="at least one prompt input"):
        GeoPromptArtifact()


def test_unsupported_schema_version_rejected():
    with pytest.raises(PromptArtifactError, match="schema_version"):
        GeoPromptArtifact(schema_version=2, points=((1.0, 1.0),))


def test_crs_declaration_validation():
    with pytest.raises(PromptArtifactError, match="invalid crs"):
        GeoPromptArtifact(crs="  ", points=((1.0, 1.0),))
    with pytest.raises(PromptArtifactError, match="invalid crs"):
        GeoPromptArtifact(crs="EPSG:4326\nx", points=((1.0, 1.0),))


@pytest.mark.parametrize(
    "kwargs,match",
    [
        ({"points": ((float("nan"), 1.0),)}, "non-finite"),
        ({"points": ((1.0, 1.0),), "boxes": ((1.0, 1.0, 0.0, 2.0),)}, "positive extent"),
        ({"points": ((1.0, 1.0),), "polylines": (((1.0, 1.0),),)}, "polyline requires"),
        ({"points": ((1.0, 1.0),), "polygons": (((0.0, 0.0), (1.0, 1.0)),)}, "polygon ring requires"),
        ({"points": ((1.0, 1.0),), "combine": "xor"}, "combine policy"),
    ],
)
def test_geometry_validation(kwargs, match):
    with pytest.raises(PromptArtifactError, match=match):
        GeoPromptArtifact(**kwargs)


def test_geometry_per_kind_cap():
    pts = tuple((float(i), 0.0) for i in range(MAX_GEOMETRY_PER_KIND + 1))
    with pytest.raises(PromptArtifactError, match="too many points"):
        GeoPromptArtifact(points=pts)


def test_mask_reference_digest_format_gate():
    with pytest.raises(PromptArtifactError, match="64-hex"):
        MaskReference(path="mask.tif", sha256="abc")
    with pytest.raises(PromptArtifactError, match="band must be"):
        MaskReference(path="mask.tif", sha256="a" * 64, band=0)


def test_reference_layer_strategy_gate():
    with pytest.raises(PromptArtifactError, match="strategy"):
        ReferenceLayer(uri="ref.tif", strategy="magic")
    with pytest.raises(PromptArtifactError, match="finite threshold"):
        ReferenceLayer(uri="ref.tif", strategy="threshold", threshold=None)


def test_mask_and_reference_mutually_exclusive():
    with pytest.raises(PromptArtifactError, match="mutually exclusive"):
        GeoPromptArtifact(
            mask_ref=MaskReference(path="m.tif", sha256="a" * 64),
            reference_layer=ReferenceLayer(uri="r.tif"),
        )


def test_time_semantics_validation():
    with pytest.raises(PromptArtifactError, match="at least one"):
        GeoPromptTime()
    with pytest.raises(PromptArtifactError, match="ISO-8601"):
        GeoPromptTime(acquisition="2026/09/15")


# ── 身份（内容寻址）───────────────────────────────────────────────────


def test_artifact_id_content_addressed_and_provenance_free():
    base = dict(points=((2.0, 3.0),), boxes=((0.0, 0.0, 4.0, 4.0),))
    a = GeoPromptArtifact(**base)
    b = GeoPromptArtifact(**base)
    assert a.artifact_id == b.artifact_id
    # 身份与 sha256(canonical json) 一致（口径锁）。
    canonical = json.dumps(
        a.identity_payload(), sort_keys=True, separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    assert a.artifact_id == hashlib.sha256(canonical).hexdigest()
    # 几何变化 → 身份变化。
    c = GeoPromptArtifact(points=((2.0, 3.5),), boxes=((0.0, 0.0, 4.0, 4.0),))
    assert c.artifact_id != a.artifact_id
    # provenance 元数据不进身份。
    d = GeoPromptArtifact(**base, created_by="ui", note="x", source_refs=("s1",))
    assert d.artifact_id == a.artifact_id


def test_payload_roundtrip_and_tamper_detection():
    artifact = GeoPromptArtifact(
        crs="EPSG:32650",
        polygons=(((116.0, 39.0), (116.1, 39.0), (116.1, 39.1), (116.0, 39.1)),),
        target=GeoPromptTarget(model_id="sam-like-x", band_names=("B04", "B03", "B02")),
        time=GeoPromptTime(acquisition="2026-09-15T00:00:00Z"),
        created_by="geoai-panel",
    )
    payload = artifact.to_payload()
    restored = GeoPromptArtifact.from_payload(payload)
    assert restored.artifact_id == artifact.artifact_id
    assert restored.polygons == artifact.polygons
    # 篡改几何但保留声明 id → fail-closed。
    tampered = dict(payload)
    tampered["polygons"] = [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]]
    with pytest.raises(PromptArtifactError, match="id mismatch"):
        GeoPromptArtifact.from_payload(tampered)


# ── 编译：像素直通 ────────────────────────────────────────────────────


def test_compile_pixel_artifact_passthrough_and_anchor():
    artifact = GeoPromptArtifact(
        points=((10.0, 12.0),),
        boxes=((20.0, 5.0, 8.0, 6.0),),
    )
    compiled = compile_prompt(
        artifact, transform=None, raster_height=64, raster_width=64
    )
    assert compiled.prompt.points == ((10.0, 12.0),)
    assert compiled.prompt.boxes == ((20.0, 5.0, 8.0, 6.0),)
    assert compiled.audit.coordinate_space == "pixel"
    assert compiled.audit.roundtrip_max_error_px == 0.0
    # anchor = 几何并集包围盒（半开）：x ∈ [10,29)（box 右缘 28），y ∈ [5,13)。
    assert compiled.audit.anchor_box == (10, 5, 29, 13)
    assert compiled.prompt.anchor_box == (10.0, 5.0, 19.0, 8.0)


def test_prompt_spec_geometry_payload_anchor_compat():
    # 无 anchor：字段集与旧口径一致（reuse key 字节兼容）。
    legacy = PromptSpec(points=((1.0, 1.0),)).geometry_payload()
    assert "prompt_anchor" not in legacy
    anchored = PromptSpec(points=((1.0, 1.0),), anchor_box=(0.0, 0.0, 4.0, 4.0))
    assert anchored.geometry_payload()["prompt_anchor"] == [0.0, 0.0, 4.0, 4.0]


def test_prompt_spec_anchor_validation():
    with pytest.raises(Exception, match="anchor box must have positive extent"):
        PromptSpec(points=((1.0, 1.0),), anchor_box=(0.0, 0.0, -1.0, 4.0))


# ── 编译：地图 CRS → 像素（前向手算 oracle）──────────────────────────


def _forward_map(transform: Affine, col: float, row: float):
    return transform * (col, row)


def test_compile_map_crs_known_answer_roundtrip():
    transform = Affine(2.0, 0.0, 100.0, 0.0, -2.0, 200.0)
    # 独立 oracle：选已知像素，前向推地图坐标；编译必须还原该像素。
    known_pixels = [(3.0, 7.0), (40.5, 12.25), (0.0, 0.0)]
    map_points = tuple(_forward_map(transform, c, r) for c, r in known_pixels)
    artifact = GeoPromptArtifact(crs="EPSG:32650", points=map_points)
    compiled = compile_prompt(
        artifact, transform=transform, raster_height=64, raster_width=64
    )
    for (col, row), got in zip(known_pixels, compiled.prompt.points):
        assert abs(got[0] - col) <= 1e-9
        assert abs(got[1] - row) <= 1e-9
    assert compiled.audit.coordinate_space == "map→pixel"
    assert compiled.audit.roundtrip_max_error_px <= PROMPT_ROUNDTRIP_TOL_PX


def test_compile_map_crs_requires_transform():
    artifact = GeoPromptArtifact(crs="EPSG:4326", points=((1.0, 1.0),))
    with pytest.raises(PlanningError, match="no transform"):
        compile_prompt(artifact, transform=None, raster_height=8, raster_width=8)


def test_compile_degenerate_transform_rejected():
    # 奇异仿射（零行列式）：逆不存在 → typed 拒绝。
    singular = Affine(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    artifact = GeoPromptArtifact(crs="EPSG:4326", points=((1.0, 1.0),))
    with pytest.raises(PlanningError, match="invertible"):
        compile_prompt(artifact, transform=singular, raster_height=8, raster_width=8)


def test_compile_ill_conditioned_roundtrip_tolerance():
    # 0.1 二进制不可精确表示 → 前向/逆向存在真实浮点残差（~1e-15px）：
    # 默认容差（1e-6）必须放行；零容差必须 typed 拒绝（同一断言路径）。
    transform = Affine(0.1, 0.0, 0.0, 0.0, -0.1, 0.0)
    artifact = GeoPromptArtifact(
        crs="EPSG:32650", points=(_forward_map(transform, 7.0, 9.0),)
    )
    compiled = compile_prompt(
        artifact, transform=transform, raster_height=64, raster_width=64
    )
    assert compiled.audit.roundtrip_max_error_px <= PROMPT_ROUNDTRIP_TOL_PX
    with pytest.raises(PlanningError, match="roundtrip error"):
        compile_prompt(
            artifact, transform=transform, raster_height=64, raster_width=64,
            tolerance_px=0.0,
        )


# ── 编译：栅格化 known-answer ─────────────────────────────────────────


def test_polygon_rasterization_known_answer():
    # 8x8 栅格上 (2,2)-(5,5) 地图单位正方形（像素坐标直通）：
    # 像元中心包含语义 → 恰好 rows/cols 2..4 为真（手导期望）。
    artifact = GeoPromptArtifact(
        polygons=(((2.0, 2.0), (5.0, 2.0), (5.0, 5.0), (2.0, 5.0)),)
    )
    compiled = compile_prompt(
        artifact, transform=None, raster_height=8, raster_width=8
    )
    mask = compiled.prompt.prior_masks[0]
    expected = np.zeros((8, 8), dtype=bool)
    expected[2:5, 2:5] = True
    assert mask.dtype == bool
    np.testing.assert_array_equal(mask, expected)
    assert compiled.audit.derived_mask_source == "polygon/polyline rasterized"
    assert compiled.audit.derived_mask_pixels == 64
    assert compiled.audit.anchor_box == (2, 2, 5, 5)


def test_polyline_rasterization_known_answer():
    # y=1.5 的水平线（行 1 中心）：触及语义 → 行 1、列 1..5 为真。
    artifact = GeoPromptArtifact(
        polylines=(((1.5, 1.5), (5.5, 1.5)),)
    )
    compiled = compile_prompt(
        artifact, transform=None, raster_height=8, raster_width=8
    )
    mask = compiled.prompt.prior_masks[0]
    expected = np.zeros((8, 8), dtype=bool)
    expected[1, 1:6] = True
    np.testing.assert_array_equal(mask, expected)
    assert compiled.audit.anchor_box == (1, 1, 6, 2)


def test_degenerate_polygon_rejected():
    with pytest.raises(PromptArtifactError, match="degenerate"):
        compile_prompt(
            GeoPromptArtifact(polygons=(((1.0, 1.0), (2.0, 2.0), (1.0, 1.0)),)),
            transform=None, raster_height=8, raster_width=8,
        )


# ── 编译：mask sidecar / reference layer（注入式 IO）─────────────────


def test_mask_sidecar_requires_loader_fail_closed():
    artifact = GeoPromptArtifact(
        mask_ref=MaskReference(path="sidecar.tif", sha256="a" * 64)
    )
    with pytest.raises(PromptArtifactError, match="mask_loader"):
        compile_prompt(artifact, transform=None, raster_height=8, raster_width=8)


def test_mask_sidecar_grid_alignment_gate():
    artifact = GeoPromptArtifact(
        mask_ref=MaskReference(path="sidecar.tif", sha256="a" * 64)
    )
    wrong_shape = np.zeros((4, 4), dtype=bool)
    with pytest.raises(PromptArtifactError, match="grid-aligned"):
        compile_prompt(
            artifact, transform=None, raster_height=8, raster_width=8,
            mask_loader=lambda path, band: wrong_shape,
        )
    good = np.zeros((8, 8), dtype=bool)
    good[2:5, 2:5] = True
    compiled = compile_prompt(
        artifact, transform=None, raster_height=8, raster_width=8,
        mask_loader=lambda path, band: good,
    )
    assert compiled.audit.derived_mask_source == "mask_sidecar"
    np.testing.assert_array_equal(compiled.prompt.prior_masks[0], good)


def test_reference_layer_resolution_strategies():
    data = np.array([[0.0, 1.0], [np.nan, 3.0]], dtype=np.float32)
    base = dict(transform=None, raster_height=2, raster_width=2,
                reference_reader=lambda uri, band: data)
    # nonzero：非零且有限 → [[F,T],[F,T]]
    compiled = compile_prompt(
        GeoPromptArtifact(reference_layer=ReferenceLayer(uri="r.tif")),
        **base,
    )
    np.testing.assert_array_equal(
        compiled.prompt.prior_masks[0], np.array([[False, True], [False, True]])
    )
    # threshold=2.5：仅 3.0 → 右下
    compiled = compile_prompt(
        GeoPromptArtifact(reference_layer=ReferenceLayer(
            uri="r.tif", strategy="threshold", threshold=2.5)),
        **base,
    )
    np.testing.assert_array_equal(
        compiled.prompt.prior_masks[0], np.array([[False, False], [False, True]])
    )
    # invert
    compiled = compile_prompt(
        GeoPromptArtifact(reference_layer=ReferenceLayer(
            uri="r.tif", strategy="threshold", threshold=2.5, invert=True)),
        **base,
    )
    np.testing.assert_array_equal(
        compiled.prompt.prior_masks[0], np.array([[True, True], [True, False]])
    )


def test_reference_layer_requires_reader_fail_closed():
    artifact = GeoPromptArtifact(reference_layer=ReferenceLayer(uri="r.tif"))
    with pytest.raises(PromptArtifactError, match="reference_reader"):
        compile_prompt(artifact, transform=None, raster_height=8, raster_width=8)


def test_empty_compiled_mask_with_no_geometry_rejected():
    empty = np.zeros((8, 8), dtype=bool)
    artifact = GeoPromptArtifact(
        mask_ref=MaskReference(path="s.tif", sha256="a" * 64)
    )
    with pytest.raises(PromptArtifactError, match="select nothing"):
        compile_prompt(
            artifact, transform=None, raster_height=8, raster_width=8,
            mask_loader=lambda p, b: empty,
        )


# ── anchor → 窗口策略（mask-only 锚定）───────────────────────────────


def test_prompt_span_uses_anchor_for_mask_only():
    prompt = PromptSpec(prior_masks=(np.ones((4, 4), dtype=bool),),
                        anchor_box=(100.0, 200.0, 8.0, 8.0))
    assert prompt_span(prompt) == (100, 200, 108, 208)


def test_prompt_windows_anchor_grid_for_large_mask_prompt():
    # 9000px 栅格、anchor 在右下角：mask-only 旧口径只会开左上 4096 窗
    # （错位）；anchor 网格必须覆盖 anchor 且每窗 ≤ 4096。
    prompt = PromptSpec(prior_masks=(np.zeros((4, 4), dtype=bool),),
                        anchor_box=(6000.0, 6000.0, 500.0, 500.0))
    windows = prompt_windows(
        prompt, raster_height=9000, raster_width=9000, chip_hw=(512, 512)
    )
    assert 1 <= len(windows) <= 4
    for row, col, h, w in windows:
        assert h <= 4096 and w <= 4096
        # 覆盖断言：anchor 区域落在窗口并集内。
        assert col < 6500 and col + w > 6000
        assert row < 6500 and row + h > 6000


def test_schema_version_constant_is_v1():
    assert GEO_PROMPT_SCHEMA_VERSION == 1
