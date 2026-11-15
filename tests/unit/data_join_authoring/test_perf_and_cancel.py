"""G38 性能曲线 + 取消/运行时预算（J20/J26 + WP12 性能报告数据源）。

规模曲线（受默认预算约束）：属性关联 1k/5k/20k 行（高匹配率）+
点面 1k/1万；运行时候选超估（J26）与取消响应（J20）。
"""
from __future__ import annotations

import time

import pytest

pytestmark = pytest.mark.usefixtures("db_tables")

from app.services.data_join_authoring import outputs, service
from app.services.data_join_authoring.errors import DataJoinError
from app.services.data_join_authoring.executor import JoinExecutor
from app.services.data_join_authoring.plan import parse_plan
from tests.unit.data_join_authoring.fixtures import loaded_from_rows


def _big_table(n: int) -> dict:
    return {"rows": [{"adcode": f"1101{i:06d}", "v": i} for i in range(n)]}


PERF_RESULTS: list = []


@pytest.mark.parametrize("n", [1000, 5000, 20000])
async def test_attribute_perf_curve(n, seeded_sources):
    """高匹配率属性关联：输入/输出规模、耗时（性能报告数据点）。"""
    left = loaded_from_rows("left", _big_table(n), "fpL")
    right = loaded_from_rows("right", _big_table(n), "fpR")
    plan = parse_plan({
        "mode": "attribute",
        "left": {"kind": "session_ref", "session_id": "s", "ref": "r1"},
        "right": {"kind": "table_rows", "session_id": "s", "ref": "r2"},
        "keys": [{"left_field": "adcode", "right_field": "adcode"}],
        "keep_side": "inner",
        "output_cardinality": "one_to_one",
        "field_map": [{"side": "left", "field": "adcode"},
                      {"side": "right", "field": "v"}],
    })
    ex = JoinExecutor(plan)
    t0 = time.monotonic()
    out = ex.run_attribute(left, right)
    dt = time.monotonic() - t0
    assert len(out) == n
    assert ex.stats.matched_pairs == n
    PERF_RESULTS.append((n, dt))
    # 预算内 20k 行必须在分钟级以下完成（预算 deadline 300s 的实证）
    assert dt < 60.0, f"{n} 行属性关联耗时 {dt:.1f}s 超出性能预期"


def test_j26_runtime_pair_cap_stops_early():
    """预估不充分时（如 pair_cap 放宽后运行中累计超限）→ 阶段预算即时
    typed 停止，不 O(n×m) 无界扫描也不静默部分完成。"""
    n = 4000
    left = loaded_from_rows("left", _big_table(n), "fpL")
    right_rows = []
    for j in range(4):                     # 每键 4 个右行 → 1:N 展开
        right_rows.extend({"adcode": f"1101{i:06d}", "v": i * 10 + j}
                          for i in range(n))
    right = loaded_from_rows("right", {"rows": right_rows}, "fpR")
    plan = parse_plan({
        "mode": "attribute",
        "left": {"kind": "session_ref", "session_id": "s", "ref": "r1"},
        "right": {"kind": "table_rows", "session_id": "s", "ref": "r2"},
        "keys": [{"left_field": "adcode", "right_field": "adcode"}],
        "keep_side": "inner",
        "output_cardinality": "one_to_many",
        "field_map": [{"side": "left", "field": "adcode"},
                      {"side": "right", "field": "v"}],
        "budgets": {"max_join_pairs": 5_000_000, "max_output_rows": 100_000},
    })
    # 估算 16k 对 ≤ pair 预算（放行）；输出 16k 行 ≤ 输出预算（放行）——
    # 收紧输出预算到 8k 模拟「运行时超估」：装配中途 typed 停止
    plan = plan.model_copy(update={"budgets": {
        "max_join_pairs": 5_000_000, "max_output_rows": 8_000}})
    ex = JoinExecutor(plan)
    t0 = time.monotonic()
    with pytest.raises(DataJoinError) as ei:
        ex.run_attribute(left, right)
    dt = time.monotonic() - t0
    assert ei.value.code == "budget_exceeded"
    assert dt < 10.0                       # 即时停止（不是扫完全量才报）


async def test_j20_cancel_converges_no_public_artifact(seeded_sources, monkeypatch):
    """执行中取消：DB 取消事实 → 看门狗/ensure → 协作检查点 → run cancelled，
    终态后无公开半成品（无终态包目录）。"""
    import threading

    from app.services.data_join_authoring.executor import JoinExecutor as JE

    gate = threading.Event()

    real_run = JE.run_attribute

    def slow_run(self, left, right):
        gate.wait(timeout=30)              # 模拟执行中（线程池内阻塞）
        return real_run(self, left, right)

    monkeypatch.setattr(JE, "run_attribute", slow_run)

    seed = seeded_sources
    from tests.unit.data_join_authoring.fixtures import attr_plan
    plan_data = attr_plan(
        left={"kind": "session_ref", "session_id": seed["session_id"],
              "ref": seed["left_ref"]},
        right={"kind": "table_rows", "session_id": seed["session_id"],
               "ref": seed["right_ref"]},
    )
    view = await service.create_plan(seed["session_id"], plan_data,
                                     actor=seed["actor"])
    import asyncio

    async def run_exec():
        try:
            await service.execute_plan(
                seed["session_id"], view["plan_id"], expected_revision=1,
                actor=seed["actor"], background=True)
        except Exception:  # noqa: BLE001 — 取消路径的终态由 DB 断言
            pass

    t = asyncio.get_running_loop().create_task(run_exec())
    await asyncio.sleep(1.0)               # job 已认领 running
    runs = await service.list_runs(seed["session_id"])
    assert runs["runs"], "run 行应已创建"
    run_id = runs["runs"][0]["run_id"]
    # 用户取消（持久事实）
    cancel = await service.cancel_run(seed["session_id"], run_id)
    assert cancel["cancel_requested"] is True
    gate.set()                             # 放行执行 → 检查点应观察到取消
    await asyncio.sleep(3.5)               # 看门狗轮询 2.5s + 余量
    final = await service.inspect_run(seed["session_id"], run_id)
    assert final["status"] == "cancelled", f"期望 cancelled，得到 {final['status']}"
    # 无公开半成品：终态包不存在
    assert not outputs.final_dir(run_id).exists()
    await t
