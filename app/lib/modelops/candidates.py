"""Mask candidate set —— 多候选掩膜与质量分契约（Platform 11 / WP-C）。

SAM ``multimask_output`` 语义的平台化：promptable provider **可以**返回
K 个候选掩膜 + 质量分；引擎按显式策略选择（best | index），或全量发布
供调用方（UI/agent）裁决后再 refine。

诚实边界（防"伪模型质量"）：
- ``score`` 语义 = **排序质量代理**，不是校准置信度；来源必须标注
  ``model``（provider 声明）或 ``heuristic``（平台启发式派生）；
- 候选数量有界（≤ :data:`MAX_MASK_CANDIDATES`）；分数 ∈ [0,1] 且有限；
- 选择策略封闭词表；越界 index = typed 拒绝，绝不静默夹取。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from app.lib.modelops.errors import PromptArtifactError

MAX_MASK_CANDIDATES = 4

CANDIDATE_SOURCE_MODEL = "model"
CANDIDATE_SOURCE_HEURISTIC = "heuristic"
CANDIDATE_SOURCES = (CANDIDATE_SOURCE_MODEL, CANDIDATE_SOURCE_HEURISTIC)

SELECTION_BEST = "best"
SELECTION_INDEX = "index"
SELECTION_POLICIES = (SELECTION_BEST, SELECTION_INDEX)


@dataclass(frozen=True)
class MaskCandidate:
    """单个候选的元数据（掩膜本体在 TileOutput.mask_candidates[index]）。"""

    index: int
    score: float
    source: str = CANDIDATE_SOURCE_MODEL
    label: Optional[int] = None

    def __post_init__(self) -> None:
        if int(self.index) < 0:
            raise PromptArtifactError(f"candidate index must be >= 0 (got {self.index})")
        score = float(self.score)
        if not (score == score and score not in (float("inf"), float("-inf"))):
            raise PromptArtifactError("candidate score must be finite")
        if not (0.0 <= score <= 1.0):
            raise PromptArtifactError(
                f"candidate score must be in [0,1] (got {score})"
            )
        if self.source not in CANDIDATE_SOURCES:
            raise PromptArtifactError(
                f"candidate source must be one of {list(CANDIDATE_SOURCES)} "
                f"(got {self.source!r})"
            )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "index": int(self.index),
            "score": float(self.score),
            "source": self.source,
            "label": self.label,
        }


@dataclass(frozen=True)
class MaskCandidateSet:
    """一次 promptable 推理的候选全集 + 选择裁决。"""

    candidates: Tuple[MaskCandidate, ...]
    selection: str = SELECTION_BEST
    #: selection=index 时必填（调用方裁决的候选）。
    selected_index: Optional[int] = None

    def __post_init__(self) -> None:
        if not self.candidates:
            raise PromptArtifactError("candidate set requires at least one candidate")
        if len(self.candidates) > MAX_MASK_CANDIDATES:
            raise PromptArtifactError(
                f"too many mask candidates ({len(self.candidates)} > "
                f"{MAX_MASK_CANDIDATES})"
            )
        seen = set()
        for cand in self.candidates:
            if cand.index in seen:
                raise PromptArtifactError(f"duplicate candidate index {cand.index}")
            seen.add(cand.index)
        if sorted(seen) != list(range(len(self.candidates))):
            raise PromptArtifactError(
                "candidate indices must be dense 0..K-1 "
                f"(got {sorted(seen)})"
            )
        if self.selection not in SELECTION_POLICIES:
            raise PromptArtifactError(
                f"unknown candidate selection {self.selection!r} "
                f"(must be one of {list(SELECTION_POLICIES)})"
            )
        if self.selection == SELECTION_INDEX:
            if self.selected_index is None:
                raise PromptArtifactError(
                    "selection=index requires selected_candidate"
                )
            if int(self.selected_index) not in seen:
                raise PromptArtifactError(
                    f"selected candidate {self.selected_index} out of range "
                    f"(0..{len(self.candidates) - 1})"
                )

    def resolve_selected(self) -> int:
        """裁决选择的候选 index（best = 最高分；平分取小 index——确定性）。"""
        if self.selection == SELECTION_INDEX:
            return int(self.selected_index or 0)
        best = self.candidates[0]
        for cand in self.candidates[1:]:
            if cand.score > best.score:
                best = cand
        return best.index

    def as_dict(self) -> Dict[str, Any]:
        return {
            "candidates": [c.as_dict() for c in self.candidates],
            "selection": self.selection,
            "selected_index": self.resolve_selected(),
        }


def candidate_set_from_arrays(
    scores: Any,
    sources: Optional[Tuple[str, ...]] = None,
    *,
    selection: str = SELECTION_BEST,
    selected_index: Optional[int] = None,
) -> MaskCandidateSet:
    """从 provider 输出的分数数组构造（index 由数组序隐含）。"""
    count = int(len(scores))
    if count == 0:
        raise PromptArtifactError("candidate scores array is empty")
    srcs = sources or (CANDIDATE_SOURCE_MODEL,) * count
    if len(srcs) != count:
        raise PromptArtifactError(
            f"candidate sources length {len(srcs)} != scores length {count}"
        )
    return MaskCandidateSet(
        candidates=tuple(
            MaskCandidate(index=i, score=float(scores[i]), source=srcs[i])
            for i in range(count)
        ),
        selection=selection,
        selected_index=selected_index,
    )


__all__ = [
    "CANDIDATE_SOURCES",
    "CANDIDATE_SOURCE_HEURISTIC",
    "CANDIDATE_SOURCE_MODEL",
    "MAX_MASK_CANDIDATES",
    "MaskCandidate",
    "MaskCandidateSet",
    "SELECTION_BEST",
    "SELECTION_INDEX",
    "SELECTION_POLICIES",
    "candidate_set_from_arrays",
]
