"""Local Visual Criteria — 本地确定性视觉判据（V11 W7.1，ADR-0167）。

缺口 G6 的 fallback 半边：``VISUAL_*`` 维度在无 VLM provider 时恒
``not_evaluated``（fail-closed）。本模块给出**可计算的本地判据** ——
从渲染 PNG 直接测量的确定性指标（构图/密度/对比/留白/可分辨），
零网络、零随机、同图恒同分。

边界（诚实）：
- 本地判据**不替代** VLM 的语义审美判断；它覆盖「可度量的视觉事实」
  （墨量、边缘密度、背景占比、色彩计数、对称性/重心偏移）；
- 与 ``golden_diff`` 的既有工具（``decode_png``/``ink_ratio``）同源复用；
- 无画面（无 PNG）时逐维 ``not_evaluated``（fail-closed 保持 —— 本地
  判据的存在不改变「无证据不通过」的总纪律）。
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

#: 判据词汇（与 selfheal 的 VISUAL_* trigger 词表对齐）。
LOCAL_DIMENSIONS = (
    "readability",                 # 可读性：墨量落在舒适带
    "information_density",         # 信息密度：边缘密度落在可读带
    "composition_balance",         # 构图平衡：视觉重心偏移
    "polish_completeness",         # 完成度：非背景覆盖率
    "color_discriminability",      # 可分辨：显著色计数
)


def _decode(png_bytes: bytes) -> Tuple[int, int, "Any"]:
    import numpy as np

    from app.lib.cartography.golden_diff import decode_png

    width, height, rgb = decode_png(png_bytes)
    arr = np.frombuffer(rgb, dtype=np.uint8).reshape(height, width, 3).astype(np.int16)
    return width, height, arr


def measure_visual_facts(png_bytes: bytes) -> Dict[str, Any]:
    """从 PNG 直接测量确定性视觉事实（纯函数）。

    - ``inkRatio``：非背景像素占比（左上角为背景参照；与 golden_diff 同口径）；
    - ``edgeDensity``：相邻像素梯度超阈的占比（信息密度代理）；
    - ``centroidOffset``：非背景像素重心相对画布中心的归一偏移量
      （构图平衡代理；0 = 正中）；
    - ``distinctColorBuckets``：4bit/通道量化后的显著色桶数（可分辨代理）。
    """
    import numpy as np

    width, height, arr = _decode(png_bytes)
    total = width * height
    if total == 0:
        return {"width": 0, "height": 0, "inkRatio": 0.0, "edgeDensity": 0.0,
                "centroidOffset": 0.0, "distinctColorBuckets": 0}
    background = arr[0, 0]
    delta = np.abs(arr - background).max(axis=2)
    foreground = delta > 16
    ink_ratio = float(foreground.mean())

    # 边缘密度：水平梯度超 24 的相邻对占比
    gx = np.abs(np.diff(arr.astype(np.int32), axis=1)).max(axis=2)
    edge_density = float((gx > 24).mean()) if gx.size else 0.0

    # 重心偏移（归一：0 居中，1 = 极端偏角）
    ys, xs = np.nonzero(foreground)
    if xs.size:
        cx = float(xs.mean()) / max(width - 1, 1) - 0.5
        cy = float(ys.mean()) / max(height - 1, 1) - 0.5
        centroid_offset = float(min(1.0, (abs(cx) + abs(cy))))
    else:
        centroid_offset = 0.0

    quant = (arr // 16).astype(np.int16)
    buckets = {tuple(px) for px in quant.reshape(-1, 3)[:: max(1, total // 20000)]}
    return {
        "width": width,
        "height": height,
        "inkRatio": round(ink_ratio, 5),
        "edgeDensity": round(edge_density, 5),
        "centroidOffset": round(centroid_offset, 5),
        "distinctColorBuckets": len(buckets),
    }


def evaluate_local_visual(png_bytes: Optional[bytes]) -> Dict[str, Any]:
    """五维本地判据（确定性 verdict；无画面 → 逐维 not_evaluated）。

    verdict 阈值（首轮 provisional，W8 实测分布校准前不拦截）：
    readability 墨量带 [0.02, 0.60]；information_density 边缘带
    [0.005, 0.45]；composition_balance 重心偏移 ≤0.25；polish 覆盖率 ≥0.02；
    discriminability 显著色桶 ≥3。
    """
    if not png_bytes:
        return {
            "evaluated": False,
            "reason": "no_screenshot",
            "dimensions": {d: {"status": "not_evaluated"} for d in LOCAL_DIMENSIONS},
        }
    facts = measure_visual_facts(png_bytes)

    def _verdict(ok: bool, value: Any, note: str) -> Dict[str, Any]:
        return {
            "status": "pass" if ok else "warning",
            "value": value,
            "provisional": True,  # W8 校准前不拦截（§0.5）
            "note": note,
        }

    dims = {
        "readability": _verdict(
            0.02 <= facts["inkRatio"] <= 0.60, facts["inkRatio"],
            "墨量落在可读带 [0.02, 0.60]"),
        "information_density": _verdict(
            0.005 <= facts["edgeDensity"] <= 0.45, facts["edgeDensity"],
            "边缘密度落在可读带 [0.005, 0.45]"),
        "composition_balance": _verdict(
            facts["centroidOffset"] <= 0.25, facts["centroidOffset"],
            "视觉重心偏移 ≤0.25"),
        "polish_completeness": _verdict(
            facts["inkRatio"] >= 0.02, facts["inkRatio"],
            "非背景覆盖率 ≥0.02（画面非空白）"),
        "color_discriminability": _verdict(
            facts["distinctColorBuckets"] >= 3, facts["distinctColorBuckets"],
            "显著色桶 ≥3"),
    }
    return {"evaluated": True, "facts": facts, "dimensions": dims}


__all__ = ["LOCAL_DIMENSIONS", "measure_visual_facts", "evaluate_local_visual"]
