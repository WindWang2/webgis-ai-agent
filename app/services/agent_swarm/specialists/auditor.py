"""CriticAuditorAgent —— 独立审计裁判（ADR-0189 D2）。

制度红线：

- **不参与实现**：零 mutation 面（角色档 ``allow_mutation=False``，工具
  白名单仅审计只读面）；输入只有交付券与 chapter 证据面；
- **fail-closed**：审计对象不可达 / 证据不全 / not_evaluated 永不入
  pass（ADR-0061 同门；PASS_CAPABLE_CLASSES：visual/assisted 永不单独
  背书 PASS）；
- **一票否决（Veto Power）**：四条红线（V1 图例未闭环 / V2 目标要素
  缺失 / V3 指标口径未对齐 / V4 数据空洞）命中即 ``verdict="fail"``，
  每条 veto 附带不可抵赖因果链（rule_id + severity + message +
  audited_fingerprint + evidence + suggested_fix）；另设 V0（审计对象
  不可达）；
- **不造第二 verdict**：``goal_score`` 是
  ``evaluate_goal_satisfaction`` counts 的派生口径，必须携带
  ``goal_score_derivation`` 披露；READY 族 / final_map_status 等 G7
  已有裁决按原样采信，报告结论是 advisory。
"""
from __future__ import annotations

import logging
from typing import Any, Callable, ClassVar, Dict, FrozenSet, List, Optional

from app.lib.cartography.component_composer import required_components_for
from app.lib.cartography.quality_loop import (
    cartographic_fingerprint,
    review_cartography,
)
from app.services.agent_swarm.base import BaseSpecialistAgent
from app.services.agent_swarm.contracts import (
    _MAX_DELIVERY_SUMMARY_LEN,
    DeliveryAuditReport,
)
from app.services.agent_swarm.specialists.ledger import ArtifactLedger

logger = logging.getLogger(__name__)


def _default_evaluate(
    chapter: Optional[Dict[str, Any]],
    *,
    user_goal: str = "",
    map_product: Optional[Dict[str, Any]] = None,
    cartographic_review: Optional[Dict[str, Any]] = None,
):
    """惰性绑定 ADR-0183 评估面（测试可注入替身）。"""
    from app.services.gis_harness.goal_satisfaction.evaluator import (
        evaluate_goal_satisfaction,
    )

    return evaluate_goal_satisfaction(
        chapter,
        user_goal=user_goal,
        map_product=map_product,
        cartographic_review=cartographic_review,
    )


def _resolve_contract(chapter: Optional[Dict[str, Any]]):
    from app.services.gis_harness.goal_satisfaction.evaluator import (
        resolve_goal_contract,
    )

    return resolve_goal_contract(chapter)


class CriticAuditorAgent(BaseSpecialistAgent):
    """审计裁判：只读挂接评估面 + 语义检查，出具《交付质量审计单》。"""

    name: ClassVar[str] = "critic_auditor"
    role_name: ClassVar[str] = "audit_judge"

    #: 审计与回放校核只读面（零 mutation；与其余专家两两不相交）。
    TOOL_ALLOWLIST: ClassVar[FrozenSet[str]] = frozenset({
        "audit_spatial_quality",
        "gis_skill_replay_check",
    })

    SPECIALIST_PROMPT: ClassVar[str] = (
        "你是独立审计裁判：只读、刁钻、fail-closed。绝不参与实现，绝不"
        "采信自称完成的叙述；对图例闭环、目标要素、指标口径、数据空洞"
        "四条红线行使一票否决，并出具带因果证据链的审计单。"
    )

    def __init__(
        self,
        *,
        evaluator: Optional[Callable[..., Any]] = None,
        **base_kwargs: Any,
    ) -> None:
        super().__init__(**base_kwargs)
        self._evaluator = evaluator or _default_evaluate

    # ── audit（只读调用链，ADR-0189 D2）──────────────────────

    def audit(
        self,
        delivery: Any,
        *,
        chapter: Optional[Dict[str, Any]] = None,
        ledger: Optional[ArtifactLedger] = None,
        source_profiles: Optional[Dict[str, Dict[str, Any]]] = None,
        map_product: Optional[Dict[str, Any]] = None,
        user_goal: str = "",
        round_index: int = 0,
    ) -> DeliveryAuditReport:
        self.heartbeat("audit")
        self.check_deadline()
        vetoes: List[Dict[str, Any]] = []
        notes: List[str] = []
        uncovered: List[str] = []
        risks: List[str] = []
        goal_score: Optional[float] = None
        threshold: Optional[float] = None
        derivation = ""

        payload = None
        if ledger is not None and getattr(delivery, "ref_id", None):
            payload = ledger.get(delivery.ref_id)
        if payload is None:
            # V0：审计对象不可达 —— 绝不基于叙述放行。
            vetoes.append({
                "veto_id": "V0",
                "rule_id": "audit.target_unreachable",
                "severity": "error",
                "message": (
                    f"审计对象不可达：ref_id={getattr(delivery, 'ref_id', None)!r}"
                    "（账本无此券或未提供账本），拒绝基于摘要放行"
                ),
                "audited_fingerprint": getattr(
                    delivery, "mapspec_fingerprint", "",
                ),
                "evidence": {"ref_id": getattr(delivery, "ref_id", None)},
                "suggested_fix": None,
            })
            notes.append("[V0] 修复建议：重新出券并确认账本取货位可达")
            return self._report(
                delivery, vetoes, notes, uncovered, risks,
                verdict="fail", goal_score=None, threshold=None,
                derivation="", review_status="", round_index=round_index,
            )

        fingerprint = cartographic_fingerprint(payload)
        self.heartbeat("review")
        # F10：payload 层携带 grammar 决策工件 → 只读对账（缺失 → None）。
        from app.lib.cartography.grammar_propagation import (
            grammar_auditor_for_mapspec,
        )
        review_result = review_cartography(
            payload, source_profiles,
            grammar_decision=grammar_auditor_for_mapspec(payload),
        )
        review = review_result.review
        review_status = review_result.status
        checks: List[Dict[str, Any]] = list(review.get("checks", []))

        # ── 风险面（blocking + warning 规则码，有界投影）──
        for check in checks:
            rule = str(check.get("rule") or check.get("check") or "")
            status = check.get("status")
            if status == "fail" and check.get("severity") == "error":
                if rule and rule not in risks:
                    risks.append(rule)
            elif status == "warning" and rule and rule not in risks:
                risks.append(rule)
        del risks[12:]

        # ── V1 图例未闭环 ──
        for check in checks:
            if (
                check.get("rule") == "carto.legend.completeness"
                and check.get("status") == "fail"
            ):
                vetoes.append(self._veto(
                    "V1", "carto.legend.completeness", check, fingerprint,
                ))
                notes.append(
                    "[V1] 修复建议：打开地图图例"
                    "（set_map_legend_visibility=True）并保持图层 legend_spec"
                )
                break
        thematic_without_legend = [
            str(layer.get("id"))
            for layer in payload.get("layers", [])
            if isinstance(layer, dict)
            and layer.get("visible") is not False
            and layer.get("type") != "raster"
            and not isinstance(layer.get("legend_spec"), dict)
            and _has_data_driven_paint(layer)
        ]
        if thematic_without_legend:
            vetoes.append({
                "veto_id": "V1",
                "rule_id": "legend.thematic_layer_without_legend_spec",
                "severity": "error",
                "message": (
                    f"可见专题层 {thematic_without_legend} 缺少 legend_spec，"
                    "读者无法解释分级"
                ),
                "audited_fingerprint": fingerprint,
                "evidence": {"layers": thematic_without_legend},
                "suggested_fix": {
                    "operation": "attach_legend_spec",
                    "layers": thematic_without_legend,
                },
            })
            notes.append(
                "[V1] 修复建议：为专题层补齐 legend_spec（分级/类别图例）"
            )

        # ── V4 数据空洞 ──
        for check in checks:
            rule = str(check.get("rule") or "")
            if rule in ("EMPTY_DATA", "RESULT_DATA_PRESENCE") and (
                check.get("status") == "fail"
            ):
                vetoes.append(self._veto("V4", rule, check, fingerprint))
                notes.append(
                    "[V4] 修复建议：核实源数据与过滤条件，交付物不得是空图"
                )
            elif rule == "NO_DATA_SEMANTICS" and check.get("status") in (
                "fail", "warning",
            ):
                vetoes.append(self._veto("V4", rule, check, fingerprint))
                notes.append(
                    "[V4] 修复建议：为数值分类补 nodata 规则，缺值不得静默入级"
                )

        # ── V2 目标要素缺失（必配组件基线）──
        layout = payload.get("layout") or {}
        purpose = str(layout.get("purpose") or "screen_16_9")
        plan = required_components_for(purpose, {
            "has_thematic_layer": any(
                isinstance(layer, dict)
                and isinstance(layer.get("legend_spec"), dict)
                and layer.get("visible") is not False
                for layer in payload.get("layers", [])
            ),
            "has_data_source": True,
        })
        present = {
            str(c.get("type"))
            for c in layout.get("components", [])
            if isinstance(c, dict)
        }
        missing = [req.type for req in plan.required if req.type not in present]
        if missing:
            vetoes.append({
                "veto_id": "V2",
                "rule_id": "components.required_missing",
                "severity": "error",
                "message": (
                    f"必配组件缺失：{missing}（用途 {purpose} 的制图学基线）"
                ),
                "audited_fingerprint": fingerprint,
                "evidence": {"missing": missing, "purpose": purpose},
                "suggested_fix": {
                    "operation": "attach_components",
                    "component_types": missing,
                },
            })
            notes.append(f"[V2] 修复建议：补齐必配组件 {missing}")

        # ── V3 指标口径未对齐（goal 契约面）──
        contract = _resolve_contract(chapter) if chapter is not None else None
        goal_report = None
        if chapter is not None:
            try:
                goal_report = self._evaluator(
                    chapter,
                    user_goal=user_goal,
                    map_product=map_product,
                )
            except Exception as exc:  # noqa: BLE001 — 评估面异常按无证据处理
                logger.warning("goal satisfaction evaluator failed: %s", exc)
                goal_report = None
        if contract is not None:
            required_ids = contract.required_ids()
            threshold = float(getattr(contract, "success_threshold", 1.0) or 1.0)
            if goal_report is None or not required_ids:
                derivation = ""
                if chapter is not None:
                    notes.append(
                        "[fail-closed] goal 契约在而评估证据不足，不入 pass"
                    )
            else:
                id_set = set(required_ids)
                required_verdicts = [
                    r for r in goal_report.requirements if r.requirement_id in id_set
                ]
                fulfilled = sum(
                    1 for r in required_verdicts
                    if r.verdict.value == "fulfilled"
                )
                goal_score = fulfilled / len(required_ids)
                derivation = (
                    f"fulfilled {fulfilled}/{len(required_ids)} required"
                    "（evaluate_goal_satisfaction 逐需求 ledger 派生，"
                    "缺证据不入分子，非独立 verdict）"
                )
                uncovered = [
                    r.requirement_id
                    for r in required_verdicts
                    if r.verdict.value != "fulfilled"
                ][:12]
                if uncovered:
                    vetoes.append({
                        "veto_id": "V3",
                        "rule_id": "goal.requirements_uncovered",
                        "severity": "error",
                        "message": f"指标口径未对齐：required 需求未满足 {uncovered}",
                        "audited_fingerprint": fingerprint,
                        "evidence": {
                            "uncovered": uncovered,
                            "goal_verdict": goal_report.verdict.value,
                        },
                        "suggested_fix": {
                            "operation": "satisfy_requirements",
                            "requirement_ids": uncovered,
                        },
                    })
                    for rid in uncovered[:4]:
                        notes.append(f"[V3] 需求 {rid} 未满足，须补齐证据链")
                if goal_score < threshold:
                    vetoes.append({
                        "veto_id": "V3",
                        "rule_id": "goal.score_below_threshold",
                        "severity": "error",
                        "message": (
                            f"goal_score={goal_score:.2f} < success_threshold="
                            f"{threshold:.2f}"
                        ),
                        "audited_fingerprint": fingerprint,
                        "evidence": {"goal_score": goal_score, "derivation": derivation},
                        "suggested_fix": {
                            "operation": "raise_goal_satisfaction",
                            "threshold": threshold,
                        },
                    })
        elif chapter is not None:
            notes.append(
                "[disclose] chapter 未派生 GIS 目标契约，本次为纯制图审计"
                "（goal 面不设门槛）"
            )

        # ── 裁决 ──
        if vetoes:
            verdict = "fail"
        elif review_status in ("passed", "passed_with_warnings"):
            goal_ok = (
                contract is None
                or (
                    goal_report is not None
                    and goal_score is not None
                    and goal_score >= (threshold or 1.0)
                    and goal_report.verdict.value not in ("blocked", "failed")
                )
            )
            verdict = "pass" if goal_ok else "fail"
            if not goal_ok and contract is not None:
                notes.append("[V3] goal 面未达标，整体不放行")
        elif review_status in ("partial", "not_evaluated"):
            verdict = "not_evaluated"
            notes.append(
                "[fail-closed] 语义检查证据不全（not_evaluated ≠ 证据），不入 pass"
            )
        else:
            verdict = "fail"
            failing = [
                str(c.get("rule")) for c in checks if c.get("status") == "fail"
            ][:4]
            notes.append(
                f"[fail] 制图语义检查未通过：{failing}，须按 suggested_fix 修复"
            )

        return self._report(
            delivery, vetoes, notes, uncovered, risks,
            verdict=verdict, goal_score=goal_score, threshold=threshold,
            derivation=derivation, review_status=review_status,
            round_index=round_index,
        )

    # ── 内部 ────────────────────────────────────────────────

    def _veto(
        self, veto_id: str, rule_id: str, check: Dict[str, Any],
        fingerprint: str,
    ) -> Dict[str, Any]:
        return {
            "veto_id": veto_id,
            "rule_id": rule_id,
            "severity": str(check.get("severity") or "error"),
            "message": str(check.get("message") or ""),
            "audited_fingerprint": fingerprint,
            "evidence": dict(check.get("evidence") or {}),
            "suggested_fix": check.get("suggested_fix"),
        }

    def _report(
        self,
        delivery: Any,
        vetoes: List[Dict[str, Any]],
        notes: List[str],
        uncovered: List[str],
        risks: List[str],
        *,
        verdict: str,
        goal_score: Optional[float],
        threshold: Optional[float],
        derivation: str,
        review_status: str,
        round_index: int,
    ) -> DeliveryAuditReport:
        bounded_notes = [
            n[: DeliveryAuditReport._MAX_NOTE_LEN]
            for n in notes[: DeliveryAuditReport._MAX_NOTES]
        ]
        summary = (
            f"audit verdict={verdict}, vetoes={len(vetoes)}, "
            f"uncovered={len(uncovered)}, risks={len(risks)}"
        )[:_MAX_DELIVERY_SUMMARY_LEN]
        logger.info(
            "[Specialist:%s] %s", self.name, summary,
        )
        return DeliveryAuditReport(
            audited_ref_id=str(getattr(delivery, "ref_id", "") or ""),
            audited_fingerprint=str(getattr(delivery, "mapspec_fingerprint", "") or ""),
            round_index=round_index,
            verdict=verdict,  # type: ignore[arg-type]
            goal_score=goal_score,
            goal_score_derivation=derivation if goal_score is not None else "",
            success_threshold=threshold,
            uncovered_requirements=uncovered[: DeliveryAuditReport._MAX_UNCOVERED],
            cartography_risks=risks[: DeliveryAuditReport._MAX_RISKS],
            vetoes=vetoes[: DeliveryAuditReport._MAX_VETOES],
            improvement_notes=bounded_notes,
            review_status=review_status,
        )


def _has_data_driven_paint(layer: Dict[str, Any]) -> bool:
    """paint 是否携带数据驱动方法（step/interpolate/match）。"""
    paint = layer.get("paint")
    if not isinstance(paint, dict):
        return False
    for value in paint.values():
        if isinstance(value, dict) and value.get("method") in (
            "step", "interpolate", "match",
        ):
            return True
    return False


__all__ = ["CriticAuditorAgent"]
