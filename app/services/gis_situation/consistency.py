"""Situation 一致性检查与 reconciliation 语义（方向 2 S7，ADR-0180）。

覆盖任务书点名的失联面（desired MapSpec vs 前端观察、选中/聚焦层被移除、
stale viewport、失败修复循环）—— **只报告状态级协调事实**（改写 situation
内事实 status / 供 agent 与 QA 消费），不做 mutation 事务（方向 8）、不写
任何权威状态。

输出是 ``SituationInconsistency`` 列表（code/severity/detail，可序列化、
确定性排序），inspector 与测试直接消费。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from app.services.gis_situation.contract import GISSituation
from app.services.gis_situation.facts import STATUS_KNOWN, STATUS_STALE

SEVERITY_INFO = "info"
SEVERITY_WARNING = "warning"
SEVERITY_ERROR = "error"


@dataclass(frozen=True)
class SituationInconsistency:
    code: str
    severity: str
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"code": self.code, "severity": self.severity, "detail": self.detail}


def _layer_ids(situation: GISSituation) -> set:
    fact = situation.map.layers
    if fact.status == STATUS_KNOWN and isinstance(fact.value, list):
        return {str(row.get("id")) for row in fact.value if isinstance(row, dict) and row.get("id")}
    return set()


def check_consistency(situation: GISSituation) -> List[SituationInconsistency]:
    """只读一致性检查（确定性行序：按 code 字典序）。"""
    out: List[SituationInconsistency] = []
    layer_ids = _layer_ids(situation)

    # 1. desired vs observed：渲染观察落后于当前 spec revision。
    desired = situation.map.desired_revision
    observed = situation.map.observed
    if desired.status == STATUS_KNOWN and observed.status == STATUS_KNOWN:
        obs_rev = observed.revision
        if isinstance(obs_rev, int) and obs_rev < desired.value:
            out.append(SituationInconsistency(
                code="observed_behind_desired",
                severity=SEVERITY_INFO,
                detail={"observed_revision": obs_rev,
                        "desired_revision": desired.value},
            ))
    elif observed.status == "unknown" and desired.status == STATUS_KNOWN and desired.value > 0:
        out.append(SituationInconsistency(
            code="observation_missing",
            severity=SEVERITY_INFO,
            detail={"desired_revision": desired.value},
        ))

    # 2. 选中/聚焦要素的宿主层已从 desired spec 移除（"这个对象"悬空）。
    selected = situation.interaction.selected_feature
    if selected.status == STATUS_KNOWN and isinstance(selected.value, dict):
        layer_id = selected.value.get("layer_id")
        if layer_id and str(layer_id) not in layer_ids:
            out.append(SituationInconsistency(
                code="selected_feature_source_removed",
                severity=SEVERITY_WARNING,
                detail={"layer_id": str(layer_id)},
            ))
    focus = situation.interaction.focus_layer_id
    if focus.status == STATUS_KNOWN and focus.value and str(focus.value) not in layer_ids:
        out.append(SituationInconsistency(
            code="focus_layer_source_removed",
            severity=SEVERITY_WARNING,
            detail={"layer_id": str(focus.value)},
        ))

    # 3. 用户 durable 隐藏层与 desired 可见面冲突 —— user wins（协调语义：
    #    agent 后续 mutation 必须尊重，UserPresentationGuard 是服务端强制）。
    hidden = situation.interaction.user_hidden_layers
    if hidden.status == STATUS_KNOWN and isinstance(hidden.value, list):
        for layer_id in hidden.value:
            if layer_id in layer_ids:
                out.append(SituationInconsistency(
                    code="user_hidden_layer_conflict",
                    severity=SEVERITY_WARNING,
                    detail={"layer_id": str(layer_id),
                            "semantics": "user_wins"},
                ))

    # 4. stale viewport：agent 取景(framed)与用户当前视口并存且用户视口
    #    无中心 —— 用户可能已移开；只作 info 披露（不自动回拉）。
    framed = situation.geographic.framed_view
    viewport = situation.geographic.viewport
    if (framed.status == STATUS_KNOWN and isinstance(framed.value, dict)
            and framed.value.get("framed")
            and viewport.status != STATUS_KNOWN):
        out.append(SituationInconsistency(
            code="stale_or_missing_viewport",
            severity=SEVERITY_INFO,
            detail={"framed_view": True, "viewport": "unknown"},
        ))

    # 5. 修复循环在途：verdict 指纹失配（stale）说明 spec 已前进、review 未跟上。
    verdict = situation.cartographic.verdict
    if verdict.status == STATUS_STALE:
        out.append(SituationInconsistency(
            code="verdict_stale_fingerprint",
            severity=SEVERITY_INFO,
            detail={"semantics": "review_belongs_to_previous_generation"},
        ))

    # 6. 后台任务在途 + 同会话 spec 又被推进 —— 结果可能落在新代上。
    pending = situation.interaction.pending_mutations
    if pending.status == STATUS_KNOWN and isinstance(pending.value, list) and pending.value:
        out.append(SituationInconsistency(
            code="pending_background_mutations",
            severity=SEVERITY_INFO,
            detail={"count": len(pending.value)},
        ))

    # 7. 权威源读取失败（部分编译）—— 消费方须知这不是"无数据"。
    if situation.evidence.sources_unavailable:
        out.append(SituationInconsistency(
            code="sources_unavailable",
            severity=SEVERITY_WARNING,
            detail={"sources": sorted(situation.evidence.sources_unavailable)},
        ))

    return sorted(out, key=lambda i: (i.code, i.severity))


def reconcile_fact_views(situation: GISSituation) -> GISSituation:
    """状态协调（只改 situation 投影视图，绝不写权威状态）：

    - 选中/聚焦宿主层已移除 → 该事实降级为 stale（诚实标注"已失效"，
      而非删除或假装仍有效）；
    - 其余协调由消费方按 inconsistency 列表执行。
    """
    updates: Dict[str, Any] = {}
    layer_ids = _layer_ids(situation)
    selected = situation.interaction.selected_feature
    if selected.status == STATUS_KNOWN and isinstance(selected.value, dict):
        layer_id = selected.value.get("layer_id")
        if layer_id and str(layer_id) not in layer_ids:
            from app.services.gis_situation.facts import stale as stale_fact

            updates["selected_feature"] = stale_fact(
                selected.value,
                source=selected.source,
            )
    focus = situation.interaction.focus_layer_id
    if focus.status == STATUS_KNOWN and focus.value and str(focus.value) not in layer_ids:
        from app.services.gis_situation.facts import stale as stale_fact

        updates["focus_layer_id"] = stale_fact(focus.value, source=focus.source)
    if not updates:
        return situation
    interaction = situation.interaction.model_copy(update=updates)
    return situation.model_copy(update={"interaction": interaction})
