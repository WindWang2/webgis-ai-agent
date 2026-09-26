"""GrammarDecision 生产传递（F10，M3/M4）—— 工件随证据链贯穿.

#1480 只建立了 quality_loop 的只读 ``grammar_decision`` 消费参数；本模块把
它接进生产链：

- **Producer**：制图工具（create_thematic_map 等）产出 ``GrammarDecision``
  后经 :func:`decision_payload` 随工具结果下发；
- **随层存续**：decision 以 ``grammar_decision`` 兄弟键挂在 MapSpec layer 上
  （与 ``provenance``/``correction_hint``/``context_role`` 同先例——compiler/
  runtime 只透传 id/type/source/paint/layout/filter，兄弟键不进 MapLibre
  paint，但让「为何这样画」在 MapSpec 上可审计）；
- **Review 消费**：:func:`collect_grammar_decisions` 从 MapSpec layers 收集
  decision（versioned fail-closed 反序列化；坏工件 → invalid 披露，不阻断），
  :class:`CompositeGrammarAuditor` 让每个 decision **只对账自己的源层**
  （避免跨层伪 findings），整体暴露 quality_loop 期望的 ``.audit(layers)``
  鸭子类型面。

无第二 verdict：审计结论只进 review 证据（informational），绝不影响
status（ADR-0205 D7 既有契约）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.lib.cartography.grammar_solver import (
    GRAMMAR_VERSION,
    GrammarAudit,
    GrammarDecision,
    GrammarFinding,
)

logger = logging.getLogger(__name__)

#: MapSpec layer 上的 decision 兄弟键（compiler 透传面，同 provenance 先例）。
GRAMMAR_LAYER_KEY = "grammar_decision"

#: 收集上限（MapSpec 层数本身有界；此处为防御性硬顶，超出截断并披露）。
_MAX_COLLECTED = 64


def decision_payload(decision: GrammarDecision) -> Dict[str, Any]:
    """GrammarDecision → 可序列化工件（工具结果 / layer 兄弟键同形）。"""
    payload = decision.model_dump()
    payload["consumable"] = True
    return payload


def attach_grammar_decision(
    layer: Dict[str, Any],
    decision: GrammarDecision,
) -> None:
    """把 decision 挂到 layer 兄弟键（幂等：重复 attach 覆盖同指纹工件）。"""
    if not isinstance(layer, dict) or not isinstance(decision, GrammarDecision):
        return
    layer[GRAMMAR_LAYER_KEY] = decision_payload(decision)


def _validate_decision_payload(payload: Any) -> Optional[GrammarDecision]:
    """反序列化（fail-closed：版本不认/形状不符 → None，调用方披露）。"""
    if not isinstance(payload, dict):
        return None
    if payload.get("grammar_version") != GRAMMAR_VERSION:
        # 版本不匹配的工件不参与对账（旧版本决策与新词表不可比）。
        return None
    try:
        return GrammarDecision.model_validate(payload)
    except Exception as exc:  # noqa: BLE001 — 坏工件披露不阻断
        logger.debug("grammar_decision payload invalid: %s", exc)
        return None


class _DecisionRef(BaseModel):
    """已收集的 decision 及其源层定位（id 优先，index 兜底）。"""

    layer_id: Optional[str] = None
    layer_index: int = -1
    decision: GrammarDecision


class CompositeGrammarAuditor:
    """quality_loop ``grammar_decision`` 参数的鸭子类型面（``.audit(layers)``）。

    每个 decision 只对账**自己的源层**（按 layer_id 匹配，退化到 index）；
    汇总为单一 ``GrammarAudit`` 形（findings 并集、evaluated 取与）。
    """

    def __init__(self, refs: List[_DecisionRef], invalid: List[Dict[str, Any]]):
        self._refs = refs
        self._invalid = invalid

    def audit(self, layers: List[Dict[str, Any]]) -> GrammarAudit:
        layer_list = layers if isinstance(layers, list) else []
        merged = GrammarAudit(
            decision_fingerprint=self._merged_fingerprint())
        if self._invalid:
            merged.findings.append(GrammarFinding(
                code="GRAMMAR.AUDIT.DECISION_INVALID",
                severity="info",
                message=(
                    f"{len(self._invalid)} 个 grammar_decision 工件无法按当前"
                    f"版本 {GRAMMAR_VERSION} 反序列化——不参与对账（诚实披露）"),
            ))
        # 每 ref 消费一个**不同**的层（review P3：重复 layer id 时不得把
        # 多个 decision 都对到第一个同名层）。
        used_indices: set = set()
        matched = 0
        for ref in self._refs:
            layer, index = self._match_layer(layer_list, ref, used_indices)
            if layer is None:
                continue
            used_indices.add(index)
            matched += 1
            try:
                sub = ref.decision.audit([layer])
            except Exception as exc:  # noqa: BLE001 — 审计只读，绝不阻断
                merged.findings.append(GrammarFinding(
                    code="GRAMMAR.AUDIT.DECISION_INVALID",
                    severity="info",
                    message=f"层 {ref.layer_id} grammar 对账失败：{exc}",
                    layer_id=ref.layer_id,
                ))
                continue
            merged.findings.extend(sub.findings)
        if len(self._refs) > matched:
            merged.findings.append(GrammarFinding(
                code="GRAMMAR.AUDIT.DECISION_UNMATCHED",
                severity="info",
                message="部分 grammar_decision 的源层不在当前 MapSpec 中——跳过对账",
            ))
        return merged

    def _merged_fingerprint(self) -> str:
        """多决策时指纹串并（有界截断；单决策 = 原指纹，证据可归因）。"""
        if not self._refs:
            return ""
        if len(self._refs) == 1:
            return self._refs[0].decision.fingerprint
        joined = ",".join(r.decision.fingerprint[:8] for r in self._refs[:8])
        extra = len(self._refs) - min(len(self._refs), 8)
        return (f"{joined}+{len(self._refs)}" + (f"+{extra}more" if extra > 0 else ""))[:64]

    @staticmethod
    def _match_layer(
        layer_list: List[Dict[str, Any]],
        ref: _DecisionRef,
        used_indices: set,
    ) -> tuple:
        """定位 ref 的源层：index+id 双匹配优先；否则首个未占用的同名层。

        返回 (layer | None, used_index)。id 一致才可信（层序可能已重排）。
        """
        if 0 <= ref.layer_index < len(layer_list):
            candidate = layer_list[ref.layer_index]
            if (
                isinstance(candidate, dict)
                and candidate.get("id") == ref.layer_id
                and ref.layer_index not in used_indices
            ):
                return candidate, ref.layer_index
        if ref.layer_id is not None:
            for i, layer in enumerate(layer_list):
                if (
                    isinstance(layer, dict)
                    and layer.get("id") == ref.layer_id
                    and i not in used_indices
                ):
                    return layer, i
        return None, -1


class GrammarDecisionCollection(BaseModel):
    """收集结果（refs 供 auditor；invalid 供披露；有界）。"""

    refs: List[_DecisionRef] = Field(default_factory=list)
    invalid: List[Dict[str, Any]] = Field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.refs

    def auditor(self) -> CompositeGrammarAuditor:
        return CompositeGrammarAuditor(self.refs, self.invalid)


def collect_grammar_decisions(mapspec: Any) -> GrammarDecisionCollection:
    """从 MapSpec layers 收集 grammar_decision 工件（只读、有界、versioned）。"""
    out = GrammarDecisionCollection()
    if not isinstance(mapspec, dict):
        return out
    layers = mapspec.get("layers")
    if not isinstance(layers, list):
        return out
    for index, layer in enumerate(layers[:_MAX_COLLECTED]):
        if not isinstance(layer, dict) or GRAMMAR_LAYER_KEY not in layer:
            continue
        payload = layer.get(GRAMMAR_LAYER_KEY)
        decision = _validate_decision_payload(payload)
        entry = {
            "layer_id": layer.get("id") if isinstance(layer.get("id"), str) else None,
            "layer_index": index,
        }
        if decision is None:
            out.invalid.append(entry)
            continue
        out.refs.append(_DecisionRef(
            layer_id=entry["layer_id"],
            layer_index=index,
            decision=decision,
        ))
    return out


def grammar_auditor_for_mapspec(mapspec: Any) -> Optional[CompositeGrammarAuditor]:
    """便捷面：MapSpec 无 decision → None（quality_loop 缺省 not evaluated）。"""
    collection = collect_grammar_decisions(mapspec)
    if collection.empty and not collection.invalid:
        return None
    return collection.auditor()


__all__ = [
    "GRAMMAR_LAYER_KEY",
    "GrammarDecisionCollection",
    "CompositeGrammarAuditor",
    "attach_grammar_decision",
    "collect_grammar_decisions",
    "decision_payload",
    "grammar_auditor_for_mapspec",
]
