"""Layout Score — 版面质量评分（V11 W5.5，ADR-0165）。

可回归的版面评分（任务书 W5.5）：平衡性 / 密度 / 留白 / 层级 / 对比五维，
纯函数、确定性（同输入恒同分），入 C4 事实库（W8 ratchet 消费；本波交付
评分器与口径锁定）。

输入：整饰组件的锚点九宫格分布 + 画布尺寸（与 compose.ts 的裁决面同构）。
评分设计（诚实分层，每维 0..1，总分加权）：

- **balance**：四象限组件数分布的基尼式均衡（1 = 完全均衡）；
- **density**：象限占用率（有组件的象限数/4）—— 半满到全满（≥0.5）
  得满分（版面有骨架），欠占用按距 0.75 的偏差降分；
- **whitespace**：top 行与 bottom 行的留白 presence（版面呼吸感）；
- **hierarchy**：primary 组件（legend/标题族）有专属锚（不与次级堆叠）；
- **contrast**：对角象限有组件（视觉重量对角分布优于单角聚集）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

#: 锚点 → 象限（Q1 左上 … Q4 右下；九宫格中列/中行按主象限归并）。
_ANCHOR_QUADRANT = {
    "top-left": 0, "top-center": 0, "top-right": 1,
    "middle-left": 0, "middle-center": -1, "middle-right": 1,
    "bottom-left": 2, "bottom-center": 2, "bottom-right": 3,
}

#: 层级主件类型（hierarchy 维的裁决对象）。
_PRIMARY_TYPES = ("legend", "continuous_colorbar", "categorical_legend",
                  "title", "chart_panel")

#: 各维权重（审定静态表；改动走 ADR）。
_WEIGHTS = {"balance": 0.25, "density": 0.2, "whitespace": 0.2,
            "hierarchy": 0.2, "contrast": 0.15}


def score_layout(
    components: List[Dict[str, Any]],
    *,
    canvas: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    """版面五维评分（确定性；可序列化 —— C4 事实库观测行）。"""
    anchored = []
    for c in components or []:
        if not isinstance(c, dict) or c.get("enabled") is False:
            continue
        placement = c.get("placement") or {}
        anchor = placement.get("anchor") or c.get("position")
        if anchor and anchor != "none":
            anchored.append((str(c.get("type", "")), str(anchor)))

    quadrants = [0, 0, 0, 0]
    middle = 0
    for _, anchor in anchored:
        q = _ANCHOR_QUADRANT.get(anchor, -1)
        if q < 0:
            middle += 1
        else:
            quadrants[q] += 1

    total = len(anchored)
    if total == 0:
        return {"version": 1, "total": 0, "scores": {}, "overall": 0.0,
                "reasons": ["无锚定组件 —— 不评分"]}

    # balance：象限计数与均值偏差的负归一
    mean = total / 4.0
    spread = sum(abs(q - mean) for q in quadrants) / max(total, 1)
    balance = max(0.0, 1.0 - spread)

    # density：占用槽位数（每象限槽容量近似 3）落在舒适带
    slots_used = sum(1 for q in quadrants if q > 0) + (1 if middle else 0)
    ratio = slots_used / 4.0
    density = 1.0 if 0.5 <= ratio <= 1.0 else max(0.0, 1.0 - abs(ratio - 0.75))

    # whitespace：top 与 bottom 都有留白（象限空）或组件 ≤ 槽容量的 2/3
    top_free = quadrants[0] == 0 or quadrants[1] == 0
    bottom_free = quadrants[2] == 0 or quadrants[3] == 0
    whitespace = 1.0 if (top_free and bottom_free) else (0.5 if (top_free or bottom_free) else 0.25)

    # hierarchy：主件不与其它组件同锚堆叠
    anchor_counts: Dict[str, int] = {}
    for t, a in anchored:
        anchor_counts[a] = anchor_counts.get(a, 0) + 1
    primaries = [(t, a) for t, a in anchored if t in _PRIMARY_TYPES]
    if not primaries:
        hierarchy = 0.75  # 无主件：中性（不奖不罚）
    else:
        shared = sum(1 for _, a in primaries if anchor_counts[a] > 1)
        hierarchy = 1.0 - (shared / len(primaries)) * 0.8

    # contrast：对角象限对 (Q1,Q4) / (Q2,Q3) 至少一对有组件
    diagonal = (quadrants[0] > 0 and quadrants[3] > 0) or \
        (quadrants[1] > 0 and quadrants[2] > 0)
    contrast = 1.0 if diagonal else 0.4

    scores = {"balance": round(balance, 4), "density": round(density, 4),
              "whitespace": round(whitespace, 4),
              "hierarchy": round(hierarchy, 4), "contrast": round(contrast, 4)}
    overall = round(sum(scores[k] * _WEIGHTS[k] for k in _WEIGHTS), 4)
    return {"version": 1, "total": total, "scores": scores, "overall": overall,
            "reasons": []}


__all__ = ["score_layout"]
