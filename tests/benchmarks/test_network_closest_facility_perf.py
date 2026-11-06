"""Keep #600's original 2s cold 8k-chain budget in the required no-cov lane.

The covered unit suite executes the identical real service workload and
checks its routes. Do not warm the snap index or reuse a computed result.
"""
import pytest

from tests.unit.test_network_issue600_need_paths_accumulation import run_closest_facility_8k_chain

pytestmark = pytest.mark.perf


def test_closest_facility_8k_chain_under_acceptance_bound() -> None:
    res, elapsed = run_closest_facility_8k_chain()
    assert len(res.routes) == 1
    assert res.routes[0].total_distance_m == pytest.approx(8000 * 8.3, rel=0.01)
    assert elapsed < 2.0, f"8k-chain closest-facility took {elapsed:.2f}s (CI budget: < 2s)"
