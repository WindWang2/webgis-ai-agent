"""Regression tests for #441 — Fisher-Jenks prefix-sum rewrite.

``CartographyService._jenks_natural_breaks`` used to recompute the class SSE
``np.sum((arr[i:j+1]-mean)**2)`` from scratch for every (class, j, i) triple
(~2n² fresh numpy calls → ~40-60 s at the n=1000 subsample cap). The fix
derives the SSE in O(1) from cumulative sums and vectorizes the argmin.

These tests pin the two contracts from the issue:

  1. EQUIVALENCE — class boundaries are IDENTICAL to the old implementation on
     the same inputs. The old algorithm is kept below verbatim
     (``_jenks_reference``) and compared across adversarial datasets: random
     floats, duplicates-heavy, constant data, n < k, n == k, the n=1000 DP,
     the n>1000 subsample cap, negative values, and huge-magnitude offsets
     (catastrophic-cancellation bait — the reason the rewrite shifts by
     arr[0] before forming prefix sums).
  2. PERFORMANCE — n=1000 classify (both k=5 and k=7, the cap every large
     layer lands at) finishes with a median wall time well under the issue's
     100 ms target. The bound is deliberately generous (target × 3 = 300 ms
     per classify) so it stays deterministic on slow CI; the OLD code needs
     tens of seconds per classify and fails this gate by >100×.
"""
import statistics
import time

import numpy as np
import pytest

from app.services.cartography_service import CartographyService


# ─── Reference: verbatim copy of the pre-#441 implementation ────────────────


def _jenks_reference(values: np.ndarray, k: int):
    """Old master (4f9030b) implementation — O(n²) numpy calls, kept ONLY for
    equivalence testing. Never call with n > ~1000: it takes ~tens of seconds
    at the cap (that slowness is the bug being tested)."""
    arr = np.sort(values)
    n = len(arr)
    if n <= k:
        # Too few points for k classes — return all unique values as breaks
        uniq = sorted(set(arr.tolist()))
        return uniq if len(uniq) >= 2 else [uniq[0], uniq[0]]
    # Cap sample size for performance (Jenks is O(n²k))
    if n > 1000:
        rng = np.random.default_rng(42)
        arr = np.sort(rng.choice(arr, size=1000, replace=False))

    def ssm(i: int, j: int) -> float:
        s = arr[i : j + 1]
        return float(np.sum((s - s.mean()) ** 2))

    mat = [[float("inf")] * n for _ in range(k + 1)]
    back = [[0] * n for _ in range(k + 1)]

    for j in range(n):
        mat[1][j] = ssm(0, j)

    for c in range(2, k + 1):
        for j in range(c - 1, n):
            best_cost = float("inf")
            best_split = c - 1
            for i in range(c - 1, j + 1):
                cost = mat[c - 1][i - 1] + ssm(i, j)
                if cost < best_cost:
                    best_cost = cost
                    best_split = i
            mat[c][j] = best_cost
            back[c][j] = best_split

    breaks = [float(arr[-1])]
    j = n - 1
    for c in range(k, 1, -1):
        split_idx = back[c][j]
        breaks.append(float(arr[split_idx - 1]))
        j = split_idx - 1
    breaks.append(float(arr[0]))
    breaks.sort()
    return list(dict.fromkeys(breaks))


# ─── Equivalence: boundaries identical to the reference ─────────────────────

_CASES = [
    # (name, values, k)
    ("random-uniform", np.random.default_rng(1).uniform(0, 100, 200), 5),
    ("random-normal", np.random.default_rng(2).normal(50, 12, 200), 5),
    ("random-normal-k7", np.random.default_rng(3).normal(-3, 1.5, 150), 7),
    ("random-sorted-input", np.sort(np.random.default_rng(4).uniform(0, 1, 120)), 4),
    (
        "duplicates-heavy",
        np.random.default_rng(5).choice([1.0, 2.0, 2.0, 2.5, 7.0], size=300),
        4,
    ),
    (
        "duplicates-heavy-k7",
        np.random.default_rng(6).choice([0.0, 0.0, 1.0, 1.0, 1.0, 9.0], size=250),
        7,
    ),
    ("constant-data", np.full(50, 3.14), 5),
    ("two-distinct", [10.0] * 40 + [20.0] * 40, 5),
    ("n-lt-k", [1.0, 5.0, 2.0], 5),
    ("n-eq-k", [4.0, -1.0, 9.0, 0.5, 3.0], 5),
    ("n-just-above-k", np.random.default_rng(7).uniform(0, 50, 6), 5),
    ("negatives", np.random.default_rng(8).normal(-100, 5, 200), 5),
    ("huge-magnitude-1e9", 1e9 + np.random.default_rng(9).uniform(0, 100, 200), 5),
    ("huge-magnitude-1e12", 1e12 + np.random.default_rng(10).uniform(0, 1, 200), 5),
    ("tiny-spread", 1e-6 + np.random.default_rng(11).uniform(0, 1e-7, 150), 5),
]


@pytest.mark.parametrize("name,values,k", _CASES, ids=[c[0] for c in _CASES])
def test_jenks_breaks_identical_to_reference(name, values, k):
    """New prefix-sum implementation must return IDENTICAL breaks (not just
    close): same DP structure, same first-minimum tie-breaking, same values
    pulled from the original (unshifted) array."""
    arr = np.asarray(values, dtype=float)
    expected = _jenks_reference(arr.copy(), k)
    got = CartographyService._jenks_natural_breaks(arr.copy(), k)
    assert got == expected, f"{name}: new breaks {got} != reference {expected}"


def test_jenks_constant_data_returns_single_break():
    """Degenerate case: every class SSE is 0 — old code dedupes to [v]."""
    assert CartographyService._jenks_natural_breaks(np.full(30, 2.5), 5) == [2.5]


def test_jenks_classify_route_matches_reference():
    """classify(values, 'natural_breaks') must agree with the reference too
    (this is the caller-facing path every thematic/graduated map uses)."""
    vals = np.random.default_rng(12).normal(10, 3, 180).tolist()
    expected = _jenks_reference(np.array(vals), 5)
    assert CartographyService.classify(vals, method="natural_breaks", k=5) == expected


@pytest.mark.timeout(240)
def test_jenks_breaks_identical_at_n1000():
    """Equivalence at the full n=1000 DP (the size every large layer hits).
    Slow: the reference needs tens of seconds here — that is the bug."""
    vals = np.random.default_rng(13).normal(50, 12, 1000)
    for k in (5, 7):
        expected = _jenks_reference(vals.copy(), k)  # ~2x 40-60 s on old code
        got = CartographyService._jenks_natural_breaks(vals.copy(), k)
        assert got == expected, f"k={k}: new breaks {got} != reference {expected}"


@pytest.mark.timeout(240)
def test_jenks_breaks_identical_through_subsample_cap():
    """Equivalence of the n>1000 → rng(42) 1000-sample cap path: both
    implementations must select the SAME subsample (seed 42) and then produce
    identical breaks on it."""
    vals = np.random.default_rng(14).normal(0, 1, 1200)
    expected = _jenks_reference(vals.copy(), 5)
    got = CartographyService._jenks_natural_breaks(vals.copy(), 5)
    assert got == expected


# ─── Performance gate (#441 acceptance: n=1000 classify < 100 ms) ───────────


@pytest.mark.perf
def test_jenks_n1000_median_under_budget():
    """Median of 5 runs at n=1000 must stay under 300 ms per classify
    (issue target 100 ms × 3 headroom for slow CI). The OLD implementation
    takes ~40-60 s per classify at this size and fails by >100×.

    The n=1000 subsample cap must NOT be widened to pass this test — the
    sample count is pinned by asserting the cap still triggers at n>1000
    (see test below)."""
    vals = np.random.default_rng(7).normal(50, 12, 1000)
    for k in (5, 7):
        CartographyService._jenks_natural_breaks(vals.copy(), k)  # warm-up
        times = []
        for _ in range(5):
            t0 = time.perf_counter()
            CartographyService._jenks_natural_breaks(vals.copy(), k)
            times.append(time.perf_counter() - t0)
        median_s = statistics.median(times)
        assert median_s < 0.3, (
            f"k={k}: median classify time {median_s * 1000:.1f} ms exceeds the "
            "300 ms budget (#441 target: <100 ms at n=1000)"
        )


def test_jenks_subsample_cap_still_1000():
    """Guard against fake speedups: the >1000 subsample cap (documented
    approximation) must keep capping at exactly 1000 samples. A 100-element
    cap would make any implementation fast and wrong."""
    vals = np.random.default_rng(15).normal(0, 1, 5000)
    # The implementation subsamples AFTER sorting — replicate that exactly.
    sorted_vals = np.sort(vals)
    capped = np.sort(np.random.default_rng(42).choice(sorted_vals, size=1000, replace=False))

    # The cap must bind: breaks of the full 5000-value array must equal the
    # breaks of the deterministic rng(42) 1000-sample of it.
    full = CartographyService._jenks_natural_breaks(vals.copy(), 5)
    direct = CartographyService._jenks_natural_breaks(capped.copy(), 5)
    assert full == direct
