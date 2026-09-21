"""CalibrationStore 测试（ADR-0213 D4：actual 回填 + 有界校准统计）。"""
import pytest

from app.services.governor.calibration import (
    MIN_SAMPLES_FOR_SUGGESTION,
    RING_SAMPLES,
    CalibrationStore,
    get_calibration_store,
    reset_calibration_store_for_tests,
    suggest_priors,
)
from app.services.governor.contract import (
    Dimension,
    DimValue,
    ResourceEstimate,
    ResourceUsage,
    Subsystem,
)


def _store() -> CalibrationStore:
    return CalibrationStore()


def test_record_and_stats_ratio():
    s = _store()
    for actual in (8.0, 12.0, 40.0):
        s.record("tool_dispatch:foo", Dimension.WALL_TIME_S, 10.0, actual)
    st = s.stats("tool_dispatch:foo")["tool_dispatch:foo"]["wall_time_s"]
    assert st["n"] == 3
    assert st["mean_ratio"] == pytest.approx(2.0)
    assert st["max_ratio"] == pytest.approx(4.0)


def test_invalid_samples_dropped():
    s = _store()
    s.record("k", Dimension.WALL_TIME_S, 0.0, 5.0)     # expected<=0
    s.record("k", Dimension.WALL_TIME_S, 10.0, -1.0)   # 负 actual
    s.record("k", Dimension.WALL_TIME_S, None, 5.0)
    s.record("", Dimension.WALL_TIME_S, 10.0, 5.0)     # 空 key
    assert s.stats() == {}


def test_bounded_ring_per_key():
    s = _store()
    for i in range(RING_SAMPLES + 50):
        s.record("k", Dimension.WALL_TIME_S, 10.0, float(i))
    st = s.stats("k")["k"]["wall_time_s"]
    assert st["n"] == RING_SAMPLES
    # 环形覆盖：最老样本被挤出（最后 50 个 actual ∈ [64,113]）
    assert st["max_ratio"] == pytest.approx(113 / 10)


def test_bounded_keys():
    s = CalibrationStore(max_keys=4)
    for i in range(10):
        s.record(f"key{i}", Dimension.WALL_TIME_S, 10.0, 10.0)
    assert s.key_count() == 4
    # 已有键仍可继续滚动
    s.record("key0", Dimension.WALL_TIME_S, 10.0, 20.0)
    assert s.stats("key0")["key0"]["wall_time_s"]["mean_ratio"] == pytest.approx(1.5)


def test_record_usage_uses_adjudged_floor_for_unknown():
    """estimate 的 unknown 维按地板记 expected（与准入同口径，不记 0）。"""
    est = ResourceEstimate(
        subsystem=Subsystem.TOOL_DISPATCH,
        dims={
            Dimension.WALL_TIME_S: DimValue.estimated(1.0, 2.0, 4.0,
                                                      confidence=0.5),
            Dimension.GPU_MEMORY_BYTES: DimValue.unknown("vram undeclared"),
        },
    )
    usage = ResourceUsage(
        subsystem=Subsystem.TOOL_DISPATCH,
        dims={Dimension.GPU_MEMORY_BYTES: 2 * 1024**3},
        wall_time_s=3.0,
    )
    s = _store()
    n = s.record_usage("tool_dispatch:bar", usage, est)
    assert n == 2
    wall = s.stats("tool_dispatch:bar")["tool_dispatch:bar"]["wall_time_s"]
    assert wall["mean_ratio"] == pytest.approx(1.5)
    gpu = s.stats("tool_dispatch:bar")["tool_dispatch:bar"]["gpu_memory_bytes"]
    assert gpu["mean_ratio"] == pytest.approx(2.0)   # 2GiB / 1GiB 地板


def test_snapshot_restore_roundtrip():
    s = _store()
    s.record("k", Dimension.WALL_TIME_S, 10.0, 30.0)
    snap = s.snapshot()
    s2 = _store()
    s2.restore(snap)
    assert s2.stats("k") == s.stats("k")
    assert s2.snapshot() == snap


def test_suggest_priors_flags_drift_and_skips_in_band():
    snap = {
        "keys": {
            "tool_dispatch:drifty": {
                "wall_time_s": {"samples": [[10.0, 40.0]] * 12},
            },
            "tool_dispatch:calm": {
                "wall_time_s": {"samples": [[10.0, 11.0]] * 12},
            },
            "tool_dispatch:thin": {
                "wall_time_s": {"samples": [[10.0, 40.0]] * 3},
            },
            "bogus_dim": {"not_a_dim": {"samples": [[1.0, 9.0]] * 12}},
        },
    }
    report = suggest_priors(snap)
    keys = {(s_["tool_key"], s_["dimension"]) for s_ in report["suggestions"]}
    assert ("tool_dispatch:drifty", "wall_time_s") in keys
    assert ("tool_dispatch:calm", "wall_time_s") not in keys
    assert ("tool_dispatch:thin", "wall_time_s") not in keys   # 样本不足
    assert all(s_["dimension"] != "not_a_dim"
               for s_ in report["suggestions"])                 # 非法维跳过
    drifty = next(s_ for s_ in report["suggestions"]
                  if s_["tool_key"] == "tool_dispatch:drifty")
    assert drifty["action"] == "raise_prior"
    assert drifty["n"] >= MIN_SAMPLES_FOR_SUGGESTION
    # 建议文件绝不携带"已应用"语义
    assert "explicit edit" in report["note"]


def test_process_singleton_reset():
    store = CalibrationStore()
    reset_calibration_store_for_tests(store)
    assert get_calibration_store() is store
    reset_calibration_store_for_tests()
    assert get_calibration_store() is not store
    assert get_calibration_store().key_count() == 0
