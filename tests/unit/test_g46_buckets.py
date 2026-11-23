"""G46 分桶统计单元测试：typed 类别、闭开规则、other 桶、facet、诚实分母。

oracle：期望计数/桶归属全部独立手算（枚举 fixture 的已知属性），不调用
生产 evaluator 生成 expected。
"""
from __future__ import annotations

from app.services.linked_exploration import contract
from app.services.linked_exploration.buckets import (
    category_buckets,
    range_buckets,
)


def _feat(props, geom_type="Point"):
    return {"type": "Feature", "geometry": {"type": geom_type, "coordinates": [0, 0]},
            "properties": props}


def _chart(kind="category", field="type", **kw):
    defaults = dict(chart_id="ch1", kind=kind, field=field, aggregation="count")
    defaults.update(kw)
    return contract.ChartSpec(**defaults)


TYPED_FEATURES = [
    _feat({"id": 1, "cat": 1, "pop": 100, "keep": True}),      # 数值类别 1
    _feat({"id": 2, "cat": "1", "pop": 200, "keep": True}),    # 字符串 "1" ≠ 1
    _feat({"id": 3, "cat": True, "pop": 300, "keep": False}),  # bool true
    _feat({"id": 4, "cat": None, "pop": 400}),                 # 显式 null
    _feat({"id": 5, "pop": 500}),                              # 缺席 cat
    _feat({"id": 6, "cat": "", "pop": 50}),                    # 空串
]


class TestCategoryBuckets:
    def test_typed_keys_not_merged(self):
        """E06：1 / "1" / true / null / 空串 各自成组。"""
        out = category_buckets(TYPED_FEATURES, _chart(field="cat"), where=None)
        by_label = {r["label"]: r for r in out["rows"]}
        # oracle：手算 —— 1(1), "1"(1), true(1), null(2: id4 显式 null + id5 缺席), ""(1)
        assert by_label["1"]["count"] == 1
        assert by_label["true"]["count"] == 1
        assert by_label["（空值）"]["count"] == 2
        assert any(r["label"] == "" and r["count"] == 1 for r in out["rows"])
        # 数值 1 与字符串 "1" 是两行（label 同为 "1" 也要分立）
        ones = [r for r in out["rows"] if r["label"] == "1"]
        assert len(ones) == 2
        machine_vals = sorted((r["machine_value"] for r in ones), key=str)
        assert 1 in machine_vals and "1" in machine_vals
        assert out["null_count"] == 2

    def test_aggregation_sum_mean_honest_denominators(self):
        """E09：非数值不计入 sum/mean；分母 = 有效数值个数。"""
        feats = [
            _feat({"cat": "a", "v": 10}),
            _feat({"cat": "a", "v": 20}),
            _feat({"cat": "a", "v": "x"}),     # 非数值
            _feat({"cat": "a"}),               # 缺席
        ]
        out = category_buckets(
            feats, _chart(field="cat", aggregation="mean", value_field="v"),
            where=None)
        row = next(r for r in out["rows"] if r["label"] == "a")
        # oracle：mean(10,20) = 15（分母 2，不是 4；非数值不当 0）
        assert row["count"] == 4
        assert row["mean"] == 15.0
        assert out["non_numeric_count"] == 2

    def test_other_bucket_typed_values_disclosed(self):
        """E13：溢出桶披露精确 typed 值集（≤ 上限可筛选）。"""
        feats = []
        for i in range(contract.MAX_CATEGORY_GROUPS + 5):
            feats.append(_feat({"cat": f"c{i}"}))
        for i in range(3):
            feats.append(_feat({"cat": f"c{i}"}))  # 前 3 类 count=4 保持 kept
        out = category_buckets(feats, _chart(field="cat"), where=None)
        other = next(r for r in out["rows"] if r["is_other"])
        assert other["other_value_count"] == contract.MAX_CATEGORY_GROUPS + 5 - contract.MAX_CATEGORY_GROUPS
        assert other["other_filterable"] is True
        assert other["other_overflow"] is False
        assert out["truncated"] is True

    def test_other_bucket_mean_recomputed_from_raw_values(self):
        """E09/P1-00：other 桶 mean 由原始数值重算，不可均值再平均。"""
        feats = []
        # 64 个 kept 组：count=5、v=10（计数最高，稳占 kept）
        for i in range(contract.MAX_CATEGORY_GROUPS):
            for _ in range(5):
                feats.append(_feat({"cat": f"k{i:03d}", "v": 10}))
        # 两个溢出组：数值个数不同（3 vs 1）、均值不同（30 vs 90）
        for _ in range(3):
            feats.append(_feat({"cat": "o1", "v": 30}))
        feats.append(_feat({"cat": "o2", "v": 90}))
        out = category_buckets(
            feats, _chart(field="cat", aggregation="mean", value_field="v"),
            where=None)
        other = next(r for r in out["rows"] if r["is_other"])
        assert out["truncated"] is True and other["count"] == 4
        # oracle：原始值 (30,30,30,90) → 全体均值 45；
        # 各组均值再平均会得 (30+90)/2 = 60（未加权，违反诚实规则）
        assert other["mean"] == 45.0

    def test_real_other_category_name_not_confused(self):
        """E12：真实类别名 __other__ 与溢出桶分立。"""
        feats = [_feat({"cat": "__other__"}), _feat({"cat": "z"})]
        out = category_buckets(feats, _chart(field="cat"), where=None)
        rows = out["rows"]
        # 真实 __other__ 是 kept 行（非 is_other），machine_value 为该字符串
        real = next(r for r in rows if r["machine_value"] == "__other__")
        assert real["is_other"] is False and real["count"] == 1
        assert not any(r["is_other"] for r in rows)

    def test_where_applied(self):
        from app.services.linked_exploration.contract import compile_combined_where
        cond = contract.parse_condition({
            "kind": "range", "condition_id": "c", "chart_id": "ch2",
            "field": "pop", "gte": 150, "lte": 450})
        w = compile_combined_where([cond])
        out = category_buckets(TYPED_FEATURES, _chart(field="cat"), where=w)
        # oracle：pop∈[150,450] → id2(200), id3(300), id4(400)
        assert out["matched"] == 3
        assert out["null_count"] == 1  # id4 显式 null cat

    def test_long_label_truncated_but_machine_value_kept(self):
        long_name = "x" * 300
        feats = [_feat({"cat": long_name})]
        out = category_buckets(feats, _chart(field="cat"), where=None)
        row = out["rows"][0]
        assert len(row["label"]) <= 129
        assert row["machine_value"] == long_name


class TestRangeBuckets:
    RANGE_FEATURES = [
        _feat({"v": 0.0}),
        _feat({"v": 10.0}),
        _feat({"v": 10.0}),     # 断点值：第二桶 [10,20) 开区间命中
        _feat({"v": 20.0}),
        _feat({"v": 25.0}),
        _feat({"v": None}),
        _feat({"v": "str"}),
        _feat({"v": True}),
        _feat({}),              # 缺席
    ]

    def test_half_open_last_closed(self):
        """E08：[a,b) 末桶闭；断点值 10 落第二桶。"""
        out = range_buckets(
            self.RANGE_FEATURES[:5], _chart(kind="range", field="v", bins=2),
            where=None)
        # oracle：min=0 max=25 bins=2 → [0,12.5) 和 [12.5,25]
        b0, b1 = out["bins"]
        assert b0["count"] == 3       # 0, 10, 10
        assert b1["count"] == 2       # 20, 25
        assert b0["upper_exclusive"] is True
        assert b1["upper_exclusive"] is False

    def test_exact_breakpoint_at_max_goes_last(self):
        feats = [_feat({"v": 0.0}), _feat({"v": 10.0})]
        out = range_buckets(feats, _chart(kind="range", field="v", bins=5), where=None)
        # oracle：bins=5 → 宽 2；10 是 max → 末桶闭命中 [8,10]
        assert out["bins"][-1]["count"] == 1

    def test_null_nonnumeric_invalid_disclosed(self):
        """E07/E09：null/非数值/无效数分开计数，不当 0。"""
        feats = [
            _feat({"v": 1.0}), _feat({"v": None}), _feat({"v": "s"}),
            _feat({"v": float("nan")}), _feat({"v": float("inf")}), _feat({}),
            _feat({"v": 3.0}),
        ]
        out = range_buckets(feats, _chart(kind="range", field="v", bins=2), where=None)
        assert out["valid_count"] == 2
        assert out["null_count"] == 2      # 显式 null + 缺席
        assert out["non_numeric_count"] == 1
        assert out["invalid_count"] == 2   # nan + inf

    def test_all_nonnumeric_empty_bins_honest(self):
        feats = [_feat({"v": "a"}), _feat({"v": None})]
        out = range_buckets(feats, _chart(kind="range", field="v", bins=4), where=None)
        assert out["bins"] == []
        assert out["valid_count"] == 0
        assert out["non_numeric_count"] == 1 and out["null_count"] == 1

    def test_degenerate_single_value(self):
        feats = [_feat({"v": 5.0}), _feat({"v": 5.0})]
        out = range_buckets(feats, _chart(kind="range", field="v", bins=4), where=None)
        assert out["bin_count"] >= 1
        assert sum(b["count"] for b in out["bins"]) == 2


class TestFacetVsFull:
    def test_facet_excludes_own_condition_only(self):
        """E11：facet 口径下本图自身条件不参与；别的条件保留。"""
        feats = [
            _feat({"cat": "a", "pop": 50}),
            _feat({"cat": "a", "pop": 500}),
            _feat({"cat": "b", "pop": 500}),
            _feat({"cat": "b", "pop": 50}),
        ]
        cat_cond = contract.parse_condition({
            "kind": "category", "condition_id": "c1", "chart_id": "ch-cat",
            "field": "cat", "values": ["a"]})
        rng_cond = contract.parse_condition({
            "kind": "range", "condition_id": "c2", "chart_id": "ch-rng",
            "field": "pop", "gte": 100})
        # pop 图（ch-rng）的 facet：只应用 cat 条件
        facet_where = contract.compile_combined_where(
            [cat_cond, rng_cond], exclude_chart_id="ch-rng")
        out = range_buckets(
            feats, _chart(kind="range", field="pop", bins=2, chart_id="ch-rng"),
            where=facet_where)
        # oracle：cat=a → pop ∈ {50, 500}
        assert out["matched"] == 2 and out["valid_count"] == 2
        # full：cat=a AND pop>=100 → 只有 (a, 500)
        full_where = contract.compile_combined_where([cat_cond, rng_cond])
        out2 = range_buckets(
            feats, _chart(kind="range", field="pop", bins=2, chart_id="ch-rng"),
            where=full_where)
        assert out2["matched"] == 1 and out2["valid_count"] == 1
