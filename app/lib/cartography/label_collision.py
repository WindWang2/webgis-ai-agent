"""Export Label Collision Solver — portable twin subset (V6, ADR-0120 W6).

``layout.labels.collision == "deterministic"`` 时导出孪生（Python/TS）共用
的确定性碰撞求解子集。与 ``label_engine.solve_labels``（harness 工具链，
shapely 候选）的关系：**同一几何口径的便携子集** —— estimate_label_box/
DECLUTTER 序/collides 语义逐常量一致；子集差异（诚实登记）：

- line/polygon 单锚点候选（锚点与 keep-upright 角度由调用方传入 —— 孪生
  编译器已在要素循环中算好中点/质心与段方位角）；不做等弧长多站点点位、
  不做 representative_point（shapely 不可移植）。
- 不做 callout 引线（导出件引线渲染属后续 wave）；放不下 = 抑制 +
  ``label_collision_relaxed`` 披露。

纯函数、无随机、无 locale；Python/TS 由共享差分 fixtures 锁定
（tests/cartography/golden_corpus/label_collision/，坐标按 3 位小数对齐）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field as _dc_field
from typing import Any, Dict, List, Optional, Tuple

#: 点标注 8 方位候选序（label_engine.DECLUTTER_CANDIDATE_OFFSETS 同表）。
DECLUTTER_OFFSETS: Tuple[Tuple[float, float], ...] = (
    (1.0, 1.0), (1.0, 0.0), (1.0, -1.0), (0.0, -1.0),
    (-1.0, -1.0), (-1.0, 0.0), (-1.0, 1.0), (0.0, 1.0),
)

_CJK_RANGES: Tuple[Tuple[int, int], ...] = (
    (0x3000, 0x303F), (0x3400, 0x4DBF), (0x4E00, 0x9FFF),
    (0xF900, 0xFAFF), (0xFF00, 0xFFEF),
)

#: 单次导出标签请求上限（超出部分抑制 + budget 披露；R1 资源包络）。
MAX_LABELS_PER_EXPORT = 400

Box = Tuple[float, float, float, float]


def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    return any(lo <= o <= hi for lo, hi in _CJK_RANGES)


def estimate_label_box(text: str, font_size: float) -> Tuple[float, float]:
    """CJK 1.0em / 其余 0.6em 加权宽；高 = 1.2em（label_engine 同口径）。"""
    if not text:
        return (0.0, font_size * 1.2)
    em = sum(1.0 if _is_cjk(c) else 0.6 for c in text)
    return (em * font_size, font_size * 1.2)


def _keep_upright(deg: float) -> float:
    a = math.fmod(deg, 360.0)
    if a > 180.0:
        a -= 360.0
    elif a <= -180.0:
        a += 360.0
    if a > 90.0 or a < -90.0:
        a = math.fmod(a + 180.0, 360.0)
    return a


def _overlaps(a: Box, b: Box) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _inside_viewport(box: Box, vp: List[float]) -> bool:
    return vp[0] <= box[0] and box[2] <= vp[2] and vp[1] <= box[1] and box[3] <= vp[3]


@dataclass
class CollisionLabel:
    """求解输入（孪生标签请求）。"""

    id: str
    text: str
    kind: str                 # point | line | polygon
    x: float                  # point: 锚点；line/polygon: 中心
    y: float
    angle: float = 0.0        # line/polygon 沿线角（度，调用方 keep-upright 后传入）
    font_size: float = 12.0
    priority: int = 0         # 小值优先；同 priority 按 id 字典序


@dataclass
class CollisionPlacement:
    id: str
    x: float
    y: float
    angle: float
    status: str               # placed | suppressed
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "x": self.x, "y": self.y, "angle": self.angle,
                "status": self.status, "reason": self.reason}


@dataclass
class CollisionSolution:
    placements: List[CollisionPlacement] = _dc_field(default_factory=list)
    stats: Dict[str, int] = _dc_field(default_factory=dict)
    budget_exceeded: bool = False

    @property
    def suppressed_count(self) -> int:
        return sum(1 for p in self.placements if p.status == "suppressed")


class _Grid:
    def __init__(self, cell: float) -> None:
        self.cell = cell if cell > 0.0 else 1e-6
        self._cells: Dict[Tuple[int, int], List[Box]] = {}

    def _span(self, box: Box) -> Tuple[int, int, int, int]:
        return (
            math.floor(box[0] / self.cell), math.floor(box[2] / self.cell),
            math.floor(box[1] / self.cell), math.floor(box[3] / self.cell),
        )

    def insert(self, box: Box) -> None:
        x0, x1, y0, y1 = self._span(box)
        for cx in range(x0, x1 + 1):
            for cy in range(y0, y1 + 1):
                self._cells.setdefault((cx, cy), []).append(box)

    def collides(self, box: Box) -> bool:
        x0, x1, y0, y1 = self._span(box)
        for cx in range(x0, x1 + 1):
            for cy in range(y0, y1 + 1):
                for other in self._cells.get((cx, cy), ()):
                    if _overlaps(box, other):
                        return True
        return False


def _corner_box(x: float, y: float, w: float, h: float) -> Box:
    return (x, y, x + w, y + h)


def _centered_box(x: float, y: float, w: float, h: float, angle_deg: float) -> Box:
    a = math.radians(angle_deg)
    c, s = math.cos(a), math.sin(a)
    hw, hh = w / 2.0, h / 2.0
    xs: List[float] = []
    ys: List[float] = []
    for sx in (-hw, hw):
        for sy in (-hh, hh):
            xs.append(x + sx * c - sy * s)
            ys.append(y + sx * s + sy * c)
    return (min(xs), min(ys), max(xs), max(ys))


def solve_export_labels(
    features: List[CollisionLabel],
    viewport: List[float],
    *,
    max_labels: int = MAX_LABELS_PER_EXPORT,
) -> CollisionSolution:
    """确定性导出标签求解（单遍贪心；同输入恒同输出）。

    预算：``len(features) > max_labels`` 时，排序尾部溢出部分直接抑制
    （reason="budget"），``budget_exceeded=True`` —— 调用方据此发射
    ``label_budget_exceeded`` 诊断。
    """
    ordered = sorted(features, key=lambda f: (f.priority, f.id))
    budget_exceeded = len(ordered) > max_labels
    gated = ordered[:max_labels]
    overflow = ordered[max_labels:]

    placements: List[CollisionPlacement] = []
    for f in overflow:
        placements.append(CollisionPlacement(
            id=f.id, x=f.x, y=f.y, angle=0.0, status="suppressed", reason="budget",
        ))

    prepared: List[Tuple[CollisionLabel, float, float]] = []
    for f in gated:
        if not f.text.strip() or f.font_size <= 0:
            # R2-M11：font_size<=0 → 负/零尺寸盒使格网失效（互叠压）——同
            # empty_text 抑制；TS 孪生同口径
            placements.append(CollisionPlacement(
                id=f.id, x=f.x, y=f.y, angle=0.0, status="suppressed", reason="empty_text",
            ))
            continue
        w, h = estimate_label_box(f.text, f.font_size)
        prepared.append((f, w, h))

    cell = (2.0 * max(max(w, h) for _, w, h in prepared)) if prepared else 48.0
    grid = _Grid(max(cell, 1.0))

    placed_n = 0
    collision_n = 0
    for f, w, h in prepared:
        chosen: Optional[Tuple[float, float, float, Box]] = None
        if f.kind == "point":
            offset = f.font_size * 0.75
            for dx, dy in DECLUTTER_OFFSETS:
                cx = f.x + dx * offset
                cy = f.y + dy * offset
                box = _corner_box(cx, cy, w, h)
                if _inside_viewport(box, viewport) and not grid.collides(box):
                    chosen = (cx, cy, 0.0, box)
                    break
            if chosen is None:
                collision_n += 1
                placements.append(CollisionPlacement(
                    id=f.id, x=f.x, y=f.y, angle=0.0, status="suppressed", reason="collision",
                ))
                continue
        else:
            box = _centered_box(f.x, f.y, w, h, _keep_upright(f.angle))
            if _inside_viewport(box, viewport) and not grid.collides(box):
                chosen = (f.x, f.y, _keep_upright(f.angle), box)
            else:
                collision_n += 1
                placements.append(CollisionPlacement(
                    id=f.id, x=f.x, y=f.y, angle=0.0, status="suppressed", reason="collision",
                ))
                continue

        cx, cy, ang, box = chosen
        placed_n += 1
        placements.append(CollisionPlacement(
            id=f.id, x=cx, y=cy, angle=ang, status="placed", reason="",
        ))
        grid.insert(box)

    return CollisionSolution(
        placements=placements,
        stats={
            "total": len(features),
            "placed": placed_n,
            "suppressed": len(features) - placed_n,
            "collisions": collision_n,
        },
        budget_exceeded=budget_exceeded,
    )
