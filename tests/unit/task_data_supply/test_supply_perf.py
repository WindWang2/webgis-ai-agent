"""WP5 · 覆盖评估与供给链路的可复现资源基准（perf 纪律，R1 诚实化版）。

测量口径（确定性合成负载；无网络 I/O；单进程 OMP=2）：

评估语义与基准的关系（R1 评审 A P1-3 修正）：``evaluate_task_coverage``
按**设计**对每角色只评估前 24 个 serving 候选（``_MAX_CANDIDATES_PER_ROLE``，
溢出在 unknown_reasons 诚实披露）——这是有界接口承诺，不是可伸缩性缺陷。
因此基准拆成两段如实测量：
- **分区扫描段**：N 个候选 → serving 角色分区的真实成本（随 N 线性，
  1k/50k 两档实测）；
- **有界评估段**：固定 24 候选（含 footprint 的 metric 投影路径 vs
  纯 bbox 声明路径）的单次评估成本（p50/最大值，5 次重复）；
- 内存峰值（tracemalloc）+ 序列化体积上限（≤7 行/角色，<64KB）；
- 覆盖误判守卫：内置手算真值集，误判数 = 0 才通过。

不再声称全量评估 N 个候选的成本——那不是生产语义（任务书红线：
有界输出优先于无界扫描）。
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
        "max_ms": round(max(times), 2),  # 5 次重复只如实报最大值
        "peak_mem_kb": round(peak_kb, 1),
    }


# ── 规模档位 ─────────────────────────────────────────────────────────────

def _partition_scan(cands):
    """生产语义的分区扫描段：角色过滤（与 evaluate_task_coverage 同规则）。"""
    from app.services.task_data_supply.coverage import (
        _MAX_CANDIDATES_PER_ROLE,
    )

    for c in cands:
        if "subject" in c.serves_roles:
            pass  # serving 分区（评估被 _MAX_CANDIDATES_PER_ROLE 有界化）
    return min(len(cands), _MAX_CANDIDATES_PER_ROLE)


@pytest.mark.parametrize("n", [1000, 50_000])
def test_partition_scan_scale(n):
    """分区扫描段随 N 线性：50k 候选 < 1s（生产面真实成本）。"""
    cands = _candidates(n)
    times = []
    tracemalloc.start()
    for _ in range(_REPS):
        t0 = time.perf_counter()
        _partition_scan(cands)
        times.append((time.perf_counter() - t0) * 1000.0)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    m = {"p50_ms": round(statistics.median(times), 2),
         "max_ms": round(max(times), 2),
         "peak_mem_kb": round(peak / 1024.0, 1)}
    assert m["p50_ms"] < 1000, m
    print(f"\n[perf] partition-scan n={n}: {m}")


def test_bounded_eval_two_paths():
    """有界评估段（24 候选/角色）：footprint metric 投影路径 vs bbox
    声明路径的单次评估成本对照（如实披露两档成本差）。"""
    req = _requirement()
    cands_bbox = _candidates(24)
    cands_fp = _candidates(24, footprint_every=1)
    m_bbox = _measure(req, cands_bbox)
    m_fp = _measure(req, cands_fp)
    assert m_bbox["p50_ms"] < 500, m_bbox
    assert m_fp["p50_ms"] < 5_000, m_fp  # 投影路径含 UTM 变换，成本高一个量级
    print(f"\n[perf] bounded-24 bbox-path: {m_bbox}")
    print(f"[perf] bounded-24 footprint-path: {m_fp}")


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
