"""Semantic Inputs 统一推导（F10，ADR-0205 × ADR-0207 汇合点）.

方向 F10 的单一事实源接缝：**Dataset semantic contract（ADR-0207 的
``FieldSemantics``/``MeasurementKind``，11 值）→ grammar 测量词表
（``MEASUREMENT_KINDS``，8 值）→ ``data_kind``** 的推导只在本模块发生一次，
生产调用点（symbology 的全部入口）从这里取 ``(data_kind, measurement_kind)``
喂 ``resolve_symbology``——替代此前「#1480 与 #1488 两引擎各跑各、互不协调」
的面（recon O1–O4）。

判定优先级（先命中先得，全程留痕）：

1. ``explicit_measurement``（grammar 词表 pin，user-wins，source=explicit）；
2. Dataset semantic contract：``profile_semantics``（#1488 ``FieldSemantics``）
   非空 kind → 冻结 11→8 投影（``MEASUREMENT_KIND_TO_GRAMMAR``）；带符号率
   证据（name:rate + 样本双符号）在适配层升级 signed_change（不动 #1488 内部）；
3. #1480 ``infer_measurement_kind`` 结构证据补残（dtype 类别面、双符号值、
   强词素、低基数整数）——角色画像缺席时的证据面；
4. 兜底：data_kind=None、measurement_kind=""（诚实未知，resolver 保持缺省），
   绝不虚构。

保守降级（M8）：推导异常由调用方按「失败退缺省 + 披露」消费；本模块自身对
非法显式词 fail-closed（ValueError）。依赖方向：cartography → gis 单向；
``visual_variables`` 与 ``gis.measurement`` 内部零改动（各自契约测试保持）。
确定性：同输入恒同输出；无 IO、无时钟、无随机。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel, Field

from app.lib.cartography.visual_variables import (  # noqa: F401  (re-export)
    MEASUREMENT_KINDS,
    MeasurementDecision,
    _SIGNED_NEG_SHARE,
    derive_data_kind,
    infer_measurement_kind,
)

#: 契约版本（to_bounded_dict 的校验键；投影语义变化必须 bump）。
SEMANTIC_INPUTS_VERSION = 1

#: #1488 MeasurementKind（11 值）→ grammar MEASUREMENT_KINDS（8 值）冻结投影。
#: 制图学依据：count/absolute_quantity/ratio 的零点均有意义 → ratio；
#: density/percentage/rate 同为归一化率族 → rate；index/温度等原点任意合成量
#: → quantitative；category → nominal。未知值 fail-closed。
MEASUREMENT_KIND_TO_GRAMMAR: Dict[str, str] = {
    "count": "ratio",
    "absolute_quantity": "ratio",
    "ratio": "ratio",
    "rate": "rate",
    "density": "rate",
    "percentage": "rate",
    "index": "quantitative",
    "category": "nominal",
    "ordinal": "ordinal",
    "signed_change": "signed_change",
    "uncertainty": "uncertainty",
}

#: GrammarDecision/GrammarRequest 消费的 source 词表。
SEMANTIC_SOURCES: Tuple[str, ...] = (
    "explicit", "dataset_contract", "evidence", "fallback",
)

#: 有界值样本上限（与 gis.measurement.MAX_VALUE_SAMPLES 同值；投影层样本
#: 独立限额，防双重过滤口径漂移）。
_SAMPLE_CAP = 200


class SemanticInputs(BaseModel):
    """单字段语义推导工件（grammar/resolver 的共同输入，可序列化）。"""

    field: str
    measurement_kind: str = ""            # ∈ MEASUREMENT_KINDS；"" = 未知
    data_kind: Optional[str] = None       # DataKind | None（None = 无色族证据）
    source: str = "fallback"              # ∈ SEMANTIC_SOURCES
    confidence: str = "unknown"           # RoleConfidence.value 口径
    unit: str = ""                        # canonical unit 名（#1488 注册表键）
    legend_unit_display: str = ""         # 图例显示串（"" = 不提供）
    # ADR-0207 原词表 kind（11 值）——resolve_symbology 的
    # ``measurement_kind`` 入参消费此面（measurement_to_data_kind /
    # measurement_diverging_center 都以 MeasurementKind 为判定键）。grammar
    # 词表消费 ``measurement_kind``（8 值投影）。值证据 signed_change 亦
    # 回填此面（resolver 可置 diverging center 0）。
    contract_measurement_kind: str = ""
    evidence: List[str] = Field(default_factory=list)      # ≤6
    checks: List[Dict[str, str]] = Field(default_factory=list)  # ≤6
    reason_codes: List[str] = Field(default_factory=list)
    disclosures: List[str] = Field(default_factory=list)
    rejected_evidence: List[Dict[str, str]] = Field(default_factory=list)  # ≤4

    @property
    def has_kind(self) -> bool:
        return self.measurement_kind in MEASUREMENT_KINDS

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "version": SEMANTIC_INPUTS_VERSION,
            "field": self.field,
            "measurement_kind": self.measurement_kind,
            "data_kind": self.data_kind,
            "source": self.source,
            "confidence": self.confidence,
            "unit": self.unit,
            "legend_unit_display": self.legend_unit_display,
            "contract_measurement_kind": self.contract_measurement_kind,
            "evidence": list(self.evidence[:6]),
            "checks": [dict(c) for c in self.checks[:6]],
            "reason_codes": list(self.reason_codes[:12]),
            "disclosures": list(self.disclosures[:6]),
            "rejected_evidence": [dict(r) for r in self.rejected_evidence[:4]],
        }


def project_measurement_kind(kind_11: str) -> str:
    """#1488 词表 → grammar 词表（词表外 fail-closed ValueError）。"""
    try:
        return MEASUREMENT_KIND_TO_GRAMMAR[kind_11]
    except KeyError:
        raise ValueError(
            f"未知 MeasurementKind {kind_11!r}"
            f"（合法：{', '.join(MEASUREMENT_KIND_TO_GRAMMAR)}）") from None


def _finite_samples(values: Optional[Sequence[Any]]) -> List[float]:
    out: List[float] = []
    for v in values or []:
        if len(out) >= _SAMPLE_CAP:
            break
        if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v)):
            out.append(float(v))
    return out


def _neg_share(finite: List[float]) -> Optional[float]:
    if not finite:
        return None
    neg = sum(1 for v in finite if v < 0)
    if not neg:
        return None
    return neg / len(finite)


def _from_explicit(field: str, explicit: str) -> SemanticInputs:
    decision = infer_measurement_kind(field, explicit=explicit)
    return SemanticInputs(
        field=field,
        measurement_kind=decision.kind,
        data_kind=decision.data_kind,
        source="explicit",
        confidence="user_declared",
        evidence=list(decision.reasons[:6]),
        reason_codes=["GRAMMAR.MEAS.EXPLICIT"],
    )


def _from_dataset_contract(
    field: str,
    profile_semantics: Any,
    finite: List[float],
) -> SemanticInputs:
    """#1488 FieldSemantics → grammar 投影（含带符号率升级）。"""
    from app.lib.gis.measurement import legend_unit_display

    kind_11 = str(getattr(profile_semantics, "measurement_kind", "") or "")
    projected = project_measurement_kind(kind_11)
    codes = ["GRAMMAR.MEAS.DATASET_CONTRACT"]
    disclosures: List[str] = []
    evidence = [str(e) for e in (getattr(profile_semantics, "evidence", None) or [])[:5]]

    # 带符号率升级：#1488 判 rate（如「增长率」名证据），但值样本呈双符号
    # （负侧占比达阈值）——符号结构必须以 diverging 保序，否则正负语义丢失。
    if projected == "rate" and kind_11 == "rate":
        share = _neg_share(finite)
        if share is not None and share >= _SIGNED_NEG_SHARE:
            projected = "signed_change"
            codes.append("GRAMMAR.MEAS.SIGNED_RATE_ESCALATION")
            disclosures.append(
                f"率字段样本双符号（neg {share:.0%} ≥ {_SIGNED_NEG_SHARE:.0%}）"
                "——按带符号变化处理（diverging 族，中心 0）")

    data_kind: Optional[str]
    if projected == "uncertainty":
        # 与 measurement_to_data_kind 同口径：不确定度无色相族证据。
        data_kind = None
        codes.append("GRAMMAR.MEAS.UNCERTAINTY_NO_COLOR_FAMILY")
    else:
        data_kind = derive_data_kind(projected, field_name=field)

    checks = [dict(c) for c in (getattr(profile_semantics, "checks", None) or [])[:4]]
    unit = str(getattr(profile_semantics, "unit", "") or "")
    return SemanticInputs(
        field=field,
        measurement_kind=projected,
        data_kind=data_kind,
        source="dataset_contract",
        confidence=str(getattr(profile_semantics, "kind_confidence", "") or "unknown"),
        unit=unit,
        legend_unit_display=(legend_unit_display(unit) if unit else ""),
        contract_measurement_kind=kind_11,
        evidence=evidence,
        checks=checks,
        reason_codes=codes,
        disclosures=disclosures,
    )


def _from_value_evidence(
    field: str,
    *,
    dtype: str,
    finite: List[float],
    unique_count: Optional[int],
) -> SemanticInputs:
    """#1480 结构证据补残（角色画像缺席时的证据面）。"""
    decision: MeasurementDecision = infer_measurement_kind(
        field, dtype=dtype, values=finite, unique_count=unique_count)
    data_kind: Optional[str] = decision.data_kind
    codes = list(decision.reason_codes)
    # 值证据 signed_change（GRAMMAR.MEAS.VALUE_SIGNED）回填 ADR-0207 词表：
    # resolver 据此置 diverging center 0（名称缺席时符号结构不丢）。
    contract_kind = (
        "signed_change"
        if decision.kind == "signed_change"
        and "GRAMMAR.MEAS.VALUE_SIGNED" in codes
        else ""
    )
    if decision.kind == "uncertainty":
        data_kind = None
        codes.append("GRAMMAR.MEAS.UNCERTAINTY_NO_COLOR_FAMILY")
    return SemanticInputs(
        field=field,
        measurement_kind=decision.kind,
        data_kind=data_kind,
        source=decision.source if decision.source in SEMANTIC_SOURCES else "evidence",
        confidence=("rule_derived" if decision.source == "evidence" else "unknown"),
        contract_measurement_kind=contract_kind,
        evidence=list(decision.reasons[:6]),
        reason_codes=codes,
        rejected_evidence=[
            {"kind": str(r.get("kind", "")), "reason": str(r.get("reason", ""))}
            for r in list(decision.rejected)[:4]
        ],
    )


def derive_semantic_inputs(
    field: str,
    *,
    roles: Sequence[str] = (),
    value_samples: Optional[Sequence[Any]] = None,
    dtype: str = "number",
    has_temporal: bool = False,
    crs: str = "",
    unit_override: str = "",
    explicit_measurement: Optional[str] = None,
    profile_semantics: Any = None,
) -> SemanticInputs:
    """单字段语义统一推导（确定性纯函数；grammar/resolver 共同输入）。

    ``profile_semantics``：#1488 ``FieldSemantics``（调用方已从 DatasetProfile
    ⊕ SemanticDatasetProfile 派生时直接传入，避免重复推导）；缺省且 ``roles``
    非空时就地派生一次。``roles``/``has_temporal``/``crs``/``unit_override``
    仅服务于该就地派生。

    失败语义：显式 pin 非法词表 ValueError（fail-closed）；其余任何异常由
    调用方按「退缺省 + 披露」消费（保守降级契约，见模块 docstring）。
    """
    name = str(field or "")
    if explicit_measurement is not None:
        if explicit_measurement not in MEASUREMENT_KINDS:
            raise ValueError(
                f"explicit 测量语义 {explicit_measurement!r} 非法"
                f"（合法：{', '.join(MEASUREMENT_KINDS)}）")
        return _from_explicit(name, explicit_measurement)

    finite = _finite_samples(value_samples)

    # Dataset semantic contract 优先；缺席且给足上下文时就地派生一次。
    contract = profile_semantics
    if contract is None and roles:
        try:
            from app.lib.gis.measurement import derive_field_semantics

            contract = derive_field_semantics(
                name, roles,
                value_samples=value_samples,
                has_temporal=has_temporal,
                crs=crs,
                unit_override=unit_override,
            )
        except Exception:  # noqa: BLE001 — 画像派生失败落回值证据面
            contract = None

    if contract is not None and str(getattr(contract, "measurement_kind", "") or ""):
        return _from_dataset_contract(name, contract, finite)

    out = _from_value_evidence(name, dtype=dtype, finite=finite, unique_count=None)
    if out.source == "fallback":
        out.checks.append({
            "code": "GRAMMAR.MEAS.INSUFFICIENT_EVIDENCE",
            "detail": "无画像角色、无词素命中且无有限值样本——保守缺省（不虚构语义）",
        })
    return out


__all__ = [
    "SEMANTIC_INPUTS_VERSION",
    "MEASUREMENT_KIND_TO_GRAMMAR",
    "SEMANTIC_SOURCES",
    "SemanticInputs",
    "project_measurement_kind",
    "derive_semantic_inputs",
]
