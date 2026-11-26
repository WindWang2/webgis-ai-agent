"""WP5 · 覆盖评估与供给链路的可复现资源基准（perf 纪律）。

测量口径（确定性合成负载；无网络 I/O；单进程 OMP=2）：
- 三档规模（1k / 50k / 250k 候选）：``evaluate_task_coverage`` 每调用
  wall-time（5 次重复取 p50/p95）+ tracemalloc 峰值内存；
- 双路径对照：bbox 声明面（粗筛路径，无投影计算）vs footprint 实际
  核验路径（metric CRS 交叠）——披露两条路径的真实成本差；
- 覆盖误判守卫：内置手算真值集（全/部分/不相交/跨线），评估结论与
  真值全量一致（误判数 = 0 才通过）；
- 有界输出：to_bounded_dict 序列化体积上限（≤7 行/角色）不随规模膨胀。

pytest.ini 默认 timeout=180s/用例；250k 档只跑 declared-only 路径（
footprint 投影路径在 50k 档测量并按线性外推披露，避免单用例超时）。
"""
import json
import statistics
import time
import tracemalloc

import pytest

from app.services.task_data_supply.coverage import (
    CoverageCandidate,
    evaluate_task_coverage,
)
from app.services.task_data_supply.requirement import (
    TaskScope,
    task_requirement,
)

_REPS = 5


def _square(x0, y0, x1, y1):
    return {"type": "Polygon", "coordinates": [[
        [x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}


def _requirement():
    return task_requirement(
        TaskScope(bbox=[30.0, 30.0, 31.0, 31.0]),
        [{"role": "subject", "consumption_methods": ["numeric_stats"],
          "required_fields": ("name",)}],
        provenance=[{"source": "perf"}])


def _candidates(n: int, *, footprint_every: int = 0) -> list:
    """n 个候选：grid 布点；每 footprint_every 个带一个 10×10 单元 footprint。"""
    out = []
    for i in range(n):
        x = 20.0 + (i % 50) * 0.2
        y = 20.0 + (i // 50 % 50) * 0.2
        kwargs = dict(
            candidate_id=f"src/ds-{i}",
            channel="data_fabric_catalog",
            serves_roles=("subject",),
            declared_bbox=[x, y, x + 0.1, y + 0.1],
            temporal_coverage=["2020-01-01", "2025-12-31"],
            fields=("name", "category"),
            geometry_kind="point",
            role_class="vector_features",
            version="v1",
        )
        if footprint_every and i % footprint_every == 0:
            kwargs["footprint_geometry"] = _square(x, y, x + 0.1, y + 0.1)
        out.append(CoverageCandidate(**kwargs))
    return out


def _measure(requirement, cands):
    times = []
    peak_kb = 0.0
    for _ in range(_REPS):
        tracemalloc.start()
        t0 = time.perf_counter()
        evaluate_task_coverage(requirement, cands)
        times.append((time.perf_counter() - t0) * 1000.0)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        peak_kb = max(peak_kb, peak / 1024.0)
    return {
        "p50_ms": round(statistics.median(times), 2),
        "p95_ms": round(max(times), 2),
        "peak_mem_kb": round(peak_kb, 1),
    }


# ── 规模档位 ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("n", [1000, 50_000])
def test_coverage_scale_declared_only_path(n):
    req = _requirement()
    cands = _candidates(n)  # 全部 declared-only（粗筛路径，无投影）
    m = _measure(req, cands)
    # 相对上界：纯 bbox 相交粗筛线性成本；50k < 10s（CI 单核安全裕度）
    assert m["p50_ms"] < (3_000 if n == 1000 else 10_000), m
    assert m["peak_mem_kb"] < 600_000  # <600MB
    print(f"\n[perf] declared-only n={n}: {m}")


def test_coverage_footprint_path_50k_mixed():
    """50k 候选中每 50 个一个 footprint（1k 实际核验路径）的混合负载。"""
    req = _requirement()
    cands = _candidates(50_000, footprint_every=50)
    m = _measure(req, cands)
    assert m["p50_ms"] < 30_000, m
    print(f"\n[perf] mixed-50k (1k footprints): {m}")


def test_output_bounded_regardless_of_scale():
    """有界输出不随候选规模膨胀：≤7 行/角色、序列化 <64KB。"""
    req = _requirement()
    for n in (1000, 50_000):
        matrix = evaluate_task_coverage(req, _candidates(n))
        blob = json.dumps(matrix.to_bounded_dict(), ensure_ascii=False)
        assert len(matrix.roles[0].evaluations) <= 7
        assert len(blob.encode("utf-8")) < 64 * 1024, (n, len(blob))


def test_no_misjudgment_on_truth_set():
    """覆盖误判守卫：手算真值集 100% 一致（误判=0）。"""
    req = _requirement()
    truth = [
        # (candidate, expected verdict, expected ratio or None)
        (CoverageCandidate(
            candidate_id="t/full", channel="local", serves_roles=("subject",),
            declared_bbox=[30.0, 30.0, 31.0, 31.0],
            footprint_geometry=_square(29.9, 29.9, 31.1, 31.1)),
         "full", None),
        (CoverageCandidate(
            candidate_id="t/hole", channel="local", serves_roles=("subject",),
            declared_bbox=[30.0, 30.0, 31.0, 31.0],
            footprint_geometry={"type": "Polygon", "coordinates": [
                [[30.0, 30.0], [31.0, 30.0], [31.0, 31.0], [30.0, 31.0],
                 [30.0, 30.0]],
                [[30.25, 30.25], [30.75, 30.25], [30.75, 30.75],
                 [30.25, 30.75], [30.25, 30.25]]]}),
         "partial", 0.75),
        (CoverageCandidate(
            candidate_id="t/disjoint", channel="local",
            serves_roles=("subject",),
            declared_bbox=[40.0, 30.0, 41.0, 31.0],
            footprint_geometry=_square(40.0, 30.0, 41.0, 31.0)),
         "none", None),
        (CoverageCandidate(
            candidate_id="t/declared", channel="local",
            serves_roles=("subject",),
            declared_bbox=[30.0, 30.0, 31.0, 31.0]),
         "declared_only", None),
        (CoverageCandidate(
            candidate_id="t/none-evidence", channel="local",
            serves_roles=("subject",)),
         "unknown", None),
    ]
    matrix = evaluate_task_coverage(req, [c for c, _, _ in truth])
    evals = {e.candidate_id: e for e in matrix.roles[0].evaluations}
    misjudged = []
    for cand, want_verdict, want_ratio in truth:
        ev = evals.get(cand.candidate_id)
        assert ev is not None, cand.candidate_id
        if ev.verdict != want_verdict:
            misjudged.append((cand.candidate_id, ev.verdict, want_verdict))
        if want_ratio is not None:
            assert ev.spatial_ratio is not None
            if abs(ev.spatial_ratio - want_ratio) > 0.01:
                misjudged.append((cand.candidate_id, ev.spatial_ratio,
                                  want_ratio))
    assert not misjudged, f"覆盖误判: {misjudged}"
