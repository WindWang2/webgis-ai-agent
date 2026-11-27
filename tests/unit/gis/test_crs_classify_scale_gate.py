"""classify_crs 兜底尺度闸门回归（#1720）—— 全球/大洲级投影不再算局部度量。

旧兜底对「投影米 + 名字不含 mercator」一律 ``projected_local_metric``，
全球/大洲级等积投影（Mollweide、Equal Earth、大洲 LAEA…）由此通过
LOCAL_METRIC 硬门 —— 而该门的目的正是拒斥尺度失真投影。修正后按投影
有效域实测尺度失真（有效域中心 + 四角的有限差分局部尺度因子 ≤1%）。
"""
from __future__ import annotations

import pytest

from app.lib.gis.crs_safety import classify_crs, crs_class_allows


@pytest.mark.parametrize(
    "crs",
    [
        "ESRI:54009",   # World Mollweide（全球等积）
        "EPSG:8857",    # Equal Earth（全球等积）
        "EPSG:3035",    # ETRS89-extended / LAEA Europe（大洲等积）
        "EPSG:9311",    # NAD27 / US National Atlas Equal Area（大洲等积）
        "EPSG:3995",    # Arctic Polar Stereographic（极方位，非 3413/3031 白名单）
    ],
)
def test_global_or_continental_equal_area_is_not_local_metric(crs: str):
    """尺度失真投影必须落 projected，不能通过 LOCAL_METRIC 硬门。"""
    assert classify_crs(crs) == "projected"
    assert crs_class_allows("LOCAL_METRIC_REQUIRED", classify_crs(crs)) is False


@pytest.mark.parametrize(
    "crs",
    [
        "EPSG:32650",              # UTM 词法白名单
        "EPSG:32717",
        "EPSG:3413",               # 极方位白名单
        "EPSG:3031",
        "WGS84 / UTM zone 50N",    # pyproj 兜底：带内失真 ~0.1%
        "EPSG:2154",               # RGF93 / Lambert-93：法国，失真 ~0.3%
        "EPSG:9822",               # RGF93 v2 / CC42：窄带
        "EPSG:32600",              # UTM 网格系统：不可投影 → 无证据不收紧
    ],
)
def test_local_metric_crs_still_classifies_local(crs: str):
    """域内尺度失真 ≤1% 的投影（含不可判定的 UTM 网格系统）照旧局部度量。"""
    assert classify_crs(crs) == "projected_local_metric"
    assert crs_class_allows("LOCAL_METRIC_REQUIRED", classify_crs(crs)) is True


def test_mercator_and_non_metric_units_unchanged():
    """既有判据不受影响：3857 仍 projected；非米单位仍 projected。"""
    assert classify_crs("EPSG:3857") == "projected"
    assert classify_crs("EPSG:26733") == "projected"   # US survey foot


def test_classify_is_cached_and_deterministic():
    """lru_cache 命中不改变判定（尺度闸门幂等）。"""
    assert classify_crs("EPSG:3035") == classify_crs("EPSG:3035") == "projected"
    assert classify_crs("EPSG:2154") == "projected_local_metric"


def test_recommend_metric_crs_returns_empty_on_invalid_crs_string():
    """crs 为非法串时 Transformer.from_crs 抛 pyproj CRSError
    （RuntimeError 子类，不是 ValueError）—— 旧代码只捕
    (TypeError, ValueError)，异常逸出到 resolver CRS 硬门之外，调用方拿不到
    文档承诺的保守空串。"""
    from app.lib.gis.crs_safety import recommend_metric_crs

    bbox = (104.0, 30.0, 104.5, 30.5)
    assert recommend_metric_crs(bbox, "EPSG:4326") == "EPSG:32648"
    # 非空但非法的 crs 串 → 保守空串（CRSError 被收编）
    for bad in ("not-a-crs", "EPSG:99999999", "EPSG:notanumber", ";;;"):
        assert recommend_metric_crs(bbox, bad) == "", bad
    # crs 缺席（空串/None）时根本不做变换，照旧推荐 UTM（`if crs:` 门）
    assert recommend_metric_crs(bbox, "") == "EPSG:32648"
    assert recommend_metric_crs(bbox, None) == "EPSG:32648"
    # bbox 本身非数值同样保守空串（既有行为不回退）
    assert recommend_metric_crs(("a", "b", "c", "d"), None) == ""
    assert recommend_metric_crs(None, "EPSG:4326") == ""
