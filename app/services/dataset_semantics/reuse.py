"""Dataset Semantics Reuse —— 指纹对账 → 诚实复用裁决（ADR-0215 D6/D7）。

MapSpec 重放 / context restore / 结论复用的统一问句："当时用的数据语义
版本，和现在的一样吗？"

- 同指纹 → ``valid``（可安全复用）；
- 异指纹且双方 descriptor 在场 → ``compare_descriptors`` 的 verdict +
  字段级 reason codes（变了什么可解释）；
- 异指纹但只有指纹证据 → ``recompute``（保守）；
- 任一缺席/损坏 → ``unknown``（绝不把「不知道」当「没变」，也绝不虚构
  staleness）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from app.lib.gis.dataset_descriptor import (
    CODE_MISSING,
    CODE_STORE_CORRUPT,
    CODE_UNCHANGED,
    CODE_UNCOMPARABLE,
    DescriptorDelta,
    compare_descriptors,
)
from app.services.dataset_semantics.store import (
    DescriptorRecord,
    STATUS_OK,
)

VERDICT_VALID = "valid"
VERDICT_STALE = "stale"
VERDICT_RECOMPUTE = "recompute"
VERDICT_UNKNOWN = "unknown"

# record.status → verdict 的保守映射（fail-closed：读不回证据就不得宣称可复用）。
_RECORD_STATUS_VERDICT = {
    "missing": VERDICT_UNKNOWN,
    "corrupt": VERDICT_UNKNOWN,
    "unsupported": VERDICT_RECOMPUTE,
}


@dataclass
class ReuseDecision:
    """复用裁决（verdict + 稳定 reason codes + 可选 delta）。"""

    verdict: str
    reason_codes: List[str] = field(default_factory=list)
    recorded_fingerprint: str = ""
    current_fingerprint: str = ""
    delta: Optional[DescriptorDelta] = None

    @property
    def safely_reusable(self) -> bool:
        return self.verdict == VERDICT_VALID

    def to_bounded_dict(self) -> dict:
        out = {
            "verdict": self.verdict,
            "reason_codes": list(self.reason_codes)[:16],
            "recorded_fingerprint": self.recorded_fingerprint[:96],
            "current_fingerprint": self.current_fingerprint[:96],
        }
        if self.delta is not None:
            out["delta"] = self.delta.to_bounded_dict()
        return out


def _from_record(record: Optional[DescriptorRecord]) -> ReuseDecision:
    """store 读失败 → 诚实裁决（缺失=unknown；损坏=unknown；不支持=recompute）。"""
    if record is None:
        return ReuseDecision(verdict=VERDICT_UNKNOWN, reason_codes=[CODE_MISSING])
    verdict = _RECORD_STATUS_VERDICT.get(record.status, VERDICT_UNKNOWN)
    codes = [record.reason_code or CODE_MISSING]
    if record.status != STATUS_OK and record.reason_code == CODE_STORE_CORRUPT:
        codes = [CODE_STORE_CORRUPT]
    return ReuseDecision(verdict=verdict, reason_codes=codes,
                         recorded_fingerprint="", current_fingerprint=record.fingerprint)


def evaluate_reuse(
    recorded_fingerprint: Optional[str],
    current: Optional[DescriptorRecord],
) -> ReuseDecision:
    """recorded（MapSpec/context 记录的指纹）vs current（store 现读）→ 裁决。"""
    if current is not None and current.status != STATUS_OK:
        decision = _from_record(current)
        decision.recorded_fingerprint = str(recorded_fingerprint or "")[:96]
        return decision
    current_fp = current.descriptor.descriptor_fingerprint if current else ""
    recorded = str(recorded_fingerprint or "")
    if not current_fp:
        return ReuseDecision(verdict=VERDICT_UNKNOWN, reason_codes=[CODE_MISSING],
                             recorded_fingerprint=recorded[:96])
    if not recorded:
        # 记录面没有指纹（旧 MapSpec/旧 session）—— 不是"没变"，
        # 但也不必强制重算：诚实 unknown，由消费面披露不可对账。
        return ReuseDecision(verdict=VERDICT_UNKNOWN, reason_codes=[CODE_MISSING],
                             current_fingerprint=current_fp[:96])
    if recorded == current_fp:
        return ReuseDecision(verdict=VERDICT_VALID, reason_codes=[CODE_UNCHANGED],
                             recorded_fingerprint=recorded[:96],
                             current_fingerprint=current_fp[:96])
    # 指纹不同：只有当前版本在场 → 保守 recompute（历史回查走
    # evaluate_reuse_with_history，可拿到精确字段级 delta）。
    return ReuseDecision(
        verdict=VERDICT_RECOMPUTE,
        reason_codes=["DESCRIPTOR_FINGERPRINT_MISMATCH", CODE_UNCOMPARABLE],
        recorded_fingerprint=recorded[:96],
        current_fingerprint=current_fp[:96],
    )


async def evaluate_reuse_with_history(
    recorded_fingerprint: str,
    current: DescriptorRecord,
    *,
    store,
    session_id: str,
    dataset_key: str,
) -> ReuseDecision:
    """异指纹时回查版本历史：recorded 仍在保留期内 → 精确 delta。

    store 参数为 DatasetSemanticStore（避免环 import 用 duck typing）。
    """
    base = evaluate_reuse(recorded_fingerprint, current)
    if base.verdict != VERDICT_RECOMPUTE or current is None or not current.ok:
        return base
    if not current.descriptor:
        return base
    old_record = await store.get_by_fingerprint(
        session_id, dataset_key, recorded_fingerprint)
    if old_record.ok and old_record.descriptor is not None:
        delta = compare_descriptors(old_record.descriptor, current.descriptor)
        return ReuseDecision(
            verdict=_delta_verdict(delta),
            reason_codes=list(delta.reason_codes),
            recorded_fingerprint=recorded_fingerprint[:96],
            current_fingerprint=(current.descriptor.descriptor_fingerprint or "")[:96],
            delta=delta,
        )
    return base


def _delta_verdict(delta: DescriptorDelta) -> str:
    mapping = {
        "valid": VERDICT_VALID,
        "stale": VERDICT_STALE,
        "recompute": VERDICT_RECOMPUTE,
        "invalid": VERDICT_RECOMPUTE,
    }
    return mapping.get(str(delta.verdict), VERDICT_UNKNOWN)


__all__ = [
    "VERDICT_VALID", "VERDICT_STALE", "VERDICT_RECOMPUTE", "VERDICT_UNKNOWN",
    "ReuseDecision", "evaluate_reuse", "evaluate_reuse_with_history",
]
