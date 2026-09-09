"""V6（ADR-0120 W6）导出标签碰撞求解 —— 差分回归（Python 参照实现）。

fixtures 由本模块参照实现生成（手核：point_declutter / budget_overflow 等），
TS 侧 label-solver.ts 必须对同一输入给出逐坐标一致的输出（3 位小数对齐）。
"""
import json
import math
from pathlib import Path

import pytest

from app.lib.cartography.label_collision import (
    MAX_LABELS_PER_EXPORT,
    CollisionLabel,
    estimate_label_box,
    solve_export_labels,
)

FIXTURE_DIR = Path(__file__).resolve().parent / "golden_corpus" / "label_collision"
FIXTURES = sorted(FIXTURE_DIR.glob("*.json"))


def _round_placements(sol) -> list:
    return [
        {"id": p.id, "x": round(p.x, 3), "y": round(p.y, 3), "angle": round(p.angle, 3),
         "status": p.status, "reason": p.reason}
        for p in sol.placements
    ]


@pytest.mark.parametrize("fixture_path", FIXTURES, ids=lambda p: p.stem)
def test_solver_matches_golden(fixture_path):
    case = json.loads(fixture_path.read_text(encoding="utf-8"))
    sol = solve_export_labels(
        [CollisionLabel(**lb) for lb in case["labels"]],
        case["viewport"],
        max_labels=case.get("maxLabels", MAX_LABELS_PER_EXPORT),
    )
    assert _round_placements(sol) == case["expected"]["placements"]
    assert sol.stats == case["expected"]["stats"]
    assert sol.budget_exceeded == case["expected"]["budgetExceeded"]


def test_deterministic_repeat():
    labels = [
        CollisionLabel(id=f"L{i}", text=f"点{i}", kind="point", x=50 + i, y=50, font_size=11, priority=i)
        for i in range(8)
    ]
    s1 = solve_export_labels(labels, [0, 0, 200, 200])
    s2 = solve_export_labels(labels, [0, 0, 200, 200])
    assert _round_placements(s1) == _round_placements(s2)


def test_placed_boxes_never_overlap():
    """不变量：placed 的碰撞盒两两不重叠（语料之外的随机性由固定语料承担）。"""
    labels = [
        CollisionLabel(id=f"M{i:02d}", text=f"标{i}", kind="point", x=30 + (i * 17) % 140, y=30 + (i * 29) % 140,
                       font_size=12, priority=i)
        for i in range(20)
    ]
    sol = solve_export_labels(labels, [0, 0, 200, 200])

    def box_of(p):
        feat = next(x for x in labels if x.id == p.id)
        w, h = estimate_label_box(feat.text, feat.font_size)
        if feat.kind == "point":
            return (p.x, p.y, p.x + w, p.y + h)
        a = math.radians(p.angle)
        c, s = math.cos(a), math.sin(a)
        hw, hh = w / 2, h / 2
        xs = [p.x + sx * c - sy * s for sx in (-hw, hw) for sy in (-hh, hh)]
        ys = [p.y + sx * s + sy * c for sx in (-hw, hw) for sy in (-hh, hh)]
        return (min(xs), min(ys), max(xs), max(ys))

    placed = [p for p in sol.placements if p.status == "placed"]
    for i in range(len(placed)):
        for j in range(i + 1, len(placed)):
            b1, b2 = box_of(placed[i]), box_of(placed[j])
            assert not (b1[0] < b2[2] and b2[0] < b1[2] and b1[1] < b2[3] and b2[1] < b1[3]), (
                placed[i].id, placed[j].id
            )


def test_budget_default_constant():
    assert MAX_LABELS_PER_EXPORT == 400
