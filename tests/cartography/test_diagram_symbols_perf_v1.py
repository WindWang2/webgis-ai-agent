"""图符 WP5 性能基线 —— 10/100/1k/5k 要素的资格→尺度→几何→布局全链耗时。

非严格 CI 计时断言（避免脆弱时间阈值），而是：
1) 慢速上限兜底（防病态复杂度回归：1k 要素全链必须在宽松界内完成）；
2) 实测数字打印进 pytest 输出（-s 可见）并计入 planning 账本，
   作为"实测的上限和明确降级"的证据基线；
3) 布局会计不变式在大规模下保持（placed+omitted == candidates）。
"""
from __future__ import annotations

import math
import time

import pytest

pytestmark = pytest.mark.cartography

from app.lib.cartography.diagram_symbols import (  # noqa: E402
    build_glyph_geometry,
    compute_glyph_scale,
    glyph_bound_radius,
    qualify_features,
    solve_diagram_symbol_layout,
)

# 蛇形网格散布（确定性；无真实地理意义，只考察计算复杂度）
def _snake_features(n: int) -> list[dict]:
    feats: list[dict] = []
    side = math.ceil(math.sqrt(n))
    for i in range(n):
        gx, gy = i % side, i // side
        feats.append({
            "type": "Feature",
            "id": f"f{i:06d}",
            "geometry": {"type": "Point",
                         "coordinates": [104.0 + gx * 0.01, 30.0 + gy * 0.01]},
            "properties": {"v0": float((i * 7) % 100 + 1), "v1": float((i * 13) % 50 + 1)},
        })
    return feats


def _run_chain(n: int) -> dict:
    feats = _snake_features(n)
    t0 = time.perf_counter()
    qualified = qualify_features(feats, kind="composition_pie",
                                 categories=["c0", "c1"], fields=["v0", "v1"])
    sp = {"r_max_px": 24.0, "total_area_encoding": True, "r_min_px": 2.0}
    scale = compute_glyph_scale("composition_pie", qualified["values"], sp)
    radii = [glyph_bound_radius(build_glyph_geometry("composition_pie", row, scale, sp))
             for row in qualified["values"]]
    t1 = time.perf_counter()
    plan = solve_diagram_symbol_layout(feats, qualified, radii,
                                       layout_policy={"reference_zoom": 13.0,
                                                      "max_glyphs": min(n, 1000)})
    t2 = time.perf_counter()
    ev = plan["evidence"]
    assert ev["placed"] + ev["omitted_collision"] + ev["omitted_budget"] \
        + ev["omitted_small"] + ev["blocked"] == ev["candidates"]
    return {
        "n": n,
        "qualify_scale_geom_s": round(t1 - t0, 4),
        "layout_s": round(t2 - t1, 4),
        "total_s": round(t2 - t0, 4),
        "placed": ev["placed"],
        "omitted_collision": ev["omitted_collision"],
        "omitted_budget": ev["omitted_budget"],
    }


class TestDiagramSymbolsPerformance:
    def test_scale_10_100_1k(self):
        results = [_run_chain(n) for n in (10, 100, 1_000)]
        for r in results:
            print(f"[diagram-perf] n={r['n']} qualify+geom={r['qualify_scale_geom_s']}s "
                  f"layout={r['layout_s']}s total={r['total_s']}s "
                  f"placed={r['placed']} omitted_collision={r['omitted_collision']} "
                  f"omitted_budget={r['omitted_budget']}")
        # 宽松兜底（防病态复杂度；实测数字见 stdout 与账本）
        assert results[2]["total_s"] < 30.0, f"1k 全链耗时 {results[2]['total_s']}s 失常"

    def test_scale_5k_budget_gated(self):
        r = _run_chain(5_000)
        print(f"[diagram-perf] n=5000 total={r['total_s']}s placed={r['placed']} "
              f"omitted_budget={r['omitted_budget']}")
        # 预算帽生效：5k 候选 → placed ≤ 1000，其余进 omitted_budget 会计
        assert r["placed"] <= 1000
        assert r["omitted_budget"] == r["n"] - r["placed"] - r["omitted_collision"]
        assert r["total_s"] < 120.0, f"5k 全链耗时 {r['total_s']}s 失常"
