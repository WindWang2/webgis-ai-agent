"""不确定度驱动的澄清状态机与显式 FallbackDecision（AC-01 / ADR-0150）。

设计要点（任务书 §2 P4 / §0.5）：

- **禁止静默 fallback**：一切兜底/降级必须携带
  :class:`FallbackDecision`（from / to / reason_code / evidence），
  随 intent 证据链外泄、可审计；
- **低置信必澄清**：置信度低于阈值、关键槽位缺失、规则-语义冲突时，
  :class:`ClarificationPolicy` 生成**最多 2 个**候选反问，每个反问带
  默认推荐项 —— 用户不答即取默认，不阻塞；
- **会话幂等**：澄清结果回填会话状态（复用 SessionStore 的
  ``set_map_state`` 键值面），同一槽位二次请求不再追问；
- 02 线（recipe adjudication）复用本模块的 ``FallbackDecision`` 结构。

本模块不 import intent 模块（结构解耦，避免循环依赖）；对 intent 的
读写全部走鸭子类型（getattr/setattr）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

# SessionStore 中澄清状态的键（map_state 键值面）
CLARIFICATION_STATE_KEY = "intent_clarification_state"

_FALLBACK_REASON_CODES = (
    "task_rule_miss",          # 规则全未命中 → 分布兜底
    "llm_task_conflict",       # 规则与语义任务冲突，按证据优先级回退
    "entity_resolution_degraded",  # 实体解析降级（服务不可用→词表）
    "slot_merge_degraded",     # 槽位合并降级
)


class FallbackDecision(BaseModel):
    """显式兜底决策（零静默 fallback 的载体；02 线复用本结构）。"""

    model_config = ConfigDict(extra="forbid")

    from_task: str = ""
    to_task: str = ""
    reason_code: str
    evidence: Dict[str, Any] = Field(default_factory=dict)

    def note(self) -> str:
        return (
            f"fallback {self.from_task or '∅'}->{self.to_task} "
            f"({self.reason_code})"
        )


class ClarificationOption(BaseModel):
    """澄清候选项；``is_default`` 的项在用户不答时被采纳。"""

    model_config = ConfigDict(extra="forbid")

    value: str
    label_zh: str
    label_en: str = ""
    is_default: bool = False


class ClarificationQuestion(BaseModel):
    """单个槽位反问（≤2 个问题/次，带默认推荐）。"""

    model_config = ConfigDict(extra="forbid")

    slot: str                     # task | subject | area
    prompt_zh: str
    prompt_en: str
    options: List[ClarificationOption] = Field(..., min_length=1, max_length=4)
    reason_code: str

    def default_option(self) -> ClarificationOption:
        for option in self.options:
            if option.is_default:
                return option
        return self.options[0]


class ClarificationRequest(BaseModel):
    """一次澄清回合（≤2 个问题；可序列化进 intent.clarification）。"""

    model_config = ConfigDict(extra="forbid")

    questions: List[ClarificationQuestion] = Field(..., min_length=1,
                                                   max_length=2)
    confidence: float = 0.0
    reason_codes: List[str] = []

    def default_answers(self) -> Dict[str, str]:
        return {q.slot: q.default_option().value for q in self.questions}


# ── 任务候选的中文/英文标签（bounded 词表，options 由此构造） ─────────────

_TASK_LABELS: Dict[str, Dict[str, str]] = {
    "distribution_overview": {"zh": "分布概览图", "en": "distribution overview"},
    "simple_view": {"zh": "轻量点图浏览", "en": "simple point map"},
    "administrative_statistic": {"zh": "按行政区统计", "en": "stats by district"},
    "analytical_density": {"zh": "定量密度分析", "en": "quantitative density"},
    "concentration_analysis": {"zh": "聚集/热点分析", "en": "hotspot analysis"},
    "categorical_distribution": {"zh": "分类构成分析", "en": "category breakdown"},
    "proximity_analysis": {"zh": "周边范围分析", "en": "proximity buffer"},
    "accessibility_analysis": {"zh": "可达性/服务区", "en": "accessibility"},
    "raster_distribution": {"zh": "栅格/遥感制图", "en": "raster mapping"},
    "change_detection": {"zh": "变化检测", "en": "change detection"},
    "vegetation_index": {"zh": "植被指数计算", "en": "vegetation index"},
    "mobility_flow": {"zh": "流动/OD 分析", "en": "mobility flows"},
    "spatial_equity": {"zh": "公平性评价", "en": "equity assessment"},
    "site_selection": {"zh": "选址评价", "en": "site selection"},
    "suitability_assessment": {"zh": "适宜性评价", "en": "suitability"},
    "risk_exposure": {"zh": "风险暴露评价", "en": "risk exposure"},
    "terrain_analysis": {"zh": "地形分析", "en": "terrain analysis"},
    "watershed_analysis": {"zh": "水文分析", "en": "watershed analysis"},
    "spatial_autocorrelation": {"zh": "空间自相关", "en": "autocorrelation"},
    "temporal_trend": {"zh": "时序趋势", "en": "temporal trend"},
    "sar_analysis": {"zh": "SAR 解译", "en": "SAR analysis"},
    "network_route": {"zh": "路径规划", "en": "route planning"},
}


def _task_option(task: str, *, default: bool = False) -> ClarificationOption:
    labels = _TASK_LABELS.get(task, {"zh": task, "en": task})
    return ClarificationOption(value=task, label_zh=labels["zh"],
                               label_en=labels.get("en", ""),
                               is_default=default)


_SUBJECT_OPTIONS = (
    ClarificationOption(value="poi", label_zh="设施/POI 点",
                        label_en="POI points", is_default=True),
    ClarificationOption(value="boundary", label_zh="行政区/边界面",
                        label_en="administrative boundaries"),
    ClarificationOption(value="raster", label_zh="栅格/遥感数据",
                        label_en="raster data"),
    ClarificationOption(value="network", label_zh="路网/线路",
                        label_en="networks"),
)

_AREA_DEFAULT = ClarificationOption(value="__viewport__",
                                    label_zh="当前视口/全局",
                                    label_en="current viewport / global",
                                    is_default=True)


class ClarificationPolicy:
    """低置信/缺槽位/多候选冲突 → ≤2 个带默认项的反问。

    策略是**语言无关**的：触发与槽位判据完全一致，提示语中英并出。
    """

    def __init__(self, confidence_floor: float = 0.55,
                 max_questions: int = 2) -> None:
        self.confidence_floor = confidence_floor
        self.max_questions = max_questions

    # ── 触发判定 ──
    def triggers(
        self,
        intent_like: Any,
        *,
        task_candidates: Optional[List[str]] = None,
        slot_conflict: bool = False,
    ) -> List[str]:
        reasons: List[str] = []
        confidence = float(getattr(intent_like, "confidence", 0.0) or 0.0)
        matched = list(getattr(intent_like, "matched_rules", []) or [])
        fallback = bool(matched) and matched[0] == "fallback_distribution_default"
        subject = getattr(intent_like, "subject", None)
        subject_type = getattr(subject, "type", "unknown")
        scope = getattr(intent_like, "scope", None)
        scope_name = getattr(scope, "name", "") or ""
        task = getattr(intent_like, "task", "")

        if confidence < self.confidence_floor:
            reasons.append("low_confidence")
        if fallback and subject_type == "unknown":
            reasons.append("missing_subject")
        if fallback and not scope_name:
            reasons.append("missing_area")
        if task == "simple_view" and subject_type == "unknown":
            reasons.append("display_without_subject")
        if slot_conflict:
            reasons.append("rule_semantic_conflict")
        if (fallback or confidence < self.confidence_floor) and task_candidates:
            reasons.append("task_ambiguous")
        return reasons

    def evaluate(
        self,
        intent_like: Any,
        *,
        task_candidates: Optional[List[str]] = None,
        slot_conflict: bool = False,
        asked_slots: Optional[set] = None,
    ) -> Optional[ClarificationRequest]:
        """生成澄清请求；无可问问题或全部已问过 → None。"""
        reasons = self.triggers(
            intent_like,
            task_candidates=task_candidates,
            slot_conflict=slot_conflict,
        )
        if not reasons:
            return None
        asked = asked_slots or set()
        questions: List[ClarificationQuestion] = []

        if "task_ambiguous" in reasons and "task" not in asked \
                and task_candidates:
            ranked = list(dict.fromkeys(task_candidates))[:2]
            if len(ranked) >= 2:
                questions.append(ClarificationQuestion(
                    slot="task",
                    prompt_zh="您想要哪种分析？（不选将采用推荐项）",
                    prompt_en="Which analysis do you want? "
                              "(default applies if unanswered)",
                    options=[_task_option(t, default=(i == 0))
                             for i, t in enumerate(ranked)],
                    reason_code="task_ambiguous",
                ))
        if ("missing_subject" in reasons or "display_without_subject" in reasons) \
                and "subject" not in asked:
            questions.append(ClarificationQuestion(
                slot="subject",
                prompt_zh="要分析的对象是哪类数据？",
                prompt_en="What kind of data should be analyzed?",
                options=list(_SUBJECT_OPTIONS),
                reason_code="missing_subject",
            ))
        if "missing_area" in reasons and "area" not in asked:
            questions.append(ClarificationQuestion(
                slot="area",
                prompt_zh="分析范围是哪里？（不选将按当前视口处理）",
                prompt_en="Which area? (current viewport by default)",
                options=[_AREA_DEFAULT],
                reason_code="missing_area",
            ))
        if not questions:
            return None
        return ClarificationRequest(
            questions=questions[: self.max_questions],
            confidence=float(getattr(intent_like, "confidence", 0.0) or 0.0),
            reason_codes=reasons,
        )

    # ── 应答回填 ──
    @staticmethod
    def apply_answers(intent_like: Any, request: ClarificationRequest,
                      answers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """把用户应答（或默认项）回填 intent；返回实际采纳的答案。"""
        effective = request.default_answers()
        if answers:
            for slot, value in answers.items():
                if value:
                    effective[slot] = value
        subject = getattr(intent_like, "subject", None)
        scope = getattr(intent_like, "scope", None)
        for slot, value in effective.items():
            if slot == "subject" and subject is not None:
                subject.type = value
            elif slot == "area" and scope is not None \
                    and value != "__viewport__":
                scope.name = value
                scope.level = "city"
            elif slot == "task" and value:
                try:
                    intent_like.task = value
                except Exception:  # noqa: BLE001 — 越词表值由 validate_assignment 拒绝
                    pass
        return effective


# ── 会话回填（复用 SessionStore 的 map_state 键值面；异步适配层） ─────────


async def load_asked_slots(session_store: Any, session_id: str) -> set:
    """读取该会话已澄清过的槽位集合（store 不可用时返回空集，不阻塞）。"""
    try:
        state = await session_store.get_map_state(session_id,
                                                  CLARIFICATION_STATE_KEY)
        if isinstance(state, dict):
            return set(state.get("asked_slots") or [])
    except Exception:  # noqa: BLE001
        pass
    return set()


async def save_asked_slots(session_store: Any, session_id: str,
                           slots: set) -> None:
    try:
        await session_store.set_map_state(
            session_id, CLARIFICATION_STATE_KEY,
            {"asked_slots": sorted(slots)})
    except Exception:  # noqa: BLE001 — 会话面故障不阻塞主链路
        pass


def make_fallback_decision(
    from_task: str,
    to_task: str,
    reason_code: str,
    *,
    evidence: Optional[Dict[str, Any]] = None,
) -> FallbackDecision:
    """构造显式 fallback 决策；reason_code 越界时收紧为 task_rule_miss。"""
    if reason_code not in _FALLBACK_REASON_CODES:
        reason_code = "task_rule_miss"
    return FallbackDecision(from_task=from_task, to_task=to_task,
                            reason_code=reason_code,
                            evidence=evidence or {})


__all__ = [
    "FallbackDecision",
    "ClarificationOption",
    "ClarificationQuestion",
    "ClarificationRequest",
    "ClarificationPolicy",
    "CLARIFICATION_STATE_KEY",
    "load_asked_slots",
    "save_asked_slots",
    "make_fallback_decision",
]
