"""跨域 finding 融合（F15/ADR-0214 决策三）—— deterministic wins。

同一 ``(entity, taxonomy 类)`` 上，视觉 finding 与确定性 finding 同因时：
视觉条目**保留在披露面**（不删减证据）但清空 ``repair_class`` 并附加
``corroborates:<finding_id>`` 收据——repair planner 不再为同一实体同类
问题触发第二次修复。确定性 finding 永不被本函数删除或降级。

纯函数、O(n)、有界（visual 输入上限沿用 seam 的 12）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Sequence, Tuple

from app.services.gis_harness.visual_observation.taxonomy import (
    deterministic_affinity,
    normalize_to_taxonomy,
)

_MAX_VISUAL_INPUT = 12
_MAX_EVIDENCE = 200


@dataclass(frozen=True)
class FusionOutcome:
    """融合结果（kept = 参与披露/计划面的条目，顺序稳定）。"""

    kept: Tuple[Any, ...] = ()
    corroborated: int = 0            # 命中确定性同因、被收据化的条数
    unmapped: int = 0                # taxonomy 归一失败被诚实丢弃的条数


def visual_taxonomy_of(finding: Any) -> str:
    """visual finding → taxonomy 类（从 code/来源维度文本归一）。"""
    code = str(getattr(finding, "code", "") or "")
    if code.startswith("visual_"):
        short = code[len("visual_"):]
        # 已是 taxonomy 词的 code 直接认；否则按 evidence 文本归一。
        from app.services.gis_harness.visual_observation.taxonomy import (
            VISUAL_TAXONOMY,
        )
        if short in VISUAL_TAXONOMY:
            return short
    evidence = str(getattr(finding, "evidence", "") or "")
    return normalize_to_taxonomy("", evidence=evidence)


def _entity_of(finding: Any) -> str:
    return str(getattr(finding, "affected_entity", "") or "") or "map"


def fuse_visual_with_deterministic(
    visual_findings: Sequence[Any],
    deterministic_findings: Sequence[Any],
) -> FusionOutcome:
    """视觉 findings 与确定性 findings 融合（返回可安全进入披露/计划面的集合）。

    - taxonomy 归一失败 → 丢弃并计 ``unmapped``（诚实缺席）；
    - ``(entity, taxonomy)`` 命中确定性 code（亲和表判定）→ 证据化：
      ``repair_class=""``、evidence 追加 ``corroborates:<finding_id>``；
    - 其余原样通过。
    """
    # 确定性侧索引：entity → codes（有界：≤24 检查面已在上游保证）。
    det_by_entity: Dict[str, List[str]] = {}
    for det in tuple(deterministic_findings)[:48]:
        code = str(getattr(det, "code", "") or "")
        if not code:
            continue
        det_by_entity.setdefault(_entity_of(det), []).append(code)
    # MapCompletionFinding 形状（dict 不可用时 duck-type 属性）兜底：target。
    for det in tuple(deterministic_findings)[:48]:
        code = str(getattr(det, "code", "") or "")
        if not code:
            continue
        target = str(getattr(det, "target", "") or "")
        if target:
            det_by_entity.setdefault(target, []).append(code)

    kept: List[Any] = []
    corroborated = 0
    unmapped = 0
    for vf in tuple(visual_findings)[:_MAX_VISUAL_INPUT]:
        cat = visual_taxonomy_of(vf)
        if not cat:
            unmapped += 1
            continue
        entity = _entity_of(vf)
        codes = det_by_entity.get(entity, ())
        if any(deterministic_affinity(cat, c) for c in codes):
            corroborated += 1
            kept.append(_evidence_backed(vf, cat))
            continue
        kept.append(vf)
    return FusionOutcome(kept=tuple(kept), corroborated=corroborated,
                         unmapped=unmapped)


def _evidence_backed(finding: Any, category: str) -> Any:
    """视觉 finding 的证据化副本（repair_class 清空 + corroborates 收据）。

    UnifiedFinding 是可变 dataclass —— 构造同形新实例，避免调用方共享
    状态被就地改写（投影纪律：融合是派生面，不改生产者产物）。
    """
    from app.services.gis_harness.completion.unified_findings import (
        UnifiedFinding,
    )

    receipt = f"corroborates:{category}"
    old_evidence = str(getattr(finding, "evidence", "") or "")
    if receipt in old_evidence:
        new_evidence = old_evidence
    else:
        new_evidence = f"{old_evidence} [{receipt}]".strip()[:_MAX_EVIDENCE]
    return UnifiedFinding(
        domain="visual",
        code=str(getattr(finding, "code", "") or ""),
        severity=str(getattr(finding, "severity", "") or "warning"),
        source=str(getattr(finding, "source", "") or ""),
        scope=str(getattr(finding, "scope", "") or "map"),
        affected_entity=str(getattr(finding, "affected_entity", "") or ""),
        evidence=new_evidence,
        repair_class="",                       # 修复归确定性通道（deterministic wins）
        retryable=False,
        blocks_completion=False,
        degradation_only=True,                 # 结构性：融合不升级权限
    )


__all__ = ["FusionOutcome", "fuse_visual_with_deterministic",
           "visual_taxonomy_of"]
