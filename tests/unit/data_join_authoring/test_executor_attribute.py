"""G38 属性关联执行面验收（J01–J09、J14、J27；人工推导 oracle）。

差分兼容：`test_attribute_matches_federation_kernel_semantics` 用
federation ``attribute_join_local`` 同夹具对拍（单键 + inner + null 排除）。
"""
from __future__ import annotations

import pytest

from app.services.data_join_authoring.errors import DataJoinError
from app.services.data_join_authoring.executor import JoinExecutor
from app.services.data_join_authoring.plan import parse_plan
from tests.unit.data_join_authoring.fixtures import (
    COMPOSITE_LEFT_ROWS,
    COMPOSITE_RIGHT_ROWS,
    DUPLICATE_RIGHT_TABLE,
    FANOUT_LEFT_ROWS,
    FANOUT_RIGHT_ROWS,
    TYPES_LEFT_ROWS,
    TYPES_RIGHT_ROWS,
    admin_boundaries,
    attr_plan,
    explosion_fixture,
    loaded_from_fc,
    loaded_from_rows,
    population_table,
)


def _run(plan_dict, left_src, right_src, **kw):
    plan = parse_plan(plan_dict)
    ex = JoinExecutor(plan, **kw)
    out = ex.run_attribute(left_src, right_src)
    return out, ex.stats, plan


# ── J01：行政区左关联人口表（旗舰 A） ────────────────────────────────

def test_j01_left_join_keeps_all_regions_with_null_population():
    left = loaded_from_fc("left", admin_boundaries(), "fpL")
    right = loaded_from_rows("right", population_table(), "fpR")
    out, stats, _ = _run(attr_plan(), left, right)
    # 全部左区保留（4）；无匹配区（110107）明确空值；输出含左几何
    assert stats.output_rows == 4
    assert stats.matched_left_rows == 3
    assert stats.unmatched_left_rows == 1
    by_code = {r["adcode"]: r for r in out}
    assert by_code["110101"]["pop"] == 708_829
    assert by_code["110107"]["pop"] is None
    assert by_code["110107"]["_join_status"] == "unmatched"
    assert by_code["110101"]["_join_status"] == "matched"
    # 左 _wfid 保留（many_to_one 单行输出）
    assert by_code["110101"]["_wfid"] == "wf1-a"
    # 左几何保留、右表无几何混入
    assert out[0]["__geometry__"]["type"] == "Polygon"
    # 未匹配右行（110999）对账
    assert stats.unmatched_right_rows == 1


def test_j02_inner_join_reconciles_counts():
    left = loaded_from_fc("left", admin_boundaries(), "fpL")
    right = loaded_from_rows("right", population_table(), "fpR")
    out, stats, _ = _run(attr_plan(keep_side="inner"), left, right)
    assert stats.output_rows == 3
    # 输入总数 / 未匹配 / 输出可对账：4 = 3 + 1；右 4 = 3 + 1
    assert stats.left_rows == 4 and stats.right_rows == 4
    assert stats.left_rows == stats.output_rows + stats.unmatched_left_rows
    assert stats.right_rows == (
        stats.matched_pairs + stats.unmatched_right_rows + stats.right_null_keys)
    assert all(r["_join_status"] == "matched" for r in out)


def test_j03_table_times_table_yields_typed_rows_no_geometry():
    # 纯表左 × 纯表右（无几何）：输出不带任何几何通道
    left = loaded_from_rows("left", COMPOSITE_LEFT_ROWS, "fpL")
    right = loaded_from_rows("right", COMPOSITE_RIGHT_ROWS, "fpR")
    plan = attr_plan(
        keys=[{"left_field": "prov", "right_field": "p"},
              {"left_field": "county", "right_field": "c"}],
        keep_side="inner",
    )
    out, stats, _ = _run(plan, left, right)
    assert stats.output_rows == 2
    assert all("__geometry__" not in r for r in out)
    assert all("geometry" not in r for r in out)


def test_j04_composite_key_prevents_cross_province_mixing():
    left = loaded_from_rows("left", COMPOSITE_LEFT_ROWS, "fpL")
    right = loaded_from_rows("right", COMPOSITE_RIGHT_ROWS, "fpR")
    plan = attr_plan(
        keys=[{"left_field": "prov", "right_field": "p"},
              {"left_field": "county", "right_field": "c"}],
        keep_side="left",
    )
    out, _, _ = _run(plan, left, right)
    by = {(r["prov"], r["county"]): r for r in out}
    assert by[("13", "正定县")]["region"] == "河北"
    assert by[("41", "正定县")]["region"] == "河南"     # 同名县不串数据
    assert by[("13", "无匹配县")]["region"] is None
    # 反证：单键 county（右「正定县」跨省重复）违反 many_to_one 唯一性 →
    # typed 阻断 —— 匹配由复合键决定，不静默串数据
    single = attr_plan(keys=[{"left_field": "county", "right_field": "c"}],
                       keep_side="inner")
    with pytest.raises(DataJoinError) as ei:
        _run(single, left, right)
    assert ei.value.code == "cardinality_violation"


def test_j05_type_and_null_policy_on_executor():
    left = loaded_from_rows("left", TYPES_LEFT_ROWS, "fpL")
    right = loaded_from_rows("right", TYPES_RIGHT_ROWS, "fpR")
    plan = attr_plan(
        keys=[{"left_field": "k", "right_field": "k"}],
        keep_side="left", output_cardinality="one_to_many",
        field_map=[{"side": "left", "field": "k"},
                   {"side": "right", "field": "v"}])
    out, stats, _ = _run(plan, left, right)
    by_key = {}
    for r in out:
        by_key.setdefault(repr(r["k"]), []).append(r)
    # "001" 只匹配字符串 "001"；"1" 只匹配 "1"；数字 1 匹配 1 与 1.0；
    # True 只匹配 True；空串只匹配空串；null/missing 不匹配（含 null 对 null）
    assert len(by_key.get("'001'", [])) == 1
    assert len(by_key.get("'1'", [])) == 1
    assert len(by_key.get("1", [])) == 2      # 1 与 1.0（数值族）
    assert len(by_key.get("True", [])) == 1
    assert len(by_key.get("''", [])) == 1
    null_rows = [r for r in out if r["k"] is None and r["_join_status"] == "null_key"]
    assert len(null_rows) == 2                # None 与 missing
    assert stats.left_null_keys == 2
    assert stats.right_null_keys == 1         # 右侧 None 键不索引
    # False 不被左命中（左无 False）→ 右未匹配对账
    assert stats.unmatched_right_rows == 1


def test_j05_explicit_transform_applied():
    left = loaded_from_rows("left", {"rows": [{"k": " 110101 "}]}, "fpL")
    right = loaded_from_rows("right", {"rows": [{"k": "110101", "v": 9}]}, "fpR")
    plan = attr_plan(
        keys=[{"left_field": "k", "right_field": "k", "transform": "trim"}],
        keep_side="inner",
        field_map=[{"side": "right", "field": "v"}])
    out, _, _ = _run(plan, left, right)
    assert out[0]["v"] == 9


def test_j06_full_scan_blocks_many_to_one_with_duplicate_right():
    # 重复键在第 2 行 —— 远超预览前 50 行也必须阻断（全量诊断）
    pad = {"rows": [{"adcode": f"pad{i}", "v": i} for i in range(60)] +
                  DUPLICATE_RIGHT_TABLE["rows"]}
    left = loaded_from_rows(
        "left", {"rows": [{"adcode": "110101"}, {"adcode": "110102"}]}, "fpL")
    right = loaded_from_rows("right", pad, "fpR")
    with pytest.raises(DataJoinError) as ei:
        _run(attr_plan(), left, right)
    assert ei.value.code == "cardinality_violation"
    assert ei.value.detail["right_duplicate_keys"] == 1
    assert ei.value.detail["right_rows_in_duplicate_keys"] == 2


def test_j07_one_to_many_expansion_lineage_and_unique_ids():
    left = loaded_from_rows("left", FANOUT_LEFT_ROWS, "fpL")
    right = loaded_from_rows("right", FANOUT_RIGHT_ROWS, "fpR")
    plan = attr_plan(
        keys=[{"left_field": "code", "right_field": "code"}],
        keep_side="inner", output_cardinality="one_to_many",
        field_map=[{"side": "left", "field": "code"},
                   {"side": "right", "field": "v"}])
    out, stats, _ = _run(plan, left, right)
    assert stats.output_rows == 3 and stats.matched_pairs == 3
    ids = [r["_wfid"] for r in out]
    assert len(set(ids)) == 3                       # 派生身份无重复
    a_rows = [r for r in out if r["code"] == "A"]
    assert len(a_rows) == 2
    assert {r["v"] for r in a_rows} == {1, 2}       # 配对完整（右输入序）
    assert all(r["_join_left_ref"] for r in out)    # 左父 lineage 保留
    assert all(r["_join_right_ref"] for r in out)


def test_j08_pair_budget_estimated_before_allocation():
    fx = explosion_fixture(n_dup=1200)     # 左右各 1200 行，5 个键 → 1200*1200/5 对
    left = loaded_from_rows("left", fx["left"], "fpL")
    right = loaded_from_rows("right", fx["right"], "fpR")
    plan = attr_plan(
        keys=[{"left_field": "k", "right_field": "k"}],
        keep_side="inner", output_cardinality="one_to_many",
        budgets={"max_join_pairs": 100_000},
        field_map=[{"side": "left", "field": "k"},
                   {"side": "right", "field": "v"}])
    with pytest.raises(DataJoinError) as ei:
        _run(plan, left, right)
    assert ei.value.code == "budget_exceeded"
    assert ei.value.detail["estimated_pairs"] > 100_000  # 分配前拦截


def test_j09_same_name_and_reserved_field_conflicts():
    left = loaded_from_rows(
        "left", {"rows": [{"_wfid": "wf1-x", "jk": 1, "value": 1, "name": "L"}]},
        "fpL")
    right = loaded_from_rows(
        "right", {"rows": [{"jk": 1, "value": 2, "name": "R", "_wfid": "wf1-y"}]},
        "fpR")
    plan = attr_plan(keys=[{"left_field": "jk", "right_field": "jk"}])
    left.declared_fields = ["_wfid", "jk", "value", "name"]
    right.declared_fields = ["jk", "value", "name", "_wfid"]
    with pytest.raises(DataJoinError) as ei:
        _run(plan, left, right)
    assert ei.value.code == "field_mapping_conflict"
    outs = {c["output"] for c in ei.value.detail["conflicts"]}
    assert {"value", "name"} <= outs             # 同名列冲突可见
    # _wfid 是自动身份列：默认投影不含它（右身份不混入输出；显式映射仍拒绝）
    # 显式别名后可执行；_wfid 恒为左身份（右不可覆盖）
    plan2 = attr_plan(
        keys=[{"left_field": "jk", "right_field": "jk"}],
        field_map=[
            {"side": "left", "field": "value", "alias": "left_value"},
            {"side": "left", "field": "name", "alias": "left_name"},
            {"side": "right", "field": "value", "alias": "right_value"},
            {"side": "right", "field": "name", "alias": "right_name"},
        ])
    out, _, _ = _run(plan2, left, right)
    assert len(out) == 1
    assert out[0]["_wfid"] == "wf1-x"
    assert out[0]["right_value"] == 2
    assert out[0]["right_name"] == "R"


def test_j14_empty_and_all_unmatched_contracts():
    # 空右：left 语义 → 全部 unmatched（非失败）；inner → 空结果（计数可对账）
    left = loaded_from_rows(
        "left", {"rows": [{"adcode": "110101"}, {"adcode": "110102"}]}, "fpL")
    right = loaded_from_rows("right", {"rows": []}, "fpR")
    plan14 = attr_plan(field_map=[{"side": "left", "field": "adcode"}])
    out, stats, _ = _run(plan14, left, right)
    assert stats.output_rows == 2
    assert all(r["_join_status"] == "unmatched" for r in out)
    out2, stats2, _ = _run(attr_plan(keep_side="inner",
                                     field_map=[{"side": "left", "field": "adcode"}]),
                           left, right)
    assert stats2.output_rows == 0 and out2 == []
    # 空左：契约化空结果
    left_empty = loaded_from_rows("left", {"rows": []}, "fpL")
    out3, stats3, _ = _run(plan14, left_empty, right)
    assert stats3.output_rows == 0


def test_j27_aggregate_null_semantics():
    left = loaded_from_rows(
        "left", {"rows": [{"code": "A"}, {"code": "B"}]}, "fpL")
    right = loaded_from_rows(
        "right",
        {"rows": [
            {"code": "A", "v": 1}, {"code": "A", "v": None}, {"code": "A", "v": 3},
            {"code": "B", "v": None}, {"code": "B", "v": None},
        ]}, "fpR")
    plan = attr_plan(
        keys=[{"left_field": "code", "right_field": "code"}],
        output_cardinality="many_to_one",
        field_map=[{"side": "left", "field": "code"}],
        aggregates=[{"function": "count"},
                    {"function": "sum", "right_field": "v"},
                    {"function": "mean", "right_field": "v"}])
    out, stats, _ = _run(plan, left, right)
    by = {r["code"]: r for r in out}
    # A：count=3；sum=1+3=4（null 不当 0）；mean=2（分母=valid=2）；B：全 null 组
    assert by["A"]["match_count"] == 3
    assert by["A"]["v_sum"] == 4
    assert by["A"]["v_mean"] == 2
    assert by["A"]["v_sum_valid_count"] == 2
    assert by["B"]["v_sum"] is None
    assert by["B"]["v_mean"] is None
    assert by["B"]["v_sum_valid_count"] == 0
    assert by["B"]["match_count"] == 2
    # 右键重复 + 聚合声明 → 不阻断（matched_count 披露）
    assert stats.matched_pairs == 5


def test_p3_internal_geometry_channel_is_reserved():
    """源字段 __geometry__ 不得静默覆盖内部几何通道（应 typed 冲突）。"""
    left = loaded_from_rows(
        "left",
        {"rows": [{"jk": 1, "__geometry__": {"type": "Point",
                                            "coordinates": [0, 0]}}]},
        "fpL")
    right = loaded_from_rows("right", {"rows": [{"jk": 1, "v": 2}]}, "fpR")
    plan = attr_plan(
        keys=[{"left_field": "jk", "right_field": "jk"}],
        field_map=[{"side": "left", "field": "__geometry__"}])
    with pytest.raises(DataJoinError) as ei:
        _run(plan, left, right)
    assert ei.value.code == "field_mapping_conflict"
    outs = {c["output"] for c in ei.value.detail["conflicts"]}
    assert "__geometry__" in outs


def test_attribute_matches_federation_kernel_semantics():
    """差分兼容：单键 + inner + 无聚合 == federation attribute_join_local。

    覆盖：null 双侧排除、左序稳定、右多命中按右输入序展开（one_to_many）。
    """
    pytest.importorskip("geopandas")  # federation 内核依赖
    from app.services.data_fabric.query.federation import attribute_join_local

    left_rows = [
        {"k": "a", "lv": 1}, {"k": None, "lv": 2}, {"k": "b", "lv": 3},
        {"k": "a", "lv": 4},
    ]
    right_rows = [
        {"k": "a", "rv": 10}, {"k": "b", "rv": 20}, {"k": None, "rv": 30},
        {"k": "a", "rv": 11},
    ]
    left = loaded_from_rows("left", {"rows": left_rows}, "fpL")
    right = loaded_from_rows("right", {"rows": right_rows}, "fpR")
    plan = attr_plan(
        keys=[{"left_field": "k", "right_field": "k"}],
        keep_side="inner", output_cardinality="one_to_many",
        field_map=[{"side": "left", "field": "k"},
                   {"side": "left", "field": "lv"},
                   {"side": "right", "field": "rv"}])
    out, _, _ = _run(plan, left, right)

    fed = attribute_join_local(
        left_rows, right_rows, join_field_left="k", join_field_right="k",
        budget=None, max_output=1000,
    )
    # federation 输出：左属性 + __right__ 嵌套；同序（左序、右输入序）
    fed_pairs = [(r["lv"], r["__right__"]["rv"]) for r in fed]
    ours = [(r["lv"], r["rv"]) for r in out]
    assert ours == fed_pairs
    assert ours == [(1, 10), (1, 11), (3, 20), (4, 10), (4, 11)]
