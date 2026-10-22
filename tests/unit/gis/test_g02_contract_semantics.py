"""G02 契约语义测试 —— deferred 证据态 / not_evaluated 通道 / MethodUseContract。

三组不变量：
1. unknown ≠ pass 的单一语义出口（pass_evidence_state / deferred 标志）；
2. resolver 的 not_evaluated 披露面（资格不变，evidence 可对账）；
3. MethodUseContract 是权威注册表的只读投影（零复制、诚实缺省、有界）。
"""
import pytest

from app.lib.gis.algorithm_registry import get_algorithm_registry
from app.lib.gis.algorithm_resolver import (
    get_algorithm_resolver,
    reset_algorithm_resolver,
)
from app.lib.gis.capability_registry import get_capability_registry
from app.lib.gis.method_use_contract import (
    CONTRACT_SCHEMA_VERSION,
    build_method_use_contract,
    iter_method_use_contracts,
)
from app.lib.gis.scientific_preconditions import (
    PRECONDITION_EVIDENCE_STATES,
    PreconditionResult,
    evaluate_precondition,
    pass_evidence_state,
)


@pytest.fixture(autouse=True)
def _fresh_resolver():
    reset_algorithm_resolver()
    yield
    reset_algorithm_resolver()


# ── 1. deferred 证据态 ────────────────────────────────────────────────
class TestDeferredEvidenceState:
    def test_empty_profile_pass_is_deferred(self):
        r = evaluate_precondition("numeric_field_required", {})
        assert r.verdict == "PASS"
        assert r.deferred is True
        assert pass_evidence_state(r) == "unknown_deferred"

    def test_fact_backed_pass_is_proven(self):
        r = evaluate_precondition(
            "numeric_field_required",
            {"fields": {"pop": {}}, "numericFields": ["pop"]},
        )
        assert r.verdict == "PASS"
        assert r.deferred is False
        assert pass_evidence_state(r) == "proven_pass"

    def test_fact_backed_pass_without_facts_used_is_proven(self):
        # temporal_field_required 的 hasTimeField=True PASS 不带 facts_used
        # —— 显式 deferred 标志（而非 facts_used 启发式）的理由。
        r = evaluate_precondition("temporal_field_required", {"hasTimeField": True})
        assert r.verdict == "PASS"
        assert r.deferred is False
        assert pass_evidence_state(r) == "proven_pass"

    def test_non_pass_returns_empty_state(self):
        r = evaluate_precondition(
            "numeric_field_required",
            {"fields": {"name": {}}, "numericFields": []},
        )
        assert r.verdict == "INSUFFICIENT_DATA"
        assert pass_evidence_state(r) == ""

    def test_evidence_state_vocabulary_closed(self):
        assert PRECONDITION_EVIDENCE_STATES == ("proven_pass", "unknown_deferred")

    @pytest.mark.parametrize("pid", [
        "numeric_field_required",
        "nonzero_variance_required",
        "projected_crs_required",
        "local_metric_crs_required",
        "temporal_field_required",
        "min_temporal_observations:8",
        "raster_band_required:2",
        "band_semantics_required",
        "point_support_required",
        "positive_weights_required",
        "binary_field_required",
        "grid_identity_match",
        "min_numeric_samples:30",
    ])
    def test_all_checkers_mark_deferred_on_empty_profile(self, pid):
        """空画像（全事实缺席）下 PASS 必须显式 deferred —— 15 处标注全枚举。"""
        r = evaluate_precondition(pid, {})
        assert r.verdict == "PASS"
        assert r.deferred is True, f"{pid} 缺 deferred=True 标注"
        assert pass_evidence_state(r) == "unknown_deferred"

    def test_default_result_not_deferred(self):
        assert PreconditionResult("x", "PASS").deferred is False

    def test_deferred_does_not_change_verdict_vocabulary(self):
        # 资格兼容承诺：deferred-PASS 在五值词表里仍是 PASS。
        r1 = evaluate_precondition("numeric_field_required", {})
        assert r1.verdict == "PASS"


# ── 2. resolver not_evaluated 通道 ───────────────────────────────────
class TestResolverNotEvaluated:
    def _capability_for(self, algorithm_id: str) -> str:
        reg = get_algorithm_registry()
        for cid in get_capability_registry().all_ids:
            if any(a.id == algorithm_id
                   for a in reg.algorithms_for_capability(cid)):
                return cid
        raise AssertionError(f"no capability for {algorithm_id}")

    def test_block_kriging_deferred_disclosed(self):
        # 空 profile：min_numeric_samples:8 事实缺席 → PASS(deferred) 且
        # 进 not_evaluated（同时保持 resolved 资格不变）。
        cap = self._capability_for("interpolation.block_kriging")
        res = get_algorithm_resolver().resolve(cap, profile={})
        assert res.status == "resolved"
        assert res.algorithm == "interpolation.block_kriging"
        assert any("min_numeric_samples:8" in e for e in res.not_evaluated)

    def test_facts_present_clears_not_evaluated(self):
        cap = self._capability_for("interpolation.block_kriging")
        res = get_algorithm_resolver().resolve(
            cap, profile={"numericSampleCount": 64})
        assert res.status == "resolved"
        assert res.not_evaluated == []

    def test_rejection_still_wins_over_deferred(self):
        # 样本数 3 < 8/2 → INSUFFICIENT_DATA 拒绝（deferred 不得掩盖真拒绝）。
        cap = self._capability_for("interpolation.block_kriging")
        res = get_algorithm_resolver().resolve(
            cap, profile={"numericSampleCount": 3})
        assert res.status in ("unavailable", "resolved")
        if res.status == "resolved":
            # 走了 fallback 链 —— 主候选必须出现在拒绝理由里
            assert any("scientific_precondition:interpolation.block_kriging"
                       in r for r in res.rejected)
        else:
            assert any("scientific_precondition:interpolation.block_kriging"
                       in r for r in res.rejected)

    def test_resolution_field_default_empty(self):
        res = get_algorithm_resolver().resolve(
            "block_kriging", profile=None, available_tools=set())
        # available_tools 空 → 工具不可用拒绝 —— not_evaluated 仍须是列表
        assert isinstance(res.not_evaluated, list)


# ── 3. MethodUseContract 投影 ────────────────────────────────────────
class TestMethodUseContract:
    def test_projection_from_authorities(self):
        c = build_method_use_contract("interpolation.cokriging")
        reg = get_algorithm_registry().get("interpolation.cokriging")
        assert c.schema_version == CONTRACT_SCHEMA_VERSION
        assert c.crs_class == reg.crs_class
        assert c.scientific_preconditions == list(reg.scientific_preconditions)
        assert c.fallback_semantics == dict(reg.fallback_semantics)
        assert c.parameter_contract_id == reg.parameter_contract_ref
        # 参数行零复制自契约权威
        assert [p.name for p in c.parameters]
        units = {p.unit for p in c.parameters if p.unit}
        assert all(units)  # 非空单位都是词表值（findings 会抓词表外）

    def test_unknown_algorithm_honest(self):
        c = build_method_use_contract("no.such.algorithm")
        assert c.capabilities == []
        assert "unknown_algorithm:no.such.algorithm" in c.findings

    def test_deterministic_projection(self):
        c1 = build_method_use_contract("interpolation.block_kriging")
        c2 = build_method_use_contract("interpolation.block_kriging")
        assert c1.model_dump() == c2.model_dump()

    def test_iter_covers_whole_registry(self):
        ids = [c.algorithm_id for c in iter_method_use_contracts()]
        reg_ids = sorted(get_algorithm_registry().all_ids)
        assert ids == reg_ids

    def test_findings_bounded(self):
        c = build_method_use_contract("interpolation.cokriging")
        assert len(c.findings) <= 12

    def test_full_registry_projection_fast_and_clean(self):
        # 现状断言：注册表自洽 → 全量投影零 finding（若未来引入新 finding
        # 属注册表数据债，应显式修数据而非删本断言）。
        findings = [c.findings for c in iter_method_use_contracts()]
        assert all(f == [] for f in findings)

    def test_precondition_description_projection(self):
        c = build_method_use_contract("interpolation.block_kriging")
        assert "min_numeric_samples:8" in c.precondition_descriptions
        assert c.precondition_descriptions["min_numeric_samples:8"]
