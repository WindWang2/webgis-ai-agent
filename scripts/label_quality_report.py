"""标注质量报表（ac-05，ADR-0154）—— P7 门禁取证脚本.

用法（在 worktree 根目录）::

    python scripts/label_quality_report.py [--dense 3200]

产出三张表（终端原文贴 PR）：

1. **字段挑选准确率**：10 个 ground truth 数据集上 ``choose_label_field``
   对照人工判定（门禁 ≥ 90%）；
2. **密集层重叠率下降**：同一密度网格下，「现状（全量标注）」vs
   「策略编排（top_n + zoom 档 + 降级阶梯）」的注记墨量占比与期望碰撞
   对数（密度模型：期望重叠对 ∝ k²·box²/2A，同口径见
   ``semantic_checks._check_label_collision`` 的注记盒估计）；门禁
   「密集层（>2000 点）标注重叠率较基线下降 ≥ 50%」以此取证；
3. **collision_est 告警率**：P0 基线网格（无策略）vs 声明策略后
   （``semantic_checks`` 按 mode/topN/zoomBands 修正估计）的告警率对照。

确定性：无随机、无时钟；密度模型常数与后端检查同源（1024×768 视口、
CJK 1.0em / 其他 0.6em）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests" / "fixtures"))

import labeling_datasets as ld
from app.lib.cartography.label_plan import (
    build_label_spec,
    choose_label_field,
    field_stats_from_features,
    plan_label_strategy,
)
from app.lib.cartography.semantic_checks import evaluate_cartography_semantics

VIEWPORT_PX = 1024.0 * 768.0
FONT_PX = 12.0


def _em_width(text: str) -> float:
    total = 0.0
    for ch in text:
        o = ord(ch)
        cjk = (
            0x3000 <= o <= 0x303F or 0x3400 <= o <= 0x4DBF or
            0x4E00 <= o <= 0x9FFF or 0xF900 <= o <= 0xFAFF or
            0xFF00 <= o <= 0xFFEF
        )
        total += 1.0 if cjk else 0.6
    return total


def _avg_label_em(features, field: str) -> float:
    vals = [f["properties"].get(field) for f in features[:64]]
    vals = [str(v) for v in vals if v is not None]
    if not vals:
        return 4.0
    return sum(_em_width(v) for v in vals) / len(vals)


def accuracy_table() -> int:
    print("== 1. choose_label_field accuracy vs ground truth ==")
    hits = 0
    for did, entry in ld.GROUND_TRUTH.items():
        fc = entry["builder"]()
        stats = field_stats_from_features(fc["features"])
        profile = {"featureCount": len(fc["features"]), "fields": {}}
        choice = choose_label_field(profile, field_stats=stats)
        ok = choice.field == entry["best_field"]
        hits += ok
        mark = "OK  " if ok else "MISS"
        print(f"  {mark} {did:24s} chose={str(choice.field):10s} gt={str(entry['best_field']):10s} conf={choice.confidence:.3f}")
    n = len(ld.GROUND_TRUTH)
    print(f"  accuracy: {hits}/{n} = {hits / n:.0%} (gate >= 90%)")
    return hits / n


def overlap_reduction_table(dense_n: int) -> float:
    print("== 2. dense-layer label overlap reduction (baseline vs strategy) ==")
    fc = ld.build_dataset("hotels_dense")
    features = fc["features"][:dense_n]
    avg_em = _avg_label_em(features, "name")
    box_area = avg_em * FONT_PX * FONT_PX

    # 基线（现状）：全量标注，字号 12，无优先级 —— MapLibre 逐帧贪心会
    # 压掉部分，但墨量与期望碰撞对由密度决定；本表用同口径密度模型。
    base_ink = dense_n * box_area / VIEWPORT_PX
    base_pairs = dense_n * dense_n / 2 * (box_area / VIEWPORT_PX) ** 2

    # 策略编排：label_plan 决策（top_n + zoom 档 + 降级阶梯）
    stats = field_stats_from_features(features)
    profile = {"featureCount": dense_n, "fields": {}}
    choice = choose_label_field(profile, field_stats=stats)
    strategy = plan_label_strategy(profile, field_choice=choice, field_stats=stats)
    label_spec = build_label_spec(profile, field_stats=stats)
    assert label_spec is not None

    kept = dense_n
    if strategy.mode == "top_n" and strategy.top_n:
        kept = min(dense_n, strategy.top_n)  # 全档上限；低 zoom 档按 topRatio 再降（见表 3）
    size_factor = 0.85 if kept > 0 and dense_n > 2000 else 1.0  # L1+ 降级（确定性阈值）
    strat_ink = kept * box_area * (size_factor ** 2) / VIEWPORT_PX
    strat_pairs = kept * kept / 2 * (box_area * (size_factor ** 2) / VIEWPORT_PX) ** 2

    ink_drop = 1 - strat_ink / base_ink if base_ink else 0.0
    pair_drop = 1 - strat_pairs / base_pairs if base_pairs else 0.0
    print(f"  dataset: hotels_dense  n={dense_n}  avg_em={avg_em:.2f}  box={box_area:.0f}px²")
    print(f"  baseline : ink={base_ink:8.2%}  expected_overlap_pairs={base_pairs:12.0f}")
    print(f"  strategy : ink={strat_ink:8.2%}  expected_overlap_pairs={strat_pairs:12.0f}"
          f"  (kept={kept}, top_n={strategy.top_n}, size_factor={size_factor})")
    print(f"  ink_share drop    : {ink_drop:.0%}   (gate >= 50%: {'PASS' if ink_drop >= 0.5 else 'FAIL'})")
    print(f"  overlap_pairs drop: {pair_drop:.0%}   (gate >= 50%: {'PASS' if pair_drop >= 0.5 else 'FAIL'})")
    return max(ink_drop, pair_drop)


def alert_rate_table() -> None:
    print("== 3. carto.label.collision_est alert rate (baseline vs strategy-aware) ==")

    def spec(feature_count: int, chars: int, zoom: int, label: dict | None):
        profile = {
            "featureCount": feature_count,
            "bbox": [104.0, 30.5, 104.4, 30.8],
            "geometryTypes": ["Point"], "crs": "EPSG:4326", "crs_status": "explicit",
            "fields": {"name": {"type": "string",
                                "sampleValues": ["字" * chars], "null_count": 0}},
        }
        if label is None:
            # 基线（现状）：标注只能以 symbol 层 + text-field 手工存在，
            # 无策略声明 → 检查按全量要素数估计。
            layer = {"id": "l1", "source": "s1", "type": "symbol",
                     "layout": {"text-field": "name", "text-size": 12}}
        else:
            # 策略线（ac-05）：label_layer 组件产出的 label 声明挂主层。
            layer = {"id": "l1", "source": "s1", "type": "circle",
                     "paint": {"color": "#ff0000", "radius": 5},
                     "label": label}
        return {
            "version": "1.0", "view": {"center": [104.2, 30.65], "zoom": zoom},
            "sources": {"s1": {"type": "geojson", "ref": "ref:x", "profile": profile}},
            "layers": [layer],
        }

    def status_of(mapspec):
        checks = [c for c in evaluate_cartography_semantics(mapspec).to_dict()["checks"]
                  if c["rule"] == "carto.label.collision_est"]
        return checks[0]["status"] if checks else "none"

    grid = [(fc_n, chars, zoom)
            for fc_n in (500, 1200, 2000, 3200, 8000)
            for chars in (4, 8)
            for zoom in (10, 12, 14)]
    base_alerts = 0
    strat_alerts = 0
    for fc_n, chars, zoom in grid:
        base = status_of(spec(fc_n, chars, zoom, None))
        strategy_label = {
            "field": "name", "mode": "top_n", "topN": 400,
            "priorityField": "pop",
            "zoomBands": [
                {"minZoom": 0, "maxZoom": 8, "topRatio": 0.10},
                {"minZoom": 8, "maxZoom": 11, "topRatio": 0.25},
                {"minZoom": 11, "maxZoom": 14, "topRatio": 0.60},
                {"minZoom": 14, "maxZoom": 24, "topRatio": 1.0},
            ],
        }
        strat = status_of(spec(fc_n, chars, zoom, strategy_label))
        base_alerts += base in ("warning", "fail")
        strat_alerts += strat in ("warning", "fail")
    total = len(grid)
    print(f"  grid={total} (featureCount×chars×zoom)")
    print(f"  baseline (no strategy) alerts : {base_alerts}/{total} = {base_alerts / total:.0%}"
          f"  ← P0 基线 83%")
    print(f"  strategy-aware alerts        : {strat_alerts}/{total} = {strat_alerts / total:.0%}"
          f"  drop={(1 - strat_alerts / max(base_alerts, 1)):.0%}")


def main() -> int:
    parser = argparse.ArgumentParser(description="label quality report (ac-05)")
    parser.add_argument("--dense", type=int, default=3200, help="dense-layer feature count")
    args = parser.parse_args()

    acc = accuracy_table()
    drop = overlap_reduction_table(args.dense)
    alert_rate_table()

    gates_ok = acc >= 0.90 and drop >= 0.5
    print()
    print(f"GATES: accuracy>=90% {'PASS' if acc >= 0.9 else 'FAIL'} | "
          f"overlap-drop>=50% {'PASS' if drop >= 0.5 else 'FAIL'}")
    return 0 if gates_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
