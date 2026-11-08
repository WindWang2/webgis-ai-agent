"""简报事实核验（G11 / BRF-02）——确定性规则，positive-proof 纪律。

自由文本中的数字不经此模块；只有结构化 ``BriefFact`` 可被核验。核验
不改动权威值：对不上就是 warning/unverified，绝不"顺手改对"。

证据解析输入由调用方（服务壳）以只读投影提供：
- ``records``：ref → ArtifactRecord.to_dict() 投影（状态/单位/revision）；
- ``payloads``：ref → 权威载荷（{chart|table} 形状；None=不可达）。

裁决词表：
- verified   —— 值与权威字段一致（±REL_TOL）且单位一致且来源新鲜；
- warning    —— 有证据但存在缺口（单位缺失/不一致、来源 stale、
                复算值与陈述值漂移超容忍但同数量级、基期口径不同）；
- unverified —— 无正向证据（ref 不可达/无记录/字段缺失/权限失败
                —— 失败封闭，不猜测）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from app.lib.storymap.brief_spec import BriefFact, EvidenceBinding

#: 相对容忍（浮点聚合的确定性重算漂移界）。
REL_TOL = 1e-9
#: 绝对容忍（值为 0 时相对容忍失效，用绝对界）。
ABS_TOL = 1e-12
#: warning 级漂移上限（复算值与陈述值相对差超过此值 → unverified）。
DRIFT_UNVERIFIED = 0.05

#: 产物台账的有效状态（stale/superseded/expired → warning 而非 fail，
#: 因为旧值仍是"曾经为真"的陈述 —— 需披露而非删改）。
_RECORD_LIVE_STATUS = "valid"


@dataclass
class FactCheckOutcome:
    """单条事实的核验裁决（含可读原因，供预检/侧栏展示）。"""

    fact_id: str
    status: str  # verified | warning | unverified
    reasons: List[str] = field(default_factory=list)
    #: 复算链路披露（numerator/denominator/baseline 的取值快照）。
    recompute: Dict[str, float] = field(default_factory=dict)


def resolve_field_path(payload: Any, path: str) -> Tuple[Optional[float], str]:
    """按点分路径取数值：``rows.2.count`` → payload["rows"][2]["count"]。

    返回 (value, reason)；失败时 value=None 且 reason 说明缺口。只接受
    有限数值（bool 不算数值 —— 防止 True 顶替 1）。
    """
    if not path:
        return None, "field_path missing"
    node = payload
    walked: List[str] = []
    for segment in path.split("."):
        if not segment:
            return None, f"empty segment in field_path at {'.'.join(walked)}"
        if isinstance(node, Mapping):
            if segment not in node:
                return None, f"key {segment!r} not found at {'.'.join(walked) or 'root'}"
            node = node[segment]
        elif isinstance(node, (list, tuple)):
            try:
                index = int(segment)
            except ValueError:
                return None, f"non-integer index {segment!r} for list at {'.'.join(walked)}"
            if not (0 <= index < len(node)):
                return None, f"index {index} out of range at {'.'.join(walked)}"
            node = node[index]
        else:
            return None, f"cannot descend into scalar at {'.'.join(walked)}"
        walked.append(segment)
    if isinstance(node, bool) or not isinstance(node, (int, float)):
        return None, f"field {'.'.join(walked)} is not numeric"
    try:
        number = float(node)
    except (TypeError, ValueError, OverflowError):
        return None, f"field {'.'.join(walked)} not convertible to number"
    if not math.isfinite(number):
        return None, f"field {'.'.join(walked)} is not finite"
    return number, ""


def _values_close(actual: float, expected: float) -> bool:
    if expected == 0.0:
        return abs(actual - expected) <= ABS_TOL
    return abs(actual - expected) <= max(REL_TOL, abs(expected) * REL_TOL)


def _recompute_fact(
    fact: BriefFact,
    fact_values: Mapping[str, float],
) -> Tuple[Optional[float], Dict[str, float], List[str]]:
    """按依赖事实复算派生值（ratio/rate/change…）。None=无法复算。"""
    notes: List[str] = []
    snapshot: Dict[str, float] = {}
    den_id = fact.denominator_fact_id
    num_id = fact.numerator_fact_id
    base_id = fact.baseline_fact_id
    den = fact_values.get(den_id) if den_id else None
    num = fact_values.get(num_id) if num_id else None
    base = fact_values.get(base_id) if base_id else None
    if den_id:
        if den is None:
            notes.append(f"denominator fact {den_id} value unavailable")
        elif den == 0.0:
            notes.append(f"denominator fact {den_id} is zero")
        else:
            snapshot["denominator"] = den
    if num_id:
        if num is None:
            notes.append(f"numerator fact {num_id} value unavailable")
        else:
            snapshot["numerator"] = num
    if base_id:
        if base is None:
            notes.append(f"baseline fact {base_id} value unavailable")
        else:
            snapshot["baseline"] = base

    if fact.kind in ("ratio", "percentage", "share", "rate"):
        if den in (None, 0.0) or num is None:
            return None, snapshot, notes
        return num / den, snapshot, notes
    if fact.kind == "change":
        # change 语义：相对变化率（+20% → 0.2）= (after - before) / before。
        if base in (None, 0.0) or num is None:
            return None, snapshot, notes
        return (num - base) / base, snapshot, notes
    if fact.kind == "difference":
        if base is None or num is None:
            return None, snapshot, notes
        return num - base, snapshot, notes
    return None, snapshot, notes


#: 派生类事实：复算链（分子/分母/基期）本身可充当正向证明。
_DERIVED_KINDS = frozenset(
    {"ratio", "percentage", "share", "rate", "change", "difference"})


def check_fact(
    fact: BriefFact,
    *,
    records: Mapping[str, Mapping[str, Any]],
    payloads: Mapping[str, Any],
    fact_values: Mapping[str, float],
) -> FactCheckOutcome:
    """单条事实核验：resolve → value → recompute → unit → freshness。

    派生类（ratio/rate/change/difference）允许无自身绑定 —— 依赖事实的
    复算命中即正向证明；非派生类无绑定 → unverified（evidence-first）。
    """
    reasons: List[str] = []
    binding = fact.basis
    ref = binding.artifact_ref
    derived = fact.kind in _DERIVED_KINDS

    # 1. 引用解析（失败封闭 —— 不可达即披露；派生类仍可走复算通道）。
    record = records.get(ref)
    payload = payloads.get(ref)
    if not ref and not derived:
        return FactCheckOutcome(
            fact.fact_id, "unverified", ["no artifact binding"])
    if ref and record is None:
        if not derived:
            return FactCheckOutcome(
                fact.fact_id, "unverified",
                [f"artifact {ref} not registered in this session"])
        reasons.append(f"artifact {ref} not registered in this session")
    elif ref and payload is None:
        if not derived:
            return FactCheckOutcome(
                fact.fact_id, "unverified",
                [f"artifact {ref} payload unavailable (permission or expired)"])
        reasons.append(
            f"artifact {ref} payload unavailable — field proof skipped")

    # 2. 权威值定位与比对（field_path 缺失 = 无法正向证明）。
    # 派生类无自身绑定（无 ref/payload）时跳过值定位 —— 复算通道兜底。
    value_ok = False
    authoritative: Optional[float] = None
    if not ref or payload is None:
        pass  # 绑定不可用：已在第 1 步披露；派生类继续走复算。
    elif not binding.field_path:
        reasons.append("field_path missing — cannot positively verify value")
    else:
        authoritative, reason = resolve_field_path(payload, binding.field_path)
        if authoritative is None:
            reasons.append(f"authoritative value unresolved: {reason}")
        elif not _values_close(fact.value, authoritative):
            drift = abs(fact.value - authoritative)
            relative = drift / abs(authoritative) if authoritative else drift
            if relative > DRIFT_UNVERIFIED:
                reasons.append(
                    f"value {fact.value!r} contradicts authoritative "
                    f"{authoritative!r} at {binding.field_path}")
                return FactCheckOutcome(
                    fact.fact_id, "unverified", reasons)
            reasons.append(
                f"value drifts from authoritative {authoritative!r} "
                f"by {drift:.6g} (within warning bound)")
        else:
            value_ok = True

    # 3. 派生复算（分母/基期义务 —— BRF-O02 的"20% 必须有可比两期"）。
    recompute: Dict[str, float] = {}
    derived_ok = False
    if derived:
        expected, recompute, notes = _recompute_fact(fact, fact_values)
        for note in notes:
            reasons.append(note)
        if expected is not None:
            if not _values_close(fact.value, expected):
                drift = abs(fact.value - expected)
                relative = drift / abs(expected) if expected else drift
                if relative > DRIFT_UNVERIFIED:
                    reasons.append(
                        f"derived value {fact.value!r} contradicts "
                        f"recomputed {expected!r}")
                    return FactCheckOutcome(
                        fact.fact_id, "unverified", reasons)
                reasons.append(
                    f"derived value drifts from recomputed {expected!r}")
            else:
                derived_ok = True

    # 4/5. 单位一致性与新鲜度（仅在绑定产物在场时评估；record None 安全跳过）。
    if record is not None:
        record_unit = str((record.get("metadata") or {}).get("unit") or "")
        if record_unit and fact.unit and record_unit != fact.unit:
            reasons.append(
                f"unit mismatch: brief says {fact.unit!r}, artifact says "
                f"{record_unit!r}")
        elif not record_unit and fact.unit:
            reasons.append(
                "artifact unit metadata missing — unit asserted by brief")
        status = str(record.get("status") or "")
        if status and status != _RECORD_LIVE_STATUS:
            reasons.append(
                f"artifact status is {status!r} (source may be outdated)")

    # 裁决（positive-proof 纪律）：
    # - 无任何正向证据（值未定位且复算未成立）→ unverified；
    # - 有正向证据且无缺口 → verified；有缺口 → warning。
    has_positive_proof = value_ok or derived_ok
    if not has_positive_proof:
        final = "unverified"
    elif reasons:
        final = "warning"
    else:
        final = "verified"
    return FactCheckOutcome(fact.fact_id, final, reasons, recompute)


def check_facts(
    facts: Sequence[BriefFact],
    *,
    records: Mapping[str, Mapping[str, Any]],
    payloads: Mapping[str, Any],
) -> List[FactCheckOutcome]:
    """整组核验。依赖事实的取值以"陈述值"进入复算（口径一致性检查），
    依赖事实自身 unverified 不阻断复算，但会以 reason 披露。"""
    values = {f.fact_id: f.value for f in facts}
    return [
        check_fact(f, records=records, payloads=payloads, fact_values=values)
        for f in facts
    ]


def apply_outcomes(
    facts: List[BriefFact], outcomes: Sequence[FactCheckOutcome]
) -> List[BriefFact]:
    """把核验裁决写回事实（status + status_reason）。返回新列表。"""
    by_id = {o.fact_id: o for o in outcomes}
    updated: List[BriefFact] = []
    for fact in facts:
        outcome = by_id.get(fact.fact_id)
        if outcome is None:
            updated.append(fact.model_copy(update={
                "status": "unverified", "status_reason": "not checked"}))
            continue
        updated.append(fact.model_copy(update={
            "status": outcome.status,  # type: ignore[arg-type]
            "status_reason": "; ".join(outcome.reasons)[:500],
        }))
    return updated


__all__ = [
    "FactCheckOutcome",
    "REL_TOL",
    "ABS_TOL",
    "DRIFT_UNVERIFIED",
    "resolve_field_path",
    "check_fact",
    "check_facts",
    "apply_outcomes",
]
