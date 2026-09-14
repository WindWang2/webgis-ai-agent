"""离线确定性 Fake VLM 体系（ADR-0185 D8）。

``GOLDEN_SAMPLES``：10 种典型视觉缺陷黄金样本 = canned JSON 响应 + 期望
维度/严重度断言元数据。``FakeVLMClient`` 实现 ``VLMClient`` 协议：按样本名
路由 canned 响应、计数外呼、支持原始响应队列与故障注入（超时/坏输出）。

定位：这是**产品代码**（契约测试/回放基准/离线演示共用），不是测试私有
夹具；canned 响应的形状就是 ``CRITIC_OUTPUT_SCHEMA`` 的合法实例。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from app.lib.harness.visual_judge.vlm_provider import (
    CriticOutputError,
    VLMRequest,
)


@dataclass(frozen=True)
class GoldenSample:
    """一个黄金缺陷样本：canned 响应 + 期望断言元数据。"""

    name: str
    description: str
    dimension: Optional[str]          # 期望主维度（良图对照为 None）
    severity: Optional[str]           # 期望严重度（良图对照为 None）
    response: Dict[str, Any]          # canned JSON（schema 合法实例）


def _critique(
    dimension: str,
    severity: str,
    suggestion: str,
    confidence: float,
    *,
    bbox: Optional[Sequence[float]] = None,
    evidence: str = "",
    defect_type: str = "",
) -> Dict[str, Any]:
    return {
        "dimension": dimension,
        "severity": severity,
        "suggestion": suggestion,
        "confidence": confidence,
        "evidence": evidence,
        "bbox": list(bbox) if bbox is not None else None,
        "defect_type": defect_type,
    }


#: 10 黄金样本（docs/dev/vlm-visual-critic-spec.md §6 逐项对应）。
GOLDEN_SAMPLES: tuple = (
    GoldenSample(
        name="overlapping_labels",
        description="标注互相重叠，图例文字完全不可读",
        dimension="readability",
        severity="error",
        response={"critiques": [_critique(
            "readability", "error",
            "Labels overlap heavily across the map center; text is unreadable.",
            0.9, bbox=[28.0, 22.0, 74.0, 81.0], evidence="dozens of colliding glyphs",
            defect_type="overlapping_labels")]},
    ),
    GoldenSample(
        name="low_contrast_dark_theme",
        description="深色底图配深色图斑，对比度严重缺失",
        dimension="color_discriminability",
        severity="error",
        response={"critiques": [_critique(
            "color_discriminability", "error",
            "Dark polygons are nearly invisible against the dark basemap.",
            0.92, bbox=[10.0, 12.0, 90.0, 88.0],
            evidence="figure-ground luminance delta far below readability threshold",
            defect_type="low_contrast")]},
    ),
    GoldenSample(
        name="adjacent_palette_confusion",
        description="相邻分类色板极难分辨",
        dimension="color_discriminability",
        severity="warning",
        response={"critiques": [_critique(
            "color_discriminability", "warning",
            "Adjacent choropleth classes use nearly indistinguishable blues.",
            0.78, bbox=[5.0, 30.0, 95.0, 70.0],
            defect_type="palette_confusion")]},
    ),
    GoldenSample(
        name="symbol_clutter_overdensity",
        description="符号过密不可辨（信息过密）",
        dimension="information_density",
        severity="error",
        response={"critiques": [_critique(
            "information_density", "error",
            "Point symbols saturate the canvas; individual features cannot be told apart.",
            0.85, bbox=[0.0, 0.0, 100.0, 100.0],
            defect_type="overdense_symbols")]},
    ),
    GoldenSample(
        name="sparse_canvas_underdensity",
        description="画布过疏，信息匮乏（信息过疏）",
        dimension="information_density",
        severity="warning",
        response={"critiques": [_critique(
            "information_density", "warning",
            "Almost the entire canvas is empty; a single feature carries no context.",
            0.7, bbox=[45.0, 60.0, 55.0, 70.0],
            defect_type="underdense_canvas")]},
    ),
    GoldenSample(
        name="bottom_heavy_layout",
        description="版面重心下坠，上半幅完全空置",
        dimension="composition_balance",
        severity="warning",
        response={"critiques": [_critique(
            "composition_balance", "warning",
            "All content crowds the bottom quarter; the upper canvas is dead space.",
            0.75, bbox=[70.0, 0.0, 100.0, 100.0],
            defect_type="bottom_heavy")]},
    ),
    GoldenSample(
        name="overlay_offset_misalignment",
        description="矢量叠加与遥感底图明显错位",
        dimension="spatial_alignment",
        severity="error",
        response={"critiques": [_critique(
            "spatial_alignment", "error",
            "Vector parcels are offset from the imagery road grid by a constant shift.",
            0.88, bbox=[20.0, 15.0, 85.0, 90.0],
            evidence="parcel outlines miss curb lines uniformly",
            defect_type="overlay_offset")]},
    ),
    GoldenSample(
        name="extreme_tilt_rotation",
        description="图面极端倾斜/旋转异常",
        dimension="spatial_alignment",
        severity="warning",
        response={"critiques": [_critique(
            "spatial_alignment", "warning",
            "The graticule is rotated far from north-up; orientation is disorienting.",
            0.72, bbox=[0.0, 0.0, 100.0, 100.0],
            defect_type="extreme_tilt")]},
    ),
    GoldenSample(
        name="tiny_unreadable_text",
        description="字号过小不可读",
        dimension="readability",
        severity="warning",
        response={"critiques": [_critique(
            "readability", "warning",
            "Annotation text renders below legible size at this zoom.",
            0.8, bbox=[30.0, 20.0, 70.0, 80.0],
            defect_type="tiny_text")]},
    ),
    GoldenSample(
        name="clean_balanced_map",
        description="良图对照：无可见缺陷（零批评）",
        dimension=None,
        severity=None,
        response={"critiques": []},
    ),
)

_SAMPLE_INDEX: Dict[str, GoldenSample] = {s.name: s for s in GOLDEN_SAMPLES}


class FakeVLMClient:
    """确定性 Fake VLM：样本名路由 / 原始响应队列 / 故障注入 / 外呼计数。"""

    provider = "fake"
    model = "fake-vlm"

    def __init__(
        self,
        sample: Optional[str] = None,
        *,
        responses: Optional[List[str]] = None,
        exception: Optional[Exception] = None,
    ):
        if sample is not None and sample not in _SAMPLE_INDEX:
            raise KeyError(f"unknown golden sample: {sample!r}")
        self.sample = sample
        self.responses = list(responses) if responses is not None else None
        self.exception = exception
        self.calls = 0
        self.requests: List[VLMRequest] = []

    @property
    def call_count(self) -> int:
        return self.calls

    async def critique(self, request: VLMRequest) -> str:
        self.calls += 1
        self.requests.append(request)
        if self.exception is not None:
            raise self.exception
        if self.responses is not None:
            if not self.responses:
                raise CriticOutputError("fake response queue exhausted")
            return self.responses.pop(0)
        assert self.sample is not None  # 构造期已校验
        return json.dumps(_SAMPLE_INDEX[self.sample].response)


__all__ = ["FakeVLMClient", "GoldenSample", "GOLDEN_SAMPLES"]
