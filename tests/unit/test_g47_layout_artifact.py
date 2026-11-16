"""G47 布局 artifact 合同（WP07）单测：走完整 ``compute_layout`` 管线。

artifact 是唯一真源 —— 图面、导出、Pi 回执、原图联动都从它读。因此这里的断言
都是"下游能否安全使用它"的问题：

* **坐标不是经纬度**：``coordinate_space=diagram`` + ``is_geographic=false``，
  且没有任何 EPSG/经纬度字段可以被误当成真实位置；
* **面积比例在归一化之后仍然成立**（等比缩放，只用一个 scale）；
* **身份可复算**：同内容同指纹、换参数换指纹，指纹在 artifact 内可读；
* **有界**：回执与 diagram 组件都有上限，超出如实 ``truncated``。
"""
from __future__ import annotations

import json
import math

import pytest

from app.services.cartogram_authoring.compute import compute_layout, summarize
from app.services.cartogram_authoring.errors import AuthoringError
from app.services.cartogram_authoring.layout import bounded_receipt
from app.services.cartogram_authoring.publication import (
    MAX_COMPONENT_CIRCLES,
    diagram_component,
    diagram_spec_from_layout,
)
from app.services.cartogram_authoring.solver import SolveCancelled
from tests.unit.g47_fixtures import frozen, grid_features, plan

V9 = [100, 200, 300, 400, 500, 600, 700, 800, 900]


def _build(features=None, p=None, revision=7):
    feats = features if features is not None else grid_features(values=V9)
    return compute_layout(p or plan(), frozen(feats, revision=revision))


@pytest.fixture(scope="module")
def built():
    return _build()


# ── 坐标语义：这是示意图，不是地图 ──────────────────────────────────────

def test_artifact_declares_diagram_coordinate_space(built):
    layout = built.layout
    assert layout["kind"] == "cartogram_layout"
    assert layout["coordinate_space"] == "diagram"
    assert layout["units"] == "diagram_unit"
    assert layout["is_geographic"] is False
    assert layout["extent"]["uniform_scale"] is True


def test_projection_is_only_for_preparation(built):
    projection = built.layout["projection"]
    assert projection["source_crs"] == "EPSG:4326"
    assert "never for layout coordinates" in projection["use"]


def test_circles_carry_no_geographic_payload(built):
    """圆上只有示意图坐标；任何 lat/lon/crs 字段都会诱使下游当真实位置用。"""
    for c in built.layout["circles"]:
        assert set(c) == {"feature_id", "label", "x", "y", "radius", "value",
                          "anchor"}
        assert not any("lat" in k or "lon" in k or "crs" in k or "epsg" in k
                       for k in c)
    assert "geojson" not in json.dumps(built.layout, ensure_ascii=False).lower()


def test_circles_stay_inside_the_canvas(built):
    extent = float(built.layout["extent"]["width"])
    margin = float(built.layout["extent"]["margin"])
    for c in built.layout["circles"]:
        r = float(c["radius"])
        assert r > 0
        assert -1e-9 <= float(c["x"]) - r
        assert float(c["x"]) + r <= extent + 1e-9
        assert -1e-9 <= float(c["y"]) - r
        assert float(c["y"]) + r <= extent + 1e-9
    assert margin > 0


def test_anchor_is_diagram_space_too(built):
    """anchor 与圆心同量纲、同画布：位移指标才有几何含义。

    回归：早先只按圆的包围盒定标，圆被吸引挤紧后 factor 被放大，anchor 会被
    推到画布外（例如 x=-1186），"从原位置移了多远"在图上就无从表达。
    """
    extent = float(built.layout["extent"]["width"])
    for c in built.layout["circles"]:
        ax, ay = c["anchor"]
        assert -1e-6 <= ax <= extent + 1e-6, (c["feature_id"], ax)
        assert -1e-6 <= ay <= extent + 1e-6, (c["feature_id"], ay)


def test_normalised_max_radius_stays_near_the_planned_target(built):
    """``area_scale.max_radius`` 是"最大圆的目标半径"，归一化后不应被放大数倍。"""
    planned = float(plan().area_scale.max_radius)
    got = float(built.layout["area_scale"]["max_radius"])
    assert 0.5 * planned <= got <= 1.6 * planned, got


# ── 面积比例 ────────────────────────────────────────────────────────────

def test_area_ratio_equals_value_ratio_after_normalisation(built):
    """归一化只用一个等比 factor，所以 ``r_i²/r_j² == v_i/v_j`` 必须仍成立。"""
    circles = built.layout["circles"]
    base = circles[0]
    for c in circles[1:]:
        expected = float(c["value"]) / float(base["value"])
        got = (float(c["radius"]) / float(base["radius"])) ** 2
        assert got == pytest.approx(expected, rel=2e-4)


def test_legend_reference_circles_use_same_coefficient(built):
    refs = built.layout["legend"]["reference_circles"]
    assert len(refs) >= 2
    for a, b in zip(refs, refs[1:]):
        # 每级数量 1/4 → 半径 1/2（读者可直接核对）
        assert (a["radius"] / b["radius"]) ** 2 == pytest.approx(
            a["value"] / b["value"], rel=2e-3)


def test_area_scale_receipt_states_shared_coefficient(built):
    scale = built.layout["area_scale"]
    assert scale["shared_coefficient"] is True
    assert scale["per_circle_clamp"] is False
    assert scale["max_radius"] > 0
    assert scale["normalization_factor"] > 0


# ── 零 / 缺失 / 排除 ────────────────────────────────────────────────────

def test_zero_and_missing_are_listed_not_drawn():
    values = list(V9)
    values[0] = 0
    values[4] = None
    built = _build(grid_features(values=values))
    layout = built.layout
    assert layout["zero_ids"] == ["r0c0"]
    assert layout["missing_ids"] == ["r1c1"]
    drawn = {c["feature_id"] for c in layout["circles"]}
    assert "r0c0" not in drawn and "r1c1" not in drawn
    assert len(drawn) == 7
    disclosures = " ".join(layout["disclosures"])
    assert "零值区域" in disclosures and "缺失值区域" in disclosures


def test_exclusions_require_reason_and_are_disclosed():
    with pytest.raises(AuthoringError) as ei:
        _build(p=plan(exclude_ids=["r0c0"]))
    assert ei.value.code == "INVALID_PLAN"

    built = _build(p=plan(exclude_ids=["r0c0"],
                          exclude_reasons={"r0c0": "数据缺失，已知不可用"}))
    layout = built.layout
    assert layout["excluded"]["ids"] == ["r0c0"]
    assert layout["excluded"]["reasons"]["r0c0"].startswith("数据缺失")
    assert "不包含它们" in " ".join(layout["disclosures"])


def test_unknown_scope_id_is_rejected():
    with pytest.raises(AuthoringError) as ei:
        _build(p=plan(include_ids=["r0c0", "does-not-exist"]))
    assert ei.value.code == "SCOPE_IDS_NOT_FOUND"


def test_budget_is_enforced_before_solving():
    with pytest.raises(AuthoringError) as ei:
        _build(p=plan(max_features=5))
    assert ei.value.code == "BUDGET_EXCEEDED"


# ── 身份与可复算 ────────────────────────────────────────────────────────

def test_fingerprint_is_deterministic(built):
    again = _build()
    assert built.fingerprint == again.fingerprint
    assert built.layout["metrics"]["layout_fingerprint"] == built.fingerprint


def test_fingerprint_changes_with_plan_but_not_with_source_revision():
    a = _build(p=plan(max_radius=36.0))
    b = _build(p=plan(max_radius=40.0))
    assert a.fingerprint != b.fingerprint

    # 同一内容、不同 revision：布局相同，候选身份不同（revision 参与身份）。
    c = _build(revision=7)
    d = _build(revision=8)
    assert c.layout["circles"] == d.layout["circles"]
    assert c.layout["candidate_digest"] != d.layout["candidate_digest"]


def test_plan_and_candidate_digests_are_present(built):
    layout = built.layout
    assert layout["plan_digest"].startswith("g47p-")
    assert layout["candidate_digest"].startswith("g47c-")
    assert layout["source"]["content_revision"] == 7


def test_metrics_are_self_consistent(built):
    metrics = built.layout["metrics"]
    assert metrics["status"] == "pass"
    assert metrics["oracle"].startswith("analytic")
    assert metrics["area"]["evaluated"] == len(built.layout["circles"])
    assert metrics["overlap"]["evaluated_pairs"] >= 1
    assert metrics["solver"]["diagnostics"]["update_scheme"] == "jacobi"
    assert metrics["solver"]["diagnostics"]["radii_fixed"] is True


def test_publication_ready_requires_convergence_and_quality(built):
    layout = built.layout
    assert layout["status"] == "converged"
    assert layout["publication_ready"] is True
    assert built.layout["metrics"]["failed"] == []


def test_non_converged_layout_is_saved_as_draft_not_success():
    """不收敛不抛异常：artifact 如实记录，``publication_ready=False``。"""
    built = _build(p=plan(max_iterations=1))
    layout = built.layout
    assert layout["status"] == "not_converged"
    assert layout["publication_ready"] is False
    assert any("未达到发布资格门" in d for d in layout["disclosures"])


# ── 有界回执与出版 adapter ──────────────────────────────────────────────

def test_bounded_receipt_limits_circles_and_keeps_metrics(built):
    receipt = bounded_receipt(built.layout, max_circles=3)
    assert receipt["circle_count"] == 9
    assert len(receipt["circles_sample"]) == 3
    assert receipt["is_geographic"] is False
    assert receipt["metrics"]["status"] == "pass"
    assert len(receipt["disclosures"]) <= 12
    # 不把整份布局（含参考轮廓点）塞进回执
    assert "reference" not in receipt


def test_diagram_spec_is_a_bounded_render_payload(built):
    spec = diagram_spec_from_layout(built.layout)
    assert spec["fit"] == "contain"
    assert spec["extent"] == pytest.approx(float(built.layout["extent"]["width"]))
    assert len(spec["circles"]) == 9
    assert spec["truncated"] is False
    assert spec["legend_unit"] == "人"
    assert spec["reference_circles"]
    assert spec["layout_fingerprint"] == built.fingerprint
    for path in spec["reference_paths"]:
        assert path.startswith("M ") and path.endswith(" Z")
    assert len(spec["disclosures"]) <= 8


def test_diagram_spec_truncates_oversized_circle_sets():
    built = _build()
    layout = dict(built.layout)
    layout["circles"] = (built.layout["circles"] * 60)[:MAX_COMPONENT_CIRCLES + 5]
    spec = diagram_spec_from_layout(layout)
    assert len(spec["circles"]) == MAX_COMPONENT_CIRCLES
    assert spec["truncated"] is True


def test_diagram_component_is_an_additive_controlled_kind(built):
    comp = diagram_component(built.layout, component_id="carto-1",
                             layout_ref="layout:abc")
    assert comp["kind"] == "diagram"
    assert comp["id"] == "carto-1"
    assert comp["diagram"]["layout_ref"] == "layout:abc"
    assert set(comp["frame"]) == {"x", "y", "width", "height"}
    # 不是地图槽：不允许带 CRS/地理范围
    assert "crs" not in comp and "bbox" not in comp


def test_summarize_is_small_and_operational(built):
    summary = summarize(built)
    assert summary["circle_count"] == 9
    assert summary["status"] == "converged"
    assert summary["publication_ready"] is True
    assert summary["area"]["max_rel_error"] < 0.01
    assert summary["adjacency"]["original_edges"] >= 1
    assert set(summary["overlap"]) == {
        "max_pair_overlap_ratio", "overlapping_pairs",
        "pairs_over_threshold", "pairs_over_threshold_ratio",
        "fraction_pair_threshold", "total_overlap_area_ratio",
        "pair_cap_violated", "fraction_cap_violated"}
    assert len(json.dumps(summary)) < 4000


# ── 取消 ────────────────────────────────────────────────────────────────

def test_cancellation_propagates_out_of_the_pipeline():
    with pytest.raises(SolveCancelled):
        compute_layout(plan(), frozen(grid_features(values=V9)),
                       should_cancel=lambda: True)
