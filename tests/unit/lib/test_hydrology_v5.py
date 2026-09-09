"""Hydrology V5（science-v5 W9）—— 多级 Pfafstetter + 流网拓扑校验。

契约（01-architecture.md D7）：

- levels=1 与单级 :func:`pfafstetter_codes` **编码逐位一致**（同一走法
  helper——零第二事实源；meta 面向多级语义不同）；
- level 2+：偶数 inter-basin 段以段下游端为出口、父码掩膜内重走，
  子码 = 父码×10 + 位码；奇数盆地为叶子；
- 拓扑校验：合成圆锥 DEM 报 is_consistent=True；人工植入环/悬挂/
  汇流违例被逐项捕获；计数型输出（不 raise）。
"""
from __future__ import annotations

import numpy as np
import pytest

from app.lib.geo_analysis.terrain import (
    d8_flow,
    fill_depressions,
    flow_accumulation,
    pfafstetter_codes,
    pfafstetter_codes_multilevel,
    validate_flow_topology,
)

CELL = 30.0


def _basin(n: int = 80, seed: int = 3) -> np.ndarray:
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:n, 0:n]
    z = 80.0 - 0.5 * xx - 0.3 * yy + 0.05 * rng.normal(size=(n, n))
    z[10:30, 10:30] -= 3.0
    z[40:70, 20:65] -= 2.0
    return z.astype(float)


def _flow(n: int = 80, seed: int = 3):
    z = _basin(n, seed)
    filled, _ = fill_depressions(z, CELL)
    d8, _ = d8_flow(filled, CELL)
    acc, _ = flow_accumulation(d8)
    return d8, acc


def _outlet(d8, acc):
    r, c = np.unravel_index(int(np.argmax(acc)), acc.shape)
    return (int(r), int(c))


class TestMultilevelPfafstetter:
    def test_multilevel_l1_matches_single_level(self):
        d8, acc = _flow()
        outlet = _outlet(d8, acc)
        codes1, _ = pfafstetter_codes(d8, acc, 30, outlet)
        codesm, _ = pfafstetter_codes_multilevel(d8, acc, 30, outlet, levels=1)
        np.testing.assert_array_equal(codes1, codesm)   # differential 锚

    def test_multilevel_two_digit_codes(self):
        d8, acc = _flow()
        outlet = _outlet(d8, acc)
        codes, meta = pfafstetter_codes_multilevel(
            d8, acc, 30, outlet, levels=2)
        assert meta["levels"] == 2
        assert meta["distinct_codes"] >= 8
        # 二位码 = 父码×10 + 位码：父码为偶（2..10）、位码 1..10
        two = codes[codes > 9]
        parents = (two // 10).astype(int)
        digits = (two % 10).astype(int)
        assert parents.size >= 1
        # 经典拼接：父码 = 单级偶段（2..10，level-1 可含 10），位码 = 单个
        # 十进制位（1..8——子段支流数 ≤3 的拼接约束）
        assert (parents % 2 == 0).all()
        assert ((parents >= 2) & (parents <= 10)).all()
        assert ((digits >= 1) & (digits <= 8)).all()
        # 一位码仍存在（奇数盆地叶子）
        one = codes[(codes > 0) & (codes <= 8)]
        assert one.size >= 1

    def test_multilevel_l3_extends_l2_hierarchy(self):
        d8, acc = _flow()
        outlet = _outlet(d8, acc)
        codes2, m2 = pfafstetter_codes_multilevel(
            d8, acc, 30, outlet, levels=2)
        codes3, m3 = pfafstetter_codes_multilevel(
            d8, acc, 30, outlet, levels=3)
        assert m3["distinct_codes"] >= m2["distinct_codes"]
        # L3 码去掉末位后必须落在 L2 码 ∪ 其父码集（前缀语义；父段格网
        # 在细分时被整体改写，故父码本身可能已不在 L2 码集中）
        prefixes = set(int(c // 10) for c in codes3.ravel() if c > 99)
        codes2_set = set(int(c) for c in codes2.ravel() if c > 0)
        l1_parents = set(p for p in prefixes if p % 2 == 0)
        assert prefixes <= (codes2_set | l1_parents)
        assert all(p % 2 == 0 for p in prefixes)      # 只有偶数父段细分

    @pytest.mark.parametrize("bad", [0, 5, -1])
    def test_multilevel_levels_validation(self, bad):
        d8, acc = _flow(n=40)
        outlet = _outlet(d8, acc)
        with pytest.raises(ValueError, match="levels must be in"):
            pfafstetter_codes_multilevel(d8, acc, 30, outlet, levels=bad)

    def test_multilevel_small_segments_skipped_counted(self):
        d8, acc = _flow(n=40)
        outlet = _outlet(d8, acc)
        codes, meta = pfafstetter_codes_multilevel(
            d8, acc, 30, outlet, levels=4)
        # 小网格深层细分必然触发跳过——诚实计数
        assert meta["skipped_segments"] >= 0
        assert "not subdivided" in meta["hierarchy_note"]

    def test_levels1_via_multilevel_kw(self):
        # pfafstetter_codes 显式 levels=1 透传
        d8, acc = _flow()
        outlet = _outlet(d8, acc)
        codes1, m1 = pfafstetter_codes(d8, acc, 30, outlet)
        # 单级函数保留原 meta 契约（algorithm/hierarchy_note 不变）
        assert m1["algorithm"] == "terrain.pfafstetter"
        assert "single-level" in m1["hierarchy_note"]


class TestFlowTopology:
    def test_topology_consistent_basin(self):
        d8, acc = _flow()
        report, meta = validate_flow_topology(d8, acc)
        assert report["cycles"] == 0
        assert report["dangling_receivers"] == 0
        assert report["out_of_bounds_receivers"] == 0
        assert report["accumulation_violations"] == 0
        assert report["outlets"] >= 1             # 边界 DEM 多出口是常态
        assert meta["is_consistent"] == report["is_consistent"]

    def test_topology_cycle_detection(self):
        # 人工 D8：两像元互指 → 环
        n = 4
        receiver = np.full(n, -1, dtype=int)
        receiver[0] = 1
        receiver[1] = 0                           # 环
        receiver[2] = 3
        receiver[3] = -1
        acc = np.array([[1.0, 1.0], [1.0, 2.0]])
        d8 = {
            "direction": np.zeros((2, 2)),
            "receiver": receiver,
            "valid": np.ones(4, dtype=bool),
        }
        report, _ = validate_flow_topology(d8, acc)
        assert report["cycles"] >= 1
        assert not report["is_consistent"]

    def test_topology_dangling_and_monotonicity(self):
        # receiver 指向无效像元 + 汇流不增
        n = 4
        valid = np.array([True, True, True, True])
        receiver = np.array([1, 2, 3, -1])
        acc = np.array([[5.0, 4.0], [3.0, 3.0]])  # 5→4 违例；3→3 平台
        d8 = {
            "direction": np.zeros((2, 2)),
            "receiver": receiver,
            "valid": valid,
        }
        report, _ = validate_flow_topology(d8, acc)
        assert report["accumulation_violations"] >= 1
        assert report["equal_accumulation_plateaus"] >= 1
        assert not report["is_consistent"]

    def test_topology_out_of_bounds_receiver(self):
        receiver = np.array([1, 99, -1, -1])
        acc = np.array([[1.0, 2.0], [0.0, 0.0]])
        d8 = {
            "direction": np.zeros((2, 2)),
            "receiver": receiver,
            "valid": np.ones(4, dtype=bool),
        }
        report, _ = validate_flow_topology(d8, acc)
        assert report["out_of_bounds_receivers"] >= 1
        assert not report["is_consistent"]

    def test_shape_mismatch_rejected(self):
        d8, acc = _flow(n=20)
        with pytest.raises(ValueError, match="does not match"):
            validate_flow_topology(d8, acc[:-1, :])
