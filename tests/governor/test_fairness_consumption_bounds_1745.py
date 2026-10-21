"""#1745（3）：FairScheduler 会话消费表的有界性。

``_consumption`` 是 governor 里唯一的 per-session 映射，且此前只有
get/set（``complete`` / ``_admit`` / ``consumption_of``）—— 没有上界、
没有驱逐路径。长命进程下它随历史会话数单调增长（每个曾排队过的 session
都留一条）。

修复：写入统一走 ``_record_consumption``，条目数超过
``_MAX_CONSUMPTION_ENTRIES`` 时驱逐**当前值最小**的会话（消费已衰减到
近零的历史会话公平基准最弱），当前写入的会话永不驱逐。
"""
from __future__ import annotations

from app.services.governor.fairness import (
    _MAX_CONSUMPTION_ENTRIES,
    FairScheduler,
    FairWaiter,
)


def _waiter(sid: str, est: float = 1.0) -> FairWaiter:
    return FairWaiter(session_id=sid, est_cost=est)


class TestConsumptionTableBounds:
    def test_table_stays_under_cap_across_many_sessions(self):
        """远超上界的历史会话逐个 admit → 表大小封顶（不单调增长）。"""
        sched = FairScheduler(4)
        total = _MAX_CONSUMPTION_ENTRIES + 500
        for i in range(total):
            sched._admit(_waiter(f"s{i}"))  # noqa: SLF001 - 表形状即契约
            sched.complete(session_id=f"s{i}", actual_cost=1.0)
        assert len(sched._consumption) <= _MAX_CONSUMPTION_ENTRIES  # noqa: SLF001

    def test_current_session_never_evicted(self):
        """驱逐只挑别人：刚写入的会话必须留在表里（否则公平基准丢失）。"""
        sched = FairScheduler(4)
        for i in range(_MAX_CONSUMPTION_ENTRIES + 10):
            sched._admit(_waiter(f"s{i}"))  # noqa: SLF001
        assert "s4095" in sched._consumption  # noqa: SLF001
        assert len(sched._consumption) <= _MAX_CONSUMPTION_ENTRIES  # noqa: SLF001

    def test_lowest_consumption_evicted_first(self):
        """驱逐挑当前值最小者：高消费（仍需公平惩罚）的会话留下。"""
        sched = FairScheduler(4)
        # 先灌满到上限：一个高消费会话 + 一批近零消费会话。
        sched._record_consumption("heavy", 1000.0)  # noqa: SLF001
        for i in range(_MAX_CONSUMPTION_ENTRIES - 1):
            sched._record_consumption(f"z{i}", float(i))  # noqa: SLF001
        # 再来一个新会话触发驱逐。
        sched._record_consumption("newcomer", 5.0)  # noqa: SLF001
        assert len(sched._consumption) == _MAX_CONSUMPTION_ENTRIES  # noqa: SLF001
        assert "heavy" in sched._consumption  # noqa: SLF001
        assert "newcomer" in sched._consumption  # noqa: SLF001
        assert "z0" not in sched._consumption  # noqa: SLF001 - 最小值先走

    def test_consumption_semantics_unchanged_under_cap(self):
        """上限之内的常见路径：记账/衰减/钳顶语义与修复前逐字一致。"""
        sched = FairScheduler(4)
        sched._admit(_waiter("a", est=3.0))
        assert sched.consumption_of("a") == 3.0
        sched.complete(session_id="a", actual_cost=5.0)
        # (3 + 5) × 0.5 衰减
        assert sched.consumption_of("a") == 4.0
        sched.complete(session_id="a", actual_cost=0.0)
        assert sched.consumption_of("a") == 2.0

    def test_cap_value_is_positive_and_bounded(self):
        assert 0 < _MAX_CONSUMPTION_ENTRIES <= 100_000
