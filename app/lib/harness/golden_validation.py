"""Golden Validation — golden_diff 的 app 侧校验入口（V11 W0.3，ADR-0160）。

缺口 G7：``app/lib/cartography/golden_diff.py``（纯函数像素 diff）此前仅被
``scripts/golden_baseline.py`` 与 tests/quality 引用，app/ 运行时零消费 ——
golden 校验能力在产品侧「存在但不可达」。本模块把它接进 harness 层：

- :func:`validate_golden_pair`：一对 PNG（基线/当前）→ 结构化校验结论
  （像素 diff + 取色点可分性 + 墨量带），供报告/评测/导出链路调用；
- :func:`self_check_golden_diff`：内存合成 PNG 的确定性自检（不落盘、
  不起浏览器），注册进 ``registry_validation`` 启动自检 —— 模块若因
  依赖/重构损坏，启动即报。

校验语义（容差/通过线）全部沿用 golden_diff 单点定义，此处只做装配，
不复制常量。
"""
from __future__ import annotations

import io
from typing import Any, Dict, Optional, Sequence, Tuple

from app.lib.cartography.golden_diff import (
    PASS_PIXEL_RATIO,
    PER_CHANNEL_TOLERANCE,
    compare_golden,
    image_diff,
    ink_ratio,
)


def _png_bytes(width: int, height: int, rgb: Sequence[Tuple[int, int, int]]) -> bytes:
    """合成最小 PNG（测试/自检专用；Pillow 为既有直接依赖）。"""
    from PIL import Image

    img = Image.new("RGB", (width, height))
    img.putdata(list(rgb) or [(0, 0, 0)] * (width * height))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def validate_golden_pair(
    baseline_png: bytes,
    current_png: bytes,
    baseline_samples: Optional[Sequence[Tuple[int, int, int]]] = None,
    current_samples: Optional[Sequence[Tuple[int, int, int]]] = None,
    *,
    ink_floor: Optional[float] = None,
) -> Dict[str, Any]:
    """app 侧 golden 校验入口：一对 PNG → 校验结论（确定性、可序列化）。

    - 像素 diff + 取色点可分性：:func:`compare_golden` 原语义；
    - ``ink_floor`` 给出时叠加墨量带下限（当前图墨量 < floor → 附加失败，
      捕获「整块要素消失但 pass-ratio 预算兜住」的盲区）；
    - 结果 ``pass`` 为总判定；不抛异常（解码失败等以 reason 体现）。
    """
    result = compare_golden(
        baseline_png,
        current_png,
        baseline_samples or (),
        current_samples or (),
    )
    if ink_floor is not None:
        try:
            ink = ink_ratio(current_png)
        except Exception as exc:  # noqa: BLE001 — 校验入口不抛，诚实报 reason
            ink = float("nan")
            result["pass"] = False
            result["reason"] = f"ink_ratio 不可用: {exc}"
        result["ink_ratio"] = ink
        result["ink_floor"] = ink_floor
        if result["pass"] and not (ink >= ink_floor):
            result["pass"] = False
            result["reason"] = (
                f"墨量 {ink} 低于下限 {ink_floor} —— 要素可能整块消失"
            )
    return result


def self_check_golden_diff() -> list[str]:
    """golden_diff 模块自检（启动自检面；内存合成，确定性）。

    用 4×4 纯色/变色 PNG 验证三件事：同图 pass、变色不 pass、
    取色点可分性判定可用。返回问题列表（空 = 自检通过）。
    """
    issues: list[str] = []
    try:
        base = _png_bytes(4, 4, [(10, 20, 30)] * 16)
        same = _png_bytes(4, 4, [(10, 20, 30)] * 16)
        diff = _png_bytes(4, 4, [(10, 20, 30)] * 12 + [(200, 200, 200)] * 4)
    except Exception as exc:  # noqa: BLE001 — 自检不抛
        return [f"golden_validation: 合成 PNG 失败: {exc}"]

    ok_pair = image_diff(base, same)
    if not ok_pair.get("pass"):
        issues.append("golden_validation: 同图 diff 未通过（容差语义漂移）")
    bad_pair = image_diff(base, diff)
    if bad_pair.get("pass"):
        issues.append("golden_validation: 变色图 diff 通过（拦截能力失效）")
    sep = validate_golden_pair(
        base, same, baseline_samples=[(10, 20, 30), (240, 240, 240)]
    )
    if not sep.get("baseline_sample_separability", {}).get("pass"):
        issues.append("golden_validation: 取色点可分性判定异常")
    if PER_CHANNEL_TOLERANCE <= 0 or not 0 < PASS_PIXEL_RATIO <= 1:
        issues.append("golden_validation: golden_diff 常量口径异常")
    return issues


__all__ = ["validate_golden_pair", "self_check_golden_diff"]
