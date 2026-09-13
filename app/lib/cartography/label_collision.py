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

V11 W0.2（ADR-0160）：排版原语（CJK 判定/标签框估算/角度/AABB/格网/
8 方位序）收敛至 :mod:`app.lib.cartography.label_typography` 单一实现；
本模块只保留孪生请求模型与 ``solve_export_labels`` 求解语义。注意本模块
使用孪生语义 :func:`keep_upright_export_twin`（与 TS ``keepUpright`` 逐
分支等价、被 parity corpus 冻结），而非引擎语义 :func:`keep_upright`。
"""
from __future__ import annotations

from dataclasses import dataclass, field as _dc_field
from typing import Any, Dict, List, Optional, Tuple

from app.lib.cartography.label_typography import (
    DECLUTTER_CANDIDATE_OFFSETS,
    Box,
    LabelGrid as _Grid,
    centered_box as _centered_box,
    corner_box as _corner_box,
    estimate_label_box,
    inside_viewport as _inside_viewport,
    keep_upright_export_twin as _keep_upright,
)

#: 单次导出标签请求上限（超出部分抑制 + budget 披露；R1 资源包络）。
MAX_LABELS_PER_EXPORT = 400

#: 点标注 8 方位候选序（label_engine.DECLUTTER_CANDIDATE_OFFSETS 同表）。
DECLUTTER_OFFSETS = DECLUTTER_CANDIDATE_OFFSETS


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
