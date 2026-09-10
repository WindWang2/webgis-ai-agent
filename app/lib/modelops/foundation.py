"""Foundation model adapter contract —— SAM 族/遥感基础模型（V3 §H）。

架构位（诚实边界）：**不下载、不执行**任何基础模型权重——本模块定义
foundation 模型接入本平面的架构面，provider 实现走既有
``promptable_segmentation`` 契约（promptable_reference 为仓内可信
参考实现；真实 SAM 权重经 remote/extension/subprocess 通道接入）。

组件：

- :class:`FoundationModelCaps`：foundation 能力声明（模型族/prompt
  模式/tile 策略/窗口上限），provider capabilities 的语义投影；
- :func:`prompts_to_pixel`：**prompt 坐标变换**——地理坐标 prompt →
  栅格像素坐标（rasterio 仿射逆变换；box 同变换）；
- :func:`prompt_windows`：**tile 策略**——prompt 锚定窗口序列（跨距
  超过单窗口上限时按 prompt 拆分；确定性排序）；
- :func:`window_local_prompts`：prompt 平移到窗口局部坐标；
- :func:`georeference_mask_polygons`：输出掩膜 → 地理多边形（复用
  vectorize 的仿射/拓扑管线）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.lib.modelops.errors import PlanningError
from app.lib.modelops.promptable import PromptSpec

FAMILY_SAM_V1 = "sam_v1"
FAMILY_SAM_V2 = "sam_v2"
FAMILY_REMOTE_FOUNDATION = "remote_foundation"
FAMILIES = frozenset({FAMILY_SAM_V1, FAMILY_SAM_V2, FAMILY_REMOTE_FOUNDATION})

TILE_SINGLE_WINDOW = "single_window"
TILE_PROMPT_ANCHORED = "prompt_anchored"
TILE_STRATEGIES = frozenset({TILE_SINGLE_WINDOW, TILE_PROMPT_ANCHORED})

#: promptable 单窗口边长上限（超过 → prompt 锚定多窗口；内存防护）。
MAX_PROMPTABLE_WINDOW_PX = 4096


@dataclass(frozen=True)
class FoundationModelCaps:
    """foundation 模型能力声明（descriptor/prov 的结构化投影）。"""

    family: str = FAMILY_SAM_V1
    tile_strategy: str = TILE_SINGLE_WINDOW
    prompt_modes: Tuple[str, ...] = ("point", "box")
    max_prompts: int = 64
    text_prompt: bool = False
    image_encoder_px: int = 1024

    def as_dict(self) -> Dict[str, Any]:
        return {
            "family": self.family,
            "tile_strategy": self.tile_strategy,
            "prompt_modes": list(self.prompt_modes),
            "max_prompts": self.max_prompts,
            "text_prompt": self.text_prompt,
            "image_encoder_px": self.image_encoder_px,
        }


def prompts_to_pixel(
    prompt: PromptSpec,
    *,
    transform: Any,
) -> PromptSpec:
    """地理坐标 prompt → 像素坐标（rasterio 仿射；box 同变换）。

    输入约定：points/boxes 为 ``(x, y)``/``(x, y, w, h)`` **地图坐标**
    （box 的 w/h 也是地图单位）；仿射取角点。prompt 坐标系由调用方
    （engine 的 ``prompt_crs`` 参数）声明，manifest 如实记录。
    """
    if transform is None:
        raise PlanningError(
            "geographic prompts require a georeferenced raster (no transform)",
            correction_hint="pass pixel-coordinate prompts or use a GeoTIFF",
        )
    inverse = ~transform  # 仿射逆变换：地图 (x,y) → 像素 (col,row)

    def to_px(mx: float, my: float) -> Tuple[float, float]:
        col, row = inverse * (float(mx), float(my))
        return (float(col), float(row))

    points = tuple(to_px(p[0], p[1]) for p in prompt.points)
    boxes: List[Tuple[float, float, float, float]] = []
    for bx, by, bw, bh in prompt.boxes:
        x0, y0 = to_px(bx, by)            # 左下角（地图 y 向上）
        x1, y1 = to_px(bx + bw, by + bh)  # 右上角
        px0, py0 = min(x0, x1), min(y0, y1)
        boxes.append((px0, py0, abs(x1 - x0), abs(y1 - y0)))
    return PromptSpec(
        points=points,
        boxes=tuple(boxes),
        prior_masks=prompt.prior_masks,
        text=prompt.text,
        combine=prompt.combine,
        labels=prompt.labels,
    )


def prompt_span(prompt: PromptSpec) -> Tuple[int, int, int, int]:
    """prompt 几何包围盒（像素；mask-only 时全幅语义由调用方处理）。"""
    xs: List[float] = []
    ys: List[float] = []
    for px, py in prompt.points:
        xs.append(px)
        ys.append(py)
    for bx, by, bw, bh in prompt.boxes:
        xs.extend([bx, bx + bw])
        ys.extend([by, by + bh])
    if not xs:
        return (0, 0, 0, 0)
    return (int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys)))


def prompt_windows(
    prompt: PromptSpec,
    *,
    raster_height: int,
    raster_width: int,
    chip_hw: Tuple[int, int],
    max_window_px: int = MAX_PROMPTABLE_WINDOW_PX,
) -> List[Tuple[int, int, int, int]]:
    """tile 策略：prompt 锚定窗口序列 [(row, col, h, w), …]（确定性）。

    - 单窗口：prompt 包围盒 + chip margin ≤ max_window_px ⇒ 一个窗口；
    - prompt 锚定多窗口：跨距超限时每个 prompt（point/box）各开一个
      chip 级窗口（含 margin），按 prompt 序（确定性）排列。
    """
    margin_y, margin_x = chip_hw
    x0, y0, x1, y1 = prompt_span(prompt)
    if not (x1 > x0 or y1 > y0):
        # mask-only/空几何：全幅 cap 窗口。
        w = min(max_window_px, raster_width)
        h = min(max_window_px, raster_height)
        return [(0, 0, min(h, raster_height), min(w, raster_width))]
    span_w = x1 - x0 + 2 * margin_x
    span_h = y1 - y0 + 2 * margin_y
    if span_w <= max_window_px and span_h <= max_window_px:
        row = max(0, y0 - margin_y)
        col = max(0, x0 - margin_x)
        h = min(max_window_px, raster_height - row)
        w = min(max_window_px, raster_width - col)
        return [(int(row), int(col), int(h), int(w))]
    windows: List[Tuple[int, int, int, int]] = []
    for point in prompt.points:
        px, py = point
        row = max(0, int(py) - margin_y // 2)
        col = max(0, int(px) - margin_x // 2)
        h = min(chip_hw[0] + margin_y // 2, raster_height - row)
        w = min(chip_hw[1] + margin_x // 2, raster_width - col)
        windows.append((int(row), int(col), int(h), int(w)))
    for bx, by, bw, bh in prompt.boxes:
        row = max(0, int(by))
        col = max(0, int(bx))
        h = min(int(bh) + margin_y, raster_height - row)
        w = min(int(bw) + margin_x, raster_width - col)
        windows.append((int(row), int(col), int(h), int(w)))
    if not windows:
        raise PlanningError("prompt spans exceed the single-window cap but carry no geometry")
    return windows


def window_local_prompts(
    prompt: PromptSpec,
    *,
    row: int,
    col: int,
) -> PromptSpec:
    """prompt 平移到窗口局部坐标（prior mask 同步切片）。"""
    return PromptSpec(
        points=tuple((px - col, py - row) for px, py in prompt.points),
        boxes=tuple((bx - col, by - row, bw, bh) for bx, by, bw, bh in prompt.boxes),
        prior_masks=prompt.prior_masks,
        text=prompt.text,
        combine=prompt.combine,
        labels=prompt.labels,
    )


__all__ = [
    "FAMILIES",
    "FAMILY_REMOTE_FOUNDATION",
    "FAMILY_SAM_V1",
    "FAMILY_SAM_V2",
    "FoundationModelCaps",
    "MAX_PROMPTABLE_WINDOW_PX",
    "TILE_STRATEGIES",
    "TILE_PROMPT_ANCHORED",
    "TILE_SINGLE_WINDOW",
    "prompt_span",
    "prompt_windows",
    "prompts_to_pixel",
    "window_local_prompts",
]
