"""Workflow V6 — adapters_cartography 渲染输入解析（P4 补强：W5）。

纯函数面：_feature_xy 坐标提取、_palette 稳定性、错误分类的
CartographyOutcome 形态（空输入/渲染失败均落证据不抛出）。
"""
from __future__ import annotations

from app.services.workflow_runtime import adapters_cartography as AC


def test_palette_is_stable_and_nonempty() -> None:
    p1, p2 = AC._palette(), AC._palette()
    assert p1 == p2 and len(p1) >= 3


def test_feature_xy_point() -> None:
    f = {"geometry": {"type": "Point", "coordinates": [116.4, 39.9]}}
    assert AC._feature_xy(f) == ([116.4], [39.9])


def test_feature_xy_polygon_uses_first_ring_first_vertex() -> None:
    f = {"geometry": {"type": "Polygon", "coordinates": [
        [[116.1, 39.8], [116.2, 39.8], [116.2, 39.9]],
    ]}}
    xs, ys = AC._feature_xy(f)
    assert [round(x, 3) for x in xs] == [116.1, 116.2, 116.2]
    assert [round(y, 3) for y in ys] == [39.8, 39.8, 39.9]


def test_feature_xy_tolerates_missing_geometry() -> None:
    assert AC._feature_xy({"geometry": None}) == ([], [])
    assert AC._feature_xy({}) == ([], [])


def test_outcome_dataclass_shape() -> None:
    ok = AC.CartographyOutcome(ok=True, output_ref="blob:k", duration_ms=3)
    assert ok.ok and ok.output_ref == "blob:k" and ok.error_code == ""
    bad = AC.CartographyOutcome(ok=False, error_code="CARTOGRAPHY_RENDER_ERROR",
                                error_message="x", duration_ms=1)
    assert not bad.ok and bad.output_ref == ""
