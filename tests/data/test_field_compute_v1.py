"""G19 类型化字段计算 —— IR / 解释器 / 服务层回归（WP02–WP08, WP11）。

合成夹具：中文人口表、带空格字段名、前导零代码、带洞/多部件面、缺失与
零分母、跨 UTC 时间、选中范围、大整数、布尔/空串/null 混合。
断言独立计算答案、单位变换等价性、批大小不变性与实际输出回读，不只断言
``response.success``。
"""
from __future__ import annotations

import copy
import math

import pytest

from app.lib.cancellation import OperationCancelled
from app.services.data_preparation.field_compute import service
from app.services.data_preparation.field_compute.contracts import (
    FUNCTION_SET_VERSION, CompileError, ExecError,
)
from app.services.data_preparation.field_compute.functions import (
    function_help, static_unit,
)
from app.services.data_preparation.field_compute.geometry import (
    geometry_gate, measure_geometry,
)
from app.services.data_preparation.field_compute.interpreter import (
    EvalConfig, RowAbort, evaluate_rows,
)
from app.services.data_preparation.field_compute.parser import (
    CompileContext, compile_columns, compile_expression, parse_expression,
    render_expression,
)
from app.services.data_preparation.field_compute.snapshot import (
    build_anchor, freeze, parse_payload, verify_anchor,
)
from app.services.dataset_semantics import reset_dataset_semantic_store
from app.services.session_data import session_data_manager as sdm


@pytest.fixture(autouse=True)
def _isolated_storage(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.mapspec.store.BASE_STORAGE_DIR", tmp_path)
    reset_dataset_semantic_store()
    yield
    reset_dataset_semantic_store()


FIELDS = ("人口", "面积 平方千米", "面积 平方米", "基期", "现期",
          "行政区划代码", "时间", "布尔列", "空串列", "大整数", "名称")


def _ctx(**kw):
    base = dict(
        available_fields=FIELDS,
        field_units={"人口": "persons", "面积 平方千米": "square_kilometers",
                     "面积 平方米": "square_meters"},
        timezone="+08:00", date_format="%Y-%m-%d %H:%M:%S",
        crs="EPSG:4326",
    )
    base.update(kw)
    return CompileContext(**base)


def _feature(props, geom=None):
    return {"type": "Feature",
            "geometry": geom or {"type": "Point", "coordinates": [104.0, 30.0]},
            "properties": props}


def _fc(n=4):
    out = []
    for i in range(n):
        out.append(_feature({
            "人口": [1000, 0, 500, 1200][i % 4],
            "面积 平方千米": [10, 0, 25, 8][i % 4],
            "面积 平方米": [10_000_000, 0, 25_000_000, 8_000_000][i % 4],
            "基期": [100, 0, 200, 50][i % 4],
            "现期": [120, 10, 180, 75][i % 4],
            "行政区划代码": ["00123", "7", "", "00123"][i % 4],
            "时间": ["2024-01-01 00:30:00", "2024-02-29 23:30:00",
                    "2023-12-31 16:30:00", "bad-date"][i % 4],
            "布尔列": True, "空串列": "", "大整数": 9007199254740993 + i,
            "名称": f"第{i}区",
        }))
    return {"type": "FeatureCollection", "features": out}


def _payload(fc=None, crs="EPSG:4326", crs_source="declared"):
    return {"geojson": fc or _fc(), "crs": crs, "crs_source": crs_source}


async def _store(sid, payload=None):
    return await sdm.store(sid, payload or _payload(), prefix="test")


# ══ WP02：闭合 IR / 解析 / 指纹 / 安全边界 ═══════════════════════════════

class TestIR:
    def test_text_and_ir_compile_to_same_node(self):
        ctx = _ctx()
        from_text = compile_expression("{人口} / {面积 平方千米}", ctx)
        from_ir = compile_expression(parse_expression("{人口} / {面积 平方千米}"), ctx)
        assert from_text.canonical() == from_ir.canonical()
        assert render_expression(from_text) == "({人口} / {面积 平方千米})"

    def test_commutative_reorder_keeps_fingerprint(self):
        ctx = _ctx()
        a = compile_expression("{人口} + {基期}", ctx)
        b = compile_expression("{基期} + {人口}", ctx)
        assert a.canonical() == b.canonical()

    def test_chinese_and_spaced_field_names_are_safe_references(self):
        ctx = _ctx()
        node = compile_expression("{面积 平方千米} * 2", ctx)
        assert node.left.name == "面积 平方千米"
        # 可交换算子在 canonical 中排序操作数（重排等价不破坏指纹）
        canon = node.canonical()
        assert {"kind": "field", "name": "面积 平方千米"} in (
            canon["left"], canon["right"])

    @pytest.mark.parametrize("bad", [
        "__import__('os').system('echo pwned')",
        "open('/etc/passwd')",
        "eval('1+1')",
        "{人口}.__class__",
        "globals()",
        "os.system('ls')",
    ])
    def test_host_code_attempts_rejected(self, bad):
        with pytest.raises(CompileError) as ei:
            compile_expression(bad, _ctx())
        assert ei.value.code in ("UNKNOWN_NODE", "UNKNOWN_FUNCTION",
                                 "UNKNOWN_FIELD", "UNKNOWN_OPERATOR")

    def test_if_unselected_division_by_zero_is_accepted(self):
        node = compile_expression("if(true, 1, 1/0)", _ctx())
        assert node.kind == "call" and node.name == "if"

    def test_unknown_node_and_operator_rejected(self):
        with pytest.raises(CompileError) as ei:
            compile_expression({"kind": "explode", "params": {}}, _ctx())
        assert ei.value.code == "UNKNOWN_NODE"
        with pytest.raises(CompileError):
            compile_expression({"kind": "binary", "op": "pow",
                                "left": {"kind": "lit", "dtype": "number", "value": 2},
                                "right": {"kind": "lit", "dtype": "number", "value": 3}},
                               _ctx())

    def test_ast_depth_and_long_string_budgets(self):
        deep = "{人口}" + " + 1" * 40
        with pytest.raises(CompileError) as ei:
            compile_expression(deep, _ctx())
        assert ei.value.code == "BUDGET_EXCEEDED"
        with pytest.raises(CompileError) as ei2:
            compile_expression("'" + "x" * 600 + "'", _ctx())
        assert ei2.value.code == "BUDGET_EXCEEDED"

    def test_unsafe_integer_literal_rejected(self):
        with pytest.raises(CompileError) as ei:
            compile_expression("9007199254740993", _ctx())
        assert ei.value.code == "UNSAFE_INTEGER"

    def test_reserved_and_existing_field_rejected(self):
        ctx = _ctx()
        cols = [{"name": "geometry", "expression": "{人口} + 1"}]
        with pytest.raises(CompileError) as ei:
            compile_columns(cols, ctx)
        assert ei.value.code == "RESERVED_FIELD"
        with pytest.raises(CompileError) as ei2:
            compile_columns([{"name": "人口", "expression": "{基期} + 1"}], ctx)
        assert ei2.value.code == "FIELD_EXISTS"

    def test_cyclic_and_forward_reference_rejected(self):
        ctx = _ctx()
        with pytest.raises(CompileError) as ei:
            compile_columns([{"name": "A", "expression": "{A} + 1"}], ctx)
        assert ei.value.code in ("CYCLIC_DEPENDENCY", "UNKNOWN_FIELD")
        with pytest.raises(CompileError) as ei2:
            compile_columns([{"name": "A", "expression": "{B} + 1"},
                             {"name": "B", "expression": "{人口} + 1"}], ctx)
        assert ei2.value.code in ("FORWARD_REFERENCE", "UNKNOWN_FIELD")
        cols, order = compile_columns([
            {"name": "密度", "expression": "{人口} / {面积 平方千米}"},
            {"name": "等级", "expression": "if({密度} > 100, '高', '低')"},
        ], ctx)
        assert order == ("密度", "等级") and len(cols) == 2

    def test_unit_incompatible_add_and_convert_rejected(self):
        ctx = _ctx()
        with pytest.raises(CompileError) as ei:
            compile_expression("{人口} + {面积 平方千米}", ctx)
        assert ei.value.code == "UNIT_INCOMPATIBLE"
        with pytest.raises(CompileError) as ei2:
            compile_expression("{面积 平方千米} + {面积 平方米}", ctx)
        assert ei2.value.code == "UNIT_INCOMPATIBLE"
        with pytest.raises(CompileError) as ei3:
            compile_expression("convert_unit({人口}, 'persons', 'meters')", ctx)
        assert ei3.value.code == "UNIT_INCOMPATIBLE"

    def test_ambiguous_timezone_rejected(self):
        with pytest.raises(CompileError) as ei:
            compile_expression("parse_datetime({时间}, '%Y-%m-%d %H:%M:%S', 'local')",
                               _ctx())
        assert ei.value.code in ("UNKNOWN_TIMEZONE", "AMBIGUOUS_DATETIME")
        with pytest.raises(CompileError):
            compile_columns([{"name": "x", "expression": "{人口} + 1"}],
                            _ctx(timezone="Asia/Nowhere"))

    def test_function_catalog_is_bounded_and_versioned(self):
        help_items = function_help()
        assert len(help_items) >= 28 and FUNCTION_SET_VERSION == "g19-fc-1.0"
        assert all({"name", "family", "arg_types", "result_type",
                    "null_behavior", "unit_rule"} <= set(x) for x in help_items)

    def test_static_unit_density_evidence(self):
        node = compile_expression("{人口} / {面积 平方千米}", _ctx())
        info = static_unit(node, {"人口": "persons",
                                  "面积 平方千米": "square_kilometers"})
        assert info.measurement_kind == "density"
        assert info.status == "derived"
        assert dict(info.derived_from)["numerator"] == "人口"
        assert dict(info.derived_from)["denominator"] == "面积 平方千米"


# ══ WP03–WP05：确定执行 / 类型 / 文本 / 日期 / 单位 / 几何 ════════════════

def _eval(expressions, rows, *, field_units=None, error_policy="null",
          crs="EPSG:4326", crs_source="declared", selected=None, batch_size=100):
    ctx = _ctx(field_units=field_units or {}, crs=crs)
    cols, _ = compile_columns(expressions, ctx)
    mode, reason = geometry_gate(crs, crs_source)
    cfg = EvalConfig(
        field_units=dict(ctx.field_units),
        column_units={c.name: c.declared_unit for c in cols},
        timezone=ctx.timezone, date_format=ctx.date_format,
        error_policy=error_policy, geometry_mode=mode,
        geometry_block_reason=reason, crs=crs)
    return cols, evaluate_rows(rows, cols, selected, cfg, batch_size=batch_size)


class TestInterpreter:
    def test_numeric_known_answers_and_rounding(self):
        rows = [({"人口": 7, "基期": 3}, None), ({"人口": -7, "基期": 2}, None)]
        cols, r = _eval([
            {"name": "和", "expression": "{人口} + {基期}"},
            {"name": "商", "expression": "{人口} / {基期}"},
            {"name": "舍入", "expression": "round({人口} / {基期}, 2)"},
            {"name": "绝对值", "expression": "abs({人口})"},
        ], rows)
        assert r.rows[0]["和"] == 10 and r.rows[0]["商"] == pytest.approx(7 / 3)
        assert r.rows[0]["舍入"] == 2.33 and r.rows[1]["绝对值"] == 7
        assert r.stats["商"].errors == 0

    def test_division_by_zero_null_and_error_strategy(self):
        rows = [({"人口": 1000, "面积 平方千米": 0}, None),
                ({"人口": 1000, "面积 平方千米": 10}, None)]
        _cols, r = _eval([{"name": "密度", "expression": "{人口} / {面积 平方千米}"}],
                         rows)
        assert r.rows[0]["密度"] is None
        assert r.stats["密度"].errors == 1
        assert r.stats["密度"].samples[0]["code"] == "DIVISION_BY_ZERO"
        assert r.rows[1]["密度"] == 100.0
        with pytest.raises(RowAbort) as ei:
            _eval([{"name": "密度", "expression": "{人口} / {面积 平方千米}"}],
                  rows, error_policy="abort")
        assert ei.value.code == "DIVISION_BY_ZERO"

    def test_if_unselected_branch_does_not_create_error(self):
        rows = [({"基期": 0, "现期": 10}, None),
                ({"基期": 100, "现期": 120}, None),
                ({"基期": None, "现期": 10}, None)]
        _cols, r = _eval([{
            "name": "变化率",
            "expression": "if({基期} == 0, null, ({现期} - {基期}) / {基期} * 100)",
        }], rows)
        assert r.rows[0]["变化率"] is None     # 未伪造 0%
        assert r.rows[1]["变化率"] == 20.0
        assert r.rows[2]["变化率"] is None
        assert r.stats["变化率"].errors == 0

    def test_bool_empty_string_null_are_not_numeric(self):
        rows = [({"布尔列": True, "空串列": "", "人口": 5}, None)]
        _cols, r = _eval([
            {"name": "布尔当数", "expression": "{布尔列} + 1"},
            {"name": "空串转数", "expression": "to_number({空串列})"},
            {"name": "显式合并", "expression": "coalesce({空串列}, 'x')"},
            {"name": "空值合并", "expression": "coalesce(null, 'x')"},
        ], rows)
        assert r.rows[0]["布尔当数"] is None
        assert r.stats["布尔当数"].samples[0]["code"] == "TYPE_MISMATCH"
        assert r.stats["空串转数"].samples[0]["code"] == "EMPTY_STRING_NOT_ZERO"
        # 空串不是 null：coalesce 不把它当缺失，也不会隐式变成 0/'x'
        assert r.rows[0]["显式合并"] == ""
        assert r.rows[0]["空值合并"] == "x"

    def test_admin_code_leading_zero_and_unicode(self):
        rows = [({"行政区划代码": "00123", "名称": "😀 中文 A"}, None),
                ({"行政区划代码": "7", "名称": "a\tb"}, None)]
        _cols, r = _eval([
            {"name": "规范代码", "expression": "pad_left({行政区划代码}, 6, '0')"},
            {"name": "长度", "expression": "str_length({名称})"},
            {"name": "大写", "expression": "upper(trim({名称}))"},
        ], rows)
        assert r.rows[0]["规范代码"] == "000123"
        assert r.rows[1]["规范代码"] == "000007"
        assert r.rows[0]["长度"] == len("😀 中文 A")   # non-BMP 计 1 码点
        assert r.rows[0]["大写"].startswith("😀")

    def test_big_integer_precision_not_silently_rounded(self):
        n = 9007199254740993
        rows = [({"大整数": n}, None)]
        _cols, r = _eval([
            {"name": "文本", "expression": "to_string({大整数})"},
            {"name": "原值", "expression": "{大整数} + 0"},
        ], rows)
        assert r.rows[0]["文本"] == str(n)
        assert r.rows[0]["原值"] == str(n)      # 超 2^53 以文本写出，不舍入
        assert r.precision_policy == "bigint_as_string"

    def test_datetime_utc_boundary_leap_day_and_invalid(self):
        rows = [({"时间": "2024-01-01 00:30:00"}, None),
                ({"时间": "2024-02-29 23:30:00"}, None),
                ({"时间": "2023-12-31 16:30:00"}, None),
                ({"时间": "2023-02-30 10:00:00"}, None),
                ({"时间": "bad-date"}, None)]
        _cols, r = _eval([{
            "name": "月份",
            "expression": "datetime_part(parse_datetime({时间}, '%Y-%m-%d %H:%M:%S', '+08:00'), 'month')",
        }], rows)
        assert [x["月份"] for x in r.rows[:3]] == [1, 2, 12]
        assert r.rows[3]["月份"] is None and r.rows[4]["月份"] is None
        assert r.stats["月份"].errors == 2
        assert r.stats["月份"].samples[0]["code"] == "INVALID_DATE"

    def test_unit_conversion_equivalence(self):
        rows = [({"人口": 1000, "面积 平方千米": 10, "面积 平方米": 10_000_000}, None)]
        _cols, r = _eval([
            {"name": "密度A", "expression": "{人口} / {面积 平方千米}"},
            {"name": "密度B", "expression": "{人口} / convert_unit({面积 平方米}, 'square_meters', 'square_kilometers')"},
        ], rows)
        assert r.rows[0]["密度A"] == r.rows[0]["密度B"] == 100.0
        assert r.stats["密度B"].errors == 0

    def test_percent_fraction_scale_is_explicit(self):
        rows = [({"基期": 1}, None)]
        _cols, r = _eval([{
            "name": "百分数", "expression": "convert_unit({基期}, 'fraction', 'percent')",
        }], rows)
        assert r.rows[0]["百分数"] == 100.0

    def test_batch_size_invariance(self):
        rows = [(p, None) for p in [
            {"人口": 1000 + i, "面积 平方千米": 10 + (i % 3),
             "基期": 100, "现期": 120, "时间": "2024-01-01 00:30:00",
             "行政区划代码": "00123", "名称": "n"} for i in range(37)]]
        exprs = [
            {"name": "密度", "expression": "{人口} / {面积 平方千米}"},
            {"name": "规范代码", "expression": "pad_left({行政区划代码}, 6, '0')"},
            {"name": "月份", "expression": "datetime_part(parse_datetime({时间}, '%Y-%m-%d %H:%M:%S', '+08:00'), 'month')"},
        ]
        _c1, r1 = _eval(exprs, rows, batch_size=1)
        _c2, r2 = _eval(exprs, rows, batch_size=7)
        _c3, r3 = _eval(exprs, rows, batch_size=100)
        assert r1.rows == r2.rows == r3.rows

    def test_selected_rows_keep_full_dataset(self):
        rows = [({"人口": 100, "面积 平方千米": 10}, None) for _ in range(4)]
        _cols, r = _eval([{"name": "密度", "expression": "{人口} / {面积 平方千米}"}],
                         rows, selected=[True, False, True, False])
        assert len(r.rows) == 4
        assert [x["密度"] for x in r.rows] == [10.0, None, 10.0, None]
        assert all(x["人口"] == 100 for x in r.rows)   # 原属性不变

    def test_no_nan_or_infinity_written(self):
        rows = [({"人口": 1, "面积 平方千米": 0}, None),
                ({"人口": 1e308, "面积 平方千米": 1e-308}, None)]
        _cols, r = _eval([{"name": "密度", "expression": "{人口} / {面积 平方千米}"}],
                         rows)
        assert all(v is None or (isinstance(v, (int, float)) and math.isfinite(v))
                   for x in r.rows for v in [x["密度"]])

    # ── 几何（CRS 门控）────────────────────────────────────────────────
    def test_geodesic_area_holes_multipart_and_dateline(self):
        square = {"type": "Polygon", "coordinates": [[
            [104.0, 30.0], [105.0, 30.0], [105.0, 31.0], [104.0, 31.0], [104.0, 30.0]]]}
        with_hole = {"type": "Polygon", "coordinates": [
            square["coordinates"][0],
            [[104.2, 30.2], [104.2, 30.8], [104.8, 30.8], [104.8, 30.2], [104.2, 30.2]],
        ]}
        multi = {"type": "MultiPolygon", "coordinates": [
            [square["coordinates"][0]],
            [[[106.0, 30.0], [107.0, 30.0], [107.0, 31.0], [106.0, 31.0], [106.0, 30.0]]],
        ]}
        a, _ = measure_geometry(square, "area_km2", "geodesic", "", "EPSG:4326")
        b, ev = measure_geometry(with_hole, "area_km2", "geodesic", "", "EPSG:4326")
        c, _ = measure_geometry(multi, "area_km2", "geodesic", "", "EPSG:4326")
        assert 9_000 < a < 11_000
        assert b < a and ev["has_holes"] is True
        assert c == pytest.approx(a * 2, rel=0.02)
        crossing = {"type": "Polygon", "coordinates": [[
            [179.0, 30.0], [-179.0, 30.0], [-179.0, 31.0], [179.0, 31.0], [179.0, 30.0]]]}
        _v, ev_cross = measure_geometry(crossing, "area_km2", "geodesic", "",
                                        "EPSG:4326")
        assert ev_cross["dateline_crossing"] is True

    def test_crs_unknown_blocks_geometry_but_not_text(self):
        rows = [({"行政区划代码": "00123"}, {"type": "Polygon", "coordinates": [[
            [104.0, 30.0], [105.0, 30.0], [105.0, 31.0], [104.0, 31.0], [104.0, 30.0]]]})]
        _cols, r = _eval([
            {"name": "代码", "expression": "pad_left({行政区划代码}, 6, '0')"},
            {"name": "面积", "expression": "area_km2()"},
        ], rows, crs="", crs_source="assumed")
        assert r.rows[0]["代码"] == "000123"
        assert r.rows[0]["面积"] is None
        assert r.stats["面积"].samples[0]["code"] == "CRS_UNKNOWN"
        assert geometry_gate("EPSG:3857")[1] == "CRS_NOT_METRIC"


# ══ WP06–WP08：版本锚 / 范围 / 预览 / 原子登记 ═══════════════════════════

class TestService:
    async def test_full_journey_and_original_immutable(self):
        sid = "sess-fc-journey"
        original = _payload()
        snapshot = copy.deepcopy(original)
        ref = await _store(sid, original)
        created = await service.create_plan({
            "session_id": sid, "source_ref": ref, "owner": "user-1",
            "columns": [
                {"name": "人口密度", "expression": "{人口} / {面积 平方千米}"},
                {"name": "变化率", "expression": "if({基期} == 0, null, ({现期} - {基期}) / {基期} * 100)"},
                {"name": "分级", "expression": "if({人口密度} > 100, '高', '低')"},
            ],
            "field_units": {"人口": "persons",
                            "面积 平方千米": "square_kilometers"},
            "timezone": "+08:00", "error_policy": "null",
        })
        plan = created["plan"]
        assert plan.anchor.content_hash and plan.compute_fingerprint
        pv = await service.preview(plan.plan_id, sid, generation=1)
        assert pv["kind"] == "sample" and pv["sampled_rows"] == 4
        assert pv["columns"][0]["measurement_kind"] == "density"
        assert pv["confirmation"]["params_fingerprint"] == plan.compute_fingerprint
        receipt = await service.execute(
            plan.plan_id, sid,
            expected_fingerprint=pv["confirmation"]["params_fingerprint"],
            project_id="proj-1")
        assert receipt.output_ref.startswith("ref:fc-")
        assert receipt.output_ref != ref
        assert receipt.row_count == 4 and receipt.error_count >= 1
        assert receipt.computed_rows < receipt.row_count   # 失败行不算成功
        got = await service.get_result(sid, receipt.output_ref)
        assert got["row_count"] == 4 and "人口密度" in got["fields"]
        assert got["sample_rows"][0]["人口密度"] == 100.0
        # 原件属性/几何完全不变
        assert await sdm.get(sid, ref) == snapshot
        # 公式、单位与来源可解释
        assert any("人口密度" in f for f in receipt.formula_summary)
        assert any(c.name == "人口密度" and c.measurement_kind == "density"
                   for c in receipt.columns)
        assert receipt.unit_summary["人口密度"]["unit_status"] == "derived"
        # 项目保存可重开找回
        items = await service.list_project_results(sid, "proj-1")
        assert len(items) == 1 and items[0]["output_ref"] == receipt.output_ref
        assert items[0]["input_ref"] == ref
        assert any("人口密度" in f for f in items[0]["formula_summary"])
        assert receipt.persistence == "project_artifact"
        # 幂等
        again = await service.execute(plan.plan_id, sid)
        assert again.output_ref == receipt.output_ref
        assert again.created_at == receipt.created_at

    async def test_stale_plan_after_source_change_rejected(self):
        sid = "sess-fc-stale"
        ref = await _store(sid)
        created = await service.create_plan({
            "session_id": sid, "source_ref": ref,
            "columns": [{"name": "密度", "expression": "{人口} / {面积 平方千米}"}],
            "field_units": {"人口": "persons",
                            "面积 平方千米": "square_kilometers"},
        })
        plan = created["plan"]
        await service.preview(plan.plan_id, sid)
        await sdm.overwrite(sid, ref, _payload(_fc(9)))
        with pytest.raises(ExecError) as ei:
            await service.preview(plan.plan_id, sid)
        assert ei.value.code == "STALE_PLAN"
        # 新计划刷新后可继续
        created2 = await service.create_plan({
            "session_id": sid, "source_ref": ref,
            "columns": [{"name": "密度", "expression": "{人口} / {面积 平方千米}"}],
        })
        pv2 = await service.preview(created2["plan"].plan_id, sid)
        assert pv2["total_rows"] == 9

    async def test_snapshot_changed_mid_execution_aborts(self, monkeypatch):
        """第二批读取时源被编辑 → 发布前二次核对中止，不发布混合 revision。"""
        sid = "sess-fc-snapshot"
        ref = await _store(sid)
        created = await service.create_plan({
            "session_id": sid, "source_ref": ref,
            "columns": [{"name": "密度", "expression": "{人口} / {面积 平方千米}"}],
        })
        plan = created["plan"]
        real_get = sdm.get

        async def _mutating_get(session_id, ref_id, *a, **kw):
            if ref_id == ref and getattr(_mutating_get, "armed", False):
                # 模拟 G14/#2022 在第二批期间编辑源：读回的必须是新版本。
                await sdm.overwrite(sid, ref, _payload(_fc(9)))
                _mutating_get.armed = False
            return await real_get(session_id, ref_id, *a, **kw)

        # 计算阶段（to_thread）之后、verify_anchor 之前的那次 get 触发变更
        import app.services.data_preparation.field_compute.service as svc
        real_prepare = svc._prepare

        async def _armed_prepare(*a, **kw):
            result = await real_prepare(*a, **kw)
            _mutating_get.armed = True
            return result

        monkeypatch.setattr(svc, "_prepare", _armed_prepare)
        monkeypatch.setattr(sdm, "get", _mutating_get)
        with pytest.raises(ExecError) as ei:
            await service.execute(plan.plan_id, sid)
        assert ei.value.code == "SNAPSHOT_CHANGED"
        # 无可见半成品：没有任何 receipt 被登记
        from app.services.data_preparation.field_compute import receipt as rc
        assert await rc.find_receipt(sid, plan.compute_fingerprint, ref,
                                     plan.anchor.content_hash) is None

    async def test_cancel_and_write_failure_publish_nothing(self, monkeypatch):
        sid = "sess-fc-cancel"
        ref = await _store(sid)
        created = await service.create_plan({
            "session_id": sid, "source_ref": ref,
            "columns": [{"name": "密度", "expression": "{人口} / {面积 平方千米}"}],
        })
        plan = created["plan"]
        import app.services.data_preparation.field_compute.service as svc
        real_check = svc._check_cancel
        monkeypatch.setattr(svc, "_check_cancel",
                            lambda *_a, **_kw: (_ for _ in ()).throw(
                                OperationCancelled("test cancel")))
        with pytest.raises(ExecError) as ei:
            await service.execute(plan.plan_id, sid)
        assert ei.value.code == "CANCELLED"
        monkeypatch.setattr(svc, "_check_cancel", real_check)
        # 原件与其他结果不变
        assert (await sdm.get(sid, ref))["geojson"]["features"][0]["properties"]["人口"] == 1000

    async def test_owner_change_and_fingerprint_drift_rejected(self):
        sid = "sess-fc-owner"
        ref = await _store(sid)
        created = await service.create_plan({
            "session_id": sid, "source_ref": ref, "owner": "alice",
            "columns": [{"name": "密度", "expression": "{人口} / {面积 平方千米}"}],
        })
        with pytest.raises(ExecError) as ei:
            await service.execute(created["plan"].plan_id, sid,
                                  expected_fingerprint="wrong")
        assert ei.value.code == "STALE_PLAN"
        # owner 是提示性元数据而非权限门禁（隔离在 session 层），
        # 跨 UI / Pi 入口复用同一计划不得被误拒。
        receipt = await service.execute(created["plan"].plan_id, sid, owner="bob")
        assert receipt.output_ref.startswith("ref:fc-")

    async def test_scope_predicate_and_selection_reliability(self):
        sid = "sess-fc-scope"
        ref = await _store(sid)
        scope = {"mode": "predicate",
                 "predicate": {"field": "人口", "op": "gt", "value": 600},
                 "unselected_policy": "null"}
        created = await service.create_plan({
            "session_id": sid, "source_ref": ref, "scope": scope,
            "columns": [{"name": "密度", "expression": "{人口} / {面积 平方千米}"}],
        })
        pv = await service.preview(created["plan"].plan_id, sid)
        assert pv["scope"]["matched_rows"] == 2
        receipt = await service.execute(created["plan"].plan_id, sid)
        got = await service.get_result(sid, receipt.output_ref)
        assert len(got["sample_rows"]) == 4             # 未悄悄缩小数据集
        vals = [r["密度"] for r in got["sample_rows"]]
        assert vals[0] == 100.0 and vals[1] is None

        # 选择列表被截断 / 过期 → 一律拒绝（不把样本当全量选中）
        sel_ref = await sdm.store(sid, {"ids": ["A"], "truncated": True,
                                        "revision": "r1"}, prefix="sel")
        bad_scope = {"mode": "selection", "selection_ref": sel_ref,
                     "selection_revision": "r1", "selection_complete": True,
                     "selection_total": 1, "id_field": "名称"}
        with pytest.raises(CompileError) as ei:
            await service.create_plan({
                "session_id": sid, "source_ref": ref, "scope": bad_scope,
                "columns": [{"name": "密度", "expression": "{人口} / 1"}],
            })
        assert ei.value.code == "SELECTION_UNRELIABLE"

        # 过期 selection_revision → 拒绝并提示重新选择
        stale_ref = await sdm.store(
            sid, {"ids": ["第0区", "第1区", "第2区", "第3区"],
                  "revision": "r0"}, prefix="sel")
        stale_scope = {"mode": "selection", "selection_ref": stale_ref,
                       "selection_revision": "r1", "selection_complete": True,
                       "selection_total": 4, "id_field": "名称"}
        with pytest.raises(CompileError) as ei2:
            await service.create_plan({
                "session_id": sid, "source_ref": ref, "scope": stale_scope,
                "columns": [{"name": "密度", "expression": "{人口} / 1"}],
            })
        assert ei2.value.code == "SELECTION_STALE"

        # 完整且 revision 一致的可靠选择集 → 精确命中，全体行保留
        good_ref = await sdm.store(
            sid, {"ids": ["第0区", "第2区"], "revision": "r1"}, prefix="sel")
        good_scope = {"mode": "selection", "selection_ref": good_ref,
                      "selection_revision": "r1", "selection_complete": True,
                      "selection_total": 2, "id_field": "名称"}
        created = await service.create_plan({
            "session_id": sid, "source_ref": ref, "scope": good_scope,
            "columns": [{"name": "密度", "expression": "{人口} / {面积 平方千米}"}],
        })
        receipt = await service.execute(created["plan"].plan_id, sid)
        got = await service.get_result(sid, receipt.output_ref)
        assert got["row_count"] == 4
        assert got["sample_rows"][0]["密度"] == 100.0
        assert got["sample_rows"][1]["密度"] is None

    async def test_table_shape_supported_and_disclosed(self):
        sid = "sess-fc-table"
        payload = {"rows": [{"人口": 100, "面积": 10}, {"人口": 200, "面积": 20}],
                   "crs": ""}
        ref = await sdm.store(sid, payload, prefix="test")
        d = await service.describe_source(sid, ref)
        assert d["data_shape"] == "table" and d["row_count"] == 2
        created = await service.create_plan({
            "session_id": sid, "source_ref": ref,
            "columns": [{"name": "密度", "expression": "{人口} / {面积}"}],
        })
        receipt = await service.execute(created["plan"].plan_id, sid)
        got = await service.get_result(sid, receipt.output_ref)
        assert got["data_shape"] == "table"
        assert got["sample_rows"][0]["密度"] == 10.0

    async def test_geometry_blocked_in_service_does_not_break_other_columns(self):
        sid = "sess-fc-crs"
        payload = _payload(crs="", crs_source="assumed")
        ref = await sdm.store(sid, payload, prefix="test")
        created = await service.create_plan({
            "session_id": sid, "source_ref": ref,
            "columns": [
                {"name": "代码", "expression": "pad_left({行政区划代码}, 6, '0')"},
                {"name": "面积", "expression": "area_km2()"},
            ],
        })
        assert created["geometry"]["block_reason"] == "CRS_UNKNOWN"
        assert any("阻断" in w for w in created["warnings"])
        pv = await service.preview(created["plan"].plan_id, sid)
        assert "GEOMETRY_BLOCKED" in pv["degraded"]
        receipt = await service.execute(created["plan"].plan_id, sid)
        got = await service.get_result(sid, receipt.output_ref)
        assert got["sample_rows"][0]["代码"] == "000123"
        assert got["sample_rows"][0]["面积"] is None
        assert any(c.name == "面积" and c.errors > 0 for c in receipt.columns)


class TestSnapshotHelpers:
    def test_anchor_detects_content_and_schema_change(self):
        p = _payload()
        anchor = build_anchor(p, "ref:x")
        verify_anchor(freeze(p), anchor)
        changed = copy.deepcopy(p)
        changed["geojson"]["features"][0]["properties"]["人口"] = 999
        with pytest.raises(ExecError) as ei:
            verify_anchor(changed, anchor)
        assert ei.value.code == "SNAPSHOT_CHANGED"
        schema_changed = copy.deepcopy(p)
        schema_changed["geojson"]["features"][0]["properties"]["新列"] = "x"
        with pytest.raises(ExecError):
            verify_anchor(schema_changed, anchor)

    def test_row_limit_not_silently_truncated(self):
        from app.services.data_preparation.field_compute.contracts import MAX_ROWS
        big = {"rows": [{"a": i} for i in range(MAX_ROWS + 1)]}
        with pytest.raises(ExecError) as ei:
            parse_payload(big)
        assert ei.value.code == "ROW_LIMIT_EXCEEDED"

    async def test_write_failure_is_typed(self, monkeypatch):
        """store 失败 → ExecError(STORE_FAILED)，不发布任何 ref。"""
        import app.services.data_preparation.field_compute.service as svc
        sid = "sess-fc-write"
        ref = await _store(sid)
        created = await svc.create_plan({
            "session_id": sid, "source_ref": ref,
            "columns": [{"name": "密度", "expression": "{人口} / 1"}],
        })
        real_store = sdm.store

        async def _failing(*a, **kw):
            if kw.get("prefix") == "fc":
                return ""
            return await real_store(*a, **kw)

        monkeypatch.setattr(sdm, "store", _failing)
        with pytest.raises(ExecError) as ei:
            await svc.execute(created["plan"].plan_id, sid)
        assert ei.value.code == "STORE_FAILED"
