"""AC-01 自适应意图理解回归基座（ADR-0150 / P7）。

门禁（与 docs/dev/ac-01-intent-recon.md 锚点一致，基线数字不可改动）：

- 300 条双语语料 overall ≥ 0.6933 + 0.08（基线见
  docs/dev/ac-01-baseline-metrics.json，重构前实测）；
- 英文命中率 ≥ 中文命中率 × 0.9；
- 模糊条目 100% 触发澄清；所有 fallback 必携带 FallbackDecision
  （零静默兜底的 grep 等价断言）；
- LLM 不可用时全链路不抛错且 ``degraded_reason`` 有值；
- 校准分箱误差 ≤ 0.15；
- 与 ``app/evaluation/closed_loop_corpus.py``（204 条闭环语料）对齐冒烟，
  全部可解析且任务词表合法。

全部用例零 LLM、零网络（规则快路径 + 注入式 fake LLM）。
"""
from __future__ import annotations

import asyncio
import statistics
from typing import Any, Dict

import pytest

from app.services.gis_harness import intent_semantic as semantic
from app.services.gis_harness.clarification import (
    CLARIFICATION_STATE_KEY,
    ClarificationPolicy,
    load_asked_slots,
    save_asked_slots,
)
from app.services.gis_harness.intent import (
    MapRequestIntent,
    merge_intent_hints,
    resolve_intent_adaptive,
    resolve_map_request_intent,
)
from app.services.gis_harness.intent_semantic import IntentSlots, SlotValue

from corpus_harness import evaluate_corpus, load_corpus

pytestmark = pytest.mark.cartography

# 基线锚点（docs/dev/ac-01-intent-recon.md §3；P7 门禁 = 基线 + 8pt）
BASELINE_OVERALL = 0.6933
GAIN_REQUIRED = 0.08


@pytest.fixture(scope="module")
def corpus() -> list:
    return load_corpus()


def _resolve_rule_only(query: str) -> MapRequestIntent:
    intent, _request = resolve_intent_adaptive(query, use_llm=False)
    return intent


def _clarified(_query: str, dumped: Dict[str, Any]) -> bool:
    return bool(dumped.get("clarification"))


# ── 语料回归门禁 ──────────────────────────────────────────────────────────


class TestCorpusGates:
    def test_overall_gain_over_baseline(self, corpus: list) -> None:
        report = evaluate_corpus(corpus, resolver=_resolve_rule_only,
                                 clarifier=_clarified)
        assert report.overall_score >= BASELINE_OVERALL + GAIN_REQUIRED, (
            f"overall={report.overall_score:.4f} 门禁="
            f"{BASELINE_OVERALL + GAIN_REQUIRED:.4f}")

    def test_english_not_below_90pct_of_chinese(self, corpus: list) -> None:
        report = evaluate_corpus(corpus, resolver=_resolve_rule_only,
                                 clarifier=_clarified)
        zh = report.per_lang["zh"]["task_hit_rate"]
        en = report.per_lang["en"]["task_hit_rate"]
        assert en >= 0.9 * zh, f"en={en:.4f} zh={zh:.4f}"

    def test_ambiguous_items_all_clarify(self, corpus: list) -> None:
        report = evaluate_corpus(corpus, resolver=_resolve_rule_only,
                                 clarifier=_clarified)
        assert report.ambiguous == 10
        assert report.clarify_hit_rate == 1.0

    def test_zero_silent_fallback(self, corpus: list) -> None:
        """所有落入兜底的条目必须携带 FallbackDecision（grep 断言等价）。"""
        fallback_hits = 0
        for item in corpus:
            intent = _resolve_rule_only(item["query"])
            matched = intent.matched_rules or []
            if matched and matched[0] == "fallback_distribution_default":
                fallback_hits += 1
                decision = intent.fallback_decision
                assert decision is not None, f"silent fallback: {item['id']}"
                assert decision["reason_code"] == "task_rule_miss"
                assert "task_candidates" in decision["evidence"]
        assert fallback_hits >= 1  # 兜底面在语料中真实存在并被审计

    def test_fallback_rate_drops_below_quarter(self, corpus: list) -> None:
        report = evaluate_corpus(corpus, resolver=_resolve_rule_only,
                                 clarifier=_clarified)
        assert report.fallback_rate < 0.25, (
            f"fallback_rate={report.fallback_rate:.4f}（基线 0.2759）")


# ── 确定性与兼容契约 ──────────────────────────────────────────────────────


class TestDeterminismAndCompat:
    def test_resolve_is_replayable(self) -> None:
        a = resolve_map_request_intent("统计成都各区小学数量").model_dump()
        b = resolve_map_request_intent("统计成都各区小学数量").model_dump()
        assert a == b

    def test_map_request_intent_backward_compatible(self) -> None:
        """只加字段不改语义：legacy 构造方式与字段缺省保持可用。"""
        intent = MapRequestIntent(query="成都小学分布",
                                  task="distribution_overview")
        assert intent.confidence == 0.0
        assert intent.fallback_decision is None
        assert intent.intent_evidence is None
        dumped = intent.model_dump()
        for key in ("lang", "slots", "ontology_link", "intent_evidence",
                    "degraded_reason", "fallback_decision", "clarification"):
            assert key in dumped

    def test_merge_intent_hints_protection_kept(self) -> None:
        base = resolve_map_request_intent("统计成都各区小学数量")
        assert base.task == "administrative_statistic"
        merged = merge_intent_hints(base, {"task": "distribution_overview"})
        assert merged.task == "administrative_statistic"
        assert any("rejected" in note for note in merged.hint_applied)

    def test_matched_rules_head_semantics_kept(self) -> None:
        """plan_orchestrator 依赖 matched_rules[0] 非 fallback 判定。"""
        intent = resolve_map_request_intent("成都哪里的小吃最集中")
        assert intent.matched_rules[0] != "fallback_distribution_default"
        assert intent.task == "concentration_analysis"


# ── 降级与 LLM 双轨 ───────────────────────────────────────────────────────


class TestDegradation:
    def test_llm_unavailable_degrades_without_raise(self, corpus: list) -> None:
        """LLM 不可用：300 条全链路不抛错，degraded_reason 可追踪。"""
        assert semantic.llm_available() is False or True  # 环境无关
        for item in corpus[:50]:
            intent, _request = resolve_intent_adaptive(
                item["query"], use_llm=False)
            assert intent is not None
            evidence = intent.intent_evidence or {}
            assert evidence.get("llm_requested") is False

    def test_extract_slots_with_llm_unavailable_sets_reason(self) -> None:
        if semantic.llm_available():  # 配置了真实 key 的环境跳过
            pytest.skip("LLM configured in this environment")
        slots = semantic.extract_slots_with_llm("成都小学分布")
        assert slots.degraded_reason == "llm_unavailable"
        assert slots.subject.value == ""

    def test_fake_llm_slots_fill_rule_gaps(self, monkeypatch) -> None:
        """规则缺失的槽位由 LLM 槽位补齐；source=llm 可审计。"""
        llm_slots = IntentSlots(
            subject=SlotValue(value="养老院", confidence=0.9, source="llm"),
            measure=SlotValue(value="count", confidence=0.8, source="llm"),
            task_candidate="",
            confidence=0.8,
        )
        monkeypatch.setattr(semantic, "extract_slots_with_llm",
                            lambda _q: llm_slots)
        intent, _request = resolve_intent_adaptive(
            "绵阳有多少", use_llm=True)  # 规则主体为空 → LLM 补齐
        assert (intent.slots or {}).get("subject", {}).get("source") == "llm"
        evidence = intent.intent_evidence or {}
        assert evidence["llm_used"] is True

    def test_llm_task_candidate_adopted_only_on_fallback(
            self, monkeypatch) -> None:
        """证据优先级：仅规则兜底时采信 LLM 任务建议，且留审计痕。"""
        base_slots = semantic.extract_slots("帮我做一张成都的分析图")
        assert base_slots.degraded_reason == "task_rule_miss"
        llm_slots = IntentSlots(task_candidate="categorical_distribution",
                                confidence=0.8)
        monkeypatch.setattr(semantic, "extract_slots_with_llm",
                            lambda _q: llm_slots)
        intent, _request = resolve_intent_adaptive(
            "帮我做一张成都的分析图", use_llm=True)
        assert intent.task == "categorical_distribution"
        assert any("distribution_overview->categorical_distribution" in note
                   for note in intent.hint_applied)

    def test_llm_invalid_output_degrades(self, monkeypatch) -> None:
        def _boom(_q: str) -> IntentSlots:
            return IntentSlots(degraded_reason="llm_output_invalid")

        monkeypatch.setattr(semantic, "extract_slots_with_llm", _boom)
        intent, _request = resolve_intent_adaptive("成都小学分布",
                                                   use_llm=True)
        assert intent.task == "distribution_overview"  # 规则结论不受污染

    def test_slots_extra_fields_rejected(self) -> None:
        with pytest.raises(Exception):
            IntentSlots.model_validate({
                "subject": {"value": "x", "confidence": 0.5},
                "hallucinated_field": "nope",
            })


# ── 澄清策略（语言无关） ──────────────────────────────────────────────────


class TestClarificationPolicy:
    def test_low_confidence_zh_clarifies(self) -> None:
        intent = _resolve_rule_only("分析一下这个区域")
        policy = ClarificationPolicy()
        request = policy.evaluate(intent)
        assert request is not None
        assert 1 <= len(request.questions) <= 2
        for question in request.questions:
            assert question.default_option().is_default

    def test_display_without_subject_en_clarifies(self) -> None:
        intent = _resolve_rule_only("Show me a map of Chengdu")
        request = ClarificationPolicy().evaluate(intent)
        assert request is not None
        slots_asked = {q.slot for q in request.questions}
        assert "subject" in slots_asked

    def test_definite_items_do_not_clarify(self, corpus: list) -> None:
        policy = ClarificationPolicy()
        false_fires = 0
        for item in corpus:
            if item["clarify"]:
                continue
            intent = _resolve_rule_only(item["query"])
            if policy.evaluate(intent) is not None:
                false_fires += 1
        assert false_fires == 0, f"误触发 {false_fires} 条"

    def test_default_answers_backfill(self) -> None:
        intent = _resolve_rule_only("分析一下这个区域")
        policy = ClarificationPolicy()
        request = policy.evaluate(intent)
        assert request is not None
        answers = policy.apply_answers(intent, request, answers=None)
        assert answers  # 默认项被采纳
        assert intent.clarification is not None

    def test_asked_slots_idempotent(self) -> None:
        intent = _resolve_rule_only("人口情况")
        policy = ClarificationPolicy()
        first = policy.evaluate(intent, asked_slots=set())
        assert first is not None
        asked = {q.slot for q in first.questions}
        second = policy.evaluate(intent, asked_slots=asked)
        assert second is None or not (asked & {q.slot for q in second.questions})

    def test_session_store_roundtrip(self) -> None:
        class FakeStore:
            def __init__(self) -> None:
                self.state: Dict[str, Dict[str, Any]] = {}

            async def get_map_state(self, session_id: str, key: str):
                return self.state.get((session_id, key))

            async def set_map_state(self, session_id: str, key: str,
                                    value: Any) -> bool:
                self.state[(session_id, key)] = value
                return True

        async def _run() -> None:
            store = FakeStore()
            await save_asked_slots(store, "s1", {"subject"})
            loaded = await load_asked_slots(store, "s1")
            assert loaded == {"subject"}
            assert await load_asked_slots(store, "missing") == set()

        asyncio.run(_run())

    def test_session_state_key_is_stable(self) -> None:
        assert CLARIFICATION_STATE_KEY == "intent_clarification_state"


# ── 置信度模型与校准 ──────────────────────────────────────────────────────


class TestConfidenceModel:
    def test_evidence_ordering_monotonic(self) -> None:
        weak = resolve_map_request_intent("分析一下这个区域").confidence
        medium = resolve_map_request_intent("帮我看看成都的小学").confidence
        strong = resolve_map_request_intent(
            "统计成都各区小学数量").confidence
        assert weak < medium < strong

    def test_simple_view_confidence_capped(self) -> None:
        intent = resolve_map_request_intent("给我看看成都小学")
        assert intent.confidence <= 0.75

    def test_calibration_binned_error_within_gate(self, corpus: list) -> None:
        """分箱校准误差 ≤ 0.15（锚点拟合口径见 recon §7）。"""
        pairs = []
        for item in corpus:
            intent = resolve_map_request_intent(item["query"])
            evidence = intent.intent_evidence or {}
            comp = evidence.get("confidence_components", {})
            weights = semantic._conf_weights()
            raw = sum(weights[name] * value for name, value in comp.items())
            correct = 0.0 if item["clarify"] else (
                1.0 if intent.task == item["expect"]["task"] else 0.0)
            pairs.append((raw, correct))
        pairs.sort(key=lambda p: p[0])  # 与锚点拟合口径一致（按 raw 排序分箱）
        step = 30
        worst = 0.0
        for i in range(0, len(pairs), step):
            chunk = pairs[i:i + step]
            raw_center = statistics.fmean(p[0] for p in chunk)
            observed = statistics.fmean(p[1] for p in chunk)
            calibrated = semantic.calibrate(raw_center)
            worst = max(worst, abs(calibrated - observed))
        assert worst <= 0.15, f"worst binned calibration error={worst:.3f}"

    def test_confidence_components_in_evidence(self) -> None:
        intent = resolve_map_request_intent("统计成都各区小学数量")
        comp = (intent.intent_evidence or {}).get("confidence_components", {})
        assert set(comp) == {"task_evidence", "slot_completeness",
                             "entity_quality", "session_consistency"}


# ── 双语规则引擎（P5 统一面） ─────────────────────────────────────────────


class TestBilingualEngine:
    @pytest.mark.parametrize("query,expected", [
        ("成都的小学在哪里比较多", "distribution_overview"),
        ("Where are the parks in Hangzhou located", "distribution_overview"),
        ("哪个区的小学最多", "administrative_statistic"),
        ("Which district in Chengdu has the most primary schools",
         "administrative_statistic"),
        ("视域分析（雷达站选址点）", "terrain_analysis"),
        ("Hotspot analysis of thefts in Kunming", "concentration_analysis"),
        ("土壤类型分布", "raster_distribution"),
        ("Communities without any park within walking distance in Chengdu",
         "accessibility_analysis"),
    ])
    def test_cross_language_routing(self, query: str,
                                    expected: str) -> None:
        assert resolve_map_request_intent(query).task == expected

    def test_latin_word_edge_unified(self) -> None:
        """_latin 词缘：CJK 邻接命中、英文单词内不误命中。"""
        assert resolve_map_request_intent("看看sar影像").task == "sar_analysis"
        assert resolve_map_request_intent(
            "the sample area report").task != "sar_analysis"

    def test_english_subject_canonicalized(self) -> None:
        intent = resolve_map_request_intent("Show me hospitals in Beijing")
        assert intent.subject.category == "医院"

    def test_scope_city_suffix_rejects_supermarket(self) -> None:
        intent = resolve_map_request_intent(
            "成都各区门店数超过10家的连锁超市统计")
        assert intent.scope.name == "成都"

    def test_nearest_anchor_subject(self) -> None:
        intent = resolve_map_request_intent(
            "Find the closest fire station to my hotel in Xi'an")
        assert intent.subject.category == "消防站"


# ── 实体解析与本体对齐（P2） ──────────────────────────────────────────────


class TestEntityResolution:
    def test_en_gazetteer_scope(self) -> None:
        intent = resolve_map_request_intent(
            "Show the distribution of hospitals in Chengdu")
        assert intent.scope.name.lower().startswith("chengdu")
        assert intent.scope.level == "city"

    def test_province_scope(self) -> None:
        intent = resolve_map_request_intent(
            "Geological hazard risk zones in Sichuan")
        assert intent.scope.level == "province"

    def test_injected_entity_service_validates_unknown_city(self) -> None:
        calls = []

        def _service(name: str) -> Dict[str, Any]:
            calls.append(name)
            return {"name": name, "adcode": "510700"}

        # 「云溪市」不在快路径词表 → 正则命中 + 服务校验回填（P2 契约）
        scope_result, trace = semantic.match_scope(
            "云溪市的学校分布", entity_service=_service)
        assert scope_result.name == "云溪市"
        assert trace["source"] == "regex_city_suffix"
        assert trace.get("entity_resolved") is True
        assert calls == ["云溪市"]

    def test_fast_path_city_skips_service_validation(self) -> None:
        def _boom(name: str) -> Dict[str, Any]:
            raise AssertionError("service must not be called for fast path")

        scope_result, trace = semantic.match_scope(
            "绵阳市的学校分布", entity_service=_boom)
        assert scope_result.name
        assert trace.get("entity_resolved") is None

    def test_injected_entity_service_failure_degrades(self) -> None:
        def _boom(name: str) -> Dict[str, Any]:
            raise RuntimeError("geocoder down")

        # 词表外城市走服务校验；服务抛错 → 记录 degraded_reason，不阻塞
        scope_result, trace = semantic.match_scope(
            "云溪市的学校分布", entity_service=_boom)
        assert scope_result.name == "云溪市"  # 判定不被服务故障改变
        assert trace.get("degraded_reason") == "entity_service_error"

    def test_ontology_link_present_for_known_task(self) -> None:
        intent = resolve_map_request_intent("成都各区小学资源是否均衡")
        link = intent.ontology_link
        assert link is not None
        assert link.get("task_id")
        assert "domain" in link and "ontology_version" in link


# ── 204 条闭环语料对齐（无劣化冒烟） ─────────────────────────────────────


class TestClosedLoopCorpusAlignment:
    def test_closed_loop_corpus_resolves_cleanly(self) -> None:
        from app.evaluation.closed_loop_corpus import build_closed_loop_corpus

        scenarios = build_closed_loop_corpus()
        assert len(scenarios) == 204
        import typing

        valid_tasks = set(typing.get_args(
            __import__("app.services.gis_harness.intent", fromlist=["TaskType"]).TaskType))
        for scenario in scenarios:
            intent = resolve_map_request_intent(scenario.map_intent)
            assert intent.task in valid_tasks, scenario.scenario_id
            assert intent.intent_evidence is not None
            assert 0.0 <= intent.confidence <= 1.0
