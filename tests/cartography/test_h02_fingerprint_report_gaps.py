"""H02 Wave 1 — 指纹 v1.6 可见性与 representation 报告死路（G1/G2）。

缺口登记：planning/h02-cartographic-conformance/RECON.md §5a。

G1: ``cartographic_fingerprint`` 的 layer 投影键表（quality_loop.
    ``_LAYER_METADATA_KEYS``）不含 v1.6 的 ``representation`` /
    ``visibility`` —— 仅改语义缩放表达或 zoom 门的两份 spec 指纹逐位
    相等。该指纹是 RenderWork ``work_id``、C13 截图 revision 门与
    lifecycle ``superseded`` 代守卫的身份根基，语义缩放变异不能是
    "隐身代"。
G2: ``representation_at_scale_report`` 只在只读 review 路径计算且
    ``to_dict()`` 不序列化、主 mutation 路径（review_and_repair）
    不计算 —— 语义缩放质量证据在生产面无处消费。
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.cartography

from app.lib.cartography.quality_loop import (
    cartographic_fingerprint,
    review_and_repair_cartography,
    review_cartography,
)


def _spec(representation=None, visibility=None) -> dict:
    layer: dict = {
        "id": "l-primary",
        "type": "circle",
        "source": "src-1",
        "paint": {"color": "#3388aa"},
    }
    if representation is not None:
        layer["representation"] = representation
    if visibility is not None:
        layer["visibility"] = visibility
    return {
        "version": "1.6",
        "view": {"zoom": 11},
        "sources": {"src-1": {"type": "geojson", "ref": "ref:a"}},
        "layers": [layer],
    }


_REP_CLUSTER = {
    "windows": [
        {"min_zoom": 0, "max_zoom": 11, "mode": "raw",
         "reason_codes": ["declared"]},
        {"min_zoom": 11, "max_zoom": 24, "mode": "point_cluster",
         "reason_codes": ["declared"]},
    ],
}
_REP_HEATMAP = {
    "windows": [
        {"min_zoom": 0, "max_zoom": 11, "mode": "raw",
         "reason_codes": ["declared"]},
        {"min_zoom": 11, "max_zoom": 24, "mode": "visual_heatmap",
         "reason_codes": ["declared"]},
    ],
}


# ── G1: 指纹对 v1.6 语义缩放字段敏感 ────────────────────────────────────


def test_fingerprint_differs_when_representation_changes():
    fp_raw = cartographic_fingerprint(_spec(representation=_REP_CLUSTER))
    fp_heat = cartographic_fingerprint(_spec(representation=_REP_HEATMAP))
    assert fp_raw != fp_heat, (
        "cartographic_fingerprint 对 layer.representation 不敏感：语义缩放"
        "表达变异产生相同指纹（RenderWork work_id / C13 截图门 / superseded"
        " 代守卫全部失明）"
    )


def test_fingerprint_differs_when_visibility_changes():
    fp_a = cartographic_fingerprint(_spec(visibility={"min_zoom": 5}))
    fp_b = cartographic_fingerprint(_spec(visibility={"min_zoom": 8}))
    assert fp_a != fp_b, (
        "cartographic_fingerprint 对 layer.visibility（结构 zoom 门）不敏感"
    )


def test_fingerprint_deterministic_for_same_input():
    a = cartographic_fingerprint(_spec(representation=_REP_CLUSTER))
    b = cartographic_fingerprint(_spec(representation=_REP_CLUSTER))
    assert a == b


# ── G2: representation 报告进入序列化面与主 mutation 路径 ────────────────


def test_review_cartography_serializes_representation_report():
    result = review_cartography(_spec(representation=_REP_CLUSTER))
    payload = result.to_dict()
    report = payload.get("representation_report")
    assert isinstance(report, dict) and report.get("layers"), (
        "review_cartography 计算了 representation_at_scale_report 但 "
        "to_dict() 不序列化 —— 证据死路"
    )


def test_review_and_repair_path_reports_representation():
    result = review_and_repair_cartography(_spec(representation=_REP_CLUSTER))
    report = result.to_dict().get("representation_report")
    assert isinstance(report, dict) and report.get("layers"), (
        "review_and_repair_cartography（主 mutation 路径）不计算语义缩放报告"
    )


def test_representation_report_absent_when_no_declaration():
    result = review_cartography(_spec())
    assert result.to_dict().get("representation_report") is None, (
        "无表达声明的 spec 应诚实缺席（None），不造空报告"
    )
