"""V6 Bloom 半连接契约测试（ADR-0118 W5）：无假阴性/确定性/饱和降级/盈利阈值。"""

from app.services.data_fabric.query.federated.bloom import (
    BloomFilter,
    build_bloom_from_rows,
    semi_join_plan,
)


# ── Bloom 基本语义 ──────────────────────────────────────────────────────────


def test_no_false_negatives_across_types():
    bloom = BloomFilter.with_capacity(expected_keys=1000)
    keys = [1, 2.0, "road", "桥", None, 99_999]
    for k in keys:
        bloom.add(k)
    for k in keys:
        assert k in bloom, f"假阴性：{k!r} 必须命中"


def test_int_float_key_equivalence():
    bloom = BloomFilter.with_capacity(expected_keys=100)
    bloom.add(1)
    assert 1.0 in bloom
    bloom2 = BloomFilter.with_capacity(expected_keys=100)
    bloom2.add(2.0)
    assert 2 in bloom2


def test_false_positive_rate_bounded():
    bloom = BloomFilter.with_capacity(expected_keys=1000, fp_rate=0.01)
    for i in range(1000):
        bloom.add(i)
    fp = sum(1 for i in range(1000, 3000) if i in bloom)
    assert fp / 2000 < 0.05, f"假阳性率 {fp / 2000:.3f} 超出诚实上界"


def test_deterministic_bits():
    b1 = BloomFilter.with_capacity(expected_keys=100)
    b2 = BloomFilter.with_capacity(expected_keys=100)
    for i in range(100):
        b1.add(f"k{i}")
        b2.add(f"k{i}")
    assert b1.bits == b2.bits


def test_saturation_flag_and_degrade():
    bloom = BloomFilter.with_capacity(expected_keys=10, max_bits=64)
    for i in range(500):
        bloom.add(i)
    assert bloom.saturated


# ── 从链式累积行构建 ────────────────────────────────────────────────────────


def test_build_from_chain_rows():
    rows = [
        {"region": "A", "v": 1},
        {"region": "B", "v": 2},
        {"v": 3},  # 键缺失 → 跳过
        {"properties": {"region": "C"}},  # 首跳源特性形状
    ]
    bloom, stats = build_bloom_from_rows(rows, "region")
    assert bloom is not None
    assert stats["keys_added"] == 3
    assert "A" in bloom and "C" in bloom


def test_build_from_rows_all_none_returns_none():
    rows = [{"v": 1}, {"properties": {"v": 2}}]
    bloom, stats = build_bloom_from_rows(rows, "region")
    assert bloom is None
    assert stats["keys_added"] == 0


# ── 盈利阈值 ────────────────────────────────────────────────────────────────


def test_profitable_case_enabled():
    plan = semi_join_plan(
        left_card=10_000, right_card=100_000, ndv_left=100, ndv_right=10_000
    )
    assert plan.enabled
    assert plan.estimated_reduction_ratio > 0.9


def test_disabled_without_ndv():
    plan = semi_join_plan(left_card=100, right_card=100, ndv_left=None, ndv_right=None)
    assert not plan.enabled
    assert "ndv" in plan.reason


def test_disabled_when_reduction_marginal():
    plan = semi_join_plan(left_card=100, right_card=100, ndv_left=90, ndv_right=100)
    assert not plan.enabled


def test_disabled_for_spatial_join_shape():
    plan = semi_join_plan(
        left_card=10, right_card=10_000, ndv_left=5, ndv_right=5_000, applicable=False
    )
    assert not plan.enabled
    assert "not applicable" in plan.reason


def test_empty_left_disabled():
    plan = semi_join_plan(left_card=0, right_card=1000, ndv_left=10, ndv_right=100)
    assert not plan.enabled
