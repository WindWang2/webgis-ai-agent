"""G38 独立评审修复回归（Round 1 findings F2/F3/F5/F6/F7/F8/F13/F14）。"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("db_tables")

from app.services.data_join_authoring import outputs, service
from app.services.data_join_authoring.errors import DataJoinError
from app.services.data_join_authoring.executor import JoinExecutor
from app.services.data_join_authoring.plan import parse_plan
from tests.unit.data_join_authoring.fixtures import (
    attr_plan,
    loaded_from_fc,
    loaded_from_rows,
    points_fc,
    regions_fc,
)


def test_f2_passthrough_rows_count_against_output_cap():
    """F2：null_key/unmatched 透传行也计入输出预算（不再只查匹配分支）。"""
    left = loaded_from_rows(
        "left", {"rows": [{"adcode": f"no{i}"} for i in range(50)]}, "fpL")
    right = loaded_from_rows("right", {"rows": [{"adcode": "x", "v": 1}]}, "fpR")
    plan = parse_plan(attr_plan(
        keys=[{"left_field": "adcode", "right_field": "adcode"}],
        field_map=[{"side": "left", "field": "adcode"}],
        budgets={"max_output_rows": 10},
    ))
    ex = JoinExecutor(plan)
    with pytest.raises(DataJoinError) as ei:
        ex.run_attribute(left, right)
    assert ei.value.code == "budget_exceeded"
    assert ei.value.detail["output_rows"] > 10   # 超帽即停（透传行触发）


async def test_f3_data_object_publishes_as_published(seeded_sources):
    """F3：data_join kind 白名单修复 —— 收据 data_object.state == published。"""
    seed = seeded_sources
    view = await service.create_plan(seed["session_id"], attr_plan(
        left={"kind": "session_ref", "session_id": seed["session_id"],
              "ref": seed["left_ref"]},
        right={"kind": "table_rows", "session_id": seed["session_id"],
               "ref": seed["right_ref"]},
    ), actor=seed["actor"])
    run = await service.execute_plan(seed["session_id"], view["plan_id"],
                                     expected_revision=1,
                                     actor=seed["actor"], background=False)
    assert run["status"] == "completed"
    assert run["receipt"]["data_object"]["state"] == "published"
    assert run["receipt"]["data_object"]["data_object_id"]


async def test_f5_convergence_across_generations(seeded_sources):
    """F5：失效 → g1 完成后，第三次执行必须收敛到 g1 run（非无限新 run）。"""
    seed = seeded_sources
    plan_data = attr_plan(
        left={"kind": "session_ref", "session_id": seed["session_id"],
              "ref": seed["left_ref"]},
        right={"kind": "table_rows", "session_id": seed["session_id"],
               "ref": seed["right_ref"]},
    )
    view = await service.create_plan(seed["session_id"], plan_data,
                                     actor=seed["actor"])
    run1 = await service.execute_plan(seed["session_id"], view["plan_id"],
                                      expected_revision=1,
                                      actor=seed["actor"], background=False)
    assert run1["status"] == "completed"
    # 内容删除 → 失效（J28）
    outputs.discard_final(run1["run_id"])
    await service.inspect_run(seed["session_id"], run1["run_id"])
    # g1 执行完成
    run2 = await service.execute_plan(seed["session_id"], view["plan_id"],
                                      expected_revision=1,
                                      actor=seed["actor"], background=False)
    assert run2["status"] == "completed"
    assert run2["run_id"] != run1["run_id"]
    # 第三次：必须复用 g1（跨世代收敛）
    run3 = await service.execute_plan(seed["session_id"], view["plan_id"],
                                      expected_revision=1,
                                      actor=seed["actor"], background=False)
    assert run3["run_id"] == run2["run_id"]
    assert run3.get("idempotent_reuse") is True


def test_f6_spatial_unmatched_right_computed():
    """F6：空间路径右未匹配 = 有效面行 - 被命中面行（不再缺省 0）。"""
    left = loaded_from_fc("left", points_fc(), "fpL")
    # 右侧只保留 R1（R2/R3/R4 全部不被命中）
    rfc = regions_fc()
    rfc["features"] = [f for f in rfc["features"] if f["properties"]["rid"] == "R1"]
    for f in rfc["features"]:
        f["properties"]["priority"] = 1
    right = loaded_from_fc("right", rfc, "fpR")
    plan = parse_plan({
        "mode": "spatial_point_in_polygon",
        "left": {"kind": "session_ref", "session_id": "s", "ref": "r1"},
        "right": {"kind": "session_ref", "session_id": "s", "ref": "r2"},
        "keep_side": "left",
        "spatial": {"predicate": "within", "multi_match": "require_unique"},
        "output_cardinality": "many_to_one",
        "field_map": [{"side": "left", "field": "pid"},
                      {"side": "right", "field": "rid"}],
    })
    ex = JoinExecutor(plan)
    ex.run_spatial(left, right)
    assert ex.stats.unmatched_right_rows == 0   # R1 被命中（P1）
    assert ex.stats.output_rows == 7
    # 再验证未命中面：加一个孤立面 R9（无点命中）→ unmatched_right = 1
    rfc2 = regions_fc()
    rfc2["features"] = [f for f in rfc2["features"] if f["properties"]["rid"] == "R1"]
    rfc2["features"].append({
        "type": "Feature",
        "properties": {"_wfid": "wf1-r9", "rid": "R9", "priority": 1},
        "geometry": {"type": "Polygon", "coordinates": [
            [[100.0, 0.0], [101.0, 0.0], [101.0, 1.0], [100.0, 1.0], [100.0, 0.0]]]},
    })
    right2 = loaded_from_fc("right", rfc2, "fpR")
    ex2 = JoinExecutor(plan)
    ex2.run_spatial(left, right2)
    assert ex2.stats.unmatched_right_rows == 1


def test_f7_aggregate_rows_consistent_schema():
    """F7：聚合计划下未匹配/空键行与匹配行同 schema（match_count=0 + 空聚合列）。"""
    left = loaded_from_rows(
        "left", {"rows": [{"code": "A"}, {"code": "zzz"}]}, "fpL")
    right = loaded_from_rows(
        "right", {"rows": [{"code": "A", "v": 5}]}, "fpR")
    plan = parse_plan(attr_plan(
        keys=[{"left_field": "code", "right_field": "code"}],
        output_cardinality="many_to_one",
        field_map=[{"side": "left", "field": "code"}],
        aggregates=[{"function": "sum", "right_field": "v"}],
    ))
    ex = JoinExecutor(plan)
    out = ex.run_attribute(left, right)
    keys0 = set(out[0].keys())
    for row in out:
        assert set(row.keys()) == keys0
    unmatched = next(r for r in out if r["code"] == "zzz")
    assert unmatched["match_count"] == 0
    assert unmatched["v_sum"] is None
    assert unmatched["v_sum_valid_count"] == 0


def test_f8_count_col_counts_non_null():
    """F8：显式字段 count = COUNT(col)（非空）；无字段 = COUNT(*)。"""
    from app.services.data_join_authoring.executor import _agg_result

    values = [None, 2, None, 4]
    star, valid = _agg_result(values, "count")
    assert star == 4 and valid == 2
    col, valid2 = _agg_result(values, "count", count_field=True)
    assert col == 2 and valid2 == 2


async def test_f13_table_rows_anchor_rejects_fc_payload(seeded_sources):
    """F13：table_rows 锚指向 FC 载荷 → typed 拒绝（不静默当几何源）。"""
    seed = seeded_sources
    # 对照：同一 FC ref 经 session_ref 锚 → 正常几何源
    res = await service.describe_source(
        seed["session_id"],
        {"kind": "session_ref", "session_id": seed["session_id"],
         "ref": seed["left_ref"]},
        seed["actor"])
    assert res["source"]["is_table"] is False
    from app.services.data_join_authoring.errors import DataJoinError as DJE

    # table_rows 锚指向 FC 载荷 → 受信回退按 rows 形态校验拒绝
    with pytest.raises(DJE) as ei:
        await service.describe_source(
            seed["session_id"],
            {"kind": "table_rows", "session_id": seed["session_id"],
             "table_ref": seed["left_ref"]},
            seed["actor"])
    assert ei.value.code == "source_unresolvable"
    assert "rows" in ei.value.message


def test_r1_unaliased_count_with_field_is_independent_column():
    """R1：显式字段 count(col)（缺省别名）是独立 COUNT(col) 列，
    不被系统 match_count（COUNT(*)）吞并。"""
    left = loaded_from_rows("left", {"rows": [{"code": "A"}]}, "fpL")
    right = loaded_from_rows(
        "right", {"rows": [{"code": "A", "v": 1}, {"code": "A", "v": None}]},
        "fpR")
    plan = parse_plan(attr_plan(
        keys=[{"left_field": "code", "right_field": "code"}],
        output_cardinality="many_to_one",
        field_map=[{"side": "left", "field": "code"}],
        aggregates=[{"function": "count", "right_field": "v"}],
    ))
    ex = JoinExecutor(plan)
    out = ex.run_attribute(left, right)
    r = out[0]
    assert r["match_count"] == 2                    # COUNT(*)：两匹配对
    assert r["v_count"] == 1                        # COUNT(col)：非空 1
    assert r["v_count_valid_count"] == 1


async def test_r3_execute_wait_surfaces_inspect_error_not_stale_view(
        seeded_sources, monkeypatch):
    """R3（tools:239）：有界等待中 inspect_run 抛 typed 错误 → 上报该错误，
    不拿 execute 初始 view（pending）冒充非终态结果。"""
    seed = seeded_sources

    async def noop_runner(*args, **kwargs):
        return None

    monkeypatch.setattr(service, "_run_execution_job", noop_runner)

    async def boom(session_id, run_id):
        raise DataJoinError("run_not_found", "运行不存在或不属于当前会话",
                            run_id=run_id)

    monkeypatch.setattr(service, "inspect_run", boom)

    view = await service.create_plan(seed["session_id"], attr_plan(
        left={"kind": "session_ref", "session_id": seed["session_id"],
              "ref": seed["left_ref"]},
        right={"kind": "table_rows", "session_id": seed["session_id"],
               "ref": seed["right_ref"]},
    ), actor=seed["actor"])

    from app.tools.data_join_authoring_tools import (
        register_data_join_authoring_tools,
    )
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    register_data_join_authoring_tools(reg)
    execute = reg._tools["webgis_data_join_execute"]
    res = await execute(plan_id=view["plan_id"],
                        expected_revision=view["revision"],
                        wait_max_s=1, session_id=seed["session_id"])
    assert res["error"] == "run_not_found"
    assert res.get("status") != "pending"      # 不虚报 execute 初始非终态


def test_r2_generated_valid_count_names_in_conflict_scan():
    """R2：用户字段名撞生成分母列 <agg>_valid_count → typed 冲突。"""
    left = loaded_from_rows("left", {"rows": [{"code": "A"}]}, "fpL")
    right = loaded_from_rows(
        "right", {"rows": [{"code": "A", "v": 1}]}, "fpR")
    plan = parse_plan(attr_plan(
        keys=[{"left_field": "code", "right_field": "code"}],
        output_cardinality="many_to_one",
        field_map=[{"side": "left", "field": "code"},
                   # 别名造碰撞：输出名 = 聚合生成分母列 v_sum_valid_count
                   {"side": "right", "field": "v",
                    "alias": "v_sum_valid_count"}],
        aggregates=[{"function": "sum", "right_field": "v"}],
    ))
    ex = JoinExecutor(plan)
    with pytest.raises(DataJoinError) as ei:
        ex.run_attribute(left, right)
    assert ei.value.code == "field_mapping_conflict"
    assert any(c["output"] == "v_sum_valid_count"
               for c in ei.value.detail["conflicts"])
