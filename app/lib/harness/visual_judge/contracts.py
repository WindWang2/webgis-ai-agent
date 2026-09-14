"""VLM Visual Critic Runtime 契约层（ADR-0185 D2/D3/D6）。

全部模型 ``extra="forbid"``（Pydantic 严格模式）：白名单外字段整条拒收——
契约层本身就是改图意图消毒的第一道闸（第二道在 critic_engine 的
``_FORBIDDEN_KEYS`` 判废，与 ADR-0158 legacy seam 同纪律）。

维度白名单（与 legacy ``visual_evaluator.VISUAL_DIMENSIONS`` 共存不混用）：
readability / color_discriminability / composition_balance /
information_density / spatial_alignment。``polish_completeness`` 由确定性
规则覆盖，不进视觉裁判 v2 白名单。

评分语义（D6）：``VisualDimensionScore`` 由引擎从 critiques 确定性推导，
不采信 VLM 自报维度分——VLM 只描述看见了什么，评分权在引擎。
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

#: 严格模式基类：未知字段一律拒收（VLM 幻觉字段/改图意图进不来）。
_STRICT = ConfigDict(extra="forbid")


class VisualDimension(str, Enum):
    """视觉评审 5 维白名单（严格枚举，ADR-0185 D2）。"""

    READABILITY = "readability"
    COLOR_DISCRIMINABILITY = "color_discriminability"
    COMPOSITION_BALANCE = "composition_balance"
    INFORMATION_DENSITY = "information_density"
    SPATIAL_ALIGNMENT = "spatial_alignment"


Severity = Literal["info", "warning", "error"]
_MAX_TEXT = 300
_MAX_DEFECT_TYPE = 60


class VisualBBox(BaseModel):
    """图面坐标定位：``[ymin, xmin, ymax, xmax]``，百分比（0–100，左上原点）。

    接受 4 元序列输入（VLM JSON 的自然形状）；值域/次序非法在契约层抛
    ``ValidationError``——engine 侧对非法 bbox 置空而非整条判废（定位是
    增值信息，不是门禁，ADR-0185 D2）。
    """

    model_config = _STRICT

    ymin: float = Field(ge=0.0, le=100.0)
    xmin: float = Field(ge=0.0, le=100.0)
    ymax: float = Field(ge=0.0, le=100.0)
    xmax: float = Field(ge=0.0, le=100.0)

    @model_validator(mode="before")
    @classmethod
    def _accept_sequence(cls, data: Any) -> Any:
        if isinstance(data, (list, tuple)):
            if len(data) != 4 or not all(
                isinstance(v, (int, float)) and not isinstance(v, bool) for v in data
            ):
                raise ValueError("bbox must be 4 numbers [ymin, xmin, ymax, xmax]")
            data = dict(zip(("ymin", "xmin", "ymax", "xmax"), (float(v) for v in data)))
        return data

    @model_validator(mode="after")
    def _ordered(self) -> "VisualBBox":
        if self.ymax <= self.ymin or self.xmax <= self.xmin:
            raise ValueError("bbox must satisfy ymax > ymin and xmax > xmin")
        return self

    def to_list(self) -> List[float]:
        return [self.ymin, self.xmin, self.ymax, self.xmax]


def _truncate(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[:limit]


class VisualCritiqueItem(BaseModel):
    """一条结构化视觉批评（evidence_class = visual）。

    ``confidence`` 缺失/非法按契约层拒收（engine 消毒时兜底 0.0——低置信
    不许冒充高置信背书，D6）。bool 不是合法数值（``True`` ≠ 1.0）。
    """

    model_config = _STRICT

    dimension: VisualDimension
    severity: Severity = "warning"
    confidence: float = Field(ge=0.0, le=1.0)
    suggestion: str
    evidence: str = ""
    bbox: Optional[VisualBBox] = None
    defect_type: str = ""

    @field_validator("confidence", mode="before")
    @classmethod
    def _reject_bool_confidence(cls, v: Any) -> Any:
        # pydantic 会把 True 宽松强转成 1.0 —— 在强转前拦截（bool ≠ 置信度）。
        if isinstance(v, bool):
            raise ValueError("confidence must be a number, not bool")
        return v

    @field_validator("suggestion")
    @classmethod
    def _bound_suggestion(cls, v: str) -> str:
        return _truncate(v, _MAX_TEXT)

    @field_validator("evidence")
    @classmethod
    def _bound_evidence(cls, v: str) -> str:
        return _truncate(v, _MAX_TEXT)

    @field_validator("defect_type")
    @classmethod
    def _bound_defect_type(cls, v: str) -> str:
        return _truncate(v, _MAX_DEFECT_TYPE)


class VisualDimensionScore(BaseModel):
    """单维度量化分（引擎推导，0–10）。"""

    model_config = _STRICT

    dimension: VisualDimension
    score: float = Field(ge=0.0, le=10.0)
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = ""

    @field_validator("rationale")
    @classmethod
    def _bound_rationale(cls, v: str) -> str:
        return _truncate(v, _MAX_TEXT)


def _dimension_scores_for(
    status: str, dimension_scores: Sequence[VisualDimensionScore]
) -> Sequence[VisualDimensionScore]:
    if status != "evaluated":
        return []
    if len(dimension_scores) != len(VisualDimension):
        raise ValueError(
            "evaluated report requires exactly %d dimension_scores, got %d"
            % (len(VisualDimension), len(dimension_scores))
        )
    return list(dimension_scores)


class VisualJudgeReport(BaseModel):
    """一次视觉评审的诚实结论（ADR-0185 D3 fail-closed 矩阵的载体）。

    不变式：
    - ``not_evaluated`` ⇒ 必携带机器可读 ``reason``，且不得携带任何批评；
    - ``evaluated`` ⇒ ``reason`` 为空、``dimension_scores`` 恰 5 条；
    - ``source`` 恒 ``"visual_judge"``——L5 ``derive_goal_satisfaction`` 消费面
      零改动；运行时代际以 ``runtime`` 键披露。
    """

    model_config = _STRICT

    status: Literal["evaluated", "not_evaluated"]
    reason: str = ""
    critiques: List[VisualCritiqueItem] = Field(default_factory=list)
    dimension_scores: List[VisualDimensionScore] = Field(default_factory=list)
    overall_score: float = Field(default=0.0, ge=0.0, le=10.0)
    overall_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    session_id: str = ""
    mapspec_fingerprint: str = ""
    image_sha256: str = ""
    image_width: int = Field(default=0, ge=0)
    image_height: int = Field(default=0, ge=0)
    provider: str = ""
    model: str = ""
    duration_ms: int = Field(default=0, ge=0)
    mode: str = "record_only"
    pre_screen: Dict[str, Any] = Field(default_factory=dict)
    runtime: str = "visual_critic_v2"
    source: str = "visual_judge"

    @model_validator(mode="after")
    def _consistent(self) -> "VisualJudgeReport":
        if self.status == "not_evaluated":
            if not self.reason:
                raise ValueError("not_evaluated report requires a machine-readable reason")
            if self.critiques:
                raise ValueError("not_evaluated report must not carry critiques")
            self.dimension_scores = []
        else:
            if self.reason:
                raise ValueError("evaluated report must carry empty reason")
            _dimension_scores_for("evaluated", self.dimension_scores)
        return self

    @property
    def evaluated(self) -> bool:
        return self.status == "evaluated"

    @property
    def has_blocking_defects(self) -> bool:
        return any(c.severity == "error" for c in self.critiques)

    @property
    def error_count(self) -> int:
        return sum(1 for c in self.critiques if c.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for c in self.critiques if c.severity == "warning")

    def to_summary(self) -> Dict[str, Any]:
        """有界摘要（进 ``cartography.visual_evidence``）。

        legacy 全键（status/reason/mode/critiques/error_count/fingerprint/
        screenshot_digest/duration_ms/model/source）语义不变 + v2 增列
        （dimension_scores/overall_score/overall_confidence/pre_screen/
        provider/runtime）。
        """
        return {
            "evidence_class": "visual",
            "source": self.source,
            "runtime": self.runtime,
            "status": self.status,
            "reason": self.reason if not self.evaluated else "",
            "mode": self.mode,
            "critiques": [
                {
                    "dimension": c.dimension.value,
                    "severity": c.severity,
                    "suggestion": c.suggestion,
                    "evidence_class": "visual",
                    "confidence": c.confidence,
                    "evidence": c.evidence,
                    "bbox": c.bbox.to_list() if c.bbox else None,
                    "defect_type": c.defect_type,
                }
                for c in self.critiques
            ],
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "dimension_scores": [
                {
                    "dimension": s.dimension.value,
                    "score": round(s.score, 2),
                    "confidence": round(s.confidence, 3),
                    "rationale": s.rationale,
                }
                for s in self.dimension_scores
            ],
            "overall_score": round(self.overall_score, 2),
            "overall_confidence": round(self.overall_confidence, 3),
            "pre_screen": dict(self.pre_screen),
            "provider": self.provider[:40],
            "fingerprint": self.mapspec_fingerprint[:80],
            "screenshot_digest": self.image_sha256[:32],
            "duration_ms": self.duration_ms,
            "model": self.model[:80],
        }

    @classmethod
    def skipped(
        cls,
        reason: str,
        *,
        session_id: str = "",
        mapspec_fingerprint: str = "",
        image_sha256: str = "",
        image_width: int = 0,
        image_height: int = 0,
        provider: str = "",
        model: str = "",
        mode: str = "record_only",
        pre_screen: Optional[Dict[str, Any]] = None,
    ) -> "VisualJudgeReport":
        """fail-closed 工厂：not_evaluated + 机器可读原因（D3 矩阵）。"""
        return cls(
            status="not_evaluated",
            reason=reason,
            session_id=session_id,
            mapspec_fingerprint=mapspec_fingerprint,
            image_sha256=image_sha256,
            image_width=image_width,
            image_height=image_height,
            provider=provider,
            model=model,
            mode=mode,
            pre_screen=pre_screen or {},
        )


def safe_bbox(value: Any) -> Optional[VisualBBox]:
    """VLM bbox 消毒：非法 ⇒ None（批评保留，定位弃置，D2）。"""
    if value is None:
        return None
    try:
        return VisualBBox.model_validate(value)
    except (ValidationError, ValueError):
        return None


__all__ = [
    "Severity",
    "VisualBBox",
    "VisualCritiqueItem",
    "VisualDimension",
    "VisualDimensionScore",
    "VisualJudgeReport",
    "safe_bbox",
]
