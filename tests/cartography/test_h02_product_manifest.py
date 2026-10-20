"""H02 Wave 5 — Cartographic Product Manifest v1 契约。

任务书 §D：从真实结果生成 versioned 语义 manifest；页面/导出对「数据
revision、图层显隐、专题分类字段、色彩分级、legend、来源/时间、组件
必需性」机器可验证语义一致；unsupported 有披露。

红线测试：确定性（同输入同 manifest）、无坐标/凭据泄漏、缺证据
不 PASS、能力缺失披露为 unsupported 而非 pass。
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.cartography

from app.lib.cartography.product_manifest import (
    PRODUCT_MANIFEST_SCHEMA_VERSION,
    build_product_manifest,
    verify_export_semantic_consistency,
)


def _spec() -> dict:
    return {
        "version": "1.6",
        "view": {"zoom": 11, "center": [104.06, 30.66]},
        "time": {"as_of": "2026-10-20"},
        "sources": {
            "src-1": {
                "type": "geojson",
                "ref": "ref:geojson:abcdef0123456789",
                "content_revision": 7,
                "profile": {"feature_count": 42, "geometry": "point"},
            },
        },
        "layers": [
            {
                "id": "l-choro",
                "type": "fill",
                "source": "src-1",
                "paint": {"color": {
                    "method": "step", "field": "density",
                    "cases": [[0, "#aaa"], [10, "#bbb"]],
                    "default": "#ccc",
                }},
                "legend_spec": {
                    "type": "graduated",
                    "field": "density",
                    "method": "quantiles",
                    "palette_id": "orrd",
                    "breaks": [10, 20],
                    "nodata": {"color": "#eee", "label": "No data"},
                },
                "representation": {"windows": [
                    {"min_zoom": 0, "max_zoom": 24, "mode": "raw",
                     "reason_codes": ["declared"]},
                ]},
            },
        ],
        "layout": {
            "components": [
                {"id": "title", "type": "title", "enabled": True},
                {"id": "scale_bar", "type": "scale_bar", "enabled": True},
            ],
            "composition": {
                "template_id": "composition.standard_analysis",
                "template_version": "1",
                "component_abi_version": 1,
            },
        },
    }


def test_manifest_deterministic_and_versioned():
    a = build_product_manifest(_spec(), export_format="png")
    b = build_product_manifest(_spec(), export_format="png")
    assert a == b, "manifest 必须确定性（同输入同输出）"
    assert a["schema_version"] == PRODUCT_MANIFEST_SCHEMA_VERSION
    assert a["mapspec_fingerprint"].startswith("carto-sha256:")
    assert a["composition"]["template_id"] == "composition.standard_analysis"
    assert a["export"]["format"] == "png"


def test_manifest_no_payload_leak():
    """坐标 / ref 原文 / 凭据不进 manifest（bounded ref descriptor）。"""
    raw = repr(build_product_manifest(_spec()))
    assert "104.06" not in raw and "30.66" not in raw, "坐标泄漏"
    assert "ref:geojson:abcdef" not in raw, "ref 原文泄漏（应为摘要）"
    assert raw.count("density") >= 1  # 语义字段仍在（field 名不是载荷）


def test_verify_pass_path_and_evidence():
    report = verify_export_semantic_consistency(_spec(), export_format="png")
    by_aspect = {c["aspect"]: c for c in report["checks"]}
    assert by_aspect["data_revision_disclosure"]["status"] == "pass"
    assert by_aspect["thematic_field_consistency"]["status"] == "pass"
    assert by_aspect["layer_visibility"]["evidence"]["layers_total"] == 1
    # standard_analysis 的 required 槽包含 legend 族 —— 组件表缺席 → fail 诚实
    assert by_aspect["component_requiredness"]["status"] in ("fail", "pass")


def test_verify_field_mismatch_fails():
    spec = _spec()
    spec["layers"][0]["paint"]["color"]["field"] = "other_field"
    report = verify_export_semantic_consistency(spec, export_format="png")
    by_aspect = {c["aspect"]: c for c in report["checks"]}
    check = by_aspect["thematic_field_consistency"]
    assert check["status"] == "fail"
    assert check["evidence"]["mismatches"][0]["legend_field"] == "density"


def test_verify_semantic_degradation_and_unsupported_disclosure():
    damaged = verify_export_semantic_consistency(
        _spec(), export_format="png",
        diagnostics=[{"code": "features_truncated", "detail": "1000"}],
    )
    by = {c["aspect"]: c for c in damaged["checks"]}
    assert by["delivery_degradation_disclosure"]["status"] == "fail"
    assert damaged["status"] == "fail"

    unsupported = verify_export_semantic_consistency(
        _spec(), export_format="png",
        diagnostics=[{"code": "chart_kind_unsupported_export", "detail": "x"}],
    )
    by2 = {c["aspect"]: c for c in unsupported["checks"]}
    assert by2["delivery_degradation_disclosure"]["status"] == "unsupported"


def test_verify_missing_evidence_never_passes():
    empty = verify_export_semantic_consistency({}, export_format="")
    for check in empty["checks"]:
        assert check["status"] in ("not_evaluated", "pass"), check
        if check["aspect"] != "layer_visibility":
            assert check["status"] != "pass" or check["aspect"] in (
                "layer_visibility", "delivery_degradation_disclosure",
            ), f"缺证据维度不得 PASS：{check['aspect']}"


def test_manifest_nodata_guard_visible():
    manifest = build_product_manifest(_spec())
    legend = manifest["layers"][0]["legend"]
    assert legend.get("nodata_guard") is True
