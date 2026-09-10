"""V8 引擎回退熔断（ADR-0130 Phase G，收口 ADR-0120 R2-Mi-4）。

覆盖：
- 阈值开闸：连续 V6 崩溃 ≥3 → OPEN，后续请求跳过 V6（V5 直达 + 披露）；
- half-open 单 trial：cool_down 后一次 V6 尝试，成功回 CLOSED / 失败重开；
- 成功归零；
- execute_chain_v6 集成：crash → V5 回退 + engine_breaker 披露段；open →
  V6 栈完全不进入。
"""
from typing import Any, Dict, List

import pytest

from app.services.data_fabric.fabric.engine_breaker import (
    EngineFallbackBreaker,
    get_engine_breaker,
    reset_engine_breaker,
)
from app.services.data_fabric.query.federation import (
    ChainJoin,
    ChainSource,
    FederatedChainRequest,
    FederatedExecutor,
)


class _FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


# ── 熔断状态机 ────────────────────────────────────────────────────────


def test_opens_after_threshold_and_blocks_v6():
    clock = _FakeClock()
    b = EngineFallbackBreaker(failure_threshold=3, cool_down_s=60.0, clock=clock)
    assert b.allow_v6() is True
    for i in range(2):
        assert b.allow_v6() is True
        b.record_v6_crash(RuntimeError(f"crash{i}"))
    assert b.state() == "closed"
    assert b.allow_v6() is True
    b.record_v6_crash(RuntimeError("crash3"))
    assert b.state() == "open"
    assert b.allow_v6() is False
    d = b.disclosure()
    assert d["consecutive_failures"] == 3 and d["total_fallbacks"] == 3


def test_half_open_single_trial_after_cooldown():
    clock = _FakeClock()
    b = EngineFallbackBreaker(failure_threshold=2, cool_down_s=60.0, clock=clock)
    b.record_v6_crash(RuntimeError("x"))
    b.record_v6_crash(RuntimeError("x"))
    assert b.allow_v6() is False
    clock.t += 61.0
    # half-open：第一个请求获得 trial，其余并发请求继续 V5。
    assert b.allow_v6() is True
    assert b.allow_v6() is False
    # trial 失败 → 重开。
    b.record_v6_crash(RuntimeError("still broken"))
    assert b.state() == "open"
    assert b.allow_v6() is False


def test_success_resets_breaker():
    clock = _FakeClock()
    b = EngineFallbackBreaker(failure_threshold=2, cool_down_s=60.0, clock=clock)
    b.record_v6_crash(RuntimeError("x"))
    b.record_v6_success()
    assert b.disclosure()["consecutive_failures"] == 0
    assert b.state() == "closed"
    # half-open trial 成功同样回 CLOSED。
    b.record_v6_crash(RuntimeError("x"))
    b.record_v6_crash(RuntimeError("x"))
    clock.t += 61.0
    assert b.allow_v6() is True
    b.record_v6_success()
    assert b.state() == "closed"


def test_singleton_and_reset():
    reset_engine_breaker()
    b1 = get_engine_breaker()
    b1.record_v6_crash(RuntimeError("x"))
    reset_engine_breaker()
    b2 = get_engine_breaker()
    assert b2 is not b1
    assert b2.disclosure()["consecutive_failures"] == 0


# ── execute_chain_v6 集成 ─────────────────────────────────────────────


class _FakeAdapter:
    """最小 2 源 attribute join 的 fake adapter（离线）。

    ``crash=True`` 只崩**第一次** query（模拟仅 V6 栈炸：V6 执行期崩溃 →
    V5 回退可正常取数）。
    """

    def __init__(self, rows: List[Dict[str, Any]], crash: bool = False):
        self._rows = rows
        self._crash = crash
        self._calls = 0

    def query(self, dataset_id: str, spec) -> Any:
        from app.schemas.data_fabric_schema import QueryResult

        self._calls += 1
        if self._crash and self._calls == 1:
            raise RuntimeError("v6 engine stack blowup (simulated)")
        return QueryResult(
            dataset_id=dataset_id,
            features=[
                {"type": "Feature", "geometry": None, "properties": r}
                for r in self._rows
            ],
            total_count=len(self._rows),
            schema_info={"fields": [{"name": "k", "type": "string"}]},
        )


def _req(engine="v6") -> FederatedChainRequest:
    return FederatedChainRequest(
        sources=[
            ChainSource(source_id="s0", dataset_id="d0", estimated_rows=2),
            ChainSource(source_id="s1", dataset_id="d1", estimated_rows=2),
        ],
        joins=[ChainJoin(kind="attribute_join", join_field_left="k",
                         join_field_right="k", left_source_id="s0",
                         right_source_id="s1")],
        engine=engine,
        session_owner="sess-brk",
        use_cache=False,
    )


@pytest.fixture()
def _clean_breaker():
    reset_engine_breaker()
    yield
    reset_engine_breaker()


def _executor(adapters: Dict[str, _FakeAdapter]) -> FederatedExecutor:
    return FederatedExecutor(lambda sid: adapters.get(sid))


def test_crash_falls_back_and_discloses_breaker(_clean_breaker):
    # 打爆真实 V6 栈：用一个会抛非 typed 异常的 adapter（V5 回退可正常取数）。
    exploding = _executor({
        "s0": _FakeAdapter([], crash=True),
        "s1": _FakeAdapter([]),
    })
    req = _req()
    result = exploding.execute_chain(req)
    assert result["engine"] == "v5_fallback"
    assert any("engine=v6 failed" in w for w in result.get("warnings") or [])
    assert result["engine_breaker"]["consecutive_failures"] == 1


def test_open_breaker_skips_v6_stack(_clean_breaker, monkeypatch):
    from app.services.data_fabric.query import federation as fed

    breaker = get_engine_breaker()
    for _ in range(10):
        breaker.record_v6_crash(RuntimeError("sustained failure"))
    assert breaker.state() == "open"

    # 若 V6 栈被进入就会炸 —— 证明 open 路径完全跳过 V6。
    def _boom(req):
        raise AssertionError("V6 stack must not be entered while breaker is open")

    monkeypatch.setattr(fed, "plan_federation_v6", _boom, raising=False)
    executor = _executor({
        "s0": _FakeAdapter([{"k": "a"}]),
        "s1": _FakeAdapter([{"k": "a"}]),
    })
    result = executor.execute_chain(_req())
    assert result["status"] == "success"
    assert result["engine"] == "v5_fallback"
    assert any("fallback breaker open" in w for w in result.get("warnings") or [])
    assert result["engine_breaker"]["state"] == "open"


def test_v6_success_resets_consecutive_failures(_clean_breaker):
    executor = _executor({
        "s0": _FakeAdapter([{"k": "a"}]),
        "s1": _FakeAdapter([{"k": "a"}]),
    })
    breaker = get_engine_breaker()
    breaker.record_v6_crash(RuntimeError("x"))
    breaker.record_v6_crash(RuntimeError("x"))
    result = executor.execute_chain(_req())
    assert result["engine"] == "v6"
    assert breaker.disclosure()["consecutive_failures"] == 0


# ── Subagent-B review 回归（P1-1：half-open trial 泄漏）────────────────


def test_typed_error_exit_releases_half_open_trial(_clean_breaker, monkeypatch):
    import app.services.data_fabric.fabric.engine_breaker as _eb_mod

    def _patched_local_breaker():
        clock = _FakeClock()
        b = EngineFallbackBreaker(failure_threshold=1, cool_down_s=60.0, clock=clock)
        b.record_v6_crash(RuntimeError("x"))
        clock.t += 61.0
        assert b.state() == "half_open"  # 已过冷却（不消耗 trial 名额）
        monkeypatch.setattr(_eb_mod, "get_engine_breaker", lambda: b)
        return b

    b = _patched_local_breaker()
    """trial 被一个从 typed 错误路径退出的请求消费后，名额必须归还 ——
    否则 HALF_OPEN 卡死，V6 被禁用到进程重启。"""
    from app.services.data_fabric.errors import SourceUnreachableError

    class _TypedFailAdapter:
        def query(self, dataset_id: str, spec):
            raise SourceUnreachableError("typed remote failure")

    executor = _executor({"s0": _TypedFailAdapter(), "s1": _TypedFailAdapter()})
    with pytest.raises(Exception):
        executor.execute_chain(_req())
    # 名额归还：下一个请求仍可获得 trial（而非永久 V5）。
    assert b.allow_v6() is True


def test_cache_enabled_typed_error_exit_releases_trial(_clean_breaker, monkeypatch):
    import app.services.data_fabric.fabric.engine_breaker as _eb_mod

    def _patched_local_breaker():
        clock = _FakeClock()
        b = EngineFallbackBreaker(failure_threshold=1, cool_down_s=60.0, clock=clock)
        b.record_v6_crash(RuntimeError("x"))
        clock.t += 61.0
        assert b.state() == "half_open"  # 已过冷却（不消耗 trial 名额）
        monkeypatch.setattr(_eb_mod, "get_engine_breaker", lambda: b)
        return b

    b = _patched_local_breaker()
    """review 场景原样复现：breaker open → 冷却 → 首个请求 typed 失败
    （负缓存落账路径）→ trial 不卡死。"""
    from app.schemas.data_fabric_schema import QueryResult  # noqa: F401
    from app.services.data_fabric.errors import SourceUnreachableError

    class _TypedFailAdapter:
        def query(self, dataset_id: str, spec):
            raise SourceUnreachableError("typed remote failure")

    executor = _executor({"s0": _TypedFailAdapter(), "s1": _TypedFailAdapter()})
    with pytest.raises(Exception):
        executor.execute_chain(_req())
    assert b.allow_v6() is True
    # 且崩溃已被记账：再次失败会重开。
    b.record_v6_crash(RuntimeError("still failing"))
    assert b.state() == "open"
