"""Promptable GeoAI contract（ADR-0119 §3.9；Epic §I）。

统一 prompt 面：point / box / mask(prior) / text（仅 provider 声明
text_prompt 时合法）/ 多目标 prompt 组合。**不硬编码任何具体 SAM 版本**
——promptable 是 capability（``ProviderCapabilities.prompt_modes``），
qualifier 校验 PromptSpec ⊆ capability。

坐标语义：prompt 坐标是**像素坐标**（相对推理窗口/chip），engine 负责
地理坐标 ↔ 窗口像素坐标换算（prompt 工具入口接受地理坐标时先换算）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.lib.modelops.capabilities import PROMPT_BOX, PROMPT_MASK, PROMPT_POINT, PROMPT_TEXT
from app.lib.modelops.errors import DescriptorError

MAX_PROMPTS_PER_KIND = 64


@dataclass(frozen=True)
class PromptSpec:
    """一次 promptable 推理的完整输入（不可变；可序列化进 manifest）。"""

    points: Tuple[Tuple[float, float], ...] = ()
    boxes: Tuple[Tuple[float, float, float, float], ...] = ()  # (x, y, w, h)
    prior_masks: Tuple[Any, ...] = ()   # bool arrays（不进 fingerprint——几何进）
    text: Optional[str] = ""
    #: 多目标 prompt 的组合语义：union | intersect（默认 union）。
    combine: str = "union"
    #: 每目标标签（可选；与 prompts 顺序对应）。
    labels: Tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if (
            not self.points
            and not self.boxes
            and len(self.prior_masks) == 0
            and not self.text
        ):
            raise DescriptorError("promptable inference requires at least one prompt")
        if len(self.points) > MAX_PROMPTS_PER_KIND or len(self.boxes) > MAX_PROMPTS_PER_KIND:
            raise DescriptorError(
                f"too many prompts (max {MAX_PROMPTS_PER_KIND} per kind)"
            )
        if self.combine not in ("union", "intersect"):
            raise DescriptorError(f"unknown prompt combine policy {self.combine!r}")
        for x, y in self.points:
            if not (np_isfinite(x) and np_isfinite(y)):
                raise DescriptorError("point prompt contains non-finite coordinate")
        for bx, by, bw, bh in self.boxes:
            if bw <= 0 or bh <= 0:
                raise DescriptorError("box prompt must have positive width/height")

    # ── 能力门（qualifier 消费）─────────────────────────────────────
    def required_prompt_modes(self) -> frozenset:
        modes = set()
        if self.points:
            modes.add(PROMPT_POINT)
        if self.boxes:
            modes.add(PROMPT_BOX)
        if self.prior_masks:
            modes.add(PROMPT_MASK)
        if self.text:
            modes.add(PROMPT_TEXT)
        return frozenset(modes)

    # ── 序列化（mask 数组不进 JSON——几何以 bounding 记录）──────────
    def to_payload(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "points": [list(p) for p in self.points],
            "boxes": [list(b) for b in self.boxes],
            "combine": self.combine,
            "labels": list(self.labels),
        }
        if self.prior_masks:
            payload["prior_mask_count"] = len(self.prior_masks)
        if self.text:
            payload["text"] = self.text
        return payload

    @classmethod
    def from_payload(cls, payload: Optional[Dict[str, Any]]) -> Optional["PromptSpec"]:
        """从 manifest/extras 还原（prior_masks 不经 JSON 往返）。

        注意：JSON 往返只保留几何 prompt；prior mask 必须以数组直接传入
        构造器（engine 路径），from_payload 用于诊断/重放几何。
        """
        if not payload:
            return None
        if not isinstance(payload, dict):
            raise DescriptorError("prompt payload must be a mapping")
        return cls(
            points=tuple(tuple(map(float, p)) for p in payload.get("points", [])),
            boxes=tuple(tuple(map(float, b)) for b in payload.get("boxes", [])),
            text=payload.get("text") or None,
            combine=payload.get("combine", "union"),
            labels=tuple(int(l) for l in payload.get("labels", [])),
        )

    def geometry_payload(self) -> Dict[str, Any]:
        """进 InferenceFingerprint.postprocess/preprocess 的具名字段。"""
        return {
            "prompt_points": [list(p) for p in self.points],
            "prompt_boxes": [list(b) for b in self.boxes],
            "prompt_text_present": bool(self.text),
            "prompt_prior_count": len(self.prior_masks),
            "prompt_combine": self.combine,
        }


def np_isfinite(value: float) -> bool:
    """无 numpy 依赖的有限性检查（prompt 校验在构造期运行）。"""
    return value == value and value not in (float("inf"), float("-inf"))
