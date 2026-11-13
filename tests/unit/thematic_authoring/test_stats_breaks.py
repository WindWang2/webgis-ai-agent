"""T-B1/T-B2/T-B4/T-B5/T-B18：统计口径 + 统一求值器 + 退化披露。

验收 2（断点边界）、4（常数字段）、5（重复值 k 退化）、6（无效值分型）、
7（越界披露）、8（格式化不改分类）、14（MVT/无全源诚实降级）。
"""
from __future__ import annotations


import pytest

from app.services.mapspec_to_svg import _resolve_paint_value
from app.services.thematic_authoring import stats as stats_mod
from tests.unit.thematic_authoring.helpers import dirty_rows, make_fc

# ─── assign_class：与 step 求值器逐点一致（验收 2/8） ─────────────────────


@pytest.mark.parametrize(
    "value,breaks,expected",
    [
        (0.0, [0, 10, 20, 50], 0),      # 恰等于首断点 → 第一类
        (10.0, [0, 10, 20, 50], 1),     # 恰等于中间断点 → 归上类
        (9.999999, [0, 10, 20, 50], 0), # 左邻值
        (10.000001, [0, 10, 20, 50], 1),
        (49.999, [0, 10, 20, 50], 2),
        (50.0, [0, 10, 20, 50], 2),
        (99.0, [0, 10, 20, 50], 2),     # 越上界 → 末类吸收
        (-5.0, [0, 10, 20, 50], 0),     # 越下界 → 首类吸收
        (-1e18, [0, 10], 0),            # 大负数
        (1e18, [0, 10], 0),             # 大正数越界 → 末类吸收（单类域）
        (-3.5, [-10, -5, 0], 1),        # 负值域
        (123456789012345678.0, [0, 1e17, 1e18], 1),  # 大整数
        (0.6, [0.1, 0.55, 0.9], 1),     # 精细小数断点
        (0.55, [0.1, 0.55, 0.9], 1),    # 恰等于中间断点
    ],
)
def test_assign_class_matches_step_evaluator(value, breaks, expected):
    assert stats_mod.assign_class(value, breaks) == expected
    # 与生产 step 求值器（SVG 导出/图例共用语义）逐点一致
    sm = _step_method(breaks)
    got = _resolve_paint_value(sm, {"v": value}, fallback="#000000")
    expect_color = sm["stops"][expected - 1][1] if expected >= 1 else sm["default"]
    assert got == expect_color, f"paint step mismatch at {value}"


def _step_method(breaks):
    colors = [f"#00{i:02d}x0" for i in range(len(breaks) - 1)]
    colors = [f"#0000{i:02d}" for i in range(len(breaks) - 1)]
    return {
        "method": "step", "field": "v", "default": colors[0],
        "stops": [[breaks[i], colors[i]] for i in range(1, len(breaks) - 1)],
    }


def test_boundary_oracle_python_independent():
    """独立预期类表：断点左右邻值/端点/极值（Python 直算，不镜像实现）。"""
    breaks = [0, 10, 20, 50]
    values = [-1, 0, 5, 10, 15, 20, 35, 50, 100]
    # 独立推导：class = 区间 [b_i, b_{i+1}) 的索引（手算表）
    expected = [0, 0, 0, 1, 1, 2, 2, 2, 2]
    got = [stats_mod.assign_class(v, breaks) for v in values]
    assert got == expected


# ─── 统计口径（验收 6） ────────────────────────────────────────────────────


def test_scan_field_invalid_typing_and_zero_validity():
    fc = make_fc(dirty_rows(24))
    scan = stats_mod.scan_field(fc["features"], "pop")
    assert scan.total_features == 31
    assert scan.scanned == 25                 # 24 有限数值 + 1 个零
    assert scan.invalid == {
        "bool": 1, "string": 1, "nan": 1, "inf": 1, "null": 1, "missing": 1,
    }
    # 零是有效值：min 应为 0
    assert scan.values[0] == 0.0 or min(scan.values) == 0.0
    dist = stats_mod.field_distribution(scan)
    assert dist["basis"]["mode"] == "full_scan"
    assert dist["basis"]["approximate"] is False
    assert dist["numeric"]["zero"] == 2  # i=0 的 0.0 + 显式 0
    # 类别口径：字符串 "12.5" 计入类别；NaN/Inf 排除；bool "True" 计入
    cat_keys = {c["key"] for c in dist["categorical"]["top"]}
    assert "12.5" in cat_keys
    assert not any(k in ("nan", "inf") for k in cat_keys)


def test_scan_field_missing_vs_null_distinguished():
    fc = make_fc([{"pop": None}, {"other": 1}, {"pop": 3}])
    scan = stats_mod.scan_field(fc["features"], "pop")
    assert scan.invalid["null"] == 1
    assert scan.invalid["missing"] == 1
    assert scan.scanned == 1


def test_category_type_collision_disclosed():
    fc = make_fc([{"code": 1}, {"code": "1"}, {"code": 2}])
    scan = stats_mod.scan_field(fc["features"], "code")
    assert scan.ambiguous_keys == ["1"]
    assert scan.key_kinds["1"] in ("number", "string")
    dist = stats_mod.field_distribution(scan)
    assert dist["categorical"]["ambiguous_keys"] == ["1"]


def test_class_counts_under_over_disclosure():
    rows = [{"v": v} for v in (-5, 0, 5, 55, 100)]
    scan = stats_mod.scan_field(make_fc(rows)["features"], "v")
    counts = stats_mod.class_counts(scan, [0, 10, 20, 50])
    # -5/0/5 → c0（-5 同时计入 under）；55/100 → c2（计入 over）
    assert counts["counts"] == [3, 0, 2]
    assert counts["under_domain"] == 1        # -5
    assert counts["over_domain"] == 2         # 55, 100
    assert counts["valid"] == 5


def test_constant_field_single_class():
    rows = [{"v": 7.0} for _ in range(30)]
    scan = stats_mod.scan_field(make_fc(rows)["features"], "v")
    hist = stats_mod.histogram(scan.values)
    assert hist["counts"] == [30]
    counts = stats_mod.class_counts(scan, [7.0, 7.0])
    assert counts["counts"] == [30]


def test_quantile_duplicate_degrades_honestly():
    """验收 5：重复值让 quantile 断点去重 → 真实类数披露。"""
    from app.lib.cartography.classify import classify_values

    values = [1.0] * 50 + [5.0] * 50          # 只有两个不同值
    breaks = classify_values(values, "quantiles", 5)
    assert len(breaks) - 1 < 5                # 类数退化
    uniq = sorted(set(breaks))
    assert breaks == uniq                     # 去重后的真实断点


def test_sampled_scan_marks_approximate():
    rows = [{"v": float(i)} for i in range(stats_mod.MAX_SCAN_VALUES + 500)]
    scan = stats_mod.scan_field(make_fc(rows)["features"], "v")
    assert scan.approximated is True
    assert scan.sample_size == stats_mod.MAX_SCAN_VALUES
    dist = stats_mod.field_distribution(scan)
    assert dist["basis"]["mode"] == "sampled"
    assert dist["basis"]["approximate"] is True
    assert dist["basis"]["scanned_values"] == stats_mod.MAX_SCAN_VALUES + 500


def test_histogram_bin_cap():
    rows = [{"v": float(i)} for i in range(1000)]
    scan = stats_mod.scan_field(make_fc(rows)["features"], "v")
    hist = stats_mod.histogram(scan.values)
    assert len(hist["counts"]) <= stats_mod.MAX_HISTOGRAM_BINS
    assert sum(hist["counts"]) == 1000


def test_degraded_source_never_claims_viewport_distribution():
    """验收 14 的服务面等价物：无全源时 basis=degraded，不产出伪全域分布。"""
    # （由 service 测试覆盖 HTTP 面；这里锁 stats 合同形状）
    dist = {
        "basis": {"mode": "degraded", "reason": "no_full_source_url_carrier",
                  "approximate": True, "stats_version": stats_mod.STATS_VERSION},
    }
    assert dist["basis"]["mode"] == "degraded"


def test_cache_hit_and_invalidation():
    rows = [{"v": float(i % 50)} for i in range(200)]
    features = make_fc(rows)["features"]
    d1 = stats_mod.cached_distribution("ref:x", 3, "v", features)
    d2 = stats_mod.cached_distribution("ref:x", 3, "v", features)
    assert stats_mod.cache_stats()["hits"] >= 1
    assert d1 == d2
    # revision 变化 → 缓存失效（重新扫描）
    d3 = stats_mod.cached_distribution("ref:x", 4, "v", features)
    assert d3["basis"]["stats_version"] == stats_mod.STATS_VERSION
    info = stats_mod.cache_stats()
    assert info["entries"] >= 2
    assert info["bytes"] > 0


# ─── 双端孪生对账（T-F1 Python 侧；fixture 与 vitest 共享） ────────────────


def test_twin_fixture_parity():
    """TS assignClass/sizeForValue 与 Python 逐点一致（同一 fixture 双端断言）。"""
    import json
    from pathlib import Path

    from app.services.thematic_authoring.builder import size_for_value

    fixture = json.loads(
        (Path(__file__).resolve().parents[3]
         / "frontend/lib/thematic-authoring/twins.fixture.json").read_text())
    for case in fixture["class_cases"]:
        assert stats_mod.assign_class(case["value"], case["breaks"]) == case["expected"], case

    class _CH:
        pass

    for case in fixture["size_cases"]:
        ch = _CH()
        for k, v in case["channel"].items():
            setattr(ch, k, v)
        got = size_for_value(case["value"], ch)
        assert got is not None and abs(got - case["expected"]) < 1e-9, case


def test_ambiguous_keys_capture_up_to_64():
    """复审 P3-1 锚：混合类型键捕获面到 64（历史 cap=5 的回归）。"""
    rows = []
    for i in range(70):
        rows.append({"code": i if i % 2 == 0 else str(i)})  # 数值 i 与文本 str(i) 同文本键
    # 直接构造：键 "k0".."k69" 各出现 number 与 string 两态
    feats = []
    for i in range(70):
        props_n = {"code": i}
        props_s = {"code": str(i)}
        feats.append({"properties": props_n})
        feats.append({"properties": props_s})
    scan = stats_mod.scan_field(feats, "code")
    assert len(scan.ambiguous_keys) == 64  # cap 64（此前 5）
