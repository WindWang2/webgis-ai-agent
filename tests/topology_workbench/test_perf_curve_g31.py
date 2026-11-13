"""G31 资源曲线测试 —— 10k 级面/线数据的实测记录（任务书 §五/WP12）。

记录：要素数、候选对、finding 数、wall time、峰值 RSS（/proc/self/status
VmHWM）—— 区分完整计算与预算拒绝。跑在本机资源上（单进程 pytest 默认
并发纪律），数值写 stdout 供 TEST_EVIDENCE 摘录；断言只锁量级（不锁绝对
时间 —— 环境漂移防护）。
"""
from __future__ import annotations

import time


from app.services.topology_workbench.evaluators import (
    MeasureSpace,
    eval_area_coverage,
    eval_cross_layer_no_overlap,
    eval_line_endpoints_connected,
    _Budget,
)
from app.services.topology_workbench.geometry_core import prepare_source

from tests.topology_workbench.fixtures_g31 import dlat, dlon

MEASURE = MeasureSpace("EPSG:4326", "EPSG:32650")
NO_CANCEL = lambda: False  # noqa: E731


def _peak_rss_mb() -> float:
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1]) / 1024.0
    except OSError:
        pass
    return -1.0


def _grid_polygons(n_side: int, size_m: float = 45.0, gap_m: float = 5.0):
    """n² 个互不重叠的规则面（网格）。"""
    step = size_m + gap_m
    feats = []
    for i in range(n_side):
        for j in range(n_side):
            x = 116.40 + dlon(10 + i * step)
            y = 39.90 + dlat(10 + j * step)
            feats.append({
                "type": "Feature", "properties": {"id": f"g{i}-{j}"},
                "geometry": {"type": "Polygon", "coordinates": [[
                    [x, y], [x + dlon(size_m), y],
                    [x + dlon(size_m), y + dlat(size_m)],
                    [x, y + dlat(size_m)], [x, y],
                ]]},
            })
    return feats


def test_perf_curve_10k_polygons():
    """10k 面 × 10k 面（跨层禁重叠，无违规基线）：索引候选有界、秒级完成。"""
    n = 100  # 100×100 = 10_000
    a = _grid_polygons(n)
    # 对照层：同规模网格**嵌在缝隙里**（每 b 面贴邻 4 个 a 单元但零面积
    # 交叠）→ 10k×~4 候选、完整零违规的计算曲线（预算截断曲线见线端点用例）。
    b = [{
        "type": "Feature", "properties": {"id": f"b{i}-{j}"},
        "geometry": {"type": "Polygon", "coordinates": [[
            [116.40 + dlon(10 + i * 50 + 46.5), 39.90 + dlat(10 + j * 50 + 46.5)],
            [116.40 + dlon(10 + i * 50 + 49.5), 39.90 + dlat(10 + j * 50 + 46.5)],
            [116.40 + dlon(10 + i * 50 + 49.5), 39.90 + dlat(10 + j * 50 + 49.5)],
            [116.40 + dlon(10 + i * 50 + 46.5), 39.90 + dlat(10 + j * 50 + 49.5)],
            [116.40 + dlon(10 + i * 50 + 46.5), 39.90 + dlat(10 + j * 50 + 46.5)],
        ]]},
    } for i in range(100) for j in range(100)]
    src_a = prepare_source(role="source_a", source_kind="session_ref", ref="r",
                           content_revision=1, fingerprint="fa", crs="EPSG:4326",
                           features=a, expected_family="polygon")
    src_b = prepare_source(role="source_b", source_kind="session_ref", ref="r",
                           content_revision=1, fingerprint="fb", crs="EPSG:4326",
                           features=b, expected_family="polygon")
    t0 = time.monotonic()
    out = eval_cross_layer_no_overlap(
        rule={"rule_id": "x", "rule_type": "cross_layer_no_overlap", "severity": "error",
              "params": {"overlap_area_threshold_m2": 1.0}},
        source_a=src_a, source_b=src_b, measure=MEASURE,
        ruleset_digest="d", budget=_Budget(), should_cancel=NO_CANCEL)
    dt = time.monotonic() - t0
    print(
        f"\n[perf] cross_layer_no_overlap(aligned): A={len(a)} B={len(b)} "
        f"pairs={out.counts['candidate_pairs']} findings={out.counts['findings_emitted']} "
        f"computation={out.computation} wall={dt:.2f}s peakRSS={_peak_rss_mb():.0f}MB"
    )
    assert out.computation == "full"  # 贴边不重叠 → 完整零违规
    assert out.counts["findings_emitted"] == 0
    assert dt < 120  # RULE_WALL_CLOCK_S 预算内（量级断言，防环境漂移误报）


def test_perf_curve_10k_coverage():
    """10k 面 coverage：AOI(100 面) − ∪coverage(10k) 完整计算。"""
    aoi_feats = _grid_polygons(10, size_m=490, gap_m=10)  # 100 个大面
    coverage = _grid_polygons(100, size_m=47.0, gap_m=3.0)  # 10k 小面
    aoi = prepare_source(role="aoi", source_kind="s", ref="r", content_revision=1,
                         fingerprint="f1", crs="EPSG:4326", features=aoi_feats,
                         expected_family="polygon")
    cov = prepare_source(role="coverage", source_kind="s", ref="r", content_revision=1,
                         fingerprint="f2", crs="EPSG:4326", features=coverage,
                         expected_family="polygon")
    t0 = time.monotonic()
    out = eval_area_coverage(
        rule={"rule_id": "c", "rule_type": "area_coverage", "severity": "error",
              "params": {"gap_area_threshold_m2": 5.0}},
        coverage=cov, aoi=aoi, measure=MEASURE,
        ruleset_digest="d", budget=_Budget(), should_cancel=NO_CANCEL)
    dt = time.monotonic() - t0
    print(
        f"\n[perf] area_coverage: aoi={len(aoi_feats)} coverage={len(coverage)} "
        f"findings={out.counts.get('findings_emitted', 0)} computation={out.computation} "
        f"wall={dt:.2f}s peakRSS={_peak_rss_mb():.0f}MB"
    )
    assert out.computation == "full"
    # 47m 面元 + 3m 缝 → 3m×3m=9m² > 5m² 缝隙应被发现（有缺口的诚实计数）
    assert out.counts.get("findings_emitted", 0) > 0
    assert dt < 120


def test_perf_curve_10k_lines_endpoints():
    """10k 线端点 × 设施点：索引候选有界。"""
    lines = []
    for i in range(10_000):
        x = 116.40 + dlon(10 + (i % 100) * 50)
        y = 39.90 + dlat(10 + (i // 100) * 50)
        lines.append({
            "type": "Feature", "properties": {"id": f"L{i}"},
            "geometry": {"type": "LineString", "coordinates": [
                [x, y], [x + dlon(40), y + dlat(40)],
            ]},
        })
    facilities = [{
        "type": "Feature", "properties": {"id": f"P{i}"},
        "geometry": {"type": "Point",
                     "coordinates": [116.40 + dlon(10 + (i % 100) * 50),
                                     39.90 + dlat(10 + (i // 100) * 50)]},
    } for i in range(10_000)]  # 每条线起点有设施（起点通过，终点远）
    src_l = prepare_source(role="checked", source_kind="s", ref="r", content_revision=1,
                           fingerprint="f1", crs="EPSG:4326", features=lines,
                           expected_family="line")
    src_p = prepare_source(role="connectors", source_kind="s", ref="r", content_revision=1,
                           fingerprint="f2", crs="EPSG:4326", features=facilities,
                           expected_family="any")
    t0 = time.monotonic()
    out = eval_line_endpoints_connected(
        rule={"rule_id": "e", "rule_type": "line_endpoints_connected", "severity": "error",
              "params": {"tolerance_m": 1.0}},
        checked=src_l, connectors=src_p, measure=MEASURE,
        ruleset_digest="d", budget=_Budget(), should_cancel=NO_CANCEL)
    dt = time.monotonic() - t0
    print(
        f"\n[perf] line_endpoints: lines={len(lines)} facilities={len(facilities)} "
        f"pairs={out.counts['candidate_pairs']} findings={out.counts['findings_emitted']} "
        f"computation={out.computation} wall={dt:.2f}s peakRSS={_peak_rss_mb():.0f}MB"
    )
    # 诚实预算曲线：10k 线全部远端未连接 → 5000 finding 触顶 → partial
    # （计数披露，绝不把截断当 pass）
    assert out.computation == "partial"
    assert out.counts["findings_emitted"] == 5000
    assert any("部分结果" in n or "触顶" in n for n in out.notes)
    assert dt < 120
