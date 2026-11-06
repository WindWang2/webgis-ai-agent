"""temporal_products 帧推导与时间过滤编译器测试（GOAL 03 M2）。

Oracle 锚点：
- 帧只来自真实观测桶（无 2024 数据则无 2024 帧）；
- 表示归一：year_int/epoch_s/epoch_ms/iso_string/date_string 各自正确、
  混合表示拒绝；
- iso 字符串字典序 = 时间序（同偏移）且 where AST 边界与数据同构；
- where AST / MapLibre filter 值同源；fingerprint 随 (ref, revision,
  frame) 变化。
"""
from __future__ import annotations


from app.services.gis_harness.temporal_products.frames import (
    buckets_to_contract_frames,
    detect_time_binding,
    granularity_from_span,
)
from app.services.gis_harness.temporal_products.time_filter import (
    compile_maplibre_filter,
    compile_where_ast,
    equal_interval_breaks,
    normalize_boundary_value,
    time_filter_fingerprint,
    union_window,
    window_where_ast,
)


def _fc(props_list):
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": None, "properties": p} for p in props_list
        ],
    }


YEARS = [2021, 2021, 2022, 2023, 2023, 2023]


class TestDetectYearInt:
    def test_chengdu_schools_years(self):
        # S1 场景数据面：6 要素横跨 3 年 → 恰 3 帧，计数真实。
        probe = detect_time_binding(_fc([{"year": y} for y in YEARS]))
        assert probe.ok
        assert probe.time_field == "year"
        assert probe.representation == "year_int"
        assert probe.granularity == "year"
        assert [b.count for b in probe.buckets] == [2, 1, 3]
        frames = buckets_to_contract_frames(probe.buckets)
        assert [f["id"] for f in frames] == ["f-2021", "f-2022", "f-2023"]
        assert frames[0]["start"] == "2021-01-01T00:00:00Z"
        assert frames[0]["end"] == "2022-01-01T00:00:00Z"

    def test_no_fabricated_periods(self):
        # 缺 2024：帧序列没有 2024（无虚构期数 —— Oracle 1）。
        probe = detect_time_binding(_fc([{"year": y} for y in (2023, 2025)]))
        assert probe.ok
        assert [b.key for b in probe.buckets] == ["2023", "2025"]
        assert "missing_period_gap" in probe.quality

    def test_single_period_quality_flag(self):
        probe = detect_time_binding(_fc([{"year": 2023}] * 5))
        assert probe.ok
        assert "single_period" in probe.quality
        assert len(probe.buckets) == 1

    def test_explicit_field_missing(self):
        probe = detect_time_binding(_fc([{"year": 2023}]), time_field="nope")
        assert not probe.ok
        assert probe.code == "no_time_field"

    def test_mixed_representation_refused(self):
        # 字符串 + 数值混合：字面比较无法诚实 → typed 拒绝。
        probe = detect_time_binding(_fc([{"year": 2023}, {"year": "2024"}]))
        assert not probe.ok
        assert probe.code == "non_uniform_time_format"

    def test_no_time_field_refused(self):
        probe = detect_time_binding(_fc([{"name": "x"}]))
        assert not probe.ok
        assert probe.code == "no_time_field"

    def test_bad_timezone_refused(self):
        probe = detect_time_binding(_fc([{"year": 2023}]), tz_name="Mars/Olympus")
        assert not probe.ok
        assert probe.code == "invalid_timezone"


class TestDetectOtherRepresentations:
    def test_epoch_ms(self):
        # 2023-01-01Z = 1672531200s = 1672531200000ms；2024-01-01Z = 1704067200000ms
        probe = detect_time_binding(_fc([
            {"t": 1672531200000}, {"t": 1672531200000 + 3600_000},
            {"t": 1704067200000},
        ]))
        assert probe.ok
        assert probe.representation == "epoch_ms"
        # 只有真实观测桶成为帧（2 天桶：两值同日）。
        assert len(probe.buckets) == 2
        # 显式年粒度 → 2 年桶。
        probe_y = detect_time_binding(_fc([
            {"t": 1672531200000}, {"t": 1672531200000 + 3600_000},
            {"t": 1704067200000},
        ]), granularity="year")
        assert [b.key for b in probe_y.buckets] == ["2023", "2024"]

    def test_iso_string_uniform_z(self):
        probe = detect_time_binding(_fc([
            {"ts": "2023-04-01T08:00:00Z"}, {"ts": "2023-07-15T10:30:00Z"},
            {"ts": "2024-02-01T00:00:00Z"},
        ]))
        assert probe.ok
        assert probe.representation == "iso_string"
        # 自动粒度：跨 ~10 个月 → day 或更粗（不 hour）。
        assert probe.granularity in ("day", "week", "month", "quarter", "year")

    def test_iso_string_mixed_offsets_refused(self):
        # 混合偏移：字符串字典序 ≠ 时间序 → 拒绝（科学性前提）。
        probe = detect_time_binding(_fc([
            {"ts": "2023-04-01T08:00:00Z"}, {"ts": "2023-04-01T16:00:00+08:00"},
        ]))
        assert not probe.ok
        assert probe.code == "non_uniform_time_format"

    def test_date_string_month(self):
        probe = detect_time_binding(_fc([
            {"m": "2023-01"}, {"m": "2023-01"}, {"m": "2023-03"},
        ]))
        assert probe.ok
        assert probe.representation == "date_string"
        assert [b.key for b in probe.buckets] == ["2023-01", "2023-03"]

    def test_month_granularity_explicit(self):
        probe = detect_time_binding(
            _fc([{"d": f"2023-{m:02d}-15"} for m in (1, 2, 2, 4)]),
            granularity="month",
        )
        assert probe.ok
        assert [b.key for b in probe.buckets] == ["2023-01", "2023-02", "2023-04"]

    def test_timezone_bucketing(self):
        # 同一 UTC instant 在不同契约时区可落不同日期桶 —— 时区是数据锚定
        # 的解释参数，不是墙钟伪造。
        fc_utc = _fc([{"ts": "2023-01-01T20:00:00Z"}, {"ts": "2023-01-02T02:00:00Z"}])
        p_utc = detect_time_binding(fc_utc, granularity="day", tz_name="UTC")
        p_sh = detect_time_binding(fc_utc, granularity="day", tz_name="Asia/Shanghai")
        assert p_utc.ok and p_sh.ok
        assert [b.key for b in p_utc.buckets] == ["2023-01-01", "2023-01-02"]
        # +08:00：两值分别是 1/2 04:00 与 1/2 10:00 → 同一日桶。
        assert [b.key for b in p_sh.buckets] == ["2023-01-02"]

    def test_snapshot_semantics_inferred_for_aligned_years(self):
        probe = detect_time_binding(_fc([{"year": y} for y in YEARS]))
        assert probe.semantics == "snapshot"  # 年对齐值 → 快照语义


class TestGranularityFromSpan:
    def test_five_years_of_hours_coarsens(self):
        span = 5 * 365 * 86400
        g = granularity_from_span(span, 512)
        assert g in ("year", "quarter", "month", "week", "day")

    def test_one_day_stays_hour(self):
        assert granularity_from_span(86400, 512) == "hour"


class TestTimeFilterCompiler:
    def test_year_int_where_ast(self):
        frame = {"id": "f-2023", "start": "2023-01-01T00:00:00Z", "end": "2024-01-01T00:00:00Z"}
        ast = compile_where_ast(frame, "year", "year_int", "year")
        assert ast == {
            "op": "and",
            "terms": [
                {"op": "gte", "field": "year", "value": 2023},
                {"op": "lt", "field": "year", "value": 2024},
            ],
        }

    def test_epoch_ms_where_ast(self):
        frame = {"start": "2023-01-01T00:00:00Z", "end": "2024-01-01T00:00:00Z"}
        ast = compile_where_ast(frame, "t", "epoch_ms", "year")
        assert ast["terms"][0]["value"] == 1672531200000
        assert ast["terms"][1]["value"] == 1704067200000

    def test_iso_string_lexicographic_boundary(self):
        frame = {"start": "2023-04-01T00:00:00Z", "end": "2023-07-01T00:00:00Z"}
        ast = compile_where_ast(frame, "ts", "iso_string", "month")
        assert ast["terms"][0]["value"] == "2023-04-01T00:00:00Z"
        assert ast["terms"][1]["value"] == "2023-07-01T00:00:00Z"
        # 字典序语义：数据 "2023-06-15T10:00:00Z" ∈ [lo, hi)。
        assert ast["terms"][0]["value"] <= "2023-06-15T10:00:00Z" < ast["terms"][1]["value"]

    def test_date_string_month_boundary_zero_padded(self):
        frame = {"start": "2023-01-01T00:00:00Z", "end": "2023-04-01T00:00:00Z"}
        v = normalize_boundary_value(frame["start"], "date_string", "quarter")
        assert v == "2023-01"
        hi = normalize_boundary_value(frame["end"], "date_string", "quarter")
        assert hi == "2023-04"

    def test_maplibre_filter_same_values_as_where(self):
        # 地图与统计同窗的结构性保证：两侧值完全同源。
        frame = {"start": "2023-01-01T00:00:00Z", "end": "2024-01-01T00:00:00Z"}
        ast = compile_where_ast(frame, "year", "year_int", "year")
        mlf = compile_maplibre_filter(frame, "year", "year_int", "year")
        assert mlf == [
            "all",
            [">=", ["get", "year"], ast["terms"][0]["value"]],
            ["<", ["get", "year"], ast["terms"][1]["value"]],
        ]

    def test_fingerprint_stability_and_sensitivity(self):
        base = time_filter_fingerprint("ref:s", 3, "year", "f-2023", "year_int")
        assert base == time_filter_fingerprint("ref:s", 3, "year", "f-2023", "year_int")
        # revision / frame / ref 任一变化 → 指纹变化。
        assert base != time_filter_fingerprint("ref:s", 4, "year", "f-2023", "year_int")
        assert base != time_filter_fingerprint("ref:s", 3, "year", "f-2024", "year_int")
        assert base != time_filter_fingerprint("ref:other", 3, "year", "f-2023", "year_int")

    def test_union_window_and_where(self):
        a = {"id": "f-2023", "start": "2023-01-01T00:00:00Z", "end": "2024-01-01T00:00:00Z"}
        b = {"id": "f-2026", "start": "2026-01-01T00:00:00Z", "end": "2027-01-01T00:00:00Z"}
        u = union_window(a, b)
        assert u["start"] == "2023-01-01T00:00:00Z"
        assert u["end"] == "2027-01-01T00:00:00Z"
        ast = window_where_ast(u["start"], u["end"], "year", "year_int", "year")
        assert ast["terms"][0]["value"] == 2023 and ast["terms"][1]["value"] == 2027

    def test_equal_interval_breaks_degenerate(self):
        # 分母/域退化 → None（typed 披露路径，不发明 0 宽分级）。
        assert equal_interval_breaks(5.0, 5.0) is None
        assert equal_interval_breaks(float("nan"), 5.0) is None
        breaks = equal_interval_breaks(0.0, 100.0, k=4)
        assert breaks == [0.0, 25.0, 50.0, 75.0, 100.0]

    def test_half_open_semantics_at_boundary(self):
        # [2023-01-01, 2024-01-01)：2023-12-31 元素命中，2024-01-01 不命中。
        frame = {"start": "2023-01-01T00:00:00Z", "end": "2024-01-01T00:00:00Z"}
        ast = compile_where_ast(frame, "year", "year_int", "year")
        assert ast["terms"][0]["value"] <= 2023 < ast["terms"][1]["value"]
        assert not (ast["terms"][0]["value"] <= 2024 < ast["terms"][1]["value"])


class TestIsoShapeSemantics:
    """R2-N1 回归：iso 字面形状（偏移族/小数秒宽度）统一 → 边界同构渲染。"""

    def _fc_iso(self, values):
        return _fc([{"ts": v} for v in values])

    def test_naive_iso_window_membership(self):
        # R2 复现样本：A=00:00:00（帧下界精确值）、C=次日 00:00 —— 修复前
        # A 无处归属、C 归错一天；修复后按 naive 同构渲染，归属正确。
        probe = detect_time_binding(self._fc_iso([
            "2023-04-01T00:00:00", "2023-04-01T12:00:00", "2023-04-02T00:00:00",
        ]), granularity="day")
        assert probe.ok and probe.representation == "iso_string"
        frames = buckets_to_contract_frames(probe.buckets)
        f1, f2 = frames[0], frames[1]
        ast1 = compile_where_ast(f1, "ts", "iso_string", "day", "UTC", probe.format_hint)
        ast2 = compile_where_ast(f2, "ts", "iso_string", "day", "UTC", probe.format_hint)
        lo1, hi1 = ast1["terms"][0]["value"], ast1["terms"][1]["value"]
        assert (lo1, hi1) == ("2023-04-01T00:00:00", "2023-04-02T00:00:00")
        # 逐值归属（字符串字典序 = 半开窗口语义）。
        for v, in_frame_1 in [("2023-04-01T00:00:00", True), ("2023-04-01T12:00:00", True),
                              ("2023-04-02T00:00:00", False)]:
            assert (lo1 <= v < hi1) is in_frame_1, v
        lo2, hi2 = ast2["terms"][0]["value"], ast2["terms"][1]["value"]
        assert ("2023-04-02T00:00:00" >= lo2) and ("2023-04-02T00:00:00" < hi2)

    def test_uniform_fractional_seconds_accepted(self):
        # R2-N4：统一毫秒时间戳（日志常见形状）可 bind（修复前整体误拒）。
        probe = detect_time_binding(self._fc_iso([
            "2023-04-01T08:00:00.123Z", "2023-04-02T09:30:00.456Z",
        ]), granularity="day")
        assert probe.ok and probe.representation == "iso_string"
        frames = buckets_to_contract_frames(probe.buckets)
        ast = compile_where_ast(frames[0], "ts", "iso_string", "day", "UTC",
                                probe.format_hint)
        assert ast["terms"][0]["value"] == "2023-04-01T00:00:00.000Z"

    def test_mixed_fractional_width_refused(self):
        probe = detect_time_binding(self._fc_iso([
            "2023-04-01T08:00:00.1Z", "2023-04-02T08:00:00.123Z",
        ]), granularity="day")
        assert not probe.ok and probe.code == "non_uniform_time_format"

    def test_uniform_offset_rendered_in_data_offset(self):
        # R2-N5：统一 +08:00 可 bind；边界按 +08:00 渲染（同构字典序）。
        probe = detect_time_binding(self._fc_iso([
            "2023-04-01T08:00:00+08:00", "2023-04-02T10:30:00+08:00",
        ]), granularity="day", tz_name="Asia/Shanghai")
        assert probe.ok, probe.message
        frames = buckets_to_contract_frames(probe.buckets)
        ast = compile_where_ast(frames[0], "ts", "iso_string", "day",
                                "Asia/Shanghai", probe.format_hint)
        lo, hi = ast["terms"][0]["value"], ast["terms"][1]["value"]
        assert lo.endswith("+08:00") and hi.endswith("+08:00")
        # 上海日桶 2023-04-01 = [00:00+08, 次日 00:00+08)。
        assert lo == "2023-04-01T00:00:00+08:00"
        assert "2023-04-01T08:00:00+08:00" >= lo and "2023-04-01T08:00:00+08:00" < hi
