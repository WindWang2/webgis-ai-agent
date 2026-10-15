"""Representation-at-scale golden corpus（方向 5）—— 双侧共享 parity 闸。

fixture（``representation_golden.json``）由本脚本从 **python 权威实现**
（``app.lib.cartography.representation``）确定性生成并冻结：

- ``cases``：24 个代表性输入（几何族 × 要素量级 × 视口/媒介 × user pin
  × artifact 可用性 × detail-visibility 开关）；
- 每个 case 在 9 个 zoom 采样点求 ``representation_mode_at_zoom`` +
  tier + 窗口计划 + 结构门投影，全部冻结进 golden；
- 后端 pytest（``test_representation_corpus_v1.py``）重算并断言相等
  （实现漂移即红）；
- 前端 vitest（``frontend/lib/carto-ir/representation-parity.golden.test.ts``）
  读取同一 JSON，用 TS 裁决器重算 mode-at-zoom 并断言相等 —— 跨语言
  parity 闸（双侧任一实现漂移即红）。

再生成：``python tests/cartography/representation_corpus/generate.py``
（显式刷新，golden 纪律同 tests/cartography/golden_corpus）。
"""
from __future__ import annotations

import json
from pathlib import Path

from app.lib.cartography.representation import (
    representation_at_scale,
    representation_mode_at_zoom,
)
from app.lib.cartography.scale_rules import resolve_scale_tier

CORPUS_DIR = Path(__file__).parent
GOLDEN = CORPUS_DIR / "representation_golden.json"

#: zoom 采样（覆盖 4 带内点 + 全部边界 ±epsilon + 域端点）。
ZOOM_SAMPLES = (0.0, 4.0, 7.999, 8.0, 10.5, 10.999, 11.0, 13.5, 13.999, 14.0, 18.0, 23.999)

#: 24 个代表性 case（name → 输入）。
CASES = [
    # 点族 × 数量梯度（small/medium/dense/extreme）
    ("point_small", dict(geometry="point", feature_count=50)),
    ("point_medium", dict(geometry="point", feature_count=800)),
    ("point_medium_plus", dict(geometry="point", feature_count=1500)),
    ("point_dense", dict(geometry="point", feature_count=5000)),
    ("point_dense_city", dict(geometry="point", feature_count=12000)),
    ("point_extreme", dict(geometry="point", feature_count=60000)),
    # 点族 × 视口/媒介（A4 portrait/landscape @300dpi、4:3）
    ("point_dense_a4_portrait", dict(geometry="point", feature_count=12000, viewport_px_w=2480, viewport_px_h=3508)),
    ("point_dense_a4_landscape", dict(geometry="point", feature_count=12000, viewport_px_w=3508, viewport_px_h=2480)),
    ("point_dense_4by3", dict(geometry="point", feature_count=5000, viewport_px_w=1024, viewport_px_h=768)),
    # 线族
    ("line_small", dict(geometry="line", feature_count=500)),
    ("line_overload_no_hidden", dict(geometry="line", feature_count=90000)),
    ("line_overload_detail_gate", dict(geometry="line", feature_count=90000, allow_detail_visibility_hidden=True)),
    ("line_generalized_artifact", dict(geometry="line", feature_count=90000, artifact_modes=("generalized",))),
    # 面族
    ("polygon_small", dict(geometry="polygon", feature_count=200)),
    ("polygon_overload", dict(geometry="polygon", feature_count=90000)),
    ("polygon_overload_artifact", dict(geometry="polygon", feature_count=90000, artifact_modes=("generalized",))),
    ("polygon_grid_artifact", dict(geometry="polygon", feature_count=90000, artifact_modes=("aggregate_grid",))),
    # 栅格 / 未知
    ("raster", dict(geometry="raster", feature_count=0)),
    ("unknown_family", dict(geometry="graph", feature_count=10)),
    # user pin
    ("pin_cluster", dict(geometry="point", feature_count=50, user_pin="point_cluster")),
    ("pin_heatmap", dict(geometry="point", feature_count=50, user_pin="visual_heatmap")),
    ("pin_incompatible", dict(geometry="polygon", feature_count=100, user_pin="visual_heatmap")),
    ("pin_unknown", dict(geometry="point", feature_count=100, user_pin="hologram")),
    # 参考 zoom 变体（快照字段）
    ("point_dense_ref_world", dict(geometry="point", feature_count=5000, reference_zoom=4.0)),
]


def build_golden() -> dict:
    cases = []
    for name, kwargs in CASES:
        decision = representation_at_scale(**kwargs)
        modes = [
            representation_mode_at_zoom(decision.windows, z)
            for z in ZOOM_SAMPLES
        ]
        tiers = [resolve_scale_tier(z).name for z in ZOOM_SAMPLES]
        cases.append({
            "name": name,
            "input": {k: (list(v) if isinstance(v, tuple) else v) for k, v in kwargs.items()},
            "geometry_family": decision.geometry_family,
            "feature_count": decision.feature_count,
            "windows": [
                {"min_zoom": w.min_zoom, "max_zoom": w.max_zoom, "mode": w.mode}
                for w in decision.windows
            ],
            "visibility_gate": decision.visibility_gate,
            "aggregation_present": decision.aggregation is not None,
            "mode_at_reference": decision.mode_at_reference,
            "tier_at_reference": decision.tier,
            "zoom_samples": list(ZOOM_SAMPLES),
            "modes_at_samples": modes,
            "tiers_at_samples": tiers,
        })
    return {
        "contract": "representation_corpus.v1",
        "description": "方向 5：24 case × 12 zoom 采样；后端 pytest 重算锁定，前端 vitest 用 TS 裁决器对拍 mode_at_samples（跨语言 parity）。",
        "zoom_samples": list(ZOOM_SAMPLES),
        "cases": cases,
    }


def main() -> None:
    golden = build_golden()
    GOLDEN.write_text(json.dumps(golden, ensure_ascii=False, indent=1) + "\n")
    print(f"wrote {GOLDEN} ({len(golden['cases'])} cases × {len(ZOOM_SAMPLES)} zooms)")


if __name__ == "__main__":
    main()
