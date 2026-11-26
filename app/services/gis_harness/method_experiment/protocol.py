"""实验评价协议（method-experiment WP2/WP4）——「怎么算公平」的可版本化
声明。

职责：

- 固定折分配族（复用 ``geo_analysis/cv.py`` splitter 词表——空间分块/
  索引/时间前向，零新 splitter）、折数、抽样上限、指标清单与方向裁决
  所需的全部口径事实；
- 协议指纹进试验内容 hash：**协议变了就是不同实验**（同一候选在两个
  协议下的指标不可互相替代）；
- kind 决定指标语义（cards.PROTOCOL_KINDS）：cv=预测对真值、
  stability=多 run 一致性、conservation=解析守恒、scenario=受控情景。
  没有真值的族**绝不产生 RMSE**（kind=conservation/scenario 的指标集
  由卡声明，编译期对账）。

红线：协议不执行任何计算；执行真相在 runner 经真实工具 dispatch。
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, Tuple

from pydantic import BaseModel, Field, field_validator

from app.services.gis_harness.method_experiment.cards import (
    EXPERIMENT_FAMILY_IDS,
    PROTOCOL_KINDS,
)
from app.services.gis_harness.method_experiment.metrics import (
    METRIC_IDS,
    metric_exists,
    metrics_for_family,
)

#: 折分配词表（唯一事实源 = cv.py 的 splitter 语义；此处只是引用）。
CV_SCHEMES = ("index", "spatial_block", "temporal_forward")

#: 折数界（cv.py 同界：2..10；过大折=每折样本过少，统计无意义）。
MIN_FOLDS = 2
MAX_FOLDS = 10

#: 单候选 CV 评估的样本上限（大数据诚实下采样并披露——不是静默全跑）。
MAX_CV_SAMPLES = 20_000

#: 聚类稳定性重复次数界（bootstrap/seed 重复）。
MIN_STABILITY_RUNS = 3
MAX_STABILITY_RUNS = 10


class EvaluationProtocol(BaseModel):
    """一个实验的评价协议（可序列化；进计划与试验指纹）。"""

    schema_version: int = 1
    kind: str                                   # ⊆ PROTOCOL_KINDS
    family_id: str                              # ⊆ EXPERIMENT_FAMILY_IDS
    scheme: str = ""                            # cv 族：⊆ CV_SCHEMES
    folds: int = 5                              # cv 族有效
    stability_runs: int = 5                     # stability 族有效
    max_samples: int = MAX_CV_SAMPLES           # CV 评估样本上限（下采样披露）
    metrics: Tuple[str, ...] = ()               # ⊆ METRIC_IDS（族适用）
    notes: Tuple[str, ...] = Field(default_factory=tuple)

    @field_validator("kind")
    @classmethod
    def _kind_vocab(cls, v: str) -> str:
        if v not in PROTOCOL_KINDS:
            raise ValueError(f"protocol kind 越界: {v}（词表: {PROTOCOL_KINDS}）")
        return v

    @field_validator("family_id")
    @classmethod
    def _family_vocab(cls, v: str) -> str:
        if v not in EXPERIMENT_FAMILY_IDS:
            raise ValueError(f"family 越界: {v}")
        return v

    @field_validator("folds")
    @classmethod
    def _folds_bound(cls, v: int) -> int:
        if not (MIN_FOLDS <= v <= MAX_FOLDS):
            raise ValueError(f"folds 越界 [{MIN_FOLDS},{MAX_FOLDS}]: {v}")
        return v

    @field_validator("stability_runs")
    @classmethod
    def _runs_bound(cls, v: int) -> int:
        if not (MIN_STABILITY_RUNS <= v <= MAX_STABILITY_RUNS):
            raise ValueError(
                f"stability_runs 越界 [{MIN_STABILITY_RUNS},{MAX_STABILITY_RUNS}]: {v}")
        return v

    @field_validator("max_samples")
    @classmethod
    def _samples_bound(cls, v: int) -> int:
        if v < 10:
            raise ValueError(f"max_samples 过小（<10 无评估意义）: {v}")
        return min(v, MAX_CV_SAMPLES)

    @field_validator("scheme")
    @classmethod
    def _scheme_vocab(cls, v: str) -> str:
        if v and v not in CV_SCHEMES:
            raise ValueError(f"cv scheme 越界: {v}（词表: {CV_SCHEMES}）")
        return v

    def fingerprint(self) -> str:
        """协议内容指纹（canonical JSON sha256；schema 版本进指纹）。"""
        payload = {
            "schema": self.schema_version,
            "kind": self.kind,
            "family": self.family_id,
            "scheme": self.scheme,
            "folds": self.folds,
            "runs": self.stability_runs,
            "max_samples": self.max_samples,
            "metrics": list(self.metrics),
        }
        from app.services.gis_harness.analysis_workflows.contracts import (
            canonical_json,
        )
        return hashlib.sha256(
            canonical_json(payload).encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "family_id": self.family_id,
            "scheme": self.scheme,
            "folds": self.folds,
            "stability_runs": self.stability_runs,
            "max_samples": self.max_samples,
            "metrics": list(self.metrics),
            "notes": list(self.notes),
            "fingerprint": self.fingerprint(),
        }


def build_protocol_from_card(
    family_id: str,
    *,
    scheme: str = "",
    folds: int = 5,
    stability_runs: int = 5,
    metric_ids: Tuple[str, ...] = (),
) -> EvaluationProtocol:
    """从方法卡构建默认协议（卡是审定默认；显式参数可收窄不可越界）。

    fail-closed：metric 越界 / 非族适用 / cv 族 scheme 缺失 → ValueError
    （plan 编译期拒绝，不静默修正）。
    """
    from app.services.gis_harness.method_experiment.cards import get_card

    card = get_card(family_id)
    if card is None:
        raise ValueError(f"未知方法族: {family_id}")

    metrics = tuple(metric_ids) if metric_ids else tuple(card.default_metrics)
    if not metrics:
        raise ValueError(f"族 {family_id} 协议无指标（卡默认缺失，拒绝编译）")
    applicable = {m.metric_id for m in metrics_for_family(family_id)}
    for m in metrics:
        if not metric_exists(m):
            raise ValueError(f"未知指标: {m}（词表: {METRIC_IDS}）")
        if m not in applicable:
            raise ValueError(
                f"指标 {m} 不适用于族 {family_id}"
                f"（适用: {sorted(applicable)}）——不同族的指标不可混编")

    kind = card.protocol_kind
    use_scheme = scheme or card.default_scheme
    if kind == "cv":
        if use_scheme not in CV_SCHEMES:
            raise ValueError(
                f"cv 族折分配方案缺失或越界（词表: {CV_SCHEMES}），"
                f"got {use_scheme!r}")
        if use_scheme == "temporal_forward":
            # 时间前向链需要时间字段——v1 卡不含时间输入角色，显式拒绝
            # 比静默按 index 退化诚实。
            raise ValueError(
                "temporal_forward 需要 time 字段绑定——当前插值卡不支持；"
                "用 spatial_block 或 index")
    else:
        use_scheme = ""                      # 非 cv 族不带折语义

    return EvaluationProtocol(
        kind=kind, family_id=family_id, scheme=use_scheme,
        folds=folds if kind == "cv" else 5,
        stability_runs=stability_runs if kind == "stability" else 5,
        metrics=metrics,
        notes=tuple(card.comparability_notes),
    )
