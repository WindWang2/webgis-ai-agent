"""Science V4 Wave 2 —— typed scientific errors 扩充契约测试。

收口内容：
- 新增 ``NumericalInstability`` / ``ConvergenceFailure``（science-v4）；
- ``to_utm_gdf_with_note`` 边界的 pyproj.CRSError → InvalidCRS 折叠
  （原 KNOWN-GAP #1，xfail 已在 test_scientific_regression.py 转正；
  本文件补边界单元测试 + 全 taxonomy 结构契约）。

语义边界（防 dead vocabulary）：生产者随后续 wave 落地 ——
NumericalInstability ← 嵌套 variogram/normal-score 退化（W4）、LMC
半正定校验失败（W6）；ConvergenceFailure ← 嵌套结构拟合（W4）、
SGS 路径奇异（W5）。本文件钉死：词表稳定性、ValueError 兼容（dispatch
错误映射通道）、to_dict 形状、correction_hint 非空。
"""
from __future__ import annotations

import pytest

from app.lib.gis import scientific_errors as se


EXPECTED_V4_MEMBERS = {
    "NUMERICAL_INSTABILITY": se.NumericalInstability,
    "CONVERGENCE_FAILURE": se.ConvergenceFailure,
}


@pytest.mark.parametrize("cls", list(EXPECTED_V4_MEMBERS.values()))
def test_v4_members_are_value_error_with_code_and_hint(cls):
    """新成员保持 ValueError 兼容（dispatch 既有错误映射通道）。"""
    err = cls("synthetic failure detail")
    assert isinstance(err, ValueError)
    assert isinstance(err, se.ScientificError)
    assert err.scientific_code
    assert err.correction_hint, "科学错误必须携带修正提示"


def test_scientific_codes_unique_across_taxonomy():
    """scientific_code 是机器可读主键 —— 全词表不得重复。"""
    codes = [
        getattr(obj, "scientific_code")
        for obj in vars(se).values()
        if isinstance(obj, type)
        and issubclass(obj, se.ScientificError)
        and obj is not se.ScientificError
    ]
    assert len(codes) == len(set(codes)), f"duplicate codes: {codes}"


def test_to_dict_shape_roundtrip():
    """to_dict 是 evidence/correction_hint 通道的合同形状。"""
    err = se.NumericalInstability("covariance not PSD")
    d = err.to_dict()
    assert d == {
        "scientific_code": "NUMERICAL_INSTABILITY",
        "detail": "covariance not PSD",
        "correction_hint": err.correction_hint,
    }
    assert "precondition_id" not in d  # 子类扩展字段只在各自 to_dict 出现


def test_unparseable_crs_typed_reject_at_utm_boundary():
    """边界单元测试：声明 CRS 不可解析 → InvalidCRS（非裸 CRSError）。

    直接调 to_utm_gdf_with_note（statistics 层 e2e 见
    test_scientific_regression.test_crs_mismatch_unparseable_crs_is_typed_scientific_reject）。
    """
    from app.lib.geo_processor.core import to_utm_gdf_with_note

    fc = {
        "type": "FeatureCollection",
        "crs": "EPSG:99999999",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [116.1, 39.9]},
                "properties": {"v": 1},
            }
        ],
    }
    with pytest.raises(se.InvalidCRS) as ri:
        to_utm_gdf_with_note(fc)
    assert ri.value.correction_hint
    # gcj02/bd09 归一化不受影响（正常 CRS 路径零回归）
    fc_ok = dict(fc, crs="EPSG:4326")
    gdf, utm, note = to_utm_gdf_with_note(fc_ok)
    assert gdf is not None and utm.startswith("EPSG:")
