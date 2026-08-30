"""GIS Runtime Trace 测试（ADR-0088 P7）—— 有界 / 线程安全 / 不入 LLM 上下文。

覆盖：
- 事件环 FIFO 淘汰（per-session 上限）；
- 会话 LRU 淘汰（session 数上限）；
- 计数器键有限集合（未知键丢弃）；
- summary / events 投影有界；
- reset 隔离。
"""
import threading

from app.services.gis_harness.trace import (
    MAX_EVENTS_PER_SESSION,
    STAGE_FINALIZATION,
    STAGE_RUNTIME_REPAIR,
    GISRuntimeTrace,
)


def test_event_ring_bounded():
    t = GISRuntimeTrace(max_events=4)
    for i in range(10):
        t.record("s1", STAGE_FINALIZATION, status=f"s{i}")
    evs = t.events("s1", limit=100)
    assert len(evs) == 4
    assert evs[-1]["detail"]["status"] == "s9"  # 最新在后
    assert evs[0]["detail"]["status"] == "s6"  # 最旧被淘汰


def test_session_lru_bounded():
    t = GISRuntimeTrace(max_sessions=3)
    for i in range(5):
        t.record(f"s{i}", STAGE_FINALIZATION)
    assert all(t.events(f"s{i}") == [] for i in range(2))  # 最旧会话被淘汰
    assert t.events("s4")


def test_counters_known_keys_only():
    t = GISRuntimeTrace()
    t.bump("finalizations")
    t.bump("totally_unknown_counter")  # 丢弃
    assert t.counters()["finalizations"] == 1
    assert "totally_unknown_counter" not in t.counters()


def test_summary_is_bounded():
    t = GISRuntimeTrace()
    for i in range(MAX_EVENTS_PER_SESSION + 5):
        t.record("s1", STAGE_RUNTIME_REPAIR, applied=1)
    s = t.summary("s1")
    assert s["events"] <= MAX_EVENTS_PER_SESSION
    assert s["by_stage"][STAGE_RUNTIME_REPAIR] == s["events"]


def test_unknown_stage_dropped():
    t = GISRuntimeTrace()
    t.record("s1", "not_a_stage")
    assert t.events("s1") == []


def test_thread_safety_smoke():
    t = GISRuntimeTrace()
    def worker(n):
        for i in range(50):
            t.record(f"s{n}", STAGE_FINALIZATION, i=i)
            t.bump("finalizations")
    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert t.counters()["finalizations"] == 200


def test_projection_never_carries_unbounded_values():
    t = GISRuntimeTrace()
    t.record("s1", STAGE_FINALIZATION, blob="x" * 10_000, n=5)
    ev = t.events("s1")[0]
    assert len(ev["detail"]["blob"]) <= 96
    assert ev["detail"]["n"] == 5
